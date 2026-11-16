# Architecture

fuse2sc has two halves. The **Fusion add-in** reads designs. The **fuse2sc package** builds, checks
and reports. The only thing they share is the intermediate file (the IR, [ir.md](ir.md)).

```
 Fusion add-in (Fusion's Python, stdlib only)      fuse2sc package (Python 3.11+)
 ─────────────────────────────────────────────     ───────────────────────────────────────────────
 reader / features / sketches / regions  ──IR──►   model ──► translate ──► replay ──► pipeline ──► cli
 export (STEP, manifest) · batch · bridge ◄─HTTP──  bridge (client)      │
                                                                         └── solidcraft (MCP session)
```

## Layers of the package

Each layer may import only the layers to its left. `tests/unit/test_layers.py` enforces this.

| Layer | What it is | Depends on |
|---|---|---|
| `ir/` | IR helpers shared with the add-in: schema version, units, key names. Stdlib only. | nothing |
| `model.py` | The IR as pydantic models; feature payloads registered with `@payload` | `ir` |
| `translate/` | **Pure**: a Document becomes a Plan of SolidCraft commands. No I/O. | `model`, `ir`, `geometry` |
| `solidcraft/` | Finding `solidcraft-cli`, the MCP session (local, or a remote app with path mapping), one-shot CLI runs | `ir` |
| `replay/` | The build: runs a Plan against a `SolidCraftPort`, checks every step | `translate`, `model`, `solidcraft` |
| `reports/`, `verify.py`, `settings.py`, `bridge.py` | Report templates, re-measuring, the workspace, the Fusion bridge client | the layers above |
| `pipeline.py` | One design or many: sources, options, outputs, linked designs | everything above |
| `cli.py` | The typer command line: argument parsing and output only | `pipeline` and below |

## Inside the replay

```
Engine.run ─► translate(doc) ─► replay(port, state, sources) ─► save, script, BuildResult
                                  │
                                  ├─ CommandRunner.run(cmd)    prepare → run → variants → strategies → guard → after
                                  │      ├─ BodyNamer          new bodies get Fusion's names (naming.py)
                                  │      └─ strategies         retries for refusals, per SolidCraft version
                                  └─ StepChecker.check(step)   compare → rename → Substituter → divergence
```

- **`SolidCraftPort`** (`port.py`) is the five calls the build makes: `execute`, `tool`,
  `bodies`, `undo`, `save`. The real MCP session implements it. So does
  `tests/unit/fake_port.py`, which is how the engine's control flow is unit-tested without
  SolidCraft.
- **`BuildState`** (`state.py`) is everything the parts share: the plan, options, step records,
  aliases, offsets and the journal. The parts have no other shared state.
- **Tolerances** all live in `checks.Tolerances`, and workspaces can override them.

## Design rules

- **Fusion only reads.** Building and checking happen outside Fusion.
- **The translator is pure.** It never guesses: input it can't express becomes a fallback step,
  never a best effort.
- **Nothing is dropped silently.** Options a reader can't carry go into `unread`, and refusals go
  into the journal. Both appear in the report.
- **Every step is checked against Fusion's numbers**, not against "it built".
- **SolidCraft's quirks** are handled in `replay/strategies.py`, each scoped to the versions it is
  for.

## Extension points

| To add | Where | Guide |
|---|---|---|
| A Fusion feature type | add-in reader, `@payload` model, translator handler | [adding-a-feature.md](adding-a-feature.md) |
| A retry for a SolidCraft refusal | `replay/strategies.py` (`Strategy`) | docstring there |
| A source of Fusion's results | anything with `async export(index, components)` (`replay/sources.py`) | `StepSource` |
| A tolerance | a field on `replay/checks.Tolerances`; workspaces can set it at once | docstring there |
| A report | a jinja2 template in `reports/templates/`, a function in `reports/__init__.py` | |
| A command | a typer command in `cli.py`; its logic in `pipeline.py` or its own module | |
| An IR change | bump `ir/schema.SCHEMA_VERSION`, upgrade old files in `schema.upgrade`, update `ir.md`, run `tools/sync_addin.py` | |
