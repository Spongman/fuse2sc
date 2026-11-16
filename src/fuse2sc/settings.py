"""The workspace: a folder (ideally its own private git repository) holding designs and reports,
marked by fusion-to-solidcraft.json. fuse2sc never writes into its own repository.

    <workspace>/fusion-to-solidcraft.json
    <workspace>/designs/<hub>/<project>/<folders>/<design>/
        timeline.json  manifest.json  final.step  final.stl  steps/  needed-steps.json
        <design>.sc  <design>.script.json  <design>.report.txt  <design>.build.json
    <workspace>/reports/  scorecard.md  scorecard.csv  index.html

Found from --workspace, then $FUSE2SC_WORKSPACE, then the current folder and its parents.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .replay.checks import Tolerances

MARKER = "fusion-to-solidcraft.json"
LFS_PATTERNS = ("f3d", "step", "stp", "stl", "sc", "glb")


class ExportSettings(BaseModel):
	projects: list[str] = []
	export_step: bool = True
	limit: int = 0


class RemoteSettings(BaseModel):
	"""A SolidCraft app on another computer, reached through its control channel
	(solidcraft/remote.py). Its token comes from $SOLIDCRAFT_CONTROL_TOKEN, never from this file."""

	address: str = ""  # host, host:port or [ipv6]:port (port 27878 by default); empty: build here
	paths: dict[str, str] = {}  # shared folder here -> the remote computer's name for it


class Settings(BaseModel):
	hub: str = ""
	designs_dir: str = "designs"
	reports_dir: str = "reports"
	solidcraft_cli: str = ""
	solidcraft_remote: RemoteSettings = RemoteSettings()
	export: ExportSettings = ExportSettings()
	versions: dict[str, str] = {}
	tolerances: Tolerances = Tolerances()


class WorkspaceError(RuntimeError):
	pass


@dataclass(frozen=True)
class Workspace:
	root: Path
	settings: Settings

	@property
	def designs(self) -> Path:
		return self.root / self.settings.designs_dir

	@property
	def reports(self) -> Path:
		return self.root / self.settings.reports_dir

	def design_dirs(self) -> Iterator[Path]:
		"""Every folder under designs/ holding a timeline.json, in order."""
		yield from sorted(
			p.parent
			for p in self.designs.rglob("timeline.json")
			if "steps" not in p.parent.parts and not any(x.startswith(".") for x in p.parent.parts)
		)


def load_workspace(root: Path) -> Workspace:
	try:
		return Workspace(root, Settings.model_validate_json((root / MARKER).read_text(encoding="utf-8")))
	except (OSError, ValidationError) as e:
		raise WorkspaceError(f"{root / MARKER}: {e}") from e


def find_workspace(explicit: Path | None = None, start: Path | None = None) -> Workspace:
	if explicit:
		if not (explicit / MARKER).is_file():
			raise WorkspaceError(f"{explicit} has no {MARKER}")
		return load_workspace(explicit.resolve())
	if env := os.environ.get("FUSE2SC_WORKSPACE"):
		return find_workspace(Path(env))
	here = (start or Path.cwd()).resolve()
	for d in (here, *here.parents):
		if (d / MARKER).is_file():
			return load_workspace(d)
	raise WorkspaceError(f"no {MARKER} here or above: run `fuse2sc init` in your workspace, or pass --workspace")


def init_workspace(root: Path, hub: str = "") -> Path:
	root.mkdir(parents=True, exist_ok=True)
	marker = root / MARKER
	if marker.exists():
		raise WorkspaceError(f"{marker} already exists")
	marker.write_text(Settings(hub=hub).model_dump_json(indent=2) + "\n", encoding="utf-8")
	attrs = root / ".gitattributes"
	if not attrs.exists():
		attrs.write_text(
			"# Large CAD files go through Git LFS (git lfs install, once per machine)\n"
			+ "".join(f"*.{ext} filter=lfs diff=lfs merge=lfs -text\n" for ext in LFS_PATTERNS),
			encoding="utf-8",
		)
	return marker


def user_config_dir() -> Path:
	"""Per-user state (the bridge's address and token); never inside a repository."""
	if os.name == "nt":
		return Path(os.environ.get("APPDATA") or Path.home()) / "fuse2sc"
	return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "fuse2sc"
