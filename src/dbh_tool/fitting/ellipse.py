"""Constrained direct ellipse fit.

Uses the numerically stable formulation of Halir and Flusser (1998), which
constrains the conic to be an ellipse, so the fit can never silently return a
hyperbola or parabola (docs 03, section 5.2).

Conic-to-geometric conversion is done by translating to the centre and
eigendecomposing the 2x2 quadratic form. That is more robust than the closed-form
axis expressions and yields the axis directions directly as eigenvectors.

Residuals are *radial* (measured along the ray from the fitted centre), matching
the circle models so that residual statistics are comparable across models. For
an ellipse the radial distance slightly exceeds the true orthogonal distance; the
difference is second order in eccentricity and is documented rather than
corrected in V1.
"""
from __future__ import annotations

import numpy as np

from ..evaluation.coverage import angular_coverage
from .common import (
    MAX_PLAUSIBLE_DIAMETER_M,
    MIN_PLAUSIBLE_DIAMETER_M,
    FitResult,
    area_equivalent_diameter,
    as_xy,
    check_plausible_diameter,
    perimeter_equivalent_diameter,
    residual_stats,
)

MIN_POINTS_ELLIPSE = 6
# An axis ratio above this is not a plausible stem cross-section; it indicates an
# under-constrained fit (short arc) or contamination. Provisional, uncalibrated.
MAX_PLAUSIBLE_AXIS_RATIO = 3.0
# Acceptance gates added by DEC-016, defaults mirroring config.EllipseConfig.
# Docs 02 section 6 required from the outset that "ellipse acceptance should
# require sufficient angular coverage and should be compared with the circle";
# only the axis-ratio half of that was implemented. Measured behaviour behind
# these numbers is in docs 02 section 24.
DEFAULT_MIN_COVERAGE_FRACTION = 0.70
DEFAULT_MAX_GAP_DEG = 100.0
DEFAULT_MAX_NORMALISED_RESIDUAL = 0.05
DEFAULT_COVERAGE_BIN_DEG = 5.0


def _conic_to_geometric(coeffs: np.ndarray):
    """Convert (A, B, C, D, E, F) to centre, semi-axes and major-axis rotation.

    Returns ``None`` if the conic is not a real ellipse.
    """
    A, B, C, D, E, F = (float(v) for v in coeffs)
    M = np.array([[A, B / 2.0], [B / 2.0, C]])
    L = np.array([D, E])
    det = np.linalg.det(M)
    if not np.isfinite(det) or det <= 0:
        return None  # not an ellipse (needs positive-definite or negative-definite M)
    try:
        center = -0.5 * np.linalg.solve(M, L)
    except np.linalg.LinAlgError:
        return None
    # Q(c + u) = u^T M u + Q(c); the centred conic is u^T M u = -Q(c).
    qc = float(center @ M @ center + L @ center + F)
    c0 = -qc
    evals, evecs = np.linalg.eigh(M)
    if np.any(np.abs(evals) < 1e-300):
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        sq = c0 / evals
    if not np.all(np.isfinite(sq)) or np.any(sq <= 0):
        return None
    semi = np.sqrt(sq)
    order = np.argsort(semi)[::-1]  # major first
    semi = semi[order]
    evecs = evecs[:, order]
    major_dir = evecs[:, 0]
    rotation = float(np.arctan2(major_dir[1], major_dir[0])) % np.pi
    return (float(center[0]), float(center[1]), float(semi[0]), float(semi[1]), rotation)


def ellipse_radial_residuals(xy: np.ndarray, cx: float, cy: float, a: float, b: float,
                             theta: float) -> np.ndarray:
    """Distance from each point to the ellipse boundary along its own centre ray."""
    dx, dy = xy[:, 0] - cx, xy[:, 1] - cy
    ct, st = np.cos(theta), np.sin(theta)
    u = dx * ct + dy * st          # coordinate along the major axis
    v = -dx * st + dy * ct         # coordinate along the minor axis
    rp = np.hypot(u, v)
    phi = np.arctan2(v, u)
    denom = np.hypot(b * np.cos(phi), a * np.sin(phi))
    with np.errstate(divide="ignore", invalid="ignore"):
        r_ell = np.where(denom > 0, a * b / denom, np.nan)
    return rp - r_ell


def ellipse_perimeter(a: float, b: float) -> float:
    """Ramanujan second approximation to the ellipse perimeter (relative error < 1e-5)."""
    h = ((a - b) / (a + b)) ** 2
    return float(np.pi * (a + b) * (1.0 + 3.0 * h / (10.0 + np.sqrt(4.0 - 3.0 * h))))


def ellipse_boundary(cx: float, cy: float, a: float, b: float, theta: float,
                     n: int = 361) -> np.ndarray:
    """Polyline of the ellipse boundary, for overlay plotting."""
    t = np.linspace(0.0, 2.0 * np.pi, n)
    u, v = a * np.cos(t), b * np.sin(t)
    ct, st = np.cos(theta), np.sin(theta)
    return np.column_stack([cx + u * ct - v * st, cy + u * st + v * ct])


def fit_ellipse(points, max_axis_ratio: float = MAX_PLAUSIBLE_AXIS_RATIO,
                min_coverage_fraction: float = DEFAULT_MIN_COVERAGE_FRACTION,
                max_gap_deg: float = DEFAULT_MAX_GAP_DEG,
                max_normalised_residual: float = DEFAULT_MAX_NORMALISED_RESIDUAL,
                coverage_bin_deg: float = DEFAULT_COVERAGE_BIN_DEG) -> FitResult:
    """Fit an ellipse and report both axis geometry and area-equivalent diameter.

    ``diameter_m`` is the area-equivalent diameter 2*sqrt(a*b), i.e. the diameter
    of the circle with the same cross-sectional area. The individual axes are in
    ``extra`` and must be used for shape interpretation.

    The fit is always computed and always returned: a rejected ellipse keeps its
    geometry and its reasons so that a reviewer can see what the data would have
    implied (docs 03, "fit every candidate model before judging"). The gates only
    decide ``valid``.

    Acceptance gates, all provisional and all listed in
    :data:`~dbh_tool.config.PROVISIONAL_PARAMETERS`:

    ``max_axis_ratio``
        a cross-section flatter than this is not a stem.
    ``min_coverage_fraction`` / ``max_gap_deg``
        angular support about the fitted centre. Five free parameters need more
        of the circumference than a circle's three, and an ellipse continued
        across a wide unobserved arc is extrapolation.
    ``max_normalised_residual``
        radial rmse as a fraction of the fitted diameter: a stem surface is a
        thin shell, a clump of vegetation is a volume. This says the points are
        not an ellipse shell; it does not say why.
    """
    xy = as_xy(points)
    fit = FitResult(
        model="ellipse",
        diameter_definition="area-equivalent diameter 2*sqrt(a*b) of the fitted ellipse",
        point_count=int(len(xy)),
    )
    if len(xy) < MIN_POINTS_ELLIPSE:
        return fit.invalidate("too_few_points")

    # Centre and scale for conditioning; the conic is fitted in normalised
    # coordinates and the geometry is mapped back afterwards.
    mx, my = xy[:, 0].mean(), xy[:, 1].mean()
    u, v = xy[:, 0] - mx, xy[:, 1] - my
    scale = float(np.sqrt(np.mean(u ** 2 + v ** 2)))
    if not np.isfinite(scale) or scale <= 0:
        return fit.invalidate("degenerate_point_spread")
    u, v = u / scale, v / scale

    D1 = np.column_stack([u * u, u * v, v * v])
    D2 = np.column_stack([u, v, np.ones_like(u)])
    S1, S2, S3 = D1.T @ D1, D1.T @ D2, D2.T @ D2
    try:
        T = -np.linalg.solve(S3, S2.T)
    except np.linalg.LinAlgError:
        return fit.invalidate("ill_conditioned_design_matrix")
    M = S1 + S2 @ T
    # Premultiply by inv(C1) for the ellipse-constrained eigenproblem.
    M = np.array([M[2] / 2.0, -M[1], M[0] / 2.0])
    try:
        evals, evecs = np.linalg.eig(M)
    except np.linalg.LinAlgError:
        return fit.invalidate("eigen_solve_failed")
    cond = 4.0 * evecs[0].real * evecs[2].real - evecs[1].real ** 2
    idx = np.argmax(np.where(np.isfinite(cond), cond, -np.inf))
    if not np.isfinite(cond[idx]) or cond[idx] <= 0:
        return fit.invalidate("no_ellipse_solution")
    a1 = evecs[:, idx].real
    coeffs_n = np.concatenate([a1, T @ a1])

    geo = _conic_to_geometric(coeffs_n)
    if geo is None:
        return fit.invalidate("conic_is_not_an_ellipse")
    cxn, cyn, an, bn, rot = geo
    # Map normalised geometry back to world units.
    cx, cy = cxn * scale + mx, cyn * scale + my
    a, b = an * scale, bn * scale
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0 or b <= 0:
        return fit.invalidate("non_positive_axes")

    area = float(np.pi * a * b)
    perim = ellipse_perimeter(a, b)
    axis_ratio = float(a / b)
    fit.center_xy = (float(cx), float(cy))
    fit.diameter_m = area_equivalent_diameter(area)
    fit.extra.update({
        "semi_major_m": float(a),
        "semi_minor_m": float(b),
        "major_diameter_m": float(2.0 * a),
        "minor_diameter_m": float(2.0 * b),
        "axis_ratio": axis_ratio,
        "rotation_deg": float(np.degrees(rot)),
        "area_m2": area,
        "perimeter_m": perim,
        "diameter_mean_axes_m": float(a + b),
        "diameter_area_equiv_m": area_equivalent_diameter(area),
        "diameter_perimeter_equiv_m": perimeter_equivalent_diameter(perim),
        "conic_coeffs_normalised": [float(c) for c in coeffs_n],
    })
    res = ellipse_radial_residuals(xy, cx, cy, a, b, rot)
    if not np.all(np.isfinite(res)):
        return fit.invalidate("residuals_not_finite")
    for k, val in residual_stats(res).items():
        setattr(fit, k, val)
    # Angular support about the fitted centre, on the same binning as the coverage
    # attached to every other model, so the gate and the exported number agree.
    cov = angular_coverage(xy, (cx, cy), bin_deg=coverage_bin_deg)
    fit.angular_coverage = cov["coverage_fraction"]
    fit.largest_gap_deg = cov["largest_gap_deg"]
    fit.n_arcs = cov["n_arcs"]
    fit.extra["coverage"] = cov

    # Shell thinness, normalised so it is comparable across stem sizes.
    norm_res = (fit.rmse_m / fit.diameter_m
                if fit.diameter_m and np.isfinite(fit.diameter_m) and fit.diameter_m > 0
                and fit.rmse_m is not None and np.isfinite(fit.rmse_m) else float("nan"))
    fit.extra.update({
        "normalised_radial_residual": float(norm_res),
        "gate_max_axis_ratio": float(max_axis_ratio),
        "gate_min_coverage_fraction": float(min_coverage_fraction),
        "gate_max_gap_deg": float(max_gap_deg),
        "gate_max_normalised_residual": float(max_normalised_residual),
    })

    fit.valid = True
    fit = check_plausible_diameter(fit)
    # The major diameter must also be physically plausible, not just the summary.
    if not MIN_PLAUSIBLE_DIAMETER_M <= 2.0 * a <= MAX_PLAUSIBLE_DIAMETER_M:
        fit.invalidate("major_axis_implausible")
    # Every gate is evaluated, and every failure recorded, rather than returning on
    # the first one: a short arc *and* a thick shell is different evidence from
    # either alone, and a reviewer needs to see both reasons.
    if axis_ratio > max_axis_ratio:
        fit.invalidate(f"axis_ratio_above_{max_axis_ratio}")
    if cov["coverage_fraction"] < min_coverage_fraction:
        fit.invalidate(
            f"ellipse_angular_coverage_{cov['coverage_fraction']:.2f}"
            f"_below_{min_coverage_fraction:.2f}")
    if cov["largest_gap_deg"] > max_gap_deg:
        fit.invalidate(
            f"ellipse_angular_gap_{cov['largest_gap_deg']:.0f}deg"
            f"_above_{max_gap_deg:.0f}deg")
    if not np.isfinite(norm_res) or norm_res > max_normalised_residual:
        fit.invalidate(
            f"ellipse_normalised_residual_{norm_res:.3f}"
            f"_above_{max_normalised_residual:.3f}")
    return fit
