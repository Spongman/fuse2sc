"""Where things are: world placement of components, and points and directions in world mm.

Fusion gives geometry in a component's own space (cm). Bodies are built flat in the world while
the timeline replays, so everything the extractor writes is in world coordinates: a component's
geometry goes through the transform of its first occurrence (later occurrences become copies at
the end of the build, placed relative to the first).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import adsk.core

from fuse2sc_ir import units

if TYPE_CHECKING:
	import adsk.fusion

	from .records import Point


class Places:
	def __init__(self, design: adsk.fusion.Design) -> None:
		self.design = design
		self.root = design.rootComponent
		self._cache: dict[str, adsk.core.Matrix3D] = {}

	def matrix(self, comp: adsk.fusion.Component | None) -> adsk.core.Matrix3D:
		"""World transform (Matrix3D) of a component's first occurrence; identity for the root."""
		if comp is None or comp == self.root:
			return adsk.core.Matrix3D.create()
		key = comp.name
		if key not in self._cache:
			occs = self.root.allOccurrencesByComponent(comp)
			self._cache[key] = occs.item(0).transform2 if occs.count else adsk.core.Matrix3D.create()
		return self._cache[key]

	def forget(self) -> None:
		"""Placements change at joints and moves: drop what was cached."""
		self._cache = {}

	def point(self, p: adsk.core.Point3D, comp: adsk.fusion.Component | None = None) -> Point:
		"""A Point3D in `comp`'s space as world [x, y, z] in mm."""
		q = p.copy()
		q.transformBy(self.matrix(comp))
		return [units.mm(q.x), units.mm(q.y), units.mm(q.z)]

	def direction(self, v: adsk.core.Vector3D, comp: adsk.fusion.Component | None = None) -> list[float]:
		"""A Vector3D in `comp`'s space as a world unit vector."""
		w = v.copy()
		w.transformBy(self.matrix(comp))
		n = (w.x * w.x + w.y * w.y + w.z * w.z) ** 0.5 or 1.0
		return [round(w.x / n, 9), round(w.y / n, 9), round(w.z / n, 9)]


def native(x: Any) -> Any:
	"""The object behind an assembly-context proxy (or the object itself)."""
	try:
		return x.nativeObject or x
	except Exception:
		return x


def owner_of(entity: Any) -> adsk.fusion.Component | None:
	"""The component a body, face, edge, vertex, sketch entity or construction lives in."""
	n = native(entity)
	for path in (
		("body", "parentComponent"),
		("parentComponent",),
		("parentSketch", "parentComponent"),
		("component",),
	):
		obj = n
		try:
			for attr in path:
				obj = getattr(obj, attr)
			if obj is not None:
				return obj
		except Exception:
			continue
	return None


def body_key(body: adsk.fusion.BRepBody) -> str:
	"""component/body: unique across the design even where components reuse body names."""
	b = native(body)
	return "%s/%s" % (b.parentComponent.name, b.name)
