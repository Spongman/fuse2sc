# Fusion API notes

Behaviours of Fusion's API that shaped the extractor.

## Reading the timeline

- **A feature's inputs can only be read with the timeline rolled to just before it**
  (`timelineObject.rollTo(True)`). Otherwise you get "Didn't roll editing feature back".
- **Edges and faces a feature consumes no longer exist after it.** Read fillet and chamfer edges,
  and shell and draft faces, before the feature. Points read afterwards lie on the moved faces.
- **Read a sketch with the timeline after it**: earlier, its curve numbering can change.
- **`rollTo` works on the active document only.** Batch export opens documents visibly.
- **Always put the marker back**, in a `finally`, and never save the document.

## Sketches and profiles

- **Sketches inside components come as proxies.** Compare curves through `.nativeObject`, because
  `entityToken` can differ from call to call.
- **`ProfileCurve.sketchEntity` can name the wrong one of two collinear lines.** Match a profile
  edge to its sketch curve by geometry (the edge's midpoint).
- **A feature's profile list can be stale after its sketch was edited later.** For new and join
  extrudes, read the regions from the feature's start faces.
- **A profile's centroid can lie outside it** (an annulus, an L). Store a point well inside the
  region as well.

## Collections

- **Some collections are Python vectors** (`BRepFaceVector`, `JointVector`, `MeshBodyVector`), with
  no `count`/`item`. Treating them as collections silently returns nothing. `safe.items()` reads
  both.

## Geometry and units

- **Lengths are cm, angles radians.** `Parameter.value` is internal whatever `Parameter.unit` says.
- **A rectangular pattern's direction entity can point opposite to the way Fusion actually
  patterns.** Take the direction from where the nearest copy landed.
- **For a two-sided extrude, the solid lies between the two ends**, which can both be on the same
  side of the sketch.

## Export

- **STEP export leaves hidden bodies out, and renames duplicate body names.** Show bodies for the
  export only. Bodies that stay invisible with every light bulb on are copied into a scratch
  document and exported from there.
- **A root-level mesh export can report success and write nothing.** Wait for the file and check
  that it exists.
- **Call `Document.close(False)` only on documents your run opened.** A user's open design may have
  unsaved changes.
