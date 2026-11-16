"""The intermediate file (IR): one JSON document per Fusion design. See docs/ir.md.

Shape (schema 1)::

    document     {schema, tool, document, fusion, units, parameters, model_parameters,
                  components, steps, appearance, errors}
    component    {name, linked?, occurrences: [{path, parent, matrix, visible}]}
    step         {index, name, kind, suppressed, component, unread, snapshot, error?,
                  sketch? | feature? | occurrence? | placements?}
    snapshot     {"comp/body": {volume, area, com, bbox: [[min], [max]], visible}}

Units: lengths mm, areas mm², volumes mm³; matrices row-major 4x4 with the translation in mm.
Expressions are Fusion's own text. Points are world coordinates except sketch points (`xy`).

Stdlib only; copied into the Fusion add-in by tools/sync_addin.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TOOL_NAME = "fuse2sc"


class IRError(ValueError):
	pass


def new_document(document: str, tool_version: str) -> dict[str, Any]:
	return {
		"schema": SCHEMA_VERSION,
		"tool": {"name": TOOL_NAME, "version": tool_version},
		"document": document,
		"fusion": {},
		"units": "mm",
		"parameters": [],
		"model_parameters": [],
		"components": [],
		"steps": [],
		"appearance": {},
		"errors": [],
	}


def upgrade(doc: dict[str, Any]) -> dict[str, Any]:
	"""Bring an older document to the current schema in place (none older exist yet), and
	refuse one newer than this fuse2sc understands."""
	v = doc.get("schema")
	if not isinstance(v, int):
		raise IRError('not an fuse2sc timeline (no integer "schema")')
	if v > SCHEMA_VERSION:
		raise IRError(f"this file is schema {v}; this fuse2sc reads up to {SCHEMA_VERSION}: upgrade fuse2sc")
	for k, default in (
		("model_parameters", []),
		("components", []),
		("appearance", {}),
		("errors", []),
		("fusion", {}),
	):
		doc.setdefault(k, default)
	return doc


def validate(doc: dict[str, Any]) -> list[str]:
	"""Problems that make the document unusable (empty when it's fine)."""
	out = []
	for k, t in (("document", str), ("parameters", list), ("steps", list), ("components", list)):
		if not isinstance(doc.get(k), t):
			out.append(f"{k}: missing or not a {t.__name__}")
	if out:
		return out
	seen = set()
	for i, s in enumerate(doc["steps"]):
		if not isinstance(s, dict):
			out.append(f"steps[{i}]: not an object")
			continue
		for k in ("index", "name", "kind"):
			if k not in s:
				out.append(f"steps[{i}]: no {k}")
		if s.get("index") in seen:
			out.append(f"steps[{i}]: index {s.get('index')!r} repeated")
		seen.add(s.get("index"))
		snap = s.get("snapshot")
		if snap is not None and not isinstance(snap, dict):
			out.append(f"steps[{i}].snapshot: not an object")
	for p in doc["parameters"]:
		if not isinstance(p, dict) or "name" not in p or "expression" not in p:
			out.append("parameters: an entry without name and expression")
	return out


def load(path: str | Path) -> dict[str, Any]:
	doc = json.loads(Path(path).read_text(encoding="utf-8"))
	upgrade(doc)
	if problems := validate(doc):
		raise IRError(f"{path}: {'; '.join(problems[:5])}")
	return doc


def dump(doc: dict[str, Any], path: str | Path) -> None:
	"""Write atomically: a crash mid-write never leaves a half file."""
	target = Path(path).resolve()
	target.parent.mkdir(parents=True, exist_ok=True)
	fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=target.parent)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as f:
			json.dump(doc, f, indent=1, ensure_ascii=False)
		Path(tmp).replace(target)
	except BaseException:
		with contextlib.suppress(OSError):
			Path(tmp).unlink()
		raise


def final_bodies(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
	"""The bodies of the last step that has a snapshot."""
	for s in reversed(doc.get("steps", [])):
		if isinstance(s.get("snapshot"), dict):
			return s["snapshot"]
	return {}
