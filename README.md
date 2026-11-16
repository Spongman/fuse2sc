# fuse2sc: Fusion to SolidCraft, with history

fuse2sc moves Autodesk Fusion designs into [SolidCraft](https://github.com/tangency-labs/solidcraft)
(open-source parametric CAD) and keeps their **parametric history**: parameters with their
expressions, sketches with constraints and dimensions, and features. Every step is checked against
Fusion's own numbers as it is built. Each design gets a report of what came across.

## How it works

```
 Fusion add-in (reads only)               fuse2sc (outside Fusion)                         SolidCraft
 ─────────────────────────────────        ───────────────────────────────────────       ──────────────────
 each step: inputs read before it,  ──►  timeline.json ──► translate (pure) ──► plan
   every body measured after it          replay step by step over MCP  ──────────────►  solidcraft-cli mcp
 Fusion's result at a step          ◄──  asked for when a step can't match               (headless)
   (STEP, on demand, via the bridge)     check every body; substitute Fusion's result
                                         save, re-measure, report ─────────────────────►  design.sc
```

1. **Extract.** The add-in walks the timeline:
   - It reads each feature's inputs with the marker just before it, so the edges a fillet
     consumes still exist.
   - It records every body's volume, area, centre of mass and box just after each step.
   - All of this goes into one JSON file per design, the [IR](docs/ir.md).
2. **Translate.** A pure function turns the IR into SolidCraft commands. Expressions stay as
   Fusion wrote them, so parameters keep driving the model.
3. **Replay and check.** The commands run in a headless SolidCraft, one Fusion step at a time:
   - A refused command is retried in other ways: regions picked by points, the axis reversed, an
     extrude split into parts, a fillet split into edge groups.
   - A sketch constraint that would move the drawing is taken back.
   - New bodies get Fusion's names, matched by geometry.
   - After each step every body is compared with Fusion's.
4. **Fall back per step.** A step with no SolidCraft equivalent, or one whose bodies don't match,
   comes in as Fusion's own result (STEP) for those bodies only. Later steps keep building with
   history on top of it.
5. **Verify and report.** The saved file is re-measured. Each design gets a category and a
   report covering every step. A workspace gets a scorecard ranking the feature types that most
   often stop history.

Fusion only reads and exports. Building, checking and reporting run in a separate process, so a
long build never freezes Fusion, and the same code runs from a terminal or CI.

## Status

Early. The SolidCraft side is tested; the Fusion add-in has not yet been run inside Fusion.

- **Tested against SolidCraft 0.42.0** (157 tests):
  - golden designs with exact volumes, centres and boxes: a lidded box (extrude, shell, cut, lid,
    knob), fillets, chamfers, a revolve on XZ, extrude direction on XZ, two-sided extrude, a hole,
    a draft, a thin extrude, ellipse and spline profiles, and a parameter change after saving;
  - the safety nets: constraint rollback, fillet splitting, fallback to Fusion's result,
    `needed-steps.json`;
  - the session against a fake MCP server that hangs, crashes and stalls;
  - the command line end to end.
- **The add-in** has been exercised against a stub of Fusion's API only:
  - it uses property names from Fusion's API reference that are not yet confirmed in Fusion;
  - each read is guarded, so a wrong name costs that feature (it falls back to Fusion's result, or
    is listed as an option not carried over), not the export.

## Requirements

- **Autodesk Fusion**, signed in to the account that owns the designs. The add-in uses Fusion's
  built-in API with your existing sign-in, so there are no API keys or OAuth apps to set up.
- **[SolidCraft](https://github.com/tangency-labs/solidcraft/releases)**, which provides
  `solidcraft-cli`. It's open source, with no account. Put it on `PATH` or set `SOLIDCRAFT_CLI`.
- **Python 3.11+ for fuse2sc itself.**
  - Libraries: `mcp` (the official MCP SDK), pydantic, numpy, typer, rich, jinja2, httpx.
  - The add-in runs on Fusion's own Python with the standard library only. It runs builds through
    your fuse2sc install.

The only secret involved is generated for you: the optional bridge's token. The bridge lets a build
ask a running Fusion for a step's result. It listens on `127.0.0.1` only, runs a fixed set of
operations (never code it is sent), and the add-in hands its token to the build.

## Install

```sh
python -m venv .venv
.venv/Scripts/activate              # macOS: source .venv/bin/activate
pip install -e ".[dev]"             # or, from a release: pip install fuse2sc-<version>-py3-none-any.whl
python tools/install_addin.py       # links the add-in into Fusion and tells it to use this Python
fuse2sc doctor
```

In Fusion, press **Shift+S**, open **Add-Ins**, pick **Fuse2SC**, click **Run** and tick **Run on
Startup**. The commands are under **Utilities > Add-Ins > SolidCraft**.

From a release you can instead unzip `Fuse2SC-fusion-addin-<version>.zip` and add its `Fuse2SC` folder
with **+**. The add-in then uses an `fuse2sc` on `PATH` (for example installed with `pipx`).

## Use

### One design, one click

Open a parametric design. Click **SolidCraft > Export to SolidCraft…** and choose where to save the
`.sc`.

The design and the designs it links to are read, then built outside Fusion. A status panel shows
each step, and Fusion stays usable meanwhile. A step that can't be rebuilt asks Fusion for its
result through the bridge, live. Next to the `.sc` you get:

| File | What it is |
|---|---|
| `<name>.report.txt` | what became of every Fusion step |
| `<name>.build.json` | the same, for machines |
| `<name>.script.json` | the design's history as commands (`solidcraft-cli run` replays it) |
| `<name>.viewer.html` | Fusion and SolidCraft side by side in 3D |

### Many designs: a workspace

A workspace is a folder, ideally its own private git repository, holding your designs and reports.

```sh
fuse2sc init my-designs --hub "My Hub"
```

1. In Fusion, run **SolidCraft > Export to workspace…**. Choose the whole hub (resumable: designs
   already exported at their current version are skipped) or the open design. Each design gets a
   folder with `timeline.json`, `manifest.json`, `final.step` and `final.stl`.
2. In the workspace, run `fuse2sc build`. Every design is built and verified, then
   `reports/scorecard.md`, `reports/scorecard.csv` and `reports/index.html` are written.
3. Designs that needed Fusion's result for a step leave a `needed-steps.json`. In Fusion, run
   **SolidCraft > Export requested Fusion results**, then `fuse2sc build` again. Or switch the
   **SolidCraft bridge** on in Fusion and run `fuse2sc build --bridge` to ask live.

`fusion-to-solidcraft.json` holds the workspace settings:

```json
{
  "hub": "My Hub",
  "designs_dir": "designs",
  "reports_dir": "reports",
  "solidcraft_cli": "",
  "solidcraft_remote": {"address": "", "paths": {}},
  "export": {"projects": [], "export_step": true, "limit": 0},
  "versions": {"solidcraft-cli": "0.42.0"},
  "tolerances": {"volume_rel": 0.001, "com_abs": 0.02, "bbox_abs": 0.5}
}
```

`versions` pins the SolidCraft you converted with: `fuse2sc build` warns when another is installed
(`--strict-versions` stops). Use Git LFS for the CAD files; `fuse2sc init` writes the
`.gitattributes`.

### Command line

| Command | What it does |
|---|---|
| `fuse2sc doctor` | checks Python, `solidcraft-cli` and its MCP server (or the remote app), the workspace and the bridge |
| `fuse2sc inspect timeline.json` | what would translate, with no SolidCraft needed (`--plan-out plan.json`) |
| `fuse2sc build [DESIGN…]` | builds designs: all of the workspace's by default |
| `fuse2sc verify [DESIGN…]` | re-measures saved designs against Fusion's numbers and explains differences |
| `fuse2sc report` | writes the workspace scorecard and index |
| `fuse2sc viewer [DESIGN…]` | writes the 3D side-by-side page |
| `fuse2sc schema` | prints the IR's JSON Schema |

`build` options:
- `--only TEXT`, `--force`, `-o OUT.sc`, `--outdir DIR`
- `--jobs N` builds designs in parallel, each in its own SolidCraft
- `--bridge` asks a running Fusion for steps' results
- `--until-step N` and `--skip-steps 12,40-45`
- `--find-divergence` stops at the first step that differs from Fusion
- `--no-check`, `--no-guard`, `--no-verify`, `--viewer`

### SolidCraft on another computer

By default each build runs its own headless SolidCraft here. To build in a SolidCraft app on
another computer (a sandbox, say), open the app's control channel there (Preferences › AI, or
`solidcraft --control 27878`) and point fuse2sc at it:

```sh
export SOLIDCRAFT_CONTROL_TOKEN=...    # the app's control token (its token file on that computer)
fuse2sc --remote 192.0.2.10 --path-map "C:/work/designs=/mnt/designs" doctor
fuse2sc --remote 192.0.2.10 --path-map "C:/work/designs=/mnt/designs" build
```

- fuse2sc still needs a local `solidcraft-cli` of the app's version: `solidcraft-cli mcp --connect`
  carries the session to the app, and `verify` and the viewer run locally on the saved design.
- The app opens Fusion's STEP results and saves the design on its own computer, so the design
  folders must be shared. `--path-map LOCAL=REMOTE` (repeatable) says what the remote computer
  calls a shared folder; paths under none are sent unchanged.
- Each build starts with a new design in the app, replacing whatever it had open.
- One design at a time: `--jobs` is ignored.
- In a workspace, `"solidcraft_remote": {"address": "192.0.2.10", "paths": {"designs": "/mnt/designs"}}`
  does the same (local paths relative to the workspace). The token is never stored there.

### Results

Each design gets a category:

| Category | Meaning |
|---|---|
| ✅ `full` | every step rebuilt with history, and the result matches Fusion |
| 🟡 `partial` | history kept; some steps came in as Fusion's result |
| 🔶 `mismatch` | built, but something differs from Fusion (the report says where it started) |
| 🟠 `geometry` | Fusion's shape only (designs with no timeline) |
| ⚪ `empty` | Fusion has no bodies |
| ❌ `failed` | nothing was saved |

Each step gets a status:

| Status | Meaning |
|---|---|
| `exact` | rebuilt as is |
| `adjusted` | rebuilt another way (a constraint left out, a fillet split, the other orientation) |
| `substituted` | Fusion's result brought in |
| `mismatch` | differs from Fusion |
| `failed` | refused by SolidCraft |
| `skipped` | suppressed or construction-only |

## What carries over

| Fusion | SolidCraft |
|---|---|
| User parameters, and the model parameters they use | parameters, with Fusion's expressions |
| Sketches | lines, arcs, circles, ellipses, fit-point and cubic control-point splines; other curves as polylines |
| Sketch constraints, driving dimensions | each added, and taken back if it would move the sketch |
| Extrude | distance, two sides, symmetric, taper, start offset, to/from object, through all, thin |
| Revolve, sweep (with twist), loft (no rails) | ✓ |
| Fillet, chamfer | constant, chord, simple variable; equal distance, two distances, distance and angle |
| Shell, draft, offset faces, hole (simple, counterbore, countersink) | ✓ |
| Combine, move, scale, remove, split body, copy/cut-paste | ✓ |
| Mirror, rectangular and circular pattern | bodies or features |
| Components, multiple placements, joints (as moves), linked designs (with rotation) | ✓ |
| Appearances, physical materials, visibility | colour, metal, opacity, density |
| Anything else | Fusion's result for that step; history continues after it |

Anything not carried over is reported, never dropped silently.

## Development

```sh
pip install -e ".[dev]"
python tools/check.py            # ruff, formatting, mypy, consistency checks and tests
python tools/check.py --fix      # let ruff fix and format first
```

The golden tests run when `SOLIDCRAFT_CLI` points at a `solidcraft-cli`.

- **[docs/architecture.md](docs/architecture.md)**: the layers, what each may import (enforced by
  a test), and the extension points.
- **[docs/adding-a-feature.md](docs/adding-a-feature.md)**: supporting another Fusion feature type
  (an add-in reader, an IR model, a translator handler, and their tests).
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: conventions.

The tests:

- `tests/unit`: the models, translator, replay building blocks (with property tests), the build's
  control flow against an in-memory SolidCraft, the MCP session against a misbehaving server,
  registry and layering checks, reports and the CLI.
- `tests/stub_fusion`: the add-in on a stub of Fusion's API.
- `tests/golden`: real SolidCraft builds against exact geometry, and every SolidCraft command fuse2sc
  uses checked against SolidCraft's catalogue.

### Releasing

CI runs lint, the unit tests on Windows, macOS and Linux (Python 3.11 to 3.13), and the golden
tests on Windows against a pinned, checksum-verified SolidCraft.

To release:
1. Bump the version in `pyproject.toml`, `src/fuse2sc/__init__.py`, `fusion_addin/Fuse2SC/Fuse2SC.manifest`
   and `fusion_addin/Fuse2SC/fuse2sc_fusion/reader.py`. `python tools/check_version.py` checks they agree.
2. Push a tag: `git tag v0.1.0 && git push origin v0.1.0`.

The release workflow then checks the tag and runs CI. It builds the wheel, the sdist and the
add-in zip, with checksums, and publishes a GitHub release. PyPI publishing is off until you set
the repository variable `PUBLISH_PYPI=true` and register the repository as a trusted publisher.

## Layout

```
src/fuse2sc/ir/          IR helpers shared with the add-in (stdlib only)
src/fuse2sc/model.py     the IR as pydantic models
src/fuse2sc/translate/   IR -> SolidCraft commands (pure)
src/fuse2sc/replay/      the build: session, per-step checks, names, regions, sources, strategies
src/fuse2sc/solidcraft/  finding solidcraft-cli, the MCP session, one-shot runs
src/fuse2sc/reports/     jinja2 templates: report, scorecard, index, 3D viewer
src/fuse2sc/pipeline.py  building one design or many; cli.py the command line
fusion_addin/Fuse2SC/    the Fusion add-in: reading, exports, hub batch, bridge, launcher
tools/                add-in install, sync, packaging, version check
```

## License

[Apache License 2.0](LICENSE). See also [NOTICE](NOTICE).

Not affiliated with or endorsed by Autodesk, Inc. or the SolidCraft project. Fusion 360 is a
trademark of Autodesk, Inc.
