"""Export: a flat CSV for analysis and a complete JSON for provenance.

The CSV is the human/statistics view and is deliberately opinionated about
columns. The JSON keeps *everything*: every candidate model at every height in
both geometries, every warning, the full run config, and the provisional-parameter
list. A reader of the CSV alone must still be able to tell that a value is
uncalibrated, so the calibration status and review state travel in the CSV too.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ..fitting.common import jsonable

CSV_COLUMNS = [
    "tree_id",
    "x", "y",
    "dbh_cm",
    "status",
    "confidence_band",
    "review_state",
    "selected_model",
    "selection_is_recommendation",
    "dbh_source",
    "primary_geometry",
    "measurement_height_m",
    "ground_z_m",
    "ground_quality",
    "ground_slope_deg",
    "ground_roughness_cm",
    "stem_tilt_deg",
    "stem_azimuth_deg",
    "coverage_fraction",
    "largest_gap_deg",
    "rmse_cm",
    "inlier_fraction",
    "bootstrap_std_cm",
    "cross_height_std_cm",
    "model_disagreement_cm",
    "ellipticity_verdict",
    "axis_ratio",
    "radial_anomaly_verdict",
    "outlier_fraction",
    "outline_cleaning_shift_cm",
    "dbh_single_slice_cm",
    "dbh_profile_median_cm",
    "dbh_taper_interpolated_cm",
    "dbh_stem_normal_cm",
    "horizontal_minus_stem_normal_cm",
    "n_points_section",
    "calibration_status",
    "warnings",
    "reasons",
]


def _cm(v):
    return None if v is None else round(float(v) * 100.0, 3)


def measurement_row(m) -> dict:
    """Flatten one :class:`TreeMeasurement` into CSV columns."""
    sel = next((f for f in m.candidate_results if f.model == m.selected_model), None)
    lg = m.local_ground
    ax = m.axis
    ell = m.ellipticity or {}
    cmp_ = m.comparison or {}
    prof = (m.profiles.get(m.primary_geometry, {}) or {}).get(m.selected_model or "", {})
    return {
        "tree_id": m.tree_id,
        "x": round(float(m.center_xy[0]), 4),
        "y": round(float(m.center_xy[1]), 4),
        "dbh_cm": _cm(m.dbh_m),
        "status": m.status,
        "confidence_band": m.confidence_band,
        "review_state": m.review_state,
        "selected_model": m.selected_model,
        "selection_is_recommendation": m.selection_is_recommendation,
        "dbh_source": m.dbh_source,
        "primary_geometry": m.primary_geometry,
        "measurement_height_m": m.measurement_height_m,
        "ground_z_m": None if lg is None else round(lg.z_m, 4),
        "ground_quality": None if lg is None else lg.quality,
        "ground_slope_deg": None if lg is None else round(lg.slope_deg, 2),
        "ground_roughness_cm": None if lg is None else _cm(lg.roughness_m),
        "stem_tilt_deg": None if ax is None else round(float(ax.tilt_deg), 2),
        "stem_azimuth_deg": None if ax is None else round(float(ax.azimuth_deg), 1),
        "coverage_fraction": None if sel is None else sel.angular_coverage,
        "largest_gap_deg": None if sel is None else sel.largest_gap_deg,
        "rmse_cm": None if sel is None else _cm(sel.rmse_m),
        "inlier_fraction": None if sel is None else sel.inlier_fraction,
        "bootstrap_std_cm": None if sel is None else _cm(sel.bootstrap_std_m),
        "cross_height_std_cm": _cm(prof.get("std_m")),
        "model_disagreement_cm": _cm(cmp_.get("max_pairwise_difference_m")),
        "ellipticity_verdict": ell.get("verdict"),
        "axis_ratio": ell.get("observed_axis_ratio"),
        "radial_anomaly_verdict": (cmp_.get("radial_anomaly") or {}).get("verdict"),
        "outlier_fraction": (cmp_.get("radial_anomaly") or {}).get("outlier_fraction"),
        "outline_cleaning_shift_cm": _cm(cmp_.get("outline_cleaning_shift_m")),
        "dbh_single_slice_cm": _cm(m.dbh_single_slice_m),
        "dbh_profile_median_cm": _cm(m.dbh_profile_median_m),
        "dbh_taper_interpolated_cm": _cm(m.dbh_taper_interpolated_m),
        "dbh_stem_normal_cm": _cm(m.dbh_stem_normal_m),
        "horizontal_minus_stem_normal_cm": _cm(cmp_.get("horizontal_minus_stem_normal_m")),
        "n_points_section": None if sel is None else sel.point_count,
        "calibration_status": m.provenance.get("calibration_status", ""),
        "warnings": "; ".join(m.warnings),
        "reasons": "; ".join(m.reasons),
    }


def write_csv(measurements, path: str | Path) -> Path:
    """Write one row per measurement."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for m in measurements:
            w.writerow(measurement_row(m))
    return p


def write_json(measurements, path: str | Path, run_meta: dict | None = None,
               include_points: bool = False) -> Path:
    """Write the complete record, including every candidate model and the config."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run": jsonable(run_meta or {}),
        "n_trees": len(list(measurements)),
        "measurements": [m.to_dict(include_points=include_points) for m in measurements],
    }
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def print_model_table(m) -> str:
    """A compact per-model comparison table for the terminal."""
    lines = [
        f"Tree {m.tree_id}: status={m.status} confidence={m.confidence_band} "
        f"review={m.review_state}",
        f"  reported DBH: "
        f"{'n/a' if m.dbh_m is None else f'{m.dbh_m * 100:.1f} cm'} "
        f"({m.selected_model}, {m.dbh_source}, {m.primary_geometry})"
        + ("  [RECOMMENDATION - not accepted]" if m.selection_is_recommendation else ""),
        f"  {'model':<24}{'D [cm]':>9}{'RMSE':>9}{'cov':>7}{'gap':>7}"
        f"{'inlier':>8}{'boot':>9}{'valid':>7}",
    ]
    for f in m.candidate_results:
        lines.append(
            f"  {f.model:<24}"
            f"{'-' if f.diameter_m is None else f'{f.diameter_m * 100:8.1f}'}"
            f"{'-' if f.rmse_m is None else f'{f.rmse_m * 1000:8.1f}':>9}"
            f"{'-' if f.angular_coverage is None else f'{f.angular_coverage:6.0%}':>7}"
            f"{'-' if f.largest_gap_deg is None else f'{f.largest_gap_deg:6.0f}':>7}"
            f"{'-' if f.inlier_fraction is None else f'{f.inlier_fraction:7.0%}':>8}"
            f"{'-' if f.bootstrap_std_m is None else f'{f.bootstrap_std_m * 1000:8.1f}':>9}"
            f"{'yes' if f.valid else 'NO':>7}"
            + ("   " + ",".join(f.warnings) if f.warnings else ""))
    anom = (m.comparison or {}).get("radial_anomaly") or {}
    if anom.get("verdict"):
        extra = ""
        if anom.get("outlier_fraction") is not None:
            extra = (f" (outliers {anom['outlier_fraction']:.0%}, "
                     f"{(anom.get('outlier_fraction_outside') or 0):.0%} of them outside, "
                     f"median excess {(anom.get('median_radial_excess_m') or 0) * 100:+.1f} cm, "
                     f"thick sectors {(anom.get('thick_sector_fraction') or 0):.0%})")
        lines.append(f"  radial anomaly: {anom['verdict']}{extra}")
    if m.warnings:
        lines.append("  WARNINGS: " + "; ".join(m.warnings))
    if m.reasons:
        lines.append("  reasons: " + "; ".join(m.reasons))
    return "\n".join(lines)
