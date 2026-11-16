"""The IR as typed models (pydantic). docs/ir.md describes it; `fuse2sc schema` writes its JSON Schema.

The Fusion add-in writes plain JSON (it runs on Fusion's embedded Python, without pydantic);
these models are how the host reads it. Feature payloads are validated per kind on demand
(`Step.payload()`), so an unknown or malformed feature costs that step, not the document.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .ir import schema as ir_schema
from .ir.keys import safe_filename

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Mat4 = Annotated[list[float], Field(min_length=16, max_length=16)]
Op = Literal["new", "join", "cut", "intersect"]


class Model(BaseModel):
	model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --- shared pieces ---------------------------------------------------------------------------------


class Body(Model):
	volume: float | None = None
	area: float | None = None
	com: Vec3 | None = None
	bbox: tuple[Vec3, Vec3] | None = None
	visible: bool = True


class Pick(Model):
	"""A point on an edge or face (world), read before the feature changed it, and its body."""

	at: Vec3
	body: str | None = None


class Plane(Model):
	origin: Vec3
	normal: Vec3


class Axis(Model):
	origin: Vec3
	dir: Vec3


class Region(Model):
	sketch: str
	outer: list[str | None] = []
	holes: list[list[str | None]] = []
	inside: Vec2 | None = None
	area: float | None = None
	centroid: Vec2 | None = None

	@property
	def outer_known(self) -> bool:
		return self.outer_curves is not None

	@property
	def outer_curves(self) -> list[str] | None:
		"""The curves around the region, when every one of them is known."""
		known = [c for c in self.outer if c is not None]
		return known if known and len(known) == len(self.outer) else None


class OtherInput(Model):
	other: str


class FeatureRef(Model):
	name: str
	component: str | None = None


class Inputs(Model):
	bodies: list[str] | None = None
	features: list[FeatureRef] | None = None


# --- sketches --------------------------------------------------------------------------------------


class Frame(Model):
	origin: Vec3
	x: Vec3
	y: Vec3


class SketchPoint(Model):
	id: str
	xy: Vec2
	fixed: bool = False


CurveType = Literal["line", "circle", "arc", "ellipse", "spline_fit", "spline_cp", "other"]


class Curve(Model):
	id: str
	type: CurveType
	construction: bool = False
	fixed: bool = False
	ends: list[str | None] | None = None
	center: str | None = None
	radius: float | None = None
	via: Vec2 | None = None
	major: Vec2 | None = None
	minor_radius: float | None = None
	through: list[str | None] | None = None
	closed: bool = False
	poles: list[Vec2] | None = None
	degree: int | None = None
	rational: bool = False
	polyline: list[Vec2] | None = None
	fusion_type: str | None = None


class Relation(Model):
	type: str
	refs: dict[str, str] = {}


class Dimension(Relation):
	expr: str | None = None
	driving: bool = False
	orientation: Literal["aligned", "horizontal", "vertical"] | None = None
	text_at: Vec2 | None = None


class Sketch(Model):
	uid: str
	name: str
	component: str | None = None
	frame: Frame
	points: list[SketchPoint] = []
	curves: list[Curve] = []
	constraints: list[Relation] = []
	dimensions: list[Dimension] = []


# --- feature payloads ------------------------------------------------------------------------------
#
# Each payload model registers the Fusion feature types (Step.kind) it describes with @payload.
# Adding a feature type: a model here, a reader in the add-in (fuse2sc_fusion/features.py READERS),
# and a handler in translate/features.py (docs/adding-a-feature.md). Tests check the three agree.

PAYLOADS: dict[str, type[Feature]] = {}


def payload(*kinds: str) -> Callable[[type[F]], type[F]]:
	"""Register a payload model for these Fusion feature types."""

	def register(cls: type[F]) -> type[F]:
		for kind in kinds:
			if kind in PAYLOADS:
				raise ValueError(f"{kind} already has a payload model: {PAYLOADS[kind].__name__}")
			PAYLOADS[kind] = cls
		return cls

	return register


class Feature(Model):
	created: list[str] = []


F = TypeVar("F", bound=Feature)


class End(Model):
	type: Literal["distance", "to", "all", "other"]
	expr: str | None = None
	mm: float | None = None
	at: Vec3 | None = None
	offset: str | None = None
	reverse: bool = False
	fusion_type: str | None = None


class Extent(Model):
	type: Literal["one", "two", "symmetric"]
	side1: End | None = None
	side2: End | None = None
	expr: str | None = None
	total: bool = False


class Start(Model):
	type: Literal["sketch", "offset", "object", "other"] = "sketch"
	expr: str | None = None
	at: Vec3 | None = None
	offset: str | None = None
	fusion_type: str | None = None


class Thin(Model):
	thickness: str | None = None
	side: Literal["one", "two", "center"] = "center"
	sketch: str | None = None
	curves: list[str | None] = []


@payload("ExtrudeFeature")
class Extrude(Feature):
	op: Op = "new"
	regions: list[Region] = []
	faces: list[Pick] = []
	targets: list[str] = []
	extent: Extent
	start: Start = Start()
	taper: str | None = None
	thin: Thin | None = None
	broken: str | None = None


class Angle(Model):
	type: Literal["one", "two", "symmetric", "other"]
	expr: str | None = None
	expr2: str | None = None
	degrees: float | None = None
	fusion_type: str | None = None


@payload("RevolveFeature")
class Revolve(Feature):
	op: Op = "new"
	regions: list[Region] = []
	axis: Axis | None = None
	angle: Angle
	targets: list[str] = []
	other_profiles: bool = False


class SweepPath(Model):
	sketch: str | None = None
	curves: list[str | None] = []


@payload("SweepFeature")
class Sweep(Feature):
	op: Op = "new"
	regions: list[Region] = []
	path: SweepPath
	orientation: Literal["perpendicular", "parallel"] = "perpendicular"
	twist: str | None = None
	rails: bool = False
	targets: list[str] = []


@payload("LoftFeature")
class Loft(Feature):
	op: Op = "new"
	sections: list[Region | OtherInput] = []
	rails: int = 0
	closed: bool = False
	solid: bool = True
	targets: list[str] = []


BlendType = Literal["constant", "chord", "variable", "equal", "two_distances", "distance_angle", "other"]


class BlendSet(Model):
	type: BlendType
	fusion_type: str | None = None
	size: dict[str, str | None] = {}
	tangent_chain: bool = False
	edges: list[Pick] = []
	flipped: bool = False
	start: Vec3 | None = None
	mid_radii: bool = False


@payload("FilletFeature", "ChamferFeature")
class Blend(Feature):
	sets: list[BlendSet] = []


@payload("ShellFeature")
class Shell(Feature):
	faces: list[Pick] = []
	bodies: list[str] = []
	inside: str | None = None
	outside: str | None = None
	tangent_chain: bool = False


@payload("DraftFeature")
class Draft(Feature):
	faces: list[Pick] = []
	angle: str | None = None
	neutral: Plane | None = None
	pull: Vec3 | None = None


@payload("OffsetFacesFeature")
class OffsetFaces(Feature):
	faces: list[Pick] = []
	distance: str | None = None


class HoleAt(Model):
	at: Vec3


@payload("HoleFeature")
class Hole(Feature):
	holes: list[HoleAt] = []
	diameter: str | None = None
	style: Literal["simple", "counterbore", "countersink"] = "simple"
	depth: str | None = None
	through_all: bool = False
	cb_diameter: str | None = None
	cb_depth: str | None = None
	cs_diameter: str | None = None
	cs_angle: str | None = None
	tip_angle: str | None = None
	targets: list[str] = []


@payload("CombineFeature")
class Combine(Feature):
	target: str
	tools: list[str]
	op: Literal["join", "cut", "intersect"] = "join"
	keep_tools: bool = False


@payload("MoveFeature")
class Move(Feature):
	bodies: list[str] = []
	matrix: Mat4 | None = None


@payload("ScaleFeature")
class Scale(Feature):
	bodies: list[str] = []
	factor: str | None = None
	factors: list[str | None] | None = None
	origin: Vec3 | None = None


@payload("RemoveFeature")
class Remove(Feature):
	bodies: list[str] = []


class SplitTool(Model):
	type: Literal["plane", "face", "line", "body", "other"]
	origin: Vec3 | None = None
	normal: Vec3 | None = None
	at: Vec3 | None = None
	body: str | None = None
	plane: Plane | None = None
	sketch: str | None = None
	curve: str | None = None
	fusion_type: str | None = None


@payload("SplitBodyFeature")
class Split(Feature):
	bodies: list[str] = []
	tool: SplitTool


@payload("MirrorFeature")
class Mirror(Feature):
	inputs: Inputs | None = None
	plane: Plane | None = None
	join: bool = False


@payload("CircularPatternFeature")
class CircularPattern(Feature):
	inputs: Inputs | None = None
	axis: Axis | None = None
	count: str | None = None
	angle: str | None = None
	symmetric: bool = False
	skipped_instances: bool = False


@payload("RectangularPatternFeature")
class RectangularPattern(Feature):
	inputs: Inputs | None = None
	dir1: Vec3 | None = None
	count1: str | None = None
	spacing1: str | None = None
	dir2: Vec3 | None = None
	count2: str | None = None
	spacing2: str | None = None
	extent_mode: Literal["spacing", "extent"] = "spacing"
	symmetric1: bool = False
	symmetric2: bool = False
	skipped_instances: bool = False


@payload("CopyPasteBody", "CutPasteBody")
class Paste(Feature):
	sources: list[str] = []


# --- steps and the document ------------------------------------------------------------------------


class OccurrenceInfo(Model):
	component: str
	path: str | None = None
	linked: str | None = None
	matrix: Mat4 | None = None


class Placements(Model):
	before: dict[str, Mat4] = {}
	after: dict[str, Mat4] = {}


class Step(Model):
	index: int
	name: str
	kind: str
	suppressed: bool = False
	component: str | None = None
	unread: list[str] = []
	snapshot: dict[str, Body] | None = None
	error: str | None = None
	sketch: Sketch | None = None
	feature: dict[str, Any] | None = None
	occurrence: OccurrenceInfo | None = None
	placements: Placements | None = None

	def payload(self) -> Feature | None:
		"""The feature's inputs as their typed model; None for kinds without one.
		Raises pydantic.ValidationError when the payload is malformed."""
		model = PAYLOADS.get(self.kind)
		if model is None or self.feature is None:
			return None
		return model.model_validate(self.feature)


class Parameter(Model):
	name: str
	expression: str
	unit: str = ""
	comment: str = ""
	value: float | None = None


class Occurrence(Model):
	path: str
	parent: str | None = None
	matrix: Mat4
	visible: bool = True


class Component(Model):
	name: str
	linked: str | None = None
	inside_linked: bool = False
	occurrences: list[Occurrence] = []


class Look(Model):
	name: str = ""
	rgb: str = "#b4b4b4"
	metal: bool = False
	roughness: float | None = None
	opacity: float | None = None


class Material(Model):
	name: str
	density: float | None = None


class FaceLook(Model):
	at: Vec3
	look: Look


class BodyAppearance(Model):
	look: Look | None = None
	material: Material | None = None
	faces: list[FaceLook] = []


class FusionInfo(Model):
	id: str | None = None
	version: int | None = None
	name: str | None = None
	hub: str | None = None
	project: str | None = None
	folder: str | None = None
	parametric: bool | None = None


class Document(Model):
	schema_version: int = Field(alias="schema")
	tool: dict[str, Any] = {}
	document: str
	fusion: FusionInfo = FusionInfo()
	units: str = "mm"
	parameters: list[Parameter] = []
	model_parameters: list[Parameter] = []
	components: list[Component] = []
	steps: list[Step] = []
	appearance: dict[str, BodyAppearance] = {}
	errors: list[str] = []

	@property
	def stem(self) -> str:
		return stem_of(self.fusion.name or self.document)

	def final_bodies(self) -> dict[str, Body]:
		return next((s.snapshot for s in reversed(self.steps) if s.snapshot is not None), {})

	def linked_components(self) -> set[str]:
		return {c.name for c in self.components if c.linked or c.inside_linked}


def stem_of(name: str) -> str:
	"""A design's file stem: its name without Fusion's " v12" version suffix, safe as a file name."""
	head, sep, tail = name.rpartition(" v")
	return safe_filename(head if sep and tail.isdigit() else name)


class IRError(ValueError):
	pass


def load(path: str | Path) -> Document:
	try:
		raw = ir_schema.load(str(path))  # schema version checks, upgrades, basic shape
		return Document.model_validate(raw)
	except (ValidationError, ir_schema.IRError, ValueError) as e:
		raise IRError(f"{path}: {e}") from e


def parse(raw: dict[str, Any]) -> Document:
	ir_schema.upgrade(raw)
	return Document.model_validate(raw)


def json_schema() -> dict[str, Any]:
	"""The document's JSON Schema, with every feature payload, for docs and other tools."""
	out = Document.model_json_schema(by_alias=True)
	out["$defs"] = out.get("$defs", {})
	for _kind, model in PAYLOADS.items():
		out["$defs"].setdefault(model.__name__, model.model_json_schema())
	out["x-feature-payloads"] = {kind: f"#/$defs/{m.__name__}" for kind, m in PAYLOADS.items()}
	return out
