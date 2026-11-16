"""Run every check CI runs, in one command, on any platform.

    python tools/check.py           lint, format, types, consistency, tests
    python tools/check.py --fix     let ruff fix and format first

The golden tests run too when SOLIDCRAFT_CLI (or solidcraft-cli on PATH) is available.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = ["src", "tools", "tests", "fusion_addin"]


def run(title: str, *args: str) -> bool:
	print(f"\n== {title}", flush=True)
	return subprocess.run([sys.executable, *args], cwd=ROOT, check=False).returncode == 0


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("--fix", action="store_true", help="apply ruff's fixes and formatting first")
	args = parser.parse_args()
	if args.fix:
		run("ruff fix", "-m", "ruff", "check", *CODE, "--fix")
		run("ruff format", "-m", "ruff", "format", *CODE)
	checks = [
		("ruff", "-m", "ruff", "check", *CODE),
		("formatting", "-m", "ruff", "format", "--check", *CODE),
		("types", "-m", "mypy"),
		("add-in types", "-m", "mypy", "-p", "fuse2sc_fusion", "-m", "Fuse2SC"),
		("the add-in's IR copy", "tools/sync_addin.py", "--check"),
		("versions", "tools/check_version.py"),
		("tests", "-m", "pytest", "-q"),
	]
	failed = [title for title, *cmd in checks if not run(title, *cmd)]
	print("\n" + ("all checks passed" if not failed else "FAILED: " + ", ".join(failed)))
	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(main())
