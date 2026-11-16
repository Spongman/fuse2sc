import json
import sys

from typer.testing import CliRunner

from fuse2sc import reports
from fuse2sc.cli import app, parse_steps
from fuse2sc.ir import schema
from fuse2sc.pipeline import order_by_links
from fuse2sc.replay import BuildResult, StepResult
from fuse2sc.settings import find_workspace, init_workspace
from fuse2sc.verify import reference, same_expression
from irkit import lidded_box

runner = CliRunner()


def test_parse_steps():
	assert parse_steps("3,5-7") == {3, 5, 6, 7}
	assert parse_steps(None) == frozenset()


def test_linked_designs_build_first(tmp_path):
	def timeline(name, links):
		d = tmp_path / name
		d.mkdir()
		raw = schema.new_document(name, "0")
		raw["components"] = [{"name": n, "linked": n, "occurrences": []} for n in links]
		(d / "timeline.json").write_text(json.dumps(raw))
		return d / "timeline.json"

	top, mid, leaf = timeline("Top", ["Mid"]), timeline("Mid", ["Leaf"]), timeline("Leaf", [])
	order = order_by_links([top, mid, leaf])
	assert order.index(leaf) < order.index(mid) < order.index(top)


def test_same_expression():
	assert same_expression("0.75 in", "19.05 mm")
	assert same_expression('0.75"', "19.05 mm")
	assert same_expression("width * 2", "width*2")
	assert not same_expression("10 mm", "11 mm")
	assert not same_expression("10 mm", "10 deg")


def test_reference_from_timeline(tmp_path):
	schema.dump(lidded_box().raw, str(tmp_path / "timeline.json"))
	ref = reference(tmp_path)
	assert ref["source"] == "timeline" and len(ref["bodies"]) == 2
	assert {p["name"] for p in ref["parameters"]} >= {"width", "wall"}


def result(category="partial") -> BuildResult:
	return BuildResult(
		document="D",
		out="D.sc",
		saved=True,
		category=category,
		solidcraft_version="0.42.0",
		commands=9,
		steps=[
			StepResult(index=0, name="Sketch1", kind="Sketch"),
			StepResult(index=1, name="Extrude1", kind="ExtrudeFeature"),
			StepResult(
				index=2,
				name="Thread1",
				kind="ThreadFeature",
				status="substituted",
				details=["Fusion's result brought in for c/B"],
			),
		],
	)


def test_design_report(tmp_path):
	j, t = reports.write_design(result(), tmp_path, "D")
	text = t.read_text(encoding="utf-8")
	assert "RESULT: PARTIAL" in text and "FUSION RESULT" in text and "Thread1" in text
	assert BuildResult.model_validate_json(j.read_text()).category == "partial"


def test_workspace_scorecard(tmp_path):
	init_workspace(tmp_path, "Hub")
	ws = find_workspace(tmp_path)
	for name, cat in (("A", "full"), ("B", "partial"), ("C", None)):
		d = ws.designs / "Hub" / "P" / name
		d.mkdir(parents=True)
		(d / "timeline.json").write_text("{}")
		if cat:
			r = result(cat)
			if cat == "full":
				r.steps[2].status = "exact"
			reports.write_design(r, d, name)
	paths = reports.write_workspace(ws, "test")
	md = paths["md"].read_text(encoding="utf-8")
	assert "ThreadFeature" in md and "not built" in md and "✅ full" in md
	assert "Hub/P/B,partial" in paths["csv"].read_text(encoding="utf-8")
	assert "<table>" in paths["html"].read_text(encoding="utf-8")


def test_cli_init_inspect_schema(tmp_path):
	r = runner.invoke(app, ["init", str(tmp_path / "ws"), "--hub", "H"])
	assert r.exit_code == 0 and (tmp_path / "ws" / "fusion-to-solidcraft.json").is_file()
	assert runner.invoke(app, ["init", str(tmp_path / "ws")]).exit_code == 2  # exists already
	tl = tmp_path / "timeline.json"
	schema.dump(lidded_box().raw, str(tl))
	r = runner.invoke(app, ["inspect", str(tl), "--plan-out", str(tmp_path / "plan.json")])
	assert r.exit_code == 0 and "9 steps" in r.output
	assert json.loads((tmp_path / "plan.json").read_text())[0]["command"] == "document.units"
	r = runner.invoke(app, ["schema"])
	assert r.exit_code == 0 and json.loads(r.output)["x-feature-payloads"]


def test_addin_carries_an_identical_copy_of_the_ir():
	sys.path.insert(0, str(__import__("conftest").ROOT / "tools"))
	import sync_addin

	assert sync_addin.differs() == [], "run: python tools/sync_addin.py"
