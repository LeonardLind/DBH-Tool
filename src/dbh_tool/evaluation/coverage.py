"""Angular coverage diagnostics.

This is the single most important guard in the tool. A model fitted to a short
visible arc can have an excellent RMSE and still report a badly wrong diameter,
so every fit must be accompanied by an honest statement of how much of the
circumference was actually observed (docs 02, section 12).

All coverage quantities are computed about a *fitted centre*, because the angle of
a point is only meaningful relative to an assumed centre.
"""
from __future__ import annotations

import numpy as np

DEFAULT_BIN_DEG = 5.0


def angular_coverage(xy: np.ndarray, center: tuple[float, float],
                     bin_deg: float = DEFAULT_BIN_DEG,
                     min_points_per_bin: int = 1) -> dict:
    """Summarise how much of the 360 degrees around ``center`` is populated.

    ``min_points_per_bin`` lets a bin holding a single stray point be treated as
    unoccupied, which keeps sparse noise from inflating apparent coverage.

    Returns a dict with:
        coverage_fraction        fraction of angular bins occupied (0..1)
        largest_gap_deg          longest unobserved angular run
        n_arcs                   number of separated observed arcs
        occupied_bins / n_bins   raw counts behind the fraction
        bin_deg                  the bin size used (coverage is bin-size dependent)
        points_per_occupied_bin_median
        radial_iqr_m             spread of point radii, a contamination hint
    """
    if bin_deg <= 0 or bin_deg > 180:
        raise ValueError("bin_deg must be in (0, 180]")
    n_bins = int(round(360.0 / bin_deg))
    xy = np.asarray(xy, dtype=float)
    out = {
        "coverage_fraction": 0.0,
        "largest_gap_deg": 360.0,
        "n_arcs": 0,
        "occupied_bins": 0,
        "n_bins": n_bins,
        "bin_deg": float(bin_deg),
        "points_per_occupied_bin_median": 0.0,
        "radial_iqr_m": float("nan"),
    }
    if xy.size == 0:
        return out

    dx, dy = xy[:, 0] - center[0], xy[:, 1] - center[1]
    r = np.hypot(dx, dy)
    ang = np.degrees(np.arctan2(dy, dx)) % 360.0
    idx = np.minimum((ang / bin_deg).astype(int), n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins)
    occ = counts >= min_points_per_bin

    out["occupied_bins"] = int(occ.sum())
    out["coverage_fraction"] = float(occ.mean())
    out["points_per_occupied_bin_median"] = float(np.median(counts[occ])) if occ.any() else 0.0
    if r.size >= 4:
        q1, q3 = np.percentile(r, [25, 75])
        out["radial_iqr_m"] = float(q3 - q1)

    if not occ.any():
        return out
    if occ.all():
        out["largest_gap_deg"] = 0.0
        out["n_arcs"] = 1
        return out

    where = np.flatnonzero(occ)
    # Circular gaps between consecutive occupied bins.
    nxt = np.roll(where, -1)
    empty_runs = (nxt - where - 1) % n_bins
    out["largest_gap_deg"] = float(empty_runs.max() * bin_deg)
    # An arc starts at every occupied bin whose predecessor is empty.
    out["n_arcs"] = int(np.count_nonzero(~occ[(where - 1) % n_bins]))
    return out


def attach_coverage(fit, xy: np.ndarray, bin_deg: float = DEFAULT_BIN_DEG,
                    min_points_per_bin: int = 1):
    """Populate the coverage fields of a :class:`FitResult` in place."""
    if fit.center_xy is None:
        return fit
    cov = angular_coverage(xy, fit.center_xy, bin_deg, min_points_per_bin)
    fit.angular_coverage = cov["coverage_fraction"]
    fit.largest_gap_deg = cov["largest_gap_deg"]
    fit.n_arcs = cov["n_arcs"]
    fit.extra["coverage"] = cov
    return fit
