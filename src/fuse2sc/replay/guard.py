"""Keeping a sketch where Fusion drew it while its constraints and dimensions are added.

The geometry is drawn at Fusion's solved positions, so every constraint Fusion had already holds.
One SolidCraft reads differently (an Equal between the wrong pair, a dimension on the wrong
entities) would move curves: after each one the sketch is inspected, and if any point or radius
moved, the constraint is undone and reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .port import SolidCraftPort

MOVE_TOL = 1e-4  # mm
SIZES = ("radius", "major_radius", "minor_radius")


def positions(inspect: Mapping[str, Any]) -> dict[str, tuple[float, ...]]:
	"""{entity: numbers} for every point's position and every curve's size."""
	out = {f"p:{p['id']}": tuple(p["at"]) for p in inspect.get("points") or [] if p.get("at") is not None}
	for c in inspect.get("curves") or []:
		out |= {f"{k}:{c['id']}": (c[k],) for k in SIZES if isinstance(c.get(k), (int, float))}
	return out


def moved(
	before: Mapping[str, tuple[float, ...]], after: Mapping[str, tuple[float, ...]], tol: float = MOVE_TOL
) -> list[str]:
	"""Entities whose numbers changed by more than `tol` (new entities don't count)."""
	return [
		k.split(":", 1)[1]
		for k, a in before.items()
		if (b := after.get(k)) is not None
		and len(a) == len(b)
		and max(abs(x - y) for x, y in zip(a, b, strict=True)) > tol
	]


class SketchGuard:
	def __init__(self, sc: SolidCraftPort) -> None:
		self.sc = sc
		self.baseline: dict[str, tuple[float, ...]] | None = None

	def reset(self) -> None:
		self.baseline = None

	async def _snapshot(self) -> dict[str, tuple[float, ...]] | None:
		reply = await self.sc.execute("sketch.inspect", {})
		return positions(reply.body) if reply.ok and isinstance(reply.body, dict) else None

	async def prepare(self) -> None:
		if self.baseline is None:
			self.baseline = await self._snapshot()

	async def moved_and_undone(self) -> list[str]:
		"""After an optional constraint ran: what it moved (and it is undone), or []."""
		if self.baseline is None or (now := await self._snapshot()) is None:
			return []
		if m := moved(self.baseline, now):
			await self.sc.undo()
			return m
		for k, v in now.items():  # points the constraint created join the baseline
			self.baseline.setdefault(k, v)
		return []
