import pytest
from pydantic import ValidationError

from fuse2sc import model
from fuse2sc.ir import schema, units
from fuse2sc.ir.keys import safe_filename
from fuse2sc.translate.expressions import architectural, is_zero, names_in, needed_model_parameters, ordered, rewrite
from irkit import lidded_box

# --- units and names -----------------------------------------------------------------------------


def test_matrices_convert_their_translation_only():
	assert units.matrix_mm([1, 0, 0, 1.5, 0, 1, 0, 0, 0, 0, 1, -2, 0, 0, 0, 1])[3::4][:3] == [15.0, 0.0, -20.0]


def test_names():
	assert safe_filename('a/b:c*"d') == "a_b_c__d"
	assert model.stem_of("Bracket v12") == "Bracket"
	assert model.stem_of("Rev v2a") == "Rev v2a"


# --- the document --------------------------------------------------------------------------------


def test_document_round_trip_and_payloads():
	doc = lidded_box().doc
	assert doc.stem == "Lidded box" and len(doc.steps) == 9
	extrude = doc.steps[1].payload()
	assert isinstance(extrude, model.Extrude) and extrude.extent.side1.expr == "height"
	assert doc.steps[0].payload() is None  # sketches carry no feature payload
	assert set(doc.final_bodies()) == {"root/Body1", "root/Lid"}


def test_malformed_payload_raises_validation_error():
	raw = lidded_box().raw
	raw["steps"][1]["feature"]["extent"] = {"type": "sideways"}
	with pytest.raises(ValidationError):
		model.parse(raw).steps[1].payload()


def test_schema_versions():
	raw = schema.new_document("D", "0")
	raw["schema"] = schema.SCHEMA_VERSION + 1
	with pytest.raises(schema.IRError):
		model.parse(raw)
	with pytest.raises(schema.IRError):
		model.parse({"document": "no schema"})


def test_json_schema_names_every_payload():
	js = model.json_schema()
	assert js["x-feature-payloads"]["ExtrudeFeature"] == "#/$defs/Extrude"
	assert "Hole" in js["$defs"] and js["properties"]["schema"]["type"] == "integer"


def test_dump_is_atomic(tmp_path):
	p = tmp_path / "sub" / "timeline.json"
	schema.dump(schema.new_document("D", "0"), str(p))
	assert model.load(p).document == "D"
	assert not list(p.parent.glob(".tmp-*"))


# --- expressions ---------------------------------------------------------------------------------


def test_architectural_lengths():
	assert architectural("1'-6 1/2\"") == "(1 ft + (6 + 1/2) in)"
	assert architectural("0'- 1/4\"") == "(0 ft + 1/4 in)"
	assert architectural("width - 2 in") == "width - 2 in"
	assert rewrite({"name": "1'-2\"", "distance": "1'-2\""}) == {"name": "1'-2\"", "distance": "(1 ft + 2 in)"}


@pytest.mark.parametrize("expr", [None, "", "0", "0 in", "0.0 mm", "-0 deg", 0])
def test_zero(expr):
	assert is_zero(expr)


@pytest.mark.parametrize("expr", ["2 mm", "0.5 mm", ".5 in", "0.5 deg", "width"])
def test_not_zero(expr):
	assert not is_zero(expr)


def test_parameter_order_and_model_parameters():
	assert names_in("width * 2 + d12 - sin(30 deg)") == {"width", "d12"}
	ps = [
		model.Parameter(name="b", expression="a * 2"),
		model.Parameter(name="a", expression="10 mm"),
		model.Parameter(name="c", expression="b + a"),
	]
	assert [p.name for p in ordered(ps)] == ["a", "b", "c"]
	cyc = [model.Parameter(name="x", expression="y"), model.Parameter(name="y", expression="x")]
	assert [p.name for p in ordered(cyc)] == ["x", "y"]
	raw = schema.new_document("T", "0")
	raw["parameters"] = [{"name": "w", "expression": "d3 * 2"}]
	raw["model_parameters"] = [
		{"name": "d3", "expression": "d1 + 1 mm"},
		{"name": "d1", "expression": "4 mm"},
		{"name": "d9", "expression": "1 mm"},
	]
	assert {p.name for p in needed_model_parameters(model.parse(raw))} == {"d1", "d3"}
