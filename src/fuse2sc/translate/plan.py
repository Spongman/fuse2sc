"""What the translator produces: SolidCraft commands with hints for the replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FALLBACK = "fusion.fallback"  # pseudo-command: bring Fusion's own result in for this step

StepPlan = Literal["planned", "fallback", "skipped"]
Params = dict[str, Any]  # a SolidCraft command's parameters, as JSON
Delta = tuple[float, float, float, float]  # a change in total volume and its first moment


@dataclass(slots=True)
class Variant:
	"""Another way to say a command. `refused`: tried when SolidCraft refuses the command;
	`wrong`: tried when it builds but changes the model differently from Fusion."""

	params: dict[str, Any]
	when: Literal["refused", "wrong"]
	why: str = ""


@dataclass(slots=True)
class RegionMatch:
	area: float
	centroid: tuple[float, float]


@dataclass(slots=True)
class Command:
	command: str
	params: dict[str, Any]
	step: int | None = None
	optional: bool = False  # a constraint, dimension or look: may be dropped
	variants: list[Variant] = field(default_factory=list)
	delta: Delta | None = None  # the step's change in volume and moment
	match: list[RegionMatch] | None = None  # profiles to find by area and centroid
	new_bodies: list[str] | None = None  # Fusion's bodies this command creates
	renames: list[str] | None = None  # split pieces to name by centre of mass
	aliases: dict[str, list[str]] | None = None  # Fusion ids of the entities a sketch command creates
	occurrence_move: list[float] | None = None  # relative world move for the occurrence it creates
	occurrence_place: list[float] | None = None  # absolute placement for the occurrence it creates
	final_name: bool = False
	fallback_kind: str | None = None

	@property
	def is_fallback(self) -> bool:
		return self.command == FALLBACK


@dataclass(slots=True)
class Note:
	step: int | None
	text: str


@dataclass
class Plan:
	commands: list[Command] = field(default_factory=list)
	notes: list[Note] = field(default_factory=list)
	steps: dict[int, StepPlan] = field(default_factory=dict)
	linked: set[str] = field(default_factory=set)

	def by_step(self) -> list[tuple[int | None, list[Command]]]:
		"""Commands grouped into runs of the same step, in order."""
		groups: list[tuple[int | None, list[Command]]] = []
		for c in self.commands:
			if groups and groups[-1][0] == c.step:
				groups[-1][1].append(c)
			else:
				groups.append((c.step, [c]))
		return groups
