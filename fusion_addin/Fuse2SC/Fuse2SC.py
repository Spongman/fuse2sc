"""fuse2sc for Fusion: designs to SolidCraft with their parametric history.

Commands, under Utilities > Add-Ins > SolidCraft:

- Export to SolidCraft…: the open design (and the designs it links to) to .sc files, built and
  checked outside Fusion while Fusion stays usable.
- Export to workspace…: the open design, or a whole hub, into an fuse2sc workspace for `fuse2sc build`.
- Export requested Fusion results: the step results that builds asked for (needed-steps.json).
- SolidCraft bridge on/off: lets `fuse2sc build --bridge` in a terminal ask this Fusion for results.

Fusion only reads and exports here; building, checking and reporting happen in the fuse2sc process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import adsk.core
import adsk.fusion

ADDIN = Path(__file__).resolve().parent
if str(ADDIN) not in sys.path:
	sys.path.insert(0, str(ADDIN))

from fuse2sc_fusion import batch, bridge, export, launcher  # noqa: E402
from fuse2sc_fusion.reader import design_of, read_document  # noqa: E402
from fuse2sc_ir import keys  # noqa: E402

PANEL = "SolidScriptsAddinsPanel"
DROPDOWN = "Fuse2SC_Menu"
PALETTE = "Fuse2SC_Status"
BUILD_EVENT = "Fuse2SC_BuildEvent"
MARKER = "fusion-to-solidcraft.json"


# --- settings --------------------------------------------------------------------------------------


def config_dir() -> Path:
	base = os.environ.get("APPDATA") if os.name == "nt" else None
	return Path(base) / "fuse2sc" if base else Path.home() / ".config" / "fuse2sc"


class Settings(dict[str, Any]):
	path = config_dir() / "addin.json"

	@classmethod
	def load(cls) -> "Settings":
		try:
			return cls(json.loads(cls.path.read_text(encoding="utf-8")))
		except (OSError, ValueError):
			return cls()

	def save(self) -> None:
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self.path.write_text(json.dumps(self, indent=1), encoding="utf-8")


def open_path(path: str, reveal: bool = False) -> None:
	if sys.platform == "darwin":
		subprocess.Popen(["open", "-R", path] if reveal else ["open", path])
	elif os.name == "nt":
		if reveal:
			subprocess.Popen(["explorer", "/select,", path])
		else:
			os.startfile(path)
	else:
		subprocess.Popen(["xdg-open", os.path.dirname(path) if reveal else path])


# --- the status panel ------------------------------------------------------------------------------


class StatusPanel:
	"""A floating HTML panel showing progress; it stays open after a run."""

	def __init__(self, addin: AddIn) -> None:
		self.addin, self.palette, self.ready = addin, None, False

	def open(self, title: str = "SolidCraft export") -> None:
		ui = self.addin.ui
		pal = ui.palettes.itemById(PALETTE)
		if pal is None:
			self.ready = False
			pal = ui.palettes.add(PALETTE, "SolidCraft", str(ADDIN / "status.html"), True, True, True, 420, 560)
			pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateFloating
			self.addin.listen(pal.incomingFromHTML, _HtmlHandler(self.on_message))
			deadline = time.monotonic() + 5
			while not self.ready and time.monotonic() < deadline:  # the page loads in the background
				adsk.doEvents()
				time.sleep(0.02)
		pal.isVisible = True
		self.palette = pal
		self.send(reset=True, title=title)

	def send(self, **message: Any) -> None:
		if self.palette is not None:
			try:
				self.palette.sendInfoToHTML("update", json.dumps(message))
			except Exception:
				pass

	def line(self, kind: str, text: str) -> None:
		self.send(lines=[{"kind": kind, "text": text}])

	def on_message(self, action: str) -> None:
		run = self.addin.run_state
		if action == "ready":
			self.ready = True
		elif action == "open" and run.out:
			open_path(run.out)
		elif action == "report" and run.report:
			open_path(run.report)
		elif action == "folder" and run.out:
			open_path(run.out, reveal=True)
		elif action == "cancel" and run.build:
			run.build.cancel()


class _HtmlHandler(adsk.core.HTMLEventHandler):
	def __init__(self, on_message: Callable[[str], None]) -> None:
		super().__init__()
		self.on_message = on_message

	def notify(self, args: adsk.core.HTMLEventArgs) -> None:
		a = adsk.core.HTMLEventArgs.cast(args)
		try:
			self.on_message(a.action)
			a.returnData = "OK"
		except Exception:
			a.returnData = traceback.format_exc()


# --- the add-in --------------------------------------------------------------------------------------


@dataclass
class RunState:
	build: launcher.Build | None = None
	bridge: bridge.Bridge | None = None
	out: str | None = None
	report: str | None = None
	from_fusion: int = 0
	failed: int = 0


@dataclass(frozen=True)
class CommandSpec:
	id: str
	name: str
	tooltip: str
	run: Callable[["AddIn"], None]


class AddIn:
	def __init__(self) -> None:
		self.app = adsk.core.Application.get()
		self.ui = self.app.userInterface
		self.handlers: list[Any] = []  # Fusion drops handlers that nothing references
		self.panel = StatusPanel(self)
		self.run_state = RunState()
		self.terminal_bridge: bridge.Bridge | None = None

	def listen(self, event: adsk.core.Event, handler: adsk.core.EventHandler) -> None:
		event.add(handler)
		self.handlers.append(handler)

	# --- lifetime ------------------------------------------------------------------------------

	def start(self) -> None:
		self.listen(self.app.registerCustomEvent(BUILD_EVENT), _BuildEventHandler(self.on_build_event))
		panel = self.ui.allToolbarPanels.itemById(PANEL)
		menu = panel.controls.itemById(DROPDOWN) or panel.controls.addDropDown("SolidCraft", "", DROPDOWN)
		for spec in COMMANDS:
			definition = self.ui.commandDefinitions.itemById(spec.id) or self.ui.commandDefinitions.addButtonDefinition(
				spec.id, spec.name, spec.tooltip
			)
			self.listen(definition.commandCreated, _CreatedHandler(self, spec))
			if not menu.controls.itemById(spec.id):
				menu.controls.addCommand(definition)

	def stop(self) -> None:
		for b in (self.run_state.bridge, self.terminal_bridge):
			if b:
				b.stop()
		try:
			self.app.unregisterCustomEvent(BUILD_EVENT)
		except Exception:
			pass
		if pal := self.ui.palettes.itemById(PALETTE):
			pal.deleteMe()
		panel = self.ui.allToolbarPanels.itemById(PANEL)
		if panel and (menu := panel.controls.itemById(DROPDOWN)):
			menu.deleteMe()
		for spec in COMMANDS:
			if d := self.ui.commandDefinitions.itemById(spec.id):
				d.deleteMe()

	# --- shared pieces ---------------------------------------------------------------------------

	def active_document(self) -> adsk.core.Document | None:
		doc = self.app.activeDocument
		if doc is None or design_of(doc) is None:
			self.ui.messageBox("Open a design first.")
			return None
		return doc

	def solidcraft_cli(self) -> str | None:
		settings = Settings.load()
		if (known := settings.get("cli")) and Path(known).is_file():
			return known
		if (env := os.environ.get("SOLIDCRAFT_CLI")) and Path(env).is_file():
			return env
		dlg = self.ui.createFileDialog()
		dlg.title = "Where is solidcraft-cli? (it comes with SolidCraft)"
		if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
			return None
		settings["cli"] = dlg.filename
		settings.save()
		return dlg.filename

	def workspace(self, ask: bool = True) -> tuple[Path, dict[str, Any]] | None:
		"""(root, settings) of the workspace, asking for it if `ask`; None if there is none."""
		settings = Settings.load()
		root = Path(settings["workspace"]) if settings.get("workspace") else None
		if root is None or not (root / MARKER).is_file():
			if not ask:
				return None
			dlg = self.ui.createFolderDialog()
			dlg.title = "Your fuse2sc workspace (the folder with fusion-to-solidcraft.json; `fuse2sc init` makes one)"
			if dlg.showDialog() != adsk.core.DialogResults.DialogOK:
				return None
			root = Path(dlg.folder)
			if not (root / MARKER).is_file():
				self.ui.messageBox(f"{root} has no {MARKER}. Run `fuse2sc init` there first.")
				return None
			settings["workspace"] = str(root)
			settings.save()
		return root, json.loads((root / MARKER).read_text(encoding="utf-8"))

	def progress(self, title: str) -> tuple[adsk.core.ProgressDialog, Callable[[str, int, int], bool]]:
		dialog = self.ui.createProgressDialog()
		dialog.isCancelButtonShown = True
		dialog.show(title, "Starting…", 0, 1, 0)

		def update(message: str, done: int, total: int) -> bool:
			dialog.message, dialog.maximumValue, dialog.progressValue = message, max(total, 1), done
			self.panel.send(phase=message, value=done, max=max(total, 1))
			adsk.doEvents()
			return not dialog.wasCancelled

		return dialog, update

	# --- build events (from the build's reader thread, delivered on Fusion's) ---------------------

	def on_build_event(self, e: dict[str, Any]) -> None:
		run, kind = self.run_state, e.get("event")
		if kind == "step":
			self.panel.send(phase=f"Building: {e.get('name')}", value=e.get("index", 0) + 1, max=max(e.get("of", 1), 1))
		elif kind == "fusion":
			run.from_fusion += 1
			self.panel.send(from_fusion=run.from_fusion)
			self.panel.line("fusion", f"step {e.get('step')} {e.get('name', '')}: Fusion's result brought in")
		elif kind == "failed":
			run.failed += 1
			self.panel.send(failed=run.failed)
			self.panel.line("failed", e.get("text", ""))
		elif kind in ("design", "message", "phase"):
			self.panel.line("note", e.get("text", ""))
		elif kind == "result":
			run.report = e.get("report") or run.report
			run.out = e.get("out") or run.out
			counts = ", ".join(f"{n} {k}" for k, n in sorted((e.get("counts") or {}).items()))
			self.panel.line("note", f"{e.get('design')}: {e.get('category')} ({counts})")
		elif kind == "exit":
			if run.bridge:
				run.bridge.stop()
			run.bridge, run.build = None, None
			if e.get("code") in (0, 1):
				self.panel.send(phase="Done.", summary=f"Saved to {run.out or '?'}", done=True, value=1, max=1)
			else:
				self.panel.send(phase="The build stopped with an error.", done=True)
				self.panel.line("failed", (e.get("stderr") or "")[-800:])

	def fire_build_event(self, e: dict[str, Any]) -> None:
		self.app.fireCustomEvent(BUILD_EVENT, json.dumps(e))


# --- commands ----------------------------------------------------------------------------------------


def export_to_solidcraft(addin: AddIn) -> None:
	"""The open design and its linked designs, read here, built and checked by fuse2sc."""
	if addin.run_state.build:
		addin.ui.messageBox("A SolidCraft export is already running.")
		return
	doc = addin.active_document()
	if doc is None:
		return
	cli = addin.solidcraft_cli()
	if not cli:
		return
	settings = Settings.load()
	dlg = addin.ui.createFileDialog()
	dlg.title, dlg.filter = "Export to SolidCraft", "SolidCraft design (*.sc)"
	dlg.initialFilename = keys.safe_filename(doc.name) + ".sc"
	if settings.get("folder") and Path(settings["folder"]).is_dir():
		dlg.initialDirectory = settings["folder"]
	if dlg.showSave() != adsk.core.DialogResults.DialogOK:
		return
	out = Path(dlg.filename).with_suffix(".sc")
	settings["folder"] = str(out.parent)
	settings.save()
	addin.run_state = RunState(out=str(out))
	addin.panel.open(doc.name)
	dialog, update = addin.progress("Export to SolidCraft")

	def read_into(document: adsk.core.Document, stem: str) -> Path:
		work = out.parent / f"{stem}.fuse2sc"
		ir = read_document(
			document, lambda i, n, name: update(f"{document.name}: {name}", i, n), lambda: dialog.wasCancelled
		)
		ir["fusion"]["name"] = stem
		export.write_design(document, work, ir, export_step=False, export_stl=True)
		return work / "timeline.json"

	timelines: list[Path] = []
	try:
		seen = set()
		for occ in design_of(doc).rootComponent.allOccurrences:
			if not (occ.isReferencedComponent and occ.documentReference):
				continue
			data_file = occ.documentReference.dataFile
			stem = keys.safe_filename(data_file.name)
			if stem in seen:
				continue
			seen.add(stem)
			linked, opened = batch.open_design(addin.app, data_file)
			try:
				timelines.append(read_into(linked, stem))
			finally:
				if opened:
					linked.close(False)
				doc.activate()
		timelines.append(read_into(doc, out.stem))
	except KeyboardInterrupt:
		addin.panel.send(phase="Cancelled.")
		return
	finally:
		dialog.hide()
	addin.panel.send(phase="Building in SolidCraft…", value=0, max=1)
	run = addin.run_state
	run.bridge = bridge.Bridge(addin.app, out.parent / f"{out.stem}.fuse2sc" / "steps", addin.handlers).start()
	run.build = launcher.Build(
		timelines, out.parent, cli=cli, bridge=run.bridge, on_event=addin.fire_build_event, extra=["--viewer"]
	)


def export_to_workspace(addin: AddIn) -> None:
	if (found := addin.workspace()) is None:
		return
	root, ws = found
	hub = f" '{ws.get('hub')}'" if ws.get("hub") else ""
	choice = addin.ui.messageBox(
		f"Export the whole hub{hub} into\n{root} ?\n\nYes: the whole hub (resumable: designs already exported at "
		"their current version are skipped)\nNo: only the open design",
		"fuse2sc",
		adsk.core.MessageBoxButtonTypes.YesNoCancelButtonType,
	)
	if choice == adsk.core.DialogResults.DialogCancel:
		return
	addin.panel.open("Export to workspace")
	dialog, update = addin.progress("Export to workspace")
	try:
		if choice == adsk.core.DialogResults.DialogYes:
			s = batch.export_hub(addin.app, root, ws, update, lambda t: addin.panel.line("note", t))
			message = f"Exported {len(s['exported'])}, up to date {len(s['skipped'])}, failed {len(s['failed'])}."
		else:
			doc = addin.active_document()
			if doc is None:
				return
			target = batch.design_dir_for(root / (ws.get("designs_dir") or "designs"), doc)
			ir = read_document(doc, lambda i, n, name: update(f"Reading {name}", i, n), lambda: dialog.wasCancelled)
			export.write_design(doc, target, ir)
			message = f"Exported to {target}."
	except KeyboardInterrupt:
		message = "Cancelled."
	finally:
		dialog.hide()
	addin.panel.send(phase=f"{message} Next: run `fuse2sc build` in the workspace.")


def export_requested_results(addin: AddIn) -> None:
	if (found := addin.workspace()) is None:
		return
	root, ws = found
	addin.panel.open("Export requested Fusion results")
	dialog, update = addin.progress("Export requested Fusion results")
	try:
		n = batch.export_requested(addin.app, root, ws, update, lambda t: addin.panel.line("note", t))
	finally:
		dialog.hide()
	addin.panel.send(phase=f"Exported {n} step result(s). Run `fuse2sc build` again.")


def toggle_bridge(addin: AddIn) -> None:
	connection = config_dir() / "bridge.json"
	if addin.terminal_bridge:
		addin.terminal_bridge.stop()
		addin.terminal_bridge = None
		connection.unlink(missing_ok=True)
		addin.ui.messageBox("The SolidCraft bridge is off.")
		return
	root = found[0] if (found := addin.workspace(ask=False)) else None
	steps = (root or Path.home()) / ".fuse2sc-bridge-steps"
	addin.terminal_bridge = bridge.Bridge(addin.app, steps, addin.handlers).start()
	addin.terminal_bridge.write_connection_file(connection)
	addin.ui.messageBox(
		f"The SolidCraft bridge is on ({addin.terminal_bridge.url}).\n`fuse2sc build --bridge` can now ask "
		"Fusion for the open design's step results. Run this command again to switch it off."
	)


COMMANDS = (
	CommandSpec(
		"Fuse2SC_Export",
		"Export to SolidCraft…",
		"Convert the open design to SolidCraft, keeping its history.",
		export_to_solidcraft,
	),
	CommandSpec(
		"Fuse2SC_Workspace",
		"Export to workspace…",
		"Export the open design, or a whole hub, into an fuse2sc workspace.",
		export_to_workspace,
	),
	CommandSpec(
		"Fuse2SC_Fallbacks",
		"Export requested Fusion results",
		"Export the step results that fuse2sc builds asked for (needed-steps.json).",
		export_requested_results,
	),
	CommandSpec(
		"Fuse2SC_Bridge",
		"SolidCraft bridge on/off",
		"Let `fuse2sc build --bridge` ask this Fusion for step results.",
		toggle_bridge,
	),
)


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
	def __init__(self, addin: AddIn, spec: CommandSpec) -> None:
		super().__init__()
		self.addin, self.spec = addin, spec

	def notify(self, args: adsk.core.CommandCreatedEventArgs) -> None:
		self.addin.listen(args.command.execute, _ExecuteHandler(self.addin, self.spec))


class _ExecuteHandler(adsk.core.CommandEventHandler):
	def __init__(self, addin: AddIn, spec: CommandSpec) -> None:
		super().__init__()
		self.addin, self.spec = addin, spec

	def notify(self, args: adsk.core.CommandEventArgs) -> None:
		try:
			self.spec.run(self.addin)
		except Exception:
			self.addin.panel.send(phase="Failed.")
			self.addin.panel.line("failed", traceback.format_exc().strip().splitlines()[-1])
			self.addin.ui.messageBox("fuse2sc failed:\n" + traceback.format_exc())


class _BuildEventHandler(adsk.core.CustomEventHandler):
	def __init__(self, on_event: Callable[[dict[str, Any]], None]) -> None:
		super().__init__()
		self.on_event = on_event

	def notify(self, args: adsk.core.CustomEventArgs) -> None:
		try:
			self.on_event(json.loads(args.additionalInfo))
		except Exception:
			pass


_running: list[AddIn] = []  # Fusion calls run() and stop() at module level: the add-in lives here


def run(context: Any) -> None:
	try:
		addin = AddIn()
		addin.start()
		_running.append(addin)
	except Exception:
		adsk.core.Application.get().userInterface.messageBox("fuse2sc could not start:\n" + traceback.format_exc())


def stop(context: Any) -> None:
	while _running:
		_running.pop().stop()
