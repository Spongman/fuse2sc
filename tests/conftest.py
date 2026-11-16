import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def solidcraft_cli() -> Path | None:
	c = os.environ.get("SOLIDCRAFT_CLI") or shutil.which("solidcraft-cli")
	return Path(c) if c and Path(c).is_file() else None


@pytest.fixture
def cli() -> Path:
	c = solidcraft_cli()
	if c is None:
		pytest.skip("needs solidcraft-cli (SOLIDCRAFT_CLI or PATH)")
	return c


@pytest.fixture
def sc_version(cli: Path) -> str:
	from fuse2sc.solidcraft import cli_version

	return cli_version(cli)


@pytest.fixture
def anyio_backend() -> str:
	return "asyncio"
