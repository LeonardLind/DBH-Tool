# DBH Point-Cloud Tool — Project Handover

## Purpose

Build a focused application for estimating tree Diameter at Breast Height (DBH) from dense terrestrial/mobile LiDAR point clouds, initially from Leica BLK2GO exports.

This is **not** an attempt to reproduce Cyclone 3DR, LISCAD, or a general point-cloud package. The goal is a narrow, testable forestry workflow:

**point cloud → local ground → height normalization → stem candidates → cross-sections → multiple geometric fits → model comparison → DBH + confidence → review/export**

The initial target is a semi-automatic scientific tool. Full automation is a later milestone.

---

## Instruction to Claude Code

Before implementing anything:

1. Read all three handover documents:
   - `01_PROBLEM_AND_HANDOVER.md`
   - `02_SCIENCE_AND_METHODS.md`
   - `03_PIPELINE_AND_BUILD_LOG.md`
2. Inspect the existing repository/codebase if one exists.
3. Critically review the proposed architecture and scientific method.
4. Decide whether you agree with the proposed approach.
5. If you agree, state the reasoning briefly and begin with the smallest testable vertical slice.
6. If you disagree with any part, **do not blindly follow this plan**. Update the relevant Markdown documents with:
   - what you changed,
   - why,
   - evidence/reasoning,
   - expected effect on accuracy or maintainability.
7. Keep `03_PIPELINE_AND_BUILD_LOG.md` updated during development. It is the persistent state/handover file for future coding sessions.
8. Never silently change a scientific assumption. Record it.

The project should prefer reproducible measurements and explicit uncertainty over producing a DBH value for every detected object.


---

## Review outcome (2026-08-24)

The plan in these documents was reviewed against the sample scan
(`Las-Sample/Yaloch Maya.las`, LAS 1.2, 35,488,852 points, 0.92 GB, local
coordinate frame with no CRS, 59 x 55 m footprint, 30.8 m of relief, ~24,500
points/m2, classification field empty) and then implemented.

**Agreed and implemented as written:** the layered pipeline with explicit data
structures; multi-model comparison with no early choice of geometry; local ground
normalisation; multi-height support sections; area-equivalent diameter as the
primary irregular-shape summary; qualitative confidence bands rather than a
fake percentage; synthetic-first development order; refusing to emit a number
when the evidence is inadequate.

**Changed, with reasoning recorded in docs 03 decisions log:**

| # | change | why |
| --- | --- | --- |
| DEC-006 | PDAL CSF/SMRF deferred behind an optional adapter; a despiked lowest-point grid plus a robust per-stem plane is the primary ground model | not pip-installable on Windows, and CSF/SMRF are plot-wide airborne-oriented classifiers; a 40 m dense terrestrial scan wants a *local* estimate at each stem |
| DEC-007 | stem-normal cross-sections implemented in V1, not deferred | the lean bias is systematic, not noise: a horizontal cut through a stem leaning `t` gives an ellipse of major axis `D/cos t`, about +3% on a circle fit at 20 degrees. Measured lean on the sample data was 5-11 degrees on ordinary stems |
| DEC-008 | added a lean-versus-ovality discriminator | otherwise a leaning circular stem is recorded as oval, which is one of the open questions in docs 03 |
| DEC-009 | added convex-perimeter-equivalent diameter as the tape comparator | a field tape bridges flutes, so tape DBH corresponds to the convex perimeter, not to cross-sectional area. Validating area-equivalent against tape would score a real geometric difference as tool error |
| DEC-010 | a cross-section is cut against **one scalar ground datum per stem**, not per-point height above ground | selecting on per-point HAG makes the cut plane follow the terrain, so ground slope adds to stem lean. Found by a failing test: on a 15 degree slope a stem leaning 20 degrees was cut at an effective 35 degrees, inflating the axis ratio from 1.06 to 1.19 |
| DEC-011 | Pratt and Taubin circle fits promoted from "later candidates" into V1 | ~20 lines each, and they remove the dominant failure mode. Measured on a 38 cm stem: the algebraic fit is biased -1.7 cm on a 90 degree arc and -16.3 cm on a 45 degree arc, while Pratt/Taubin/geometric stay within 0.3 cm |
| DEC-012 | Open3D dropped from the stack | scipy cKDTree and ndimage cover the neighbour and labelling work, matplotlib covers 2D review; Open3D DBSCAN over tens of millions of points is memory-hostile, as these documents already note |
| DEC-013 | `status`, `review_state` and `selected_model` separated; and a `report_diameter` distinction between soft and hard review | "no automatic winner" taken literally makes every tree REVIEW_REQUIRED and destroys the signal the status field carries |
| DEC-014 | added a contamination-versus-irregularity discriminator | on real data an attached liana produces the exact residual signature of a fluted stem, and an outline model will trace it and report it as stem shape. This is one of the open questions, and hitting it was unavoidable rather than optional |

**Not changed but worth stating:** thresholds are not left literally `null` as
section 9 asks, because a null-valued config cannot run. Instead every
uncalibrated default is listed in `dbh_tool.config.PROVISIONAL_PARAMETERS` and
stamped into the provenance of every export, so a result can never be read as
calibrated when it is not.

---

## The problem

We have dense 3D forest point clouds where:

- the terrain may be sloped or locally uneven;
- trees may lean;
- vegetation, lianas, branches and nearby stems create noise;
- stems may be partly occluded;
- scanner trajectory and viewing angle cause uneven point density;
- a stem cross-section may not be circular;
- tropical stems can be elliptical, fluted, buttressed or otherwise irregular;
- a single horizontal slice can fail if ground normalization is slightly wrong or the slice happens to intersect a local deformity.

A naïve implementation such as:

> take all points at global Z = 1.3 m → fit one circle

is therefore not sufficient.

DBH must be measured relative to **local ground**, not an arbitrary global Z plane.

---

## Scope

### In scope for the first useful product

- Import LAS/LAZ first.
- Add E57 through a reliable conversion/import layer if practical.
- Preserve original XYZ and metadata where available.
- Ground classification.
- Build/interpolate a local ground surface.
- Add `height_above_ground` for points.
- Extract configurable bands around breast height.
- Detect/segment likely stem cross-sections.
- Fit several candidate models to each stem.
- Score model quality.
- Compare model outputs.
- Flag uncertain/irregular stems instead of forcing a result.
- Visual review of each measurement.
- Manual approve/reject/override.
- CSV/JSON export.
- Reproducible run configuration.

### Not required for V1

- Replacing Cyclone registration.
- SLAM.
- Registering multiple raw BLK2GO scans.
- Full forest inventory platform.
- Biomass/carbon equations.
- Tree species recognition.
- Perfect fully automatic detection of every tree.
- A polished enterprise UI.

Registration should initially happen upstream, e.g.:

**BLK2GO → Cyclone REGISTER 360 → LAS/LAZ/E57 → DBH Tool**

---

## Recommended implementation stack

### Core language

**Python**

Reason:
- strong scientific/numerical ecosystem;
- rapid experimentation;
- easy comparison of alternative algorithms;
- good support for point-cloud processing and statistics.

### Candidate libraries

Use only what is justified; do not add every package immediately.

As implemented, the dependency set is deliberately small. Everything in the
scientific core runs on NumPy and SciPy.

- **NumPy** — arrays/numerics. *Used.*
- **SciPy** — optimisation (`least_squares` for the geometric circle),
  `ndimage` (ground raster despiking, filling, connected components),
  `spatial` (cKDTree for isolated-point removal, ConvexHull for the tape
  comparator). *Used.*
- **laspy** — LAS/LAZ access, chunked. *Used.* A 0.92 GB, 35 M point file reads
  in about 3 seconds, so full passes are affordable and the streaming design
  never needs more than chunk-sized memory.
- **matplotlib** — review figures, Agg backend so it runs headless. *Used.*
- **PyYAML** — serialisable run configuration. *Used.*
- **pytest** — tests. *Used.*
- **scikit-learn** — *not used.* Nothing needed it; the robust estimators here
  are purpose-written and testable.
- **Open3D** — *not used*, see DEC-012.
- **PDAL** — *optional*, see DEC-006. Needed only to benchmark CSF/SMRF against
  the local ground model, and it requires conda on Windows.
- **Shapely** — *not used.* Polygon area is the exact polar integral
  `0.5 * integral(r^2 dtheta)` and perimeter is a segment sum; a geometry library
  would add a dependency without adding correctness.
- **PySide6** — *not yet.* M7 is not started, and the review figures currently
  serve the inspection need.

Avoid coupling the scientific core to the UI. Model fitting and scoring should be plain functions/modules that can run headless in tests.

---

## Architecture principle

Use a layered pipeline:

```text
I/O
  ↓
Preprocessing
  ↓
Ground model
  ↓
Height normalization
  ↓
Stem candidate extraction
  ↓
Cross-section construction
  ↓
Candidate geometric models
  ↓
Fit metrics / model comparison
  ↓
Scientific decision layer
  ↓
Human review
  ↓
Export
```

Each stage should accept and return explicit data structures. Avoid hidden global state.

---

## Key product rule: do not choose one geometry too early

For each candidate stem, evaluate multiple interpretations.

At minimum, support:

1. standard circle;
2. ellipse;
3. robust/RANSAC circle;
4. irregular-outline / perimeter or area based equivalent diameter.

Potential later candidates:
- robust/RANSAC ellipse;
- Pratt/Taubin/Lemen-style circle fits;
- radial-median polygon;
- spline/alpha-shape outline;
- short vertical cylinder/frustum fit;
- multi-height taper fit.

The purpose is **not** to pick the mathematically most complex model. The purpose is to determine which model is most defensible for that particular stem and point coverage.

---

## Why multi-model comparison matters

Different failures produce different signatures.

Examples:

### Clean, near-circular trunk
Circle and RANSAC circle should agree closely. Ellipse should have near-equal axes.

### Partial occlusion
Ordinary least-squares circle can shift toward the visible arc. RANSAC may be more stable, but uncertainty must increase because geometric coverage is incomplete.

### Oval stem
Circle may have low residuals yet erase meaningful shape. Ellipse can represent major/minor axes and area more faithfully.

### Fluted / irregular stem
Neither circle nor ellipse may be scientifically honest. An outline-derived area-equivalent diameter may better summarize cross-sectional area, but only if enough of the perimeter is observed.

### Liana / branch contamination
An ordinary fit may be pulled outward. RANSAC should be less sensitive if the stem itself remains the dominant structure.

Therefore the software should compute competing models and **compare them quantitatively**.

---

## Proposed model-comparison strategy

Do not call this "A/B testing" in the statistical product-experiment sense. Internally, think of it as **candidate-model benchmarking and selection**.

For every cross-section:

1. fit all eligible models;
2. compute common diagnostics;
3. reject geometrically impossible models;
4. compare model estimates;
5. select a preferred interpretation only when evidence is sufficiently strong;
6. otherwise label the tree `REVIEW_REQUIRED`.

Suggested diagnostics:

- number of points;
- inlier count;
- inlier fraction;
- RMSE / robust residual statistic;
- median absolute residual;
- angular coverage around fitted center;
- largest angular gap;
- fitted diameter;
- ellipse axis ratio;
- stability across nearby heights;
- sensitivity to point resampling/bootstrap;
- agreement between independent models;
- local point density;
- percentage of circumference visibly supported by data;
- cluster compactness;
- distance from nearby clusters/objects.

Never rely on RMSE alone. A model fitted to a small visible arc can have excellent residual error and still give a wrong diameter.

---

## Multi-height strategy

Do not depend entirely on one razor-thin slice.

Initial proposal:

- primary target height: **1.30 m above local ground**;
- configurable slice thickness, e.g. 5–10 cm initially;
- additionally evaluate nearby cross-sections such as:
  - 1.20 m
  - 1.25 m
  - 1.30 m
  - 1.35 m
  - 1.40 m

These are not five independent DBH definitions. They are supporting observations for robustness.

Use them to:
- detect sudden anomalies;
- estimate local taper;
- interpolate back to 1.30 m;
- identify bad ground estimates;
- reduce the influence of a single noisy slice.

The exact heights and widths must be configurable and validated against real BLK2GO data.

---

## Ground is a first-class scientific problem

The DBH height reference is only as good as the local ground estimate.

Suggested initial evaluation:
- PDAL CSF;
- PDAL SMRF;
- potentially a simple local-low-point/TIN baseline.

For each stem, estimate local ground close to the stem rather than using one plot-wide scalar elevation.

Store:
- ground Z at stem center;
- ground model source;
- local interpolation distance;
- local ground roughness;
- confidence/quality diagnostics.

If the local ground is uncertain, that uncertainty should propagate to the DBH measurement status.

---

## Definition of success

The application succeeds when it can produce DBH measurements that are:

- repeatable;
- inspectable;
- explainable;
- benchmarked against field measurements;
- accompanied by quality metrics;
- robust enough to identify when it **should not** automatically decide.

A smaller number of trusted measurements is better than a larger number of silently wrong measurements.

---

## Validation dataset

Before claiming accuracy, create a reference dataset containing trees with manually measured DBH.

Each reference tree should ideally include:
- unique tree ID;
- tape/caliper DBH;
- measurement height;
- note if buttressed/irregular/leaning;
- approximate tree center/location;
- scanner conditions where known.

Split reference trees into:
- development/calibration set;
- held-out validation set.

Do not repeatedly tune thresholds against the held-out set.

Report:
- bias;
- MAE;
- RMSE;
- relative RMSE;
- failure/review rate;
- errors by stem shape/size/occlusion class.

A tool that achieves low RMSE only by discarding all difficult trees must disclose that review/failure rate.

---

## V1 acceptance criteria

The first vertical slice should demonstrate:

1. load a real LAS/LAZ file;
2. classify or provide ground;
3. derive height above local ground;
4. extract a user-selected region around one known tree;
5. extract a 1.30 m cross-section;
6. fit:
   - standard circle,
   - ellipse,
   - RANSAC circle,
   - an initial irregular-shape estimate;
7. display overlays;
8. print/export all model values and diagnostics;
9. allow the user to select/approve the correct interpretation;
10. save the result reproducibly.

Do this for a handful of known trees **before** attempting automatic plot-wide tree detection.

---

## Recommended development order

```text
Known single tree
    ↓
Correct local ground
    ↓
Correct normalized slice
    ↓
Multiple fit models
    ↓
Metrics + visual comparison
    ↓
Field validation
    ↓
Automatic stem detection
    ↓
Batch processing
    ↓
GUI/polish
```

Do not reverse this order.

---

## Sources used to design the approach

- Koreň et al. / circle-fitting comparison work:  
  https://www.sciencedirect.com/science/article/pii/S0303243417301617
- Olofsson, Holmgren & Olsson (2014), RANSAC stem measurements:  
  https://www.mdpi.com/2072-4292/6/5/4323
- Liang et al. style TLS workflows / multi-height fitting example:  
  https://www.mdpi.com/2072-4292/12/17/2672
- Ye et al. (2020), elliptic stem mapping:  
  https://www.mdpi.com/2072-4292/12/3/352
- Large-scale section fitting with ellipses/polygons/splines:  
  https://www.mdpi.com/2072-4292/13/13/2476
- Tropical forest DBH estimation and limitations of simple circular fitting:  
  https://www.mdpi.com/2071-1050/16/6/2275
- PDAL CSF documentation:  
  https://pdal.io/en/latest/stages/filters.csf.html
- PDAL ground-filter tutorial / SMRF:  
  https://pdal.org/en/stable/tutorial/ground-filters.html
- Open3D point-cloud / DBSCAN documentation:  
  https://www.open3d.org/docs/latest/tutorial/geometry/pointcloud.html
