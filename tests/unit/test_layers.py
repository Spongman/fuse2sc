"""The layering in docs/architecture.md, enforced: each layer imports only the layers below it."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "fuse2sc"

# layer -> fuse2sc modules it must not import (directly)
FORBIDDEN = {
	"ir": {"model", "translate", "replay", "solidcraft", "pipeline", "cli", "reports", "settings", "verify", "bridge"},
	"model": {"translate", "replay", "solidcraft", "pipeline", "cli", "reports", "settings", "verify", "bridge"},
	"translate": {"replay", "solidcraft", "pipeline", "cli", "reports", "settings", "verify", "bridge"},
	"replay": {"pipeline", "cli", "reports", "settings", "verify", "bridge"},
	"solidcraft": {"model", "translate", "replay", "pipeline", "cli", "reports", "settings", "verify", "bridge"},
	"reports": {"pipeline", "cli"},
	"pipeline": {"cli"},
}


def fuse2sc_imports(path: Path) -> set[str]:
	"""The top-level fuse2sc subpackages a module imports."""
	package = path.relative_to(SRC).parts[:-1]
	found = set()
	for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
		if isinstance(node, ast.ImportFrom) and node.level:
			base = list(package[: len(package) - node.level + 1])
			target = base + (node.module.split(".") if node.module else [])
			found |= {target[0]} if target else {alias.name for alias in node.names}
		elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("fuse2sc."):
			found.add(node.module.split(".")[1])
	return found


def layer_of(path: Path) -> str:
	rel = path.relative_to(SRC)
	return rel.parts[0] if len(rel.parts) > 1 else rel.stem


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(SRC)))
def test_layering(path):
	bad = fuse2sc_imports(path) & FORBIDDEN.get(layer_of(path), set())
	assert not bad, f"{path.relative_to(SRC)} imports {sorted(bad)}, which sits above it (docs/architecture.md)"


def test_the_import_reader_sees_relative_and_absolute_imports():
	assert {"translate", "solidcraft", "replay", "model"} <= fuse2sc_imports(SRC / "replay" / "engine.py")
	assert "replay" in fuse2sc_imports(SRC / "pipeline.py")
