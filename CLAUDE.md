# CLAUDE.md — orientation for a new session

Tree DBH (Diameter at Breast Height) from dense terrestrial/mobile LiDAR. The point
of this tool is **trustworthy measurements with explicit uncertainty**, not a number
for every object. A smaller set of defensible measurements beats a larger set of
silently wrong ones. Keep that ordering when trading off anything.

## Read these first, in order

1. `README.md` — what the tool does; `docs/00_RUNNING_THE_TOOL.md` to operate it
   (setup, every command, the GUI, how to read a result).
2. `docs/03_PIPELINE_AND_BUILD_LOG.md` — **the persistent state file.** Decisions
   log (DEC-001…DEC-016), milestones, open questions, five build-log entries, and
   "Current project state" with the exact next step. Read the **Known issues**
   lists in all five build-log entries before trusting any number.
3. `docs/02_SCIENCE_AND_METHODS.md` — the measurement science. Sections 20–24 are
   measured results and corrections from implementation, not plans. §21–23 are
   *sensitivity*; §24 is synthetic accuracy; neither is field accuracy.
4. `docs/01_PROBLEM_AND_HANDOVER.md` — scope, stack, and the review outcome table.

Update `docs/03` before you finish. It is how the next session starts.

## Environment

- **Windows. Use the venv explicitly:** `./.venv/Scripts/python.exe -m pytest -q`
  (expect **153 passed**, ~65 s). The package is installed editable, so
  `./.venv/Scripts/python.exe -m dbh_tool.cli ...` works, as does `dbh ...`.
- **`.venv/` is gitignored, so a fresh checkout has none.** Rebuild with
  `python -m venv .venv` then `./.venv/Scripts/python.exe -m pip install -e ".[dev]"`.
- Git repository, `main` tracking `origin/main` at
  <https://github.com/LeonardLind/DBH-Tool>. Commit or push only when asked.
- `Las-Sample/Yaloch Maya.las` is a 0.92 GB local sample (35.5 M points, no CRS,
  classification empty, 59×55 m, 30.8 m relief, ~24,500 pts/m²). **Gitignored, and
  it must stay that way** — it is above GitHub's file limit and not ours to
  redistribute. A fresh clone has no point cloud; ask the user for one.
- `out/` is generated output, gitignored. Regenerate freely.
- `data/reference_trees.csv` is tracked but header-only. If it ever holds real
  survey measurements, check with the user before committing them.
- Writing Python via bash heredocs has mangled escapes and apostrophes here. Prefer
  the Write/Edit tools for source files.

## Non-negotiables

These are the rules that make the tool worth anything. Breaking one silently is
worse than leaving work undone.

1. **Never fabricate reference data.** `data/reference_trees.csv` is header-only on
   purpose. No invented rows, no back-filling from tool output. If asked for
   accuracy without a reference table, say it cannot be produced yet.
2. **Sensitivity is not accuracy.** Docs 02 §21–23 say which parameters *move* the
   answer, never which value is *right*. Never quote them as accuracy.
3. **Never silently change a scientific assumption.** Add a `DEC-0NN` entry to the
   decisions log in docs 03 with reason, implementation, and how to revisit.
4. **Do not remove a name from `config.PROVISIONAL_PARAMETERS`** without field
   validation evidence recorded in the decisions log. Everything is uncalibrated
   until then, and every export says so.
5. **Confidence is a qualitative band**, never a percentage. There is no calibration
   set.
6. **Fit every candidate model before judging.** No geometry gets chosen early.
7. **Status must match the model actually recommended**, and a hard failure
   (inadequate coverage, oversized gap, deformity at breast height) must not emit a
   headline `dbh_cm` — see `report_diameter` in `evaluation/confidence.py`.
8. A **tape** field measurement is compared against the **convex-perimeter**
   equivalent diameter, not the area-equivalent one (DEC-009). These are different
   quantities on an irregular stem.

## Current state (2026-08-25, fourth session)

M0–M4 done. M3 partial (local ground model done; PDAL CSF/SMRF unimplemented,
DEC-006). **M5: harness complete, blocked only on field data.** M6 exists as an
unvalidated exploration aid. **M7 done** — approve/reject/override with
persistence, in the Tk GUI (`dbh gui`). M8 partial. 37 provisional parameters.

DEC-016 gave the ellipse real acceptance gates (angular coverage, angular gap,
shell thinness) and they are now **validated on the sample scan**: ellipse
validity 9/10 → 3/10, max deviation from the other models 73.15 cm → 0.15 cm, and
no reported diameter changed. Docs 02 §24 has the synthetic derivation, §22 the
regenerated real-data table, §24.5 the validation.

**The open scientific problem is now the four non-robust circle fits** (known
issue 14). On S10 `circle_algebraic` reports 176.9 cm at an RMSE of **246.7 cm**
and is marked valid; on S07 the circles report ~3.6 m diameters. The DEC-016 shell
test is model-independent and would decline them, but it changes the baseline of
the §20.2 circle comparison, so it needs its own DEC entry.

**Next exact step:** populate `data/reference_trees.csv` (schema and rules in
`data/README.md`), then:

```bash
./.venv/Scripts/python.exe -m dbh_tool.cli benchmark "Las-Sample/Yaloch Maya.las" \
    -r data/reference_trees.csv --outdir out
```

Calibrate `ransac_circle.residual_threshold_m` first — it is ~6× more influential
than slice thickness and it also gates contamination detection.

If no field data has arrived, do **not** invent it. Useful unblocked work instead:
gate the non-robust circle fits (known issue 14, needs a DEC entry); start M7
review persistence (approve/reject/override — needs no point cloud); fix known
issue 18 (S09 gets confidence `HIGH` despite a 1.37 m seed drift); or validate
`stems/candidates.py` against a manually annotated stem list, which needs a person
willing to stand behind the annotations — never generate that list from tool
output.

## Traps found the hard way

- **Full-coverage tests miss the bugs that matter.** A wrong Taubin coefficient set
  recovered a full circle to 1e-16 and was biased +22 cm on a half arc. Test partial
  arcs, sparse bins, and edge parameter values.
- **Non-robust fits succeed on garbage.** Ordinary circle fits and the ellipse
  returned numbers on vegetation clumps where RANSAC declined. Silent success is
  the failure mode to watch for. The ellipse is gated as of DEC-016; the four
  circle fits are not.
- **A wrong shape class is worse than a wrong number.** On a 120° arc of a
  *circular* stem the ungated ellipse reported axis ratio 1.45, and
  `attribute_ellipticity` read that as genuine ovality (docs 02 §24.1).
- **Angular coverage about a *fitted* centre is not monotone in arc length.** It
  bottoms out near 0.49 at 120–150° and rises again to 0.53 at 90°, because a badly
  constrained fit relocates its own centre. Any coverage threshold below ~0.55 does
  not mean what it looks like it means — including
  `coverage.min_coverage_fraction = 0.60`.
- **A GUI smoke test that asserts on internal state proves nothing.** An install
  that raised half way through still had `self.info` set, so the check passed while
  the header panel stayed empty and the Measure button stayed disabled. Assert on
  what the widgets *show*, and fail on anything the app logs as an error.
- **`gui/theme.py` is a verbatim copy of `pano360/theme.py`** in the sibling
  360-pointcloud-tool project. Change a colour in one, change it in the other.
- **Seeding must be robust.** A least-squares circle on a wide breast-height slice
  in dense forest is meaningless; that produced 2 m diameters. Seed with RANSAC.
- **Height datum ≠ cut plane** (DEC-010). Per-point height-above-ground makes the
  section follow the terrain, so ground slope adds to stem lean.
- `dbh detect` is **unvalidated** — precision/recall never measured. It produced 51
  "accepted" candidates on the sample scan; measurement refused half of the ten
  inspected. Two of its targets point at the same stem: S02 and S09 report 18.869
  and 18.881 cm, and that 0.012 cm agreement is a **duplicate, not precision**.
  Anything that averages the ten sample targets double-counts that tree.

## Command reference

```bash
dbh gui                                   # review window; dbh.bat with no args does this
dbh inspect  cloud.las                    # header, units, CRS, classification, warnings
dbh config                                # full run configuration as YAML
dbh ground   cloud.las -o ground.npz      # ground surface + diagnostics
dbh detect   cloud.las --decimate 10      # stem candidates (UNVALIDATED)
dbh measure  cloud.las --targets data/targets_sample.json --roi 4 --outdir out
dbh benchmark  cloud.las -r data/reference_trees.csv --outdir out
dbh experiment cloud.las --targets data/targets_sweep.json -e all --outdir out
```
