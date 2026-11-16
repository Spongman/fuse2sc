"""Fusion's curve and point ids as SolidCraft actually named them.

SolidCraft keeps the ids given to lines, circles and arcs but names splines and ellipses itself
(s1, p4…). After such a command the replay records, per sketch, Fusion id -> SolidCraft id, and
rewrites later references: the sketch's own constraints and dimensions, and the profiles, paths
and lines of features that name the sketch.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..translate.plan import Params

SKETCH_REF_PARAMS = frozenset({"a", "b", "line", "point", "entity", "entities", "points"})
FEATURE_REF_PARAMS = {"sketch": ("profiles", "line", "curves"), "path_sketch": ("path",)}


class Aliases:
	def __init__(self) -> None:
		self.by_sketch: dict[str, dict[str, str]] = defaultdict(dict)

	def record(self, sketch: str, wanted: dict[str, list[str]], reply: Any) -> None:
		created = reply.get("created", {}) if isinstance(reply, dict) else {}
		table = self.by_sketch[sketch]
		for kind in ("curves", "points"):
			want, got = wanted.get(kind) or [], created.get(kind) or []
			if want and len(got) >= len(want):
				table.update({a: b for a, b in zip(want, got, strict=False) if a != b})

	@staticmethod
	def _sub(table: dict[str, str], value: Any) -> Any:
		match value:
			case str() if value in table:
				return table[value]
			case str() if "." in value and value.split(".", 1)[0] in table:
				head, tail = value.split(".", 1)
				return f"{table[head]}.{tail}"
			case list():
				return [Aliases._sub(table, v) for v in value]
			case dict():
				return {k: Aliases._sub(table, v) for k, v in value.items()}
			case _:
				return value

	def in_sketch(self, sketch: str | None, params: Params) -> Params:
		table = self.by_sketch.get(sketch or "")
		if not table:
			return params
		return {k: self._sub(table, v) if k in SKETCH_REF_PARAMS else v for k, v in params.items()}

	def in_feature(self, params: Params) -> Params:
		out = dict(params)
		for sketch_key, keys in FEATURE_REF_PARAMS.items():
			name = params.get(sketch_key)
			table = self.by_sketch.get(name) if isinstance(name, str) else None
			if table:
				out.update({k: self._sub(table, out[k]) for k in keys if k in out})
		if isinstance(params.get("sections"), list):
			out["sections"] = [
				sec | {"profiles": self._sub(self.by_sketch[sec["sketch"]], sec["profiles"])}
				if sec.get("sketch") in self.by_sketch and "profiles" in sec
				else sec
				for sec in params["sections"]
			]
		return out
