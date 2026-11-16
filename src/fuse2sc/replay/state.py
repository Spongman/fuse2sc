"""What the parts of a build share: the plan, options, per-step records and a journal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import Body, Document
from ..solidcraft import Remote
from ..translate import Plan
from .aliases import Aliases
from .checks import Offsets, Tolerances
from .results import StepResult

EventSink = Callable[[str, dict[str, Any]], None]


def no_events(kind: str, data: dict[str, Any]) -> None:
	pass


@dataclass
class Options:
	check_every_step: bool = True
	guard_constraints: bool = True
	tolerances: Tolerances = field(default_factory=Tolerances)
	command_timeout: float = 300.0
	until_step: int | None = None
	skip_steps: frozenset[int] = frozenset()
	stop_at_divergence: bool = False
	linked_dir: Path | None = None
	allow: list[Path] = field(default_factory=list)
	write_script: bool = True
	remote: Remote | None = None  # build in a SolidCraft app on another computer


@dataclass
class Journal:
	"""What the report lists besides step statuses."""

	failed: list[str] = field(default_factory=list)  # commands SolidCraft refused
	dropped: list[str] = field(default_factory=list)  # optional commands left out
	checks: list[str] = field(default_factory=list)  # observations from checks and retries


@dataclass
class BuildState:
	doc: Document
	plan: Plan
	options: Options = field(default_factory=Options)
	events: EventSink = no_events
	journal: Journal = field(default_factory=Journal)
	offsets: Offsets = field(default_factory=Offsets)
	aliases: Aliases = field(default_factory=Aliases)
	records: dict[int, StepResult] = field(default_factory=dict)
	sketch: str | None = None  # the sketch being drawn
	sketch_ids: dict[str, int] = field(default_factory=dict)  # sketch name -> SolidCraft id
	final_names: dict[str, str] = field(default_factory=dict)  # "comp/body" -> its name at the end

	def __post_init__(self) -> None:
		self._steps = {s.index: s for s in self.doc.steps}
		for s in self.doc.steps:
			self.records.setdefault(s.index, StepResult(index=s.index, name=s.name, kind=s.kind))
			if self.plan.steps.get(s.index) == "skipped":
				self.records[s.index].status = "skipped"

	@property
	def tol(self) -> Tolerances:
		return self.options.tolerances

	def snapshot(self, index: int | None) -> dict[str, Body]:
		"""Fusion's bodies just after a step (empty when unknown)."""
		step = self._steps.get(index) if index is not None else None
		return dict(step.snapshot or {}) if step else {}
