"""Hand-built IR documents for shapes whose volume, centre of mass and box are known exactly.

Fusion's numbers in a real export come from Fusion; here they come from geometry, so a test fails
only when the converter (or SolidCraft) is wrong, never because of how a design was modelled.
Solids are written as sums of boxes and cylinders (`Solid`), whose mass properties add up.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from fuse2sc import model
from fuse2sc.ir import schema

Json = dict[str, Any]  # an IR object
Frame = dict[str, list[float]]  # a sketch plane: origin, x and y

XY: Frame = {"origin": [0.0, 0.0, 0.0], "x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0]}
XZ: Frame = {"origin": [0.0, 0.0, 0.0], "x": [1.0, 0.0, 0.0], "y": [0.0, 0.0, 1.0]}  # normal: x × y = -Y
COMP = "root"
BODY = f"{COMP}/Body1"


@dataclass
class Solid:
	"""A solid as signed pieces: (volume, centroid, sign). Mass properties add."""

	parts: list[tuple[float, tuple[float, float, float]]] = field(default_factory=list)
	box: tuple[tuple[float, ...], tuple[float, ...]] | None = None

	def add_box(self, lo: Sequence[float], hi: Sequence[float], sign: int = 1) -> Solid:
		v = math.prod(h - lo_ for lo_, h in zip(lo, hi, strict=True))
		self.parts.append((sign * v, tuple((a + b) / 2 for a, b in zip(lo, hi, strict=True))))
		return self

	def add_cylinder(
		self, center: Sequence[float], radius: float, z0: float, z1: float, sign: int = 1, axis: str = "z"
	) -> Solid:
		v = math.pi * radius**2 * (z1 - z0)
		c = list(center)
		c.insert("xyz".index(axis), (z0 + z1) / 2)
		self.parts.append((sign * v, tuple(c)))
		return self

	@property
	def volume(self) -> float:
		return sum(v for v, _ in self.parts)

	@property
	def com(self) -> list[float]:
		return [sum(v * c[i] for v, c in self.parts) / self.volume for i in range(3)]

	def body(self, box: tuple[Sequence[float], Sequence[float]]) -> Json:
		return {"volume": self.volume, "com": self.com, "bbox": [list(box[0]), list(box[1])], "visible": True}


class Doc:
	def __init__(self, name: str) -> None:
		self.raw = schema.new_document(name, "test")
		self.raw["fusion"] = {"name": name}
		self.bodies: dict[str, Json] = {}

	def param(self, name: str, expression: str, unit: str = "mm") -> None:
		self.raw["parameters"].append({"name": name, "expression": expression, "unit": unit})

	def step(self, **s: Any) -> Json:
		s.setdefault("index", len(self.raw["steps"]))
		s.setdefault("component", COMP)
		s.setdefault("snapshot", {k: dict(v) for k, v in self.bodies.items()})
		self.raw["steps"].append(s)
		return s

	def sketch(
		self,
		name: str,
		frame: Frame,
		points: list[Json],
		curves: list[Json],
		constraints: Iterable[Json] = (),
		dimensions: Iterable[Json] = (),
	) -> str:
		uid = f"{COMP}::{name}@{len(self.raw['steps'])}"
		self.step(
			name=name,
			kind="Sketch",
			sketch={
				"uid": uid,
				"name": name,
				"component": COMP,
				"frame": frame,
				"points": points,
				"curves": curves,
				"constraints": list(constraints),
				"dimensions": list(dimensions),
			},
		)
		return uid

	def rect(
		self, name: str, x0: float, y0: float, x1: float, y1: float, frame: Frame = XY, constraints: Iterable[Json] = ()
	) -> Json:
		pts = [{"id": "P0", "xy": [0.0, 0.0], "fixed": True}] + [
			{"id": f"P{i + 1}", "xy": list(p)} for i, p in enumerate([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
		]
		curves = [{"id": f"C{i}", "type": "line", "ends": [f"P{i + 1}", f"P{(i + 1) % 4 + 1}"]} for i in range(4)]
		uid = self.sketch(name, frame, pts, curves, constraints)
		return {
			"sketch": uid,
			"outer": ["C0", "C1", "C2", "C3"],
			"inside": [(x0 + x1) / 2, (y0 + y1) / 2],
			"area": abs(x1 - x0) * abs(y1 - y0),
			"centroid": [(x0 + x1) / 2, (y0 + y1) / 2],
		}

	def circle(self, name: str, cx: float, cy: float, r: float, frame: Frame = XY) -> Json:
		pts = [{"id": "P0", "xy": [0.0, 0.0], "fixed": True}, {"id": "P1", "xy": [cx, cy]}]
		uid = self.sketch(name, frame, pts, [{"id": "C0", "type": "circle", "center": "P1", "radius": r}])
		return {"sketch": uid, "outer": ["C0"], "inside": [cx, cy], "area": math.pi * r * r, "centroid": [cx, cy]}

	def feature(self, name: str, kind: str, payload: Json, bodies: dict[str, Json] | None = None, **extra: Any) -> Json:
		if bodies is not None:
			self.bodies = bodies
		return self.step(name=name, kind=kind, feature=payload, **extra)

	def extrude(
		self,
		name: str,
		region: Json,
		distance: str,
		op: str = "new",
		bodies: dict[str, Json] | None = None,
		two: str | None = None,
		targets: Iterable[str] = (),
		start: str | None = None,
	) -> Json:
		ext = (
			{
				"type": "two",
				"side1": {"type": "distance", "expr": distance, "mm": 1.0},
				"side2": {"type": "distance", "expr": two, "mm": 1.0},
			}
			if two
			else {"type": "one", "side1": {"type": "distance", "expr": distance}}
		)
		payload = {"op": op, "regions": [region], "extent": ext, "targets": list(targets)}
		if start:
			payload["start"] = {"type": "offset", "expr": start}
		return self.feature(name, "ExtrudeFeature", payload, bodies)

	@property
	def doc(self) -> model.Document:
		return model.parse(self.raw)


def lidded_box() -> Doc:
	"""An open box (80 x 50 x 30, walls 2.5) with an 8 mm hole in its front wall, and a separate
	lid (80 x 50 x 2.5) floating above it with a round knob (r 6, 6 high). Every number below is
	exact: boxes and cylinders, added and taken away."""
	d = Doc("Lidded box")
	for n, e in (
		("width", "80 mm"),
		("depth", "50 mm"),
		("height", "30 mm"),
		("wall", "2.5 mm"),
		("hole_d", "8 mm"),
		("lid_gap", "10 mm"),
	):
		d.param(n, e)
	base = d.rect("Base", -40, -25, 40, 25)
	box = Solid().add_box((-40, -25, 0), (40, 25, 30))
	d.extrude("Extrude1", base, "height", bodies={BODY: box.body(((-40, -25, 0), (40, 25, 30)))})
	box.add_box((-37.5, -22.5, 2.5), (37.5, 22.5, 30), sign=-1)
	d.feature(
		"Shell1",
		"ShellFeature",
		{"faces": [{"at": [0, 0, 30], "body": BODY}], "inside": "wall"},
		bodies={BODY: box.body(((-40, -25, 0), (40, 25, 30)))},
	)
	# the hole: a circle on the XZ plane through the front wall (y from -25 to -22.5), centred at z 15
	hole = d.circle("Hole", 0, 15, 4, frame=XZ)
	box.add_cylinder((0, 15), 4, -25, -22.5, sign=-1, axis="y")
	d.extrude(
		"Extrude2",
		hole,
		"wall",
		op="cut",
		targets=[BODY],
		bodies={BODY: box.body(((-40, -25, 0), (40, 25, 30)))},
		start=None,
	)
	# Fusion extrudes along the XZ sketch's normal (-Y): from y 0 to -2.5 would miss the wall, so
	# the cut starts at the wall's inner face: start offset 22.5 along the normal
	d.raw["steps"][-1]["feature"]["start"] = {"type": "offset", "expr": "depth / 2 - wall"}
	lid_key = f"{COMP}/Lid"
	lid = Solid().add_box((-40, -25, 40), (40, 25, 42.5))
	lid_region = d.rect("Lid", -40, -25, 40, 25)
	d.extrude(
		"Extrude3",
		lid_region,
		"wall",
		start="height + lid_gap",
		bodies={BODY: box.body(((-40, -25, 0), (40, 25, 30))), lid_key: lid.body(((-40, -25, 40), (40, 25, 42.5)))},
	)
	knob = d.circle("Knob", 0, 0, 6)
	lid.add_cylinder((0, 0), 6, 42.5, 48.5, axis="z")
	d.extrude(
		"Extrude4",
		knob,
		"6 mm",
		op="join",
		start="height + lid_gap + wall",
		targets=[lid_key],
		bodies={BODY: box.body(((-40, -25, 0), (40, 25, 30))), lid_key: lid.body(((-40, -25, 40), (40, 25, 48.5)))},
	)
	return d
