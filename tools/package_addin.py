"""Package the Fusion add-in as a zip for a release.

    python tools/package_addin.py [--out dist]

The zip holds one folder, Fuse2SC/, ready to add in Fusion (Shift+S > Add-Ins > + > pick Fuse2SC). The
add-in runs builds through an installed fuse2sc (`pip install fuse2sc`, or the release's wheel).
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_version  # noqa: E402
import sync_addin  # noqa: E402

SKIP = {"__pycache__", "config.json"}


def add_tree(z: zipfile.ZipFile, src: Path, arc_root: str) -> None:
	for path in sorted(src.rglob("*")):
		if path.is_file() and not SKIP & set(path.relative_to(src).parts) and path.suffix not in (".pyc", ".pyo"):
			z.write(path, f"{arc_root}/{path.relative_to(src).as_posix()}")


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--out", type=Path, default=ROOT / "dist")
	args = parser.parse_args()
	versions = set(check_version.versions().values())
	if len(versions) != 1:
		raise SystemExit("versions disagree: run tools/check_version.py")
	if problems := sync_addin.differs():
		raise SystemExit(f"the add-in's IR copy is stale ({'; '.join(problems)}): run tools/sync_addin.py")
	args.out.mkdir(parents=True, exist_ok=True)
	path = args.out / f"Fuse2SC-fusion-addin-{versions.pop()}.zip"
	with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
		add_tree(z, ROOT / "fusion_addin" / "Fuse2SC", "Fuse2SC")
		for name in ("LICENSE", "NOTICE"):
			z.write(ROOT / name, f"Fuse2SC/{name}")
	print(path)
	return 0


if __name__ == "__main__":
	sys.exit(main())
