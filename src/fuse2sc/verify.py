"""Independent verification: a saved .sc re-measured with `solidcraft-cli eval` against Fusion's
numbers, with differences explained where possible.

- Hidden Fusion bodies that came across visible: the volume matches Fusion's all-bodies total.
- Linked designs missing: the volume falls short and the design references others.
- Open bodies: SolidCraft can't give them a volume.
- Merged or split bodies: the count differs but the volume matches.
- Parameters missing, or with a different expression (a unit rewrite like 0.75" -> 19.05 mm counts
  as the same).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from .model import Document
from .model import load as load_ir
from .replay.results import Verification
from .solidcraft import runner

LENGTH_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
ANGLE_DEG = {"deg": 1.0, "rad": 180.0 / np.pi}
LITERAL = re.compile(r"(?P<n>[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)\s*(?P<u>[a-z]*)", re.I)


def literal_value(expr: str) -> tuple[str, float] | None:
	"""('length', mm) or ('angle', deg) for a plain literal like '0.75 in' or '45°'; else None."""
	text = (expr or "").strip().replace('"', " in").replace("°", " deg").replace("'", " ft")
	if not (m := LITERAL.fullmatch(text)):
		return None
	value, unit = float(m["n"]), (m["u"] or "mm").lower()
	if unit in LENGTH_MM:
		return "length", value * LENGTH_MM[unit]
	if unit in ANGLE_DEG:
		return "angle", value * ANGLE_DEG[unit]
	return None


def same_expression(a: str, b: str) -> bool:
	if re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or ""):
		return True
	va, vb = literal_value(a), literal_value(b)
	return bool(va and vb and va[0] == vb[0] and np.isclose(va[1], vb[1], rtol=1e-6, atol=1e-9))


def reference(design_dir: Path, doc: Document | None = None) -> dict[str, Any] | None:
	"""Fusion's numbers: manifest.json when there is one, else the timeline's last snapshot."""
	manifest = design_dir / "manifest.json"
	if manifest.is_file():
		m = json.loads(manifest.read_text(encoding="utf-8"))
		bodies = [b for b in m.get("bodies", []) if b.get("solid", True) and b.get("volume") is not None]
		return {
			"source": "manifest",
			"bodies": bodies,
			"parameters": m.get("parameters", []),
			"references": m.get("references", []),
		}
	if doc is None:
		tl = design_dir / "timeline.json"
		if not tl.is_file():
			return None
		doc = load_ir(tl)
	bodies = [b.model_dump() | {"key": k} for k, b in doc.final_bodies().items() if b.volume is not None]
	return {
		"source": "timeline",
		"bodies": bodies,
		"parameters": [p.model_dump() for p in doc.parameters],
		"references": sorted({c.linked for c in doc.components if c.linked}),
	}


def overall_box(boxes: Iterable[Any]) -> np.ndarray | None:
	arrays = [np.asarray(b, float) for b in boxes if b is not None]
	if not arrays:
		return None
	return np.array([np.min([b[0] for b in arrays], axis=0), np.max([b[1] for b in arrays], axis=0)])


def verify(
	design_dir: Path, sc_path: Path, cli: Path, tol_rel: float = 1e-3, doc: Document | None = None
) -> Verification:
	ref = reference(design_dir, doc)
	if ref is None:
		return Verification(ok=None, notes=["no reference: no manifest.json or timeline.json"])
	try:
		got = runner.evaluate(cli, sc_path)
	except (runner.RunError, OSError) as e:
		return Verification(ok=False, notes=[f"could not evaluate {sc_path.name}: {e}"])
	bodies = got.get("bodies", [])
	vol = sum(b["volume_mm3"] for b in bodies if b.get("volume_mm3") is not None)
	open_bodies = [b["name"] for b in bodies if b.get("closed") is False or b.get("volume_mm3") is None]
	visible = [b for b in ref["bodies"] if b.get("visible", True)]
	vol_vis, vol_all = sum(b["volume"] for b in visible), sum(b["volume"] for b in ref["bodies"])

	def rel(want: float) -> float:
		return abs(vol - want) / want if want else (0.0 if vol == 0 else 1.0)

	err_vis, err_all = rel(vol_vis), rel(vol_all)
	volume_ok = min(err_vis, err_all) <= tol_rel
	notes = []
	if not volume_ok:
		if ref["references"] and vol < vol_vis:
			notes.append(
				f"volume short of Fusion's: linked designs ({', '.join(ref['references'][:3])}) may be missing"
			)
		else:
			notes.append(f"volume {vol:.1f} mm3, Fusion {vol_vis:.1f} (visible) / {vol_all:.1f} (all)")
	elif err_all < err_vis and vol_all != vol_vis:
		notes.append("hidden Fusion bodies came across (the volume matches Fusion's all-bodies total)")
	if open_bodies:
		notes.append(f"open bodies (no volume): {', '.join(open_bodies[:5])}")
	count_ok = len(bodies) in (len(visible), len(ref["bodies"]))
	if not count_ok:
		notes.append(
			f"{len(bodies)} bodies, Fusion {len(visible)}" + (" (volume matches: merged or split)" if volume_ok else "")
		)
	want_box = overall_box(b.get("bbox") for b in visible)
	have_box = overall_box((b["bbox"]["min"], b["bbox"]["max"]) for b in bodies if isinstance(b.get("bbox"), dict))
	box_diff = None
	if want_box is not None and have_box is not None:
		box_diff = float(np.max(np.abs(want_box - have_box)))
		if box_diff > 0.5 + 0.01 * float(np.linalg.norm(want_box[1] - want_box[0])):
			notes.append(f"overall bounding box differs by {box_diff:.2f} mm")
	params = {p["name"]: p for p in got.get("params", []) if not p.get("model")}
	missing = [p["name"] for p in ref["parameters"] if p["name"] not in params]
	changed = [
		p["name"]
		for p in ref["parameters"]
		if p["name"] in params and not same_expression(p.get("expression", ""), params[p["name"]].get("expression", ""))
	]
	if missing:
		notes.append(f"parameters missing: {', '.join(missing[:8])}")
	if changed:
		notes.append(f"parameters changed: {', '.join(changed[:8])}")
	errors = [t["name"] for t in got.get("timeline", []) if t.get("error")]
	if errors:
		notes.append(f"timeline features with errors: {', '.join(errors[:8])}")
	return Verification(
		ok=volume_ok and count_ok and not open_bodies and not errors,
		reference=ref["source"],
		volume_mm3=round(vol, 3),
		fusion_volume_visible=round(vol_vis, 3),
		fusion_volume_all=round(vol_all, 3),
		volume_rel_err=round(min(err_vis, err_all), 6),
		bodies=len(bodies),
		fusion_bodies=len(visible),
		bbox_max_diff_mm=None if box_diff is None else round(box_diff, 3),
		parameters_missing=missing,
		parameters_changed=changed,
		timeline_errors=errors,
		notes=notes,
	)
