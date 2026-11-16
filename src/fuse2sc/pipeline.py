"""Building designs: one timeline.json to a verified .sc with its reports, or many at once.

Designs linked into others are built first; up-to-date designs are skipped; a failed rebuild never
leaves the old design looking current (its outputs are removed before building).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

import anyio

from . import __version__, reports
from .bridge import Bridge, from_environment
from .ir.keys import safe_filename
from .model import Document, stem_of
from .model import load as load_ir
from .replay import BuildResult, Options, Sources, StepsFolder, build
from .replay.geometry_route import import_step
from .replay.sources import StepSource
from .settings import Workspace
from .solidcraft import Remote, runner
from .verify import verify

log = logging.getLogger(__name__)
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass
class BuildSettings:
	out: Path | None = None
	outdir: Path | None = None
	force: bool = False
	bridge: bool = False
	check: bool = True
	guard: bool = True
	verify: bool = True
	viewer: bool = False
	until_step: int | None = None
	skip_steps: frozenset[int] = frozenset()
	stop_at_divergence: bool = False
	timeout: float = 300.0
	jobs: int = 1
	extra_allow: list[Path] = field(default_factory=list)
	remote: Remote | None = None


def output_path(doc: Document, timeline: Path, s: BuildSettings) -> Path:
	if s.out:
		return s.out.resolve()
	folder = s.outdir.resolve() if s.outdir else timeline.parent
	return folder / f"{doc.stem}.sc"


def up_to_date(sc: Path, build_json: Path, timeline: Path, sc_version: str) -> BuildResult | None:
	if not (sc.is_file() and build_json.is_file()):
		return None
	try:
		prev = BuildResult.model_validate_json(build_json.read_text(encoding="utf-8"))
	except ValueError:
		return None
	if (prev.tool_version, prev.solidcraft_version, prev.timeline_mtime) == (
		__version__,
		sc_version,
		timeline.stat().st_mtime,
	):
		return prev
	return None


def order_by_links(timelines: Iterable[Path]) -> list[Path]:
	"""Designs that others link to come first."""
	timelines = list(timelines)
	by_stem: dict[str, Path] = {}
	links: dict[Path, set[str]] = {}
	for t in timelines:
		try:
			raw = json.loads(t.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			links[t] = set()
			continue
		by_stem[stem_of((raw.get("fusion") or {}).get("name") or raw.get("document") or t.parent.name)] = t
		links[t] = {safe_filename(c["linked"]) for c in raw.get("components", []) if c.get("linked")}
	graph = {t: {by_stem[n] for n in names if n in by_stem} for t, names in links.items()}
	try:
		return list(TopologicalSorter(graph).static_order())
	except CycleError:
		return timelines


async def build_design(
	timeline: Path,
	s: BuildSettings,
	*,
	cli: Path,
	sc_version: str,
	ws: Workspace | None = None,
	linked: dict[str, Path] | None = None,
	on_event: EventSink | None = None,
) -> BuildResult:
	events = on_event or (lambda kind, data: None)
	doc = load_ir(timeline)
	stem = doc.stem
	sc = output_path(doc, timeline, s)
	sc.parent.mkdir(parents=True, exist_ok=True)
	build_json = sc.with_name(f"{stem}.build.json")
	if not s.force and (prev := up_to_date(sc, build_json, timeline, sc_version)):
		events("design", {"text": f"{stem}: up to date ({prev.category})"})
		return prev
	for stale in (sc, build_json, sc.with_name(f"{stem}.report.txt"), sc.with_name(f"{stem}.script.json")):
		stale.unlink(missing_ok=True)
	events("design", {"text": f"{stem}:"})
	design_dir = timeline.parent
	steps_dir = design_dir / "steps"
	sources: list[StepSource] = [StepsFolder(steps_dir)] if steps_dir.is_dir() else []
	allow = [steps_dir] if steps_dir.is_dir() else []
	bridge: Bridge | None = from_environment(doc.fusion.id, use_file=s.bridge)
	if bridge:
		try:
			if folder := (await bridge.ping()).get("steps_folder"):
				Path(folder).mkdir(parents=True, exist_ok=True)
				allow.append(Path(folder))
			sources.append(bridge)
		except Exception as e:  # noqa: BLE001 - the build goes on without live fallbacks
			events("message", {"text": f"warning: the Fusion bridge did not answer: {e}"})
	elif s.bridge:
		events("message", {"text": "warning: --bridge given but no bridge is running (switch it on in Fusion)"})
	links = {safe_filename(c.linked) for c in doc.components if c.linked}
	linked_dir = next(iter({(linked or {}).get(n) for n in links} - {None}), None) if links else None
	options = Options(
		check_every_step=s.check,
		guard_constraints=s.guard,
		tolerances=ws.settings.tolerances if ws else Options().tolerances,
		command_timeout=s.timeout,
		until_step=s.until_step,
		skip_steps=s.skip_steps,
		stop_at_divergence=s.stop_at_divergence,
		linked_dir=linked_dir,
		allow=allow + s.extra_allow,
		remote=s.remote,
	)
	final_step = design_dir / "final.step"
	if not doc.steps and final_step.is_file():
		result = await import_step(
			cli, final_step, sc, name=stem, document=doc.document, sc_version=sc_version, remote=s.remote
		)
	else:
		needed = design_dir / "needed-steps.json"
		result = await build(
			doc, sc, cli, sources=Sources(sources, needed), options=options, on_event=events, sc_version=sc_version
		)
	if result.saved and s.verify:
		result.verify = await anyio.to_thread.run_sync(verify, design_dir, sc, cli, options.tolerances.volume_rel, doc)
	if result.saved and s.viewer:
		await anyio.to_thread.run_sync(write_viewer, design_dir, sc, cli, stem)
	result.timeline, result.timeline_mtime = str(timeline), timeline.stat().st_mtime
	_, report = reports.write_design(result, sc.parent, stem)
	events(
		"result",
		{
			"design": stem,
			"category": result.category,
			"counts": result.counts,
			"out": result.out,
			"report": str(report),
		},
	)
	return result


def write_viewer(design_dir: Path, sc: Path, cli: Path, stem: str) -> Path:
	try:
		glb = runner.export(cli, sc, ".glb")
	except (runner.RunError, OSError) as e:
		log.warning("viewer: %s", e)
		glb = None
	stl = design_dir / "final.stl"
	return reports.write_viewer(
		sc.with_name(f"{stem}.viewer.html"), stem, glb, stl.read_bytes() if stl.is_file() else None
	)


def linked_lookup(ws: Workspace | None, timelines: list[Path], outdir: Path | None) -> dict[str, Path]:
	"""Design name -> the folder its .sc is (or will be) in."""
	out: dict[str, Path] = {}
	candidates = [(t, True) for t in timelines] + [
		(d / "timeline.json", False) for d in (ws.design_dirs() if ws else [])
	]
	for t, requested in candidates:
		try:
			stem = load_ir(t).stem
		except Exception:  # noqa: BLE001 - an unreadable design just can't be linked to
			continue
		out.setdefault(stem, outdir.resolve() if outdir and requested else t.parent)
	return out


async def build_many(
	timelines: list[Path],
	s: BuildSettings,
	*,
	cli: Path,
	sc_version: str,
	ws: Workspace | None,
	on_event: EventSink | None = None,
) -> list[BuildResult]:
	ordered = order_by_links(timelines)
	linked = linked_lookup(ws, ordered, s.outdir)
	results: dict[Path, BuildResult] = {}
	limiter = anyio.CapacityLimiter(max(1, s.jobs))

	async def one(t: Path) -> None:
		async with limiter:
			try:
				results[t] = await build_design(
					t, s, cli=cli, sc_version=sc_version, ws=ws, linked=linked, on_event=on_event
				)
			except Exception as e:  # one design's failure doesn't stop the rest
				log.exception("building %s", t)
				(on_event or (lambda *_: None))("failed", {"text": f"{t}: {type(e).__name__}: {e}"})
				results[t] = BuildResult(document=str(t), category="failed", failed=[f"{type(e).__name__}: {e}"])

	if s.jobs > 1:
		async with anyio.create_task_group() as tg:
			for t in ordered:
				tg.start_soon(one, t)
	else:
		for t in ordered:  # in order: linked designs must exist before the designs that use them
			await one(t)
	return [results[t] for t in ordered]
