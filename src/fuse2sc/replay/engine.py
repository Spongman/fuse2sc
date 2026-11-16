"""The build: a plan replayed in SolidCraft one Fusion step at a time.

    result = await build(doc, out, cli, sources=sources, options=options)

`replay()` is the heart, and works against any SolidCraftPort (the real session, or a fake in tests):
for each step it runs the step's commands (runner.py) and then checks every body against Fusion's
snapshot, bringing Fusion's own result in for bodies that differ (checker.py, substitution.py).
`Engine` adds what surrounds it: translating, the SolidCraft session, saving, the command script and
the BuildResult.
"""

from __future__ import annotations

import time
from pathlib import Path

import anyio

from .. import __version__
from ..ir.keys import safe_filename
from ..model import Document
from ..solidcraft import SessionError, open_session, runner
from ..translate import Note, Plan, translate
from . import strategies
from .checker import StepChecker
from .naming import BodyNamer
from .port import SolidCraftPort
from .results import BuildResult, Category, NoteOut, StepResult
from .runner import CommandRunner
from .sources import Sources
from .state import BuildState, EventSink, Options, no_events
from .substitution import Substituter

__all__ = ["Engine", "Options", "build", "categorize", "replay", "resolve_linked"]


async def replay(
	port: SolidCraftPort, state: BuildState, sources: Sources, active: list[strategies.Strategy] | None = None
) -> list[str]:
	"""Run the plan's commands step by step, checking each step. Returns how the finished design
	differs from Fusion's (empty when it matches, or when the run stopped early)."""
	namer = BodyNamer(port, state)
	run = CommandRunner(port, state, namer, list(strategies.STRATEGIES) if active is None else active)
	checker = StepChecker(port, state, namer, Substituter(port, state, sources, namer))
	steps = {s.index: s for s in state.doc.steps}
	last_checked = None
	stopped = False
	opt = state.options
	for index, commands in state.plan.by_step():
		if index is None:  # parameters before the timeline, or components and looks after it
			if last_checked is not None and opt.until_step is None:
				await checker.settle(last_checked)
				last_checked = None
			for cmd in commands:
				await run.run(cmd, None)
			continue
		if stopped or (opt.until_step is not None and index > opt.until_step):
			continue
		rec = state.records[index]
		if index in opt.skip_steps:
			rec.status = "skipped"
			rec.details.append("skipped on request")
			continue
		state.events("step", {"index": index, "name": rec.name, "of": len(state.doc.steps)})
		for cmd in commands:
			await run.run(cmd, rec)
		step = steps[index]
		if opt.check_every_step and step.snapshot is not None:
			await checker.check(step, rec)
			last_checked = step
		if opt.stop_at_divergence and rec.status in ("mismatch", "failed"):
			state.journal.checks.append(f"stopped at the first divergence: step {index} {rec.name}")
			stopped = True
	if last_checked is not None and opt.until_step is None:
		await checker.settle(last_checked)
	return [] if (stopped or opt.until_step is not None) else await checker.final_problems()


def categorize(saved: bool, doc: Document, final_problems: list[str], steps: list[StepResult]) -> Category:
	if not saved:
		return "failed"
	if not doc.final_bodies():
		return "empty"
	if final_problems:
		return "mismatch"
	if any(s.status in ("substituted", "failed", "mismatch") for s in steps):
		return "partial"
	return "full"


def resolve_linked(plan: Plan, folder: Path) -> bool:
	"""Point linked-design inserts at <folder>/<design>.sc; returns whether any were found."""
	found = False
	for cmd in plan.commands:
		path = cmd.params.get("path")
		if cmd.command == "component.insert_design" and isinstance(path, dict):
			target = folder / f"{safe_filename(path['linked'])}.sc"
			cmd.params["path"] = str(target)
			found = True
			if not target.exists():
				plan.notes.append(
					Note(cmd.step, f"linked design {path['linked']} is not converted yet ({target}): convert it first")
				)
	return found


class Engine:
	def __init__(
		self,
		doc: Document,
		out: Path,
		cli: Path,
		*,
		sources: Sources | None = None,
		options: Options | None = None,
		on_event: EventSink | None = None,
		sc_version: str = "",
	) -> None:
		self.doc, self.out, self.cli = doc, Path(out).resolve(), cli
		self.options = options or Options()
		self.sources = sources or Sources()
		self.on_event = on_event or no_events
		self.sc_version = sc_version

	async def run(self) -> BuildResult:
		started = time.monotonic()
		plan = translate(self.doc)
		linked_dir = self.options.linked_dir or self.out.parent
		allow = [self.out.parent, *self.options.allow] + ([linked_dir] if resolve_linked(plan, linked_dir) else [])
		state = BuildState(self.doc, plan, self.options, self.on_event)
		result = BuildResult(
			document=self.doc.document,
			solidcraft_version=self.sc_version,
			commands=len(plan.commands),
			tool_version=__version__,
		)
		state.events("phase", {"text": "Building in SolidCraft", "steps": len(self.doc.steps)})
		try:
			async with open_session(
				self.cli,
				allow,
				timeout=self.options.command_timeout,
				log_path=self.out.with_suffix(".solidcraft.log"),
				remote=self.options.remote,
			) as sc:
				result.final_problems = await replay(sc, state, self.sources, strategies.active(self.sc_version))
				saved = await sc.save(self.out)
				result.saved = saved.ok
				if not saved.ok:
					state.journal.failed.append(f"save: {saved.body}")
		except (SessionError, OSError) as e:
			state.journal.failed.append(f"SolidCraft session: {e}")
		finally:
			self.sources.write_needed()
		if result.saved and self.options.write_script:
			await self._write_script(state)
		return self._finish(result, state, plan, started)

	async def _write_script(self, state: BuildState) -> None:
		try:
			await anyio.to_thread.run_sync(
				runner.write_script, self.cli, self.out, self.out.with_suffix(".script.json")
			)
		except (runner.RunError, OSError) as e:
			state.journal.checks.append(f"could not write the command script: {e}")

	def _finish(self, result: BuildResult, state: BuildState, plan: Plan, started: float) -> BuildResult:
		result.out = str(self.out) if result.saved else None
		result.steps = [state.records[i] for i in sorted(state.records)]
		result.notes = [NoteOut(step=n.step, note=n.text) for n in plan.notes]
		result.failed, result.dropped, result.log = state.journal.failed, state.journal.dropped, state.journal.checks
		result.needed_steps = {i: sorted(c) for i, c in self.sources.missing.items()}
		result.category = categorize(result.saved, self.doc, result.final_problems, result.steps)
		result.seconds = round(time.monotonic() - started, 1)
		self.on_event("done", {"category": result.category, "counts": result.counts})
		return result


async def build(
	doc: Document,
	out: Path,
	cli: Path,
	*,
	sources: Sources | None = None,
	options: Options | None = None,
	on_event: EventSink | None = None,
	sc_version: str = "",
) -> BuildResult:
	return await Engine(doc, out, cli, sources=sources, options=options, on_event=on_event, sc_version=sc_version).run()
