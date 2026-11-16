"""The replay's building blocks, without SolidCraft."""

import json

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from fuse2sc.model import Body
from fuse2sc.replay.aliases import Aliases
from fuse2sc.replay.checks import Offsets, Tolerances, compare, delta_ok
from fuse2sc.replay.guard import moved, positions
from fuse2sc.replay.naming import match_bodies, plan_renames
from fuse2sc.replay.regions import match_profiles
from fuse2sc.replay.sources import Sources, StepsFolder, StepUnavailable
from fuse2sc.solidcraft import in_range
from fuse2sc.translate import RegionMatch


def sc_body(v, c, box=None):
	d = {"volume_mm3": v, "center_of_mass": c}
	if box:
		d["bbox"] = {"min": box[0], "max": box[1]}
	return d


# --- renames -------------------------------------------------------------------------------------


def apply(renames, names):
	names = set(names)
	for a, b in renames:
		assert a in names and b not in names, (a, b, names)
		names.remove(a)
		names.add(b)
	return names


def test_swap_goes_through_a_temporary_name():
	r = plan_renames([("A", "B"), ("B", "A")], {"A", "B"})
	assert apply(r, {"A", "B"}) == {"A", "B"} and any(n.startswith("__fuse2sc_tmp") for _, n in r)


def test_infeasible_renames_are_dropped():
	assert plan_renames([("A", "C")], {"A", "C"}) == []
	assert plan_renames([("A", "B"), ("B", "C")], {"A", "B", "C"}) == []


names = st.sampled_from([f"b{i}" for i in range(8)])


@given(st.lists(st.tuples(names, names), max_size=8, unique_by=lambda p: p[0]), st.sets(names, min_size=1))
def test_renames_never_collide_and_land_where_asked(wanted, existing):
	wanted = [(a, b) for a, b in wanted if a in existing]
	if len({b for _, b in wanted}) != len(wanted):
		return  # two bodies can't both take one name
	final = apply(plan_renames(wanted, existing), existing)
	assert not any(n.startswith("__fuse2sc_tmp") for n in final)
	assert len(final) == len(existing)


def test_match_bodies_best_first():
	exp = {"k1": Body(volume=1000, com=(0, 0, 0)), "k2": Body(volume=1000, com=(10, 0, 0))}
	have = {"x": sc_body(1000.5, [10.01, 0, 0]), "y": sc_body(999.9, [0, 0, 0.01]), "z": sc_body(5, [0, 0, 0])}
	assert sorted(match_bodies(exp, have)) == [("k1", "y"), ("k2", "x")]


# --- checks --------------------------------------------------------------------------------------


def test_compare_volume_centre_box_and_extra():
	tol, off = Tolerances(), Offsets()
	want = {"c/A": Body(volume=1000.0, com=(0, 0, 5), bbox=((-5, -5, 0), (5, 5, 10)))}
	assert compare(want, {"c/A": sc_body(1000.4, [0, 0, 5.001], [[-5, -5, 0], [5, 5, 10]])}, tol, off).ok
	moved_box = compare(want, {"c/A": sc_body(1000.0, [0, 0, 5], [[-5, -5, 3], [5, 5, 13]])}, tol, off)
	assert moved_box.wrong == ["c/A"] and "bounding box" in moved_box.problems[0]
	assert compare(want, {"c/A": sc_body(1100.0, [0, 0, 5])}, tol, off).wrong == ["c/A"]
	off.by_body["c/A"] = (100.0, np.zeros(3))  # SolidCraft measures this imported body 100 mm3 more
	assert compare(want, {"c/A": sc_body(1100.0, [0, 0, 5])}, tol, off).ok
	extra = compare(want, {"c/A": sc_body(1000, [0, 0, 5]), "c/Z": sc_body(1, [0, 0, 0])}, tol, Offsets())
	assert extra.extra == ["c/Z"] and not extra.ok
	assert compare(want, {}, tol, Offsets()).wrong == ["c/A"]


def test_delta():
	tol = Tolerances()
	assert delta_ok(np.array([100, 0, 0, 0.0]), np.array([200, 50, 0, 0.0]), (100, 50, 0, 0), tol)
	assert not delta_ok(np.array([100, 0, 0, 0.0]), np.array([150, 0, 0, 0.0]), (100, 0, 0, 0), tol)


# --- regions -------------------------------------------------------------------------------------


def test_regions_match_by_area_and_centroid():
	have = [
		{"index": 0, "area_mm2": 100.0, "centroid_sketch": [0, 0]},
		{"index": 1, "area_mm2": 50.0, "centroid_sketch": [20, 0]},
	]
	assert match_profiles(have, [RegionMatch(50.0, (20, 0))]) == ([1], None)
	assert match_profiles(have, [RegionMatch(50.1, (20.02, 0))]) == ([1], None)
	picks, why = match_profiles(have, [RegionMatch(70.0, (5, 5))])
	assert picks is None and "no SolidCraft region" in why


def test_regions_refuse_ambiguity_and_duplicates():
	twins = [
		{"index": 0, "area_mm2": 50.0, "centroid_sketch": [0, 0.01]},
		{"index": 1, "area_mm2": 50.0, "centroid_sketch": [0, -0.01]},
	]
	picks, why = match_profiles(twins, [RegionMatch(50.0, (0, 0))])
	assert picks is None and "several" in why
	one = [{"index": 0, "area_mm2": 50.0, "centroid_sketch": [0, 0]}]
	assert match_profiles(one, [RegionMatch(50.0, (0, 0))] * 2)[0] is None


# --- aliases and the guard -----------------------------------------------------------------------


def test_aliases_rewrite_sketch_and_feature_references():
	a = Aliases()
	a.record("S", {"curves": ["C6"], "points": ["P1", "P2"]}, {"created": {"curves": ["s1"], "points": ["p1", "p2"]}})
	assert a.in_sketch("S", {"a": "C6", "b": "C2", "entities": ["P1", "C6.start"]}) == {
		"a": "s1",
		"b": "C2",
		"entities": ["p1", "s1.start"],
	}
	assert a.in_feature({"sketch": "S", "profiles": [{"curves": ["C0", "C6"]}]})["profiles"] == [
		{"curves": ["C0", "s1"]}
	]
	assert a.in_feature({"sketch": "T", "profiles": [{"curves": ["C6"]}]})["profiles"] == [{"curves": ["C6"]}]
	sec = a.in_feature({"sections": [{"sketch": "S", "profiles": [{"curves": ["C6"]}]}]})["sections"]
	assert sec[0]["profiles"] == [{"curves": ["s1"]}]


def test_guard_sees_moved_points_and_radii():
	before = positions(
		{"points": [{"id": "a", "at": [0, 0]}, {"id": "b", "at": [10, 0]}], "curves": [{"id": "c", "radius": 2.0}]}
	)
	same = positions(
		{
			"points": [{"id": "a", "at": [0, 0]}, {"id": "b", "at": [10, 0.00001]}, {"id": "n", "at": [5, 5]}],
			"curves": [{"id": "c", "radius": 2.0}],
		}
	)
	assert moved(before, same) == []
	bad = positions(
		{"points": [{"id": "a", "at": [0, 0]}, {"id": "b", "at": [10, 3]}], "curves": [{"id": "c", "radius": 2.5}]}
	)
	assert sorted(moved(before, bad)) == ["b", "c"]


# --- sources and versions ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_steps_folder_and_needed_steps(tmp_path):
	steps = tmp_path / "steps"
	steps.mkdir()
	(steps / "s3.step").write_text("x")
	(steps / "index.json").write_text(
		json.dumps({"3": [{"component": "c", "path": "s3.step", "matrix": [1] + [0] * 15}]})
	)
	folder = StepsFolder(steps)
	assert (await folder.export(3, ["c"]))[0].path == steps / "s3.step"
	with pytest.raises(StepUnavailable):
		await folder.export(4, ["c"])
	needed = tmp_path / "needed-steps.json"
	sources = Sources([folder], needed)
	with pytest.raises(StepUnavailable):
		await sources.export(7, ["c", "d"])
	sources.write_needed()
	assert json.loads(needed.read_text())["steps"] == [{"index": 7, "components": ["c", "d"]}]
	Sources([folder], needed).write_needed()  # nothing missing: the file goes
	assert not needed.exists()


@pytest.mark.parametrize(
	("version", "spec", "ok"),
	[
		("0.42.0", "", True),
		("0.42.0", ">=0.30,<0.43", True),
		("0.43.1", ">=0.30,<0.43", False),
		("0.42.0-nightly.20261115.134.1", "==0.42.0", True),
	],
)
def test_version_ranges(version, spec, ok):
	assert in_range(version, spec) is ok
