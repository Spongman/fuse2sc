"""Fusion's own result at a timeline step, for steps SolidCraft can't rebuild the same.

A source returns STEP files of components as they are just after a step, each with the
component's world placement. Sources:

- StepsFolder: files the Fusion add-in exported earlier, listed in steps/index.json.
- the Fusion bridge (fuse2sc.bridge.Bridge): asks a running Fusion.
- Sources: tries several in turn and records what none had (needed-steps.json), so the add-in's
  "Export requested Fusion results" can export it for the next build.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class StepUnavailable(RuntimeError):
	pass


@dataclass(frozen=True, slots=True)
class ExportedStep:
	component: str
	path: Path
	matrix: list[float] | None = None


class StepSource(Protocol):
	async def export(self, index: int, components: list[str]) -> list[ExportedStep]: ...


@dataclass
class StepsFolder:
	folder: Path

	def _index(self) -> dict[str, list[dict[str, Any]]]:
		try:
			return json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
		except (OSError, ValueError):
			return {}

	async def export(self, index: int, components: list[str]) -> list[ExportedStep]:
		entries = self._index().get(str(index), [])
		have = {e["component"] for e in entries}
		if missing := set(components) - have:
			raise StepUnavailable(f"no exported result for step {index} ({', '.join(sorted(missing))})")
		out = []
		for e in entries:
			if e["component"] in components:
				p = Path(e["path"])
				p = p if p.is_absolute() else self.folder / p
				if not p.is_file():
					raise StepUnavailable(f"missing file {p}")
				out.append(ExportedStep(e["component"], p, e.get("matrix")))
		return out


@dataclass
class Sources:
	sources: list[StepSource] = field(default_factory=list)
	needed_path: Path | None = None
	missing: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))

	async def export(self, index: int, components: list[str]) -> list[ExportedStep]:
		why = []
		for s in self.sources:
			try:
				return await s.export(index, components)
			except StepUnavailable as e:
				why.append(str(e))
		self.missing[index].update(components)
		raise StepUnavailable(
			"; ".join(why) or "no source of Fusion's results (build from Fusion, or export the requested steps there)"
		)

	def write_needed(self) -> None:
		if not self.needed_path:
			return
		if not self.missing:
			self.needed_path.unlink(missing_ok=True)
			return
		steps = [{"index": i, "components": sorted(c)} for i, c in sorted(self.missing.items())]
		self.needed_path.write_text(json.dumps({"steps": steps}, indent=1), encoding="utf-8")
