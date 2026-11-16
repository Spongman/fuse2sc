"""After each step: does SolidCraft have Fusion's bodies?

A body under the wrong name is renamed. Bodies that still differ are replaced with Fusion's result
at that step, when a source has it. Otherwise the step is a mismatch. A body that keeps differing
step after step is reported once, where it started ("diverged"), not at every later step.
"""

from __future__ import annotations

import contextlib

from ..model import Step
from .checks import area_notes, compare
from .naming import BodyNamer, match_bodies
from .port import SolidCraftPort
from .results import StepResult
from .sources import StepUnavailable
from .state import BuildState
from .substitution import Substituter


class StepChecker:
	def __init__(self, port: SolidCraftPort, state: BuildState, namer: BodyNamer, substituter: Substituter) -> None:
		self.port, self.state, self.namer, self.substituter = port, state, namer, substituter
		self.diverged: dict[str, int] = {}  # Fusion body -> the step where it started to differ

	async def check(self, step: Step, rec: StepResult) -> None:
		after, tol, skip = step.snapshot or {}, self.state.tol, self.state.plan.linked
		have = await self.port.bodies()
		result = compare(after, have, tol, self.state.offsets, skip)
		# the same shape under another name: rename, don't replace
		if not result.ok and (
			pairs := match_bodies(
				{k: after[k] for k in result.wrong}, {x: have[x] for x in result.extra}, vol_rel=tol.volume_rel * 2
			)
		):
			await self.namer.rename([(n, k) for k, n in pairs], rec)
			have = await self.port.bodies()
			result = compare(after, have, tol, self.state.offsets, skip)
		if result.ok:
			self.diverged.clear()
			self.state.journal.checks += [f"step {step.index}: {n}" for n in area_notes(after, have, tol)]
			return
		try:
			replaced_all = await self.substituter.substitute(step, result.wrong, result.extra)
			again = compare(after, await self.port.bodies(), tol, self.state.offsets, skip)
			if replaced_all and again.ok:
				rec.worse(
					"substituted",
					"Fusion's result brought in for " + (", ".join(result.wrong) or "extra bodies removed"),
				)
				self.state.events("fusion", {"step": step.index, "name": step.name, "bodies": result.wrong})
				for k in result.wrong:
					self.diverged.pop(k, None)
				return
			result = again
		except StepUnavailable as e:
			if any(k not in self.diverged for k in result.wrong) or result.extra:
				rec.details.append(f"Fusion's result not available: {e}")
		newly = [k for k in result.wrong if k not in self.diverged]
		self.diverged.update(dict.fromkeys(newly, step.index))
		if newly or result.extra:
			rec.worse("mismatch", "; ".join(result.problems[:6]))
		elif result.wrong:
			since = sorted({self.diverged[k] for k in result.wrong})
			rec.details.append(
				f"still differs since step {', '.join(map(str, since[:3]))}: {', '.join(result.wrong[:3])}"
			)

	async def settle(self, last: Step) -> None:
		"""The end of the timeline once more, before components and copies are set up."""
		result = compare(
			last.snapshot or {}, await self.port.bodies(), self.state.tol, self.state.offsets, self.state.plan.linked
		)
		if not result.ok:
			with contextlib.suppress(StepUnavailable):
				await self.substituter.substitute(last, result.wrong, result.extra)

	async def final_problems(self) -> list[str]:
		"""How the finished design differs from Fusion's (bodies under their final names)."""
		state = self.state
		final = {state.final_names.get(k, k): b for k, b in state.doc.final_bodies().items()}
		for old, new in state.final_names.items():
			state.offsets.rename(old, new)
		return compare(final, await self.port.bodies(), state.tol, state.offsets, state.plan.linked).problems
