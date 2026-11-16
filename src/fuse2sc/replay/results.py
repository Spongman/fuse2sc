"""What a build reports: a status per Fusion step and a category for the design."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel

StepStatus = Literal["exact", "adjusted", "mismatch", "substituted", "failed", "skipped"]
Category = Literal["full", "partial", "mismatch", "geometry", "empty", "failed"]
SEVERITY: dict[str, int] = {"exact": 0, "adjusted": 1, "mismatch": 2, "substituted": 3, "failed": 4}


class StepResult(BaseModel):
	index: int
	name: str
	kind: str | None = None
	status: StepStatus = "exact"
	details: list[str] = []
	commands: int = 0
	failed_commands: int = 0

	def worse(self, status: StepStatus, why: str | None = None) -> None:
		if self.status != "skipped" and SEVERITY[status] > SEVERITY[self.status]:
			self.status = status
		if why:
			self.details.append(why)

	def adjust(self, why: str) -> None:
		self.worse("adjusted", why)


class NoteOut(BaseModel):
	step: int | None
	note: str


class Verification(BaseModel):
	ok: bool | None
	reference: str | None = None
	volume_mm3: float | None = None
	fusion_volume_visible: float | None = None
	fusion_volume_all: float | None = None
	volume_rel_err: float | None = None
	bodies: int | None = None
	fusion_bodies: int | None = None
	bbox_max_diff_mm: float | None = None
	parameters_missing: list[str] = []
	parameters_changed: list[str] = []
	timeline_errors: list[str] = []
	notes: list[str] = []


class BuildResult(BaseModel):
	document: str
	out: str | None = None
	saved: bool = False
	category: Category = "failed"
	steps: list[StepResult] = []
	notes: list[NoteOut] = []
	failed: list[str] = []
	dropped: list[str] = []
	log: list[str] = []
	final_problems: list[str] = []
	needed_steps: dict[int, list[str]] = {}
	solidcraft_version: str = ""
	seconds: float = 0.0
	commands: int = 0
	verify: Verification | None = None
	tool_version: str = ""
	timeline: str | None = None
	timeline_mtime: float | None = None

	@property
	def counts(self) -> dict[str, int]:
		return dict(Counter(s.status for s in self.steps))

	def first_blocker(self) -> StepResult | None:
		return next((s for s in self.steps if s.status in ("substituted", "failed", "mismatch")), None)
