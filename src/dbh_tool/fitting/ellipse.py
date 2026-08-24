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


def fit_ellipse(points, max_axis_ratio: float = MAX_PLAUSIBLE_AXIS_RATIO) -> FitResult:
    """Fit an ellipse and report both axis geometry and area-equivalent diameter.

    ``diameter_m`` is the area-equivalent diameter 2*sqrt(a*b), i.e. the diameter
    of the circle with the same cross-sectional area. The individual axes are in
    ``extra`` and must be used for shape interpretation.
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
    fit.valid = True
    fit = check_plausible_diameter(fit)
    # The major diameter must also be physically plausible, not just the summary.
    if not MIN_PLAUSIBLE_DIAMETER_M <= 2.0 * a <= MAX_PLAUSIBLE_DIAMETER_M:
        fit.invalidate("major_axis_implausible")
    if axis_ratio > max_axis_ratio:
        fit.invalidate(f"axis_ratio_above_{max_axis_ratio}")
    return fit
