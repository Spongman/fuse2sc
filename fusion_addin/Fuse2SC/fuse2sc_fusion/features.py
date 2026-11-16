"""Reading a feature's inputs (with the timeline just before it), one reader per Fusion type.

Each reader returns the IR payload for its kind (docs/ir.md) and appends to `ctx.unread` every
option it sees but the IR can't carry, so nothing is lost silently. Readers never guess: an input
they can't describe is left out, and the build falls back to Fusion's result for that step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import adsk.core
import adsk.fusion

from fuse2sc_ir import units

from . import timeline
from .places import body_key, native, owner_of
from .regions import read_region
from .safe import get, items
from .sketches import SketchIndex, sketch_uid

if TYPE_CHECKING:
	from collections.abc import Callable

	from .places import Places
	from .records import Items, Point, Record

OPS = {0: "join", 1: "cut", 2: "intersect", 3: "new", 4: "new"}


class Ctx:
	def __init__(self, design: adsk.fusion.Design, places: Places, index: int) -> None:
		self.design = design
		self.places = places
		self.index = index
		self.unread: list[str] = []
		self.region_cache: dict[str, SketchIndex] = {}

	# --- values ---------------------------------------------------------------------------------

	@staticmethod
	def expr(param: adsk.fusion.Parameter | None) -> str | None:
		try:
			return param.expression if param is not None else None
		except Exception:
			return None

	@staticmethod
	def nonzero(param: adsk.fusion.Parameter | None) -> bool:
		try:
			return param is not None and abs(param.value) > 1e-12
		except Exception:
			return False

	def op(self, f: adsk.fusion.Feature) -> str:
		try:
			return OPS.get(int(f.operation), "new")
		except Exception:
			return "new"

	def bodies(self, coll: Items) -> list[str]:
		return [body_key(b) for b in items(coll) if isinstance(native(b), adsk.fusion.BRepBody)]

	# --- geometry -------------------------------------------------------------------------------

	def point(self, p: adsk.core.Point3D, entity: Any) -> Point:
		return self.places.point(p, owner_of(entity))

	def pick_face(self, face: adsk.fusion.BRepFace) -> Record:
		n = native(face)
		return {"at": self.places.point(n.pointOnFace, n.body.parentComponent), "body": body_key(n.body)}

	def pick_edge(self, edge: adsk.fusion.BRepEdge) -> Record:
		n = native(edge)
		p = None
		try:
			ev = n.evaluator
			ok, t0, t1 = ev.getParameterExtents()
			ok, p = ev.getPointAtParameter(t0 + 0.5 * (t1 - t0))
			if not ok:
				p = None
		except Exception:
			p = None
		if p is None:
			p = n.pointOnEdge
		return {"at": self.places.point(p, n.body.parentComponent), "body": body_key(n.body)}

	def point_of(self, entity: Any) -> Point | None:
		"""A world point on a face, edge, vertex, sketch point or construction (To/From Object)."""
		n = native(entity)
		try:
			if isinstance(n, adsk.fusion.BRepFace):
				return self.pick_face(n)["at"]
			if isinstance(n, adsk.fusion.BRepEdge):
				return self.pick_edge(n)["at"]
			if isinstance(n, adsk.fusion.BRepVertex):
				return self.places.point(n.geometry, n.body.parentComponent)
			if isinstance(n, adsk.fusion.SketchPoint):
				return self.places.point(n.worldGeometry, n.parentSketch.parentComponent)
			if isinstance(n, adsk.fusion.ConstructionPlane):
				return self.places.point(n.geometry.origin, n.component)
			if isinstance(n, adsk.fusion.ConstructionPoint):
				return self.places.point(n.geometry, n.component)
		except Exception:
			pass
		return None

	def plane(self, entity: Any) -> Record | None:
		"""{origin, normal} of a construction plane or a planar face, world."""
		if entity is None:
			return None
		n = native(entity)
		try:
			if isinstance(n, adsk.fusion.ConstructionPlane):
				g, comp, origin = n.geometry, n.component, None
			elif isinstance(n, adsk.fusion.BRepFace) and isinstance(n.geometry, adsk.core.Plane):
				g, comp, origin = n.geometry, n.body.parentComponent, n.pointOnFace
			else:
				return None
			return {
				"origin": self.places.point(origin or g.origin, comp),
				"normal": self.places.direction(g.normal, comp),
			}
		except Exception:
			return None

	def axis(self, entity: Any) -> Record | None:
		"""{origin, dir} of a construction axis, sketch line, straight or round edge, or a
		cylinder/cone face, world."""
		if entity is None:
			return None
		n = native(entity)
		try:
			comp = owner_of(n)
			if isinstance(n, adsk.fusion.ConstructionAxis):
				g = n.geometry
				o, d = g.origin, g.direction
			elif isinstance(n, adsk.fusion.SketchLine):
				g = n.worldGeometry
				o, d = g.startPoint, g.startPoint.vectorTo(g.endPoint)
			elif isinstance(n, adsk.fusion.BRepEdge):
				g = n.geometry
				if isinstance(g, adsk.core.Line3D):
					o, d = g.startPoint, g.startPoint.vectorTo(g.endPoint)
				elif hasattr(g, "center") and hasattr(g, "normal"):
					o, d = g.center, g.normal
				else:
					return None
			elif isinstance(n, adsk.fusion.BRepFace) and hasattr(n.geometry, "axis"):
				o, d = n.geometry.origin, n.geometry.axis
			else:
				return None
			return {"origin": self.places.point(o, comp), "dir": self.places.direction(d, comp)}
		except Exception:
			return None

	def direction(self, entity: Any) -> list[float] | None:
		if entity is None:
			return None
		a = self.axis(entity)
		return a["dir"] if a else None

	# --- regions --------------------------------------------------------------------------------

	def regions(self, coll: Items) -> list[Record]:
		return [read_region(p, self.region_cache) for p in items(coll) if isinstance(p, adsk.fusion.Profile)]

	def inputs(self, coll: Items) -> Record | None:
		ents = items(coll)
		if ents and all(isinstance(native(e), adsk.fusion.BRepBody) for e in ents):
			return {"bodies": [body_key(e) for e in ents]}
		feats: list[Record] = []
		for e in ents:
			try:
				feats.append({"name": e.name, "component": e.parentComponent.name})
			except Exception:
				return None
		return {"features": feats} if feats else None


# --- extrude -------------------------------------------------------------------------------------


def _end(ctx: Ctx, e: adsk.fusion.ExtentDefinition) -> Record:
	t = type(e).__name__
	if t == "DistanceExtentDefinition":
		return {"type": "distance", "expr": ctx.expr(e.distance), "mm": units.mm(e.distance.value)}
	if t == "ToEntityExtentDefinition":
		return {"type": "to", "at": ctx.point_of(e.entity), "offset": ctx.expr(get(e, "offset"))}
	if t == "ThroughAllExtentDefinition":
		return {"type": "all", "reverse": get(e, "isPositiveDirection", default=True) is False}
	return {"type": "other", "fusion_type": t}


def _on_start_faces(ctx: Ctx, f: adsk.fusion.ExtrudeFeature, regions: list[Record]) -> list[Record] | None:
	"""Fusion can report a stale profile list for an extrude whose sketch was edited later. The
	regions that really fed a new or join extrude are those lying on its start faces: test every
	region of that sketch against the faces, with the feature computed."""
	sketches = {r["sketch"] for r in regions}
	profs = [p for p in items(f.profile) if isinstance(p, adsk.fusion.Profile)]
	if len(sketches) != 1 or not profs:
		return None
	sk = native(profs[0].parentSketch)
	with timeline.marker_at(ctx.design, ctx.index, before=False):
		faces = [native(x) for x in items(f.startFaces)]
		planar = [x for x in faces if isinstance(x.geometry, adsk.core.Plane)]
		if not planar:
			return None
		hits: list[Record] = []
		for p in sk.profiles:
			r = read_region(p, ctx.region_cache)
			if not r.get("inside"):
				continue
			q = sk.sketchToModelSpace(
				adsk.core.Point3D.create(r["inside"][0] / units.CM_TO_MM, r["inside"][1] / units.CM_TO_MM, 0)
			)
			for face in planar:
				pl = face.geometry
				n = pl.normal
				d = (q.x - pl.origin.x) * n.x + (q.y - pl.origin.y) * n.y + (q.z - pl.origin.z) * n.z
				on = adsk.core.Point3D.create(q.x - d * n.x, q.y - d * n.y, q.z - d * n.z)
				ok, prm = face.evaluator.getParameterAtPoint(on)
				if ok and face.evaluator.isParameterOnFace(prm):
					hits.append(r)
					break
		return hits or None


def read_extrude(ctx: Ctx, f: adsk.fusion.ExtrudeFeature) -> Record:
	out: Record = {"op": ctx.op(f), "targets": ctx.bodies(get(f, "participantBodies")), "created": ctx.bodies(f.bodies)}
	try:
		chosen = items(f.profile)
	except Exception:
		out["broken"] = (get(f, "errorOrWarningMessage", default="") or "profile reference lost").split("<")[0].strip()
		chosen = []
	out["regions"] = ctx.regions(chosen)
	out["faces"] = [ctx.pick_face(x) for x in chosen if isinstance(native(x), adsk.fusion.BRepFace)]
	curves = [native(x) for x in chosen if isinstance(native(x), adsk.fusion.SketchCurve)]
	one = f.extentOne
	if isinstance(one, adsk.fusion.SymmetricExtentDefinition):
		out["extent"] = {
			"type": "symmetric",
			"expr": ctx.expr(one.distance),
			"total": bool(get(one, "isFullLength", default=False)),
		}
	elif f.hasTwoExtents:
		out["extent"] = {"type": "two", "side1": _end(ctx, one), "side2": _end(ctx, f.extentTwo)}
		if ctx.nonzero(get(f, "taperAngleTwo")):
			ctx.unread.append("taper on side two")
	else:
		out["extent"] = {"type": "one", "side1": _end(ctx, one)}
	st = f.startExtent
	st_t = type(st).__name__
	if st_t == "OffsetStartDefinition":
		out["start"] = {"type": "offset", "expr": ctx.expr(st.offset)}
	elif st_t == "FromEntityStartDefinition":
		out["start"] = {"type": "object", "at": ctx.point_of(get(st, "entity")), "offset": ctx.expr(get(st, "offset"))}
	elif st_t == "ProfilePlaneStartDefinition":
		out["start"] = {"type": "sketch"}
	else:
		out["start"] = {"type": "other", "fusion_type": st_t}
	if ctx.nonzero(get(f, "taperAngleOne")):
		out["taper"] = ctx.expr(f.taperAngleOne)
	if get(f, "isThinExtrude", default=False):
		side = {0: "one", 1: "two", 2: "center"}.get(int(get(f, "thinExtrudeWallLocationOne", default=2)), "center")
		thin: Record = {"thickness": ctx.expr(get(f, "thinExtrudeWallThicknessOne")), "side": side}
		if curves:
			ix = SketchIndex(curves[0].parentSketch)
			thin.update(sketch=sketch_uid(curves[0].parentSketch), curves=[ix.id_of(c) for c in curves])
		out["thin"] = thin
	if get(f, "isSolid", default=True) is False:
		ctx.unread.append("surface (not solid) extrude")
	if out["op"] in ("new", "join") and out["regions"]:
		try:
			fixed = _on_start_faces(ctx, f, out["regions"])
			if fixed:
				out["regions"] = fixed
		except Exception as e:
			out["start_face_check"] = str(e)[:200]
	return out


# --- other sweeps --------------------------------------------------------------------------------


def read_revolve(ctx: Ctx, f: adsk.fusion.RevolveFeature) -> Record:
	out: Record = {
		"op": ctx.op(f),
		"regions": ctx.regions(f.profile),
		"axis": ctx.axis(f.axis),
		"targets": ctx.bodies(get(f, "participantBodies")),
		"created": ctx.bodies(f.bodies),
	}
	if any(not isinstance(p, adsk.fusion.Profile) for p in items(f.profile)):
		out["other_profiles"] = True
	e = f.extentDefinition
	t = type(e).__name__
	if t == "AngleExtentDefinition":
		sym = bool(get(e, "isSymmetric", default=False))
		out["angle"] = {
			"type": "symmetric" if sym else "one",
			"expr": ctx.expr(e.angle),
			"degrees": units.deg(e.angle.value),
		}
	elif t == "TwoSidesAngleExtentDefinition":
		out["angle"] = {"type": "two", "expr": ctx.expr(e.angleOne), "expr2": ctx.expr(e.angleTwo)}
	else:
		out["angle"] = {"type": "other", "fusion_type": t}
	if get(f, "isSolid", default=True) is False:
		ctx.unread.append("surface (not solid) revolve")
	return out


def read_sweep(ctx: Ctx, f: adsk.fusion.SweepFeature) -> Record:
	out: Record = {
		"op": ctx.op(f),
		"regions": ctx.regions(f.profile),
		"targets": ctx.bodies(get(f, "participantBodies")),
		"created": ctx.bodies(f.bodies),
	}
	path = get(f, "path")
	curves, sk = [], None
	for i in range(path.count if path is not None else 0):
		e = native(path.item(i).entity)
		if isinstance(e, adsk.fusion.SketchCurve):
			sk = sk or e.parentSketch
			curves.append(SketchIndex(e.parentSketch).id_of(e))
		else:
			curves.append(None)
	out["path"] = {"sketch": sketch_uid(sk) if sk is not None else None, "curves": curves}
	try:
		out["orientation"] = {0: "perpendicular", 1: "parallel"}.get(int(f.orientation), "perpendicular")
	except Exception:
		pass
	if ctx.nonzero(get(f, "twistAngle")):
		out["twist"] = ctx.expr(f.twistAngle)
	if ctx.nonzero(get(f, "taperAngle")):
		ctx.unread.append("sweep taper")
	out["rails"] = get(f, "guideRail") is not None
	return out


def read_loft(ctx: Ctx, f: adsk.fusion.LoftFeature) -> Record:
	secs: list[Record] = []
	for s in items(f.loftSections):
		e = s.entity
		if isinstance(e, adsk.fusion.Profile):
			secs.append(read_region(e, ctx.region_cache))
		else:
			secs.append({"other": type(e).__name__})
		cond = type(get(s, "endCondition")).__name__
		if cond not in ("NoneType", "FreeEndCondition", "LoftFreeEndCondition"):
			ctx.unread.append("loft end condition %s" % cond)
	return {
		"op": ctx.op(f),
		"sections": secs,
		"rails": len(items(get(f, "centerLineOrRails"))),
		"closed": bool(get(f, "isClosed", default=False)),
		"solid": bool(get(f, "isSolid", default=True)),
		"targets": ctx.bodies(get(f, "participantBodies")),
		"created": ctx.bodies(f.bodies),
	}


# --- blends --------------------------------------------------------------------------------------

BLEND_SIZES = {
	"ConstantRadiusFilletEdgeSet": ("constant", {"radius": "radius"}),
	"ChordLengthFilletEdgeSet": ("chord", {"chord": "chordLength"}),
	"VariableRadiusFilletEdgeSet": ("variable", {"start": "startRadius", "end": "endRadius"}),
	"EqualDistanceChamferEdgeSet": ("equal", {"distance": "distance"}),
	"TwoDistancesChamferEdgeSet": ("two_distances", {"d1": "distanceOne", "d2": "distanceTwo"}),
	"DistanceAndAngleChamferEdgeSet": ("distance_angle", {"distance": "distance", "angle": "angle"}),
}


def read_blend(ctx: Ctx, f: adsk.fusion.FilletFeature | adsk.fusion.ChamferFeature) -> Record:
	if get(f, "isRollingBallCorner") is False:
		ctx.unread.append("setback corners")
	if get(f, "isG2"):
		ctx.unread.append("curvature-continuous (G2) blend")
	sets: list[Record] = []
	for es in items(f.edgeSets):
		kind = type(es).__name__
		t, fields = BLEND_SIZES.get(kind, (None, {}))
		rec: Record = {
			"type": t or "other",
			"fusion_type": kind,
			"size": {k: ctx.expr(get(es, attr)) for k, attr in fields.items()},
			"tangent_chain": bool(get(es, "isTangentChain", default=False)),
			"edges": [],
		}
		for e in items(es.edges):
			n = native(e)
			if isinstance(n, adsk.fusion.BRepEdge):
				rec["edges"].append(ctx.pick_edge(n))
			elif isinstance(n, adsk.fusion.BRepFace):
				rec["edges"] += [ctx.pick_edge(x) for x in n.edges]
			else:
				ctx.unread.append("blend input %s" % type(n).__name__)
		if get(es, "isFlipped"):
			rec["flipped"] = True
		if t == "variable":
			if items(get(es, "midRadii")):
				rec["mid_radii"] = True
			first = native(items(es.edges)[0]) if items(es.edges) else None
			if first is not None and get(first, "startVertex") is not None:
				rec["start"] = ctx.places.point(first.startVertex.geometry, first.body.parentComponent)
		sets.append(rec)
	return {"sets": sets, "created": ctx.bodies(f.bodies)}


# --- faces ---------------------------------------------------------------------------------------


def read_shell(ctx: Ctx, f: adsk.fusion.ShellFeature) -> Record:
	ents = [native(x) for x in items(f.inputEntities)]
	return {
		"faces": [ctx.pick_face(x) for x in ents if isinstance(x, adsk.fusion.BRepFace)],
		"bodies": [body_key(x) for x in ents if isinstance(x, adsk.fusion.BRepBody)],
		"inside": ctx.expr(get(f, "insideThickness")),
		"outside": ctx.expr(get(f, "outsideThickness")),
		"tangent_chain": bool(get(f, "isTangentChain", default=False)),
		"created": ctx.bodies(f.bodies),
	}


def read_draft(ctx: Ctx, f: adsk.fusion.DraftFeature) -> Record:
	neutral = ctx.plane(get(f, "plane", "neutralPlane"))
	out: Record = {
		"faces": [ctx.pick_face(x) for x in items(get(f, "inputFaces"))],
		"angle": ctx.expr(get(f, "angleOne", "angle")),
		"neutral": neutral,
		"created": ctx.bodies(f.bodies),
	}
	if neutral:
		out["pull"] = [-c for c in neutral["normal"]] if get(f, "isFlipped") else list(neutral["normal"])
	if get(f, "isTwoSided") or get(f, "isSymmetric"):
		ctx.unread.append("two-sided or symmetric draft")
	return out


def read_offset_faces(ctx: Ctx, f: adsk.fusion.OffsetFacesFeature) -> Record:
	return {
		"faces": [ctx.pick_face(x) for x in items(f.inputFaces)],
		"distance": ctx.expr(f.distance),
		"created": ctx.bodies(f.bodies),
	}


def read_hole(ctx: Ctx, f: adsk.fusion.HoleFeature) -> Record:
	style = {0: "simple", 1: "counterbore", 2: "countersink"}.get(int(get(f, "holeType", default=0) or 0), "simple")
	out: Record = {
		"diameter": ctx.expr(get(f, "holeDiameter")),
		"style": style,
		"tip_angle": ctx.expr(get(f, "tipAngle")),
		"targets": ctx.bodies(get(f, "participantBodies")),
		"created": ctx.bodies(f.bodies),
		"holes": [],
	}
	if style == "counterbore":
		out.update(cb_diameter=ctx.expr(get(f, "counterboreDiameter")), cb_depth=ctx.expr(get(f, "counterboreDepth")))
	elif style == "countersink":
		out.update(cs_diameter=ctx.expr(get(f, "countersinkDiameter")), cs_angle=ctx.expr(get(f, "countersinkAngle")))
	ext = get(f, "extentDefinition")
	t = type(ext).__name__
	if t == "DistanceExtentDefinition":
		out["depth"] = ctx.expr(ext.distance)
	elif t == "ThroughAllExtentDefinition":
		out["through_all"] = True
	else:
		ctx.unread.append("hole extent %s" % t)
	pos = get(f, "holePositionDefinition")
	for sp in items(get(pos, "sketchPoints")) if pos is not None else []:
		n = native(sp)
		out["holes"].append({"at": ctx.places.point(n.worldGeometry, n.parentSketch.parentComponent)})
	if not out["holes"] and get(f, "position") is not None:
		out["holes"].append({"at": ctx.places.point(f.position, f.parentComponent)})
	return out


# --- bodies --------------------------------------------------------------------------------------


def read_combine(ctx: Ctx, f: adsk.fusion.CombineFeature) -> Record:
	if get(f, "isNewComponent"):
		ctx.unread.append("result into a new component")
	return {
		"target": body_key(f.targetBody),
		"tools": ctx.bodies(f.toolBodies),
		"op": {0: "join", 1: "cut", 2: "intersect"}.get(int(f.operation), "join"),
		"keep_tools": bool(get(f, "isKeepToolBodies", default=False)),
		"created": ctx.bodies(f.bodies),
	}


def read_move(ctx: Ctx, f: adsk.fusion.MoveFeature) -> Record:
	m = get(f, "transform")
	return {"bodies": ctx.bodies(f.inputEntities), "matrix": units.matrix_mm(m.asArray()) if m is not None else None}


def read_scale(ctx: Ctx, f: adsk.fusion.ScaleFeature) -> Record:
	out: Record = {"bodies": ctx.bodies(f.inputEntities)}
	if get(f, "isUniform", default=True):
		out["factor"] = ctx.expr(get(f, "scaleFactor"))
	else:
		out["factors"] = [ctx.expr(get(f, k)) for k in ("xScale", "yScale", "zScale")]
	p = get(f, "point")
	if p is not None:
		out["origin"] = ctx.point_of(p)
	return out


def read_remove(ctx: Ctx, f: adsk.fusion.RemoveFeature) -> Record:
	return {"bodies": ctx.bodies([get(f, "itemToRemove")])}


def read_split(ctx: Ctx, f: adsk.fusion.SplitBodyFeature) -> Record:
	tool = native(get(f, "splittingTool"))
	out: Record = {"bodies": ctx.bodies(f.splitBodies), "created": ctx.bodies(f.bodies)}
	if isinstance(tool, adsk.fusion.SketchLine):
		out["tool"] = {
			"type": "line",
			"sketch": sketch_uid(tool.parentSketch),
			"curve": SketchIndex(tool.parentSketch).id_of(tool),
		}
	elif isinstance(tool, adsk.fusion.BRepFace):
		out["tool"] = dict(ctx.pick_face(tool), type="face")
		pl = ctx.plane(tool)
		if pl:
			out["tool"]["plane"] = pl
	elif isinstance(tool, adsk.fusion.ConstructionPlane):
		out["tool"] = dict(ctx.plane(tool) or {}, type="plane")
	elif isinstance(tool, adsk.fusion.BRepBody):
		out["tool"] = {"type": "body", "body": body_key(tool)}
	else:
		out["tool"] = {"type": "other", "fusion_type": type(tool).__name__}
	if get(f, "isSplittingToolExtended") is False:
		ctx.unread.append("splitting tool not extended")
	return out


def read_paste(ctx: Ctx, f: adsk.fusion.CopyPasteBody | adsk.fusion.CutPasteBody) -> Record:
	return {"sources": ctx.bodies(get(f, "sourceBody")), "created": ctx.bodies(f.bodies)}


# --- patterns ------------------------------------------------------------------------------------


def read_mirror(ctx: Ctx, f: adsk.fusion.MirrorFeature) -> Record:
	return {
		"inputs": ctx.inputs(f.inputEntities),
		"plane": ctx.plane(f.mirrorPlane),
		"join": bool(get(f, "isCombine", default=False)),
		"created": ctx.bodies(f.bodies),
	}


def read_circular(ctx: Ctx, f: adsk.fusion.CircularPatternFeature) -> Record:
	return {
		"inputs": ctx.inputs(f.inputEntities),
		"axis": ctx.axis(f.axis),
		"count": ctx.expr(f.quantity),
		"angle": ctx.expr(get(f, "totalAngle")),
		"symmetric": bool(get(f, "isSymmetric", default=False)),
		"skipped_instances": bool(list(get(f, "suppressedElementsIds", default=[]) or [])),
		"created": ctx.bodies(f.bodies),
	}


def read_rectangular(ctx: Ctx, f: adsk.fusion.RectangularPatternFeature) -> Record:
	out: Record = {
		"inputs": ctx.inputs(f.inputEntities),
		"created": ctx.bodies(f.bodies),
		"extent_mode": "spacing" if int(get(f, "patternDistanceType", default=1)) == 1 else "extent",
	}
	for n, ent, q, dist, sym in (
		("1", "directionOneEntity", "quantityOne", "distanceOne", "isSymmetricInDirectionOne"),
		("2", "directionTwoEntity", "quantityTwo", "distanceTwo", "isSymmetricInDirectionTwo"),
	):
		out["dir" + n] = ctx.direction(get(f, ent))
		out["count" + n] = ctx.expr(get(f, q))
		out["spacing" + n] = ctx.expr(get(f, dist))
		out["symmetric" + n] = bool(get(f, sym, default=False))
	if list(get(f, "suppressedElementsIds", default=[]) or []):
		out["skipped_instances"] = True
	return out


READERS: dict[str, Callable[[Ctx, Any], Record]] = {
	"ExtrudeFeature": read_extrude,
	"RevolveFeature": read_revolve,
	"SweepFeature": read_sweep,
	"LoftFeature": read_loft,
	"FilletFeature": read_blend,
	"ChamferFeature": read_blend,
	"ShellFeature": read_shell,
	"DraftFeature": read_draft,
	"OffsetFacesFeature": read_offset_faces,
	"HoleFeature": read_hole,
	"CombineFeature": read_combine,
	"MoveFeature": read_move,
	"ScaleFeature": read_scale,
	"RemoveFeature": read_remove,
	"SplitBodyFeature": read_split,
	"CopyPasteBody": read_paste,
	"CutPasteBody": read_paste,
	"MirrorFeature": read_mirror,
	"CircularPatternFeature": read_circular,
	"RectangularPatternFeature": read_rectangular,
}


def read_other(ctx: Ctx, f: adsk.fusion.Feature) -> Record:
	return {"created": ctx.bodies(get(f, "bodies"))}


def read_feature(ctx: Ctx, f: adsk.fusion.Feature) -> Record:
	return READERS.get(type(f).__name__, read_other)(ctx, f)
