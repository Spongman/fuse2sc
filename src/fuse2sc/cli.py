"""The fuse2sc command line (typer)."""

from __future__ import annotations

import functools
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, NoReturn

import anyio
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import __version__, model, reports
from .bridge import from_environment
from .pipeline import BuildSettings, build_many, write_viewer
from .replay import BuildResult, strategies
from .settings import Workspace, WorkspaceError, find_workspace, init_workspace
from .solidcraft import CliNotFound, Remote, RemoteError, cli_version, find_cli, open_session
from .translate import translate
from .verify import verify as verify_design

app = typer.Typer(
	no_args_is_help=True,
	add_completion=False,
	rich_markup_mode="rich",
	help="Move Autodesk Fusion designs to SolidCraft with their parametric history.",
)
console = Console(stderr=True)


@dataclass
class State:
	workspace: Path | None = None
	cli: str | None = None
	remote: str | None = None
	path_map: list[str] = field(default_factory=list)


state = State()


def version_callback(value: bool) -> None:
	if value:
		print(f"fuse2sc {__version__}")
		raise typer.Exit()


@app.callback()
def main(
	workspace: Annotated[
		Path | None, typer.Option(help="Workspace folder (default: $FUSE2SC_WORKSPACE, or found from here upward).")
	] = None,
	cli: Annotated[str | None, typer.Option("--cli", help="solidcraft-cli to use.")] = None,
	remote: Annotated[
		str | None,
		typer.Option(
			envvar="FUSE2SC_SOLIDCRAFT_REMOTE",
			help="Build in the SolidCraft app at HOST[:PORT] (its control channel, 27878 by default) instead of "
			"a local headless one. The token comes from $SOLIDCRAFT_CONTROL_TOKEN.",
		),
	] = None,
	path_map: Annotated[
		list[str] | None,
		typer.Option(
			"--path-map",
			help="LOCAL=REMOTE: what the remote computer calls a shared local folder (repeatable; adds to the "
			"workspace's solidcraft_remote.paths).",
		),
	] = None,
	verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log what happens.")] = False,
	version: Annotated[bool, typer.Option("--version", callback=version_callback, is_eager=True)] = False,
) -> None:
	logging.basicConfig(
		level=logging.DEBUG if verbose else logging.WARNING,
		format="%(message)s",
		handlers=[RichHandler(console=console, show_path=False)],
	)
	state.workspace, state.cli, state.remote, state.path_map = workspace, cli, remote, path_map or []


# --- helpers -------------------------------------------------------------------------------------


def workspace() -> Workspace | None:
	"""The workspace, when there is one."""
	try:
		return find_workspace(state.workspace)
	except WorkspaceError:
		return None


def required_workspace() -> Workspace:
	try:
		return find_workspace(state.workspace)
	except WorkspaceError as e:
		fail(str(e))


def solidcraft(ws: Workspace | None) -> Path:
	try:
		return find_cli(state.cli or (ws.settings.solidcraft_cli if ws else None) or None)
	except CliNotFound as e:
		fail(str(e))


def solidcraft_remote(ws: Workspace | None) -> Remote | None:
	"""The remote SolidCraft app to build in, from --remote or the workspace; None: build here."""
	configured = ws.settings.solidcraft_remote if ws else None
	address = state.remote or (configured.address if configured else "")
	if not address:
		return None
	paths = {str(ws.root / local): there for local, there in configured.paths.items()} if ws and configured else {}
	for item in state.path_map:
		local, sep, remote = item.partition("=")
		if not (sep and local and remote):
			fail(f"--path-map wants LOCAL=REMOTE, not {item!r}")
		paths[local] = remote
	try:
		return Remote.parse(address, paths=paths)
	except RemoteError as e:
		fail(str(e))


def fail(message: str, code: int = 2) -> NoReturn:
	console.print(f"[red]fuse2sc:[/] {message}")
	raise typer.Exit(code)


def timelines(designs: list[Path], ws: Workspace | None, only: str | None) -> list[Path]:
	out = []
	for given in designs:
		p = given / "timeline.json" if given.is_dir() else given
		if not p.is_file():
			fail(f"not found: {p}")
		out.append(p.resolve())
	if not out:
		if ws is None:
			fail("no design given and no workspace found (run `fuse2sc init`, or pass a timeline.json)")
		out = [d / "timeline.json" for d in ws.design_dirs()]
	if only:
		out = [p for p in out if only.lower() in str(p).lower()]
	return out


def parse_steps(spec: str | None) -> frozenset[int]:
	out: set[int] = set()
	for part in filter(None, (spec or "").split(",")):
		a, _, b = part.partition("-")
		out |= set(range(int(a), int(b or a) + 1))
	return frozenset(out)


class Progress:
	"""Build events: readable lines on stderr, or JSON lines on stdout for the Fusion add-in."""

	def __init__(self, json_lines: bool, quiet: bool) -> None:
		self.json_lines, self.quiet = json_lines, quiet

	def __call__(self, kind: str, data: dict[str, Any]) -> None:
		if self.json_lines:
			sys.stdout.write(json.dumps({"event": kind, **data}, default=str) + "\n")
			sys.stdout.flush()
		elif self.quiet:
			return
		elif kind == "step":
			console.print(f"  [dim]step {data['index'] + 1}/{data['of']}[/] {data['name']}")
		elif kind == "fusion":
			console.print(f"  [yellow]Fusion's result[/] for step {data['step']} {data['name']}")
		elif kind == "failed":
			console.print(f"  [red]failed[/] {data['text']}")
		elif kind in ("design", "message"):
			console.print(data["text"])


# --- commands ------------------------------------------------------------------------------------


@app.command()
def init(
	folder: Annotated[Path, typer.Argument()] = Path(),
	hub: Annotated[str, typer.Option(help="The Fusion hub to export by default.")] = "",
) -> None:
	"""Make a workspace (fusion-to-solidcraft.json) in FOLDER."""
	try:
		marker = init_workspace(folder.resolve(), hub)
	except WorkspaceError as e:
		fail(str(e))
	console.print(
		f"made {marker}\nNext: install the Fusion add-in (python tools/install_addin.py), export from "
		"Fusion, then run [bold]fuse2sc build[/] here."
	)


@app.command()
def doctor() -> None:
	"""Check Python, solidcraft-cli and its MCP server, the workspace, the bridge and the add-in."""
	table = Table(show_header=False, box=None)
	ok = True

	def row(good: bool | None, what: str, detail: str = "") -> None:
		nonlocal ok
		ok &= good is not False
		table.add_row({True: "[green]ok[/]", False: "[red]FAIL[/]", None: "[dim]--[/]"}[good], what, detail)

	row(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}")
	ws = workspace()
	row(ws is not None or None, "workspace", str(ws.root) if ws else "none found (fuse2sc init); needed for batch work")
	try:
		cli = find_cli(state.cli or (ws.settings.solidcraft_cli if ws else None) or None)
		version = cli_version(cli)
		row(True, f"solidcraft-cli {version}", str(cli))
		if ws and (pin := ws.settings.versions.get("solidcraft-cli")):
			row(pin == version, "version pin", f"the workspace pins {pin}")

		remote = solidcraft_remote(ws)

		async def probe() -> tuple[dict[str, Any], float]:
			started = time.monotonic()
			async with open_session(cli, [Path.cwd()], timeout=60, remote=remote) as sc:
				return sc.server, time.monotonic() - started

		server, seconds = anyio.run(probe)
		where = f"the app at {remote.address}" if remote else "solidcraft-cli mcp"
		row(True, f"{where} answers", f"{server.get('name')} {server.get('version')} in {seconds:.1f} s")
		if remote:
			shared = ", ".join(f"{local} = {there}" for local, there in remote.paths)
			row(None if remote.paths else False, "shared folders", shared or "none mapped (--path-map LOCAL=REMOTE)")
		row(None, f"retry strategies for {version}", ", ".join(s.name for s in strategies.active(version)) or "none")
	except CliNotFound as e:
		row(False, "solidcraft-cli", str(e))
	except Exception as e:  # noqa: BLE001
		row(False, "solidcraft-cli mcp", str(e))
	if bridge := from_environment(use_file=True):
		try:
			anyio.run(bridge.ping)
			row(True, "Fusion bridge", bridge.url)
		except Exception as e:  # noqa: BLE001
			row(False, "Fusion bridge", str(e))
	else:
		row(None, "Fusion bridge", "not running (optional)")
	console.print(table)
	raise typer.Exit(0 if ok else 1)


@app.command()
def inspect(
	timeline: Path, plan_out: Annotated[Path | None, typer.Option(help="Write the plan as JSON.")] = None
) -> None:
	"""What would translate, without SolidCraft."""
	doc = model.load(timeline)
	plan = translate(doc)
	kinds: dict[str, dict[str, int]] = {}
	for s in doc.steps:
		st = plan.steps.get(s.index, "?")
		kinds.setdefault(s.kind, {}).setdefault(st, 0)
		kinds[s.kind][st] += 1
	counts = {k: sum(1 for v in plan.steps.values() if v == k) for k in ("planned", "fallback", "skipped")}
	console.print(
		f"[bold]{doc.document}[/]: {len(doc.steps)} steps: {counts['planned']} translate, "
		f"{counts['fallback']} need Fusion's result, {counts['skipped']} skipped; {len(plan.commands)} commands"
	)
	table = Table("Fusion type", "planned", "fallback", "skipped")
	for kind, c in sorted(kinds.items(), key=lambda kv: -sum(kv[1].values())):
		table.add_row(kind, *(str(c.get(k, "")) for k in ("planned", "fallback", "skipped")))
	console.print(table)
	for n in plan.notes:
		console.print(f"  [dim]step {n.step}:[/] {n.text}")
	if plan_out:
		plan_out.write_text(json.dumps([asdict(c) for c in plan.commands], indent=1), encoding="utf-8")


@app.command()
def build(
	designs: Annotated[
		list[Path] | None,
		typer.Argument(help="Design folders or timeline.json files (default: every design in the workspace)."),
	] = None,
	out: Annotated[Path | None, typer.Option("-o", "--out", help="The .sc to write (one design only).")] = None,
	outdir: Annotated[Path | None, typer.Option(help="Write every design's .sc and reports here.")] = None,
	only: Annotated[str | None, typer.Option(help="Only designs whose path contains this.")] = None,
	force: Annotated[bool, typer.Option(help="Rebuild designs that are up to date.")] = False,
	bridge: Annotated[bool, typer.Option(help="Ask a running Fusion for steps' results.")] = False,
	check: Annotated[bool, typer.Option(help="Compare with Fusion after every step.")] = True,
	guard: Annotated[bool, typer.Option(help="Take back constraints that move a sketch.")] = True,
	verify: Annotated[bool, typer.Option(help="Re-measure the saved design.")] = True,
	viewer: Annotated[bool, typer.Option(help="Also write <design>.viewer.html.")] = False,
	until_step: Annotated[int | None, typer.Option(help="Stop after this timeline step.")] = None,
	skip_steps: Annotated[str | None, typer.Option(help="Steps to leave out, e.g. 12,40-45.")] = None,
	find_divergence: Annotated[bool, typer.Option(help="Stop at the first step that differs from Fusion.")] = False,
	timeout: Annotated[float, typer.Option(help="Seconds per SolidCraft command.")] = 300.0,
	jobs: Annotated[int, typer.Option(help="Designs built at once, each in its own SolidCraft.")] = 1,
	strict_versions: Annotated[bool, typer.Option(help="Stop when solidcraft-cli differs from the pin.")] = False,
	progress_json: Annotated[bool, typer.Option(hidden=True)] = False,
	quiet: Annotated[bool, typer.Option("-q", "--quiet")] = False,
) -> None:
	"""Build SolidCraft designs from Fusion timelines, checked step by step."""
	ws = workspace()
	cli = solidcraft(ws)
	version = cli_version(cli)
	if ws and (pin := ws.settings.versions.get("solidcraft-cli")) and pin != version:
		msg = f"solidcraft-cli is {version}; the workspace pins {pin} (rebuild with --force after upgrading)"
		if strict_versions:
			fail(msg)
		console.print(f"[yellow]warning:[/] {msg}")
	targets = timelines(designs or [], ws, only)
	if out and len(targets) != 1:
		fail("-o/--out needs exactly one design")
	remote = solidcraft_remote(ws)
	if remote and jobs > 1:
		console.print(f"[yellow]warning:[/] one design at a time in the app at {remote.address} (--jobs ignored)")
		jobs = 1
	settings = BuildSettings(
		out=out,
		outdir=outdir,
		force=force,
		bridge=bridge,
		check=check,
		guard=guard,
		verify=verify,
		viewer=viewer,
		until_step=until_step,
		skip_steps=parse_steps(skip_steps),
		stop_at_divergence=find_divergence,
		timeout=timeout,
		jobs=jobs,
		remote=remote,
	)
	progress = Progress(progress_json, quiet)
	results = anyio.run(
		functools.partial(build_many, targets, settings, cli=cli, sc_version=version, ws=ws, on_event=progress)
	)
	tally: dict[str, int] = {}
	for r in results:
		tally[r.category] = tally.get(r.category, 0) + 1
	progress("message", {"text": "done: " + ", ".join(f"{n} {c}" for c, n in sorted(tally.items()))})
	if ws and not (out or outdir):
		reports.write_workspace(ws, f"fuse2sc {__version__}, SolidCraft {version}")
	raise typer.Exit(1 if any(r.category in ("failed", "mismatch") for r in results) else 0)


@app.command()
def verify(
	designs: Annotated[list[Path] | None, typer.Argument()] = None, only: Annotated[str | None, typer.Option()] = None
) -> None:
	"""Re-measure saved designs against Fusion's numbers and explain differences."""
	ws = workspace()
	cli = solidcraft(ws)
	bad = 0
	for tl in timelines(designs or [], ws, only):
		doc = model.load(tl)
		sc = tl.parent / f"{doc.stem}.sc"
		if not sc.is_file():
			console.print(f"{doc.stem}: [dim]not built[/]")
			continue
		v = verify_design(tl.parent, sc, cli, doc=doc)
		bad += 0 if v.ok else 1
		console.print(f"{doc.stem}: {'[green]ok[/]' if v.ok else '[red]NOT OK[/]'} {'; '.join(v.notes)}")
		bj = tl.parent / f"{doc.stem}.build.json"
		if bj.is_file():
			r = BuildResult.model_validate_json(bj.read_text(encoding="utf-8"))
			r.verify = v
			reports.write_design(r, tl.parent, doc.stem)
	if ws:
		reports.write_workspace(ws, f"fuse2sc {__version__}")
	raise typer.Exit(1 if bad else 0)


@app.command()
def report() -> None:
	"""Write the workspace scorecard (md, csv) and index.html."""
	ws = required_workspace()
	for kind, path in reports.write_workspace(ws, f"fuse2sc {__version__}").items():
		console.print(f"{kind}: {path}")


@app.command()
def viewer(
	designs: Annotated[list[Path] | None, typer.Argument()] = None, only: Annotated[str | None, typer.Option()] = None
) -> None:
	"""Write <design>.viewer.html: Fusion and SolidCraft side by side in 3D."""
	ws = workspace()
	cli = solidcraft(ws)
	for tl in timelines(designs or [], ws, only):
		stem = model.load(tl).stem
		sc = tl.parent / f"{stem}.sc"
		if sc.is_file():
			console.print(str(write_viewer(tl.parent, sc, cli, stem)))


@app.command()
def schema(out: Annotated[Path | None, typer.Option(help="Write here instead of stdout.")] = None) -> None:
	"""The IR's JSON Schema."""
	text = json.dumps(model.json_schema(), indent=1)
	if out:
		out.write_text(text + "\n", encoding="utf-8")
	else:
		print(text)


if __name__ == "__main__":
	app()
