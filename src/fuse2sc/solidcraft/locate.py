"""Finding solidcraft-cli and knowing its version."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

EXE = "solidcraft-cli.exe" if sys.platform == "win32" else "solidcraft-cli"


class CliNotFound(RuntimeError):
	pass


def _candidates(configured: str | None) -> Iterator[Path]:
	for c in (configured, os.environ.get("SOLIDCRAFT_CLI"), shutil.which("solidcraft-cli")):
		if c:
			yield Path(c)
	if sys.platform == "win32":
		for var in ("ProgramFiles", "LOCALAPPDATA"):
			if base := os.environ.get(var):
				yield Path(base, "SolidCraft", EXE)
				yield Path(base, "Programs", "SolidCraft", EXE)
	elif sys.platform == "darwin":
		yield Path("/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli")
		yield Path("/opt/homebrew/bin/solidcraft-cli")
	else:
		yield Path("/usr/bin/solidcraft-cli")
		yield Path.home() / ".local/bin/solidcraft-cli"
	yield Path.home() / ".cargo/bin" / EXE


def find_cli(configured: str | None = None) -> Path:
	"""The first solidcraft-cli that exists: configured, $SOLIDCRAFT_CLI, PATH, usual places."""
	for c in _candidates(configured):
		if c.is_file():
			return c.resolve()
	raise CliNotFound(
		'solidcraft-cli not found: put it on PATH, set SOLIDCRAFT_CLI, or set "solidcraft_cli" '
		"in fusion-to-solidcraft.json"
	)


@cache
def cli_version(cli: Path) -> str:
	out = subprocess.run(
		[str(cli), "--version"],
		check=False,
		capture_output=True,
		text=True,
		timeout=60,
		encoding="utf-8",
		errors="replace",
	)
	if m := re.search(r"\d+\.\d+\.\d+(?:[-+][\w.]+)?", out.stdout + out.stderr):
		return m.group(0)
	raise RuntimeError(f"could not read the version of {cli}: {(out.stdout + out.stderr)[:200]!r}")


def in_range(version: str, spec: str) -> bool:
	"""Whether a SolidCraft version satisfies a PEP 440 range such as ">=0.30,<0.43" (empty: any).
	Nightly suffixes are ignored: 0.42.0-nightly.5 counts as 0.42.0."""
	if not spec:
		return True
	base = re.match(r"\d+(\.\d+)*", version)
	try:
		return Version(base.group(0) if base else version) in SpecifierSet(spec)
	except InvalidVersion:
		return False
