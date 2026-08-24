"""Circle fits: algebraic (Kasa), Pratt, Taubin, and geometric least squares.

Why four? They differ mainly on *partial arcs*, which is the dominant real-world
failure mode under occlusion (docs 02, section 5). Kasa is the textbook algebraic
fit and is strongly biased on short arcs; Pratt and Taubin are gradient-weighted
algebraic fits that largely remove that bias at negligible extra cost; geometric
least squares minimises the true orthogonal distance and is the reference
definition of "best-fit circle".

Pratt and Taubin follow the standard formulations in N. Chernov, "Circular and
linear regression: Fitting circles and lines by least squares" (2010).
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares

from .common import (
    FitResult,
    as_xy,
    check_plausible_diameter,
    radial_residuals,
    residual_stats,
)

MIN_POINTS_CIRCLE = 3


def _finish(model: str, xy: np.ndarray, cx: float, cy: float, r: float,
            warnings: list[str] | None = None, extra: dict | None = None) -> FitResult:
    fit = FitResult(
        model=model,
        diameter_definition="2 * fitted circle radius",
        point_count=int(len(xy)),
        warnings=list(warnings or []),
        extra=dict(extra or {}),
    )
    if not all(np.isfinite(v) for v in (cx, cy, r)):
        return fit.invalidate("fit_did_not_converge")
    fit.diameter_m = float(2.0 * r)
    fit.center_xy = (float(cx), float(cy))
    fit.extra["radius_m"] = float(r)
    res = radial_residuals(xy, cx, cy, r)
    for k, v in residual_stats(res).items():
        setattr(fit, k, v)
    fit.valid = True
    return check_plausible_diameter(fit)


def fit_circle_algebraic(points) -> FitResult:
    """Kasa / Coope linear algebraic circle fit.

    Minimises the algebraic distance sum((x^2 + y^2 + Dx + Ey + F)^2), which is a
    single linear solve. Kept as the transparent baseline and as the documented
    example of partial-arc bias. Not recommended as a default.
    """
    xy = as_xy(points)
    if len(xy) < MIN_POINTS_CIRCLE:
        return FitResult(model="circle_algebraic", point_count=len(xy)).invalidate(
            "too_few_points")
    x, y = xy[:, 0], xy[:, 1]
    # Centre the data for conditioning; the shift is undone afterwards.
    mx, my = x.mean(), y.mean()
    u, v = x - mx, y - my
    A = np.column_stack([u, v, np.ones_like(u)])
    b = -(u ** 2 + v ** 2)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return FitResult(model="circle_algebraic", point_count=len(xy)).invalidate(
            "linear_solve_failed")
    D, E, F = sol
    cx, cy = -D / 2.0, -E / 2.0
    disc = cx ** 2 + cy ** 2 - F
    if disc <= 0:
        return FitResult(model="circle_algebraic", point_count=len(xy)).invalidate(
            "negative_radius_discriminant")
    return _finish("circle_algebraic", xy, cx + mx, cy + my, float(np.sqrt(disc)))


def _moments(u: np.ndarray, v: np.ndarray) -> dict:
    z = u ** 2 + v ** 2
    return {
        "Mz": z.mean(),
        "Mxy": (u * v).mean(),
        "Mxx": (u * u).mean(),
        "Myy": (v * v).mean(),
        "Mxz": (u * z).mean(),
        "Myz": (v * z).mean(),
        "Mzz": (z * z).mean(),
    }


def _newton_root(coeffs, x0: float = 0.0, max_iter: int = 100, tol: float = 1e-14):
    """Newton iteration on a polynomial given as ``coeffs``, highest order first.

    Both Pratt and Taubin need the root of a low-order polynomial that is closest
    to zero, and starting from 0.0 converges to it monotonically for well-posed
    inputs. Falls back to the numerically smallest real root if Newton stalls.
    """
    p = np.asarray(coeffs, dtype=float)
    dp = np.polyder(p)
    x = float(x0)
    for _ in range(max_iter):
        fx = float(np.polyval(p, x))
        dfx = float(np.polyval(dp, x))
        if dfx == 0.0 or not np.isfinite(dfx):
            break
        step = fx / dfx
        x -= step
        if not np.isfinite(x):
            break
        if abs(step) < tol * max(1.0, abs(x)):
            return x
    if np.isfinite(x):
        return x
    roots = np.roots(p)
    real = roots[np.abs(roots.imag) < 1e-9].real
    return float(real[np.argmin(np.abs(real))]) if real.size else float("nan")


def _center_from_eta(m: dict, eta: float):
    """Recover the circle centre from the gradient-weighted eigenvalue ``eta``."""
    det = eta * eta - eta * m["Mz"] + (m["Mxx"] * m["Myy"] - m["Mxy"] ** 2)
    if det == 0 or not np.isfinite(det):
        return float("nan"), float("nan")
    cx = (m["Mxz"] * (m["Myy"] - eta) - m["Myz"] * m["Mxy"]) / det / 2.0
    cy = (m["Myz"] * (m["Mxx"] - eta) - m["Mxz"] * m["Mxy"]) / det / 2.0
    return cx, cy


def fit_circle_taubin(points) -> FitResult:
    """Taubin gradient-weighted algebraic circle fit.

    Substantially less biased than Kasa on short arcs, at essentially the same
    cost. This is the recommended initialiser for the geometric fit.
    """
    xy = as_xy(points)
    if len(xy) < MIN_POINTS_CIRCLE:
        return FitResult(model="circle_taubin", point_count=len(xy)).invalidate(
            "too_few_points")
    mx, my = xy[:, 0].mean(), xy[:, 1].mean()
    u, v = xy[:, 0] - mx, xy[:, 1] - my
    m = _moments(u, v)
    cov_xy = m["Mxx"] * m["Myy"] - m["Mxy"] ** 2
    var_z = m["Mzz"] - m["Mz"] ** 2
    # Note: A1 and A0 use var_z, while A2 uses the raw Mzz. Crossing these with
    # the Pratt coefficient set produces a fit that is badly biased on arcs.
    a3 = 4.0 * m["Mz"]
    a2 = -3.0 * m["Mz"] ** 2 - m["Mzz"]
    a1 = var_z * m["Mz"] + 4.0 * cov_xy * m["Mz"] - m["Mxz"] ** 2 - m["Myz"] ** 2
    a0 = (m["Mxz"] * (m["Mxz"] * m["Myy"] - m["Myz"] * m["Mxy"])
          + m["Myz"] * (m["Myz"] * m["Mxx"] - m["Mxz"] * m["Mxy"])
          - var_z * cov_xy)
    eta = _newton_root([a3, a2, a1, a0], x0=0.0)
    cx, cy = _center_from_eta(m, eta)
    disc = cx ** 2 + cy ** 2 + m["Mz"]
    r = float(np.sqrt(disc)) if np.isfinite(disc) and disc > 0 else float("nan")
    return _finish("circle_taubin", xy, cx + mx, cy + my, r,
                   extra={"taubin_eta": float(eta)})


def fit_circle_pratt(points) -> FitResult:
    """Pratt algebraic circle fit (normalises by the conic gradient)."""
    xy = as_xy(points)
    if len(xy) < MIN_POINTS_CIRCLE:
        return FitResult(model="circle_pratt", point_count=len(xy)).invalidate(
            "too_few_points")
    mx, my = xy[:, 0].mean(), xy[:, 1].mean()
    u, v = xy[:, 0] - mx, xy[:, 1] - my
    m = _moments(u, v)
    cov_xy = m["Mxx"] * m["Myy"] - m["Mxy"] ** 2
    var_z = m["Mzz"] - m["Mz"] ** 2
    a2 = 4.0 * cov_xy - 3.0 * m["Mz"] ** 2 - m["Mzz"]
    a1 = var_z * m["Mz"] + 4.0 * cov_xy * m["Mz"] - m["Mxz"] ** 2 - m["Myz"] ** 2
    a0 = (m["Mxz"] * (m["Mxz"] * m["Myy"] - m["Myz"] * m["Mxy"])
          + m["Myz"] * (m["Myz"] * m["Mxx"] - m["Mxz"] * m["Mxy"])
          - var_z * cov_xy)
    # Pratt characteristic polynomial: 4*eta^4 + a2*eta^2 + a1*eta + a0.
    eta = _newton_root([4.0, 0.0, a2, a1, a0], x0=0.0)
    cx, cy = _center_from_eta(m, eta)
    disc = cx ** 2 + cy ** 2 + m["Mz"] + 2.0 * eta
    r = float(np.sqrt(disc)) if np.isfinite(disc) and disc > 0 else float("nan")
    return _finish("circle_pratt", xy, cx + mx, cy + my, r,
                   extra={"pratt_eta": float(eta)})


def fit_circle_geometric(points, init: tuple[float, float, float] | None = None) -> FitResult:
    """Geometric (orthogonal-distance) least-squares circle.

    Minimises the sum of squared radial distances, which is the reference
    definition. Initialised from Taubin so that the Levenberg-Marquardt step
    stays reliable even on partial arcs.
    """
    xy = as_xy(points)
    if len(xy) < MIN_POINTS_CIRCLE:
        return FitResult(model="circle_geometric", point_count=len(xy)).invalidate(
            "too_few_points")
    if init is None:
        seed = fit_circle_taubin(xy)
        if seed.center_xy is None:
            seed = fit_circle_algebraic(xy)
        if seed.center_xy is None:
            return FitResult(model="circle_geometric", point_count=len(xy)).invalidate(
                "no_usable_initialisation")
        init = (seed.center_xy[0], seed.center_xy[1], seed.extra["radius_m"])

    def resid(p):
        return np.hypot(xy[:, 0] - p[0], xy[:, 1] - p[1]) - p[2]

    def jac(p):
        dx, dy = xy[:, 0] - p[0], xy[:, 1] - p[1]
        d = np.hypot(dx, dy)
        d = np.where(d < 1e-12, 1e-12, d)
        return np.column_stack([-dx / d, -dy / d, -np.ones_like(d)])

    out = least_squares(resid, np.asarray(init, float), jac=jac, method="lm",
                        xtol=1e-12, ftol=1e-12)
    cx, cy, r = out.x
    warn = [] if out.success else ["optimiser_reported_failure"]
    # A negative radius parameterises the same circle; normalise the sign.
    return _finish("circle_geometric", xy, cx, cy, abs(float(r)), warnings=warn,
                   extra={"n_function_evals": int(out.nfev),
                          "init": [float(v) for v in init]})


CIRCLE_FITTERS = {
    "circle_algebraic": fit_circle_algebraic,
    "circle_taubin": fit_circle_taubin,
    "circle_pratt": fit_circle_pratt,
    "circle_geometric": fit_circle_geometric,
}
