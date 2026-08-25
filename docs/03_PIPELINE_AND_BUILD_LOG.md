# DBH Point-Cloud Tool — Pipeline, Roadmap and Persistent Build Log

> This file is intentionally operational. Claude Code should update it as the project changes.
>
> Keep the high-level scientific reasoning in `02_SCIENCE_AND_METHODS.md`.  
> Keep the project scope/problem in `01_PROBLEM_AND_HANDOVER.md`.

---

# 0. Claude Code startup protocol

Start with `CLAUDE.md` in the repository root: it carries the environment
specifics, the non-negotiable rules, and the current next step in a form meant to
be read cold.

Then, at the beginning of each development session:

1. Read `CLAUDE.md`, then this file, starting from section 13 (Current project
   state) and the **Known issues** lists in every section 12 build-log entry.
2. Read the decisions log (section 10). DEC-006 onwards are the ones that differ
   from the original plan.
3. Check the working tree and recent commits.
4. Run the test suite and confirm the count matches section 13:

       ./.venv/Scripts/python.exe -m pytest -q

   A different count means the tree is not in the state this file describes.
   Reconcile that before building anything.
5. Verify the current milestone and its blockers.
6. Work only on the smallest next testable increment.
7. Before ending:
   - run tests;
   - append a build-log entry to section 12 (goal, work, tests, decisions, bugs
     found, known issues, files changed, milestone, next exact step);
   - add a `DEC-0NN` entry for any changed scientific assumption;
   - update section 13 and `CLAUDE.md` if the state or next step moved;
   - state the exact next step.

If reality contradicts this plan, update the plan and say so in the build log.

**Two things never to do:** invent reference measurements, and quote the
sensitivity results in docs 02 sections 21 to 23 as accuracy. Both are covered in
`CLAUDE.md` under Non-negotiables.

---

# 1. Repository structure

Proposed here, and implemented very close to this shape. Differences as built:
`io/e57.py`, `preprocessing/`, `ground/csf.py`, `ground/smrf.py`,
`stems/clustering.py`, `evaluation/benchmark.py` and `export/{csv,json}.py` do not
exist; isolated-point removal lives in `stems/slices.py`, export is a single
`export/tables.py`, and `synthetic.py` plus `measure.py` sit at package root.

```text
dbh-tool/
├─ README.md
├─ pyproject.toml
├─ docs/
│  ├─ 01_PROBLEM_AND_HANDOVER.md
│  ├─ 02_SCIENCE_AND_METHODS.md
│  └─ 03_PIPELINE_AND_BUILD_LOG.md
├─ src/
│  └─ dbh_tool/
│     ├─ io/
│     │  ├─ las.py
│     │  └─ e57.py
│     ├─ preprocessing/
│     │  ├─ outliers.py
│     │  └─ downsample.py
│     ├─ ground/
│     │  ├─ csf.py
│     │  ├─ smrf.py
│     │  ├─ local_surface.py
│     │  └─ normalize.py
│     ├─ stems/
│     │  ├─ candidates.py
│     │  ├─ clustering.py
│     │  ├─ axis.py
│     │  └─ slices.py
│     ├─ fitting/
│     │  ├─ circle.py
│     │  ├─ ellipse.py
│     │  ├─ ransac_circle.py
│     │  ├─ outline.py
│     │  └─ common.py
│     ├─ evaluation/
│     │  ├─ coverage.py
│     │  ├─ bootstrap.py
│     │  ├─ compare.py
│     │  ├─ confidence.py
│     │  └─ benchmark.py
│     ├─ export/
│     │  ├─ csv.py
│     │  └─ json.py
│     ├─ visualization/
│     │  └─ cross_section.py
│     └─ cli.py
├─ tests/
│  ├─ unit/
│  ├─ synthetic/
│  └─ integration/
└─ data/
   └─ README.md
```

Do not commit large proprietary point clouds unless explicitly intended.

---

# 2. Data model proposal

## TreeCrossSection

```python
@dataclass
class TreeCrossSection:
    tree_id: str
    target_height_m: float
    band_thickness_m: float
    local_ground_z_m: float
    points_xyz: np.ndarray
    points_xy: np.ndarray
    source_point_count: int
    metadata: dict
```

## FitResult

```python
@dataclass
class FitResult:
    model: str
    diameter_m: float | None
    center_xy: tuple[float, float] | None
    rmse_m: float | None
    median_abs_residual_m: float | None
    point_count: int
    inlier_count: int | None
    inlier_fraction: float | None
    angular_coverage: float | None
    largest_gap_deg: float | None
    bootstrap_std_m: float | None
    valid: bool
    warnings: list[str]
    extra: dict
```

## TreeMeasurement

```python
@dataclass
class TreeMeasurement:
    tree_id: str
    selected_model: str | None
    dbh_m: float | None
    status: str
    confidence_band: str
    local_ground_z_m: float
    measurement_height_m: float
    candidate_results: list[FitResult]
    warnings: list[str]
    provenance: dict
```

Schema can change, but keep scientific provenance.

---

# 3. End-to-end pipeline

```text
[1] IMPORT
    LAS/LAZ initially
        ↓
[2] VALIDATE UNITS/COORDINATES
    confirm meters or normalize
        ↓
[3] OPTIONAL PREPROCESS
    isolated-noise removal
    optional controlled downsample
        ↓
[4] GROUND CLASSIFICATION
    CSF / SMRF / baseline
        ↓
[5] LOCAL GROUND SURFACE
    interpolate Zground(x,y)
        ↓
[6] HEIGHT NORMALIZATION
    HAG = Z - Zground
        ↓
[7] TARGET HEIGHT BAND
    around 1.30 m
        ↓
[8] STEM CANDIDATE SEGMENTATION
    initial manual ROI in V1
    automatic clustering later
        ↓
[9] CROSS-SECTION CLEANING
    remove isolated points
    optional local stem-axis correction later
        ↓
[10] FIT ALL CANDIDATE MODELS
    circle
    ellipse
    RANSAC circle
    irregular outline
        ↓
[11] DIAGNOSTICS
    residual
    angular coverage
    inlier ratio
    bootstrap
    multi-height stability
        ↓
[12] MODEL COMPARISON
    accept / choose / review
        ↓
[13] VISUAL REVIEW
    points + model overlays
        ↓
[14] EXPORT
    CSV + JSON/provenance
```

---

# 4. Milestones

## M0 — Project bootstrap

**Status: DONE** (2026-08-24)

Deliver:
- Python project;
- dependency strategy;
- lint/test setup;
- CLI entry point;
- docs copied into repository.

Acceptance:
- `pytest` runs;
- CLI `--help` works.

---

## M1 — Synthetic cross-section fitting

**Status: DONE** (2026-08-24)

Before touching a massive real point cloud, build synthetic 2D data generators.

Generate:
- perfect circle;
- noisy circle;
- circle with outliers;
- 50% visible arc;
- 25% visible arc;
- ellipse;
- irregular/fluted synthetic outline.

Implement:
- standard circle;
- ellipse;
- RANSAC circle;
- initial outline method;
- residual metrics;
- angular coverage.

Acceptance:
- tests demonstrate expected behavior;
- RANSAC beats ordinary circle in contaminated circular data;
- coverage metric correctly flags partial arcs;
- ellipse wins on truly elliptical synthetic data according to appropriate criteria;
- no model is "accepted" for intentionally underconstrained cases.

Why this comes first:
It isolates geometric correctness from forest segmentation complexity.

---

## M2 — Single known real tree, manually selected

**Status: DONE** (2026-08-24)

Input:
- one LAS/LAZ;
- manually provided bounding box or XY/radius around a tree;
- manually or simply supplied local ground initially if necessary.

Deliver:
- extract 1.30 m band;
- show top-down scatter;
- overlay all model fits;
- print model table.

Acceptance:
- result is visually inspectable;
- units confirmed;
- no automatic tree detection yet.

---

## M3 — Ground classification and normalization

**Status: PARTIAL** — local ground model done and tested; CSF/SMRF not benchmarked (DEC-006)

Implement adapters/experiments:
- CSF;
- SMRF;
- simple local ground baseline.

Deliver:
- `HeightAboveGround`;
- local ground estimate per known tree;
- ground diagnostic visualization/export.

Acceptance:
- known tree 1.30 m band follows local terrain correctly on slope;
- methods can be compared with identical downstream fitting.

---

## M4 — Multi-height profile

**Status: DONE** (2026-08-24)

Generate sections around:
- 1.20;
- 1.25;
- 1.30;
- 1.35;
- 1.40 m.

Deliver:
- per-height model estimates;
- stability metric;
- optional local taper/interpolation experiment.

Acceptance:
- isolated bad slice can be detected;
- output clearly identifies exact reported 1.30 m estimate.

---

## M5 — Reference validation

**Status: HARNESS COMPLETE, BLOCKED ON DATA** (2026-08-25) — the reference table schema, loader, comparator selection, benchmark statistics, stratification and all five experiments are implemented and tested, and the sweeps that need no ground truth have been run. What is missing is only `data/reference_trees.csv` with real field measurements. This is the critical path and nothing else blocks it.

Create reference table:

```text
tree_id
field_dbh_cm
measurement_height_m
shape_class
notes
x
y
```

Benchmark:
- each model;
- each ground method;
- slice thickness;
- multi-height strategy.

Report:
- bias;
- MAE;
- RMSE;
- relative RMSE;
- rejected/review rate.

Acceptance:
- no algorithm chosen from visual intuition alone;
- preferred default is selected based on reference error + failure behavior.

---

## M6 — Automatic stem candidate detection

**Status: PREVIEW ONLY** — `stems/candidates.py` exists as an exploration aid; precision/recall never measured. Do not use for inventory.

Only after M1–M5 are stable.

Candidate approaches:
- DBSCAN on normalized breast-height band;
- connectivity/Delaunay-based 2D segmentation;
- Hough/circle candidate proposals;
- vertical continuity checks over several heights.

Reject clusters based on:
- too few points;
- implausible diameter;
- inadequate vertical continuity;
- poor coverage/geometry;
- proximity/merge ambiguity.

Acceptance:
- compare automatic detections to a manually annotated tree list;
- report precision/recall, not only successful examples.

---

## M7 — Human-review workflow

**Status: NOT_STARTED** — review figures serve inspection for now; no UI, no approve/reject persistence.

Minimal UI:
- point-cloud overview;
- detected stem markers;
- click tree;
- top-down cross-section;
- model overlays;
- diagnostics;
- approve/reject/select model;
- save.

Potential technology:
- PySide6 desktop app,
- or a lightweight local web UI if it reduces complexity.

Do not select UI framework before the scientific core works.

---

## M8 — Batch/export

**Status: PARTIAL** — CSV + full JSON + run config are written and tested. Single-pass multi-target cropping now exists (`LasSource.crop_many`), so a run over N trees reads the file once; there is still no tiled batching for whole-plot work.

CSV example:

```text
tree_id,x,y,dbh_cm,status,selected_model,confidence,ground_z,measurement_height,coverage,rmse_cm,reviewed
```

JSON should preserve:
- every candidate model;
- parameters;
- warnings;
- software version;
- run config;
- source file identity/hash if appropriate.

---

# 5. Candidate model implementation plan

## 5.1 Standard circle

Start with a transparent implementation and test against synthetic truth.

Candidates:
- simple algebraic fit baseline;
- geometric least squares later;
- optionally compare Pratt/Taubin/Lemen-style methods if evidence/validation warrants.

Never hide which algorithm produced `circle`.

---

## 5.2 Ellipse

Use a constrained ellipse fit that cannot silently return a hyperbola/parabola.

Store:
- center;
- axes;
- rotation;
- axis ratio;
- area;
- area-equivalent diameter.

Add validation:
- axes > 0;
- plausible maximum diameter;
- sufficient angular support;
- numerical conditioning.

---

## 5.3 RANSAC circle

Parameters must be in run config:
- residual threshold;
- max trials;
- minimum inliers;
- random seed.

Deterministic/reproducible tests require a fixed seed.

After RANSAC identifies inliers, optionally refit the final circle on all inliers using a stronger circle solver.

---

## 5.4 Irregular outline

Start conservatively.

Recommended first experiment:
**angular radial-median polygon**

Concept:
1. obtain robust center from circle/ellipse consensus;
2. divide 360° into angular sectors;
3. calculate a robust radial statistic (median) for each sufficiently populated sector;
4. reject unsupported sectors;
5. only close an outline when angular coverage is adequate;
6. calculate polygon area/perimeter;
7. produce area-equivalent diameter.

Do not interpolate over huge unseen angular gaps and pretend the perimeter was observed.

Later compare with:
- alpha shapes;
- splines;
- other concave contour methods.

---

# 6. Model comparison design

## Phase 1 — no automatic winner

Initially output every candidate and require manual selection.

This lets the team collect evidence about which diagnostics predict correct results.

## Phase 2 — rule-based recommendation

Use validated rules such as:
- coverage minimum;
- max gap;
- max residual;
- minimum inlier fraction;
- max bootstrap variance;
- cross-height consistency;
- circle-vs-RANSAC agreement;
- ellipse axis ratio.

The software may recommend a model but keep human approval.

## Phase 3 — calibrated automatic selection

Only after enough reference trees exist.

Possible selection score:

```text
score =
  fit_quality
+ coverage_quality
+ stability_quality
+ cross_model_agreement
+ cross_height_agreement
- complexity_penalty
- warning_penalties
```

Do not assign weights arbitrarily and then call the output scientific. Learn/tune them on a development set and validate on held-out trees.

---

# 7. Testing strategy

## Unit tests

- circle parameter recovery;
- ellipse parameter recovery;
- equivalent-diameter formulas;
- angular coverage;
- largest angular gap;
- residual metrics;
- deterministic RANSAC;
- model serialization.

## Synthetic property tests

Randomly generate known shapes under:
- Gaussian noise;
- outliers;
- different point density;
- missing arcs;
- center offsets;
- scaling.

Assert expected error ranges.

## Integration tests

Small cropped real point clouds checked into test fixtures if licensing/data policy permits.

## Scientific regression tests

Once a reference tree set exists, store expected summary metrics and detect accidental degradation.

---

# 8. Performance strategy

Do not optimize prematurely.

For large scans:
- use PDAL streaming/cropping where possible;
- avoid loading hundreds of millions of points into Python objects;
- operate in NumPy arrays;
- crop by spatial tile/ROI before expensive algorithms;
- use downsampling only where it does not bias DBH;
- keep full-resolution points for final local fits if feasible.

Open3D DBSCAN can precompute neighborhoods and become memory-intensive for poor parameter choices; avoid running naïve global DBSCAN over a huge scan.

---

# 9. Reproducible run configuration

Every run should be serializable, e.g.:

```yaml
units: meters

ground:
  method: csf
  resolution: null
  threshold: null

slice:
  target_height_m: 1.30
  thickness_m: 0.10
  supporting_heights_m: [1.20, 1.25, 1.30, 1.35, 1.40]

ransac_circle:
  residual_threshold_m: null
  max_trials: null
  random_seed: 42

coverage:
  angular_bin_deg: null

outline:
  method: radial_median_polygon
  sectors: null

decision:
  automatic_selection: false
```

`null` means the parameter still requires calibration; Claude Code should not invent an undocumented production default merely to fill the file.

---

# 10. Decisions log

Append entries; do not erase history.

## DEC-001 — Python as initial core language
**Status:** proposed  
**Reason:** strongest fit for rapid scientific point-cloud algorithm development and testing.  
**Revisit if:** performance profiling demonstrates a real bottleneck that cannot be addressed with vectorized/native libraries.

## DEC-002 — LAS/LAZ before E57
**Status:** proposed  
**Reason:** simplify first vertical slice and leverage mature Python/PDAL tooling.  
**Revisit if:** actual source workflow requires direct E57 with metadata that would be lost through conversion.

## DEC-003 — Multi-model rather than circle-only
**Status:** proposed  
**Reason:** published research and expected tropical stem geometry show a single circular model is insufficient.

## DEC-004 — Area-equivalent diameter preferred over perimeter-equivalent for irregular contours
**Status:** proposed  
**Reason:** perimeter is highly sensitive to roughness and contour resolution; area has a clearer connection to cross-sectional/basal area.  
**Must validate:** outline reconstruction quality.

## DEC-005 — Semi-automatic before full automatic detection
**Status:** proposed  
**Reason:** validate measurement science separately from tree-detection errors.

---

## DEC-006 — PDAL CSF/SMRF deferred; local ground model is primary

**Status:** accepted, implemented 2026-08-24
**Reason:** two reasons, the second stronger than the first. (1) PDAL is not
pip-installable on Windows; its Python bindings need the PDAL C++ library via
conda, which would make the whole project conda-only for one benchmark. (2) CSF
and SMRF are plot-wide classifiers designed for airborne data. For a 40 m dense
terrestrial scan on a steep mound, the quantity that matters is the ground
elevation *at each stem*, and a despiked lowest-point grid plus a robust local
plane fit estimates that directly and reports its own quality.
**Implemented as:** `ground/dtm.py` (grid, despike, fill, smooth) and
`ground/local_plane.py` (Huber-weighted plane, slope, roughness, GOOD/FAIR/POOR).
`GroundGrid` is the module boundary, so CSF/SMRF can be added as competing
implementations without touching anything downstream.
**Revisit if:** experiment E shows CSF or SMRF gives lower field-referenced DBH
error, or a site type appears where lowest-point grids fail (deep litter, dense
low scrub, terraces).

## DEC-007 — Stem-normal cross-sections in V1, not deferred

**Status:** accepted, implemented 2026-08-24
**Reason:** docs 01 proposed starting with horizontal slices and adding stem-axis
correction later. The lean bias is systematic rather than random: a horizontal cut
through a stem leaning `t` is an ellipse of major axis `D/cos t`, so a circle fit
reads about `0.5*(D/cos t + D)`, i.e. +0.8% at 10 degrees and +3.2% at 20 degrees.
On the sample scan ordinary stems lean 5-11 degrees, and the site is a mound where
lean is the norm. The axis estimate costs one pass over a 1.4 m vertical
neighbourhood and is also the only way to tell a leaning circular stem from an oval
one (DEC-008).
**Implemented as:** `stems/axis.py`, `stems/slices.py::stem_normal_section`. Both
geometries are computed and exported for every tree at every height;
`slice.primary_geometry` chooses only the headline number.
**Validated by:** `tests/synthetic/test_ground_axis_slices.py` — the stem-normal
cut recovers a 40 cm stem leaning 20 degrees to within 1 cm; the horizontal cut
lands on the predicted `0.5*(major+minor)`.
**Open:** which geometry should be *reported* as THE DBH is a validation question,
listed in the open questions. Note the honest caveat from the sample scan: at the
5-10 degree leans seen there, the measured `horizontal - stem_normal` difference
(-0.26 to +0.19 cm) is the right magnitude but inconsistent in sign, so the
correction is not demonstrated to help on gently leaning stems. It is validated on
synthetic stems at 20 degrees, where the effect is 1.3 cm on a 40 cm stem. See
docs 02 section 20.3.

## DEC-008 — Lean-versus-ovality discriminator

**Status:** accepted, implemented 2026-08-24
**Reason:** an elliptical horizontal section has two causes with the same
appearance: a genuinely oval stem, or a circular stem that leans. Docs 02 section
11 would classify both as ELLIPTICAL, which then poisons any shape-stratified
error report. The two are separable: lean predicts axis ratio `1/cos(tilt)` *and*
a major axis aligned with the lean azimuth.
**Implemented as:** `evaluation/compare.py::attribute_ellipticity`, verdicts
CIRCULAR / LEAN_EXPLAINS_ELLIPTICITY / GENUINELY_OVAL / OVAL_BEYOND_LEAN /
INCONCLUSIVE, plus `lean_bias_estimate` so the size of the effect is in the output
rather than only in this document.

## DEC-009 — Convex-perimeter-equivalent diameter added as the tape comparator

**Status:** accepted, implemented 2026-08-24
**Reason:** amends DEC-004. Area-equivalent diameter remains correct for basal
area and biomass, but a field tape does not follow flutes, it bridges them. So
tape DBH on a fluted stem corresponds to the *convex* perimeter. Validating an
area-equivalent diameter against tape measurements would score a genuine geometric
difference as tool error, in the direction that makes the tool look biased low.
**Implemented as:** `outline.extra["diameter_convex_perimeter_equiv_m"]` alongside
the area- and perimeter-equivalent values, all three exported.
**Consequence for M5:** the reference table must record how each field measurement
was taken, and the benchmark must compare tape DBH against the convex-perimeter
column, not against the headline area-equivalent diameter.

## DEC-010 — One scalar ground datum per stem; the cut plane is geometrically flat

**Status:** accepted, implemented 2026-08-24
**Reason:** found by a failing test, not by design. Selecting section points by
*per-point* height above the ground raster makes the slab follow the terrain, so
the cut plane is tilted by the ground slope, and that tilt adds to stem lean. On a
15 degree slope a stem leaning 20 degrees downhill was effectively cut at 35
degrees, inflating the observed axis ratio from 1.06 to 1.19 and silently breaking
the DEC-008 diagnostic, which assumes `1/cos(tilt)`.
**Implemented as:** `measure_tree` computes one datum from the robust local ground
plane at the refined stem centre and drives every section and axis bin from
`z - datum`. Per-point height above ground is retained for plot-wide detection,
where it is the right tool.
**Recorded in:** `config.MEASUREMENT_CONVENTION`, exported with every measurement.
**Left open:** *which* point on a sloping site defines the datum. This
implementation uses the plane at the stem centre; many field protocols specify the
uphill side, and on a 25 degree slope the difference across a 1 m stem is ~0.45 m
of ground elevation.

## DEC-011 — Pratt and Taubin circle fits promoted into V1

**Status:** accepted, implemented 2026-08-24
**Reason:** docs 03 section 5.1 listed these as optional later comparisons. They
are ~20 lines each and they remove the dominant real-world failure mode. Measured
on a 38 cm stem with 4 mm noise: the algebraic fit is biased -1.68 cm on a 90
degree arc and -16.30 cm on a 45 degree arc, while Pratt, Taubin and geometric all
stay within 0.25 cm. Occlusion makes short arcs common, so this is not a refinement.
**Note:** the first implementation of Taubin was wrong in a way worth remembering
— it used `Mzz` where the coefficients need `Var_z = Mzz - Mz^2`, crossing the
Pratt and Taubin coefficient sets. It recovered a full circle perfectly and was
biased +22 cm on a 180 degree arc. Full-circle recovery tests do not catch this
class of error; the partial-arc test does.

## DEC-012 — Open3D dropped from the stack

**Status:** accepted 2026-08-24
**Reason:** nothing needed it. `scipy.spatial.cKDTree` covers neighbour queries,
`scipy.ndimage.label` covers 2D connected components, `ConvexHull` covers the
tape comparator, matplotlib covers 2D review. Docs 03 section 8 already warns that
Open3D DBSCAN precomputes neighbourhoods and becomes memory-hostile at scale.
**Revisit if:** interactive 3D review (M7) is wanted, where Open3D or a web viewer
becomes a real candidate.

## DEC-013 — status, review_state and selected_model separated

**Status:** accepted, implemented 2026-08-24
**Reason:** docs 03 phase 1 asks for "no automatic winner, require manual
selection". Taken literally, every tree becomes REVIEW_REQUIRED and the status
field carries no information. Three concerns are separated instead: `status` is
the scientific verdict on the evidence, `review_state` is the human workflow state
(always PENDING until a person acts), and `selected_model` carries
`selection_is_recommendation=True` whenever `decision.automatic_selection` is
false. Nothing is silently auto-accepted, and the status still says what the tool
concluded.
**Extended 2026-08-24** with a `report_diameter` distinction, because the two
kinds of review are not the same thing. A *soft* flag means "here is a number,
please check it" and keeps the number. A *hard* failure — inadequate angular
coverage, an oversized gap, a deformity at breast height — means the geometry does
not constrain a diameter, so the headline `dbh_cm` is withheld while every
candidate fit is still exported. Without this, two vegetation clumps on the sample
scan were reported at 131 cm and 362 cm with REVIEW_REQUIRED beside them.
**Also:** status is derived from the model actually recommended, so
`ACCEPTED_IRREGULAR` can never appear next to a reported circle diameter.

## DEC-014 — Contamination-versus-irregularity discriminator

**Status:** accepted, implemented 2026-08-24. **Thresholds provisional.**
**Reason:** this is one of the open scientific questions, and real data forces it.
An attached liana or vegetation clump produces exactly the signature of a fluted
stem — large residuals, non-convex outline, radial roughness — and an outline model
traces the contaminant and reports it as stem shape. Before this discriminator the
tool recommended a contaminated outline of 27.6 cm for a stem RANSAC measured at
25.1 cm, and labelled it ACCEPTED_IRREGULAR.
**Implemented as:** `evaluation/compare.py::classify_radial_anomaly`, using two
signals. Shell thickness: a stem surface is thin at any given angle even when
fluted, while vegetation is volumetric (per-sector radial IQR). One-sided radial
excess: flutes deviate both inward and outward about the surface RANSAC locks
onto, so almost-entirely-outward anomaries beyond bark roughness indicate attached
material. An outline refitted on RANSAC inliers is exported as a diagnostic, so
"how much does cleaning move the answer?" is visible.
**Consequences:** CONTAMINATION_SUSPECTED forces the robust circle as the
recommendation and leaves the shape class unresolved, because the ellipse and
outline are both fitted to all section points and describe the contaminant too.
**Not resolved:** this is a diagnostic with five provisional thresholds, not an
answer to the open question. It needs reference trees with known contamination.

## DEC-015 — Provisional parameters are listed, not left null

**Status:** accepted, implemented 2026-08-24
**Reason:** docs 03 section 9 asks that uncalibrated parameters stay `null`. A
null-valued config cannot run, so the equivalent honest position is taken instead:
every threshold has a documented working default, and every default not validated
against field measurements is named in `config.PROVISIONAL_PARAMETERS` (33 entries
at the time of this decision, 37 after DEC-016) and stamped into the provenance of
every export along with an explicit
`calibration_status` string. Removing a name from that tuple is a deliberate act
that belongs in this log together with its validation evidence.

## DEC-016 — Ellipse acceptance gated on angular support and shell thinness

**Status:** accepted, implemented 2026-08-25
**Supersedes:** nothing. Completes a requirement stated in docs 02 section 6 at
handover time and only half implemented.

**Reason.** Docs 02 section 6 said from the outset that "ellipse acceptance should
require sufficient angular coverage and should be compared with the circle". Only
the axis-ratio half of that was built. Docs 02 section 22 then measured the price
on the sample scan: the ellipse was "valid" on 9 of 10 real targets, including
vegetation clumps where RANSAC declined, and deviated by up to 73 cm from the
per-tree median. Section 24 reproduces the mechanism on synthetic geometry with
known truth. Two findings drove this decision:

1. On a **120 degree arc of a truly circular stem** the ellipse reported a 1.45
   axis ratio and a diameter 12.3 cm wrong, and `attribute_ellipticity` read that
   ratio as genuine ovality. So the failure was not extra error, it was a
   *manufactured shape class*. At 90 degrees: ratio 2.27, error 23.2 cm.
2. The ellipse returned a **valid fit to a structureless blob containing no stem
   at all**, on all 40 seeds, where RANSAC declined on all 40.

**Decision.** An ellipse is accepted only when it is identifiable. Three gates are
added alongside the existing axis-ratio check, all in the new
`config.EllipseConfig`, all added to `PROVISIONAL_PARAMETERS` (33 -> 37 entries):

| gate | default | argument |
| --- | --- | --- |
| `min_coverage_fraction` | 0.70 | five free parameters need more of the circumference than a circle's three |
| `max_gap_deg` | 100.0 | an ellipse across a wider unobserved arc is extrapolated, matching the `outline.max_bridge_gap_deg` argument |
| `max_normalised_residual` | 0.05 | radial rmse / fitted diameter: a stem is a thin shell, vegetation is a volume |
| `max_axis_ratio` | 3.0 | unchanged; previously hard-coded in `measure.py`, now configurable |

**Implementation.** `fitting/ellipse.py::fit_ellipse` takes the four thresholds and
a coverage bin size. Coverage is computed with the shared
`evaluation.coverage.angular_coverage` about the fitted centre, on
`cfg.coverage.angular_bin_deg`, so the number that drives the gate is the same
number the export shows. `measure.py` passes them from config for both the primary
fit and the bootstrap resamples, so the bootstrap measures the same estimator.
Two sweeps, `ellipse_coverage` and `ellipse_shell`, are registered for calibration.

**The fit is still always computed and always exported.** Only `valid` changes. A
rejected ellipse keeps its centre, axes, ratio, rotation and residuals, plus a
named reason per failed gate and the threshold values applied, so a reviewer sees
what the data would have implied. This preserves "fit every candidate model before
judging".

**Why the coverage threshold is 0.70 and not lower.** Coverage measured about the
*fitted* centre is not monotone in arc length: it falls to 0.49 at a 150 and 120
degree arc and rises again to 0.53 at 90 degrees, because a badly constrained fit
relocates its own centre. A threshold below about 0.55 therefore does not mean what
it appears to mean and would readmit the worst cases. 0.70 sits clear of that fold.
The cost is visible and accepted: a 240 degree arc is declined although its error
was 0.10 cm. A circle remains available there.

**What this gate does not claim.** `max_normalised_residual` is a model-adequacy
test — "these points are not an ellipse shell" — and never a verdict on why. A
short arc, an attached liana and heavy fluting all fail it, and the measured ranges
overlap (flute amplitude 0.15 gives 0.053, a 0.22 m clump gives 0.066, amplitude
0.25 gives 0.088). That overlap is the still-open question of DEC-014 reappearing,
not a property of this gate. Attribution stays with `classify_radial_anomaly`.

**Consequences to expect.**

- A declined ellipse makes `attribute_ellipticity` return `NO_ELLIPSE_FIT`, which
  in `assess` falls through to `REVIEW_REQUIRED` with reason
  `shape_attribution_no_ellipse_fit`. That is a soft review: a diameter is still
  reported. Trees whose shape genuinely cannot be attributed will move from
  `ACCEPTED_CIRCULAR` to `REVIEW_REQUIRED`, which is the honest label.
- **Model-agreement statistics are no longer comparable across this change.** The
  model most often excluded is also the one that was widening the spread, so docs
  02 section 22 must be regenerated before it is quoted again. It is annotated to
  that effect.
- The four non-robust circle fits are untouched and are still valid on 9 of 10
  sample-scan targets including vegetation clumps. This decision fixes the ellipse
  row only.

**How to revisit.** All four thresholds are provisional. Once
`data/reference_trees.csv` exists, run `dbh experiment -e ellipse_coverage -e
ellipse_shell -r ...`: the gates trade measurement count against error, and a
reference table turns that trade into a curve. Two specific questions to settle
then: whether 0.70 coverage is needlessly strict for stems between 210 and 270
degrees of arc, and whether real bark roughness on a large tropical stem exceeds
0.05 normalised residual often enough to matter.

# 11. Open scientific questions

Claude Code/developer must not silently resolve these.

Still open, with what implementation has added:

- [ ] **Exact field DBH convention on slope.** Sharpened by DEC-010: the datum is
      now explicitly one point, currently the ground plane at the stem centre. On a
      25 degree slope the ground varies ~0.45 m across a 1 m stem, so "uphill side"
      versus "centre" is a real difference. Needs a protocol decision, not code.
- [ ] **Horizontal versus stem-normal as the reported section.** Both are now
      computed and exported (DEC-007), and the difference is measured per tree
      (`horizontal_minus_stem_normal_cm`). Which one to report is experiment B.
- [ ] **What to report for an elliptical stem.** Axes, rotation, area-equivalent
      and mean-axes diameters are all exported. The reporting choice is unmade.
- [ ] **Where the ellipse acceptance gates belong.** DEC-016 set coverage 0.70,
      gap 100 deg and normalised residual 0.05 from synthetic geometry, which can
      say where the estimator stops being identifiable but not where the useful
      trade sits. Two sub-questions: is 0.70 needlessly strict between 210 and 270
      degrees of arc (error there was under 0.3 cm on synthetic data), and does
      real bark roughness on a large tropical stem exceed 0.05 normalised residual
      often enough to push the ellipse out of the comparison on sound stems?
      Sweeps `ellipse_coverage` and `ellipse_shell` answer both with a reference
      table.
- [ ] **Buttress/deformity protocol.** Detection exists (taper threshold, status
      INVALID_MEASUREMENT_HEIGHT, no invented number) but the follow-up workflow —
      measure above the buttress and record the height — is not implemented.
- [ ] **Minimum acceptable angular coverage** (provisional 0.60) and
      **maximum acceptable gap** (provisional 120 deg). These two gate whether a
      number is emitted at all, so they are the highest-value thresholds to
      calibrate.
- [ ] **Best slice thickness for BLK2GO density.** Provisional 10 cm. Note the
      sample scan has ~24,500 points/m2, so thickness is not point-starved here and
      a thinner slice may be affordable; experiment C.
- [ ] **Best ground classifier and parameters** — CSF/SMRF still unbenchmarked
      (DEC-006), experiment E.
- [ ] **Whether local taper interpolation improves field-referenced accuracy.**
      All three height strategies are computed and exported every run
      (`decision.primary_dbh_source` switches the headline); experiment D is now a
      config flag away, but needs reference trees.
- [ ] **Irregular stem versus contaminated data.** Partially addressed by DEC-014
      with two measured discriminators, but its five thresholds are provisional and
      unvalidated. **This is the open question most likely to produce wrong shape
      classes in the field.**
- [ ] **Whether automatic model selection can be calibrated for unattended batch
      use.** Unchanged: `automatic_selection` stays false, and every selection is
      flagged as a recommendation.

---

# 12. Build log

Use this exact-ish format.

## YYYY-MM-DD — Session title

### Goal
What this session attempted.

### Work completed
- ...

### Tests
- command:
- result:

### Scientific decisions
- none / list with rationale

### Known issues
- ...

### Files changed
- ...

### Current milestone
`M#`

### Next exact step
One small concrete next action.

---

## 2026-08-24 — Review of the handover, then M0-M4 implemented

### Goal

Review the three handover documents against the sample scan, agree or disagree
explicitly, then build the smallest testable vertical slice and extend it as far as
the evidence allowed.

### Work completed

- Reviewed the plan. Agreed with the architecture and the scientific framing;
  changed ten things, recorded as DEC-006 to DEC-015.
- Characterised the sample scan: LAS 1.2, point format 2, 35,488,852 points,
  0.92 GB, no CRS, classification empty, 59 x 55 m, 30.8 m relief, ~24,500
  points/m2. Reads in ~3 s, so full passes are affordable.
- **M0** project skeleton, `pyproject.toml`, CLI (`inspect`, `config`, `ground`,
  `detect`, `measure`), serialisable `RunConfig` with `PROVISIONAL_PARAMETERS`.
- **M1** synthetic generators with known ground truth; circle (algebraic, Taubin,
  Pratt, geometric), constrained ellipse, deterministic RANSAC, radial-median
  outline; coverage, bootstrap, comparison, profile, decision layer.
- **M2/M3** chunked LAS I/O with unit and CRS validation; despiked lowest-point
  ground surface; robust per-stem ground datum with quality verdict; stem axis;
  horizontal and stem-normal sections; review figures; CSV/JSON export.
- **M4** five heights, per-model profiles, local taper fit, anomaly flags, and all
  three height strategies exported.
- Measured 10 targets on the sample scan; 5 produced a DBH, 5 were refused.

### Tests

    command: ./.venv/Scripts/python.exe -m pytest -q
    result:  83 passed in ~22 s

Coverage of note: exact circle recovery to 1e-9; translation/scale equivariance;
measured Kasa arc bias versus Pratt/Taubin; scatter growth as coverage shrinks;
RANSAC determinism and 3x advantage under contamination; ellipse parameter and
rotation recovery; fluted area-equivalent versus convex-perimeter; outline refusing
to bridge a large gap; ground despiking; datum recovery on a slope; axis tilt
recovery at 0/10/20 degrees; lean bias present in horizontal and absent in
stem-normal sections; contamination versus fluting classification; one-sided stem
flagged not answered; provenance contents; bit-identical reproducibility; and the
whole pipeline through a generated LAS fixture.

### Scientific decisions

DEC-006 through DEC-015, in the decisions log above. The three that change numbers
most: DEC-010 (datum versus cut plane), DEC-011 (Pratt/Taubin), DEC-014
(contamination versus irregularity).

### Bugs found and fixed during the session, worth remembering

1. **Taubin coefficients crossed with Pratt.** Used `Mzz` where `Var_z` was
   needed. Recovered a full circle to 1e-16 and was biased +22 cm on a half arc.
   Full-coverage tests cannot catch this; the partial-arc bias test can.
2. **Non-robust seeding.** An ordinary circle fit on a wide breast-height slice is
   meaningless in dense forest, and the section radius then never tightens onto the
   stem. Produced 2 m diameters with 6-29 cm RMSE. Fixed by seeding with RANSAC.
3. **Per-point HAG used to cut sections.** DEC-010. Terrain-following cut plane;
   ground slope added to stem lean.
4. **Outline traced contamination and was reported as stem shape.** DEC-014.
5. **`INVALID_MEASUREMENT_HEIGHT` claimed on inadequate data.** A deformity verdict
   is a claim about a tree and needs trustworthy diameters at several heights;
   data-adequacy checks now run first.
6. **Hard failures still emitted a number** (131 cm, 362 cm on vegetation clumps).
   Fixed with the `report_diameter` distinction in DEC-013.
7. An ellipse-validity probe reused `check_plausible_diameter`, which only ever
   invalidates, so every ellipse was rejected.

### Sample-scan results (uncalibrated, no field reference)

| tree | DBH | status | confidence | model | lean | coverage | RMSE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | 24.7 cm | REVIEW_REQUIRED | LOW | circle_ransac | 9.8 deg | 97% | 5.0 mm |
| S02 | 18.9 cm | ACCEPTED_IRREGULAR | MEDIUM | outline_radial_median | 9.2 deg | 100% | 6.4 mm |
| S05 | 33.2 cm | REVIEW_REQUIRED | LOW | circle_ransac | 10.5 deg | 97% | 5.2 mm |
| S06 | 21.3 cm | REVIEW_REQUIRED | LOW | circle_ransac | 5.5 deg | 97% | 5.2 mm |
| S09 | 18.9 cm | ACCEPTED_IRREGULAR | HIGH | outline_radial_median | 9.3 deg | 100% | 6.4 mm |
| S03, S08, S10 | refused | INVALID_MEASUREMENT_HEIGHT | — | — | — | — | — |
| S04, S07 | refused | REVIEW_REQUIRED (hard) | — | — | — | — | — |

S09 carries `seed_drifted_1.37m_from_requested_location` and is the same stem as
S02 — the targets came from the unvalidated detector, and two of them pointed at
one tree. The drift warning is what makes that visible.

**These numbers have no accuracy claim attached.** There is no field reference, so
they demonstrate that the pipeline runs and that its diagnostics behave sensibly.
Nothing more.

### Known issues

1. **No calibration.** 33 provisional parameters. Highest-value targets:
   `coverage.min_coverage_fraction`, `coverage.max_gap_deg`, and the five DEC-014
   contamination thresholds.
2. **Irregularity thresholds fire readily on real bark.** With
   `irregular_convexity_deficit_min = 0.02` and
   `irregular_roughness_ratio_min = 0.05`, ordinary stems on the sample scan reach
   deficits of 0.05-0.08. Either bark roughness genuinely exceeds these values or
   the thresholds are too tight; only reference trees can say which.
3. **Detection is not validated.** `stems/candidates.py` produced 51 "accepted"
   candidates on the sample scan; measurement refused half of the ten inspected.
   Precision and recall are unmeasured. Do not use for inventory.
4. **One full file pass per tree.** `crop_cylinder` rescans the file per target
   (~1 s here, but it scales with tree count). A tile index or a single pass
   collecting all ROIs is the fix when batching matters.
5. **Ellipse residuals are radial, not orthogonal.** Second order in eccentricity,
   documented in `fitting/ellipse.py`, not corrected.
6. **The lean correction is unproven on real data.** Validated on synthetic stems
   at 20 degrees lean, but the sample scan only offers 5-10 degrees, where the
   predicted 0.05-0.28 cm effect is smaller than the observed scatter and
   inconsistent in sign (docs 02 section 20.3). Needs either strongly leaning
   reference trees or repeat scans to settle.
7. **Axis estimation fails silently to vertical** on messy stems
   (`axis_estimation_failed_using_vertical`). It warns, but a failed axis means the
   stem-normal section is just a horizontal one.
8. **No E57 path.** LAS/LAZ only (DEC-002).
9. `dbh detect` loads a decimated cloud into memory; fine at this scale, not
   general.

### Files changed

Created the whole `src/dbh_tool` tree, `tests/{unit,synthetic,integration}`,
`pyproject.toml`, `README.md`, `data/targets_sample.json`. Moved the three handover
documents into `docs/`. Updated all three.

### Current milestone

M5 — reference validation. Blocked on field data.

### Next exact step

Create `data/reference_trees.csv` with columns
`tree_id, field_dbh_cm, measurement_height_m, measurement_method, shape_class,
buttressed, leaning, notes, x, y` and populate it with tape or caliper
measurements for trees identifiable in the sample scan. Everything in M5 —
experiments A to E, all five of which are already a config flag or a column away —
is blocked on that file and nothing else.

## 2026-08-25 — M5 harness, and the parameter sweeps that need no ground truth

### Goal

Continue into M5. The documented next step was to create the reference table, which
is blocked on field data that does not exist. So: build everything M5 needs so that
dropping the file in is sufficient, and run the experiments that do not require
ground truth.

### Work completed

- `evaluation/benchmark.py`: reference-table loader with validation, per-tree
  comparator selection driven by `measurement_method` (DEC-009 wired end to end),
  bias/MAE/RMSE/relative-RMSE/max-error, stratification by status, shape class,
  size class, coverage class and confidence band, dev/holdout splitting, and a
  summary that cannot print accuracy without the review rate beside it.
- `evaluation/experiments.py`: dotted-path config mutation, six parameter sweeps,
  and the three experiments that need no sweep because every run already computes
  them (model agreement, section geometry, height strategy).
- `io/las.py::crop_many`: all regions of interest in one pass. Fixes known issue 4;
  10 targets on the sample scan now crop in 4.8 s total instead of one pass each.
- `cli.py`: `dbh benchmark` and `dbh experiment`; `measure` now uses the shared
  target loader and the single-pass crop.
- `data/reference_trees.csv` (header only) and `data/README.md` documenting the
  schema, why `measurement_method` changes the comparator, and the dev/holdout
  discipline.
- Ran all six sweeps and all three free experiments on the sample scan.

### Tests

    command: ./.venv/Scripts/python.exe -m pytest -q
    result:  117 passed in ~68 s   (was 83)

34 new tests. The benchmark arithmetic is checked against hand-computed
bias/MAE/RMSE; the disclosure rules are asserted directly (a refused tree raises
the review rate and does not enter the error statistics; the summary refuses to
print accuracy when nothing was reported; a comparator fallback is always named);
comparator selection is tested per measurement method; and the M5 path is exercised
end to end on the synthetic LAS scene, including the CLI. `crop_many` is tested to
return exactly what per-tree cropping returns.

### Scientific decisions

None new. The results inform, but do not yet settle, several open questions; see
docs 02 sections 21 to 23.

### Findings worth acting on

1. **`ransac_circle.residual_threshold_m` is the parameter to calibrate first.**
   7.84% median sensitivity, six times slice thickness, and it also gates the
   contamination diagnostic. At 0.040 m it adopts attached vegetation as inliers.
2. **Slice thickness barely matters at this point density**, contradicting the
   expectation in docs 02 section 4.1. Experiment C is low priority for BLK2GO-class
   density, though not necessarily for sparse scans.
3. **The non-robust models succeed where the robust one declines.** RANSAC and the
   outline were valid on 5 of 10 targets; the ordinary circle fits and the ellipse
   on 9, including vegetation clumps. Silent success on bad data is worse than
   refusal.
4. **The ellipse is the riskiest model on real data**: valid on 9 of 10 targets and
   deviating up to 73 cm from the per-tree median. Its gates should probably tighten.
5. `coverage.angular_bin_deg` and `outline.n_sectors` are inert (0.00 cm). Deprioritise.

### Bugs found and fixed

8. **`estimate_stem_axis` crashed on a length mismatch** when a later refinement
   iteration dropped below three usable bins: it broke out of the loop leaving the
   working bin-elevation list shorter than the bin heights kept from the previous
   iteration, and interpolating the reference elevation then raised "fp and xp are
   not of the same length". Found by the slice-thickness sweep, not by the existing
   tests, because it needs a setting that makes a *later* pass fail. Fixed by
   tracking the last successful iteration's elevations separately, with a guard and
   a warning if they are unavailable. Regression test added.
9. A non-numeric `field_dbh_cm` raised a bare float-conversion error with no line
   number. A hand-entered field table is exactly where typos live, so the error now
   names the file, the line and the offending value.

### Known issues

Items 1 to 9 from the previous entry still stand, with these changes: item 4
(one file pass per tree) is fixed by `crop_many`; item 2 (irregularity thresholds
firing on ordinary bark) is unchanged and now has company in the form of the
ellipse over-validity finding above. New:

10. **The height-strategy metric is partly circular.** Deviation from the median of
    three strategies flatters whichever tends to sit in the middle. It is reported
    with that caveat; a field reference replaces it with a real answer.
11. **Sensitivity is not accuracy.** Everything in docs 02 sections 21 to 23 says
    which parameters *move* the answer, never which value is *right*. Nothing in
    the code can stop that distinction being forgotten when the numbers are quoted;
    the summaries label themselves `INTERNAL SENSITIVITY ONLY` for that reason.
12. Sweeps re-measure serially and take a few minutes for six trees across four
    values. Fine for calibration work, too slow for a large reference set.

### Files changed

Added `src/dbh_tool/evaluation/{benchmark,experiments}.py`,
`tests/unit/test_benchmark_and_experiments.py`, `data/reference_trees.csv`,
`data/README.md`. Extended `io/las.py`, `cli.py`, `stems/axis.py`,
`tests/integration/test_end_to_end_las.py`,
`tests/synthetic/test_ground_axis_slices.py`, `README.md`, docs 02 and 03.

### Current milestone

M5 — reference validation. Harness complete; blocked only on field data.

### Next exact step

Populate `data/reference_trees.csv` with tape or caliper measurements for trees
identifiable in the sample scan, setting `measurement_method` correctly on every
row, then run:

    dbh benchmark "Las-Sample/Yaloch Maya.las" -r data/reference_trees.csv --outdir out

and, once ten or more trees are in the dev split:

    dbh experiment "Las-Sample/Yaloch Maya.las" --targets data/targets_sample.json \
        -e all -r data/reference_trees.csv --outdir out

which converts every sensitivity number in docs 02 sections 21 to 23 into a
field-referenced error, starting with `ransac_circle.residual_threshold_m`.

## 2026-08-25 (second session) — DEC-016: the ellipse now has to earn its five parameters

### Goal

M5 is still blocked: `data/reference_trees.csv` is header-only and no field
measurements were supplied, so nothing was invented and the benchmark was not run.
Of the three unblocked options in the handover, took the ellipse validity gates,
because the previous session measured it as the most dangerous model in the set
(valid on 9 of 10 real targets, deviating up to 73 cm) and because docs 02 section
6 had asked for exactly these gates since handover and only half of them existed.

Note for the next session: this was a **fresh checkout with no `.venv` and no
`Las-Sample/`**. The venv was rebuilt from `pyproject.toml` (Python 3.14.7); the
0.92 GB sample scan is gitignored and absent, so nothing in this entry is measured
on real data. That is why the evidence here is synthetic geometry with known truth
plus the integration-fixture plot.

### Work completed

- `config.EllipseConfig`, a new section: `max_axis_ratio` (3.0, previously
  hard-coded in `measure.py` and unreachable from config), `min_coverage_fraction`
  (0.70), `max_gap_deg` (100.0), `max_normalised_residual` (0.05). All four added
  to `PROVISIONAL_PARAMETERS`, 33 -> 37 entries.
- `fitting/ellipse.py`: the three new gates. Angular support comes from the shared
  `evaluation.coverage.angular_coverage` about the fitted centre on
  `cfg.coverage.angular_bin_deg`, so the gate and the exported `angular_coverage`
  are the same number rather than two implementations that can drift. Every gate is
  evaluated and every failure named — a short arc *and* a thick shell is different
  evidence from either alone. `extra` now carries `normalised_radial_residual` and
  the four `gate_*` thresholds actually applied.
- `measure.py` passes the config for the primary fit and for the bootstrap
  resamples, so the bootstrap measures the same estimator instead of an ungated one.
- `evaluation/experiments.py`: sweeps `ellipse_coverage` and `ellipse_shell`.
- Docs 02 section 24, the measurements; sections 6, 21 and 22 annotated.

### Tests

    command: ./.venv/Scripts/python.exe -m pytest -q
    result:  128 passed in ~83 s   (was 117)

11 new tests. They assert the behaviour, not the thresholds: a 120 degree arc is
declined *and* the number it would have reported is asserted wrong by more than
5 cm, so the test states its own reason for existing. Also covered: the wide-gap
case, the clump case (which has full coverage, so only the shell gate can catch
it), seven good sections that must all survive, a declined fit still carrying its
geometry and reasons, each gate individually re-admitting its case when loosened
(which proves the threshold caused the rejection and not a fitting failure), the
gate agreeing with the exported coverage, config round-trip through YAML with the
names present in `PROVISIONAL_PARAMETERS`, and the downstream `NO_ELLIPSE_FIT`
consequence.

One test exists purely to stop a plausible mistake:
`test_coverage_gate_is_independent_of_eccentricity`. A complete outline of an
ellipse surrounds its own centre whatever the axis ratio, so coverage stays 1.00 up
to ratio 12. If that ever stopped being true, the coverage gate would have quietly
become an eccentricity gate and would suppress the oval stems the model exists to
find.

### Scientific decisions

**DEC-016** — ellipse acceptance gated on angular support and shell thinness. Full
entry in section 10, measurements in docs 02 section 24.

### Findings worth acting on

1. **The pre-DEC-016 failure was a manufactured shape class, not just error.** On a
   120 degree arc of a *circular* stem the ellipse reported axis ratio 1.45 and a
   12.3 cm diameter error; at 90 degrees, ratio 2.27 and 23.2 cm.
   `attribute_ellipticity` then read the invented ratio as genuine ovality. A wrong
   number is bad; a wrong number carrying a confident shape label is worse.
2. **The ellipse fitted a structureless blob with no stem in it**, valid on 40 of
   40 seeds, where RANSAC declined on 40 of 40.
3. **Coverage about a fitted centre is not monotone in arc length.** 0.49 at a 150
   and a 120 degree arc, back up to 0.53 at 90 degrees, because a badly constrained
   fit relocates its own centre. Any gate threshold below ~0.55 is therefore
   meaningless and would readmit the worst cases. This is a trap for anyone tempted
   to loosen the gate later — and it applies to
   `coverage.min_coverage_fraction = 0.60` too, which is uncomfortably close to the
   fold. Largest gap is monotone down to 180 degrees, which is why both are gated.
4. **Shell thinness cannot separate heavy fluting from contamination**, by
   measurement: flute amplitude 0.15 gives 0.053, a 0.22 m clump 0.066, amplitude
   0.25 gives 0.088. The overlap is the DEC-014 open question reappearing. The gate
   is therefore documented as model adequacy only.
5. **On the integration plot the change costs nothing and fixes one tree.** The
   one-sided stem (110 degree arc, true D 0.34 m) had an ellipse of 0.196 m — 14.4
   cm too small, ratio 1.74 — and it was the *recommended* model; it is now declined
   and the recommendation is `circle_ransac`, with the shape verdict moving from a
   false `GENUINELY_OVAL` to `NO_ELLIPSE_FIT`. Upright, leaning and fluted stems are
   unchanged, to the millimetre.

### Bugs found and fixed

10. `measure.py` called `fit_ellipse(xy, max_axis_ratio=3.0)` with the value
    hard-coded, so the one ellipse threshold that did exist was unreachable from the
    run configuration and silently absent from `PROVISIONAL_PARAMETERS`. Every
    export therefore under-reported its own uncalibrated surface. Now config-driven.
11. The bootstrap fitter dict passed bare `fit_ellipse`, so resamples were judged by
    library defaults rather than by the run's configured thresholds. Harmless while
    the defaults matched; wrong the moment anyone swept a gate, which the two new
    sweeps now do. Now a closure over the same config.

### Known issues

Items 1-3 and 5-12 from the previous entries stand. Item 4 was fixed by
`crop_many`. Changes and additions:

- Item 2 (irregularity thresholds firing on ordinary bark) is unchanged, and the
  ellipse over-validity finding that used to sit beside it is now addressed.
- **Item 11 (sensitivity is not accuracy) now has a sibling that is easier to get
  wrong.** Docs 02 section 24 is *synthetic accuracy*, which is a real error against
  known truth — unlike sections 21-23 — but it is accuracy on generated geometry, so
  it says where the estimator breaks, not how the tool performs on a tree. Do not
  quote section 24 as field accuracy either.

New:

13. **Docs 02 section 22 is stale and must be regenerated before it is quoted.**
    Every row is a deviation from the per-tree median of the *valid* models, and
    DEC-016 changes which models are valid. Dropping the model that was widening the
    spread will narrow every other row without any measurement improving. Annotated
    in place, but it needs a re-run on the sample scan.
14. **The four non-robust circle fits are still ungated.** DEC-016 fixed the ellipse
    row only. `circle_algebraic`, `circle_taubin`, `circle_geometric` and
    `circle_pratt` were valid on 9 of 10 sample-scan targets including vegetation
    clumps, with max deviations of 5.4 to 11.2 cm. The shell-thinness gate is
    model-independent and would transfer directly, but doing so changes the baseline
    every published circle-fit comparison rests on, so it wants its own decision and
    its own measurement rather than being bolted on here.
15. **The gates are unvalidated on real stems.** Every number in docs 02 section 24
    comes from synthetic sections. The fluted case is the one to watch: at flute
    amplitude 0.12 the normalised residual is 0.0426 against a 0.05 gate, so real
    tropical fluting will push the ellipse out of the comparison on some sound
    stems. That is the correct model choice, but the frequency is unknown.
16. **`coverage.min_coverage_fraction = 0.60` sits close to the non-monotonicity
    fold** described in finding 3. It gates whether a headline diameter is emitted at
    all, so it is worth re-examining with the same synthetic method used for the
    ellipse — the argument is not ellipse-specific.

### Files changed

`src/dbh_tool/config.py` (new `EllipseConfig`, wired into `RunConfig`, `_NESTED` and
`PROVISIONAL_PARAMETERS`), `src/dbh_tool/fitting/ellipse.py`,
`src/dbh_tool/measure.py`, `src/dbh_tool/evaluation/experiments.py`,
`tests/unit/test_ellipse_outline_coverage.py`,
`tests/unit/test_benchmark_and_experiments.py`,
`tests/unit/test_ransac_and_evaluation.py`, docs 02 (new section 24; sections 6, 21,
22 annotated), docs 03 (DEC-016, open questions, this entry, section 13),
`CLAUDE.md`, `README.md`.

### Current milestone

M5 — reference validation. Harness complete; still blocked only on field data.
M6 still unvalidated. M7 still not started.

### Next exact step

Unchanged and still the highest-value work: populate `data/reference_trees.csv`
(schema and rules in `data/README.md`), then

    dbh benchmark "Las-Sample/Yaloch Maya.las" -r data/reference_trees.csv --outdir out

calibrating `ransac_circle.residual_threshold_m` first.

If field data still has not arrived, the two best unblocked jobs are now:

1. **Regenerate docs 02 section 22** on the sample scan (known issue 13), which also
   measures how often DEC-016 declines the ellipse on real stems and settles known
   issue 15. Needs `Las-Sample/Yaloch Maya.las` restored locally — ask.
2. **M7 review persistence** (approve/reject/override), which needs no point cloud
   at all. `review_state` is already a separate field returning `PENDING` from
   `evaluation/confidence.py`, and DEC-013 separated it from `status` and
   `selected_model` for exactly this purpose, so the data model is ready.

Validating `stems/candidates.py` is still available but needs a manually annotated
stem list, and annotating one requires the sample scan and a person willing to stand
behind the annotations. Do not generate that list from tool output.

## 2026-08-25 (third session) — DEC-016 validated on the real scan, and section 22 regenerated

### Goal

The sample scan was restored locally, which unblocked the two jobs the previous
entry named: regenerate docs 02 section 22 (known issue 13) and find out how often
the DEC-016 ellipse gates decline a real stem (known issue 15). Also wrote an
operator guide, `docs/00_RUNNING_THE_TOOL.md`, which did not exist.

M5 is **still blocked**: the user supplied a point cloud, not field measurements.
`data/reference_trees.csv` is still header-only and `dbh benchmark` was not run.
Nothing was invented to fill it.

### Work completed

- Confirmed the restored file matches the documented sample exactly: 35,488,852
  points, 59.36 x 55.27 x 30.78 m, no CRS, empty classification, both expected
  warnings. Verified `git status --untracked-files=all` still shows no LAS —
  gitignore is holding.
- Ran `dbh measure` on all 10 sample targets (`--roi 4`): 5 report a headline
  diameter (S01 24.7, S02 18.9, S05 33.2, S06 21.3, S09 18.9 cm), 5 refuse. Review
  and ground PNGs regenerated in `out/`.
- Ran `dbh experiment -e models -e geometry -e height_strategy`.
- Wrote a before/after harness measuring all 10 targets twice, gates open and gates
  at DEC-016 defaults, on one shared ground grid. **The gates-open column reproduces
  the old section 22 table exactly** (5.36 / 7.47 / 11.22 / 1.98 / 5.36 / 73.15 /
  0.57 / 183.58 cm), which is what makes the comparison trustworthy.
- Docs 02 section 22 fully regenerated with both columns, per-target gate
  attribution, and a correction (below). New section 24.5 records the real-data
  validation. Section 24.4's forward-looking claim replaced by a pointer to it.
- New `docs/00_RUNNING_THE_TOOL.md`: setup from a bare checkout, every command with
  expected output, how to read status/confidence/recommendation, the two kinds of
  `REVIEW_REQUIRED`, a troubleshooting table, and the rules that make the output
  worth anything. Linked first from the README docs table.

### Tests

    command: ./.venv/Scripts/python.exe -m pytest -q
    result:  128 passed in ~70 s

No test changes this session; no source changes either. Everything here is
measurement and documentation.

### Scientific decisions

None new. DEC-016 is unchanged — this session validated it rather than altering it.

### Findings worth acting on

1. **DEC-016 selected the well-behaved subset, it did not merely thin the set.**
   Ellipse validity 9 of 10 -> 3 of 10. The three survivors deviate from the median
   of the other models by 0.1 cm median / 0.2 cm max; the six declined by 1.6 cm
   median / 78.9 cm max. Max deviation on the ellipse row fell 73.15 -> 0.15 cm.
2. **Both new gates were necessary; neither would have sufficed.** Shell thinness
   declined S01, S03, S06, S10 — all with angular coverage between 0.81 and 0.97,
   so no coverage threshold would have reached them. Coverage declined S04, S07,
   S08. **The gap gate never fired alone on real data**; coverage always caught
   those cases first. It stays as the cheap guard against the two-clean-arcs
   geometry that the synthetic panel covers and this scan happens not to contain.
3. **No reported diameter, status or confidence band changed on any of the 10
   targets.** The gates cost nothing measurable on this scan. Everything DEC-016
   changed here is shape attribution and model comparison.
4. **Six of ten targets had been carrying a shape class their fit could not
   support.** S01, S06, S08 were `OVAL_BEYOND_LEAN`; S03, S10 `GENUINELY_OVAL`; S04
   `CIRCULAR` — all now `NO_ELLIPSE_FIT`. S04 is the instructive one: a *circular*
   verdict sounds harmless but came from a 0.54-coverage fit 2.7 cm off the other
   models. A wrong "circular" is as unfounded as a wrong "oval".
5. **Known issue 14 is materially worse than the previous entry described, and now
   has hard numbers.** On S10 `circle_algebraic` reports a 176.9 cm diameter with an
   RMSE of **246.7 cm** and is marked valid; `circle_pratt` 188.2 cm at RMSE
   260.2 cm, valid. On S07 the circle fits report ~3.6 m diameters, inside the 4.0 m
   plausibility envelope. A fit whose residual scatter exceeds its own diameter is
   not a measurement. S10's circles sit at rmse/D of roughly 1.4 against the
   ellipse gate of 0.05, so the DEC-016 shell test would decline them instantly.

### Corrections to earlier work

**A claim written into docs 02 section 22 when DEC-016 was authored was wrong.**
The annotation asserted that excluding the ellipse would *narrow* every other
row's deviation, on the reasoning that the model being dropped was the one
widening the spread. The regenerated table falsifies it: `circle_algebraic` went
5.36 -> 10.72 cm and `circle_geometric` 7.47 -> 7.82 cm, both **wider**, while
`circle_taubin` went 5.36 -> 0.86 cm and `circle_pratt` 11.22 -> 10.87 cm,
narrower. Every deviation is measured against the per-tree *median*, and removing
a model moves the median, so the effect is not directional and cannot be reasoned
out. Corrected in place as section 22.2, kept rather than deleted because the
general lesson matters: **a change in which models are valid invalidates every
agreement number in that section, in an unpredictable direction.**

### Known issues

Resolved this session:

- **13 (section 22 stale) — resolved.** Regenerated with both columns.
- **15 (gates unvalidated on real stems) — resolved for the ellipse gates.** See
  finding 1 and docs 02 section 24.5. Note the limit: with no reference table,
  "deviation from the median of the other models" is internal agreement, not
  error. It shows the declined fits were the disagreeing ones. It cannot show the
  surviving three are accurate.

Standing, with changes:

- **14 (non-robust circle fits ungated) — unchanged in status, upgraded in
  severity.** See finding 5 for the numbers. Still deliberately not fixed here:
  applying the shell test to the circles changes the baseline every published
  circle-fit comparison rests on, so it wants its own DEC entry and its own
  measurement. It is now the strongest candidate for the next scientific change.
- **16** (`coverage.min_coverage_fraction = 0.60` near the non-monotonicity fold)
  stands, untested on real data.
- Items 1-3, 5-12 from earlier entries stand.

New:

17. **`dbh detect` remains unvalidated and the duplicate-target problem is
    confirmed in this run.** S09 carries `seed_drifted_1.37m_from_requested_location`
    and reports 18.881 cm against S02's 18.869 cm — they are the same stem measured
    twice, 0.012 cm apart. That agreement is a *duplicate*, not a precision
    result, and anything that averaged the ten targets would double-count it.
18. **S09 is `ACCEPTED_IRREGULAR` with confidence `HIGH` despite a 1.37 m seed
    drift.** The drift is reported as a warning but adds no confidence penalty, so
    a tree the tool has arguably mis-located can still come out at the top band.
    Either the drift warning should cost a penalty in `assess`, or the threshold
    for accepting a drifted seed should be explicit. Needs a decision, not a
    silent tweak.

### Files changed

`docs/00_RUNNING_THE_TOOL.md` (new), `docs/02_SCIENCE_AND_METHODS.md` (section 22
regenerated and corrected, section 24.4 amended, section 24.5 added),
`docs/03_PIPELINE_AND_BUILD_LOG.md` (this entry, section 13), `README.md` (docs
table), `CLAUDE.md`. No source or test changes.

### Current milestone

M5 — reference validation. Harness complete; **still blocked only on field data.**

### Next exact step

Unchanged: populate `data/reference_trees.csv` (schema and rules in
`data/README.md`), then

    dbh benchmark "Las-Sample/Yaloch Maya.las" -r data/reference_trees.csv --outdir out

calibrating `ransac_circle.residual_threshold_m` first. The point cloud is present
now, so this runs the moment the table has rows. Reference measurements must come
from tape or caliper in the field; do not derive them from tool output.

If field data still has not arrived, the two best unblocked jobs are:

1. **Gate the non-robust circle fits** (known issue 14, finding 5). Needs a DEC
   entry, the same synthetic-then-real method used for DEC-016, and a decision
   about what happens to the section 20.2 circle-fit comparison when its baseline
   models start declining. Highest scientific value of anything left unblocked.
2. **M7 review persistence** (approve/reject/override). Needs no point cloud.
   `review_state` already returns `PENDING` as a separate field and DEC-013
   separated it from `status` and `selected_model` for this purpose.

Known issue 18 (HIGH confidence on a 1.37 m drifted seed) is a smaller, well-scoped
third option and would pair naturally with either.

## 2026-08-25 (fourth session) — M7: a review GUI, and an operator guide

### Goal

The user asked whether a GUI existed and to build one if not, themed to match the
sibling **360-pointcloud-tool** project. None existed: the interface was the CLI
plus PNGs written to a directory.

That turned out to be the natural home for **M7 (human-review workflow)**, which
DEC-013 had already prepared the data model for by separating `review_state` from
`status` and `selected_model`. So this session delivered the GUI *and* M7.

M5 is **still blocked**. A point cloud is not field data; `data/reference_trees.csv`
is still header-only and `dbh benchmark` was not run.

### Work completed

- `src/dbh_tool/gui/` — new package, four modules:
  - `theme.py`, copied verbatim from `pano360/theme.py` and extended with
    `STATUS_COLOUR` / `BAND_COLOUR` / `REVIEW_COLOUR` maps and `mpl_rc()`. Copied
    rather than imported: separate repositories, separate dependency sets, and a
    colour palette is not worth coupling them over. **If a colour changes there, it
    must change here.**
  - `widgets.py` — the scrolling fixed-width side panel, cards, kv rows, and the
    collapsible log strip, same set and same reasoning as the sibling picker.
  - `plots.py` — embedded matplotlib: a plan view (log point density) and the
    section view with the coverage rose.
  - `review.py` — M7 decision store.
  - `app.py` — the window.
- `visualization/cross_section.py`: extracted `model_boundary_xy(name, fit)`, now
  shared by the export figure and the GUI. Two implementations of "where is the
  fitted ellipse" would eventually disagree, and a review view you cannot trust is
  worse than none.
- `cli.py`: `dbh gui`, importing tkinter lazily so a Python without it still runs
  every measurement command.
- `dbh.bat` / `setup.bat`, matching the sibling project's launcher conventions
  (interpreter search order, dependency precheck, `PYTHONPATH` at `src/` so a bare
  checkout works). `dbh.bat` with no arguments opens the GUI.
- `docs/00_RUNNING_THE_TOOL.md` — the operator guide, which did not exist. Setup
  from a bare checkout, every command with expected output, how to read a result,
  the two kinds of `REVIEW_REQUIRED`, a troubleshooting table, and section 10 on
  the GUI. Linked first from the README.

### Tests

    command: ./.venv/Scripts/python.exe -m pytest -q
    result:  153 passed in ~62 s   (was 128)

25 new tests in `tests/unit/test_gui_review.py`. The widgets are not unit-tested --
Tk needs a display and the interesting logic is not in them -- but everything that
could quietly produce a wrong number is: the decision store, and the one function
that decides what a reviewed tree reports.

Separately, a scripted end-to-end pass drove the real window against the real
0.92 GB scan: load, plan view, measure all ten targets, section and profile views,
approve, reject-without-a-note, reject, confirm-a-refusal, override, reset, the
model table, and export. 23 checks, all passing.

### Scientific decisions

None. M7 is workflow, not measurement. The rules it enforces are the existing
non-negotiables; see below.

### Design positions worth keeping

1. **Nothing can turn a refusal into a diameter.** A refused section still carries
   candidate fits with numbers in them, so "override to `circle_algebraic`" is an
   obvious route to a 3.62 m diameter on S07. `resolved_diameter_cm` is the single
   place that answers "so what is the DBH", and it refuses on the same rule the
   measurement did. The override is still *recorded* -- the reviewer's opinion is
   real -- but it resolves to nothing and the stored note says why. Two tests exist
   solely to keep it that way.
2. **Approving a refusal records agreement that there is no number.** Not a number.
3. **Rejection and override require a note**; approval does not. The first two are
   disagreements with the tool's reasoning, and the next reader cannot see your
   screen.
4. **Decisions go stale when the measurement under them changes.** A decision
   describes one specific measurement; re-measuring with different settings must not
   let an approval carry silently over to a different number. `check_against`
   compares status, recommended model and diameter, and flags the row.
5. **Declined models are drawn dashed and labelled, not hidden.** Judging the
   tool's refusals is part of the job, and "the ellipse was declined" is a claim you
   can only check by seeing what it claimed.
6. **The results list never shows a number where there is none, and the word says
   *who* decided that.** `refused` is the tool declining to measure; `rejected` is a
   person rejecting a measurement the tool did make; `withdrawn` is a reported tree
   whose review resolves to nothing. One word for all three -- which is how it was
   first written -- conflates a tool refusal with a human judgement, and a screenshot
   made that obvious immediately. An empty cell reads as a bug; `0.0` would be a lie.
7. **Confidence stays a coloured word.** No bars, no meters, nothing implying a
   scale that does not exist.
8. **Decisions are written on every change, not at exit.** A GUI that loses an
   afternoon of review to a crash is worse than no persistence, because the
   reviewer believes the work is saved.
9. **No cloud work on the UI thread.** Import is two full passes over 0.92 GB and
   measurement is tens of seconds; both run on a worker and report through a queue
   the main loop polls. `_poll` reschedules itself in a `finally`, because an
   exception escaping it would strand `busy=True` and refuse every later run while
   the window looked healthy.

### Bugs found and fixed

12. **`LasInfo` has no `extent_m` attribute** -- that name exists only as a key in
    `to_dict()`; the attribute is the `extent` property. `_install_cloud` raised on
    it *after* assigning `self.info` and `self.raster` but *before* filling the
    header labels and calling `_set_enabled(True)`. The result was the worst kind of
    partial failure: the plan view rendered perfectly, the Cloud card stayed full of
    dashes, and the Measure button stayed greyed out -- which reads as "still
    loading", not "broken". **Found from a screenshot the user sent, not by the
    smoke test**, which had asserted on internal state (`app.info is not None`) that
    the half-completed install had already satisfied. The smoke test now asserts on
    what the widgets *show* and fails on any error the app logs, and a failed
    install now resets to a clean no-cloud state via `_forget_cloud` instead of
    leaving a half-loaded one.
13. **`ReviewStore` wrote itself *as* the output directory.** It decided
    file-versus-directory with `Path.is_dir()`, which is False on a first run
    because the directory does not exist yet -- so `out/` became a *file* containing
    the review JSON, after which nothing could create the directory. Now decided by
    the `.json` suffix, which does not depend on the filesystem. Two regression
    tests, including the first-run case the original tests missed by always using an
    existing `tmp_path`.
14. `tight_layout` cannot lay out polar axes and warned on every section redraw.
    Switched to the constrained layout engine, which handles both.
15. **The GUI's own graceful-degradation handling did not work**, and the claim that
    it did was in the docstring. `cmd_gui` wrapped `from .gui import launch` in an
    `except ImportError`, but `dbh_tool.gui.launch` defers importing the window until
    it is *called* -- so on a Python without tkinter the ImportError surfaced from
    inside the call, one line later, outside the handler, as a traceback. The lazy
    import that was meant to protect the CLI defeated the protection. Now the CLI
    probes `tkinter` directly, which is both precise and honest: any *other*
    ImportError from the GUI is a real bug and still raises. Regression test added,
    and it is worth noting this was found only because the claim was checked rather
    than assumed.

### Known issues

Items from the earlier entries stand. New:

19. **The GUI is not covered by the automated suite.** `tests/unit/test_gui_review.py`
    covers the decision logic; the window itself is verified by a scripted pass that
    needs a display and the sample scan, so it does not run in CI. Bug 12 is exactly
    what that gap lets through. A `pytest` marker plus a synthetic-LAS fixture would
    close most of it.
20. **`theme.py` is duplicated between the two projects.** Deliberate (see above),
    but it will drift. There is no test that they match, and there cannot easily be
    one across repositories.
21. **The plan raster is rebuilt on every import and never cached.** ~30-60 s on the
    sample scan. A `.npz` beside the cloud, keyed on the file hash already in
    `LasInfo.file_sha256_head`, would make reopening instant.
22. **Override offers every model that produced a diameter**, including
    `outline_radial_median_inliers`, which is a diagnostic rather than a competing
    interpretation (it is excluded from the model-agreement statistic for that
    reason). Overriding to it should probably require more friction, or be excluded.

### Files changed

New: `src/dbh_tool/gui/{__init__,theme,widgets,plots,review,app}.py`,
`tests/unit/test_gui_review.py`, `docs/00_RUNNING_THE_TOOL.md`, `dbh.bat`,
`setup.bat`. Modified: `src/dbh_tool/visualization/cross_section.py` (extracted
`model_boundary_xy`), `src/dbh_tool/cli.py` (`dbh gui`), `README.md`, `CLAUDE.md`,
`docs/03` (this entry, section 13).

### Current milestone

**M7 — done** for approve/reject/override with persistence, staleness detection and
an audit trail. M5 still blocked on field data. M6 still unvalidated. M8 partial.

### Next exact step

Unchanged: populate `data/reference_trees.csv` (schema and rules in
`data/README.md`), then

    dbh benchmark "Las-Sample/Yaloch Maya.las" -r data/reference_trees.csv --outdir out

calibrating `ransac_circle.residual_threshold_m` first. The cloud is present, so
this runs the moment the table has rows.

If field data still has not arrived, the best unblocked job is **gating the four
non-robust circle fits** (known issue 14): on S10 `circle_algebraic` reports
176.9 cm at an RMSE of 246.7 cm and is marked valid. It needs its own DEC entry,
the synthetic-then-real method DEC-016 used, and a decision about what happens to
the section 20.2 comparison when its baseline models start declining. The GUI now
makes that failure visible in one click, which is a good reason to do it next.

# 13. Current project state

**Last updated:** 2026-08-25 (fourth session)

**Implementation status:** M0, M1, M2, M4 done. M3 partial (local ground model done
and tested; CSF/SMRF unbenchmarked). **M5 harness complete, blocked on field data.**
M6 preview only. **M7 done** (approve/reject/override with persistence, staleness
detection and an audit trail, in the GUI). M8 partial. 153 tests passing.

**Nothing is calibrated.** 37 provisional parameters, no field reference. Confidence
is a qualitative band and accuracy is unclaimed. The sensitivity results in docs 02
sections 21 to 23 say which parameters matter; they do not say which values are
right, and must never be quoted as accuracy. Docs 02 section 24 *is* error against
known truth, but on synthetic geometry: it says where the ellipse estimator stops
being identifiable, not how the tool performs on a tree. Do not quote it as field
accuracy either.

**Local prerequisites are not in the repository.** `.venv/` and
`Las-Sample/Yaloch Maya.las` are both gitignored, so a fresh checkout has neither.
Rebuild the venv from `pyproject.toml`; ask the user for the point cloud. Both are
present on the current machine as of the third session, and the scan was verified
to match its documented header exactly.

**To operate the tool, read `docs/00_RUNNING_THE_TOOL.md`** — setup from a bare
checkout, every command with expected output, and how to read a result. There is a
Tk review GUI (`dbh gui`, or `dbh.bat` with no arguments); section 10 of that guide
covers it. Its theme is shared with the sibling 360-pointcloud-tool project.

**Current milestone:** M5 — reference validation. Everything is built and tested;
the only missing input is `data/reference_trees.csv` with real measurements.

**Next exact step:** populate `data/reference_trees.csv` (schema and rules in
`data/README.md`), then run `dbh benchmark` and `dbh experiment -e all -r ...`.
Calibrate `ransac_circle.residual_threshold_m` first: it is the most influential
uncalibrated parameter by a factor of six.

**If field data still has not arrived,** the best unblocked job is **gating the
four non-robust circle fits** (known issue 14 — on S10 `circle_algebraic` reports
176.9 cm at an RMSE of 246.7 cm and is marked valid; the DEC-016 shell test would
decline it, but changing those models changes the baseline of the section 20.2
comparison, so it needs its own DEC entry). The GUI now shows that failure in one
click, which is a good reason to do it next.

**If you are picking this up cold:** read `README.md`, run
`./.venv/Scripts/python.exe -m pytest -q` (expect 128 passed), then
`dbh measure "Las-Sample/Yaloch Maya.las" --targets data/targets_sample.json
--roi 4 --outdir out` and look at `out/S01_review.png` and `out/S01_ground.png`.
Read the "Known issues" lists in all five build-log entries before trusting any
number. DEC-016's ellipse gates are validated on the sample scan (docs 02 section
24.5); nothing else is.

---

# 14. Core references

Circle-fit comparison:  
https://www.sciencedirect.com/science/article/pii/S0303243417301617

RANSAC forestry measurement:  
https://www.mdpi.com/2072-4292/6/5/4323

Ellipse stem mapping:  
https://www.mdpi.com/2072-4292/12/3/352

Polygon / ellipse / detailed sectional shape:  
https://www.mdpi.com/2072-4292/13/13/2476

Multi-height diameter/taper approach:  
https://www.mdpi.com/2072-4292/12/17/2672

Tropical TLS DBH:  
https://www.mdpi.com/2071-1050/16/6/2275

PDAL CSF:  
https://pdal.io/en/latest/stages/filters.csf.html

PDAL ground filtering / SMRF:  
https://pdal.org/en/stable/tutorial/ground-filters.html

Open3D DBSCAN:  
https://www.open3d.org/docs/latest/tutorial/geometry/pointcloud.html
