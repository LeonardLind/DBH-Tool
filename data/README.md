# data/

## reference_trees.csv

The field-reference table for milestone M5. **Currently header-only: no rows.**
Accuracy cannot be claimed until this file has real measurements in it, and rows
must never be invented or back-filled from tool output.

| column | required | meaning |
| --- | --- | --- |
| `tree_id` | yes | must match the `tree_id` used when measuring |
| `field_dbh_cm` | yes | the field measurement, in centimetres |
| `measurement_height_m` | no | height the field value was taken at, if not 1.30 |
| `measurement_method` | no | `tape`, `girth`, `caliper_crossed`, `caliper_single`, `caliper`, `unknown` |
| `shape_class` | no | e.g. `regular`, `elliptical`, `fluted`, `buttressed`, free text |
| `buttressed` | no | true/false |
| `leaning` | no | true/false |
| `split` | no | `dev` or `holdout`; defaults to `dev` |
| `x`, `y` | no | approximate stem location in the cloud's coordinate frame |
| `notes` | no | anything that would change how the value should be read |

### Why `measurement_method` matters

It selects what the tool compares against, per tree:

- `tape` / `girth` -> the **convex-perimeter-equivalent** diameter. A tape bridges
  flutes rather than following them, so on an irregular stem tape DBH and
  cross-sectional-area-equivalent diameter are genuinely different numbers.
  Comparing against the area-equivalent value would charge that difference to the
  tool and bias it low on the hardest stems. See DEC-009.
- `caliper_crossed` -> the ellipse **mean-axes** diameter, which is the mean of two
  roughly perpendicular readings.
- `caliper_single` / `unknown` -> the reported DBH, and the report says it fell back.

Getting this column wrong silently corrupts the benchmark, which is why
`load_reference_table` rejects any value it does not recognise.

### dev and holdout

Tune thresholds on `dev`. Score `holdout` once, at the end. Repeatedly tuning
against the held-out set is how a benchmark stops meaning anything, and nothing in
the code can prevent it -- only discipline can.

A useful split needs enough trees to stratify by shape and size class. The
benchmark warns below ten reported trees because the statistics are not
interpretable at that size.

## targets_sample.json

Measurement targets for `Las-Sample/Yaloch Maya.las`, taken from the unvalidated
`dbh detect` output. These are **not** reference trees: no field DBH is attached,
and at least one pair points at the same stem (see the `seed_drifted` warning in
the build log). They exist so the pipeline can be exercised on real data.

## Large point clouds

Not committed. `Las-Sample/` holds the working sample locally.
