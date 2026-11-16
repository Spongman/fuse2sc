"""A sketch region (Fusion "profile") as the IR names it: the curves around it, a point well
inside it, and its area and centroid computed from its outline.

The inside point is chosen for clearance, not just taken from the centroid: an L-shape's or a
ring's centroid lies outside the region. Area and centroid come from the outline polygons, so
they are certainly in sketch coordinates.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .sketches import SketchIndex, point_at_middle, sketch_uid, strokes

if TYPE_CHECKING:
	import adsk.fusion

	from .records import Point, Polyline, Record


def _chain(runs: list[Polyline]) -> Polyline:
	"""Join a loop's curve runs end to end, turning each to continue from the previous end."""
	runs = [r for r in runs if len(r) >= 2]
	if not runs:
		return []
	out = list(runs[0])
	rest = runs[1:]
	if rest:
		# orient the first run so its end meets one of the others
		def gap(p: Point) -> float:
			return min(min(math.dist(p, r[0]), math.dist(p, r[-1])) for r in rest)

		if gap(out[0]) < gap(out[-1]):
			out.reverse()
	while rest:
		end = out[-1]
		best = min(range(len(rest)), key=lambda i: min(math.dist(end, rest[i][0]), math.dist(end, rest[i][-1])))
		r = rest.pop(best)
		if math.dist(end, r[-1]) < math.dist(end, r[0]):
			r = r[::-1]
		out += r[1:]
	return out


def shoelace(poly: Polyline) -> tuple[float, list[float]]:
	"""(signed area, centroid) of a polygon."""
	a = cx = cy = 0.0
	n = len(poly)
	for i in range(n):
		x0, y0 = poly[i]
		x1, y1 = poly[(i + 1) % n]
		k = x0 * y1 - x1 * y0
		a += k
		cx += (x0 + x1) * k
		cy += (y0 + y1) * k
	a *= 0.5
	if abs(a) < 1e-12:
		return 0.0, [0.0, 0.0]
	return a, [cx / (6 * a), cy / (6 * a)]


def contains(poly: Polyline, p: Point) -> bool:
	x, y = p
	inside = False
	n = len(poly)
	for i in range(n):
		x0, y0 = poly[i]
		x1, y1 = poly[(i + 1) % n]
		if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
			inside = not inside
	return inside


def _seg_dist(p: Point, a: Point, b: Point) -> float:
	ax, ay = a
	bx, by = b
	dx, dy = bx - ax, by - ay
	L = dx * dx + dy * dy
	t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
	return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def clearance(polys: list[Polyline], p: Point) -> float:
	return min(_seg_dist(p, poly[i], poly[(i + 1) % len(poly)]) for poly in polys for i in range(len(poly)))


def inside_point(outer: Polyline, holes: list[Polyline], prefer: Point | None = None, grid: int = 24) -> Point | None:
	"""A point inside `outer` and outside every hole, as far from the edges as a grid search finds."""
	if len(outer) < 3:
		return None
	polys = [outer] + [h for h in holes if len(h) >= 3]

	def ok(p: Point) -> bool:
		return contains(outer, p) and not any(contains(h, p) for h in polys[1:])

	best: Point | None = None
	best_c = -1.0
	if prefer and ok(prefer):
		best, best_c = prefer, clearance(polys, prefer)
	xs = [p[0] for p in outer]
	ys = [p[1] for p in outer]
	x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
	for i in range(grid):
		for j in range(grid):
			p = [x0 + (x1 - x0) * (i + 0.5) / grid, y0 + (y1 - y0) * (j + 0.5) / grid]
			if ok(p):
				c = clearance(polys, p)
				if c > best_c * 1.5:  # prefer the centroid unless a point is clearly better
					best, best_c = p, c
	return [round(best[0], 6), round(best[1], 6)] if best else None


def read_region(profile: adsk.fusion.Profile, index_cache: dict[str, SketchIndex] | None = None) -> Record:
	"""IR region for a Fusion Profile."""
	sk = profile.parentSketch
	key = sketch_uid(sk)
	cache = index_cache if index_cache is not None else {}
	ix = cache.get(key)
	if ix is None:
		ix = cache[key] = SketchIndex(sk)
	outer: list[str | None] = []
	holes: list[list[str | None]] = []
	outer_poly: Polyline = []
	hole_polys: list[Polyline] = []
	for loop in profile.profileLoops:
		ids: list[str | None] = []
		runs: list[Polyline] = []
		for pc in loop.profileCurves:
			hint = None
			try:
				hint = pc.sketchEntity
			except Exception:
				pass
			mid = None
			try:
				mid = point_at_middle(pc.geometry)
			except Exception:
				pass
			ids.append(ix.curve_through(mid, hint) if mid is not None else ix.id_of(hint))
			runs.append(strokes(pc.geometry))
		poly = _chain(runs)
		if loop.isOuter:
			outer, outer_poly = ids, poly
		else:
			holes.append(ids)
			hole_polys.append(poly)
	rec: Record = {"sketch": key, "outer": outer, "holes": holes}
	a, c = shoelace(outer_poly) if len(outer_poly) >= 3 else (0.0, None)
	area = abs(a)
	moment = [c[0] * area, c[1] * area] if c else [0.0, 0.0]
	for h in hole_polys:
		ha, hc = shoelace(h) if len(h) >= 3 else (0.0, [0.0, 0.0])
		ha = abs(ha)
		area -= ha
		moment = [moment[0] - hc[0] * ha, moment[1] - hc[1] * ha]
	if area > 1e-9 and c:
		rec["area"] = round(area, 6)
		rec["centroid"] = [round(moment[0] / area, 6), round(moment[1] / area, 6)]
	rec["inside"] = inside_point(outer_poly, hole_polys, rec.get("centroid"))
	return rec
