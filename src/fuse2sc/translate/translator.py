"""The translator: an IR document becomes a Plan of SolidCraft commands. Pure: no SolidCraft,
no Fusion, no files.

Bodies are built flat at their components' first placement while the timeline replays; at the
end each component gets its bodies, further placements become copies, and looks are applied.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from .. import geometry as g
from ..model import Body, Document, Sketch, Step
from .assembly import finish, plan_joint, plan_linked, plan_regrouped
from .expressions import needed_model_parameters, ordered, rewrite
from .features import plan_feature
from .plan import FALLBACK, Command, Delta, Note, Params, Plan
from .sketch import plan_sketch

log = logging.getLogger(__name__)
SKIPPED_KINDS = frozenset(
	{
		"ConstructionPlane",
		"ConstructionAxis",
		"ConstructionPoint",
		"Snapshot",
		"Canvas",
		"AttachedCanvas",
		"DecalFeature",
		"JointOrigin",
		"NoneType",
	}
)
JOINT_KINDS = frozenset({"Joint", "AsBuiltJoint", "RigidGroup"})


class Translator:
	def __init__(self, doc: Document) -> None:
		self.doc = doc
		self.plan = Plan(linked=doc.linked_components())
		self.step: int | None = None
		self.prev: dict[str, Body] = {}
		self.sketch_names: dict[str, str] = {}  # sketch uid -> SolidCraft name
		self.feature_names: dict[tuple[str | None, str], str] = {}

	# --- emitting -------------------------------------------------------------------------------

	def emit(self, command: str, params: Params, *, optional: bool = False, **hints: Any) -> Command:
		for v in hints.get("variants") or []:
			v.params = rewrite(v.params)
		c = Command(command, rewrite(params), step=self.step, optional=optional, **hints)
		self.plan.commands.append(c)
		return c

	def note(self, text: str) -> None:
		self.plan.notes.append(Note(self.step, text))

	def fallback(self, step: Step, why: str) -> None:
		"""No SolidCraft equivalent here: the replay brings in Fusion's result for this step."""
		self.emit(FALLBACK, {"bodies": self.changed_bodies(step)}, fallback_kind=step.kind)
		self.plan.steps[step.index] = "fallback"
		self.note(f"{step.name} ({step.kind}): {why}; Fusion's result is brought in without history")

	# --- names ----------------------------------------------------------------------------------

	def sketch_name(self, sk: Sketch) -> str:
		placed = {c.name for c in self.doc.components}
		name = f"{sk.name} ({sk.component})" if sk.component in placed else sk.name
		if name in self.sketch_names.values():
			name = f"{name} #{self.step}"
		self.sketch_names[sk.uid] = name
		return name

	def sketch_ref(self, uid: str) -> str:
		return self.sketch_names.get(uid, uid)

	def feature_name(self, step: Step) -> str:
		"""A SolidCraft feature name for the step, unique even where components reuse names."""
		taken = set(self.feature_names.values())
		name = step.name
		for candidate in (name, f"{name} ({step.component or ''})", f"{name} #{step.index}"):
			if candidate not in taken:
				name = candidate
				break
		self.feature_names[(step.component, step.name)] = name
		return name

	# --- what a step did ------------------------------------------------------------------------

	def after(self, step: Step) -> dict[str, Body]:
		return step.snapshot if step.snapshot is not None else self.prev

	def new_bodies(self, step: Step) -> list[str]:
		return [k for k in self.after(step) if k not in self.prev]

	def changed_bodies(self, step: Step) -> list[str]:
		after = self.after(step)
		return [k for k, b in after.items() if k not in self.prev or self.prev[k] != b]

	def delta(self, step: Step) -> Delta:
		"""The step's change in total volume and its first moment."""

		def dump(bodies: dict[str, Body]) -> dict[str, dict[str, Any]]:
			return {k: b.model_dump() for k, b in bodies.items()}

		d = g.totals(dump(self.after(step))) - g.totals(dump(self.prev))
		return float(d[0]), float(d[1]), float(d[2]), float(d[3])

	def placement(self, component: str | None) -> list[float] | None:
		"""A component's first placement (where its bodies are built), or None for the root."""
		for c in self.doc.components:
			if c.name == component and c.occurrences:
				return c.occurrences[0].matrix
		return None

	def move_bodies(self, bodies: list[str], matrix: list[float], component: str | None) -> None:
		"""Move bodies by a matrix given in `component`'s space (world for the root)."""
		place = self.placement(component)
		world = matrix if place is None else g.flat(g.conjugate(matrix, place))
		self.emit("solid.move", {"bodies": list(bodies), "matrix": world})

	# --- the run --------------------------------------------------------------------------------

	def run(self) -> Plan:
		doc = self.doc
		self.emit("document.units", {"units": doc.units or "mm"})
		user = {p.name for p in doc.parameters}
		model = [p for p in needed_model_parameters(doc) if p.name not in user]
		for p in ordered(model + list(doc.parameters)):
			params = {"name": p.name, "expression": p.expression}
			if p.comment:
				params["comment"] = p.comment
			self.emit("parameters.change", params)
		for step in doc.steps:
			self.step = step.index
			self.plan_step(step)
			self.prev = self.after(step)
		self.step = None
		finish(self, self.prev)
		return self.plan

	def plan_step(self, step: Step) -> None:
		if step.suppressed or step.kind in SKIPPED_KINDS:
			self.plan.steps[step.index] = "skipped"
			return
		if step.error:
			self.note(f"{step.name}: not fully read from Fusion ({step.error})")
		for opt in step.unread:
			self.note(f"{step.name}: {opt} is not carried over")
		self.plan.steps[step.index] = "planned"
		try:
			if step.kind == "Sketch" and step.sketch:
				plan_sketch(self, step.sketch)
			elif step.kind == "Occurrence" and step.occurrence and step.occurrence.linked:
				plan_linked(self, step)
			elif step.kind == "Occurrence":
				plan_regrouped(self, step)
			elif step.kind in JOINT_KINDS:
				plan_joint(self, step)
			else:
				payload = step.payload()
				if payload is None:
					self.fallback(step, f"no SolidCraft equivalent for {step.kind}")
				else:
					plan_feature(payload, self, step)
		except ValidationError as e:
			self.fallback(
				step,
				f"its inputs could not be read ({e.error_count()} problem(s): "
				f"{e.errors()[0]['loc']} {e.errors()[0]['msg']})",
			)
		except Exception as e:
			log.exception("translating step %d (%s)", step.index, step.kind)
			self.fallback(step, f"translation failed ({type(e).__name__}: {e})")


def translate(doc: Document) -> Plan:
	return Translator(doc).run()


__all__ = ["Translator", "translate"]
