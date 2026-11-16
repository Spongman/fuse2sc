"""Retry strategies for commands SolidCraft refuses, each scoped to the SolidCraft versions it is
for. When a release fixes the problem behind one, narrow its range (keeping the test that shows the
difference) rather than deleting it blind.

A strategy gets a `StrategyHost` (what it may do) and the refused command, and returns "full" when
it built the equivalent, "partial" when it built some of it, or None when it built nothing. The
check after the step still decides whether the result matches Fusion.

To add one: write `async def my_strategy(host, command, params, rec) -> Outcome`, and add a
`Strategy` to STRATEGIES with the commands it applies to and the versions it is for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..solidcraft import Reply, in_range
from .results import StepResult

Outcome = Literal["full", "partial"] | None


class StrategyHost(Protocol):
	async def execute(self, command: str, params: dict[str, Any]) -> Reply: ...

	async def undo(self) -> bool: ...

	async def created(self, replies: list[Any], rec: StepResult) -> None:
		"""Name the bodies these replies created, as the command being retried would have."""


StrategyFn = Callable[[StrategyHost, str, dict[str, Any], StepResult], Awaitable[Outcome]]


@dataclass(frozen=True)
class Strategy:
	name: str
	versions: str  # PEP 440 range, e.g. ">=0.30,<0.43"; "" for every version
	applies: Callable[[str, dict[str, Any]], bool]
	run: StrategyFn
	why: str


async def extrude_in_parts(host: StrategyHost, command: str, params: dict[str, Any], rec: StepResult) -> Outcome:
	"""One extrude of several regions refused: extrude the regions in halves, recursively, until
	each group builds or is a single refused region."""
	built: list[Any] = []
	refused: list[list[Any]] = []

	async def attempt(regions: list[Any], depth: int = 0) -> None:
		name = f"{params.get('name', 'Extrude')} (part {len(built) + 1})"
		reply = await host.execute(command, params | {"profiles": regions, "name": name})
		if reply.ok:
			built.append(reply.body)
		elif len(regions) == 1 or depth > 8:
			refused.append(regions)
		else:
			mid = len(regions) // 2
			await attempt(regions[:mid], depth + 1)
			await attempt(regions[mid:], depth + 1)

	await attempt(list(params["profiles"]))
	if not built:
		return None
	await host.created(built, rec)
	rec.adjust(f"extruded in {len(built)} parts; {len(refused)} region group(s) refused")
	return "partial" if refused else "full"


async def blend_edge_groups(host: StrategyHost, command: str, params: dict[str, Any], rec: StepResult) -> Outcome:
	"""A fillet or chamfer of several edges refused: find the edges SolidCraft takes on their own,
	round those together, else one pass each. Edges are found again by their points."""
	good, bad = [], []
	for edge in params["edges"]:
		if (await host.execute(command, params | {"edges": [edge]})).ok:
			await host.undo()
			good.append(edge)
		else:
			bad.append(edge)
	if not good:
		return None
	replies: list[Any] = []
	together = await host.execute(command, params | {"edges": good})
	if together.ok:
		replies.append(together.body)
	else:
		for i, edge in enumerate(good, 1):
			part = await host.execute(
				command, params | {"edges": [edge], "name": f"{params.get('name', command)} (part {i})"}
			)
			if part.ok:
				replies.append(part.body)
			else:
				bad.append(edge)
	if not replies:
		return None
	rec.adjust(f"{command}: {len(params['edges']) - len(bad)} edge(s) rounded, {len(bad)} refused by SolidCraft")
	return "partial" if bad else "full"


STRATEGIES: tuple[Strategy, ...] = (
	Strategy(
		"extrude-in-parts",
		"",
		lambda c, p: c == "solid.extrude" and len(p.get("profiles") or []) > 1,
		extrude_in_parts,
		"SolidCraft can refuse one region of a multi-region extrude; the others still build.",
	),
	Strategy(
		"blend-edge-groups",
		"",
		lambda c, p: c in ("solid.fillet", "solid.chamfer") and len(p.get("edges") or []) > 1,
		blend_edge_groups,
		"SolidCraft refuses some edge combinations Fusion rounds together; most still round alone.",
	),
)


def active(version: str) -> list[Strategy]:
	"""The strategies for a SolidCraft version (all of them when the version is unknown)."""
	return [s for s in STRATEGIES if not version or in_range(version, s.versions)]
