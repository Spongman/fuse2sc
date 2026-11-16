"""Finding Fusion's regions among SolidCraft's, by area and centroid.

SolidCraft only knows a sketch's regions once it has built the sketch, and may split them its own
way. Each Fusion region takes the SolidCraft region with the same area and centroid. Tolerances grow
with the region's size; an ambiguous match (two candidates about equally good) is refused rather
than guessed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ..geometry import dist
from ..translate.plan import RegionMatch
from .checks import Tolerances

CLEAR_WINNER = 0.3  # the best match must score below this share of the runner-up's


def match_profiles(
	have: Sequence[Mapping[str, Any]], wanted: Sequence[RegionMatch], tol: Tolerances | None = None
) -> tuple[list[int] | None, str | None]:
	"""SolidCraft profile indices for Fusion's regions, or (None, why)."""
	tol = tol or Tolerances()
	picks: list[int] = []
	for w in wanted:
		area_tol = tol.region_area_rel * w.area
		c_tol = tol.region_centroid_abs + tol.region_centroid_rel * math.sqrt(max(w.area, 1e-12))
		scored = sorted(
			(abs(p["area_mm2"] - w.area) / area_tol + dist(p["centroid_sketch"], w.centroid) / c_tol, p["index"])
			for p in have
			if p.get("area_mm2") is not None
			and p.get("centroid_sketch") is not None
			and abs(p["area_mm2"] - w.area) <= area_tol
			and dist(p["centroid_sketch"], w.centroid) <= c_tol
		)
		where = f"{w.area:.1f} mm2 at {[round(x, 2) for x in w.centroid]}"
		if not scored:
			return None, f"no SolidCraft region of {where}"
		if len(scored) > 1 and scored[0][0] > CLEAR_WINNER * scored[1][0]:
			return None, f"several SolidCraft regions match {where}"
		picks.append(scored[0][1])
	if len(set(picks)) != len(picks):
		return None, "two of Fusion's regions matched the same SolidCraft region"
	return picks, None
