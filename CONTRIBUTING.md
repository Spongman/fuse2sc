# Contributing

## Set up

```sh
python -m venv .venv
.venv/Scripts/activate           # macOS / Linux: source .venv/bin/activate
pip install -e ".[dev]"
python tools/check.py            # lint, formatting, types, consistency and tests
```

The golden tests build real designs in SolidCraft. They run when `SOLIDCRAFT_CLI` points at a
`solidcraft-cli` (or one is on `PATH`), and are skipped otherwise. CI always runs them, against
a pinned SolidCraft.

## Where things go

[docs/architecture.md](docs/architecture.md) describes the layers, what each may import (a test
enforces it), and the extension points. To support another Fusion feature type, follow
[docs/adding-a-feature.md](docs/adding-a-feature.md).

## Conventions

- **Code style:** `ruff check` and `ruff format` (tabs, line length 120), with `mypy` for the package and the add-in.
  `.editorconfig` gives editors the same rules: tabs, except YAML, TOML and Markdown.
  `python tools/check.py --fix` applies the fixes ruff can make.
- **Fusion add-in compatibility:** the add-in runs on Fusion's embedded Python, so it uses the
  standard library only.
  - It may use 3.10 syntax, but keep annotations lazy (`from __future__ import annotations`).
  - Read Fusion through the guards in `safe.py`.
- **The shared IR helpers:** `src/fuse2sc/ir` is copied into the add-in. Edit it there, then run
  `python tools/sync_addin.py`; a test fails if you forget.
- **Small parts behind small interfaces.**
  - The translator is pure.
  - The replay talks to SolidCraft only through `SolidCraftPort`, so new build logic can be
    tested with `tests/unit/fake_port.py`.
  - Tolerances belong in `replay/checks.Tolerances`, not in literals.
- **No silent loss:** anything not carried over must appear in the report, as an `unread`
  option, a note, a dropped command or a step status.
- **Tests for every fix:** a bug fix comes with the test that would have caught it. A new
  feature type comes with a unit test and a golden test whose result is known exactly.

## Releasing

See "Releasing" in the README: bump the version in the four places `tools/check_version.py`
checks, then push a `v*` tag.
