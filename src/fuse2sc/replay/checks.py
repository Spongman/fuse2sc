"""Comparing SolidCraft's bodies with Fusion's snapshot, and every tolerance the build uses.

All tolerances live in `Tolerances`, which a workspace can override (setting "tolerances"):

- volume: 0.1% relative with a 0.5 mm³ floor. Both kernels measure curved faces closely; a missing
  feature in a real part changes volume by far more.
- centre of mass: 0.02 mm plus 0.1% of the body's size (its box diagonal). Catches a feature in
  the wrong place with the right volume.
- bounding box: 0.5 mm plus 1% of the body's size, per corner coordinate. Fusion's box is not
  always tight on curved bodies, so this only catches gross misplacement.
- area: 0.5% relative, reported but never a mismatch on its own.
- regions: SolidCraft's region of the same area (0.5%) and centroid (0.02 mm plus 0.2% of the
  region's size) as Fusion's.
- a command's change (to decide between two ways of building it): volume within 0.1% plus 1 mm³,
  first moment within 0.1% plus 0.05 mm × the volume change plus 2 mm⁴.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel

from ..geometry import dist
from ..ir.keys import component_of
from ..model import Body

Measured = Mapping[str, Any]  # one body as SolidCraft's `measure` reports it


class Tolerances(BaseModel):
	volume_rel: float = 1e-3
	volume_abs: float = 0.5
	com_abs: float = 0.02
	com_rel: float = 1e-3
	bbox_abs: float = 0.5
	bbox_rel: float = 0.01
	area_rel: float = 5e-3
	region_area_rel: float = 5e-3
	region_centroid_abs: float = 0.02
	region_centroid_rel: float = 2e-3
	delta_volume_rel: float = 1e-3
	delta_volume_abs: float = 1.0
	delta_moment_rel: float = 1e-3
	delta_moment_per_volume: float = 0.05
	delta_moment_abs: float = 2.0


def size_of(b: Body) -> float:
	if b.bbox:
		return dist(b.bbox[0], b.bbox[1])
	return abs(b.volume or 0.0) ** (1 / 3)


def sc_box(b: Measured) -> tuple[Any, Any] | None:
	box = b.get("bbox")
	return (box["min"], box["max"]) if isinstance(box, Mapping) and "min" in box else None


@dataclass
class Offsets:
	"""Bodies whose Fusion-made geometry SolidCraft measures slightly differently (after a fallback
	import): later checks compare against Fusion's number plus this difference."""

	by_body: dict[str, tuple[float, np.ndarray]] = field(default_factory=dict)

	def corrected(self, key: str, b: Measured) -> tuple[float | None, np.ndarray | None]:
		v, c = b.get("volume_mm3"), b.get("center_of_mass")
		com = None if c is None else np.asarray(c, float)
		if key in self.by_body and v is not None:
			dv, dc = self.by_body[key]
			v -= dv
			com = None if com is None else com - dc
		return v, com

	def rename(self, old: str, new: str) -> None:
		if old in self.by_body:
			self.by_body[new] = self.by_body.pop(old)


def problems_with(key: str, want: Body, have: Measured | None, tol: Tolerances, offsets: Offsets) -> list[str]:
	"""Why SolidCraft's body is not Fusion's (empty when it is)."""
	if want.volume is None:
		return []
	if have is None:
		return [f"{key} is missing"]
	v, c = offsets.corrected(key, have)
	if v is None:
		return [f"{key} is open (no volume); Fusion {want.volume:.1f} mm3"]
	out = []
	if abs(v - want.volume) > max(tol.volume_rel * abs(want.volume), tol.volume_abs):
		out.append(f"{key} volume {v:.1f} mm3, Fusion {want.volume:.1f} ({100 * (v / want.volume - 1):+.2f}%)")
	size = size_of(want)
	if want.com is not None and c is not None and dist(want.com, c) > tol.com_abs + tol.com_rel * size:
		out.append(f"{key} is {dist(want.com, c):.2f} mm from where Fusion has it")
	box = sc_box(have)
	if want.bbox and box and key not in offsets.by_body:
		worst = float(np.max(np.abs(np.asarray(want.bbox, float) - np.asarray(box, float))))
		if worst > tol.bbox_abs + tol.bbox_rel * size:
			out.append(f"{key}'s bounding box is {worst:.2f} mm off Fusion's")
	return out


@dataclass(frozen=True)
class Comparison:
	wrong: list[str]
	extra: list[str]
	problems: list[str]

	@property
	def ok(self) -> bool:
		return not (self.wrong or self.extra)


def compare(
	expected: Mapping[str, Body],
	have: Mapping[str, Measured],
	tol: Tolerances,
	offsets: Offsets,
	skip: Set[str] = frozenset(),
) -> Comparison:
	wrong, problems = [], []
	for key, want in expected.items():
		if component_of(key) in skip:
			continue
		if p := problems_with(key, want, have.get(key), tol, offsets):
			wrong.append(key)
			problems += p
	extra = [k for k in have if k not in expected and component_of(k) not in skip]
	if extra:
		problems.append("extra bodies: " + ", ".join(extra))
	return Comparison(wrong, extra, problems)


def area_notes(expected: Mapping[str, Body], have: Mapping[str, Measured], tol: Tolerances) -> list[str]:
	out = []
	for k, want in expected.items():
		a = (have.get(k) or {}).get("area_mm2")
		if want.area and a and abs(a - want.area) > tol.area_rel * want.area:
			out.append(f"{k} area {a:.1f} mm2, Fusion {want.area:.1f}")
	return out


def totals(have: Mapping[str, Measured]) -> np.ndarray:
	"""[V, Mx, My, Mz]: SolidCraft's total volume and its first moment about the origin."""
	out = np.zeros(4)
	for b in have.values():
		v, c = b.get("volume_mm3"), b.get("center_of_mass")
		if v and c is not None:
			out += np.concatenate(([v], v * np.asarray(c, float)))
	return out


def delta_ok(before: np.ndarray, now: np.ndarray, want: tuple[float, float, float, float], tol: Tolerances) -> bool:
	"""Whether a command changed total volume and moment as the Fusion step did."""
	got, w = now - before, np.asarray(want, float)
	tol_v = tol.delta_volume_rel * abs(w[0]) + tol.delta_volume_abs
	tol_m = (
		tol.delta_moment_rel * float(np.linalg.norm(w[1:]))
		+ tol.delta_moment_per_volume * abs(w[0])
		+ tol.delta_moment_abs
	)
	return bool(abs(got[0] - w[0]) <= tol_v and float(np.linalg.norm(got[1:] - w[1:])) <= tol_m)
