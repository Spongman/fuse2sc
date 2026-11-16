"""Moving Fusion's timeline marker, always putting it back.

A feature's inputs exist only before the feature (a fillet consumes its edges); its result only
after. `marker_at(design, i, before=True)` rolls to just before step i, `before=False` to just
after, and restores the user's marker when the block ends, whatever happens inside. The document
is never saved.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from collections.abc import Iterator

	import adsk.fusion


def _restore(tl: adsk.fusion.Timeline, marker: int) -> None:
	try:
		if marker >= tl.count:
			tl.moveToEnd()
		else:
			tl.markerPosition = marker
	except Exception:
		pass


@contextlib.contextmanager
def preserved_marker(design: adsk.fusion.Design) -> Iterator[adsk.fusion.Timeline]:
	tl = design.timeline
	marker = tl.markerPosition
	try:
		yield tl
	finally:
		_restore(tl, marker)


def roll(design: adsk.fusion.Design, index: int, before: bool) -> None:
	"""Put the marker just before (True) or just after (False) step `index`."""
	design.timeline.item(index).rollTo(before)


@contextlib.contextmanager
def marker_at(design: adsk.fusion.Design, index: int, before: bool) -> Iterator[None]:
	with preserved_marker(design):
		roll(design, index, before)
		yield
