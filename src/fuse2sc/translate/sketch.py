"""Sketches: geometry where Fusion has it, then its constraints and driving dimensions.

The geometry is drawn at Fusion's solved positions, so the shape is right even where SolidCraft
reads a constraint differently. Constraints and dimensions are optional commands: the replay adds
each one, and takes it back if it moved anything (replay/guard.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model import Curve, Dimension, Relation, Sketch
from .plan import Params

if TYPE_CHECKING:
	from .translator import Translator

# Fusion constraint -> (SolidCraft command, {param: Fusion role or (roles…)}, fixed params)
CONSTRAINTS: dict[str, tuple[str, dict[str, str | tuple[str, ...]], Params]] = {
	"Horizontal": ("sketch.constraint.horizontal_vertical", {"line": "line"}, {"mode": "horizontal"}),
	"Vertical": ("sketch.constraint.horizontal_vertical", {"line": "line"}, {"mode": "vertical"}),
	"HorizontalPoints": (
		"sketch.constraint.horizontal_vertical",
		{"points": ("pointOne", "pointTwo")},
		{"mode": "horizontal"},
	),
	"VerticalPoints": (
		"sketch.constraint.horizontal_vertical",
		{"points": ("pointOne", "pointTwo")},
		{"mode": "vertical"},
	),
	"Coincident": ("sketch.constraint.coincident", {"a": "point", "b": "entity"}, {}),
	"Perpendicular": ("sketch.constraint.perpendicular", {"a": "lineOne", "b": "lineTwo"}, {}),
	"Parallel": ("sketch.constraint.parallel", {"a": "lineOne", "b": "lineTwo"}, {}),
	"Collinear": ("sketch.constraint.collinear", {"a": "lineOne", "b": "lineTwo"}, {}),
	"Tangent": ("sketch.constraint.tangent", {"a": "curveOne", "b": "curveTwo"}, {}),
	"Equal": ("sketch.constraint.equal", {"a": "curveOne", "b": "curveTwo"}, {}),
	"Smooth": ("sketch.constraint.curvature", {"a": "curveOne", "b": "curveTwo"}, {}),
	"Concentric": ("sketch.constraint.concentric", {"a": "entityOne", "b": "entityTwo"}, {}),
	"MidPoint": ("sketch.constraint.midpoint", {"point": "point", "line": "midPointCurve"}, {}),
	"Symmetry": ("sketch.constraint.symmetry", {"a": "entityOne", "b": "entityTwo", "line": "symmetryLine"}, {}),
}

# Fusion dimension -> (roles of its entities, SolidCraft type, or None to use the orientation)
DIMENSIONS: dict[str, tuple[tuple[str, ...], str | None]] = {
	"Linear": (("entityOne", "entityTwo"), None),
	"Offset": (("line", "entityTwo"), "distance"),
	"Diameter": (("entity",), "diameter"),
	"Radial": (("entity",), "radius"),
	"Angular": (("lineOne", "lineTwo"), "angle"),
}
LINEAR_TYPES = {"aligned": "distance", "horizontal": "horizontal", "vertical": "vertical", None: "distance"}


class SketchPlanner:
	def __init__(self, t: Translator, sk: Sketch) -> None:
		self.t, self.sk = t, sk
		self.name = t.sketch_name(sk)
		self.at = {p.id: list(p.xy) for p in sk.points}
		self.ref: dict[str, str] = {}  # Fusion point id -> SolidCraft point reference
		self.drawn: set[str] = set()
		self.fixed: list[str] = []

	def plan(self) -> None:
		sk, t = self.sk, self.t
		t.emit(
			"sketch.create",
			{
				"name": self.name,
				"plane": {"origin": list(sk.frame.origin), "x_dir": list(sk.frame.x), "y_dir": list(sk.frame.y)},
			},
		)
		origin = next((p for p in sk.points if p.fixed and tuple(p.xy) == (0.0, 0.0)), None)
		if origin:
			self.ref[origin.id] = "origin"
		for c in sk.curves:
			self.curve(c)
		self.loose_points()
		if self.fixed:
			t.emit("sketch.constraint.fix", {"entities": self.fixed, "fixed": True}, optional=True)
		left_out: dict[str, int] = {}
		for g in sk.constraints:
			if not self.constraint(g):
				left_out[g.type] = left_out.get(g.type, 0) + 1
		if left_out:
			t.note(
				f"{self.name}: constraints not carried over: "
				+ ", ".join(f"{k} ×{n}" for k, n in sorted(left_out.items()))
			)
		skipped = sum(1 for d in sk.dimensions if d.driving and d.expr and not self.dimension(d))
		if skipped:
			t.note(f"{self.name}: {skipped} driving dimension(s) not carried over")
		t.emit("sketch.finish", {})

	# --- geometry -------------------------------------------------------------------------------

	def curve(self, c: Curve) -> None:
		flags = {"construction": True} if c.construction else {}
		ok = self._draw(c, flags)
		if ok:
			self.drawn.add(c.id)
			if c.fixed:
				self.fixed.append(c.id)
		elif c.polyline and len(c.polyline) >= 2:
			pts = [list(p) for p in c.polyline]
			self.t.emit("sketch.line", {"points": pts, "ids": [f"{c.id}~{i}" for i in range(len(pts) - 1)], **flags})
			self.t.note(f"{self.name}: {c.fusion_type or c.type} {c.id} drawn as {len(pts) - 1} straight pieces")
		else:
			self.t.note(f"{self.name}: {c.fusion_type or c.type} {c.id} left out")

	def _known(self, ids: list[str | None] | None) -> list[str] | None:
		"""The point ids, if every one is a point this sketch has."""
		if not ids or not all(isinstance(i, str) and i in self.at for i in ids):
			return None
		return [i for i in ids if i is not None]

	def _draw(self, c: Curve, flags: Params) -> bool:
		at, t = self.at, self.t
		ends, through = self._known(c.ends), self._known(c.through)
		center = c.center if c.center in at else None
		match c.type:
			case "line" if ends:
				t.emit("sketch.line", {"points": [at[ends[0]], at[ends[1]]], "ids": [c.id], **flags})
				self._end_refs(c, ("start", "end"))
			case "circle" if center and c.radius:
				t.emit("sketch.circle.center", {"center": at[center], "radius": c.radius, "id": c.id, **flags})
				self.ref.setdefault(center, f"{c.id}.center")
			case "arc" if ends and c.via:
				t.emit(
					"sketch.arc.three_point",
					{"start": at[ends[0]], "end": at[ends[1]], "through": list(c.via), "id": c.id, **flags},
				)
				self._end_refs(c, ("start", "end"))
				if c.center:
					self.ref.setdefault(c.center, f"{c.id}.center")
			case "ellipse" if center and c.major and c.minor_radius:
				t.emit(
					"sketch.ellipse",
					{
						"center": at[center],
						"major": list(c.major),
						"minor_radius": c.minor_radius,
						"axes": False,
						**flags,
					},
					aliases={"curves": [c.id]},
				)
			case "spline_fit" if through:
				params: Params = {"points": [at[p] for p in through], **flags}
				if c.closed:
					params["periodic"] = True
				t.emit("sketch.spline.fit_point", params, aliases={"curves": [c.id], "points": through})
				for p in through:
					self.ref.setdefault(p, p)
			case "spline_cp" if c.poles and c.degree == 3 and not c.rational:
				t.emit(
					"sketch.spline.control_point",
					{"points": [list(p) for p in c.poles], **flags},
					aliases={"curves": [c.id]},
				)
			case _:
				return False
		return True

	def _end_refs(self, c: Curve, names: tuple[str, str]) -> None:
		for pid, end in zip(c.ends or [], names, strict=False):
			if pid:
				self.ref.setdefault(pid, f"{c.id}.{end}")

	def loose_points(self) -> None:
		used = set(self.ref)
		for p in self.sk.points:
			if p.id in used:
				if p.fixed and self.ref[p.id] != "origin":
					self.fixed.append(self.ref[p.id])
				continue
			self.t.emit("sketch.point", {"point": list(p.xy), "id": p.id})
			self.ref[p.id] = p.id
			if p.fixed:
				self.fixed.append(p.id)

	# --- relations ------------------------------------------------------------------------------

	def resolve(self, fusion_id: str | None) -> str | None:
		if fusion_id is None:
			return None
		if fusion_id in self.drawn:
			return fusion_id
		return self.ref.get(fusion_id)

	def constraint(self, g: Relation) -> bool:
		rule = CONSTRAINTS.get(g.type)
		if rule is None:
			return False
		command, roles, fixed = rule
		params: Params = dict(fixed)
		for param, role in roles.items():
			if isinstance(role, tuple):
				vals = [self.resolve(g.refs.get(r)) for r in role]
				if None in vals:
					return False
				params[param] = vals
			else:
				val = self.resolve(g.refs.get(role))
				if val is None:
					return False
				params[param] = val
		if g.type == "Coincident" and params["a"] == params["b"]:
			return True  # the same point twice: already true
		self.t.emit(command, params, optional=True)
		return True

	def dimension(self, d: Dimension) -> bool:
		rule = DIMENSIONS.get(d.type)
		if rule is None:
			return False
		roles, kind = rule
		ents = [self.resolve(d.refs.get(r)) for r in roles]
		if None in ents:
			return False
		params: Params = {"entities": ents, "type": kind or LINEAR_TYPES[d.orientation], "value": d.expr}
		if d.type == "Angular" and d.text_at:
			params["text_at"] = list(d.text_at)
		self.t.emit("sketch.dimension", params, optional=True)
		return True


def plan_sketch(t: Translator, sk: Sketch) -> None:
	SketchPlanner(t, sk).plan()
