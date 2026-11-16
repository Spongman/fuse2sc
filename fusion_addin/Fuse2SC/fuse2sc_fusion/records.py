"""Type aliases for the plain data the add-in reads and writes.

Fusion's API objects are annotated with their adsk classes; these name the rest: the IR records
written to JSON (docs/ir.md), points and polylines in mm, and the loosely typed collections that
`safe.items()` reads.
"""

from __future__ import annotations

from typing import Any

Record = dict[str, Any]  # one IR object, as written to JSON
Point = list[float]  # [x, y, z] in world mm, or [x, y] in sketch mm
Polyline = list[Point]
Items = Any  # a Fusion collection, a Python-style vector, a list, a single object or None
