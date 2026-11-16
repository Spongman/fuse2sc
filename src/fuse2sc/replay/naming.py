"""Body identity: SolidCraft's bodies get Fusion's names, matched by geometry.

Pure planning (testable without SolidCraft):

- `match_bodies` pairs Fusion bodies with SolidCraft bodies by volume and centre of mass, best
  pairs first, each body used once.
- `nearest_pairs` pairs bodies a command created with the Fusion bodies its step created.
- `plan_renames` orders renames so none gives a name still in use, moving one body to a temporary
  name to break a cycle (A -> B, B -> A).

`BodyNamer` applies them through SolidCraft.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import count

from ..geometry import dist
from ..model import Body
from .checks import Measured
from .port import SolidCraftPort
from .results import StepResult
from .state import BuildState


def score(want: Body, have: Measured, vol_rel: float = 0.02, com_abs: float = 0.5) -> float | None:
	"""How well SolidCraft body `have` fits Fusion body `want` (lower is better), or None."""
	v, c = have.get("volume_mm3"), have.get("center_of_mass")
	if not want.volume or v is None or want.com is None or c is None:
		return None
	rel = abs(v - want.volume) / abs(want.volume)
	tol_c = com_abs + 1e-3 * abs(want.volume) ** (1 / 3)
	d = dist(want.com, c)
	if rel > vol_rel or d > tol_c:
		return None
	return rel / vol_rel + d / tol_c


def _unique_pairs(scored: Iterable[tuple[float, str, str]]) -> list[tuple[str, str]]:
	"""Best-first pairs from (score, a, b), each a and b used once."""
	used_a: set[str] = set()
	used_b: set[str] = set()
	pairs = []
	for _, a, b in sorted(scored):
		if a not in used_a and b not in used_b:
			used_a.add(a)
			used_b.add(b)
			pairs.append((a, b))
	return pairs


def match_bodies(
	expected: Mapping[str, Body], candidates: Mapping[str, Measured], vol_rel: float = 0.02, com_abs: float = 0.5
) -> list[tuple[str, str]]:
	"""[(Fusion key, SolidCraft name)]."""
	return _unique_pairs(
		(s, k, n)
		for k, w in expected.items()
		for n, h in candidates.items()
		if (s := score(w, h, vol_rel, com_abs)) is not None
	)


def nearest_pairs(
	created: list[str], have: Mapping[str, Measured], wanted: list[str], after: Mapping[str, Body]
) -> list[tuple[str, str]]:
	"""[(SolidCraft name, Fusion key)] by centre of mass, closest first. Bodies with no centre to
	go by (Fusion couldn't measure one) are paired in order with what's left."""
	scored = []
	for c in created:
		com = have.get(c, {}).get("center_of_mass")
		for k in wanted:
			fusion = after.get(k)
			if com is not None and fusion is not None and fusion.com is not None:
				scored.append((dist(fusion.com, com), c, k))
	pairs = _unique_pairs(scored)
	paired_c, paired_k = {c for c, _ in pairs}, {k for _, k in pairs}
	pairs += zip([c for c in created if c not in paired_c], [k for k in wanted if k not in paired_k], strict=False)
	return pairs


def plan_renames(wanted: Iterable[tuple[str, str]], existing: set[str]) -> list[tuple[str, str]]:
	"""Order renames [(from, to)] so none names a body with a name in use.

	A rename onto a name held by a body that isn't itself being renamed can't happen without losing
	that body's name, and is dropped (as are renames that depend on it)."""
	pending = {a: b for a, b in wanted if a != b}
	while blocked := [a for a, b in pending.items() if b in existing and b not in pending]:
		for a in blocked:
			del pending[a]
	names = set(existing)
	temps = (f"__fuse2sc_tmp_{i}" for i in count(1))
	out: list[tuple[str, str]] = []
	while pending:
		ready = [a for a, b in pending.items() if b not in names]
		if not ready:  # a cycle: move one body out of the way
			a = next(iter(pending))
			tmp = next(t for t in temps if t not in names)
			out.append((a, tmp))
			names.discard(a)
			names.add(tmp)
			pending[tmp] = pending.pop(a)
			continue
		for a in ready:
			b = pending.pop(a)
			out.append((a, b))
			names.discard(a)
			names.add(b)
	return out


def inside_box(body: Body, point: Iterable[float], pad: float = 0.01) -> bool:
	if not body.bbox:
		return False
	lo, hi = body.bbox
	return all(a - pad <= p <= b + pad for a, b, p in zip(lo, hi, point, strict=True))


class BodyNamer:
	def __init__(self, port: SolidCraftPort, state: BuildState) -> None:
		self.port, self.state = port, state

	async def rename(self, pairs: Iterable[tuple[str, str]], rec: StepResult | None = None) -> None:
		"""Rename [(SolidCraft name, new name)] safely; offsets follow the bodies."""
		have = await self.port.bodies()
		for old, new in plan_renames(pairs, set(have)):
			reply = await self.port.execute("body.rename", {"body": old, "name": new})
			if reply.ok:
				self.state.offsets.rename(old, new)
			else:
				where = rec.index if rec else "-"
				self.state.journal.checks.append(f"step {where}: could not name {old} as {new}: {reply.error[:150]}")

	async def name_new(self, created: list[str], fusion_new: list[str], rec: StepResult) -> None:
		"""Give a step's new bodies Fusion's names. Where SolidCraft made several bodies of what
		Fusion made one (touching regions), they are joined first."""
		after = self.state.snapshot(rec.index)
		wanted = [k for k in fusion_new if k in after]
		if not created or not wanted:
			return
		have = await self.port.bodies()
		if len(created) > len(wanted):
			created = await self._join_over_split(created, have, wanted, after, rec)
			have = await self.port.bodies()
		await self.rename(nearest_pairs(created, have, wanted, after), rec)

	async def name_pieces(self, pieces: list[str], wanted: list[str], rec: StepResult) -> None:
		"""Name the pieces of a split as Fusion does, by centre of mass."""
		after = self.state.snapshot(rec.index)
		have = await self.port.bodies()
		await self.rename(nearest_pairs(pieces, have, [k for k in wanted if k in after], after), rec)

	async def _join_over_split(
		self,
		created: list[str],
		have: Mapping[str, Measured],
		wanted: list[str],
		after: Mapping[str, Body],
		rec: StepResult,
	) -> list[str]:
		groups: dict[str, list[str]] = {}
		for c in created:
			com = have.get(c, {}).get("center_of_mass")
			if com is None:
				continue
			home = [k for k in wanted if inside_box(after[k], com)]
			key = home[0] if len(home) == 1 else min(wanted, key=lambda k: dist(after[k].com or (0, 0, 0), com))
			groups.setdefault(key, []).append(c)
		joined: list[str] = []
		for members in groups.values():
			if len(members) > 1:
				reply = await self.port.execute(
					"solid.combine", {"target": members[0], "tools": members[1:], "operation": "join"}
				)
				if not reply.ok:
					self.state.journal.checks.append(
						f"step {rec.index}: could not join {members} as Fusion has them: {reply.error[:200]}"
					)
					joined += members
					continue
			joined.append(members[0])
		return joined
