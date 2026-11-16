"""Stub of adsk.fusion: the classes the add-in tests with isinstance; any other name becomes an
empty class on first use (so isinstance checks against it are simply False)."""

from __future__ import annotations

from typing import Any


class SketchCurve:
	pass


class SketchLine(SketchCurve):
	pass


class SketchCircle(SketchCurve):
	pass


class SketchArc(SketchCurve):
	pass


class BRepBody:
	pass


class BRepFace:
	pass


class BRepEdge:
	pass


class Profile:
	pass


class Design:
	@staticmethod
	def cast(x: Any) -> Any:
		return x


def __getattr__(name: str) -> type:
	cls = type(name, (), {})
	globals()[name] = cls
	return cls
