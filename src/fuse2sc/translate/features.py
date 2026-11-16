"""Feature handlers, one per payload type (functools.singledispatch).

A handler either plans SolidCraft commands or calls `t.fallback(...)`. It never guesses: an input
it can't express (an extent type, a body it can't name, a pattern of unknown features) is a
fallback, and the replay brings Fusion's own result in for that step.
"""

from __future__ import annotations

from collections import defaultdict
from functools import singledispatch
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from .. import geometry as g
from ..model import (
	Blend,
	CircularPattern,
	Combine,
	Draft,
	End,
	Extrude,
	Feature,
	Hole,
	Inputs,
	Loft,
	Mirror,
	Move,
	OffsetFaces,
	Paste,
	Pick,
	RectangularPattern,
	Region,
	Remove,
	Revolve,
	Scale,
	Shell,
	Split,
	Step,
	Sweep,
)
from .expressions import is_zero
from .plan import Params, RegionMatch, Variant

if TYPE_CHECKING:
	from .translator import Translator


@singledispatch
def plan_feature(f: Feature, t: Translator, step: Step) -> None:
	t.fallback(step, f"no SolidCraft equivalent for {step.kind}")


# --- shared --------------------------------------------------------------------------------------


class Selection(NamedTuple):
	"""How a feature names its regions: by curves first, by inside points as the fallback, and
	by area and centroid to find them among SolidCraft's own regions."""

	sketch: str
	by_curves: list[Params]
	by_points: list[Params] | None
	match: list[RegionMatch] | None


def profile_selection(t: Translator, regions: list[Region]) -> Selection | None:
	"""None when the regions can't be named (several sketches, or a region with no handle)."""
	if not regions or len({r.sketch for r in regions}) != 1:
		return None
	by_curves: list[Params] = []
	for r in regions:
		if (curves := r.outer_curves) is not None:
			by_curves.append({"curves": curves})
		elif r.inside is not None:
			by_curves.append({"point": list(r.inside)})
		else:
			return None
	by_points: list[Params] = [{"point": list(r.inside)} for r in regions if r.inside is not None]
	matches = [RegionMatch(r.area, r.centroid) for r in regions if r.area and r.centroid is not None]
	return Selection(
		t.sketch_ref(regions[0].sketch),
		by_curves,
		by_points if len(by_points) == len(regions) else None,
		matches if len(matches) == len(regions) else None,
	)


def points_variant(params: Params, by_points: list[Params] | None) -> list[Variant]:
	if by_points and by_points != params.get("profiles"):
		return [Variant(dict(params, profiles=by_points, exact_regions=True), "refused", "profiles picked by points")]
	return []


def by_body(picks: list[Pick]) -> dict[str | None, list[list[float]]]:
	groups: dict[str | None, list[list[float]]] = defaultdict(list)
	for p in picks:
		groups[p.body].append(list(p.at))
	return groups


def with_targets(params: Params, op: str, targets: list[str]) -> Params:
	params["operation"] = op
	if op != "new" and targets:
		params["targets"] = list(targets)
	return params


# --- extrude -------------------------------------------------------------------------------------


def _side(params: Params, end: End) -> bool:
	match end.type:
		case "distance" if end.expr:
			params["distance"] = end.expr
		case "to" if end.at:
			params["to"] = list(end.at)
			if not is_zero(end.offset):
				params["to_offset"] = end.offset
		case "all":
			params["through_all"] = True
			if end.reverse:
				params["direction"] = "negative"
		case _:
			return False
	return True


@plan_feature.register(Extrude)
def _extrude(f: Extrude, t: Translator, step: Step) -> None:
	if f.broken:
		t.note(f"{step.name}: {f.broken} in Fusion")
		return t.fallback(step, f.broken)
	params: Params = {"name": t.feature_name(step)}
	variants: list[Variant] = []
	match = by_points = None
	if f.thin:
		sketch = t.sketch_ref(f.thin.sketch) if f.thin.sketch else None
		if sketch is None and f.regions:
			sketch = t.sketch_ref(f.regions[0].sketch)
		if not sketch or not f.thin.thickness:
			return t.fallback(step, "thin extrude without its sketch or thickness")
		side = {"center": "center", "one": "inside", "two": "outside"}[f.thin.side]
		params.update(sketch=sketch, thin=True, thickness=f.thin.thickness, side=side)
		if f.thin.curves and None not in f.thin.curves:
			params["curves"] = list(f.thin.curves)
	elif sel := profile_selection(t, f.regions):
		sketch, by_curves, by_points, match = sel
		params.update(sketch=sketch, profiles=by_curves)
	elif f.faces:
		faces = [list(p.at) for p in f.faces]
		if len(faces) == 1:
			params["face"] = faces[0]
		else:
			params["faces"] = faces
	else:
		return t.fallback(step, "no profile it can name")

	ext = f.extent
	if ext.type == "symmetric":
		if not ext.expr:
			return t.fallback(step, "symmetric extent without a distance")
		params.update(direction="symmetric", distance=ext.expr if ext.total else f"2 * ({ext.expr})")
	elif ext.type == "one":
		if not (ext.side1 and _side(params, ext.side1)):
			return t.fallback(step, f"extent {ext.side1.type if ext.side1 else '?'}")
	else:  # two sides: Fusion's solid spans [-d2, d1] along the sketch normal
		s1, s2 = ext.side1, ext.side2
		if not (s1 and s2 and s1.type == s2.type == "distance" and s1.expr and s2.expr):
			return t.fallback(step, "two-sided extent other than two distances")
		if (s1.mm or 0) >= 0 and (s2.mm or 0) >= 0:
			params.update(distance=s1.expr, distance2=s2.expr)
		else:  # both ends on one side of the sketch: start at one end, run to the other
			params.update(start_offset=f"-({s2.expr})", distance=f"({s1.expr}) + ({s2.expr})")

	match f.start.type:
		case "offset" if not is_zero(f.start.expr):
			params["start_offset"] = f.start.expr
		case "object" if f.start.at and is_zero(f.start.offset):
			params["from"] = list(f.start.at)
		case "sketch" | "offset":
			pass
		case _:
			return t.fallback(step, f"start {f.start.type}")
	if f.taper and not is_zero(f.taper):
		params["taper"] = f.taper
	with_targets(params, f.op, f.targets)

	if "profiles" in params:
		variants += points_variant(params, by_points)
	if f.thin and params["side"] != "center":
		variants.append(
			Variant(
				dict(params, side="outside" if params["side"] == "inside" else "inside"),
				"wrong",
				"the other side of the curves",
			)
		)
	t.emit("solid.extrude", params, variants=variants, match=match, new_bodies=t.new_bodies(step), delta=t.delta(step))


# --- revolve, sweep, loft ------------------------------------------------------------------------


@plan_feature.register(Revolve)
def _revolve(f: Revolve, t: Translator, step: Step) -> None:
	sel = profile_selection(t, f.regions)
	if not sel or f.other_profiles or not f.axis:
		return t.fallback(step, "profile or axis it can't name")
	sketch, by_curves, by_points, match = sel
	params = {
		"name": t.feature_name(step),
		"sketch": sketch,
		"profiles": by_curves,
		"axis": {"origin": list(f.axis.origin), "dir": list(f.axis.dir)},
	}
	match f.angle.type:
		case "one" if f.angle.expr:
			params["angle"] = f.angle.expr
		case "symmetric" if f.angle.expr:
			params.update(angle=f.angle.expr, direction="symmetric")
		case "two" if f.angle.expr and f.angle.expr2:
			params.update(angle=f.angle.expr, angle2=f.angle.expr2)
		case _:
			return t.fallback(step, f"revolve extent {f.angle.fusion_type or f.angle.type}")
	with_targets(params, f.op, f.targets)
	variants = points_variant(params, by_points)
	full_turn = f.angle.type == "symmetric" or abs(abs(f.angle.degrees or 0) - 360) < 1e-6
	if not full_turn:
		variants.append(
			Variant(
				dict(params, axis={"origin": list(f.axis.origin), "dir": [-c for c in f.axis.dir]}),
				"wrong",
				"the axis the other way",
			)
		)
	t.emit("solid.revolve", params, variants=variants, match=match, new_bodies=t.new_bodies(step), delta=t.delta(step))


@plan_feature.register(Sweep)
def _sweep(f: Sweep, t: Translator, step: Step) -> None:
	sel = profile_selection(t, f.regions)
	if not sel or not f.path.sketch or None in f.path.curves or not f.path.curves or f.rails:
		return t.fallback(step, "path, rails or profile it can't name")
	sketch, by_curves, by_points, match = sel
	params = {
		"name": t.feature_name(step),
		"sketch": sketch,
		"profiles": by_curves,
		"path_sketch": t.sketch_ref(f.path.sketch),
		"path": list(f.path.curves),
	}
	if f.orientation == "parallel":
		params["orientation"] = "parallel"
	if f.twist and not is_zero(f.twist):
		params["twist"] = f.twist
	with_targets(params, f.op, f.targets)
	reversed_path = dict(params, path=params["path"][::-1])
	variants = [Variant(reversed_path, "refused", "the path the other way")]
	variants += points_variant(params, by_points) + points_variant(reversed_path, by_points)
	t.emit("solid.sweep", params, variants=variants, match=match, new_bodies=t.new_bodies(step), delta=t.delta(step))


@plan_feature.register(Loft)
def _loft(f: Loft, t: Translator, step: Step) -> None:
	regions = [s for s in f.sections if isinstance(s, Region)]
	if f.rails or f.closed or not f.solid or len(regions) != len(f.sections) or len(regions) < 2:
		return t.fallback(step, "rails, closed, surface or point sections")
	if len({len(r.holes) for r in regions}) > 1 or not all(r.outer_known for r in regions):
		return t.fallback(step, "sections SolidCraft can't pair up")
	params = {
		"name": t.feature_name(step),
		"sections": [{"sketch": t.sketch_ref(r.sketch), "profiles": [{"curves": list(r.outer)}]} for r in regions],
	}
	with_targets(params, f.op, f.targets)
	t.emit("solid.loft", params, new_bodies=t.new_bodies(step), delta=t.delta(step))


# --- blends --------------------------------------------------------------------------------------

BLEND_PARAMS = {
	"constant": lambda s: {"radius": s.get("radius")},
	"chord": lambda s: {"radius": s.get("chord"), "type": "chord"},
	"variable": lambda s: {"radius": s.get("start"), "radius2": s.get("end"), "type": "variable"},
	"equal": lambda s: {"distance": s.get("distance")},
	"two_distances": lambda s: {"distance": s.get("d1"), "distance2": s.get("d2")},
	"distance_angle": lambda s: {"distance": s.get("distance"), "angle": s.get("angle")},
}


@plan_feature.register(Blend)
def _blend(f: Blend, t: Translator, step: Step) -> None:
	command = "solid.fillet" if step.kind == "FilletFeature" else "solid.chamfer"
	planned = []
	for es in f.sets:
		size = BLEND_PARAMS.get(es.type, lambda s: None)(es.size)
		if not size or not all(size.values()) or not es.edges:
			return t.fallback(step, f"{es.fusion_type or es.type} edge set")
		if es.type == "variable":
			if es.mid_radii or not es.start or len(es.edges) != 1:
				return t.fallback(step, "variable radius with mid radii or several edges")
			size["start"] = list(es.start)
		for body, edges in by_body(es.edges).items():
			p = dict(size, edges=edges, tangent_chain=es.tangent_chain)
			if body:
				p["body"] = body
			planned.append((p, es.flipped))
	base = t.feature_name(step)
	for i, (p, flipped) in enumerate(planned, 1):
		p["name"] = base if len(planned) == 1 else f"{base} ({i})"
		if flipped:
			p["flip"] = True
		variants = []
		if "distance2" in p or "angle" in p:
			variants.append(Variant(dict(p, flip=not flipped), "wrong", "the faces the other way"))
		single = len(planned) == 1
		t.emit(command, p, variants=variants, delta=t.delta(step) if single else None, new_bodies=t.new_bodies(step))


# --- faces ---------------------------------------------------------------------------------------


@plan_feature.register(Shell)
def _shell(f: Shell, t: Translator, step: Step) -> None:
	if f.inside and f.outside:
		size = {"thickness": f.inside, "direction": "both"}
	elif f.outside:
		size = {"thickness": f.outside, "direction": "outside"}
	elif f.inside:
		size = {"thickness": f.inside, "direction": "inside"}
	else:
		return t.fallback(step, "shell without a thickness")
	groups = by_body(f.faces) or {b: [] for b in f.bodies}
	if not groups:
		return t.fallback(step, "shell of nothing it can name")
	for body, faces in groups.items():
		p = dict(size, faces=faces, tangent_chain=f.tangent_chain)
		if body:
			p["body"] = body
		t.emit("solid.shell", p, new_bodies=t.new_bodies(step))


@plan_feature.register(Draft)
def _draft(f: Draft, t: Translator, step: Step) -> None:
	if not (f.faces and f.angle and f.neutral):
		return t.fallback(step, "draft without faces, angle or neutral plane")
	for body, faces in by_body(f.faces).items():
		p: Params = {
			"faces": faces,
			"angle": f.angle,
			"neutral": {"origin": list(f.neutral.origin), "normal": list(f.neutral.normal)},
		}
		if f.pull:
			p["pull"] = list(f.pull)
		if body:
			p["body"] = body
		t.emit(
			"solid.draft",
			p,
			variants=[Variant(dict(p, angle=f"-({f.angle})"), "wrong", "the angle the other way")],
			delta=t.delta(step),
		)


@plan_feature.register(OffsetFaces)
def _offset_faces(f: OffsetFaces, t: Translator, step: Step) -> None:
	if not (f.faces and f.distance):
		return t.fallback(step, "offset without faces or distance")
	for body, faces in by_body(f.faces).items():
		p = {"faces": faces, "distance": f.distance}
		if body:
			p["body"] = body
		t.emit("solid.offset_face", p)


@plan_feature.register(Hole)
def _hole(f: Hole, t: Translator, step: Step) -> None:
	if not (f.holes and f.diameter) or not (f.depth or f.through_all):
		return t.fallback(step, "hole without positions, diameter or depth")
	base: Params = {"diameter": f.diameter}
	if f.style == "counterbore":
		if not (f.cb_diameter and f.cb_depth):
			return t.fallback(step, "counterbore without its sizes")
		base.update(type="counterbore", cb_diameter=f.cb_diameter, cb_depth=f.cb_depth)
	elif f.style == "countersink":
		if not (f.cs_diameter and f.cs_angle):
			return t.fallback(step, "countersink without its sizes")
		base.update(type="countersink", cs_diameter=f.cs_diameter, cs_angle=f.cs_angle)
	if f.depth:
		base["depth"] = f.depth
		if f.tip_angle:
			base["tip_angle"] = f.tip_angle
	if f.targets:
		base["targets"] = list(f.targets)
	name = t.feature_name(step)
	for i, h in enumerate(f.holes, 1):
		t.emit("solid.hole", dict(base, position=list(h.at), name=name if len(f.holes) == 1 else f"{name} ({i})"))


# --- bodies --------------------------------------------------------------------------------------


@plan_feature.register(Combine)
def _combine(f: Combine, t: Translator, step: Step) -> None:
	t.emit(
		"solid.combine",
		{"target": f.target, "tools": list(f.tools), "operation": f.op, "keep_tools": f.keep_tools},
		new_bodies=t.new_bodies(step),
	)


@plan_feature.register(Move)
def _move(f: Move, t: Translator, step: Step) -> None:
	if not f.bodies or f.matrix is None:
		return t.fallback(step, "move without bodies or transform")
	t.move_bodies(f.bodies, f.matrix, step.component)


@plan_feature.register(Scale)
def _scale(f: Scale, t: Translator, step: Step) -> None:
	if not f.bodies or not (f.factor or f.factors):
		return t.fallback(step, "scale without bodies or factor")
	p: Params = {"bodies": list(f.bodies)}
	if f.factor:
		p["factor"] = f.factor
	else:
		p["factors"] = list(f.factors or [])
	if f.origin:
		p["origin"] = list(f.origin)
	t.emit("solid.scale", p)


@plan_feature.register(Remove)
def _remove(f: Remove, t: Translator, step: Step) -> None:
	if not f.bodies:
		return t.fallback(step, "remove of something other than a body")
	# A removed body keeps its name in SolidCraft's history; Fusion may reuse it: free it first.
	freed = [f"{b} (removed at step {step.index})" for b in f.bodies]
	for old, new in zip(f.bodies, freed, strict=True):
		t.emit("body.rename", {"body": old, "name": new})
	t.emit("solid.remove", {"bodies": freed})


@plan_feature.register(Split)
def _split(f: Split, t: Translator, step: Step) -> None:
	tool = f.tool
	for body in f.bodies:
		p: Params
		match tool.type:
			case "line" if tool.sketch and tool.curve:
				p = {"body": body, "sketch": t.sketch_ref(tool.sketch), "line": tool.curve}
			case "plane" if tool.origin and tool.normal:
				p = {"body": body, "plane": {"origin": list(tool.origin), "normal": list(tool.normal)}}
			case "face" if tool.plane:
				p = {"body": body, "plane": {"origin": list(tool.plane.origin), "normal": list(tool.plane.normal)}}
			case "face" if tool.at and tool.body:
				p = {"body": body, "tool": {"body": tool.body, "point": list(tool.at)}}
			case _:
				return t.fallback(step, f"split by {tool.fusion_type or tool.type}")
		t.emit("solid.split_body", p, renames=[body, *t.new_bodies(step)])


@plan_feature.register(Paste)
def _paste(f: Paste, t: Translator, step: Step) -> None:
	new = t.new_bodies(step)
	if step.kind == "CopyPasteBody":
		t.emit("solid.move", {"bodies": list(f.sources), "translate": [0, 0, 0], "copy": True}, new_bodies=new)
	else:  # cut-paste moves bodies between components; components are sorted out at the end
		for old, fresh in zip(f.sources, new, strict=False):
			t.emit("body.rename", {"body": old, "name": fresh})


# --- patterns ------------------------------------------------------------------------------------


def pattern_inputs(t: Translator, inputs: Inputs | None) -> Params | None:
	if inputs is None:
		return None
	if inputs.bodies:
		return {"bodies": list(inputs.bodies)}
	if inputs.features:
		names = [t.feature_names.get((r.component, r.name)) for r in inputs.features]
		return {"features": names} if None not in names else None
	return None


@plan_feature.register(Mirror)
def _mirror(f: Mirror, t: Translator, step: Step) -> None:
	ins = pattern_inputs(t, f.inputs)
	if not ins or not f.plane:
		return t.fallback(step, "mirror of inputs or about a plane it can't name")
	p = dict(ins, plane={"origin": list(f.plane.origin), "normal": list(f.plane.normal)}, name=t.feature_name(step))
	if "bodies" in ins and f.join:
		p["combine"] = True
	variants = []
	if "features" in ins and f.created and not t.new_bodies(step):
		# mirroring the whole (symmetric) body gives the same shape when a feature mirror is refused
		whole = {k: v for k, v in p.items() if k != "features"} | {"bodies": list(f.created), "combine": True}
		variants.append(Variant(whole, "refused", "the whole body mirrored"))
	t.emit("solid.mirror", p, variants=variants, new_bodies=t.new_bodies(step), delta=t.delta(step))


def _count(expr: str) -> int | str:
	"""A count as a number when Fusion's expression is one (SolidCraft takes either)."""
	try:
		return int(float(expr))
	except ValueError:
		return expr


@plan_feature.register(CircularPattern)
def _circular(f: CircularPattern, t: Translator, step: Step) -> None:
	ins = pattern_inputs(t, f.inputs)
	if not ins or not f.axis or not f.count or f.skipped_instances:
		return t.fallback(step, "circular pattern it can't express")
	p = dict(
		ins,
		axis={"origin": list(f.axis.origin), "dir": list(f.axis.dir)},
		count=_count(f.count),
		angle=f.angle or "360 deg",
		name=t.feature_name(step),
	)
	if f.symmetric:
		p["alignment"] = "center"
	flipped = dict(p, axis={"origin": list(f.axis.origin), "dir": [-c for c in f.axis.dir]})
	t.emit(
		"solid.pattern.circular",
		p,
		variants=[Variant(flipped, "wrong", "the axis the other way")],
		new_bodies=t.new_bodies(step),
		delta=t.delta(step),
	)


@plan_feature.register(RectangularPattern)
def _rectangular(f: RectangularPattern, t: Translator, step: Step) -> None:
	ins = pattern_inputs(t, f.inputs)
	if not ins or not f.dir1 or not f.count1 or not f.spacing1 or f.skipped_instances:
		return t.fallback(step, "rectangular pattern it can't express")
	p = dict(ins)
	dir1 = list(f.dir1)
	# The sense of Fusion's direction entity isn't reliable: take it from where the copy landed.
	new = t.new_bodies(step)
	if ins.get("bodies") and len(ins["bodies"]) == 1 and new:
		src = t.prev.get(ins["bodies"][0])
		src_com = src.com if src else None
		after = step.snapshot or {}
		coms = [np.asarray(c) for k in new if k in after and (c := after[k].com) is not None]
		if src_com is not None and coms:
			d = min(coms, key=lambda c: g.dist(c, src_com)) - np.asarray(src_com)
			if np.linalg.norm(d) > 1e-6:
				dir1 = d.tolist()
	for n, d, count, spacing, sym in (
		("1", dir1, f.count1, f.spacing1, f.symmetric1),
		("2", f.dir2, f.count2, f.spacing2, f.symmetric2),
	):
		if not d or not count or not spacing or (n == "2" and str(count).strip() in ("1", "1.0")):
			continue
		p[f"dir{n}"] = g.unit(d)
		p[f"count{n}"] = _count(count)
		p[f"spacing{n}"] = spacing if f.extent_mode == "spacing" else f"({spacing}) / ({count} - 1)"
		if sym:
			p["alignment" if n == "1" else "alignment2"] = "center"
	variants = []
	if "features" in ins:
		p["name"] = t.feature_name(step)
		variants.append(Variant(dict(p, reversed=True), "wrong", "the other way"))
	t.emit("solid.pattern.rectangular", p, variants=variants, new_bodies=new, delta=t.delta(step) if variants else None)
