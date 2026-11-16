"""Putting Fusion's own result for a step in place of SolidCraft's bodies.

Fusion's result is fetched first: when it can't be had, nothing of SolidCraft's is touched. Then
bodies Fusion doesn't have are removed, the wrong ones are taken out (renamed first: a removed body
keeps its name in SolidCraft's history), and Fusion's STEP comes in, its bodies named after
Fusion's. Where SolidCraft measures Fusion's geometry slightly differently, the difference is
remembered so later checks compare like with like.
"""

from __future__ import annotations

import numpy as np

from ..ir.keys import component_of, plain_name
from ..model import Body, Step
from ..translate.plan import Params
from .naming import BodyNamer, match_bodies
from .port import SolidCraftPort
from .sources import ExportedStep, Sources
from .state import BuildState


class Substituter:
	def __init__(self, port: SolidCraftPort, state: BuildState, sources: Sources, namer: BodyNamer) -> None:
		self.port, self.state, self.sources, self.namer = port, state, sources, namer

	async def substitute(self, step: Step, wrong: list[str], extra: list[str]) -> bool:
		"""Replace `wrong` bodies with Fusion's result at `step` and remove `extra` ones.
		Returns whether every wrong body was replaced. Raises StepUnavailable (changing nothing)
		when no source has Fusion's result."""
		files = await self.sources.export(step.index, sorted({component_of(k) for k in wrong})) if wrong else []
		await self._remove([x for x in extra if x in await self.port.bodies()], step)
		if not wrong:
			return True
		await self._take_out(wrong, step)
		complete = True
		for f in files:
			want = {k for k in wrong if component_of(k) == f.component}
			if want:
				complete &= await self._bring_in(f, want, step)
		return complete

	async def _remove(self, bodies: list[str], step: Step) -> None:
		if bodies:
			reply = await self.port.execute("solid.remove", {"bodies": bodies})
			if not reply.ok:
				self.state.journal.checks.append(f"step {step.index}: removing {bodies} failed: {reply.error[:200]}")

	async def _take_out(self, wrong: list[str], step: Step) -> None:
		present = [k for k in wrong if k in await self.port.bodies()]
		freed = [f"{k} (replaced at step {step.index})" for k in present]
		for old, new in zip(present, freed, strict=True):
			await self.port.execute("body.rename", {"body": old, "name": new})
		await self._remove(freed, step)

	async def _bring_in(self, f: ExportedStep, want: set[str], step: Step) -> bool:
		after = step.snapshot or {}
		params: Params = {"path": str(f.path), "name": f"{step.name} from Fusion", "names": "solid"}
		if f.matrix:
			params["matrix"] = f.matrix
		reply = await self.port.execute("file.insert_step", params)
		if not reply.ok:
			self.state.journal.checks.append(
				f"step {step.index}: importing Fusion's result failed: {reply.error[:200]}"
			)
			return False
		created: list[str] = reply.get("created_bodies") or []
		have = await self.port.bodies()
		by_name = {plain_name(k): k for k in want}
		pairs = [(c, by_name.pop(c)) for c in created if c in by_name]
		named = {c for c, _ in pairs}
		unnamed = {k: after[k] for k in by_name.values() if k in after}
		pairs += [
			(n, k)
			for k, n in match_bodies(
				unnamed, {c: have[c] for c in created if c not in named and c in have}, vol_rel=0.01, com_abs=0.2
			)
		]
		await self.namer.rename(pairs)
		await self._remove([c for c in created if c not in {c for c, _ in pairs}], step)
		await self._remember_offsets([k for _, k in pairs], after, step)
		if missing := want - {k for _, k in pairs}:
			self.state.journal.checks.append(f"step {step.index}: Fusion's export lacked {sorted(missing)}")
			return False
		return True

	async def _remember_offsets(self, keys: list[str], after: dict[str, Body], step: Step) -> None:
		have = await self.port.bodies()
		for k in keys:
			b, fusion = have.get(k, {}), after.get(k)
			v = b.get("volume_mm3")
			if not (fusion and fusion.volume and v is not None):
				continue
			if abs(v - fusion.volume) > self.state.tol.volume_rel * fusion.volume:
				com = np.asarray(b.get("center_of_mass") or fusion.com or (0, 0, 0), float)
				self.state.offsets.by_body[k] = (v - fusion.volume, com - np.asarray(fusion.com or (0, 0, 0), float))
				self.state.journal.checks.append(
					f"step {step.index}: SolidCraft measures Fusion's {k} {100 * (v / fusion.volume - 1):+.2f}% "
					"differently; later steps compare against that"
				)
