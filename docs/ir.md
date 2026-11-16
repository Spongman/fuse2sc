# The intermediate format (IR), schema 1

One JSON file per Fusion design (`timeline.json`), written by the add-in and read by `fuse2sc build`.
`src/fuse2sc/ir/` defines it. The add-in carries a byte-identical copy (`tools/sync_addin.py`, and a
test checks the copy).

## Conventions

- Lengths are **mm**, areas **mm²**, volumes **mm³**. Fusion's API works in cm, and `ir/units.py`
  is the only place that converts.
- **Matrices** are row-major 4×4 with the translation in **mm**.
- **Expressions** are Fusion's own text (`"width / 2 + 3 mm"`), never evaluated or converted.
- A parameter's `value` is Fusion's internal value (cm or rad), whatever its `unit` says.
- **Points** are world coordinates. The exception is sketch points (`xy`), which are in their
  sketch's frame.
- **Bodies** are keyed `"component/body"`.
- **Schema versions:** a reader refuses a newer `schema` than it knows.

## Document

```jsonc
{
  "schema": 1,
  "tool": {"name": "fuse2sc", "version": "0.1.0"},
  "document": "Bracket v12",
  "fusion": {"id": "urn:…", "version": 12, "name": "Bracket", "hub": "H", "project": "P", "folder": "a/b",
             "parametric": true},
  "units": "mm",                                  // the design's display units
  "parameters":       [{"name": "width", "expression": "80 mm", "unit": "mm", "comment": "", "value": 8.0}],
  "model_parameters": [{"name": "d12", "expression": "width / 2", "unit": "mm", "value": 4.0}],
  "components": [{"name": "Leg", "linked": "Leg design",    // linked: another Fusion design
                  "occurrences": [{"path": "Leg:1", "parent": null, "matrix": [/* 16 */], "visible": true}]}],
  "steps": [ /* below */ ],
  "appearance": {"Leg/Body1": {"look": {"name": "Steel - Satin", "rgb": "#9a9a9a", "metal": true, "roughness": 0.4},
                               "material": {"name": "Steel", "density": 7.85},       // g/cm³
                               "faces": [{"at": [0, 0, 5], "look": {"name": "Paint", "rgb": "#c02020"}}]}},
  "errors": ["reads that failed, kept rather than raised"]
}
```

A `look` may also carry `opacity` (0–1).

## Steps

```jsonc
{
  "index": 7, "name": "Fillet3", "kind": "FilletFeature",   // kind: Fusion's class name
  "suppressed": false, "component": "Leg",
  "unread": ["setback corners"],          // options seen and not carried over
  "snapshot": {"Leg/Body1": {"volume": 1234.5, "area": 678.9, "com": [x, y, z],
                             "bbox": [[minx, miny, minz], [maxx, maxy, maxz]], "visible": true}},
  "error": "…",                           // the step could not be fully read
  "sketch": {…} | "feature": {…} | "occurrence": {…},
  "placements": {"before": {"Leg:1": [/* 16 */]}, "after": {…}}  // joints: occurrence matrices
}
```

`snapshot` is Fusion's ground truth just after the step. The build compares SolidCraft with it
after every step.

## Sketch

```jsonc
{
  "uid": "Leg/Base#3", "name": "Base", "component": "Leg",
  "frame": {"origin": [x, y, z], "x": [..], "y": [..]},       // world
  "points": [{"id": "P0", "xy": [0, 0], "fixed": true}],
  "curves": [
    {"id": "C0", "type": "line",   "ends": ["P1", "P2"]},
    {"id": "C1", "type": "circle", "center": "P3", "radius": 4.0},
    {"id": "C2", "type": "arc",    "center": "P4", "ends": ["P5", "P6"], "via": [x, y]},
    {"id": "C3", "type": "ellipse", "center": "P7", "major": [x, y], "minor_radius": 2.0},
    {"id": "C4", "type": "spline_fit", "through": ["P8", "P9", "P10"], "closed": false},
    {"id": "C5", "type": "spline_cp", "poles": [[x, y]], "degree": 3, "rational": false},
    {"id": "C6", "type": "other", "fusion_type": "SketchConicCurve"}
  ],
  "constraints": [{"type": "Coincident", "refs": {"point": "P1", "entity": "C1"}}],
  "dimensions":  [{"type": "Linear", "refs": {"entityOne": "P1", "entityTwo": "P2"},
                   "expr": "width", "driving": true, "orientation": "horizontal", "text_at": [x, y]}]
}
```

Every curve also has `construction` and `fixed`. Any curve may carry a `polyline` (sketch points),
which is drawn when its own type can't be. `refs` are keyed by the Fusion property that held the
entity (`line`, `pointOne`, `curveTwo`…).

## Regions (profiles)

```jsonc
{"sketch": "Leg/Base#3", "outer": ["C0", "C1", "C2", "C3"], "holes": [["C4"]],
 "inside": [x, y],               // a point well inside the region (not just its centroid)
 "area": 1200.0, "centroid": [x, y]}
```

A curve id is `null` where the region's edge could not be tied to a sketch curve. The build looks
a region up by its curves, then by area and centroid among SolidCraft's regions, then by `inside`.

## Features

`op` is `new`, `join`, `cut` or `intersect`. `targets` are the bodies a join, cut or intersect
acted on. `created` are bodies the step made.

An edge or face pick is `{"at": [x, y, z], "body": "comp/body"}`. The point is on the edge or face,
read before the feature changes it.

| kind | payload |
|---|---|
| `ExtrudeFeature` | `op`, `regions`, `faces` (picks, when a face is the profile), `targets`, `created`, `extent`, `start`, `taper`, `thin` |
| `RevolveFeature` | `op`, `regions`, `axis {origin, dir}`, `angle {type: one\|two\|symmetric\|other, expr, expr2, degrees}`, `targets` |
| `SweepFeature` | `op`, `regions`, `path {sketch, curves}`, `orientation`, `twist`, `rails`, `targets` |
| `LoftFeature` | `op`, `sections` (regions), `rails` (count), `closed`, `solid`, `targets` |
| `FilletFeature`, `ChamferFeature` | `sets: [{type, size, edges, tangent_chain, start?, mid_radii?}]` |
| `ShellFeature` | `faces`, `inside`, `outside`, `tangent_chain`, `bodies` (a whole-body shell) |
| `DraftFeature` | `faces`, `angle`, `neutral {origin, normal}`, `pull` |
| `OffsetFacesFeature` | `faces`, `distance` |
| `HoleFeature` | `holes: [{at}]`, `diameter`, `depth` or `through_all`, `style`, `cb_diameter`, `cb_depth`, `cs_diameter`, `cs_angle`, `tip_angle`, `targets` |
| `CombineFeature` | `target`, `tools`, `op`, `keep_tools` |
| `MoveFeature` | `bodies`, `matrix` |
| `ScaleFeature` | `bodies`, `factor` or `factors`, `origin` |
| `RemoveFeature` | `bodies` |
| `SplitBodyFeature` | `bodies`, `tool {type: plane, origin, normal}`, `{type: face, at, body}`, `{type: line, sketch, curve}` or `{type: body, body}` |
| `MirrorFeature` | `inputs`, `plane {origin, normal}`, `join` |
| `CircularPatternFeature` | `inputs`, `axis`, `count`, `angle`, `symmetric`, `skipped_instances` |
| `RectangularPatternFeature` | `inputs`, `dir1`, `count1`, `spacing1`, `dir2`, `count2`, `spacing2`, `extent_mode`, `symmetric1`, `symmetric2` |
| `CopyPasteBody`, `CutPasteBody` | `sources` |

The remaining structures:

- **`extent`** is one of:
  - `{"type": "one", "side1": end}`
  - `{"type": "two", "side1": end, "side2": end}`
  - `{"type": "symmetric", "expr": "…", "total": bool}`
- **`end`** is one of:
  - `{"type": "distance", "expr": "…", "mm": n}`
  - `{"type": "to", "at": [x, y, z], "offset": "…"}`
  - `{"type": "all", "reverse": bool}`
- **`start`** is one of:
  - `{"type": "sketch"}`
  - `{"type": "offset", "expr"}`
  - `{"type": "object", "at", "offset"}`
- **`inputs`** is one of:
  - `{"bodies": [keys]}`
  - `{"features": [{"name", "component"}]}`
- **Blend sizes** (fillet and chamfer `size`) by `type`:

  | `type` | `size` |
  |---|---|
  | `constant` | `{radius}` |
  | `chord` | `{chord}` |
  | `variable` | `{start, end}` |
  | `equal` | `{distance}` |
  | `two_distances` | `{d1, d2}` |
  | `distance_angle` | `{distance, angle}` |

Any other kind is recorded with `created` and `changed` bodies. The build brings Fusion's result in
for it.

## Occurrence steps

```jsonc
"occurrence": {"component": "Leg", "path": "Leg:2", "linked": "Leg design", "matrix": [/* 16 */]}
```
