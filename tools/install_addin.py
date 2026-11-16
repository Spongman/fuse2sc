"""Install the fuse2sc add-in into Fusion, from a checkout.

    python tools/install_addin.py [--copy] [--dest DIR]

Run it with the Python that has fuse2sc installed (your venv). It links (or with --copy, copies)
fusion_addin/Fuse2SC into Fusion's AddIns folder and writes Fuse2SC/config.json naming this Python, so
"Export to SolidCraft" can run builds. Then in Fusion: Shift+S > Add-Ins > Fuse2SC > Run, and tick
Run on Startup. On Windows the link is a directory junction (no administrator rights needed).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDIN = ROOT / "fusion_addin" / "Fuse2SC"
sys.path.insert(0, str(ROOT / "tools"))

import sync_addin  # noqa: E402


def addins_dir() -> Path:
	if sys.platform == "win32":
		return Path(os.environ["APPDATA"], "Autodesk", "Autodesk Fusion 360", "API", "AddIns")
	if sys.platform == "darwin":
		return Path.home() / "Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"
	raise SystemExit("Fusion runs on Windows and macOS only")


def remove(dest: Path) -> None:
	if dest.is_symlink() or getattr(dest, "is_junction", lambda: False)():
		dest.unlink()
	elif dest.exists():
		shutil.rmtree(dest)


def main() -> None:
	parser = argparse.ArgumentParser(description="Install the fuse2sc add-in into Fusion.")
	parser.add_argument("--copy", action="store_true", help="copy instead of linking")
	parser.add_argument("--dest", type=Path, help="AddIns folder (default: Fusion's)")
	args = parser.parse_args()
	if importlib.util.find_spec("fuse2sc") is None:
		raise SystemExit(f"fuse2sc is not installed in this Python ({sys.executable}): pip install -e . first")
	if sync_addin.differs():
		sync_addin.sync()
	(ADDIN / "config.json").write_text(json.dumps({"python": sys.executable}, indent=1), encoding="utf-8")
	dest = (args.dest or addins_dir()) / "Fuse2SC"
	dest.parent.mkdir(parents=True, exist_ok=True)
	remove(dest)
	if args.copy:
		shutil.copytree(ADDIN, dest)
		how = "copied"
	elif sys.platform == "win32":
		subprocess.check_call(["cmd", "/c", "mklink", "/J", str(dest), str(ADDIN)], stdout=subprocess.DEVNULL)
		how = "linked (junction)"
	else:
		dest.symlink_to(ADDIN, target_is_directory=True)
		how = "linked"
	print(
		f"{how} {ADDIN} -> {dest}\nbuilds will run with {sys.executable}\n"
		"In Fusion: Shift+S > Add-Ins > Fuse2SC > Run (tick Run on Startup); the commands are under "
		"Utilities > Add-Ins > SolidCraft."
	)


if __name__ == "__main__":
	main()
