"""Running `fuse2sc build` from Fusion, as its own process.

The build runs outside Fusion, so a long build or a SolidCraft crash can't take Fusion down, and
it is the same code that runs from a terminal. It needs fuse2sc installed in a Python of its own
(fuse2sc uses libraries Fusion's embedded Python doesn't have): tools/install_addin.py records that
Python in config.json; otherwise an `fuse2sc` on PATH (e.g. installed with pipx) is used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
	from .bridge import Bridge
	from .records import Record

ADDIN = Path(__file__).resolve().parent.parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def config() -> dict[str, Any]:
	try:
		return json.loads((ADDIN / "config.json").read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return {}


def fuse2sc_command() -> list[str]:
	"""How to run fuse2sc: the Python the installer recorded, or fuse2sc on PATH."""
	python = config().get("python")
	if python and Path(python).is_file():
		return [python, "-m", "fuse2sc"]
	if exe := shutil.which("fuse2sc"):
		return [exe]
	raise RuntimeError(
		"fuse2sc is not installed for the add-in to run: `pip install fuse2sc` (or `pipx install fuse2sc`), "
		"or run tools/install_addin.py from your fuse2sc checkout"
	)


class Build:
	"""One build process; `on_event(dict)` is called from a reader thread for each JSON line."""

	def __init__(
		self,
		timelines: list[Path],
		outdir: Path,
		*,
		cli: str | None,
		bridge: Bridge | None = None,
		on_event: Callable[[Record], None] | None = None,
		extra: list[str] | None = None,
	) -> None:
		args = fuse2sc_command()
		if cli:
			args += ["--cli", cli]
		args += ["build", *map(str, timelines), "--outdir", str(outdir), "--progress-json", "--force", *(extra or [])]
		env = dict(os.environ, PYTHONIOENCODING="utf-8")
		if bridge is not None:
			env.update(FUSE2SC_BRIDGE_URL=bridge.url, FUSE2SC_BRIDGE_TOKEN=bridge.token)
		self.on_event = on_event or (lambda e: None)
		self.stderr: list[str] = []
		self.proc = subprocess.Popen(
			args,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			env=env,
			text=True,
			encoding="utf-8",
			errors="replace",
			creationflags=NO_WINDOW,
		)
		threading.Thread(target=self._read_events, daemon=True).start()
		threading.Thread(target=self._read_stderr, daemon=True).start()

	def _read_stderr(self) -> None:
		assert self.proc.stderr is not None  # stderr=PIPE
		self.stderr.extend(self.proc.stderr)

	def _read_events(self) -> None:
		assert self.proc.stdout is not None  # stdout=PIPE
		for line in self.proc.stdout:
			if line := line.strip():
				try:
					self.on_event(json.loads(line))
				except ValueError:
					self.on_event({"event": "message", "text": line})
		self.on_event({"event": "exit", "code": self.proc.wait(), "stderr": "".join(self.stderr[-20:])})

	def cancel(self) -> None:
		if self.proc.poll() is None:
			self.proc.terminate()
