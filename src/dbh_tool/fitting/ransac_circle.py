"""Deterministic RANSAC circle fit.

RANSAC finds the dominant circular structure while ignoring points that do not
support it, which is what we need against branch points, understory vegetation,
lianas and neighbouring stems (docs 02, section 7).

Two properties are deliberately enforced:

* **Determinism.** The trial samples come from a seeded generator, so a given
  (points, config) pair always produces the same answer. Reproducibility is a
  stated product requirement, and non-deterministic tests are untestable tests.
* **Coverage is reported, never assumed.** A clean 90-degree arc can yield a high
  inlier fraction and a poorly constrained radius, so the caller must read
  ``angular_coverage`` alongside ``inlier_fraction``.
"""
from __future__ import annotations

import numpy as np

from .circle import fit_circle_geometric, fit_circle_pratt
from .common import (
    MAX_PLAUSIBLE_DIAMETER_M,
    MIN_PLAUSIBLE_DIAMETER_M,
    FitResult,
    as_xy,
    check_plausible_diameter,
    radial_residuals,
    residual_stats,
)

# Provisional defaults. residual_threshold_m is the one parameter that must be
# matched to sensor noise plus bark roughness; see docs 03 open questions.
DEFAULT_RESIDUAL_THRESHOLD_M = 0.01
DEFAULT_MAX_TRIALS = 500
DEFAULT_MIN_INLIER_FRACTION = 0.3
DEFAULT_SEED = 42
# Points used for scoring. Terrestrial scans can put >100k points in one slice;
# scoring every trial against all of them is wasted work, so a seeded subsample
# is used for the search and the final refit uses every inlier.
DEFAULT_MAX_SCORING_POINTS = 20000
_TRIAL_CHUNK = 64


def _circumcircles(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray):
    """Vectorised circumcircle of point triples. Returns (cx, cy, r) with NaN
    where the triple is collinear."""
    ax, ay = p1[:, 0], p1[:, 1]
    bx, by = p2[:, 0], p2[:, 1]
    cx_, cy_ = p3[:, 0], p3[:, 1]
    d = 2.0 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    sa, sb, sc = ax ** 2 + ay ** 2, bx ** 2 + by ** 2, cx_ ** 2 + cy_ ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        ux = (sa * (by - cy_) + sb * (cy_ - ay) + sc * (ay - by)) / d
        uy = (sa * (cx_ - bx) + sb * (ax - cx_) + sc * (bx - ax)) / d
    bad = ~np.isfinite(ux) | ~np.isfinite(uy) | (np.abs(d) < 1e-15)
    ux = np.where(bad, np.nan, ux)
    uy = np.where(bad, np.nan, uy)
    r = np.hypot(ax - ux, ay - uy)
    return ux, uy, r


def fit_circle_ransac(points,
                      residual_threshold_m: float = DEFAULT_RESIDUAL_THRESHOLD_M,
                      max_trials: int = DEFAULT_MAX_TRIALS,
                      min_inlier_fraction: float = DEFAULT_MIN_INLIER_FRACTION,
                      seed: int = DEFAULT_SEED,
                      max_scoring_points: int = DEFAULT_MAX_SCORING_POINTS,
                      refit: str = "geometric") -> FitResult:
    """Fit a circle robustly and report the inlier set.

    ``refit`` selects how the final circle is computed from the inliers:
    ``"geometric"`` (default, orthogonal-distance), ``"pratt"``, or ``"none"``
    to keep the raw best-trial circumcircle.
    """
    xy = as_xy(points)
    n = len(xy)
    fit = FitResult(
        model="circle_ransac",
        diameter_definition="2 * radius of circle refitted on RANSAC inliers",
        point_count=n,
        extra={
            "residual_threshold_m": float(residual_threshold_m),
            "max_trials": int(max_trials),
            "seed": int(seed),
            "refit": refit,
        },
    )
    if n < 3:
        return fit.invalidate("too_few_points")
    if residual_threshold_m <= 0:
        raise ValueError("residual_threshold_m must be positive")

    rng = np.random.default_rng(seed)
    # Seeded subsample for scoring keeps large slices affordable and deterministic.
    if n > max_scoring_points:
        score_idx = rng.choice(n, size=max_scoring_points, replace=False)
        score_xy = xy[score_idx]
        fit.extra["scoring_subsample"] = int(max_scoring_points)
    else:
        score_xy = xy

    best = {"count": -1, "cx": np.nan, "cy": np.nan, "r": np.nan, "resid": np.inf}
    trials_done = 0
    while trials_done < max_trials:
        k = min(_TRIAL_CHUNK, max_trials - trials_done)
        trials_done += k
        idx = rng.integers(0, n, size=(k, 3))
        cx, cy, r = _circumcircles(xy[idx[:, 0]], xy[idx[:, 1]], xy[idx[:, 2]])
        ok = (np.isfinite(r) & (r > MIN_PLAUSIBLE_DIAMETER_M / 2.0)
              & (r < MAX_PLAUSIBLE_DIAMETER_M / 2.0))
        if not ok.any():
            continue
        cx, cy, r = cx[ok], cy[ok], r[ok]
        # (k_ok, n_score) residual magnitudes for this chunk of trials.
        d = np.abs(np.hypot(score_xy[None, :, 0] - cx[:, None],
                            score_xy[None, :, 1] - cy[:, None]) - r[:, None])
        inl = d <= residual_threshold_m
        counts = inl.sum(axis=1)
        j = int(np.argmax(counts))
        if counts[j] > best["count"]:
            best = {"count": int(counts[j]), "cx": float(cx[j]), "cy": float(cy[j]),
                    "r": float(r[j]),
                    "resid": float(np.mean(d[j][inl[j]])) if counts[j] else np.inf}

    fit.extra["trials_run"] = trials_done
    if best["count"] <= 0:
        return fit.invalidate("no_consensus_circle_found")

    # Re-evaluate the best hypothesis against the full point set, then refit.
    res_all = radial_residuals(xy, best["cx"], best["cy"], best["r"])
    inliers = np.abs(res_all) <= residual_threshold_m
    n_in = int(inliers.sum())
    if n_in < 3:
        return fit.invalidate("too_few_inliers_for_refit")

    if refit == "geometric":
        final = fit_circle_geometric(xy[inliers])
    elif refit == "pratt":
        final = fit_circle_pratt(xy[inliers])
    elif refit == "none":
        final = None
    else:
        raise ValueError(f"unknown refit mode {refit!r}")

    if final is not None and final.center_xy is not None:
        cxf, cyf = final.center_xy
        rf = final.extra["radius_m"]
        # One consistency pass: recount inliers about the refitted circle.
        inliers = np.abs(radial_residuals(xy, cxf, cyf, rf)) <= residual_threshold_m
        n_in = int(inliers.sum())
    else:
        cxf, cyf, rf = best["cx"], best["cy"], best["r"]

    fit.center_xy = (float(cxf), float(cyf))
    fit.diameter_m = float(2.0 * rf)
    fit.inlier_count = n_in
    fit.inlier_fraction = float(n_in / n)
    fit.extra.update({
        "radius_m": float(rf),
        "best_trial_inliers": best["count"],
        "outlier_count": int(n - n_in),
    })
    # Residual statistics are reported on inliers: including rejected outliers
    # would describe the contamination, not the quality of the stem fit.
    stats = residual_stats(radial_residuals(xy[inliers], cxf, cyf, rf))
    for k_, v_ in stats.items():
        setattr(fit, k_, v_)
    fit.extra["rmse_all_points_m"] = residual_stats(
        radial_residuals(xy, cxf, cyf, rf))["rmse_m"]
    fit.valid = True
    fit = check_plausible_diameter(fit)
    if fit.inlier_fraction < min_inlier_fraction:
        fit.invalidate(f"inlier_fraction_below_{min_inlier_fraction}")
    fit.extra["inlier_mask"] = inliers
    return fit
