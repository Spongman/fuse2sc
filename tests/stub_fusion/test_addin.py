"""The add-in's code on a stub of Fusion's API (no Fusion needed).

These guard what doesn't depend on Fusion's geometry kernel: module imports, collection handling,
region geometry, sketch reading and ids, unit conversion, the timeline marker being restored
whatever happens, folder naming and how builds are launched. What only real Fusion can show needs
the add-in run there.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ADDIN = HERE.parent.parent / "fusion_addin" / "Fuse2SC"


@pytest.fixture(autouse=True, scope="module")
def stub_adsk():
	sys.path[:0] = [str(HERE), str(ADDIN)]
	for m in [m for m in sys.modules if m == "adsk" or m.startswith(("adsk.", "fuse2sc_fusion", "fuse2sc_ir"))]:
		del sys.modules[m]
	yield
	for p in (str(HERE), str(ADDIN)):
		sys.path.remove(p)


@pytest.mark.parametrize(
	"module",
	[
		"safe",
		"places",
		"timeline",
		"snapshot",
		"sketches",
		"regions",
		"features",
		"appearance",
		"reader",
		"export",
		"batch",
		"bridge",
		"launcher",
	],
)
def test_modules_import(module):
	importlib.import_module(f"fuse2sc_fusion.{module}")


class Collection:
	"""count + item(i), like Fusion's collections."""

	def __init__(self, xs):
		self.xs = list(xs)

	@property
	def count(self):
		return len(self.xs)

	def item(self, i):
		return self.xs[i]

	def __iter__(self):
		return iter(self.xs)


class Vector:
	"""len + indexing only, like BRepFaceVector."""

	def __init__(self, xs):
		self.xs = list(xs)

	def __len__(self):
		return len(self.xs)

	def __getitem__(self, i):
		return self.xs[i]


def test_safe_reads():
	from fuse2sc_fusion.safe import attempt, get, items

	assert items(Collection([1, 2])) == [1, 2] and items(Vector([3, 4])) == [3, 4]
	assert items(None) == [] and items(5) == [5]
	errors = []
	assert attempt(errors, "x", lambda: 1 / 0, "d") == "d" and errors[0].startswith("x:")
	assert get(type("O", (), {"b": 2})(), "a", "b") == 2


def test_region_geometry():
	from fuse2sc_fusion.regions import _chain, contains, inside_point, shoelace

	# an L whose centroid falls in its notch
	L = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]
	area, centroid = shoelace(L)
	assert abs(area) == pytest.approx(36) and not contains(L, centroid)
	p = inside_point(L, [], prefer=centroid)
	assert contains(L, p)
	# a ring: the point must be in the band, not the hole
	outer = [(-10, -10), (10, -10), (10, 10), (-10, 10)]
	hole = [(-8, -8), (8, -8), (8, 8), (-8, 8)]
	p = inside_point(outer, [hole], prefer=(0, 0))
	assert contains(outer, p) and not contains(hole, p)
	# runs of a loop, some drawn backwards, chain into one outline
	assert _chain([[(0, 0), (1, 0)], [(1, 1), (1, 0)], [(1, 1), (0, 0)]]) == [(0, 0), (1, 0), (1, 1), (0, 0)]


def fake_sketch():
	import adsk.core
	import adsk.fusion

	tokens = iter(range(10_000))

	def point(x, y, fixed=False):
		return type(
			"SketchPoint",
			(),
			{
				"geometry": adsk.core.Point3D(x, y, 0),
				"isFixed": fixed,
				"isReference": False,
				"entityToken": f"t{next(tokens)}",
			},
		)()

	def curve(cls, **attrs):
		c = cls()
		c.__dict__.update(
			isConstruction=False, isReference=False, isFixed=False, entityToken=f"t{next(tokens)}", **attrs
		)
		return c

	o, a, b, ctr = point(0, 0, True), point(2, 0), point(2, 1), point(1, 0.5)
	l1 = curve(adsk.fusion.SketchLine, startSketchPoint=o, endSketchPoint=a)
	l2 = curve(adsk.fusion.SketchLine, startSketchPoint=a, endSketchPoint=b)
	circle = curve(adsk.fusion.SketchCircle, centerSketchPoint=ctr, radius=0.25)
	HorizontalConstraint = type("HorizontalConstraint", (), {"line": l1})
	param = type("Param", (), {"expression": "width"})()
	SketchLinearDimension = type(
		"SketchLinearDimension",
		(),
		{"entityOne": o, "entityTwo": a, "isDriving": True, "orientation": 1, "parameter": param},
	)
	comp = type("Component", (), {"name": "Part"})()
	sketch = type(
		"Sketch",
		(),
		{
			"name": "Base",
			"sketchPoints": Collection([o, a, b, ctr]),
			"sketchCurves": Collection([l1, l2, circle]),
			"geometricConstraints": Collection([HorizontalConstraint()]),
			"sketchDimensions": Collection([SketchLinearDimension()]),
			"transform": adsk.core.Matrix3D([1, 0, 0, 1.0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
			"parentComponent": comp,
		},
	)()
	design = type("Design", (), {"rootComponent": comp})()
	return design, sketch


def test_sketch_reading():
	from fuse2sc_fusion.places import Places
	from fuse2sc_fusion.sketches import read_sketch

	design, sketch = fake_sketch()
	s = read_sketch(sketch, Places(design), index=3)
	assert s["uid"] == "Part::Base@3"
	assert [p["id"] for p in s["points"]] == ["P0", "P1", "P2", "P3"] and s["points"][1]["xy"] == [20.0, 0.0]
	assert [c["type"] for c in s["curves"]] == ["line", "line", "circle"]
	assert s["curves"][1]["ends"] == ["P1", "P2"] and s["curves"][2]["radius"] == 2.5
	assert s["constraints"] == [{"type": "Horizontal", "refs": {"line": "C0"}}]
	d = s["dimensions"][0]
	assert d["type"] == "Linear" and d["expr"] == "width" and d["orientation"] == "horizontal"
	assert d["refs"] == {"entityOne": "P0", "entityTwo": "P1"}
	assert s["frame"]["origin"] == [10.0, 0.0, 0.0]


def test_the_timeline_marker_comes_back():
	from fuse2sc_fusion import timeline

	class Timeline:
		count, markerPosition = 3, 2

		def item(self, i):
			return type("Item", (), {"rollTo": lambda item, before: setattr(self, "markerPosition", i)})()

		def moveToEnd(self):
			self.markerPosition = self.count

	tl = Timeline()
	design = type("Design", (), {"timeline": tl})()
	with pytest.raises(ValueError), timeline.marker_at(design, 1, before=True):
		assert tl.markerPosition == 1
		raise ValueError("a read failed")
	assert tl.markerPosition == 2


def test_design_folders_tell_twins_apart(tmp_path):
	from fuse2sc_fusion.batch import design_dir

	def data_file(name, id_):
		return type("DataFile", (), {"name": name, "id": id_})()

	siblings = ["Bracket", "Bracket", "X"]
	a = design_dir(
		tmp_path,
		hub="Hub",
		project="Proj",
		folders=("Sub",),
		data_file=data_file("Bracket", "urn:aaaa11112222"),
		siblings=siblings,
	)
	b = design_dir(
		tmp_path,
		hub="Hub",
		project="Proj",
		folders=("Sub",),
		data_file=data_file("Bracket", "urn:bbbb33334444"),
		siblings=siblings,
	)
	c = design_dir(
		tmp_path, hub="Hub", project="Proj", folders=("Sub",), data_file=data_file("X", "urn:cccc"), siblings=siblings
	)
	assert a != b and a.name == "Bracket (11112222)" and c == tmp_path / "Hub" / "Proj" / "Sub" / "X"


def test_builds_run_with_the_recorded_python(tmp_path, monkeypatch):
	from fuse2sc_fusion import launcher

	monkeypatch.setattr(launcher, "ADDIN", tmp_path)
	(tmp_path / "config.json").write_text(json.dumps({"python": sys.executable}))
	assert launcher.fuse2sc_command() == [sys.executable, "-m", "fuse2sc"]
	(tmp_path / "config.json").unlink()
	monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
	with pytest.raises(RuntimeError, match="not installed"):
		launcher.fuse2sc_command()


def test_the_addin_reads_exactly_the_kinds_the_ir_models():
	"""A reader without a model (or a model without a reader) is a feature half added."""
	from fuse2sc.model import PAYLOADS
	from fuse2sc_fusion.features import READERS

	assert set(READERS) == set(PAYLOADS)
