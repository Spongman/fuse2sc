# Adding a Fusion feature type

A Fusion feature type is supported once three pieces agree:

1. the add-in **reads** it,
2. the IR **models** it,
3. the translator **plans** SolidCraft commands for it.

Until all three exist, steps of that type come in as Fusion's result, so a half-added feature
never breaks a build. Tests fail when the pieces disagree.

Below, `ThreadFeature` is the worked example. Its Fusion properties and SolidCraft parameters are illustrative:
check them against Fusion's API reference and `solidcraft-cli commands` before relying on them.

## 1. Model the payload (`src/fuse2sc/model.py`)

```python
@payload("ThreadFeature")
class Thread(Feature):
    faces: list[Pick] = []          # a point on each cylindrical face, read before the thread
    designation: str | None = None  # "M6x1"
    modeled: bool = False
    length: str | None = None       # an expression, as Fusion wrote it
```

- Lengths are mm, and expressions stay as Fusion's text.
- A pick is a world point on an edge or face, read with the timeline before the feature.
- Give every field a default, so an older or partial file still parses.

## 2. Read it in Fusion (`fusion_addin/Fuse2SC/fuse2sc_fusion/features.py`)

```python
def read_thread(ctx, f):
    if get(f, "isFullLength") is False and not get(f, "threadLength"):
        ctx.unread.append("thread length")
    return {"faces": [ctx.pick_face(x) for x in items(f.inputCylindricalFaces)],
            "designation": get(get(f, "threadInfo"), "designation"),
            "modeled": bool(get(f, "isModeled", default=False)),
            "length": ctx.expr(get(f, "threadLength")),
            "created": ctx.bodies(f.bodies)}

READERS["ThreadFeature"] = read_thread   # or add it to the READERS dict literal
```

- **Use `Ctx` for every read.** It has helpers for picks, planes, axes, regions, bodies and
  expressions.
- **Read through `get()` and `items()`** (`safe.py`). Property names differ between Fusion
  versions, and some collections are vectors rather than collections.
- **Anything seen but not carried goes in `ctx.unread`.** It shows up in the report.
- **Inputs a feature consumes** (edges, faces) are read here: the timeline is rolled to just
  before the feature for you.

## 3. Plan it (`src/fuse2sc/translate/features.py`)

```python
@plan_feature.register(Thread)
def _thread(f: Thread, t: Translator, step: Step) -> None:
    if not f.faces or not f.designation:
        return t.fallback(step, "thread without faces or size")
    for pick in f.faces:
        t.emit("solid.thread", {"face": list(pick.at), "body": pick.body, "thread": f.designation,
                                "modeled": f.modeled, "name": t.feature_name(step)},
               delta=t.delta(step) if f.modeled else None)
```

- **Fall back rather than guess.** `t.fallback(step, why)` brings Fusion's result in for this
  step, and history carries on after it.
- **Give alternatives as `Variant`s.**
  - `Variant(params, "refused", why)` is tried when SolidCraft refuses the command.
  - `Variant(params, "wrong", why)` is tried when the command builds something different from
    Fusion. It needs `delta=t.delta(step)`.
- **Name regions by curves first:** `profile_selection(t, f.regions)` returns the curves, the
  inside points (as a refused-variant) and area/centroid matches.
- **Let the replay name the bodies:** pass `new_bodies=t.new_bodies(step)` and it gives
  SolidCraft's new bodies Fusion's names.
- **Check the command's parameters** with `solidcraft-cli commands`, or the MCP tool
  `describe_command`.

## 4. Test it

1. **A unit test** (`tests/unit/test_translate.py`): build a one-step document with
   `one_step("ThreadFeature", {...})` and assert on the commands planned. Also cover the fallbacks.
2. **A golden test** (`tests/golden/test_golden.py`): build a shape with `irkit` whose volume,
   centre of mass and box you can compute exactly, and assert `result.category == "full"`.
   `Solid` adds and subtracts boxes and cylinders for you.
3. **If the replay needs new behaviour,** add an engine test with `tests/unit/fake_port.py`.
4. **Run `python tools/check.py`.** The registry tests check that the model, the add-in reader
   and the translator handler all exist. The golden suite checks that every SolidCraft command
   fuse2sc uses exists in the installed SolidCraft.

Then add the feature to the "What carries over" table in the README.
