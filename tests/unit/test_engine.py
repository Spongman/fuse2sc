"""The build's control flow, against an in-memory SolidCraft (fake_port.py)."""

from __future__ import annotations

import pytest

from fake_port import FakeSolidCraft, body, creates, refuses
from fuse2sc import model
from fuse2sc.ir import schema
from fuse2sc.replay import BuildState, Options, Sources, categorize, replay
from fuse2sc.replay.sources import ExportedStep, StepUnavailable
from fuse2sc.solidcraft import Reply
from fuse2sc.translate import Command, Plan, Variant

pytestmark = pytest.mark.anyio
B = "root/Body1"


def doc(*snapshots: dict) -> model.Document:
	"""A design whose steps have these Fusion snapshots ({key: (volume, com)})."""
	raw = schema.new_document("D", "test")
	for i, snap in enumerate(snapshots):
		raw["steps"].append(
			{
				"index": i,
				"name": f"Step{i}",
				"kind": "ExtrudeFeature",
				"snapshot": {k: {"volume": v, "com": list(c)} for k, (v, c) in snap.items()},
			}
		)
	return model.parse(raw)


def state(d: model.Document, *commands: Command, **options) -> BuildState:
	plan = Plan(commands=list(commands), steps=dict.fromkeys((s.index for s in d.steps), "planned"))
	return BuildState(d, plan, Options(**options))


async def run(fake: FakeSolidCraft, st: BuildState, sources: Sources | None = None, strategies=None):
	problems = await replay(fake, st, sources or Sources(), strategies)
	return problems, {r.name: r.status for r in st.records.values()}


async def test_a_step_that_builds_as_fusion_has_it_is_exact():
	d = doc({B: (1000, (0, 0, 5))})
	fake = FakeSolidCraft().on("solid.extrude", creates("Body1", 1000, (0, 0, 5)))
	st = state(d, Command("solid.extrude", {}, step=0, new_bodies=[B]))
	problems, statuses = await run(fake, st)
	assert statuses == {"Step0": "exact"} and not problems
	assert set(fake.model) == {B}  # named after Fusion's body
	assert categorize(True, d, problems, list(st.records.values())) == "full"


async def test_a_refused_command_tries_its_refused_variants():
	d = doc({B: (1000, (0, 0, 5))})
	fake = FakeSolidCraft().on(
		"solid.extrude",
		lambda p, f: (
			creates("Body1", 1000, (0, 0, 5))(p, f)
			if p.get("profiles") == "by points"
			else Reply(False, "no profile matches")
		),
	)
	variant = Variant({"profiles": "by points"}, "refused", "profiles picked by points")
	st = state(d, Command("solid.extrude", {"profiles": "by curves"}, step=0, new_bodies=[B], variants=[variant]))
	_, statuses = await run(fake, st)
	assert statuses == {"Step0": "adjusted"}
	assert "profiles picked by points" in st.records[0].details[0]


async def test_a_command_that_builds_the_wrong_way_is_flipped():
	d = doc({B: (1000, (0, 0, -5))})
	fake = FakeSolidCraft().on("solid.revolve", lambda p, f: creates("Body1", 1000, (0, 0, 5 * p.get("sign", 1)))(p, f))
	flipped = Variant({"sign": -1}, "wrong", "the axis the other way")
	st = state(
		d, Command("solid.revolve", {"sign": 1}, step=0, new_bodies=[B], variants=[flipped], delta=(1000, 0, 0, -5000))
	)
	_, statuses = await run(fake, st)
	assert statuses == {"Step0": "adjusted"} and fake.undone == 1
	assert fake.model[B]["center_of_mass"] == [0, 0, -5]


async def test_retry_strategies_run_when_every_variant_is_refused():
	d = doc({B: (900, (0, 0, 0))})
	fake = FakeSolidCraft().on(
		"solid.fillet",
		lambda p, f: (
			Reply(False, "no edge there")
			if [9, 9, 9] in p["edges"]
			else (f.model.__setitem__(B, body(900)) or Reply(True, {}))
		),
	)
	fake.model[B] = body(1000)
	st = state(d, Command("solid.fillet", {"edges": [[0, 0, 0], [1, 0, 0], [9, 9, 9]]}, step=0))
	problems, statuses = await run(fake, st)
	assert statuses == {"Step0": "adjusted"} and not problems
	assert "2 edge(s) rounded, 1 refused" in st.records[0].details[0]


async def test_refused_optional_commands_are_dropped_not_failed():
	d = doc({})
	fake = FakeSolidCraft().on("sketch.dimension", refuses()).on("appearance.assign", refuses())
	st = state(
		d,
		Command("sketch.dimension", {}, step=0, optional=True),
		Command("appearance.assign", {}, step=0, optional=True),
	)
	_, statuses = await run(fake, st)
	assert statuses == {"Step0": "adjusted"} and len(st.journal.dropped) == 2 and not st.journal.failed
	assert st.records[0].details == ["sketch.dimension refused by SolidCraft"]  # looks don't mark the step


async def test_a_constraint_that_moves_the_sketch_is_taken_back():
	d = doc({})
	sketch = {"points": [{"id": "a", "at": [0, 0]}, {"id": "b", "at": [10, 0]}]}
	fake = FakeSolidCraft().on("sketch.inspect", lambda p, f: Reply(True, sketch))

	def equal(params, fake):
		sketch["points"][1]["at"] = [10, 5]
		return Reply(True, {})

	fake.on("sketch.constraint.equal", equal)
	st = state(
		d,
		Command("sketch.create", {"name": "S"}, step=0),
		Command("sketch.constraint.equal", {"a": "l1", "b": "l2"}, step=0, optional=True),
	)
	await run(
		fake,
		st,
	)
	assert fake.undone == 1 and "moved b" in st.journal.dropped[0]


async def test_without_fusions_result_nothing_is_removed():
	"""Regression: bodies used to be removed before knowing Fusion's result could be had."""
	d = doc({B: (2000, (0, 0, 0))})
	fake = FakeSolidCraft().on("solid.extrude", creates("Body1", 1000))
	st = state(d, Command("solid.extrude", {}, step=0, new_bodies=[B]))
	problems, statuses = await run(fake, st, Sources())
	assert statuses == {"Step0": "mismatch"} and problems
	assert set(fake.model) == {B} and not fake.commands("solid.remove")
	assert "not available" in st.records[0].details[0]


class OneFile:
	async def export(self, index, components):
		if index != 0:
			raise StepUnavailable("only step 0")
		return [ExportedStep("root", __import__("pathlib").Path("step0.step"))]


async def test_fusions_result_replaces_a_body_that_differs():
	d = doc({B: (2000, (0, 0, 0))})
	fake = FakeSolidCraft().on("solid.extrude", creates("Body1", 1000)).on("file.insert_step", creates("Body1", 2000))
	st = state(d, Command("solid.extrude", {}, step=0, new_bodies=[B]))
	problems, statuses = await run(fake, st, Sources([OneFile()]))
	assert statuses == {"Step0": "substituted"} and not problems
	assert fake.model == {B: body(2000)}
	assert categorize(True, d, problems, list(st.records.values())) == "partial"


async def test_a_divergence_is_reported_where_it_starts():
	d = doc({B: (2000, (0, 0, 0))}, {B: (2000, (0, 0, 0))})
	fake = FakeSolidCraft().on("solid.extrude", creates("Body1", 1000))
	st = state(d, Command("solid.extrude", {}, step=0, new_bodies=[B]), Command("sketch.create", {"name": "S"}, step=1))
	_, statuses = await run(fake, st)
	assert statuses == {"Step0": "mismatch", "Step1": "exact"}
	assert "still differs since step 0" in st.records[1].details[0]


async def test_stop_at_divergence_and_until_step():
	d = doc({B: (2000, (0, 0, 0))}, {B: (2000, (0, 0, 0))})
	fake = FakeSolidCraft().on("solid.extrude", creates("Body1", 1000))
	commands = (Command("solid.extrude", {}, step=0, new_bodies=[B]), Command("solid.extrude", {"again": 1}, step=1))
	st = state(d, *commands, stop_at_divergence=True)
	problems, _ = await run(fake, st)
	assert len(fake.commands("solid.extrude")) == 1 and problems == []
	st = state(d, *commands, until_step=0)
	await run(FakeSolidCraft().on("solid.extrude", creates("Body1", 2000)), st)
	assert st.records[1].commands == 0


def test_categories():
	d = doc({B: (1, (0, 0, 0))})
	assert categorize(False, d, [], []) == "failed"
	assert categorize(True, doc({}), [], []) == "empty"
	assert categorize(True, d, ["differs"], []) == "mismatch"
