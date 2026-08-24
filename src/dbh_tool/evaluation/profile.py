"""Multi-height diameter profile and taper interpolation.

The supporting heights around 1.30 m are not five competing definitions of DBH.
They are supporting observations that let a single bad section be detected instead
of silently believed: a slice can be wrong because the ground estimate was off, or
because it happened to cut a branch scar, a liana or a local deformity.

Two products come out of the profile:

* a robust diameter at the target height, obtained by fitting a local linear taper
  and evaluating it at exactly 1.30 m, which uses all the observations instead of
  trusting one slice, and
* an anomaly report, flagging heights whose diameter departs from the local taper
  trend by more than a robust tolerance.
"""
from __future__ import annotations

import numpy as np


def _robust_scale(res: np.ndarray) -> float:
    mad = float(np.median(np.abs(res - np.median(res))))
    return max(1.4826 * mad, 1e-9)


def diameter_profile(heights_m, diameters_m, target_height_m: float = 1.30,
                     anomaly_sigma: float = 3.0,
                     buttress_taper_threshold_per_m: float = 0.10) -> dict:
    """Summarise a set of (height, diameter) observations for one stem.

    ``taper_per_m`` is dD/dh: negative is the normal case (a stem narrows going
    up). A large positive value near breast height suggests a buttress or another
    deformity rather than a stem.
    """
    h = np.asarray([v for v in heights_m], dtype=float)
    d = np.asarray([v if v is not None else np.nan for v in diameters_m], dtype=float)
    ok = np.isfinite(h) & np.isfinite(d)
    out = {
        "n_heights": int(ok.sum()),
        "heights_m": [float(v) for v in h],
        "diameters_m": [None if not np.isfinite(v) else float(v) for v in d],
        "median_m": None,
        "std_m": None,
        "range_m": None,
        "taper_per_m": None,
        "interpolated_at_target_m": None,
        "target_height_m": float(target_height_m),
        "anomalous_heights_m": [],
        "warnings": [],
    }
    if ok.sum() == 0:
        out["warnings"].append("no_valid_heights")
        return out
    hv, dv = h[ok], d[ok]
    out["median_m"] = float(np.median(dv))
    out["range_m"] = float(dv.max() - dv.min())
    if ok.sum() >= 2:
        out["std_m"] = float(np.std(dv, ddof=1))
    if ok.sum() < 3:
        out["warnings"].append(f"only_{int(ok.sum())}_heights_available")
        out["interpolated_at_target_m"] = out["median_m"]
        return out

    # Local linear taper. A straight line is the right model over a 20 cm span:
    # anything more flexible would start absorbing the very anomalies we want to
    # detect.
    A = np.column_stack([hv - target_height_m, np.ones_like(hv)])
    coef, *_ = np.linalg.lstsq(A, dv, rcond=None)
    slope, intercept = float(coef[0]), float(coef[1])
    res = dv - A @ coef
    scale = _robust_scale(res)
    out["taper_per_m"] = slope
    out["interpolated_at_target_m"] = intercept   # the fit evaluated at h = target
    out["taper_fit_residual_std_m"] = float(np.std(res, ddof=1)) if len(res) > 2 else None

    flagged = [float(hh) for hh, rr in zip(hv, res) if abs(rr) > anomaly_sigma * scale]
    out["anomalous_heights_m"] = flagged
    if flagged:
        out["warnings"].append(
            f"{len(flagged)}_height(s)_depart_from_local_taper_trend")
    if abs(slope) > buttress_taper_threshold_per_m:
        out["warnings"].append(
            f"taper_{slope:+.3f}m_per_m_exceeds_deformity_threshold")
        if slope > buttress_taper_threshold_per_m:
            out["warnings"].append("diameter_increases_with_height_possible_buttress")
    return out


def cross_height_agreement(profile: dict, single_height_diameter_m: float | None) -> dict:
    """Compare a single-slice answer with the profile as a whole."""
    out = {"single_vs_profile_difference_m": None, "cross_height_std_m": profile.get("std_m")}
    interp = profile.get("interpolated_at_target_m")
    if single_height_diameter_m is None or interp is None:
        return out
    out["single_vs_profile_difference_m"] = float(abs(single_height_diameter_m - interp))
    return out
