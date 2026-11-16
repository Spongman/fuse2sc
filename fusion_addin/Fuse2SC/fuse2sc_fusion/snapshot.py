"""Fusion's ground truth at a point in the timeline: every body's volume, area, centre of
mass, box and visibility, in world mm. The build compares SolidCraft with this after every step."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fuse2sc_ir import units

from .places import body_key

if TYPE_CHECKING:
	from collections.abc import Sequence

	import adsk.fusion

	from .places import Places
	from .records import Record


def corners(a: Sequence[float], b: Sequence[float]) -> list[list[float]]:
	"""[[min], [max]] of two opposite corners (a turned placement can swap them)."""
	return [[min(x, y) for x, y in zip(a, b, strict=True)], [max(x, y) for x, y in zip(a, b, strict=True)]]


def body_record(places: Places, comp: adsk.fusion.Component, b: adsk.fusion.BRepBody) -> Record:
	rec: Record = {"visible": bool(b.isLightBulbOn)}
	try:
		rec["volume"] = units.mm3(b.volume)
	except Exception:
		rec["volume"] = None
	try:
		rec["area"] = units.mm2(b.area)
	except Exception:
		pass
	try:
		rec["com"] = places.point(b.physicalProperties.centerOfMass, comp)
	except Exception:
		pass
	try:
		box = b.boundingBox
		lo, hi = places.point(box.minPoint, comp), places.point(box.maxPoint, comp)
		rec["bbox"] = corners(lo, hi)
	except Exception:
		pass
	return rec


def snapshot(design: adsk.fusion.Design, places: Places) -> dict[str, Record]:
	places.forget()
	out: dict[str, Record] = {}
	for comp in design.allComponents:
		for b in comp.bRepBodies:
			out[body_key(b)] = body_record(places, comp, b)
	return out


def occurrence_matrices(design: adsk.fusion.Design) -> dict[str, list[float]]:
	"""Every occurrence's world matrix (mm) by its full path, for steps that move components."""
	out: dict[str, list[float]] = {}
	for o in design.rootComponent.allOccurrences:
		try:
			out[o.fullPathName] = units.matrix_mm(o.transform2.asArray())
		except Exception:
			continue
	return out
