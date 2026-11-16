"""Check that every place stating fuse2sc's version agrees, and optionally matches a release tag.

python tools/check_version.py              print the version; exit 1 if the places disagree
python tools/check_version.py --tag v0.1.0 also require the tag to be that version
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _assigned(path: str, name: str) -> str | None:
	m = re.search(rf'^{name}\s*=\s*"([^"]+)"', (ROOT / path).read_text(encoding="utf-8"), re.M)
	return m.group(1) if m else None


def versions() -> dict[str, str | None]:
	return {
		"pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
		"src/fuse2sc/__init__.py": _assigned("src/fuse2sc/__init__.py", "__version__"),
		"fusion_addin/Fuse2SC/Fuse2SC.manifest": json.loads(
			(ROOT / "fusion_addin/Fuse2SC/Fuse2SC.manifest").read_text()
		)["version"],
		"fusion_addin/Fuse2SC/fuse2sc_fusion/reader.py": _assigned(
			"fusion_addin/Fuse2SC/fuse2sc_fusion/reader.py", "VERSION"
		),
	}


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--tag", help="a release tag such as v0.1.0")
	args = parser.parse_args()
	found = versions()
	if len(set(found.values())) != 1 or None in found.values():
		for where, v in found.items():
			print(f"{where:44} {v}")
		print("the versions disagree")
		return 1
	version = next(iter(found.values()))
	if args.tag and args.tag.removeprefix("v") != version:
		print(f"tag {args.tag} does not match version {version}")
		return 1
	print(version)
	return 0


if __name__ == "__main__":
	sys.exit(main())
