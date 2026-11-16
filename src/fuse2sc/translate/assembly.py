"""Components, placements, joints, linked designs, and the finishing commands (components,
copies, looks, visibility, plain body names)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import geometry as g
from ..ir.keys import component_of, plain_name
from ..model import Body, BodyAppearance, Look, Step
from .plan import Params

if TYPE_CHECKING:
	from .translator import Translator

LIBRARY_MATERIALS = frozenset(
	{
		"Default",
		"Steel",
		"Stainless Steel",
		"Aluminum",
		"Brass",
		"Copper",
		"Titanium",
		"Cast Iron",
		"ABS Plastic",
		"PLA",
		"Nylon",
		"Polycarbonate",
		"Oak",
		"Glass",
	}
)


def same_body(a: Body, b: Body) -> bool:
	if not (a.volume and b.volume and a.com is not None and b.com is not None):
		return False
	return abs(a.volume - b.volume) <= 1e-6 * a.volume + 1e-3 and g.dist(a.com, b.com) < 1e-3


def plan_regrouped(t: Translator, step: Step) -> None:
	"""A new component made from existing bodies: the same bodies under new keys."""
	after = t.after(step)
	gone = {k: b for k, b in t.prev.items() if k not in after}
	new = {k: b for k, b in after.items() if k not in t.prev}
	for old, ob in gone.items():
		match = next((k for k, nb in new.items() if same_body(ob, nb)), None)
		if match:
			t.emit("body.rename", {"body": old, "name": match})
			del new[match]


def plan_linked(t: Translator, step: Step) -> None:
	"""A design inserted from another file: linked to that design, converted on its own."""
	occ = step.occurrence
	if occ is None or not occ.linked:
		return t.fallback(step, "a linked occurrence without its design")
	m = occ.matrix or g.flat(g.IDENTITY)
	params = {"path": {"linked": occ.linked}, "name": occ.component, "translate": [m[3], m[7], m[11]]}
	t.emit("component.insert_design", params, occurrence_place=list(m) if g.has_rotation(m) else None)


def plan_joint(t: Translator, step: Step) -> None:
	"""A joint moves components: their bodies move by the change in their first placement."""
	pl = step.placements
	if pl is None:
		return t.fallback(step, "joint without placements")
	first = {c.name: c.occurrences[0].path for c in t.doc.components if c.occurrences}
	moved = False
	for comp, path in first.items():
		before, after = pl.before.get(path), pl.after.get(path)
		if before is None or after is None or g.is_identity(g.relative(after, before)):
			continue
		bodies = [k for k in t.prev if component_of(k) == comp]
		if bodies:
			t.emit("solid.move", {"bodies": bodies, "matrix": g.flat(g.relative(after, before))})
			moved = True
	if not moved and t.changed_bodies(step):
		t.fallback(step, "a joint that moves bodies other than its components'")


def finish(t: Translator, final: dict[str, Body]) -> None:
	doc = t.doc
	skip = t.plan.linked
	comps = [c for c in doc.components if c.name not in skip]
	for c in comps:
		bodies = [k for k in final if component_of(k) == c.name]
		t.emit("component.create", {"name": c.name, "parent": 0, "activate": False})
		if bodies:
			t.emit("component.move_bodies", {"bodies": bodies, "component": c.name})
	for c in comps:
		if len(c.occurrences) < 2 or not any(component_of(k) == c.name for k in final):
			continue
		first = c.occurrences[0].matrix
		for occ in c.occurrences[1:]:
			rel = g.flat(g.relative(occ.matrix, first))
			t.emit("occurrence.copy", {"component": c.name}, occurrence_move=rel)
	appearance(t, final, skip)
	hidden_comps = {c.name for c in doc.components for o in c.occurrences if not o.visible}
	hidden = [
		k
		for k, b in final.items()
		if (not b.visible or component_of(k) in hidden_comps) and component_of(k) not in skip
	]
	if hidden:
		t.emit("browser.visibility", {"items": hidden, "visible": False}, optional=True)
	plain = [plain_name(k) for k in final]
	for k in final:
		name = plain_name(k)
		if name != k and plain.count(name) == 1 and component_of(k) not in skip:
			t.emit("body.rename", {"body": k, "name": name}, optional=True, final_name=True)


def look_params(look: Look) -> Params:
	p: Params = {"color": look.rgb, "name": look.name}
	if look.opacity is not None and look.opacity < 1:
		p["opacity"] = look.opacity
	elif look.metal:
		p["pbr"] = {
			"metallic": 1.0,
			"roughness": max(0.045, look.roughness if look.roughness is not None else 0.4),
			"dielectric_ior": 1.5,
		}
	return p


def appearance(t: Translator, final: dict[str, Body], skip: set[str]) -> None:
	looks: dict[str, BodyAppearance] = {
		k: a for k, a in t.doc.appearance.items() if k in final and component_of(k) not in skip
	}
	by_material: dict[str, list[str]] = {}
	for k, a in looks.items():
		if a.material:
			by_material.setdefault(a.material.name, []).append(k)
	made = set()
	for a in looks.values():
		m = a.material
		if m and m.name not in LIBRARY_MATERIALS and m.name not in made and m.density:
			t.emit("material.create", {"name": m.name, "density_g_cm3": m.density}, optional=True)
			made.add(m.name)
	for name, bodies in by_material.items():
		t.emit("material.assign", {"bodies": bodies, "material": name}, optional=True)
	by_look: dict[str, tuple[Look, list[str]]] = {}
	for k, a in looks.items():
		if a.look:
			by_look.setdefault(a.look.model_dump_json(), (a.look, []))[1].append(k)
	for look, bodies in by_look.values():
		t.emit("appearance.assign", look_params(look) | {"bodies": bodies}, optional=True)
	for k, a in looks.items():
		for face in a.faces:
			t.emit("appearance.assign", look_params(face.look) | {"faces": [list(face.at)], "body": k}, optional=True)
