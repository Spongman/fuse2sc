"""Golden tests: real builds in SolidCraft, checked against geometry known exactly.

They need solidcraft-cli (SOLIDCRAFT_CLI or PATH) and are skipped without it.
"""

import functools
import json
import math
import os
import subprocess
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner

from fuse2sc.cli import app
from fuse2sc.ir import schema
from fuse2sc.replay import Options, Sources, StepsFolder, build
from fuse2sc.solidcraft import open_session, runner
from fuse2sc.verify import verify
from irkit import BODY, COMP, XY, XZ, Doc, Solid, lidded_box

pytestmark = pytest.mark.live


def run(d: Doc, tmp_path: Path, cli: Path, version: str, sources=None, **options):
	out = tmp_path / f"{d.raw['document'].replace(' ', '_')}.sc"
	result = anyio.run(
		functools.partial(build, d.doc, out, cli, sources=sources, options=Options(**options), sc_version=version)
	)
	return result, out


def statuses(result):
	return {s.name: s.status for s in result.steps}


def plate(name="Plate", w=60.0, d=40.0, t=5.0) -> tuple[Doc, Solid]:
	doc = Doc(name)
	region = doc.rect("Base", -w / 2, -d / 2, w / 2, d / 2)
	solid = Solid().add_box((-w / 2, -d / 2, 0), (w / 2, d / 2, t))
	doc.extrude("Extrude1", region, f"{t:g} mm", bodies={BODY: solid.body(((-w / 2, -d / 2, 0), (w / 2, d / 2, t)))})
	return doc, solid


# --- a whole design ------------------------------------------------------------------------------


def test_lidded_box(tmp_path, cli, sc_version):
	d = lidded_box()
	result, out = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)
	assert set(statuses(result).values()) == {"exact"}
	assert out.with_suffix(".script.json").is_file()
	schema.dump(d.raw, str(tmp_path / "timeline.json"))
	v = verify(tmp_path, out, cli)
	assert v.ok, v
	assert not v.parameters_missing and not v.parameters_changed


# --- features with exact results -----------------------------------------------------------------


def rounded_corners(r=5.0, extra_edges=()):
	d, s = plate()
	v = s.volume - 4 * (r * r - math.pi * r * r / 4) * 5
	edges = [{"at": [x, y, 2.5], "body": BODY} for x in (-30.0, 30.0) for y in (-20.0, 20.0)] + list(extra_edges)
	d.feature(
		"Fillet1",
		"FilletFeature",
		{"sets": [{"type": "constant", "size": {"radius": f"{r:g} mm"}, "edges": edges}]},
		bodies={BODY: {"volume": v, "com": [0, 0, 2.5], "bbox": [[-30, -20, 0], [30, 20, 5]]}},
	)
	return d


def test_rounded_plate(tmp_path, cli, sc_version):
	result, _ = run(rounded_corners(), tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_chamfered_block(tmp_path, cli, sc_version):
	d = Doc("Block")
	region = d.rect("Base", -20, -10, 20, 10)
	block = Solid().add_box((-20, -10, 0), (20, 10, 10))
	d.extrude("Extrude1", region, "10 mm", bodies={BODY: block.body(((-20, -10, 0), (20, 10, 10)))})
	prism = 0.5 * 2 * 2 * 40  # each chamfered top edge removes a right-angled prism
	block.parts += [(-prism, (0, -10 + 2 / 3, 10 - 2 / 3)), (-prism, (0, 10 - 2 / 3, 10 - 2 / 3))]
	d.feature(
		"Chamfer1",
		"ChamferFeature",
		{
			"sets": [
				{
					"type": "equal",
					"size": {"distance": "2 mm"},
					"edges": [{"at": [0, -10, 10], "body": BODY}, {"at": [0, 10, 10], "body": BODY}],
				}
			]
		},
		bodies={BODY: block.body(((-20, -10, 0), (20, 10, 10)))},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_revolved_ring_on_xz(tmp_path, cli, sc_version):
	d = Doc("Ring")
	region = d.rect("Section", 10, 0, 20, 5, frame=XZ)
	v = math.pi * (20**2 - 10**2) * 5
	d.feature(
		"Revolve1",
		"RevolveFeature",
		{
			"op": "new",
			"regions": [region],
			"axis": {"origin": [0, 0, 0], "dir": [0, 0, 1]},
			"angle": {"type": "one", "expr": "360 deg", "degrees": 360},
		},
		bodies={BODY: {"volume": v, "com": [0, 0, 2.5], "bbox": [[-20, -20, 0], [20, 20, 5]]}},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_extrude_follows_the_sketch_normal_on_xz(tmp_path, cli, sc_version):
	d = Doc("Wall")  # an XZ sketch's normal is x × y = -Y
	region = d.rect("Base", 0, 0, 10, 20, frame=XZ)
	d.extrude(
		"Extrude1",
		region,
		"5 mm",
		bodies={BODY: {"volume": 1000, "com": [5, -2.5, 10], "bbox": [[0, -5, 0], [10, 0, 20]]}},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_two_sided_extrude(tmp_path, cli, sc_version):
	d = Doc("TwoSided")
	region = d.rect("Base", -10, -5, 10, 5)
	d.extrude(
		"Extrude1",
		region,
		"5 mm",
		two="3 mm",
		bodies={BODY: {"volume": 1600, "com": [0, 0, 1], "bbox": [[-10, -5, -3], [10, 5, 5]]}},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_hole_through_a_plate(tmp_path, cli, sc_version):
	d, s = plate("Holed", 40, 40, 10)
	s.add_cylinder((0, 0), 4, 0, 10, sign=-1)
	d.feature(
		"Hole1",
		"HoleFeature",
		{"holes": [{"at": [0, 0, 10]}], "diameter": "8 mm", "through_all": True, "targets": [BODY]},
		bodies={BODY: s.body(((-20, -20, 0), (20, 20, 10)))},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_draft_tilts_a_face(tmp_path, cli, sc_version):
	d = Doc("Drafted")
	region = d.rect("Base", -20, -10, 20, 10)
	block = Solid().add_box((-20, -10, 0), (20, 10, 10))
	d.extrude("Extrude1", region, "10 mm", bodies={BODY: block.body(((-20, -10, 0), (20, 10, 10)))})
	inset = 10 * math.tan(math.radians(5))  # the +X face leans in by this at the top
	block.parts.append((-0.5 * 10 * inset * 20, (20 - inset / 3, 0, 20 / 3)))
	d.feature(
		"Draft1",
		"DraftFeature",
		{
			"faces": [{"at": [20, 0, 5], "body": BODY}],
			"angle": "5 deg",
			"neutral": {"origin": [0, 0, 0], "normal": [0, 0, 1]},
			"pull": [0, 0, 1],
		},
		bodies={BODY: block.body(((-20, -10, 0), (20, 10, 10)))},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_thin_extrude_of_an_open_line(tmp_path, cli, sc_version):
	d = Doc("Thin")
	uid = d.sketch(
		"Path",
		XY,
		[{"id": "P0", "xy": [0, 0], "fixed": True}, {"id": "P1", "xy": [40, 0]}],
		[{"id": "C0", "type": "line", "ends": ["P0", "P1"]}],
	)
	d.feature(
		"Extrude1",
		"ExtrudeFeature",
		{
			"op": "new",
			"extent": {"type": "one", "side1": {"type": "distance", "expr": "10 mm"}},
			"thin": {"thickness": "2 mm", "side": "center", "sketch": uid, "curves": ["C0"]},
		},
		bodies={BODY: {"volume": 800, "com": [20, 0, 5], "bbox": [[0, -1, 0], [40, 1, 10]]}},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_ellipse_profile_through_its_alias(tmp_path, cli, sc_version):
	d = Doc("Oval")
	uid = d.sketch(
		"Oval",
		XY,
		[{"id": "P0", "xy": [0, 0], "fixed": True}],
		[{"id": "C0", "type": "ellipse", "center": "P0", "major": [20, 0], "minor_radius": 10}],
	)
	region = {"sketch": uid, "outer": ["C0"], "inside": [0, 0], "area": math.pi * 200, "centroid": [0, 0]}
	d.extrude(
		"Extrude1",
		region,
		"5 mm",
		bodies={BODY: {"volume": math.pi * 200 * 5, "com": [0, 0, 2.5], "bbox": [[-20, -10, 0], [20, 10, 5]]}},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)


def test_spline_profile_through_its_alias(tmp_path, cli, sc_version):
	d = Doc("Arch")
	pts = [{"id": "P0", "xy": [0, 0], "fixed": True}, {"id": "P1", "xy": [20, 0]}, {"id": "P2", "xy": [10, 8]}]
	uid = d.sketch(
		"Arch",
		XY,
		pts,
		[
			{"id": "C0", "type": "spline_fit", "through": ["P0", "P2", "P1"]},
			{"id": "C1", "type": "line", "ends": ["P1", "P0"]},
		],
	)
	region = {"sketch": uid, "outer": ["C0", "C1"], "inside": [10, 3]}
	d.extrude("Extrude1", region, "5 mm", bodies={BODY: {"volume": None}})
	result, out = run(d, tmp_path, cli, sc_version)
	assert not result.failed, result.failed
	volume = runner.evaluate(cli, out)["total"]["volume_mm3"]
	assert 0.5 * 20 * 8 * 5 < volume < 20 * 8 * 5  # more than the triangle, less than the box


def test_history_stays_parametric(tmp_path, cli, sc_version):
	d = Doc("Param")
	d.param("thk", "4 mm")
	region = d.rect("Base", -15, -10, 15, 10)
	d.extrude(
		"Extrude1",
		region,
		"thk",
		bodies={BODY: {"volume": 2400, "com": [0, 0, 2], "bbox": [[-15, -10, 0], [15, 10, 4]]}},
	)
	result, out = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)
	change = tmp_path / "change.json"
	change.write_text(
		json.dumps({"commands": [{"command": "parameters.change", "params": {"name": "thk", "expression": "8 mm"}}]})
	)
	assert runner.evaluate(cli, out, change)["total"]["volume_mm3"] == pytest.approx(4800, rel=1e-6)


# --- safety nets ---------------------------------------------------------------------------------


def test_a_constraint_that_would_move_the_sketch_is_left_out(tmp_path, cli, sc_version):
	d = Doc("Guard")
	region = d.rect(
		"Base",
		0,
		0,
		40,
		20,
		constraints=[
			{"type": "Equal", "refs": {"curveOne": "C0", "curveTwo": "C1"}},
			{"type": "Horizontal", "refs": {"line": "C0"}},
		],
	)
	d.extrude(
		"Extrude1",
		region,
		"5 mm",
		bodies={BODY: {"volume": 4000, "com": [20, 10, 2.5], "bbox": [[0, 0, 0], [40, 20, 5]]}},
	)
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)
	assert any("moved" in x for x in result.dropped) and statuses(result)["Base"] == "adjusted"


def test_refused_fillet_edges_are_split(tmp_path, cli, sc_version):
	d = rounded_corners(extra_edges=[{"at": [500.0, 500.0, 500.0], "body": BODY}])  # a point on no edge
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full", result.model_dump_json(indent=1)
	assert statuses(result)["Fillet1"] == "adjusted"


def thickened(name="Thick"):
	"""A plate, then a step SolidCraft has no command for, which doubles its thickness in Fusion."""
	d, _ = plate(name, 20, 20, 5)
	d.feature(
		"Mystery1",
		"MysteryFeature",
		{"created": []},
		bodies={BODY: {"volume": 4000, "com": [0, 0, 5], "bbox": [[-10, -10, 0], [10, 10, 10]]}},
	)
	return d


def test_unmapped_step_without_fusion_is_reported_and_requested(tmp_path, cli, sc_version):
	needed = tmp_path / "needed-steps.json"
	result, _ = run(thickened(), tmp_path, cli, sc_version, sources=Sources([], needed))
	assert result.category == "mismatch" and statuses(result)["Mystery1"] == "mismatch"
	assert json.loads(needed.read_text())["steps"] == [{"index": 2, "components": [COMP]}]


def test_unmapped_step_comes_in_as_fusions_result(tmp_path, cli, sc_version):
	steps = tmp_path / "steps"
	steps.mkdir()

	async def export_fusions_result():  # what the add-in would export: a 20 x 20 x 10 block
		async with open_session(cli, [tmp_path], timeout=120, log_path=tmp_path / "prep.log") as sc:
			await sc.execute("sketch.create", {"plane": "XY", "name": "S"})
			await sc.execute("sketch.line", {"points": [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]})
			await sc.execute("sketch.finish")
			assert (
				await sc.execute("solid.extrude", {"profiles": "all", "distance": "10 mm", "body_name": "Body1"})
			).ok
			assert (await sc.tool("export", {"path": str(steps / "step2.step")})).ok

	anyio.run(export_fusions_result)
	identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
	(steps / "index.json").write_text(
		json.dumps({"2": [{"component": COMP, "path": "step2.step", "matrix": identity}]})
	)
	result, _ = run(
		thickened("Thick2"), tmp_path, cli, sc_version, sources=Sources([StepsFolder(steps)]), allow=[steps]
	)
	assert result.category == "partial", result.model_dump_json(indent=1)
	assert statuses(result) | {} == {"Base": "exact", "Extrude1": "exact", "Mystery1": "substituted"}


def test_suppressed_step_is_skipped(tmp_path, cli, sc_version):
	d, _ = plate("Supp")
	d.step(name="Fillet9", kind="FilletFeature", suppressed=True, feature={"sets": []})
	result, _ = run(d, tmp_path, cli, sc_version)
	assert result.category == "full" and statuses(result)["Fillet9"] == "skipped"


# --- the command line, end to end ----------------------------------------------------------------


def test_cli_workspace_round_trip(tmp_path, cli):
	cli_runner = CliRunner()
	ws = tmp_path / "ws"
	assert cli_runner.invoke(app, ["init", str(ws), "--hub", "Hub"]).exit_code == 0
	design = ws / "designs" / "Hub" / "Proj" / "Box"
	schema.dump(lidded_box().raw, str(design / "timeline.json"))
	base = ["--workspace", str(ws), "--cli", str(cli)]
	r = cli_runner.invoke(app, [*base, "build", "-q"])
	assert r.exit_code == 0, r.output
	built = json.loads((design / "Lidded box.build.json").read_text(encoding="utf-8"))
	assert built["category"] == "full" and built["verify"]["ok"]
	assert "RESULT: FULL" in (design / "Lidded box.report.txt").read_text(encoding="utf-8")
	assert (ws / "reports" / "scorecard.md").is_file() and (ws / "reports" / "index.html").is_file()
	sc = design / "Lidded box.sc"
	mtime = sc.stat().st_mtime
	assert cli_runner.invoke(app, [*base, "build", "-q"]).exit_code == 0
	assert sc.stat().st_mtime == mtime  # up to date: not rebuilt
	assert cli_runner.invoke(app, [*base, "verify"]).exit_code == 0
	doctor = subprocess.run(
		["fuse2sc", "--cli", str(cli), "doctor"],
		check=False,
		capture_output=True,
		text=True,
		encoding="utf-8",
		env=os.environ | {"PYTHONIOENCODING": "utf-8"},
	)
	assert "solidcraft-cli mcp answers" in doctor.stdout + doctor.stderr


def test_every_command_fuse2sc_uses_exists_in_solidcraft(cli):
	"""Catches a SolidCraft release that renames or drops a command fuse2sc sends."""
	import re

	root = Path(__file__).resolve().parents[2] / "src" / "fuse2sc"
	pattern = re.compile(
		r"""["']((?:solid|sketch|component|occurrence|body|parameters|material|appearance|browser"""
		r"""|document|file|construct)\.[a-z_][a-z0-9_.]*[a-z0-9_])["']"""
	)
	used = {m for p in root.rglob("*.py") for m in pattern.findall(p.read_text(encoding="utf-8"))}
	out = subprocess.run([str(cli), "commands"], capture_output=True, text=True, encoding="utf-8", check=True).stdout
	known = {c["id"] for c in json.loads(out)}
	assert used and not used - known, f"not in solidcraft-cli {sorted(used - known)}"
