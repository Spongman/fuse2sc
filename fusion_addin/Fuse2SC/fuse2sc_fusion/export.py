"""Files Fusion writes for fuse2sc: a design folder (timeline.json, manifest.json, final.step,
final.stl), and Fusion's result at given timeline steps, for steps SolidCraft can't rebuild the same.

STEP export leaves hidden bodies out, so bodies and occurrences are shown for the export and hidden
again afterwards; bodies still invisible go through a scratch direct-modelling document.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import adsk.core
import adsk.fusion

from fuse2sc_ir import keys, schema, units

from . import timeline
from .places import Places
from .reader import design_of, manifest, read_document

if TYPE_CHECKING:
	from collections.abc import Iterable, Iterator

	from .records import Record


def write_json(path: Path, data: Any) -> None:
	"""Atomically: a crash mid-write never leaves half a file."""
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
	with os.fdopen(fd, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=1, ensure_ascii=False)
	os.replace(tmp, path)


@contextlib.contextmanager
def all_shown(items: Iterable[adsk.fusion.BRepBody | adsk.fusion.Occurrence]) -> Iterator[None]:
	"""Turn these bodies' and occurrences' light bulbs on for the block, then back off."""
	hidden = [x for x in items if not x.isLightBulbOn]
	for x in hidden:
		x.isLightBulbOn = True
	try:
		yield
	finally:
		for x in hidden:
			x.isLightBulbOn = False


def hidden_things(
	design: adsk.fusion.Design, comp: adsk.fusion.Component
) -> list[adsk.fusion.BRepBody | adsk.fusion.Occurrence]:
	"""The light bulbs that keep a component's bodies out of a STEP export."""
	things: list[adsk.fusion.BRepBody | adsk.fusion.Occurrence] = list(comp.bRepBodies)
	if comp != design.rootComponent:
		for occ in design.rootComponent.allOccurrencesByComponent(comp):
			things += list(occ.bRepBodies)
			o = occ
			while o is not None:
				things.append(o)
				o = o.assemblyContext
	return things


def export_scratch(bodies: Iterable[adsk.fusion.BRepBody], path: Path) -> None:
	"""Bodies to STEP through a scratch document, whatever their visibility."""
	app = adsk.core.Application.get()
	copies = [(b.name, adsk.fusion.TemporaryBRepManager.get().copy(b)) for b in bodies]
	previous = app.activeDocument
	scratch = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
	try:
		d = design_of(scratch)
		d.designType = adsk.fusion.DesignTypes.DirectDesignType
		for name, brep in copies:
			d.rootComponent.bRepBodies.add(brep).name = name
		d.exportManager.execute(d.exportManager.createSTEPExportOptions(str(path), d.rootComponent))
	finally:
		scratch.close(False)
		if previous:
			previous.activate()


def export_at_step(design: adsk.fusion.Design, index: int, components: list[str], folder: Path) -> list[Record]:
	"""The bodies of these components just after step `index`, one STEP per component:
	[{component, path, matrix}] with the component's world placement (row-major, mm)."""
	folder = Path(folder)
	folder.mkdir(parents=True, exist_ok=True)
	out: list[Record] = []
	with timeline.marker_at(design, index, before=False):
		places = Places(design)
		for name in components:
			comp = design.rootComponent if name == design.rootComponent.name else design.allComponents.itemByName(name)
			if comp is None:
				continue
			path = folder / f"step{index}-{keys.safe_filename(name)}.step"
			with all_shown(hidden_things(design, comp)):
				em = design.exportManager
				em.execute(em.createSTEPExportOptions(str(path), comp))
				still_hidden = [b for b in comp.bRepBodies if not b.isVisible]
			matrix = units.matrix_mm(places.matrix(comp).asArray())
			out.append({"component": name, "path": str(path), "matrix": matrix})
			if still_hidden:
				extra = path.with_name(path.stem + "-hidden.step")
				export_scratch(still_hidden, extra)
				out.append({"component": name, "path": str(extra), "matrix": matrix})
	return out


def export_steps_into(design: adsk.fusion.Design, design_dir: Path, requests: list[Record]) -> int:
	"""Export requested steps ([{index, components}]) into <design>/steps, recorded in its
	index.json. Returns how many files were written."""
	folder = Path(design_dir) / "steps"
	index_path = folder / "index.json"
	try:
		index = json.loads(index_path.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		index = {}
	written = 0
	for req in requests:
		files = export_at_step(design, int(req["index"]), req["components"], folder)
		entries = [dict(f, path=Path(f["path"]).name) for f in files]
		replaced = {e["component"] for e in entries}
		key = str(req["index"])
		index[key] = [e for e in index.get(key, []) if e["component"] not in replaced] + entries
		written += len(files)
	write_json(index_path, index)
	return written


def export_final(design: adsk.fusion.Design, folder: Path, step: bool = True, stl: bool = True) -> dict[str, str]:
	"""final.step (every body) and final.stl (for the 3D viewer)."""
	written: dict[str, str] = {}
	root = design.rootComponent
	em = design.exportManager
	with all_shown([b for c in design.allComponents for b in c.bRepBodies]):
		if step:
			path = Path(folder) / "final.step"
			em.execute(em.createSTEPExportOptions(str(path), root))
			written["step"] = str(path)
		if stl:
			path = Path(folder) / "final.stl"
			options = em.createSTLExportOptions(root, str(path))
			options.isOneFilePerBodyExported = False
			with contextlib.suppress(Exception):
				options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
			em.execute(options)
			written["stl"] = str(path)
	return written


def write_design(
	doc: adsk.core.Document,
	design_dir: Path,
	ir_doc: Record | None = None,
	export_step: bool = True,
	export_stl: bool = True,
) -> Record:
	"""timeline.json, manifest.json, final.step and final.stl into a design folder."""
	design_dir = Path(design_dir)
	design_dir.mkdir(parents=True, exist_ok=True)
	ir_doc = ir_doc if ir_doc is not None else read_document(doc)
	schema.dump(ir_doc, str(design_dir / "timeline.json"))
	man = manifest(doc, ir_doc)
	try:
		man["exported"] = export_final(design_of(doc), design_dir, export_step, export_stl)
	except Exception as e:
		man["errors"].append(f"final export: {e}")
	write_json(design_dir / "manifest.json", man)
	return man
