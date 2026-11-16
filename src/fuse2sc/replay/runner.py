"""Running one planned command, and everything around it:

1. prepare: Fusion's curve ids as SolidCraft named them; regions found by area and centroid;
2. run, and when it builds but changes the model differently from Fusion, its "wrong" variants;
3. when SolidCraft refuses it, its "refused" variants, then the retry strategies;
4. for a sketch constraint, take it back if it moved the drawing;
5. afterwards: record aliases and ids, place occurrences, name new bodies.
"""

from __future__ import annotations

from typing import Any

from ..solidcraft import Reply
from ..translate import Command, Variant
from .checks import delta_ok, totals
from .guard import SketchGuard
from .naming import BodyNamer
from .port import SolidCraftPort
from .regions import match_profiles
from .results import StepResult
from .state import BuildState
from .strategies import Strategy

GUARDED = ("sketch.constraint.", "sketch.dimension")
QUIET_OPTIONAL = ("appearance.", "material.", "browser.")  # cosmetic: a refusal doesn't mark the step


class CommandRunner:
	def __init__(self, port: SolidCraftPort, state: BuildState, namer: BodyNamer, strategies: list[Strategy]) -> None:
		self.port, self.state, self.namer, self.strategies = port, state, namer, strategies
		self.guard = SketchGuard(port)
		self._command: Command | None = None

	# --- StrategyHost ---------------------------------------------------------------------------

	async def execute(self, command: str, params: dict[str, Any]) -> Reply:
		return await self.port.execute(command, params, timeout=self.state.options.command_timeout)

	async def undo(self) -> bool:
		return await self.port.undo()

	async def created(self, replies: list[Any], rec: StepResult) -> None:
		cmd = self._command
		if cmd is None:
			return
		made = [b for r in replies if isinstance(r, dict) for b in r.get("created_bodies") or []]
		if cmd.renames:
			await self.namer.name_pieces([p for p in [cmd.params.get("body"), *made] if p], cmd.renames, rec)
		elif cmd.new_bodies is not None:
			await self.namer.name_new(made, cmd.new_bodies, rec)

	# --- one command ----------------------------------------------------------------------------

	async def run(self, cmd: Command, rec: StepResult | None) -> None:
		self._command = cmd
		if rec is not None:
			rec.commands += 1
		if cmd.is_fallback:
			if rec is not None:
				rec.adjust(f"no SolidCraft equivalent for {cmd.fallback_kind or 'this step'}")
			return  # the check after the step brings Fusion's result in if the bodies differ
		params = await self.prepare(cmd, cmd.params, use_match=True)
		guarded = self.state.options.guard_constraints and cmd.optional and cmd.command.startswith(GUARDED)
		if guarded:
			await self.guard.prepare()
		reply = await self._build(cmd, params, rec)
		if not reply.ok:
			reply = await self._try_refused_variants(cmd, reply, rec)
		if not reply.ok and not cmd.optional and await self._try_strategies(cmd, params, rec):
			return
		if not reply.ok:
			self._record_refusal(cmd, reply, rec)
			return
		if guarded and (moved := await self.guard.moved_and_undone()):
			self.state.journal.dropped.append(
				f"step {cmd.step} {cmd.command}: moved {', '.join(moved[:6])}; taken back"
			)
			if rec is not None:
				rec.adjust(f"{cmd.command} would have moved {', '.join(moved[:3])}: left out")
			return
		await self._after(cmd, params, reply, rec)

	async def prepare(self, cmd: Command, params: dict[str, Any], use_match: bool) -> dict[str, Any]:
		state = self.state
		if cmd.command == "sketch.create":
			state.sketch = params.get("name")
			self.guard.reset()
			return params
		if cmd.command.startswith("sketch.") and state.sketch:
			params = state.aliases.in_sketch(state.sketch, params)
		else:
			params = state.aliases.in_feature(params)
		if cmd.command == "solid.split_body" and params.get("sketch") in state.sketch_ids:
			params = params | {"sketch": state.sketch_ids[params["sketch"]]}
		if use_match and cmd.match and params.get("sketch"):
			inspected = await self.execute("sketch.inspect", {"sketch": params["sketch"]})
			if inspected.ok and isinstance(inspected.body, dict):
				picks, why = match_profiles(inspected.body.get("profiles", []), cmd.match, state.tol)
				if picks is not None:
					params = params | {"profiles": picks, "exact_regions": True}
				elif why:
					state.journal.checks.append(f"step {cmd.step}: {why}")
		return params

	async def _build(self, cmd: Command, params: dict[str, Any], rec: StepResult | None) -> Reply:
		"""Run the command; if it builds differently from Fusion, try its "wrong" variants."""
		wrong = [v for v in cmd.variants if v.when == "wrong"]
		if not (wrong and cmd.delta):
			return await self.execute(cmd.command, params)
		before = totals(await self.port.bodies())
		reply = await self.execute(cmd.command, params)
		if not reply.ok or delta_ok(before, totals(await self.port.bodies()), cmd.delta, self.state.tol):
			return reply
		for variant in wrong:
			if not await self.undo():
				return reply
			alt = await self.execute(cmd.command, await self.prepare(cmd, variant.params, use_match=True))
			if alt.ok and delta_ok(before, totals(await self.port.bodies()), cmd.delta, self.state.tol):
				if rec is not None:
					rec.adjust(f"{cmd.command}: {variant.why}")
				return alt
			if alt.ok:
				await self.undo()
			reply = await self.execute(cmd.command, params)  # back to the plain way
			if not reply.ok:
				return reply
		return reply

	async def _try_refused_variants(self, cmd: Command, first: Reply, rec: StepResult | None) -> Reply:
		reply = first
		refused: list[Variant] = [v for v in cmd.variants if v.when == "refused"]
		for variant in refused:
			reply = await self.execute(cmd.command, await self.prepare(cmd, variant.params, use_match=False))
			if reply.ok:
				if rec is not None:
					rec.adjust(f"{cmd.command}: {variant.why} (first refusal: {first.error[:120]})")
				return reply
		return reply

	async def _try_strategies(self, cmd: Command, params: dict[str, Any], rec: StepResult | None) -> bool:
		target = rec or StepResult(index=-1, name="")
		for strategy in self.strategies:
			if strategy.applies(cmd.command, params) and (
				outcome := await strategy.run(self, cmd.command, params, target)
			):
				self.state.journal.checks.append(f"step {cmd.step}: {strategy.name} ({outcome})")
				return True
		return False

	def _record_refusal(self, cmd: Command, reply: Reply, rec: StepResult | None) -> None:
		msg = f"step {cmd.step} {cmd.command}: {reply.error[:300]}"
		if cmd.optional:
			self.state.journal.dropped.append(msg)
			if rec is not None and not cmd.command.startswith(QUIET_OPTIONAL):
				rec.adjust(f"{cmd.command} refused by SolidCraft")
			return
		self.state.journal.failed.append(msg)
		if rec is not None:
			rec.failed_commands += 1
			rec.worse("failed", f"{cmd.command} refused: {reply.error[:200]}")
		self.state.events("failed", {"step": cmd.step, "text": msg})

	async def _after(self, cmd: Command, params: dict[str, Any], reply: Reply, rec: StepResult | None) -> None:
		state = self.state
		if cmd.aliases and state.sketch:
			state.aliases.record(state.sketch, cmd.aliases, reply.body)
		if cmd.command == "sketch.create" and (sketch_id := reply.get("sketch")) is not None:
			state.sketch_ids[params["name"]] = sketch_id
		elif cmd.command == "sketch.finish":
			state.sketch = None
		if (occurrence := reply.get("occurrence")) is not None:
			await self._place(cmd, params, occurrence)
		if cmd.final_name:
			state.final_names[params["body"]] = params["name"]
		if rec is not None and isinstance(reply.body, dict):
			await self.created([reply.body], rec)

	async def _place(self, cmd: Command, params: dict[str, Any], occurrence: Any) -> None:
		moves = [
			("matrix", cmd.occurrence_move, {"space": "world"}, f"placing a copy of {params.get('component')}"),
			("to_matrix", cmd.occurrence_place, {}, f"placing linked design {params.get('name')}"),
		]
		for key, matrix, extra, what in moves:
			if matrix:
				reply = await self.execute("occurrence.move", {"occurrence": occurrence, key: matrix} | extra)
				if not reply.ok:
					self.state.journal.failed.append(f"{what}: {reply.error[:200]}")
