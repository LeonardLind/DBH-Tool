# Running the tool — a practical walkthrough

For the person at the keyboard. What to type, what you should see, and how to tell
a good result from a bad one. Windows/PowerShell paths throughout; on POSIX swap
`.venv/Scripts/` for `.venv/bin/`.

The science is in `docs/02`, the project state in `docs/03`. This file is only
about operating the thing.

---

## 0. The short version

There is a GUI. It is the fastest way to do the thing this tool is for — looking
at a section before believing a number.

```powershell
setup.bat          # once per machine: makes a venv, installs, runs the tests
dbh                # opens the review window
```

Then: **Import point cloud** → **Load JSON** (`data\targets_sample.json`) →
**Measure all targets** → click a tree in the results list → **Section**.

Everything below documents the command line, which is what you want for batch
work and for anything scripted. Section 10 covers the GUI in detail.

---

## 1. One-time setup

`.venv/` is gitignored, so a fresh checkout has no environment. Build one:

```powershell
cd "C:\Users\leonard.lind\OneDrive - Hexagon\Desktop\Code\DBH-Tool"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

That installs the package **editable**, so both of these work and mean the same
thing:

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli inspect "Las-Sample\Yaloch Maya.las"
.\.venv\Scripts\dbh.exe inspect "Las-Sample\Yaloch Maya.las"
```

The rest of this file uses the `python.exe -m dbh_tool.cli` form because it works
regardless of whether the venv is activated. If you'd rather type `dbh ...`,
activate first with `.\.venv\Scripts\Activate.ps1`.

### Confirm the install

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

**Expect `153 passed`, about 60–85 s.** If you get a different number, stop and
find out why before trusting any measurement — several of those tests exist
specifically to catch silently-wrong geometry.

### Where the point cloud goes

`Las-Sample/` is gitignored and must stay that way: the sample is 0.92 GB, above
GitHub's file limit, and not ours to redistribute. Drop `.las`/`.laz` files there
and they stay local. `out/` is likewise gitignored — regenerate it freely.

---

## 2. Look at the file before measuring it

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli inspect "Las-Sample\Yaloch Maya.las"
```

Prints JSON: point count, extent, scales, CRS, classification, and warnings. On
the sample scan:

```
"point_count": 35488852,
"extent_m": [59.36, 55.27, 30.78],
"crs_wkt": null,
"has_classification": false,
"warnings": [
  "no CRS in file: units cannot be confirmed from metadata, assuming metres",
  "classification field is empty: ground must be derived by this tool"
]
```

**Read the warnings, they are not decoration.** "No CRS" means nothing in the file
confirms the units are metres — if a cloud were actually in feet every diameter
would be wrong by a factor of 3.28 and no fit would complain. "Classification
empty" means the tool derives ground itself rather than trusting a ground flag
that isn't there.

Sanity check: does `extent_m` look like a forest plot in metres? 59 × 55 × 31 m
does. 195 × 181 × 101 would be the same plot in feet.

### See every threshold in force

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli config
```

Dumps the full run configuration as YAML. To change anything, save that to a file,
edit it, and pass it back with `-c`:

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli config > my_run.yaml
# edit my_run.yaml
.\.venv\Scripts\python.exe -m dbh_tool.cli measure "Las-Sample\Yaloch Maya.las" -c my_run.yaml --targets data\targets_sample.json --roi 4 --outdir out
```

**37 of those values are provisional** — working defaults, never validated against
field measurements. `config.PROVISIONAL_PARAMETERS` names them and every export
carries the list, so no downstream reader can mistake one for a calibrated number.

---

## 3. Ground surface (optional, useful when a tree looks wrong)

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli ground "Las-Sample\Yaloch Maya.las" -o out\ground.npz
```

On the sample scan: `120x112 cells at 0.5 m, 36.9% observed, 1721 low-outlier
cells rejected`. "36.9% observed" is normal for dense forest — most cells have no
ground return at all and get interpolated. A tree whose diameter looks wrong is
often a tree whose ground datum is wrong, and this is where you check that.

---

## 4. Measure known stems — the main command

This is the one you'll use. It needs to be told *where* the stems are:

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli measure "Las-Sample\Yaloch Maya.las" `
    --targets data\targets_sample.json --roi 4 --outdir out
```

Or a single stem by coordinate:

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli measure "Las-Sample\Yaloch Maya.las" `
    --at 0.06 0.55 --tree-id S01 --roi 4 --outdir out
```

- `--targets` is a JSON list of `{"tree_id", "x", "y"}`. See
  `data/targets_sample.json`.
- `--roi` is the crop radius in metres around each stem. 4 is a good default for
  this scan; larger costs time and pulls in more clutter.
- `--no-plots` skips the PNGs when you only want the tables.
- All targets are cropped in **one pass** over the file, so cost is one read
  regardless of tree count (~6 s for 10 ROIs here).

### What comes out

In `out/`: `measurements.csv`, `measurements.json`, and per tree
`<id>_review.png` and `<id>_ground.png`.

**Look at the PNGs.** `S01_review.png` overlays every candidate model on the
actual section points; `S01_ground.png` shows the local ground fit. A number you
haven't looked at a section for is a number you don't know anything about.

### Reading the result — the part that matters

Every tree gets a **status**, a **confidence band**, and a **recommended model**,
and these are three different statements:

| status | meaning |
| --- | --- |
| `ACCEPTED_CIRCULAR` | the evidence supports a circular stem |
| `ACCEPTED_ELLIPTICAL` | genuinely oval beyond what lean explains |
| `ACCEPTED_IRREGULAR` | fluted/buttressed; an outline model is being reported |
| `REVIEW_REQUIRED` | a person needs to look at this one |
| `INVALID_MEASUREMENT_HEIGHT` | a deformity runs through breast height |
| `FAILED_INSUFFICIENT_DATA` | not enough points to fit anything |

Confidence is a **qualitative band** — `HIGH`/`MEDIUM`/`LOW` — never a
percentage. There is no calibration set, so a number there would be false
precision.

**`REVIEW_REQUIRED` comes in two flavours and the difference is important:**

- A *soft* flag still reports a `dbh_cm`. Meaning: "here is a number, please check
  it."
- A *hard* failure reports **no** `dbh_cm` at all — inadequate angular coverage,
  an oversized gap, or a deformity at breast height. Meaning: "the data does not
  support a number." Every candidate fit is still exported so you can see what the
  data would have implied, but the headline field is empty on purpose. **Do not
  reach into the candidate fits and pull a number out of a hard failure.** That is
  precisely the silent-wrong-answer this tool exists to prevent.

`selected_model` is a **recommendation**, not a decision:
`selection_is_recommendation` is true whenever `decision.automatic_selection` is
false, which is the default. `review_state` starts at `PENDING` — nothing is ever
auto-accepted. Recording a human's approve/reject/override is what the GUI adds
(section 10); the CLI does not write review decisions.

### What "good" looks like on the sample scan

Roughly half the ten sample targets refuse to produce a headline diameter, and
**that is the tool working**. The targets came from the unvalidated detector; two
of them point at the same stem (S09 carries
`seed_drifted_1.37m_from_requested_location` and is the same tree as S02). A
smaller set of defensible measurements beats a larger set of quietly wrong ones.

---

## 5. Find stems automatically — **unvalidated, do not use for inventory**

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli detect "Las-Sample\Yaloch Maya.las" `
    --decimate 10 -o out\candidates.json
```

Precision and recall have **never been measured**. On the sample scan it produced
51 "accepted" candidates, and measurement then refused half of the ten inspected.
Treat the output as a list of places to look, hand-check it, and never feed it
straight into a report.

---

## 6. Validate against field measurements — the blocked step

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli benchmark "Las-Sample\Yaloch Maya.las" `
    -r data\reference_trees.csv --outdir out
```

`data/reference_trees.csv` is **header-only on purpose** and this command has
nothing to compare against until you fill it in. Schema and rules are in
`data/README.md`. Two things to get right when you do:

- **Set `measurement_method` on every row.** It selects the comparator. A **tape**
  measurement is scored against the **convex-perimeter** equivalent diameter, not
  the area-equivalent one — a tape bridges flutes instead of following them, so
  comparing it to an area-equivalent diameter scores a real geometric difference
  as tool error.
- **Never back-fill it from tool output.** A reference table derived from the
  thing it is meant to test measures nothing. If there is no field data, there is
  no accuracy figure, and the tool will say so rather than invent one.

Once ten or more trees are in the dev split, the parameter sweeps turn from
"which settings move the answer" into "which setting is right":

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli experiment "Las-Sample\Yaloch Maya.las" `
    --targets data\targets_sample.json -e all -r data\reference_trees.csv --outdir out
```

**Calibrate `ransac_circle.residual_threshold_m` first.** It is ~6× more
influential than slice thickness and it also gates contamination detection.

---

## 7. Experiments and sweeps (these work today, without field data)

```powershell
.\.venv\Scripts\python.exe -m dbh_tool.cli experiment "Las-Sample\Yaloch Maya.las" `
    --targets data\targets_sample.json -e models -e geometry -e height_strategy `
    --roi 4 --outdir out
```

`-e` is repeatable; `-e all` runs everything. Three "free" experiments need no
sweep (`models`, `geometry`, `height_strategy`) and eight sweeps re-measure:
`slice_thickness`, `ransac_threshold`, `ground_cell`, `ground_despike`,
`outline_sectors`, `coverage_bin`, `ellipse_coverage`, `ellipse_shell`.

Sweeps re-measure serially and take a few minutes for a handful of trees across
four values. Fine for calibration; too slow for a large reference set.

> **Without `-r`, every sweep result is labelled `INTERNAL SENSITIVITY ONLY`, and
> that label is the whole point.** It says which parameters *move* the answer. It
> never says which value is *right*. Quoting a sensitivity number as an accuracy
> figure is the single easiest way to misrepresent this tool.

---

## 8. If something looks wrong

| symptom | first thing to check |
| --- | --- |
| diameter absurd (metres) | is the seed on the stem? Check `<id>_review.png` |
| all trees refuse | ground datum — run `ground` and look at `<id>_ground.png` |
| `seed_drifted_..._from_requested_location` | your target coordinate is off, or two targets share a stem |
| `axis_estimation_failed_using_vertical` | the stem-normal section is just a horizontal one; lean is uncorrected |
| diameter drifts with settings | expected — see the sweeps; nothing is calibrated |
| tests fail after an edit | run `pytest -q` before anything else and read which test |

A recurring trap worth knowing: **a full-coverage test misses the bugs that
matter.** A wrong circle-fit coefficient set once recovered a complete circle to
1e-16 while being biased +22 cm on a half arc. If you add a fit or change one,
test partial arcs, sparse bins and edge parameter values, not just the easy case.

---

## 9. The rules that make the output worth anything

Short version of the non-negotiables in `CLAUDE.md`:

1. Never fabricate reference data.
2. Sensitivity is not accuracy.
3. Never silently change a scientific assumption — it gets a `DEC-0NN` entry in
   `docs/03`.
4. Confidence is a qualitative band, never a percentage.
5. Every candidate model is fitted before any is judged.
6. Status must match the model actually recommended, and a hard failure must not
   emit a headline `dbh_cm`.
7. A tape field measurement is compared against the convex-perimeter equivalent
   diameter, not the area-equivalent one.

---

## 10. The review GUI

```powershell
dbh                                          # or: dbh gui
dbh gui "Las-Sample\Yaloch Maya.las"         # open a cloud straight away
dbh gui cloud.las --targets data\targets_sample.json --outdir out -c my_run.yaml
```

Or `dbh.bat` with no arguments — double-clicking it opens the window, because
someone who double-clicks wants a window, not a usage message.

The window exists for one loop: **pick a tree, look at its section, decide.**
Everything else in it is scaffolding for that loop.

### Layout

Three columns, the same arrangement as the sibling 360-pointcloud-tool picker:

| column | holds |
| --- | --- |
| left | what you are measuring: the cloud, the targets, the settings |
| centre | what you are looking at: **Plan**, **Section**, **Profile** |
| right | what you decide: measure, results, this tree, review, export |

A collapsible log strip runs along the bottom. It stays shut for progress noise
and **opens itself** on a warning or an error, so nothing important hides behind
a disclosure triangle.

### The three views

**Plan** — top-down log point density, with a marker per target. Double-click to
place a target, single-click to select one. After a run the markers are coloured
by status, so the plan doubles as the overview of a whole plot.

**Section** — the point that the GUI exists for. The cross-section with every
model drawn on it, plus an angular-coverage rose whose *unobserved* bins are
shaded red, because an absent bar and a short bar look alike otherwise.

- **Declined models are drawn dashed and labelled `(DECLINED)`**, not hidden. Your
  job includes judging the tool's refusals, and "the ellipse was declined" is a
  claim you can only check by seeing what it claimed. Toggle them off if they
  clutter.
- Points outside the RANSAC inlier band are ringed in red.
- The curves come from the same shared `model_boundary_xy` the export PNG uses, so
  the window and the report cannot disagree about where a model lies.

**Profile** — diameter against height. A stem whose diameter changes sharply
through breast height is a deformity, not a measurement.

### Reading the right-hand column

The results list never shows a number where there isn't one, and the word says
*who* decided that:

| cell | meaning |
| --- | --- |
| `24.7` | a diameter, in cm |
| `refused` | the **tool** declined to measure this section |
| `rejected` | the tool measured it; a **person** rejected the result |
| `withdrawn` | reported, but the recorded review resolves to no number |

An empty cell would read as a bug and a `0.0` would be a lie. There is **no control
anywhere in the window that turns a refusal into a diameter** — see below.

"This tree" carries the status, the confidence **band** (a coloured word — never a
percentage, because there is no calibration set), the recommended model *labelled
as a recommendation*, the shape verdict, the anomaly verdict, ground quality, lean,
and the worst model disagreement. Underneath, every warning and reason, with a
banner when no diameter was reported.

**Model table** opens every candidate fit with its diameter, residual, coverage,
gap, inlier fraction, bootstrap spread, validity, and — for the declined ones —
exactly which gate rejected it. On the sample scan S08's ellipse row reads
`ellipse_angular_coverage_0.26_below_0.70, ellipse_angular_gap_155deg_above_100deg,
ellipse_normalised_residual_0.080_above_0.050`: three gates, named.

**Full report PNG** writes the four-panel light-themed figure — the one that goes
in a document — plus the ground-check figure, into the output directory.

### Review: approve, reject, override (M7)

Decisions persist to `out/review.json` and are written **on every change**, not on
exit: a GUI that loses an afternoon of review to a crash is worse than no
persistence, because you believe the work is saved.

| action | effect on the reported diameter |
| --- | --- |
| **Approve** a reported tree | the tool's number stands, now attributed to you |
| **Approve** a *refusal* | records that you agree there is **no** number |
| **Reject** | withdraws the number; **requires a note** |
| **Override** | reports the model you chose instead; **requires a note** |
| **Reset** | back to pending, decision forgotten |

Rejecting and overriding are disagreements with the tool's reasoning, so both
require a note — the next person cannot see your screen.

**An override cannot create a diameter.** A refused section still has candidate
fits with numbers in them; overriding to one of those would extract a diameter the
geometry does not support. The attempt is recorded, because your opinion is real
and worth keeping, but it resolves to no number and the stored note says so. This
is non-negotiable 7, and it is enforced in one place —
`gui/review.py::resolved_diameter_cm` — with tests that exist specifically to keep
it that way.

**Stale decisions.** A decision describes one specific measurement. Re-measure with
different settings and any decision whose status, recommended model, or diameter
moved is flagged stale, highlighted in the list, and asks to be re-reviewed. An
approval must never silently carry over to a different number.

### What runs where

Nothing that touches the point cloud runs on the UI thread. Opening a 0.92 GB scan
is one full pass for the plan raster and another for the ground surface; measuring
is tens of seconds. Both run on a worker and report through a queue, so the window
stays responsive and the log tells you what is happening. Expect roughly:

| step | sample scan (35.5 M points) |
| --- | --- |
| import (raster) | ~30–60 s |
| ground surface | ~10 s, once per cloud |
| crop 10 targets | ~6 s, one pass |
| measure, per tree | ~1–3 s |

### If the GUI will not start

`dbh gui` needs tkinter. Every measurement command works without it. On Windows,
reinstall Python with the "tcl/tk and IDLE" option ticked; on Debian/Ubuntu,
`apt install python3-tk`. `setup.bat` warns about this rather than failing.
