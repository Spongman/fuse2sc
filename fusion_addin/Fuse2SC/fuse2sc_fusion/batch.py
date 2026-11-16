"""Exporting many designs into a workspace: a hub, or some of its projects.

Resumable and safe to stop: a design whose manifest already records this Fusion version (and the
current IR schema) is skipped. Only documents this run opened are closed, never saved. One function
names design folders for both the batch and the single-design export, so two designs with the same
name in one Fusion folder never overwrite each other (each gets part of its id).
"""

from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from fuse2sc_ir import keys, schema

from . import export
from .reader import design_of, read_document
from .safe import attempt, items

if TYPE_CHECKING:
	from collections.abc import Iterator

	import adsk.core

	from .records import Record

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], bool]  # message, done, total -> keep going?


def design_files(
	folder: adsk.core.DataFolder, rel: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], adsk.core.DataFile, list[str]]]:
	"""(folder path, data file, names of the designs beside it) for every design under a folder."""
	files = [f for f in items(folder.dataFiles) if (f.fileExtension or "").lower() == "f3d"]
	names = [f.name for f in files]
	for f in files:
		yield rel, f, names
	for sub in items(folder.dataFolders):
		yield from design_files(sub, (*rel, sub.name))


def design_dir(
	root: Path,
	*,
	hub: str,
	project: str,
	folders: tuple[str, ...],
	data_file: adsk.core.DataFile,
	siblings: list[str],
) -> Path:
	name = data_file.name
	if siblings.count(name) > 1:
		name = f"{name} ({(data_file.id or '')[-8:]})"
	return root.joinpath(*(keys.safe_filename(p) for p in (hub, project, *folders, name)))


def design_dir_for(root: Path, doc: adsk.core.Document) -> Path:
	"""design_dir() for a document open now."""
	df = doc.dataFile
	if df is None:
		return root / "_unsaved" / keys.safe_filename(doc.name)
	folders: list[str] = []
	folder = df.parentFolder
	while folder is not None and not folder.isRoot:
		folders.append(folder.name)
		folder = folder.parentFolder
	siblings = [f.name for f in items(df.parentFolder.dataFiles)]
	return design_dir(
		root,
		hub=df.parentProject.parentHub.name,
		project=df.parentProject.name,
		folders=tuple(reversed(folders)),
		data_file=df,
		siblings=siblings,
	)


def up_to_date(ddir: Path, data_file: adsk.core.DataFile) -> bool:
	try:
		m = json.loads((ddir / "manifest.json").read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return False
	fusion = m.get("fusion") or {}
	return (
		fusion.get("id") == data_file.id
		and fusion.get("version") == data_file.versionNumber
		and m.get("schema_ir") == schema.SCHEMA_VERSION
		and (ddir / "timeline.json").is_file()
		and not any(str(e).startswith("final export") for e in m.get("errors", []))
	)


def open_design(app: adsk.core.Application, data_file: adsk.core.DataFile) -> tuple[adsk.core.Document, bool]:
	"""(document, opened_here). rollTo works on the active document only: open it visibly."""
	for d in items(app.documents):
		try:
			if d.dataFile and d.dataFile.id == data_file.id and d.dataFile.versionNumber == data_file.versionNumber:
				d.activate()
				return d, False
		except Exception:
			continue
	return app.documents.open(data_file, True), True


def export_hub(
	app: adsk.core.Application, root: Path, settings: Record, progress: Progress, note: Callable[[str], None]
) -> Record:
	designs = root / (settings.get("designs_dir") or "designs")
	hub_name = settings.get("hub") or ""
	hub = next((h for h in items(app.data.dataHubs) if h.name == hub_name), None) if hub_name else app.data.activeHub
	if hub is None:
		raise RuntimeError(f"hub {hub_name!r} not found")
	opts = settings.get("export") or {}
	only, limit = set(opts.get("projects") or []), int(opts.get("limit") or 0)
	todo: list[tuple[adsk.core.DataProject, tuple[str, ...], adsk.core.DataFile, list[str]]] = []
	for project in items(hub.dataProjects):
		if not only or project.name in only:
			progress(f"Listing {project.name}…", 0, 1)
			todo += [(project, rel, df, sib) for rel, df, sib in design_files(project.rootFolder)]
	summary: Record = {"exported": [], "skipped": [], "failed": []}
	original = app.activeDocument
	for n, (project, rel, df, siblings) in enumerate(todo):
		if limit and len(summary["exported"]) >= limit:
			break
		ddir = design_dir(designs, hub=hub.name, project=project.name, folders=rel, data_file=df, siblings=siblings)
		if not progress(f"{df.name} ({n + 1} of {len(todo)})", n, len(todo)):
			note("cancelled")
			break
		if up_to_date(ddir, df):
			summary["skipped"].append(str(ddir))
			continue
		doc, opened = None, False
		try:
			doc, opened = open_design(app, df)
			export.write_design(doc, ddir, read_document(doc), export_step=opts.get("export_step", True))
			summary["exported"].append(str(ddir))
			note(f"exported {ddir}")
		except Exception as e:  # one design never stops the hub
			summary["failed"].append({"design": str(ddir), "error": str(e)[:300]})
			note(f"FAILED {df.name}: {e}")
		finally:
			if doc is not None and opened:
				attempt([], "close", functools.partial(doc.close, False))
	if original is not None:
		attempt([], "activate", original.activate)
	export.write_json(root / (settings.get("reports_dir") or "reports") / "export-summary.json", summary)
	return summary


def export_requested(
	app: adsk.core.Application, root: Path, settings: Record, progress: Progress, note: Callable[[str], None]
) -> int:
	"""For each workspace design with a needed-steps.json: open it, export what the build asked for."""
	designs = root / (settings.get("designs_dir") or "designs")
	todo = sorted(p.parent for p in designs.rglob("needed-steps.json") if (p.parent / "manifest.json").is_file())
	written = 0
	original = app.activeDocument
	for n, ddir in enumerate(todo):
		if not progress(f"Fusion results for {ddir.name}", n, len(todo)):
			break
		need = json.loads((ddir / "needed-steps.json").read_text(encoding="utf-8")).get("steps", [])
		fid = (json.loads((ddir / "manifest.json").read_text(encoding="utf-8")).get("fusion") or {}).get("id")
		doc, opened = None, False
		try:
			df = app.data.findFileById(fid) if fid else None
			if df is None:
				raise RuntimeError(f"design {fid} not found in Fusion")
			doc, opened = open_design(app, df)
			written += export.export_steps_into(design_of(doc), ddir, need)
			(ddir / "needed-steps.json").unlink()
			note(f"exported {len(need)} step result(s) for {ddir}")
		except Exception as e:
			note(f"FAILED {ddir}: {e}")
		finally:
			if doc is not None and opened:
				attempt([], "close", functools.partial(doc.close, False))
	if original is not None:
		attempt([], "activate", original.activate)
	return written
