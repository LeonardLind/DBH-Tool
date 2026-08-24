# DBH Point-Cloud Tool

Estimate tree Diameter at Breast Height (DBH) from dense terrestrial or mobile
LiDAR point clouds, with multiple competing geometric models, explicit
uncertainty, and a refusal to emit a number when the data does not support one.

The scientific reasoning behind every choice lives in [docs/](docs/):

| document | contents |
| --- | --- |
| [01_PROBLEM_AND_HANDOVER.md](docs/01_PROBLEM_AND_HANDOVER.md) | problem, scope, stack |
| [02_SCIENCE_AND_METHODS.md](docs/02_SCIENCE_AND_METHODS.md) | measurement science and methods |
| [03_PIPELINE_AND_BUILD_LOG.md](docs/03_PIPELINE_AND_BUILD_LOG.md) | pipeline, decisions log, build log, open questions |

## Status

Milestones M0–M4 are implemented and tested. The **M5 benchmark harness is
complete and blocked only on field data**: drop real measurements into
`data/reference_trees.csv` (schema in [data/README.md](data/README.md)) and
`dbh benchmark` scores them. M6 (automatic detection) exists only as an
unvalidated exploration aid.

**No result is calibrated.** Every threshold is a documented working default,
listed in `PROVISIONAL_PARAMETERS` and stamped into the provenance of every
export. Confidence is a qualitative band, never a percentage.

Parameter sensitivity has been measured (docs 02 §21–23). It says which
parameters *move* the answer, never which value is *right*. The most influential
uncalibrated parameter is `ransac_circle.residual_threshold_m` — about six times
more influential than slice thickness, and it also gates contamination detection,
so it should be calibrated first.

## Install

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"      # Windows
# python -m pip install -e ".[dev]"                        # POSIX
```

PDAL (for the CSF/SMRF ground classifiers) is deliberately not a dependency; see
DEC-006. It needs conda: `conda install -c conda-forge pdal python-pdal`.

## Use

```bash
dbh inspect  cloud.las                       # header, units, CRS, classification, warnings
dbh config                                   # the full run configuration, as YAML
dbh ground   cloud.las -o ground.npz         # build and describe the ground surface
dbh detect   cloud.las --decimate 10         # list stem candidates (M6 preview)
dbh measure  cloud.las --at 0.06 0.55 --tree-id T1 --roi 4 --outdir out
dbh measure  cloud.las --targets targets.json --outdir out

dbh benchmark  cloud.las -r data/reference_trees.csv --outdir out   # M5: score vs field
dbh experiment cloud.las --targets targets.json -e all --outdir out # sweeps + experiments
```

`benchmark` reports bias, MAE, RMSE and relative RMSE **always beside the review
rate**, stratified by status, shape class, size class and coverage class, with
`dev`/`holdout` splits kept separate. It picks the comparator per tree from the
reference row's `measurement_method`, because a tape reading and a
cross-sectional-area-equivalent diameter are different quantities.

`experiment` runs six parameter sweeps plus three experiments that need no sweep
(model agreement, section geometry, height strategy). Without `-r` it reports
**internal sensitivity only** and labels itself as such.

`measure` writes `measurements.csv` (one row per tree), `measurements.json` (every
candidate model at every height in both section geometries, plus the run config and
input hash), `run_config.yaml`, and two review figures per tree.

## What it actually does

```text
LAS/LAZ  ->  validate units and coordinates
         ->  ground surface (despiked lowest-point grid)
         ->  ONE scalar ground datum per stem (robust local plane)
         ->  robust seed of the stem centre and radius (RANSAC)
         ->  local stem axis, hence lean
         ->  sections at 1.20 / 1.25 / 1.30 / 1.35 / 1.40 m,
             both horizontal and stem-normal
         ->  fit EVERY model to EVERY section
         ->  diagnostics: coverage, gaps, bootstrap, taper, contamination
         ->  compare models, attribute shape, then decide
         ->  status + confidence band + recommendation for human review
         ->  CSV / JSON
```

### Candidate models

Six competing interpretations plus one diagnostic, all fitted before anything is
selected:

| model | diameter definition |
| --- | --- |
| `circle_algebraic` | Kasa algebraic fit. Transparent baseline; biased on short arcs |
| `circle_taubin` | gradient-weighted algebraic fit |
| `circle_pratt` | gradient-weighted algebraic fit |
| `circle_geometric` | orthogonal-distance least squares, the reference definition |
| `circle_ransac` | robust circle refitted on inliers |
| `ellipse` | constrained conic; area-equivalent diameter `2*sqrt(ab)` |
| `outline_radial_median` | radial-median polygon; area-equivalent diameter from the polar area |
| `outline_radial_median_inliers` | the same outline on RANSAC inliers (diagnostic, not a competitor) |

Three equivalent diameters come out of the outline model and they are **different
quantities**: area-equivalent (right for basal area), perimeter-equivalent (a
noise-sensitive diagnostic), and **convex-perimeter-equivalent, which is the
correct comparator for a field tape**, because a tape bridges flutes instead of
following them. Validating an area-equivalent diameter against tape DBH compares
two different things.

### Outcomes

`ACCEPTED_CIRCULAR`, `ACCEPTED_ELLIPTICAL`, `ACCEPTED_IRREGULAR`,
`REVIEW_REQUIRED`, `INVALID_MEASUREMENT_HEIGHT`, `FAILED_INSUFFICIENT_DATA`.

A headline `dbh_cm` is written only when the geometry constrains it. A soft flag
still reports a number for a reviewer to check; a hard failure (inadequate angular
coverage, an oversized gap, a deformity at breast height) reports none, while still
exporting every candidate fit so a reviewer can see what the data would have
implied.

## Tests

```bash
./.venv/Scripts/python.exe -m pytest -q
```

117 tests. They assert measured behaviour, not just absence of crashes:

- circle fits recover a noiseless circle to 1e-9 and are translation/scale equivariant
- the Kasa short-arc bias is large and negative, and Pratt/Taubin/geometric remove it
- diameter scatter grows as angular coverage shrinks, while residuals stay small
- RANSAC beats least squares by 3x under 25% outward contamination, and is deterministic
- a horizontal cut through a stem leaning 20 degrees is an ellipse of ratio `1/cos(20)`,
  and the stem-normal cut recovers the true diameter
- an attached cluster is classified as contamination, a fluted stem as shape
- a one-sided stem is flagged rather than answered
- the whole pipeline runs through a generated LAS fixture (no proprietary data committed)
- benchmark bias/MAE/RMSE match hand-computed values, a refused tree raises the
  review rate instead of counting as zero error, and the summary refuses to print
  accuracy when nothing was reported
- a tape reference is scored against the convex perimeter, not the area-equivalent
- single-pass multi-crop returns exactly what per-tree cropping returns

## Layout

```text
src/dbh_tool/
  io/las.py                 chunked LAS/LAZ reading, unit and CRS validation
  ground/dtm.py             despiked lowest-point ground surface
  ground/local_plane.py     robust per-stem ground datum with a quality verdict
  ground/normalize.py       height above ground
  stems/axis.py             local stem axis, hence lean
  stems/slices.py           horizontal and stem-normal cross-sections
  stems/candidates.py       stem detection (M6 preview, unvalidated)
  fitting/                  circle, ellipse, RANSAC, outline, shared FitResult
  evaluation/               coverage, bootstrap, comparison, profile, confidence
  evaluation/benchmark.py   field-reference scoring with mandatory review-rate disclosure
  evaluation/experiments.py parameter sweeps and the docs 02 experiment set
  visualization/            review figures
  export/tables.py          CSV and JSON
  measure.py                orchestration
  config.py                 run configuration and PROVISIONAL_PARAMETERS
  synthetic.py              generators with known ground truth
```
