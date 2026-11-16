"""Reading Fusion's API without one bad read sinking a design.

- `items()` reads Fusion collections (count/item), Python-style vectors (BRepFaceVector and
  friends: len and indexing only) and single objects alike. Reading a vector as a collection
  silently returns nothing.
- `attempt()` runs one read; a failure becomes a note in `errors` instead of an exception.
- `get()` returns the first readable attribute of several names (Fusion's property names differ
  between object types and versions).
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar, overload

T = TypeVar("T")
D = TypeVar("D")  # the default, which may differ from what the read returns (None, [])


def items(c: Any) -> list[Any]:
	if c is None:
		return []
	if isinstance(c, (list, tuple)):
		return list(c)
	if hasattr(c, "count") and hasattr(c, "item"):
		try:
			return [c.item(i) for i in range(c.count)]
		except Exception:
			pass
	if hasattr(c, "__len__") and hasattr(c, "__getitem__"):
		try:
			return [c[i] for i in range(len(c))]
		except Exception:
			pass
	try:
		return list(c)
	except TypeError:
		return [c]


@overload
def attempt(errors: list[str], what: str, read: Callable[[], T]) -> T | None: ...


@overload
def attempt(errors: list[str], what: str, read: Callable[[], T], default: D) -> T | D: ...


def attempt(errors: list[str], what: str, read: Callable[[], T], default: D | None = None) -> T | D | None:
	try:
		return read()
	except Exception as e:
		first = (str(e) or type(e).__name__).strip().splitlines()[0]
		errors.append(f"{what}: {first[:300]}")
		return default


def get(obj: Any, *names: str, default: Any = None) -> Any:
	for name in names:
		try:
			value = getattr(obj, name)
		except Exception:
			continue
		if value is not None:
			return value
	return default
