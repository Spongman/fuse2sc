"""One-shot solidcraft-cli runs: measuring a saved design, exporting it, writing its script."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RunError(RuntimeError):
	pass


def _run(cli: Path, *args: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
	p = subprocess.run(
		[str(cli), *args],
		check=False,  # the return code is examined below, with SolidCraft's message
		capture_output=True,
		text=True,
		encoding="utf-8",
		errors="replace",
		timeout=timeout,
		creationflags=NO_WINDOW,
	)
	if p.returncode != 0:
		last = (p.stderr.strip().splitlines() or ["?"])[-1]
		raise RunError(f"solidcraft-cli {args[0]} failed: {last}")
	return p


def evaluate(cli: Path, design: Path, script: Path | None = None) -> dict[str, Any]:
	"""`solidcraft-cli eval`: bodies, params, sketches, timeline and totals of a design (after
	running `script` on it, without saving, when given)."""
	with tempfile.TemporaryDirectory(prefix="fuse2sc-eval-") as d:
		out = Path(d, "eval.json")
		args = [str(script), "--in", str(design)] if script else [str(design)]
		_run(cli, "eval", *args, "--json-out", str(out))
		return json.loads(out.read_text(encoding="utf-8"))


def write_script(cli: Path, design: Path, out: Path) -> None:
	"""The design's history as a command script that `solidcraft-cli run` replays."""
	_run(cli, "script", str(design), "--out", str(out), timeout=300)


def export(cli: Path, design: Path, suffix: str) -> bytes:
	"""The design exported in the format the suffix names (".glb", ".stl", ".step"...)."""
	with tempfile.TemporaryDirectory(prefix="fuse2sc-export-") as d:
		out = Path(d, "model" + suffix)
		_run(cli, "run", "--in", str(design), "--out", str(out), timeout=600)
		return out.read_bytes()
