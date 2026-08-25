"""Parameter sweeps and the docs 02 experiment set (A to E).

Two kinds of evidence come out of here, and they are not equally strong.

**Field-referenced error** is what actually settles a parameter choice. It needs a
reference table and is therefore unavailable until M5 has data.

**Internal sensitivity** is available now: re-measure the same trees under
different settings and report how much the answer moves, and how the review rate
changes. It cannot say which setting is *right*, but it does say which parameters
matter enough to be worth calibrating, and which are almost irrelevant. Reporting
sensitivity as if it were accuracy would be exactly the sort of self-flattery this
project is built to avoid, so the two are kept in separate fields and the summary
labels which one it is showing.

Experiments D (height strategy) and B (section geometry) need no sweep at all: all
three height strategies and both geometries are computed on every run, so they are
read straight out of existing measurements.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from ..config import RunConfig
from ..ground.dtm import build_ground_grid
from ..measure import measure_tree
from .benchmark import compare_to_reference


def set_config_value(cfg: RunConfig, dotted: str, value) -> RunConfig:
    """Return a deep copy of ``cfg`` with one dotted-path field replaced."""
    out = copy.deepcopy(cfg)
    parts = dotted.split(".")
    target = out
    for p in parts[:-1]:
        if not hasattr(target, p):
            raise ValueError(f"unknown config section {p!r} in {dotted!r}")
        target = getattr(target, p)
    if not hasattr(target, parts[-1]):
        raise ValueError(f"unknown config field {dotted!r}")
    setattr(target, parts[-1], value)
    return out


def get_config_value(cfg: RunConfig, dotted: str):
    target = cfg
    for p in dotted.split("."):
        target = getattr(target, p)
    return target


# Sweeps that require re-measuring. Values are provisional test ranges, chosen to
# bracket the current default rather than to be exhaustive.
SWEEPS: dict[str, dict] = {
    "slice_thickness": {
        "param": "slice.thickness_m",
        "values": [0.02, 0.05, 0.10, 0.20],
        "question": "docs 02 experiment C: how thick should the cross-section band be?",
        "rebuild_ground": False,
    },
    "ground_cell": {
        "param": "ground.cell_m",
        "values": [0.25, 0.50, 1.00],
        "question": ("docs 02 experiment E, parameterisation proxy: how coarse can "
                     "the ground grid be? CSF/SMRF are not implemented (DEC-006)"),
        "rebuild_ground": True,
    },
    "ground_despike": {
        "param": "ground.despike_tolerance_m",
        "values": [0.15, 0.35, 0.75],
        "question": "how aggressively should sub-surface low outliers be rejected?",
        "rebuild_ground": True,
    },
    "ransac_threshold": {
        "param": "ransac_circle.residual_threshold_m",
        "values": [0.005, 0.010, 0.020, 0.040],
        "question": ("what inlier tolerance matches sensor noise plus bark "
                     "roughness? Also gates the contamination diagnostic"),
        "rebuild_ground": False,
    },
    "outline_sectors": {
        "param": "outline.n_sectors",
        "values": [36, 72, 144],
        "question": "how finely should the irregular outline be resolved?",
        "rebuild_ground": False,
    },
    "coverage_bin": {
        "param": "coverage.angular_bin_deg",
        "values": [2.0, 5.0, 10.0],
        "question": "coverage is bin-size dependent; how much does that matter?",
        "rebuild_ground": False,
    },
    "ellipse_coverage": {
        "param": "ellipse.min_coverage_fraction",
        "values": [0.55, 0.70, 0.85],
        "question": ("DEC-016: how much of the circumference must an ellipse see "
                     "before its five parameters are identifiable? Derived on "
                     "synthetic arcs (docs 02 section 24); a field reference "
                     "replaces that with a real answer"),
        "rebuild_ground": False,
    },
    "ellipse_shell": {
        "param": "ellipse.max_normalised_residual",
        "values": [0.03, 0.05, 0.08],
        "question": ("DEC-016: how thick may the shell be before the section is "
                     "not an ellipse? Overlaps heavy fluting by construction, so "
                     "read it with the anomaly verdict beside it"),
        "rebuild_ground": False,
    },
}


@dataclass
class SweepResult:
    """One parameter sweep over a fixed set of trees."""

    name: str
    param: str
    question: str
    values: list = field(default_factory=list)
    baseline_value: object = None
    per_variant: list = field(default_factory=list)   # one dict per swept value
    per_tree: dict = field(default_factory=dict)      # tree_id -> [dbh_cm or None]
    sensitivity: dict = field(default_factory=dict)
    reference_available: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "param": self.param,
            "question": self.question,
            "values": self.values,
            "baseline_value": self.baseline_value,
            "reference_available": self.reference_available,
            "per_variant": self.per_variant,
            "per_tree_dbh_cm": self.per_tree,
            "sensitivity": self.sensitivity,
        }

    def summary(self) -> str:
        head = "field-referenced error" if self.reference_available else \
            "INTERNAL SENSITIVITY ONLY (no field reference: this cannot say which " \
            "value is correct)"
        lines = [
            f"Sweep {self.name}: {self.param}",
            f"  {self.question}",
            f"  evidence: {head}",
            "",
        ]
        cols = f"  {'value':>10}{'reported':>10}{'refused':>9}{'median D':>10}{'spread':>9}"
        if self.reference_available:
            cols += f"{'bias':>9}{'MAE':>8}{'RMSE':>8}"
        lines.append(cols)
        for v in self.per_variant:
            row = (f"  {str(v['value']):>10}{v['n_reported']:>10}{v['n_refused']:>9}"
                   f"{_fmt(v['median_dbh_cm']):>10}{_fmt(v['spread_dbh_cm']):>9}")
            if self.reference_available and v.get("overall"):
                o = v["overall"]
                row += (f"{_fmt(o.get('bias_cm')):>9}{_fmt(o.get('mae_cm')):>8}"
                        f"{_fmt(o.get('rmse_cm')):>8}")
            lines.append(row)
        s = self.sensitivity
        if s:
            lines += [
                "",
                f"  per-tree range across values: median {_fmt(s.get('median_range_cm'))} cm, "
                f"max {_fmt(s.get('max_range_cm'))} cm"
                f"  ({_fmt(s.get('median_range_percent'))}% of diameter)",
                f"  trees measurable at every value: {s.get('n_trees_all_values', 0)}"
                f" of {s.get('n_trees', 0)}",
            ]
        return "\n".join(lines)


def _fmt(v, nd: int = 2) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def run_sweep(name: str, tree_points: dict, targets: dict, base_cfg: RunConfig,
              bounds=None, ground_source=None, reference: dict | None = None,
              roi_radius_m: float = 1.0, values=None, param: str | None = None,
              question: str | None = None, rebuild_ground: bool | None = None,
              progress=None) -> SweepResult:
    """Re-measure a fixed set of trees across values of one parameter.

    ``tree_points`` maps tree_id to its pre-cropped (N, 3) points, so the file is
    read once no matter how many variants run. ``targets`` maps tree_id to (x, y).
    ``ground_source`` is an iterable factory (a callable returning chunks) used when
    a variant changes the ground surface and it must be rebuilt.
    """
    spec = SWEEPS.get(name, {})
    param = param or spec.get("param")
    values = list(values if values is not None else spec.get("values", []))
    question = question or spec.get("question", "")
    rebuild = spec.get("rebuild_ground", False) if rebuild_ground is None else rebuild_ground
    if not param or not values:
        raise ValueError(f"sweep {name!r} needs a param and values")

    res = SweepResult(name=name, param=param, question=question, values=values,
                      baseline_value=get_config_value(base_cfg, param),
                      reference_available=bool(reference))

    # A ground surface that does not depend on the swept parameter is built once.
    shared_grid = None
    if not rebuild:
        if ground_source is None:
            raise ValueError("ground_source is required to build the ground surface")
        shared_grid = build_ground_grid(
            ground_source(), cell_m=base_cfg.ground.cell_m,
            despike_radius_cells=base_cfg.ground.despike_radius_cells,
            despike_tolerance_m=base_cfg.ground.despike_tolerance_m,
            smooth_iterations=base_cfg.ground.smooth_iterations, bounds=bounds,
            method=base_cfg.ground.method)

    res.per_tree = {tid: [] for tid in tree_points}
    for value in values:
        cfg = set_config_value(base_cfg, param, value)
        if rebuild:
            grid = build_ground_grid(
                ground_source(), cell_m=cfg.ground.cell_m,
                despike_radius_cells=cfg.ground.despike_radius_cells,
                despike_tolerance_m=cfg.ground.despike_tolerance_m,
                smooth_iterations=cfg.ground.smooth_iterations, bounds=bounds,
                method=cfg.ground.method)
        else:
            grid = shared_grid

        measurements = []
        for tid, xyz in tree_points.items():
            if progress:
                progress(f"{name}={value} {tid}")
            m = measure_tree(xyz, grid, tid, targets[tid], cfg,
                             roi_radius_m=roi_radius_m)
            measurements.append(m)
            res.per_tree[tid].append(None if m.dbh_m is None
                                     else round(m.dbh_m * 100.0, 3))

        reported = [m.dbh_m * 100.0 for m in measurements if m.dbh_m is not None]
        entry = {
            "value": value,
            "n_trees": len(measurements),
            "n_reported": len(reported),
            "n_refused": len(measurements) - len(reported),
            "median_dbh_cm": float(np.median(reported)) if reported else None,
            "spread_dbh_cm": (float(np.max(reported) - np.min(reported))
                              if len(reported) > 1 else None),
            "statuses": _counts(m.status for m in measurements),
            "confidence_bands": _counts(m.confidence_band for m in measurements),
            "median_rmse_cm": _median_selected(measurements, "rmse_m"),
            "median_coverage": _median_selected(measurements, "angular_coverage",
                                                scale=1.0),
        }
        if reference:
            entry["overall"] = compare_to_reference(measurements, reference).overall
        res.per_variant.append(entry)

    res.sensitivity = _sensitivity(res.per_tree)
    return res


def _counts(it) -> dict:
    out: dict[str, int] = {}
    for v in it:
        out[v] = out.get(v, 0) + 1
    return out


def _median_selected(measurements, attr: str, scale: float = 100.0):
    vals = []
    for m in measurements:
        sel = next((f for f in m.candidate_results if f.model == m.selected_model), None)
        if sel is None:
            continue
        v = getattr(sel, attr, None)
        if v is not None and np.isfinite(v):
            vals.append(float(v) * scale)
    return float(np.median(vals)) if vals else None


def _sensitivity(per_tree: dict) -> dict:
    """How much does each tree move across the swept values?"""
    ranges, rel = [], []
    complete = 0
    for tid, vals in per_tree.items():
        good = [v for v in vals if v is not None]
        if len(good) == len(vals) and len(good) > 1:
            complete += 1
        if len(good) > 1:
            rng = float(max(good) - min(good))
            ranges.append(rng)
            med = float(np.median(good))
            if med > 0:
                rel.append(100.0 * rng / med)
    return {
        "n_trees": len(per_tree),
        "n_trees_all_values": complete,
        "median_range_cm": float(np.median(ranges)) if ranges else None,
        "max_range_cm": float(np.max(ranges)) if ranges else None,
        "median_range_percent": float(np.median(rel)) if rel else None,
    }


# ---------------------------------------------------------------------------
# Experiments that need no sweep: everything is already in each measurement.
# ---------------------------------------------------------------------------

def height_strategy_comparison(measurements, reference: dict | None = None) -> dict:
    """Docs 02 experiment D: single slice vs profile median vs taper interpolation.

    All three are computed on every run, so this is a read, not a re-measurement.
    """
    keys = ("single_slice", "profile_median", "taper_interpolated")
    rows, diffs = [], {k: [] for k in keys}
    for m in measurements:
        vals = {
            "single_slice": m.dbh_single_slice_m,
            "profile_median": m.dbh_profile_median_m,
            "taper_interpolated": m.dbh_taper_interpolated_m,
        }
        got = {k: v for k, v in vals.items() if v is not None and np.isfinite(v)}
        if len(got) < 2:
            continue
        med = float(np.median(list(got.values())))
        rows.append({"tree_id": m.tree_id,
                     **{k: (None if v is None else round(v * 100, 3))
                        for k, v in vals.items()},
                     "max_difference_cm": round(
                         100.0 * (max(got.values()) - min(got.values())), 3)})
        for k, v in got.items():
            diffs[k].append(100.0 * (v - med))
    out = {
        "n_trees": len(rows),
        "per_tree": rows,
        "deviation_from_tree_median_cm": {
            k: {"n": len(v), "mean": (float(np.mean(v)) if v else None),
                "std": (float(np.std(v, ddof=1)) if len(v) > 1 else None)}
            for k, v in diffs.items()},
        "evidence": "internal agreement only" if not reference else "field-referenced",
    }
    if reference:
        # Score each strategy against the reference by swapping the headline value.
        import dataclasses
        scored = {}
        for k in keys:
            variants = []
            for m in measurements:
                v = {"single_slice": m.dbh_single_slice_m,
                     "profile_median": m.dbh_profile_median_m,
                     "taper_interpolated": m.dbh_taper_interpolated_m}[k]
                variants.append(dataclasses.replace(m, dbh_m=v))
            scored[k] = compare_to_reference(variants, reference).to_dict()
        out["scored_against_reference"] = scored
    return out


def geometry_comparison(measurements) -> dict:
    """Docs 02 experiment B, partial: horizontal versus stem-normal sections."""
    rows, diffs = [], []
    for m in measurements:
        h, s = m.dbh_single_slice_m, m.dbh_stem_normal_m
        if h is None or s is None:
            continue
        d = 100.0 * (h - s)
        tilt = float(getattr(m.axis, "tilt_deg", float("nan")))
        predicted = (50.0 * h * (1.0 / np.cos(np.radians(tilt)) - 1.0)
                     if np.isfinite(tilt) else None)
        rows.append({
            "tree_id": m.tree_id,
            "horizontal_cm": round(h * 100, 3),
            "stem_normal_cm": round(s * 100, 3),
            "difference_cm": round(d, 3),
            "tilt_deg": round(tilt, 2) if np.isfinite(tilt) else None,
            "predicted_difference_cm": (None if predicted is None
                                        else round(predicted, 3)),
            "selected_model": m.selected_model,
        })
        diffs.append(d)
    return {
        "n_trees": len(rows),
        "per_tree": rows,
        "mean_difference_cm": float(np.mean(diffs)) if diffs else None,
        "median_difference_cm": float(np.median(diffs)) if diffs else None,
        "std_difference_cm": (float(np.std(diffs, ddof=1)) if len(diffs) > 1 else None),
        "sign_consistent": (bool(all(d > 0 for d in diffs) or all(d < 0 for d in diffs))
                            if diffs else None),
        "note": ("the horizontal section is expected to read LARGER by about "
                 "0.5*D*(1/cos(tilt)-1); a sign-inconsistent result means the lean "
                 "effect is buried in other error sources at these tilt angles"),
    }


def model_comparison(measurements) -> dict:
    """Docs 02 experiment A and B: how the candidate models differ, across trees."""
    per_model: dict[str, list] = {}
    pairwise: dict[str, list] = {}
    for m in measurements:
        fits = {f.model: f for f in m.candidate_results if f.valid
                and f.diameter_m is not None and np.isfinite(f.diameter_m)}
        if len(fits) < 2:
            continue
        med = float(np.median([f.diameter_m for f in fits.values()]))
        for name, f in fits.items():
            per_model.setdefault(name, []).append(100.0 * (f.diameter_m - med))
        ref = fits.get("circle_geometric")
        if ref is not None:
            for name, f in fits.items():
                if name == "circle_geometric":
                    continue
                pairwise.setdefault(name, []).append(
                    100.0 * (f.diameter_m - ref.diameter_m))
    def stats(d):
        return {k: {"n": len(v), "mean_cm": float(np.mean(v)),
                    "median_cm": float(np.median(v)),
                    "std_cm": (float(np.std(v, ddof=1)) if len(v) > 1 else None),
                    "max_abs_cm": float(np.max(np.abs(v)))}
                for k, v in sorted(d.items())}
    return {
        "n_trees": sum(1 for _ in measurements),
        "deviation_from_per_tree_median": stats(per_model),
        "difference_from_circle_geometric": stats(pairwise),
        "evidence": ("internal agreement only: without field reference this shows "
                     "which models disagree, not which is right"),
    }
