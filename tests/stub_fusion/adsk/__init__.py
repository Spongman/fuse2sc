"""A stand-in for Fusion's `adsk` package: just enough to import the add-in and run its
readers on hand-made objects. Any class the code names but this stub doesn't define is made
on first use, so `isinstance(x, adsk.fusion.Whatever)` is simply False."""

from __future__ import annotations

from . import core, fusion

__all__ = ["core", "fusion"]


def doEvents() -> None:
	pass
