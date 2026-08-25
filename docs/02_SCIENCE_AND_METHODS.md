# DBH Point-Cloud Tool — Scientific Basis and Measurement Methods

## 1. What DBH represents

Diameter at Breast Height (DBH) is a standardized stem-size measurement used in forest inventory and as an input to basal-area, volume, biomass and carbon calculations.

For this project, the nominal target is:

**stem diameter at 1.30 m above local ground**

The software must make the height convention configurable because field protocols can differ, especially for:
- sloping ground;
- leaning stems;
- forks;
- deformities;
- buttresses.

The tool must not silently reinterpret a difficult tree. It should store the applied measurement convention.

---

## 2. Why point-cloud DBH is not "just fit a circle"

TLS/mobile LiDAR represents visible stem surfaces as discrete points. The data can contain:
- measurement noise;
- registration/SLAM noise;
- occlusion;
- non-uniform angular sampling;
- foliage;
- lianas;
- branches;
- adjacent stems;
- incomplete circumference;
- stem non-circularity.

Published TLS studies commonly derive DBH from horizontal or approximately stem-normal cross-sections and fit geometric models, but the choice of algorithm matters. Comparative work has found differences between circle-fitting algorithms and has shown benefits from robust or multi-height strategies.

Scientific implication:

> model uncertainty and observational coverage must be evaluated alongside fit residuals.

---

# 3. Height normalization

## 3.1 Why local ground is required

A global Z slice is wrong on sloped terrain.

For point `p`:

```text
HAG(p) = Z(p) - Z_ground(X(p), Y(p))
```

where `HAG` = height above ground.

Then a 1.30 m band can be extracted using HAG rather than raw Z.

## 3.2 Candidate ground methods

### A. Cloth Simulation Filter (CSF)

PDAL provides a CSF ground classifier. CSF conceptually inverts the terrain and simulates a cloth surface that settles onto it. It is widely used as a ground/non-ground segmentation method.

Variables such as cloth resolution, threshold, rigidness and slope handling must be tuned/validated rather than hard-coded from defaults.

### B. Simple Morphological Filter (SMRF)

PDAL also provides SMRF as a ground-classification method based on morphological operations. It provides a useful independent competitor to CSF.

### C. Local/TIN baseline

A transparent baseline should be considered:
- identify plausible low points;
- create a local triangulated/interpolated surface;
- reject obvious low outliers;
- estimate local ground at each stem.

The simplest method may outperform a sophisticated generic classifier in a small dense terrestrial scan, so benchmark rather than assume.

## 3.3 Ground-method benchmark

For a representative manually inspected subset:

```text
CSF vs SMRF vs baseline
```

Compare:
- visible ground classification quality;
- estimated ground Z at known trees;
- resulting DBH error;
- stability on slopes and near roots.

The method yielding the best final DBH accuracy may be more important than the method producing the prettiest global ground cloud.

---

# 4. Constructing the stem cross-section

## 4.1 Slice width

An infinitely thin plane contains too few points.

A height band is required:

```text
target = 1.30 m
band = target ± Δh/2
```

Candidate starting values:
- 5 cm;
- 10 cm;
- perhaps adaptive thickness based on density.

These values are hypotheses, not final constants.

Wider bands:
- provide more points;
- may blur taper, lean and shape changes.

Narrower bands:
- better approximate one height;
- may be sparse and incomplete.

Benchmark slice thickness.

## 4.2 Horizontal vs stem-normal cross-section

V1 can start with a horizontal normalized slice.

However, leaning trees create an important geometric issue: a horizontal plane through an inclined circular cylinder produces an ellipse.

Later versions should estimate the local stem axis from a short vertical neighborhood and optionally construct a plane **normal to the stem axis**.

This gives two useful geometries:

- `horizontal_DBH_cross_section`
- `stem_normal_cross_section`

The project must document which one is used in the final forestry measurement.

---

# 5. Candidate model 1 — standard circle

## Purpose

The circle is the simplest conventional representation of a tree stem.

Parameters:

```text
center = (cx, cy)
radius = r
DBH_circle = 2r
```

A least-squares fit minimizes some form of radial residual.

## Why keep it

- easy to understand;
- fast;
- stable with good full-circle coverage;
- directly comparable with much TLS forestry literature;
- strong baseline;
- expected to work well for regular stems.

## Failure modes

- outliers pull the solution;
- partial arcs can produce plausible but incorrect radii;
- oval/fluted stems are oversimplified;
- lianas may increase radius;
- lean can distort a horizontal section.

Therefore, **standard circle must be a baseline, not the universal answer**.

---

# 6. Candidate model 2 — ellipse

## Purpose

An ellipse allows different major and minor axes.

Store at least:

```text
center
major_axis
minor_axis
rotation
axis_ratio = major / minor
```

Possible derived diameter summaries include:

### arithmetic mean diameter

```text
D_mean = (major + minor) / 2
```

### area-equivalent diameter

Ellipse area:

```text
A = πab
```

where `a` and `b` are semi-axes.

Equivalent circular diameter:

```text
D_area = 2 * sqrt(A / π)
       = 2 * sqrt(ab)
```

The project should prefer clearly named metrics rather than calling every ellipse reduction simply "DBH".

## Why we need it

Real stems are not always circular. Ellipse fitting has been used in TLS stem mapping research, and it gives information that a circle destroys:
- major/minor diameter;
- orientation;
- non-circularity.

It also helps diagnose leaning stems if a horizontal section becomes systematically elliptical.

## Failure modes

An ellipse has more degrees of freedom than a circle and can overfit:
- sparse arcs;
- asymmetric noise;
- partial scans.

Therefore ellipse acceptance should require sufficient angular coverage and should be compared with the circle.

**Implemented in DEC-016; see section 24 for the measurements.** Until then only
the axis-ratio half of this requirement existed, and section 22 measured what that
cost. The gates are now angular coverage, largest angular gap, shell thinness
(radial rmse as a fraction of the fitted diameter) and axis ratio, all in
`config.EllipseConfig` and all provisional.

---

# 7. Candidate model 3 — robust / RANSAC circle

## Purpose

RANSAC repeatedly fits a model to small random subsets, then measures how many points support the candidate within an inlier tolerance.

Output should include:

```text
circle parameters
inlier_count
inlier_fraction
residuals on inliers
number of iterations
threshold
```

## Why we need it

Forest cross-sections contain contaminants:
- branch points;
- understory vegetation;
- lianas;
- neighbouring objects;
- isolated scanner noise.

RANSAC can identify a dominant circular structure while ignoring points that do not support it. Published TLS forestry work has specifically used RANSAC for robust stem detection/diameter estimation.

## Important warning

RANSAC does not solve incomplete geometry.

A clean 90-degree arc can generate a high-inlier circle but may still leave radius poorly constrained.

Therefore RANSAC quality must include **angular coverage**, not only inlier ratio.

---

# 8. Candidate model 4 — irregular-outline / equivalent diameter

## Why we need it

Some stems are genuinely not representable by either a circle or ellipse:
- fluted stems;
- buttress influence;
- irregular tropical trees;
- asymmetric growth.

Published point-cloud work has used polygonal and spline-based closed curves because stem sections are not always well approximated by ellipses.

## Preferred concept: area-equivalent diameter

If a defensible closed cross-sectional outline can be reconstructed with area `A`:

```text
D_eq_area = 2 * sqrt(A / π)
```

This returns the diameter of a circle with the **same cross-sectional area**.

For basal area / biomass-related usage, area-equivalent diameter has a more direct geometric interpretation than forcing an irregular contour into a circular radius.

## Perimeter-equivalent diameter

If perimeter `P` is defensibly estimated:

```text
D_eq_perimeter = P / π
```

This is the diameter of a circle with the same perimeter.

However, perimeter is highly sensitive to:
- noise;
- polygon resolution;
- bark roughness;
- concavities;
- incomplete contour reconstruction.

Therefore **area-equivalent diameter should be the primary irregular-shape summary**, while perimeter-equivalent diameter can be stored as a diagnostic/secondary estimate.

## Possible outline algorithms

Candidates to benchmark:
- radial median bins around an estimated center;
- alpha shape;
- concave hull;
- polygon with fixed angular sectors;
- spline around angularly ordered points.

A plain convex hull can overestimate deeply concave/fluted stems, so it should not automatically be treated as truth.

---

# 9. Buttressed trees require special treatment

A major scientific exception:

If buttresses extend through 1.30 m, the conventional DBH location may be invalid under the selected field protocol.

The software should detect/flag likely cases but **must not silently invent a DBH**.

Possible status:

```text
BUTTRESS_OR_DEFORMITY_AT_BREAST_HEIGHT
```

Possible later workflow:
- identify a measurement point above the buttress according to field protocol;
- store that measurement height;
- call it diameter-at-measurement-height or diameter-above-buttress where appropriate;
- only convert/predict a 1.30 m value if a validated taper model explicitly justifies it.

This distinction matters especially in tropical forests.

---

# 10. Model comparison rather than one winner by default

For each tree and each sampled height, produce all eligible models.

Example:

| Model | Diameter | Residual | Coverage | Inlier % | Shape notes |
|---|---:|---:|---:|---:|---|
| Circle LS | 38.1 cm | ... | ... | n/a | baseline |
| Circle RANSAC | 37.8 cm | ... | ... | ... | robust |
| Ellipse area-equivalent | 38.4 cm | ... | ... | ... | axis ratio 1.08 |
| Outline area-equivalent | 38.2 cm | ... | ... | n/a | ... |

Then compute disagreement:

```text
max_pairwise_difference_cm
max_pairwise_difference_percent
```

Small disagreement is evidence of stability.

Large disagreement is evidence that geometry/model choice matters and should reduce confidence.

---

# 11. Proposed selection logic

Do **not** hard-code these numbers until validated. The logic is more important than the first thresholds.

### Example conceptual logic

```text
IF coverage is poor:
    REVIEW_REQUIRED

ELSE IF circle and RANSAC agree
        AND residuals are low
        AND ellipse axis ratio ~ 1:
    prefer robust circle / circle consensus

ELSE IF ellipse strongly improves fit
        AND has adequate coverage
        AND result is stable across nearby heights:
    prefer ellipse-derived area-equivalent diameter
    mark shape = ELLIPTICAL

ELSE IF outline reconstruction is well-supported
        AND circle/ellipse fits are structurally poor
        AND outline is stable across nearby heights:
    use area-equivalent outline diameter
    mark shape = IRREGULAR

ELSE:
    REVIEW_REQUIRED
```

The final implementation should likely use a quality score plus explicit hard-failure rules.

---

# 12. Angular coverage

For a fitted center, calculate point angles:

```text
θi = atan2(yi - cy, xi - cx)
```

Possible metrics:
- occupied angular bins;
- percentage of 360° covered;
- maximum empty angular gap;
- number of separated visible arcs.

This is critical.

Example:
- 90% coverage + moderate noise can be trustworthy.
- 25% coverage + tiny residual can be very untrustworthy.

Coverage should directly affect confidence.

---

# 13. Multi-height consistency

Research has used diameter profiles across multiple heights and then interpolated/fitted a taper curve to obtain DBH. This can reduce sensitivity to one bad cross-section.

Suggested first implementation:

```text
1.20
1.25
1.30
1.35
1.40 m
```

For each candidate model, calculate:
- diameter at each height;
- local median;
- local slope/taper;
- standard deviation;
- abrupt deviations.

A single 1.30 m estimate that differs greatly from 1.25/1.35 m should be flagged.

A later method can fit a local linear or spline taper model and interpolate the diameter at exactly 1.30 m.

---

# 14. Bootstrap / resampling stability

For accepted candidate stem points:

1. resample points with replacement or random subsets;
2. refit the model many times;
3. record diameter distribution.

Output:
- mean/median diameter;
- standard deviation;
- percentile interval.

A model whose diameter changes strongly under small resampling changes is geometrically unstable.

This should be evaluated as a confidence feature, especially for partial arcs.

---

# 15. Model metrics

Every model should emit a common diagnostics object where possible:

```text
model_name
diameter_cm
center_xy
point_count
inlier_count
inlier_fraction
rmse
median_abs_residual
coverage_fraction
largest_angular_gap_deg
bootstrap_std_cm
cross_height_std_cm
status
warnings[]
```

Additional ellipse:
```text
major_cm
minor_cm
axis_ratio
rotation_deg
```

Additional outline:
```text
area_m2
perimeter_m
outline_method
outline_resolution
```

This common schema enables proper benchmark tables.

---

# 16. Confidence

Do not initially claim that a score such as 93% means a calibrated 93% probability of correctness.

Use qualitative bands until calibrated:

```text
HIGH
MEDIUM
LOW
REVIEW_REQUIRED
FAILED
```

A future probabilistic confidence can be trained/calibrated from reference measurements.

---

# 17. Benchmark experiments

## Experiment A — circle algorithms

On the same reference trees compare:
- algebraic least squares;
- geometric least squares;
- Pratt/Taubin if implemented;
- RANSAC circle.

Measure:
- bias;
- MAE;
- RMSE;
- failure rate.

## Experiment B — geometry

Compare:
- circle;
- ellipse area-equivalent diameter;
- outline area-equivalent diameter.

Stratify by:
- regular;
- elliptical;
- irregular/fluted;
- buttressed/deformed.

## Experiment C — slice thickness

Test e.g.:
- 2 cm;
- 5 cm;
- 10 cm;
- 20 cm.

## Experiment D — height strategy

Compare:
- single 1.30 m slice;
- median of nearby estimates;
- local taper fit interpolated to 1.30 m.

## Experiment E — ground method

Compare:
- CSF;
- SMRF;
- simple local/TIN baseline.

The metric of interest is final **field-referenced DBH accuracy**, not just intermediate geometric residual.

---

# 18. Scientific output categories

Every tree should end in one of these broad states:

```text
ACCEPTED_CIRCULAR
ACCEPTED_ELLIPTICAL
ACCEPTED_IRREGULAR
REVIEW_REQUIRED
INVALID_MEASUREMENT_HEIGHT
FAILED_INSUFFICIENT_DATA
```

Do not force all trees into a numeric DBH.

---

# 19. References / reading list

### Circle fitting and comparative algorithms
Accuracy of tree diameter estimation from TLS by circle-fitting methods  
https://www.sciencedirect.com/science/article/pii/S0303243417301617

Influence of scan mode and circle fitting on tree stem detection/diameter  
https://www.sciencedirect.com/science/article/pii/S0924271612002225

### RANSAC
Olofsson, Holmgren & Olsson — Tree Stem and Height Measurements using TLS and RANSAC  
https://www.mdpi.com/2072-4292/6/5/4323

### Ellipses
Improved 3D Stem Mapping Method and Elliptic Hypothesis  
https://www.mdpi.com/2072-4292/12/3/352

### Ellipses / polygons / spline-like sectional modelling
Efficient Calculation Method for Tree Stem Traits from Large-Scale Point Clouds  
https://www.mdpi.com/2072-4292/13/13/2476

### Multi-height / taper strategy
Structural Changes in Boreal Forests Can Be Quantified Using TLS  
https://www.mdpi.com/2072-4292/12/17/2672

### Tropical DBH and limitations of traditional circular fitting
Estimation of DBH in Tropical Forests Based on TLS  
https://www.mdpi.com/2071-1050/16/6/2275

### Ground classification
PDAL CSF  
https://pdal.io/en/latest/stages/filters.csf.html

PDAL ground-filter tutorial / SMRF  
https://pdal.org/en/stable/tutorial/ground-filters.html

### Clustering
Open3D point-cloud documentation / DBSCAN  
https://www.open3d.org/docs/latest/tutorial/geometry/pointcloud.html


---

# 20. Results and corrections from implementation (2026-08-24)

Everything below was measured, not assumed. Numbers marked *synthetic* come from
generators with known ground truth (`dbh_tool.synthetic`); numbers marked
*sample scan* come from `Las-Sample/Yaloch Maya.las`.

## 20.1 The height datum is not the same thing as the cut plane

This is the most important correction to section 3, and it was found by a failing
test rather than by reasoning.

Height above ground is the right way to choose *which height* to measure at. It is
the wrong way to *cut a cross-section*. Selecting points by per-point HAG makes the
slab follow the terrain, so the cut plane is tilted by the local ground slope, and
that tilt adds to any stem lean.

*Synthetic, 15 degree slope, stem leaning 20 degrees downhill:*

| section defined by | observed axis ratio | expected `1/cos(tilt)` |
| --- | --- | --- |
| per-point HAG band (terrain-following) | 1.19 | 1.06 |
| scalar datum at the stem (truly horizontal) | 1.065 | 1.064 |

The terrain-following cut behaved like a cylinder cut at `20 + 15 = 35` degrees,
`1/cos(35) = 1.22`. It also silently breaks the section 12 lean diagnostic, which
assumes `1/cos(tilt)`.

**Applied convention:** one scalar datum per stem, the robust local ground plane
evaluated at the stem centre. The horizontal section is geometrically horizontal at
`datum + 1.30 m`; the stem-normal section is perpendicular to the fitted axis. This
is recorded in `MEASUREMENT_CONVENTION` and exported with every measurement.

Note the remaining convention question, now explicit in the open questions: on a
25 degree slope the ground under a 1 m stem varies by ~0.45 m across its own
footprint, so "ground at the stem" has to name a point. This implementation uses
the plane evaluated at the stem centre; field protocols often specify the uphill
side.

## 20.2 Circle-fitting algorithms: measured partial-arc bias

*Synthetic, 38 cm stem, 4 mm noise, 300 points, 200 trials per cell. Bias and
standard deviation in cm.*

| arc | algebraic (Kasa) | Taubin | Pratt | geometric |
| --- | --- | --- | --- | --- |
| 360 deg | +0.02 / 0.04 | +0.02 / 0.04 | +0.03 / 0.04 | +0.01 / 0.04 |
| 180 deg | -0.05 / 0.11 | +0.02 / 0.11 | +0.04 / 0.11 | +0.01 / 0.11 |
| 90 deg | **-1.68** / 0.45 | -0.00 / 0.49 | +0.01 / 0.49 | -0.01 / 0.49 |
| 60 deg | **-7.45** / 0.74 | -0.08 / 1.11 | -0.07 / 1.11 | -0.08 / 1.10 |
| 45 deg | **-16.30** / 0.99 | +0.22 / 1.90 | +0.23 / 1.90 | +0.23 / 1.90 |

Two conclusions:

1. The gradient-weighted fits cost nothing and remove a bias that reaches 43% of
   the true diameter at a 45 degree arc. Section 5 treated Pratt/Taubin as optional
   refinements; on occluded stems they are the difference between a usable and an
   unusable answer (DEC-011).
2. **Scatter grows 47-fold from full circle to 45 degree arc while residuals stay
   small.** This is the quantitative form of the warning in section 7, and it is why
   angular coverage gates the verdict and why the bootstrap is worth computing.

## 20.3 Lean bias, confirmed

*Synthetic, 40 cm stem, 20 degree lean, full coverage:* the horizontal section
measures 0.4131 m against a stem-normal 0.4001 m. Predicted circle-fit value
`0.5*(D/cos t + D) = 0.4137`. So the first-order model in section 6 holds, and the
best-fit circle to a modest ellipse does sit near the mean of the axes.

*Sample scan:* ordinary stems lean 5 to 10.5 degrees, and at those angles the
predicted effect is small enough that it does **not** cleanly separate from other
error sources. Measured `horizontal - stem_normal`, after the DEC-010 datum fix:

| tree | DBH | lean | predicted bias | measured difference |
| --- | --- | --- | --- | --- |
| S01 | 24.7 cm | 9.8 deg | +0.18 cm | -0.07 cm |
| S02 | 18.9 cm | 9.2 deg | +0.12 cm | +0.19 cm |
| S05 | 33.2 cm | 10.5 deg | +0.28 cm | -0.26 cm |
| S06 | 21.3 cm | 5.5 deg | +0.05 cm | +0.16 cm |
| S09 | 18.9 cm | 9.3 deg | +0.12 cm | +0.17 cm |

The magnitudes are right but the sign is inconsistent, so at 5-10 degrees of lean
this correction is buried in the noise of everything else. Two honest readings
follow: the stem-normal geometry is *not* demonstrated to help on gently leaning
stems, and it should matter on the strongly leaning stems the synthetic test covers
(20 degrees, where the effect is 1.3 cm on a 40 cm stem and 5x the scatter seen
here). Which geometry to report therefore stays an open question for M5 rather than
something this session settled. Note also that most of these trees selected the
robust circle, which is inherently less sensitive to the elongation the lean
produces.

## 20.4 Separating an irregular stem from a contaminated section

Section 8 assumed an irregular outline reflects stem shape. On the sample scan that
assumption fails immediately: an attached liana or vegetation clump produces the
same signature as fluting (large residuals, non-convex outline, radial roughness),
and the outline model traces the contaminant and reports it as the stem. The first
version of this tool recommended a contaminated outline of 27.6 cm for a stem that
RANSAC measured at 25.1 cm, and labelled it `ACCEPTED_IRREGULAR`.

Two discriminators now separate the cases, and the second was needed because the
first was not sufficient on its own.

**Shell thickness.** A stem surface is a thin shell: at a given angle its returns
span only bark roughness plus sensor noise. That stays true for a fluted or
buttressed stem, which is still a surface. Vegetation is volumetric and spans
centimetres. Measured as the per-sector radial interquartile range.

**One-sided radial excess.** Flutes and fissures deviate in *both* directions about
the dominant surface RANSAC locks onto. Anomalies that are almost entirely outward,
and further out than bark roughness explains, indicate attached material.

*Sample scan, target-height sections:*

| tree | median sector IQR | thick sectors | outliers outside | median excess | verdict |
| --- | --- | --- | --- | --- | --- |
| S01 | 6.8 mm | 17% | 95% | **+2.5 cm** | contamination |
| S02 | 6.8 mm | 6% | 35% | -1.2 cm | irregular shape |
| S03 | 14.4 mm | 46% | 43% | -1.9 cm | contamination |

S01 is the case the thickness test alone missed: thin nearly everywhere, but
carrying a diffuse population 2.5 cm outside the stem over roughly 55 degrees of
arc. This remains a *diagnostic*, not a resolution of the open question; all five
thresholds are provisional.

A consequence worth recording: **fluting is invisible to an ellipse.** A symmetric
6-lobed stem has a near-circular best-fit ellipse, so the ellipse axis ratio must
never gate whether the outline model is considered.

## 20.5 Ground on steep forested terrain

*Sample scan:* the plot is an archaeological mound under closed canopy. A 0.5 m
lowest-point grid over the full 35 M points builds in 1.2 s, populates 36.9% of
cells, and rejects 1,721 cells as sub-surface low outliers. Per-stem ground quality
came out GOOD for 7 of 10 targets, FAIR for 1 (local slope 25.6 degrees, roughness
17.4 cm) and POOR for 1.

The despiking step is not optional. A plain per-cell minimum adopts any
sub-surface noise return as ground, and a 3 m spike is enough to move a
neighbouring stem measurement off the stem entirely. There is a test for exactly
this.

CSF and SMRF remain unbenchmarked (DEC-006). The comparison that matters is
experiment E, and it must be scored on final field-referenced DBH error, not on
which method produces the prettiest ground cloud.

## 20.6 Robust seeding is a prerequisite, not a refinement

A breast-height slice taken over a metre-scale radius in dense forest is mostly not
the stem. Seeding the stem centre with an ordinary circle fit produced diameters of
2 m with 6-29 cm RMSE on the sample scan; the section radius never tightened onto
the stem because the seed was meaningless. Seeding with RANSAC and then tightening
the section radius to `1.5 x` the seeded radius brought the same trees to 18-33 cm
with 5-6 mm RMSE and 97-100% angular coverage.

The seed also has to report when it moves: on the sample scan two requested
locations 1.4 m apart converged onto the same stem, which the drift warning caught.
Without it the export would have contained two independent-looking measurements of
one tree.

## 20.7 Outline area: use the polar integral

For a star-shaped outline the exact area is `0.5 * integral(r^2 dtheta)`. Summing
sector contributions gives this exactly, while the shoelace area of a polygon
inscribed at sector centres is systematically small (0.11% of area, 0.06% of
diameter at 72 sectors). Both are reported; the polar integral is primary.


---

# 21. Parameter sensitivity, measured (2026-08-25)

Field-referenced accuracy is still unavailable, so these are **internal
sensitivity** results: the same trees re-measured under different settings. They
cannot say which value is correct. What they *can* say is which parameters change
the answer enough to be worth calibrating, and that turns the provisional list
(33 entries when this was run, 37 after DEC-016 added the ellipse gates) into a
short priority order.

Method: 6 targets on the sample scan (4 distinct measurable stems plus 2
hard-failure cases so that changes in the refusal rate are visible), each
re-measured across the values below. Sensitivity is the per-tree range of reported
DBH across the sweep, median over trees.

| parameter | range swept | median per-tree range | as % of D | max range | refusal rate moved? |
| --- | --- | --- | --- | --- | --- |
| `ransac_circle.residual_threshold_m` | 0.005 - 0.040 | **2.27 cm** | **7.84%** | 85.4 cm | yes, 4 -> 3 reported at 0.02 |
| `ground.cell_m` | 0.25 - 1.00 | 0.95 cm | 2.88% | 2.25 cm | yes, 4 -> 3 reported at 1.0 m |
| `slice.thickness_m` | 0.02 - 0.20 | 0.32 cm | 1.38% | 2.05 cm | no |
| `ground.despike_tolerance_m` | 0.15 - 0.75 | 0.15 cm | 0.72% | 0.63 cm | no |
| `outline.n_sectors` | 36 - 144 | 0.00 cm | 0.00% | 0.13 cm | no |
| `coverage.angular_bin_deg` | 2 - 10 | 0.00 cm | 0.00% | 0.00 cm | no |

## 21.1 What this changes

**The RANSAC inlier threshold is the parameter that matters.** It is roughly six
times more influential than slice thickness and it also gates the contamination
diagnostic, so it moves both the number and the verdict. At 0.040 m the median
diameter jumps from 23.0 to 30.9 cm because the tolerance is loose enough to adopt
attached vegetation as inliers, which is the failure this project exists to avoid.
Calibrating it needs reference trees plus a sensor-noise figure for the BLK2GO;
it should be the first entry removed from `PROVISIONAL_PARAMETERS`.

**Slice thickness is close to irrelevant here, which contradicts the expectation
in section 4.1.** That section reasoned that thinner bands would be point-starved.
At ~24,500 points/m2 they are not: a 2 cm band still has ample points, and going
from 2 cm to 20 cm moves the answer by a third of a centimetre. Experiment C is
therefore *low* priority on data of this density, though the conclusion should not
be carried over to sparser scans, where the original reasoning may hold.

**Coverage bin size and outline sector count are inert.** Both changed the reported
diameter by 0.00 cm. Note the metric only sees diameter, and both parameters mainly
affect *gating*, so a zero here means "did not flip any gate on these six trees",
not "cannot matter". Still, neither deserves calibration effort.

**Ground grid cell size matters more than the despiking tolerance.** Coarsening to
1.0 m lost a tree and shifted the median by ~4 cm, while the despike tolerance was
almost inert across a 5x range. The despiking *step* is essential (section 20.5),
but its exact threshold is not delicate.

## 22. Model agreement across trees, measured

Docs 02 experiment A and B, on the same 10 targets from the sample scan.
Deviation is from the per-tree median of all valid models. **Regenerated
2026-08-25 after DEC-016**, with the pre-DEC-016 run reproduced alongside it so
the effect of the gates is visible rather than inferred.

| model | valid before | median dev before | max abs before | valid after | median dev after | max abs after |
| --- | --- | --- | --- | --- | --- | --- |
| `circle_ransac` | 5 of 10 | -0.17 cm | **1.98 cm** | 5 of 10 | -0.17 cm | **1.98 cm** |
| `outline_radial_median` | 5 of 10 | +0.15 cm | 0.57 cm | 5 of 10 | +0.15 cm | 0.68 cm |
| `circle_algebraic` | 9 of 10 | -0.06 cm | 5.36 cm | 9 of 10 | -0.35 cm | 10.72 cm |
| `circle_taubin` | 9 of 10 | +0.70 cm | 5.36 cm | 9 of 10 | +0.08 cm | 0.86 cm |
| `circle_geometric` | 9 of 10 | -0.16 cm | 7.47 cm | 9 of 10 | -0.18 cm | 7.82 cm |
| `circle_pratt` | 9 of 10 | +1.67 cm | 11.22 cm | 9 of 10 | +0.88 cm | 10.87 cm |
| `ellipse` | 9 of 10 | +0.00 cm | **73.15 cm** | **3 of 10** | +0.04 cm | **0.15 cm** |
| `outline_radial_median_inliers` | 3 of 10 | +0.07 cm | 183.58 cm | 3 of 10 | +0.07 cm | 188.94 cm |

The important column is still "valid": **the non-robust models return a number on
data where the robust model declines to.** RANSAC and the outline are valid on 5
targets; the ordinary circle fits are "valid" on 9, including vegetation clumps.
That is not the ordinary fits performing better, it is them failing silently, and
it remains the strongest single argument for keeping a robust model in the
comparison rather than treating least squares as the default.

### 22.1 What DEC-016 actually did to this table

**The gates selected the well-behaved subset, they did not merely thin the set.**
Of the 6 targets whose ellipse is now declined, deviation from the median of the
other models was 1.6 cm median and 78.9 cm max. Of the 3 still accepted, 0.1 cm
median and 0.2 cm max. The ellipse row's max deviation fell from 73.15 cm to
0.15 cm — a factor of ~490 — because the fits that were wrong are the fits that
were dropped.

Which gate fired, per target:

| target | coverage | gap | rmse / D | ellipse D | median of others | declined by |
| --- | --- | --- | --- | --- | --- | --- |
| S01 | 0.97 | 10 deg | 0.066 | 27.1 cm | 26.6 cm | shell |
| S02 | 1.00 | 0 deg | 0.045 | 18.8 cm | 18.7 cm | *kept* |
| S03 | 0.83 | 35 deg | 0.131 | 105.9 cm | 106.5 cm | shell |
| S04 | 0.54 | 60 deg | 0.156 | 129.1 cm | 131.8 cm | coverage |
| S05 | 0.97 | 10 deg | 0.025 | 35.3 cm | 35.1 cm | *kept* |
| S06 | 0.97 | 10 deg | 0.120 | 20.3 cm | 20.7 cm | shell |
| S07 | 0.67 | 85 deg | 0.253 | 100.9 cm | 362.2 cm | coverage (axis ratio 3.69 already declined it) |
| S08 | 0.26 | 155 deg | 0.080 | 116.5 cm | 195.4 cm | coverage |
| S09 | 1.00 | 0 deg | 0.044 | 18.8 cm | 18.7 cm | *kept* |
| S10 | 0.81 | 25 deg | 0.137 | 173.2 cm | 177.3 cm | shell |

**Both gates earn their place, and neither would have sufficed alone.** The shell
gate caught 4 targets (S01, S03, S06, S10) that had 0.81-0.97 angular coverage and
gaps of 10-35 degrees — no coverage threshold would have touched them. The
coverage gate caught 3 (S04, S07, S08) including S08 at 0.26 coverage with a
155 degree gap. The gap gate never fired alone on real data: coverage always
caught those cases first, so on this evidence it is redundant here, though it
remains the cheapest guard against the two-clean-arcs geometry that the synthetic
panel in section 24.3 exercises and this scan happens not to contain.

**No reported diameter changed on any of the 10 targets.** Status, confidence band
and selected model are identical before and after. Everything DEC-016 changed on
this scan is in shape attribution and in the model comparison — which is what it
was for, but it also means the gates cost nothing measurable here.

**Shape attribution changed on 6 of 10 targets, in every case from a confident
claim to an honest refusal:** S01, S06 and S08 moved from `OVAL_BEYOND_LEAN`, S03
and S10 from `GENUINELY_OVAL`, and S04 from `CIRCULAR`, all to `NO_ELLIPSE_FIT`.
Six of ten targets were carrying a shape class derived from a fit that could not
support one. Note S04 in particular: the pre-DEC-016 verdict was `CIRCULAR`, which
sounds harmless, but it came from a 0.54-coverage fit whose diameter was 2.7 cm off
the other models. A wrong "circular" is as unfounded as a wrong "oval".

### 22.2 A prediction that was wrong, and the correction

When DEC-016 was written this section was annotated with the claim that excluding
the ellipse would **narrow** every other row's deviation, because the model being
dropped was the one widening the spread. **That was wrong, and the regenerated
table above shows it.** `circle_algebraic` went from 5.36 to 10.72 cm and
`circle_geometric` from 7.47 to 7.82 cm — both *wider*; `circle_taubin` went from
5.36 to 0.86 cm and `circle_pratt` from 11.22 to 10.87 cm — narrower.

The reason is that every deviation is measured against the per-tree *median*, and
removing a model moves the median itself. So the effect of a validity change on
the other rows is not directional and cannot be reasoned out — it has to be
re-run. That is the general lesson, and it applies to any future gate: **a change
in which models are valid invalidates every agreement number in this section, in
an unpredictable direction.**

### 22.3 Still outstanding

`circle_pratt` sits ~0.9 cm above the per-tree median (was 1.67 cm). On clean
synthetic data Pratt is unbiased (section 20.2), so this is a property of
contaminated real sections rather than of the estimator, and it is worth
re-checking once reference trees exist.

**The four non-robust circle fits are still ungated, and the raw output shows this
is worse than a deviation statistic conveys.** On S10 `circle_algebraic` reports a
176.9 cm diameter with an RMSE of **246.7 cm** and is marked valid; on S07 the
circle fits report ~3.6 m diameters, inside the 4.0 m plausibility envelope. A
fit whose residual scatter exceeds its own diameter is not a measurement, and
nothing currently declines it. The shell-thinness ratio that DEC-016 applies to
the ellipse is model-independent — S10's circles sit at rmse/D of roughly 1.4
against the ellipse gate of 0.05 — so it would transfer directly. It is not
applied here because doing so changes the baseline that every published
circle-fit comparison rests on, and that deserves its own decision and its own
measurement rather than being carried in on the back of DEC-016.

## 23. Height strategy, measured

Docs 02 experiment D, 7 trees, deviation from the per-tree median of the three
strategies:

| strategy | mean deviation | std |
| --- | --- | --- |
| `single_slice` | +5.70 cm | 15.08 cm |
| `profile_median` | +0.00 cm | 0.03 cm |
| `taper_interpolated` | -2.86 cm | 7.25 cm |

Read this carefully, because the metric is partly self-fulfilling: a strategy that
tends to fall in the middle of three will show a small deviation from their median
by construction. The defensible conclusion is narrower than the table suggests:
**on messy trees the three strategies diverge by many centimetres, and the single
1.30 m slice is consistently the most extreme of the three.** That is consistent
with the section 13 rationale for computing a profile at all. Which strategy is
*most accurate* is unanswerable without field reference, and
`decision.primary_dbh_source` is the switch that will answer it in one run when the
reference table exists.

## 24. Ellipse acceptance gates, measured (2026-08-25)

Section 6 has said since the handover that "ellipse acceptance should require
sufficient angular coverage and should be compared with the circle". Only the
axis-ratio half of that was ever implemented, and section 22 measured the cost:
the ellipse was "valid" on 9 of 10 real targets and deviated up to 73 cm from the
per-tree median. This section is the measurement behind the gates added in
DEC-016.

**No field data is involved and none is implied.** These are numerical properties
of the estimator on synthetic geometry with known truth, in the same sense as the
partial-arc circle bias in section 20.2. They say where the ellipse stops being
identifiable, not how accurate it is on a tree.

Method: 40 seeds per row, 600 points spread over the arc, 4 mm Gaussian noise.
Error is in the area-equivalent diameter against the generating truth.

### 24.1 The arc-length cliff, and why a low coverage gate is unsafe

A truly circular stem, D = 0.40 m, fitted with an ellipse:

| observed arc | coverage about fitted centre | largest gap | median abs error | fitted axis ratio |
| --- | --- | --- | --- | --- |
| 360 deg | 1.00 | 0 deg | 0.02 cm | 1.00 |
| 300 deg | 0.86 | 50 deg | 0.03 cm | 1.00 |
| 270 deg | 0.76 | 85 deg | 0.03 cm | 1.00 |
| 240 deg | 0.67 | 118 deg | 0.10 cm | 1.01 |
| 210 deg | 0.60 | 145 deg | 0.29 cm | 1.02 |
| 180 deg | 0.53 | 170 deg | 1.22 cm | 1.04 |
| 150 deg | 0.49 | 185 deg | 4.30 cm | 1.14 |
| 120 deg | 0.49 | 182 deg | **12.26 cm** | **1.45** |
| 90 deg | 0.53 | 170 deg | **23.18 cm** | **2.27** |
| 60 deg | — | — | declined (no ellipse solution) | — |

Two things matter here.

**The failure is a false shape claim, not just a wrong number.** At 120 degrees a
circular stem is reported as a 1.45-ratio oval, and `attribute_ellipticity` then
reads that as genuine ovality. So the pre-DEC-016 ellipse did not merely add
error, it manufactured a shape class. The same sweep run on the geometric circle
for comparison peaks at 6.8 cm and never invents an axis ratio, because a circle
has no ratio to invent.

**Coverage measured about the fitted centre is not monotone in arc length.** It
falls to 0.49 at 150 and 120 degrees and then rises again to 0.53 at 90 degrees,
because a badly constrained fit relocates its own centre and the points end up
distributed around the wrong point. A coverage threshold set below about 0.55
therefore does not mean what it appears to mean, and would let the worst cases
through. `min_coverage_fraction = 0.70` is set deliberately clear of that fold
rather than at the point where error becomes intolerable. Largest gap *is*
monotone down to 180 degrees, which is why both are gated and not just one.

The cost of that conservatism is visible and small: the 240-degree case is
declined although its error was 0.10 cm. A circle is still available there, and
the project's stated ordering prefers a smaller defensible set.

### 24.2 Shell thinness separates a surface from a volume

A stem surface is a thin shell: at any angle the returns span bark roughness plus
sensor noise. Attached vegetation is volumetric. Radial rmse normalised by the
fitted diameter makes that comparable across stem sizes.

| section | rmse / D (median) | ellipse valid before DEC-016 | RANSAC circle valid |
| --- | --- | --- | --- |
| circle D0.40, 2 mm noise | 0.0049 | yes | 40/40 |
| circle D0.40, 8 mm noise | 0.0197 | yes | 40/40 |
| circle D0.40, 15 mm noise | 0.0367 | yes | 40/40 |
| ellipse 0.46 x 0.38, 4 mm | 0.0096 | yes | 40/40 |
| fluted D0.50, amplitude 0.08 | 0.0288 | yes | 40/40 |
| fluted D0.50, amplitude 0.15 | 0.0531 | yes | 14/40 |
| fluted D0.50, amplitude 0.25 | 0.0876 | yes | 0/40 |
| circle + clump 0.22 m offset | 0.0663 | yes | 40/40 |
| circle + clump 0.30 m offset | 0.0947 | yes | 40/40 |
| pure blob, no stem at all | 0.2382 | **yes** | 0/40 |

`max_normalised_residual = 0.05` admits 1.5 cm of radial scatter on a 30 cm stem,
which is generous against any plausible bark-roughness figure, and declines every
contaminated case. It cannot separate heavy fluting from contamination — flute
amplitude 0.15 (0.053) and a 0.22 m clump (0.066) overlap, and amplitude 0.25
(0.088) sits inside the clump range entirely. **That overlap is not a defect of
the gate, it is the open question of section 20.4 reappearing.** The gate is
therefore a statement about the *model*, "these points are not an ellipse shell",
and never a verdict on *why*. Attribution stays with
`classify_radial_anomaly`, which has the robust circle and the sector structure to
work with.

Note the last row. Before DEC-016 the ellipse returned a valid fit to a
structureless blob containing no stem at all, where RANSAC declined on all 40
seeds. That is the silent-success failure mode in its purest form.

### 24.3 Gate screening

Chosen combination: coverage >= 0.70, largest gap <= 100 deg, rmse/D <= 0.05,
axis ratio <= 3.0 (unchanged). Screened over 40 seeds per case:

| must be kept | kept | | must be declined | kept |
| --- | --- | --- | --- | --- |
| circle full, 2 / 8 / 15 mm noise | 40/40 | | circle 240 / 210 / 180 deg arc | 0/40 |
| circle 330 / 300 / 270 deg arc | 40/40 | | circle 150 / 120 / 90 deg arc | 0/40 |
| ellipse full and 300 deg arc | 40/40 | | two 100 deg arcs, 110 deg gap | 0/40 |
| fluted, amplitude 0.08 | 40/40 | | clump at 0.22 m and 0.30 m | 0/40 |
| | | | fluted, amplitude 0.15 and 0.25 | 0/40 |
| | | | pure blob | 0/40 |

The coverage gate is not a hidden eccentricity gate: a complete outline of an
ellipse surrounds its own centre whatever the axis ratio, so coverage stays at
1.00 up to ratio 12 and only the explicit axis-ratio gate rejects a flat section.
That is asserted in the tests, because a coverage gate that quietly suppressed
oval stems would defeat the purpose of having an ellipse at all.

### 24.4 End-to-end effect on the synthetic plot

The integration scene (15 degree slope, four stems of known diameter) run with the
gates open and then at the new defaults:

| tree | arc | truth | ellipse D before | error | ratio before | now | recommended model before -> after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| upright | 360 | 0.42 | 0.420 | 0.0 cm | 1.00 | valid | `circle_ransac` -> `circle_ransac` |
| leaning 18 deg | 360 | 0.38 | 0.390 | +1.0 cm | 1.06 | valid | `circle_ransac` -> `circle_ransac` |
| one-sided | 110 | 0.34 | 0.196 | **-14.4 cm** | 1.74 | declined | **`ellipse` -> `circle_ransac`** |
| fluted, amp 0.12 | 360 | 0.50 | 0.503 | +0.3 cm | 1.00 | valid | `outline_radial_median` -> same |

The one-sided stem is the whole point. Its ellipse was 14.4 cm too small, claimed
a 1.74 axis ratio on a circular stem, and was the *recommended* model. The tree
was already refused for inadequate coverage, so no headline diameter changed
hands, but the recommendation a reviewer sees moved from a 14 cm-wrong ellipse to
the robust circle, and the shape verdict moved from a false `GENUINELY_OVAL` to an
honest `NO_ELLIPSE_FIT`. Nothing else in the scene changed.

The fluted stem is the case to watch: at amplitude 0.12 its rmse/D is 0.0426,
under the 0.05 gate but not by much. Real tropical fluting will push the ellipse
out of the comparison on some stems. That is the correct model choice — the
outline exists for those — but it means model-agreement statistics computed after
DEC-016 are not comparable with those computed before it. Section 22 has been
regenerated on the sample scan for exactly that reason, and one prediction made
here before the re-run turned out to be wrong; see section 22.2.

### 24.5 Validated on the real sample scan (2026-08-25)

The point cloud was restored locally, so the gates derived above have now been
checked against the data that motivated them. Full per-target evidence is in
section 22.1; the summary:

- **Ellipse validity fell from 9 of 10 targets to 3 of 10**, and the gates
  selected the well-behaved subset rather than merely thinning it. The three
  survivors deviate from the median of the other models by 0.1 cm median and
  0.2 cm max; the six declined by 1.6 cm median and 78.9 cm max. The ellipse row's
  max deviation fell from 73.15 cm to 0.15 cm.
- **Both new gates were necessary and neither sufficed alone.** Shell thinness
  declined 4 targets whose angular coverage was 0.81-0.97 — no coverage threshold
  would have reached them. Coverage declined 3 more, down to 0.26 on S08. The gap
  gate never fired alone on this scan.
- **No reported diameter changed on any target**, and no status or confidence band
  changed. The gates cost nothing measurable here.
- **Shape attribution changed on 6 of 10 targets**, in every case from a confident
  claim to `NO_ELLIPSE_FIT`. Four had been called oval; S04 had been called
  *circular* on a 0.54-coverage fit, which is an equally unfounded claim in the
  opposite direction.

This closes the "unvalidated on real stems" caveat for the ellipse gates
specifically. It is still not field validation: there is no reference table, so
"deviation from the median of the other models" is internal agreement and not
error. It says the declined fits were the disagreeing ones, which is what section
22 measured in the first place. It cannot say the surviving three are *accurate*.
