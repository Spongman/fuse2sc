"""Appearances and physical materials, reduced to what SolidCraft can show: a colour, whether it
reads as metal, roughness and opacity. Textures don't come across."""

from __future__ import annotations

from typing import TYPE_CHECKING

import adsk.core

from .places import body_key

if TYPE_CHECKING:
	import adsk.fusion

	from .places import Places
	from .records import Record

# Colour properties, most telling first, by words in their ids.
COLOR_HINTS = ("albedo", "diffuse", "color", "colour", "tint", "f0")
METAL_WORDS = (
	"metal",
	"steel",
	"alumin",
	"chrome",
	"brass",
	"bronze",
	"copper",
	"iron",
	"titanium",
	"zinc",
	"nickel",
	"gold",
	"silver",
	"anodized",
)
CLEAR_WORDS = ("glass", "clear", "transparent", "acrylic", "plexi", "polycarbonate - clear")


def look(app: adsk.core.Appearance) -> Record:
	name = app.name or ""
	low = name.lower()
	colors: list[tuple[str, tuple[int, int, int]]] = []
	floats: dict[str, float] = {}
	for p in app.appearanceProperties:
		try:
			if isinstance(p, adsk.core.ColorProperty) and p.value is not None:
				colors.append((p.id.lower(), (p.value.red, p.value.green, p.value.blue)))
			elif isinstance(p, adsk.core.FloatProperty):
				floats[p.id.lower()] = p.value
		except Exception:
			continue
	rgb = None
	for hint in COLOR_HINTS:
		hit = next((c for pid, c in colors if hint in pid), None)
		if hit is not None:
			rgb = hit
			break
	if rgb is None and colors:
		rgb = colors[0][1]
	rgb = rgb or (180, 180, 180)
	out: Record = {
		"name": name,
		"rgb": "#%02x%02x%02x" % tuple(int(c) for c in rgb),
		"metal": any(w in low for w in METAL_WORDS),
	}
	rough = next((v for k, v in floats.items() if "roughness" in k), None)
	if rough is not None:
		out["roughness"] = round(max(0.0, min(1.0, float(rough))), 3)
	transp = next((v for k, v in floats.items() if "transparency" in k or "opacity" in k), None)
	if transp is not None and "transparency" in next(k for k, v in floats.items() if v is transp):
		out["opacity"] = round(1.0 - max(0.0, min(1.0, float(transp))), 3)
	elif any(w in low for w in CLEAR_WORDS):
		out["opacity"] = 0.4
	return out


def read_appearance(design: adsk.fusion.Design, places: Places, limit_faces: int = 200) -> dict[str, Record]:
	out: dict[str, Record] = {}
	for comp in design.allComponents:
		for b in comp.bRepBodies:
			rec: Record = {}
			try:
				rec["look"] = look(b.appearance)
			except Exception:
				pass
			try:
				m = b.material
				dens = m.materialProperties.itemByName("Density")
				rec["material"] = {
					"name": m.name,
					"density": round(dens.value / 1000.0, 6) if dens else None,
				}  # kg/m³ -> g/cm³
			except Exception:
				pass
			faces: list[Record] = []
			try:
				base = b.appearance.name
				for f in b.faces:
					a = f.appearance
					if a is not None and a.name != base:
						faces.append({"at": places.point(f.pointOnFace, comp), "look": look(a)})
						if len(faces) >= limit_faces:
							break
			except Exception:
				pass
			if faces:
				rec["faces"] = faces
			out[body_key(b)] = rec
	return out
