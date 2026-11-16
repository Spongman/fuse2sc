"""The translator, without SolidCraft."""

import pytest

from fuse2sc import model
from fuse2sc.translate import FALLBACK, translate
from irkit import BODY, COMP, XY, Doc, lidded_box


def commands(plan, name):
	return [c for c in plan.commands if c.command == name]


def one_step(kind, payload, snapshot=None, **extra):
	d = Doc("T")
	d.raw["steps"].append(
		{
			"index": 0,
			"name": "S",
			"kind": kind,
			"component": COMP,
			"feature": payload,
			"snapshot": snapshot or {},
			**extra,
		}
	)
	return d.doc


def test_lidded_box_plans_every_step():
	plan = translate(lidded_box().doc)
	assert set(plan.steps.values()) == {"planned"} and not plan.notes
	assert len(commands(plan, "solid.extrude")) == 4 and len(commands(plan, "solid.shell")) == 1
	names = [c.params["name"] for c in commands(plan, "parameters.change")]
	assert names == ["width", "depth", "height", "wall", "hole_d", "lid_gap"]
	cut = commands(plan, "solid.extrude")[1]
	assert cut.params["operation"] == "cut" and cut.params["start_offset"] == "depth / 2 - wall"
	assert cut.params["targets"] == [BODY]
	assert cut.match and cut.match[0].area == pytest.approx(16 * 3.141592653589793)
	shell = commands(plan, "solid.shell")[0]
	assert shell.params == {
		"thickness": "wall",
		"direction": "inside",
		"faces": [[0, 0, 30]],
		"tangent_chain": False,
		"body": BODY,
	}


def test_unknown_and_suppressed_steps():
	d = Doc("T")
	d.step(name="Thread1", kind="ThreadFeature", feature={"created": []}, snapshot={BODY: {"volume": 1}})
	d.step(name="Fillet9", kind="FilletFeature", suppressed=True)
	plan = translate(d.doc)
	assert plan.steps == {0: "fallback", 1: "skipped"}
	fb = commands(plan, FALLBACK)[0]
	assert fb.params["bodies"] == [BODY] and fb.fallback_kind == "ThreadFeature"


def test_malformed_payload_falls_back():
	plan = translate(one_step("ExtrudeFeature", {"op": "new", "extent": {"type": "nonsense"}}))
	assert plan.steps[0] == "fallback" and "could not be read" in plan.notes[-1].text


def test_unread_options_are_noted():
	raw = lidded_box().raw
	raw["steps"][1]["unread"] = ["taper on side two"]
	assert any("taper on side two" in n.text for n in translate(model.parse(raw)).notes)


def test_blends():
	edges = [{"at": [0, 0, 0], "body": BODY}]
	chord = {"sets": [{"type": "chord", "size": {"chord": "3 mm"}, "edges": edges}]}
	c = commands(translate(one_step("FilletFeature", chord)), "solid.fillet")[0]
	assert c.params["radius"] == "3 mm" and c.params["type"] == "chord" and c.params["body"] == BODY
	angled = {"sets": [{"type": "distance_angle", "size": {"distance": "1 mm", "angle": "30 deg"}, "edges": edges}]}
	c = commands(translate(one_step("ChamferFeature", angled)), "solid.chamfer")[0]
	assert c.variants and c.variants[0].params["flip"] is True and c.variants[0].when == "wrong"
	unknown = {"sets": [{"type": "other", "fusion_type": "SetbackFilletEdgeSet", "edges": edges}]}
	assert translate(one_step("FilletFeature", unknown)).steps[0] == "fallback"


def test_two_sided_extrude_with_both_ends_below():
	d = Doc("T")
	region = d.rect("S", 0, 0, 10, 10)
	d.feature(
		"E",
		"ExtrudeFeature",
		{
			"op": "new",
			"regions": [region],
			"extent": {
				"type": "two",
				"side1": {"type": "distance", "expr": "-2 mm", "mm": -2},
				"side2": {"type": "distance", "expr": "5 mm", "mm": 5},
			},
		},
	)
	p = commands(translate(d.doc), "solid.extrude")[0].params
	assert p["start_offset"] == "-(5 mm)" and p["distance"] == "(-2 mm) + (5 mm)" and "distance2" not in p


def test_sketch_curves_constraints_and_aliases():
	d = Doc("T")
	pts = [
		{"id": "P0", "xy": [0, 0], "fixed": True},
		{"id": "P1", "xy": [10, 0]},
		{"id": "P2", "xy": [5, 5]},
		{"id": "P3", "xy": [20, 0]},
	]
	curves = [
		{"id": "C0", "type": "spline_fit", "through": ["P0", "P2", "P1"]},
		{"id": "C1", "type": "ellipse", "center": "P3", "major": [24, 0], "minor_radius": 2},
		{"id": "C2", "type": "line", "ends": ["P1", "P0"]},
		{"id": "C3", "type": "other", "fusion_type": "SketchConicCurve", "polyline": [[0, 0], [1, 1], [2, 0]]},
	]
	cons = [
		{"type": "Horizontal", "refs": {"line": "C2"}},
		{"type": "Offset", "refs": {}},
		{"type": "Coincident", "refs": {"point": "P1", "entity": "C2"}},
	]
	d.sketch(
		"S",
		XY,
		pts,
		curves,
		cons,
		[
			{
				"type": "Linear",
				"refs": {"entityOne": "P1", "entityTwo": "P0"},
				"expr": "w",
				"driving": True,
				"orientation": "horizontal",
			}
		],
	)
	plan = translate(d.doc)
	sp = commands(plan, "sketch.spline.fit_point")[0]
	assert sp.params["points"] == [[0, 0], [5, 5], [10, 0]] and sp.aliases == {
		"curves": ["C0"],
		"points": ["P0", "P2", "P1"],
	}
	assert commands(plan, "sketch.ellipse")[0].aliases == {"curves": ["C1"]}
	assert commands(plan, "sketch.constraint.horizontal_vertical")[0].params == {"mode": "horizontal", "line": "C2"}
	dim = commands(plan, "sketch.dimension")[0]
	assert dim.params == {"entities": ["P1", "origin"], "type": "horizontal", "value": "w"} and dim.optional
	texts = " ".join(n.text for n in plan.notes)
	assert "Offset ×1" in texts and "2 straight pieces" in texts


def test_move_inside_a_placed_component_is_conjugated():
	d = Doc("T")
	d.raw["components"] = [
		{
			"name": "Part",
			"occurrences": [{"path": "Part:1", "matrix": [1, 0, 0, 100, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}],
		}
	]
	d.step(
		name="Turn",
		kind="MoveFeature",
		component="Part",
		feature={"bodies": ["Part/B"], "matrix": [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
	)
	m = commands(translate(d.doc), "solid.move")[0].params["matrix"]
	# a quarter turn about the component's own origin, which sits at x=100 in the world
	assert m[:4] == [0, -1, 0, 100] and m[4:8] == [1, 0, 0, -100]


def test_joint_moves_its_component():
	d = Doc("T")
	eye = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
	moved = [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
	d.raw["components"] = [{"name": "Arm", "occurrences": [{"path": "Arm:1", "matrix": moved}]}]
	d.bodies = {"Arm/B": {"volume": 1, "com": [0, 0, 0]}}
	d.step(name="Body", kind="ExtrudeFeature", feature=None)
	d.step(
		name="Joint1",
		kind="Joint",
		placements={"before": {"Arm:1": eye}, "after": {"Arm:1": moved}},
		snapshot={"Arm/B": {"volume": 1, "com": [5, 0, 0]}},
	)
	move = commands(translate(d.doc), "solid.move")[0]
	assert move.params["bodies"] == ["Arm/B"] and move.params["matrix"][3] == 5


def test_linked_design_keeps_its_rotation():
	m = [0, -1, 0, 10.0, 1, 0, 0, 20.0, 0, 0, 1, 0.0, 0, 0, 0, 1]
	d = Doc("T")
	d.raw["components"] = [{"name": "Part", "linked": "Part v1", "occurrences": [{"path": "Part:1", "matrix": m}]}]
	d.step(name="L", kind="Occurrence", occurrence={"component": "Part", "linked": "Part v1", "matrix": m})
	plan = translate(d.doc)
	c = commands(plan, "component.insert_design")[0]
	assert c.params["translate"] == [10.0, 20.0, 0.0] and c.occurrence_place == m
	assert "Part" in plan.linked and not commands(plan, "component.create")


def test_hole_draft_and_rectangular_pattern_direction():
	hole = {
		"holes": [{"at": [1, 2, 3]}, {"at": [4, 5, 6]}],
		"diameter": "6 mm",
		"style": "counterbore",
		"cb_diameter": "10 mm",
		"cb_depth": "3 mm",
		"depth": "12 mm",
	}
	holes = commands(translate(one_step("HoleFeature", hole)), "solid.hole")
	assert len(holes) == 2 and holes[1].params["position"] == [4, 5, 6] and holes[0].params["type"] == "counterbore"
	draft = {
		"faces": [{"at": [0, 0, 5], "body": BODY}],
		"angle": "3 deg",
		"neutral": {"origin": [0, 0, 0], "normal": [0, 0, 1]},
	}
	dr = commands(translate(one_step("DraftFeature", draft)), "solid.draft")[0]
	assert dr.variants[0].params["angle"] == "-(3 deg)"
	d = Doc("T")
	d.bodies = {"root/A": {"volume": 1, "com": [0, 0, 0]}}
	d.step(name="Body", kind="ExtrudeFeature", feature=None)
	d.step(
		name="Pattern",
		kind="RectangularPatternFeature",
		feature={"inputs": {"bodies": ["root/A"]}, "dir1": [1, 0, 0], "count1": "3", "spacing1": "10 mm"},
		snapshot={
			"root/A": {"volume": 1, "com": [0, 0, 0]},
			"root/A (2)": {"volume": 1, "com": [-10, 0, 0]},
			"root/A (3)": {"volume": 1, "com": [-20, 0, 0]},
		},
	)
	p = commands(translate(d.doc), "solid.pattern.rectangular")[0].params
	assert p["dir1"] == [-1.0, 0.0, 0.0] and p["count1"] == 3  # from where Fusion put the copies


def test_appearance_and_components_at_the_end():
	raw = lidded_box().raw
	raw["appearance"] = {
		BODY: {"look": {"name": "Blue", "rgb": "#2040c0"}, "material": {"name": "Plywood", "density": 0.55}},
		"root/Lid": {"look": {"name": "Steel", "rgb": "#999999", "metal": True}},
	}
	plan = translate(model.parse(raw))
	assert commands(plan, "material.create")[0].params == {"name": "Plywood", "density_g_cm3": 0.55}
	looks = commands(plan, "appearance.assign")
	assert {tuple(c.params["bodies"]) for c in looks} == {(BODY,), ("root/Lid",)}
	assert next(c for c in looks if c.params["bodies"] == ["root/Lid"]).params["pbr"]["metallic"] == 1.0
	renames = [c for c in commands(plan, "body.rename") if c.final_name]
	assert {c.params["name"] for c in renames} == {"Body1", "Lid"}


def test_translation_bug_costs_one_step(monkeypatch):
	from fuse2sc.translate import features

	def broken(picks):
		raise RuntimeError("boom")

	monkeypatch.setattr(features, "by_body", broken)  # the shell handler groups its faces with this
	plan = translate(lidded_box().doc)
	assert plan.steps[2] == "fallback" and plan.steps[4] == "planned"
	assert any("translation failed (RuntimeError: boom)" in n.text for n in plan.notes)
