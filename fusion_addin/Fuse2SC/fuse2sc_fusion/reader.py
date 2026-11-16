"""Reading a whole design into the IR (docs/ir.md), and its manifest of Fusion's own numbers.

Each timeline step is read twice: its inputs with the marker just before it (what a feature
consumes still exists), its result with the marker just after (sketches, occurrences, and the
snapshot of every body). The marker is put back and the document is never saved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

import adsk.fusion

from fuse2sc_ir import schema, units

from . import timeline
from .appearance import read_appearance
from .features import Ctx, read_feature
from .places import Places, body_key
from .safe import attempt, get
from .sketches import read_sketch
from .snapshot import corners, occurrence_matrices, snapshot

if TYPE_CHECKING:
	import adsk.core

	from .records import Record

log = logging.getLogger(__name__)
VERSION = "0.1.0"
CONSTRUCTION = frozenset({"ConstructionPlane", "ConstructionAxis", "ConstructionPoint", "JointOrigin"})
JOINTS = frozenset({"Joint", "AsBuiltJoint", "RigidGroup"})
Progress = Callable[[int, int, str], object]  # done, total, step name; the result is ignored


def design_of(doc: adsk.core.Document) -> adsk.fusion.Design:
	return adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))


def is_parametric(design: adsk.fusion.Design) -> bool:
	return design.designType == adsk.fusion.DesignTypes.ParametricDesignType


def fusion_info(doc: adsk.core.Document) -> Record:
	info: Record = {"name": doc.name}
	df = doc.dataFile
	if df is not None:
		errors: list[str] = []
		info.update(
			id=attempt(errors, "id", lambda: df.id),
			version=attempt(errors, "version", lambda: df.versionNumber),
			name=attempt(errors, "name", lambda: df.name) or doc.name,
			project=attempt(errors, "project", lambda: df.parentProject.name),
			hub=attempt(errors, "hub", lambda: df.parentProject.parentHub.name),
			folder=attempt(errors, "folder", lambda: _folder_path(df.parentFolder)),
		)
	return info


def _folder_path(folder: adsk.core.DataFolder | None) -> str:
	parts = []
	while folder is not None and not folder.isRoot:
		parts.append(folder.name)
		folder = folder.parentFolder
	return "/".join(reversed(parts))


def parameters(design: adsk.fusion.Design) -> tuple[list[Record], list[Record]]:
	"""(user parameters, model parameters); values are Fusion's internal (cm, rad)."""

	def record(p: adsk.fusion.Parameter) -> Record:
		return {
			"name": p.name,
			"expression": p.expression,
			"unit": p.unit,
			"comment": get(p, "comment", default=""),
			"value": get(p, "value"),
		}

	user = [record(p) for p in design.userParameters]
	names = {p["name"] for p in user}
	model = [record(p) for p in get(design, "allParameters", default=[]) if p.name not in names]
	return user, model


def components(design: adsk.fusion.Design) -> list[Record]:
	by_name: dict[str, Record] = {}
	for occ in design.rootComponent.allOccurrences:
		comp = by_name.setdefault(occ.component.name, {"name": occ.component.name, "occurrences": []})
		if occ.isReferencedComponent and occ.documentReference:
			comp["linked"] = occ.documentReference.dataFile.name
		parent = occ.assemblyContext
		while parent is not None:
			if parent.isReferencedComponent:
				comp["inside_linked"] = True
				break
			parent = parent.assemblyContext
		comp["occurrences"].append(
			{
				"path": occ.fullPathName,
				"parent": occ.assemblyContext.fullPathName if occ.assemblyContext else None,
				"matrix": units.matrix_mm(occ.transform2.asArray()),
				"visible": bool(occ.isVisible),
			}
		)
	return list(by_name.values())


def read_step(design: adsk.fusion.Design, places: Places, index: int) -> Record:
	item = design.timeline.item(index)
	entity = item.entity
	kind = type(entity).__name__
	step: Record = {
		"index": index,
		"name": item.name,
		"kind": kind,
		"suppressed": bool(item.isSuppressed),
		"unread": [],
	}
	try:
		step["component"] = entity.parentComponent.name
	except Exception:
		pass
	try:
		if kind == "Sketch":
			with timeline.marker_at(design, index, before=False):
				step["sketch"] = read_sketch(entity, places, index)
		elif kind == "Occurrence":
			with timeline.marker_at(design, index, before=False):
				occ = {
					"component": entity.component.name,
					"path": entity.fullPathName,
					"matrix": units.matrix_mm(entity.transform2.asArray()),
				}
				if entity.isReferencedComponent and entity.documentReference:
					occ["linked"] = entity.documentReference.dataFile.name
				step["occurrence"] = occ
		elif kind in JOINTS:
			with timeline.marker_at(design, index, before=True):
				before = occurrence_matrices(design)
			with timeline.marker_at(design, index, before=False):
				step["placements"] = {"before": before, "after": occurrence_matrices(design)}
		elif kind not in CONSTRUCTION and not step["suppressed"]:
			with timeline.marker_at(design, index, before=True):
				ctx = Ctx(design, places, index)
				step["feature"] = read_feature(ctx, entity)
				step["unread"] = ctx.unread
	except Exception as e:
		step["error"] = f"{type(e).__name__}: {e}"[:300]
		log.debug("step %d (%s) not fully read", index, item.name, exc_info=True)
	try:
		with timeline.marker_at(design, index, before=False):
			step["snapshot"] = snapshot(design, places)
	except Exception as e:
		step.setdefault("error", f"snapshot: {e}"[:300])
	return step


def read_document(
	doc: adsk.core.Document, progress: Progress | None = None, cancelled: Callable[[], bool] | None = None
) -> Record:
	"""The whole design as an IR document."""
	design = design_of(doc)
	out = schema.new_document(doc.name, VERSION)
	out["fusion"] = dict(fusion_info(doc), parametric=is_parametric(design))
	out["units"] = design.unitsManager.defaultLengthUnits
	errors = out["errors"]
	out["parameters"], out["model_parameters"] = attempt(errors, "parameters", lambda: parameters(design), ([], []))
	out["components"] = attempt(errors, "components", lambda: components(design), [])
	places = Places(design)
	if is_parametric(design):
		count = design.timeline.count
		for i in range(count):
			if cancelled and cancelled():
				raise KeyboardInterrupt("cancelled")
			if progress:
				progress(i, count, design.timeline.item(i).name)
			out["steps"].append(read_step(design, places, i))
	places.forget()
	out["appearance"] = attempt(errors, "appearance", lambda: read_appearance(design, places), {})
	return out


def _manifest_body(b: adsk.fusion.BRepBody, comp: adsk.fusion.Component, places: Places, errors: list[str]) -> Record:
	rec: Record = {
		"key": body_key(b),
		"name": b.name,
		"component": comp.name,
		"solid": bool(b.isSolid),
		"visible": attempt(errors, f"visible {b.name}", lambda: bool(b.isVisible), True),
		"volume": attempt(errors, f"volume {b.name}", lambda: units.mm3(b.volume)),
		"area": attempt(errors, f"area {b.name}", lambda: units.mm2(b.area)),
	}
	box = attempt(errors, f"box {b.name}", lambda: b.boundingBox)
	if box is not None:
		lo, hi = places.point(box.minPoint, comp), places.point(box.maxPoint, comp)
		rec["bbox"] = corners(lo, hi)
	return rec


def manifest(doc: adsk.core.Document, ir_doc: Record | None = None) -> Record:
	"""Fusion's own numbers for the design, which `fuse2sc verify` checks against."""
	design = design_of(doc)
	places = Places(design)
	errors: list[str] = []
	bodies: list[Record] = []
	for comp in design.allComponents:
		for b in comp.bRepBodies:
			bodies.append(_manifest_body(b, comp, places, errors))
	solid = [b for b in bodies if b["solid"] and b.get("volume") is not None]
	out: Record = {
		"schema": "fuse2sc.manifest/1",
		"fusion": dict(fusion_info(doc), parametric=is_parametric(design)),
		"parameters": attempt(errors, "parameters", lambda: parameters(design)[0], []),
		"bodies": bodies,
		"totals": {
			"bodies": len(bodies),
			"solid_bodies": len(solid),
			"volume_all": round(sum(b["volume"] for b in solid), 3),
			"volume_visible": round(sum(b["volume"] for b in solid if b["visible"]), 3),
		},
		"references": attempt(
			errors, "references", lambda: sorted({r.dataFile.name for r in doc.documentReferences if r.dataFile}), []
		),
		"timeline_items": design.timeline.count if is_parametric(design) else 0,
		"errors": errors,
	}
	if ir_doc is not None:
		kinds: dict[str, int] = {}
		for s in ir_doc.get("steps", []):
			kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
		out["feature_kinds"] = kinds
		out["schema_ir"] = ir_doc.get("schema")
	return out
