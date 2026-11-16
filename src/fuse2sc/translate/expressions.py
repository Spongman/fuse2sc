"""Fusion expressions: kept as text, with the rewrites SolidCraft needs, and parameter ordering."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from fractions import Fraction
from graphlib import CycleError, TopologicalSorter
from typing import Any

from ..model import Document, Parameter

IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
QUOTED = re.compile(r'"[^"]*"')
NOT_PARAMETERS = frozenset(
	[
		"mm",
		"cm",
		"m",
		"in",
		"ft",
		"deg",
		"rad",
		"um",
		"km",
		"mi",
		"yd",
		"inch",
		"foot",
		"feet",
		"sin",
		"cos",
		"tan",
		"asin",
		"acos",
		"atan",
		"sqrt",
		"abs",
		"floor",
		"ceil",
		"round",
		"min",
		"max",
		"pi",
		"PI",
		"e",
		"log",
		"ln",
		"exp",
		"pow",
		"sign",
		"if",
		"and",
		"or",
		"not",
		"true",
		"false",
	]
)

# Fusion writes architectural lengths as 1'-6 1/2" (one foot six and a half inches).
ARCHITECTURAL = re.compile(
	r"""(?P<feet>\d+(?:\.\d+)?)\s*'\s*-\s*(?P<inches>\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*\""""
)
NAME_FIELDS = frozenset(
	{
		"name",
		"body",
		"bodies",
		"targets",
		"target",
		"tools",
		"component",
		"body_name",
		"material",
		"comment",
		"sketch",
		"path_sketch",
		"features",
		"items",
		"face_body",
		"path",
	}
)


def _inches(text: str) -> str:
	whole, _, frac = text.strip().partition(" ")
	if "/" in whole:
		return str(Fraction(whole))
	return f"({whole} + {Fraction(frac.strip())})" if frac else whole


def architectural(expr: str) -> str:
	"""1'-6 1/2" -> (1 ft + (6 + 1/2) in): Fusion reads the dash as "and", SolidCraft as minus."""
	return ARCHITECTURAL.sub(lambda m: f"({m['feet']} ft + {_inches(m['inches'])} in)", expr)


def rewrite(value: Any, key: str | None = None) -> Any:
	"""Apply the rewrites to every expression in command params (names are left alone)."""
	match value:
		case str() if key not in NAME_FIELDS and "'" in value:
			return architectural(value)
		case dict():
			return {k: rewrite(v, k) for k, v in value.items()}
		case list():
			return [rewrite(v, key) for v in value]
		case _:
			return value


def is_zero(expr: str | float | None) -> bool:
	"""True for no expression, or a literal zero in any unit ("0", "0.0 mm", "0 deg")."""
	if expr is None:
		return True
	if isinstance(expr, (int, float)):
		return expr == 0
	text = expr.strip()
	if not text:
		return True
	number = re.sub(r"\s*(mm|cm|m|in|ft|deg|rad|\"|')\s*$", "", text)
	try:
		return float(number) == 0.0
	except ValueError:
		return False


def names_in(expr: str | None) -> set[str]:
	"""Identifiers in an expression that could be parameters."""
	if not expr:
		return set()
	return {n for n in IDENTIFIER.findall(QUOTED.sub(" ", expr)) if n not in NOT_PARAMETERS}


def ordered(params: Iterable[Parameter]) -> list[Parameter]:
	"""Parameters, each after the parameters its expression uses. A cycle (Fusion wouldn't have
	one, but a hand-edited file might) keeps the remaining ones in their original order."""
	params = list(params)
	by_name = {p.name: p for p in params}
	graph = {p.name: names_in(p.expression) & by_name.keys() - {p.name} for p in params}
	try:
		order = list(TopologicalSorter(graph).static_order())
	except CycleError:
		done: list[str] = []
		for p in params:
			if p.name not in done:
				done.append(p.name)
		order = done
	return [by_name[n] for n in order]


def strings(value: Any) -> Iterator[str]:
	match value:
		case str():
			yield value
		case dict():
			for v in value.values():
				yield from strings(v)
		case list() | tuple():
			for v in value:
				yield from strings(v)


def needed_model_parameters(doc: Document) -> list[Parameter]:
	"""Model parameters (d1, d2…) used by user parameters or features, and those they use."""
	model = {p.name: p for p in doc.model_parameters}
	if not model:
		return []
	used = set().union(*(names_in(p.expression) for p in doc.parameters)) if doc.parameters else set()
	for s in doc.steps:
		for text in strings(s.feature or {}):
			used |= names_in(text)
		if s.sketch:
			used |= set().union(*(names_in(d.expr) for d in s.sketch.dimensions)) if s.sketch.dimensions else set()
	need: set[str] = set()
	todo = [n for n in used if n in model]
	while todo:
		n = todo.pop()
		if n not in need:
			need.add(n)
			todo += [m for m in names_in(model[n].expression) if m in model]
	return [p for name, p in model.items() if name in need]
