"""Vectors and rigid transforms (numpy). Matrices are row-major 4x4, translations in mm."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

IDENTITY = np.eye(4)


def mat(m: Sequence[float] | np.ndarray) -> np.ndarray:
	return np.asarray(m, dtype=float).reshape(4, 4)


def flat(m: np.ndarray, ndigits: int = 9) -> list[float]:
	return [round(float(v), ndigits) + 0.0 for v in np.asarray(m).reshape(-1)]


def relative(to: Sequence[float], frm: Sequence[float]) -> np.ndarray:
	"""The world move that takes placement `frm` to placement `to`: to · frm⁻¹."""
	return mat(to) @ np.linalg.inv(mat(frm))


def conjugate(m: Sequence[float], place: Sequence[float]) -> np.ndarray:
	"""A transform given in a placed component's space, as a world transform: P · M · P⁻¹."""
	p = mat(place)
	return p @ mat(m) @ np.linalg.inv(p)


def is_identity(m: Sequence[float] | np.ndarray, tol: float = 1e-9) -> bool:
	return bool(np.allclose(mat(m), IDENTITY, atol=tol))


def has_rotation(m: Sequence[float] | np.ndarray, tol: float = 1e-9) -> bool:
	return not np.allclose(mat(m)[:3, :3], np.eye(3), atol=tol)


def unit(v: Iterable[float]) -> list[float]:
	a = np.asarray(list(v), dtype=float)
	n = np.linalg.norm(a)
	return (a / n if n else a).tolist()


def dist(a: Iterable[float], b: Iterable[float]) -> float:
	return float(np.linalg.norm(np.asarray(list(a), float) - np.asarray(list(b), float)))


def totals(bodies: Mapping[str, Mapping[str, Any]], volume_key: str = "volume", com_key: str = "com") -> np.ndarray:
	"""[V, Mx, My, Mz]: total volume and its first moment about the origin."""
	out = np.zeros(4)
	for b in bodies.values():
		v, c = b.get(volume_key), b.get(com_key)
		if v and c is not None:
			out += np.concatenate(([v], v * np.asarray(c, float)))
	return out
