"""Reading a sketch: points, curves, constraints and dimensions, in sketch mm.

Ids are positional (P<n> for the n-th sketch point, C<n> for the n-th curve), so two reads of the
same sketch at different timeline positions name things the same way. Entity tokens are only used
as a fast path within one read; Fusion can hand out a different token for the same entity later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fuse2sc_ir import units

from .places import native

if TYPE_CHECKING:
	import adsk.core
	import adsk.fusion

	from .places import Places
	from .records import Point, Polyline, Record

# Properties of constraint and dimension objects that hold the entities they act on.
REF_ROLES = (
	"point",
	"pointOne",
	"pointTwo",
	"line",
	"lineOne",
	"lineTwo",
	"curveOne",
	"curveTwo",
	"entity",
	"entityOne",
	"entityTwo",
	"circleOne",
	"circleTwo",
	"midPointCurve",
	"symmetryLine",
	"centerPoint",
)
ORIENTATION = {0: "aligned", 1: "horizontal", 2: "vertical"}
STROKE_TOL = 0.0005  # cm


def xy(p: adsk.core.Point3D) -> Point:
	return [units.mm(p.x), units.mm(p.y)]


def strokes(curve_geometry: adsk.core.Curve3D, limit: int = 400) -> Polyline:
	"""A sketch-space curve as points in mm (its own direction)."""
	try:
		ev = curve_geometry.evaluator
		ok, t0, t1 = ev.getParameterExtents()
		ok, pts = ev.getStrokes(t0, t1, STROKE_TOL)
		pts = list(pts) if ok else []
	except Exception:
		return []
	if len(pts) > limit:
		step = len(pts) / float(limit)
		pts = [pts[int(i * step)] for i in range(limit)] + [pts[-1]]
	return [xy(p) for p in pts]


def point_at_middle(curve_geometry: adsk.core.Curve3D) -> adsk.core.Point3D | None:
	ev = curve_geometry.evaluator
	ok, t0, t1 = ev.getParameterExtents()
	ok, p = ev.getPointAtParameter(t0 + 0.5 * (t1 - t0))
	return p if ok else None


class SketchIndex:
	"""Positional ids for a sketch's points and curves."""

	def __init__(self, sketch: adsk.fusion.Sketch) -> None:
		self.sketch = native(sketch)
		self.points = list(self.sketch.sketchPoints)
		self.curves = list(self.sketch.sketchCurves)
		self._tok: dict[str | int, str] = {}
		for i, p in enumerate(self.points):
			self._tok[_token(p)] = "P%d" % i
		for i, c in enumerate(self.curves):
			self._tok[_token(c)] = "C%d" % i

	def id_of(self, entity: Any) -> str | None:
		if entity is None:
			return None
		n = native(entity)
		hit = self._tok.get(_token(n))
		if hit:
			return hit
		for i, c in enumerate(self.curves):
			if c == n:
				return "C%d" % i
		for i, p in enumerate(self.points):
			if p == n:
				return "P%d" % i
		return None

	def curve_through(self, p_cm: adsk.core.Point3D, hint: adsk.fusion.SketchCurve | None = None) -> str | None:
		"""The id of the sketch curve passing through a sketch-space point: the hint if it does,
		else the first curve that does. (Fusion can name the wrong one of two collinear lines.)"""
		order = ([hint] if hint is not None else []) + self.curves
		for c in order:
			try:
				ev = c.geometry.evaluator
				ok, t = ev.getParameterAtPoint(p_cm)
				if not ok:
					continue
				ok, t0, t1 = ev.getParameterExtents()
				if not (min(t0, t1) - 1e-9 <= t <= max(t0, t1) + 1e-9):
					continue
				ok, q = ev.getPointAtParameter(t)
				if ok and q.distanceTo(p_cm) < 1e-5:
					return self.id_of(c)
			except Exception:
				continue
		return self.id_of(hint) if hint is not None else None


def _token(e: Any) -> str | int:
	try:
		return e.entityToken
	except Exception:
		return id(e)


def sketch_uid(sketch: adsk.fusion.Sketch, index: int | None = None) -> str:
	s = native(sketch)
	if index is None:
		try:
			index = s.timelineObject.index
		except Exception:
			index = -1
	return "%s::%s@%s" % (s.parentComponent.name, s.name, index)


def read_sketch(sketch: adsk.fusion.Sketch, places: Places, index: int | None = None) -> Record:
	s = native(sketch)
	ix = SketchIndex(s)
	comp = s.parentComponent
	m = s.transform.copy()
	m.transformBy(places.matrix(comp))
	o, x, y, _z = m.getAsCoordinateSystem()
	frame = {"origin": [units.mm(o.x), units.mm(o.y), units.mm(o.z)], "x": _unit(x), "y": _unit(y)}
	out: Record = {
		"uid": sketch_uid(s, index),
		"name": s.name,
		"component": comp.name,
		"frame": frame,
		"points": [],
		"curves": [],
		"constraints": [],
		"dimensions": [],
	}
	for i, p in enumerate(ix.points):
		out["points"].append({"id": "P%d" % i, "xy": xy(p.geometry), "fixed": bool(p.isFixed or p.isReference)})
	for i, c in enumerate(ix.curves):
		out["curves"].append(_curve(ix, "C%d" % i, c))
	for g in s.geometricConstraints:
		out["constraints"].append(
			{
				"type": type(g).__name__[: -len("Constraint")]
				if type(g).__name__.endswith("Constraint")
				else type(g).__name__,
				"refs": _refs(ix, g),
			}
		)
	for d in s.sketchDimensions:
		out["dimensions"].append(_dimension(ix, d))
	return out


def _unit(v: adsk.core.Vector3D) -> list[float]:
	n = (v.x * v.x + v.y * v.y + v.z * v.z) ** 0.5 or 1.0
	return [round(v.x / n, 9), round(v.y / n, 9), round(v.z / n, 9)]


def _curve(ix: SketchIndex, cid: str, c: adsk.fusion.SketchCurve) -> Record:
	kind = type(c).__name__
	rec: Record = {"id": cid, "construction": bool(c.isConstruction), "fixed": bool(c.isFixed or c.isReference)}
	try:
		if kind == "SketchLine":
			rec.update(type="line", ends=[ix.id_of(c.startSketchPoint), ix.id_of(c.endSketchPoint)])
		elif kind == "SketchCircle":
			rec.update(type="circle", center=ix.id_of(c.centerSketchPoint), radius=units.mm(c.radius))
		elif kind == "SketchArc":
			via = point_at_middle(c.geometry)
			rec.update(
				type="arc",
				center=ix.id_of(c.centerSketchPoint),
				ends=[ix.id_of(c.startSketchPoint), ix.id_of(c.endSketchPoint)],
				via=xy(via) if via else None,
			)
		elif kind == "SketchEllipse":
			g = c.geometry
			ax = g.majorAxis
			n = (ax.x * ax.x + ax.y * ax.y) ** 0.5 or 1.0
			end = [g.center.x + ax.x / n * g.majorRadius, g.center.y + ax.y / n * g.majorRadius]
			rec.update(
				type="ellipse",
				center=ix.id_of(c.centerSketchPoint),
				major=[units.mm(end[0]), units.mm(end[1])],
				minor_radius=units.mm(g.minorRadius),
				polyline=strokes(g),
			)
		elif kind == "SketchFittedSpline":
			rec.update(
				type="spline_fit",
				through=[ix.id_of(p) for p in c.fitPoints],
				closed=bool(c.isClosed),
				polyline=strokes(c.geometry),
			)
		elif kind in ("SketchFixedSpline", "SketchControlPointSpline"):
			_ok, poles, degree, _knots, rational, _weights, _periodic = c.geometry.getData()
			rec.update(
				type="spline_cp",
				poles=[xy(p) for p in poles],
				degree=degree,
				rational=bool(rational),
				polyline=strokes(c.geometry),
			)
		else:
			rec.update(type="other", fusion_type=kind, polyline=strokes(c.geometry))
	except Exception as e:
		rec.update(type="other", fusion_type=kind, read_error=str(e)[:200])
		try:
			rec["polyline"] = strokes(c.geometry)
		except Exception:
			pass
	return rec


def _refs(ix: SketchIndex, obj: Any) -> dict[str, str]:
	refs = {}
	for role in REF_ROLES:
		try:
			v = getattr(obj, role)
		except Exception:
			continue
		if v is None or isinstance(v, (int, float, str, bool)):
			continue
		rid = ix.id_of(v)
		if rid:
			refs[role] = rid
	return refs


def _dimension(ix: SketchIndex, d: adsk.fusion.SketchDimension) -> Record:
	name = type(d).__name__
	if name.startswith("Sketch"):
		name = name[len("Sketch") :]
	if name.endswith("Dimension"):
		name = name[: -len("Dimension")]
	rec: Record = {"type": name, "refs": _refs(ix, d), "driving": bool(getattr(d, "isDriving", False)), "expr": None}
	try:
		rec["expr"] = d.parameter.expression if d.parameter else None
	except Exception:
		pass
	try:
		rec["orientation"] = ORIENTATION.get(int(d.orientation))
	except Exception:
		pass
	try:
		rec["text_at"] = xy(d.textPosition)
	except Exception:
		pass
	return rec
