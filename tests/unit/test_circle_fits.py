"""Circle-fit correctness, and the partial-arc behaviour that motivates four fits."""
from __future__ import annotations

import numpy as np
import pytest

from dbh_tool.fitting.circle import (
    CIRCLE_FITTERS,
    fit_circle_algebraic,
    fit_circle_geometric,
    fit_circle_pratt,
    fit_circle_taubin,
)
from dbh_tool.synthetic import circle_section

TRUE_D = 0.38
CENTER = (3.5, -2.25)


@pytest.mark.parametrize("name,fitter", sorted(CIRCLE_FITTERS.items()))
def test_exact_recovery_on_noiseless_full_circle(name, fitter):
    """Every fit must recover a noiseless circle to numerical precision."""
    t = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    xy = np.column_stack([CENTER[0] + TRUE_D / 2 * np.cos(t),
                          CENTER[1] + TRUE_D / 2 * np.sin(t)])
    fit = fitter(xy)
    assert fit.valid
    assert fit.diameter_m == pytest.approx(TRUE_D, abs=1e-9)
    assert fit.center_xy[0] == pytest.approx(CENTER[0], abs=1e-9)
    assert fit.center_xy[1] == pytest.approx(CENTER[1], abs=1e-9)
    assert fit.rmse_m == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("name,fitter", sorted(CIRCLE_FITTERS.items()))
def test_translation_and_scale_equivariance(name, fitter):
    """A fit must not depend on where the data sits or how big it is."""
    s = circle_section(diameter_m=0.4, noise_m=0.003, arc_deg=200, seed=7)
    base = fitter(s.xy)
    shifted = fitter(s.xy + np.array([1234.5, -987.6]))
    scaled = fitter(s.xy * 10.0)
    assert shifted.diameter_m == pytest.approx(base.diameter_m, rel=1e-6)
    assert scaled.diameter_m == pytest.approx(base.diameter_m * 10.0, rel=1e-6)


def test_too_few_points_is_invalid_not_an_exception():
    for fitter in CIRCLE_FITTERS.values():
        fit = fitter(np.array([[0.0, 0.0], [1.0, 0.0]]))
        assert not fit.valid
        assert "too_few_points" in fit.warnings


def test_implausible_diameter_is_rejected():
    """A near-collinear point set implies an enormous circle, which is not a stem."""
    xy = np.column_stack([np.linspace(0, 1, 50), np.full(50, 0.0)])
    xy[:, 1] += 1e-6 * xy[:, 0] ** 2
    fit = fit_circle_geometric(xy)
    assert not fit.valid


def _arc_bias(fitter, arc_deg: float, n_trials: int = 120) -> tuple[float, float]:
    errs = []
    for trial in range(n_trials):
        s = circle_section(diameter_m=TRUE_D, n_points=300, noise_m=0.004,
                           arc_deg=arc_deg, center=CENTER, seed=1000 + trial)
        fit = fitter(s.xy)
        if fit.diameter_m is not None and np.isfinite(fit.diameter_m):
            errs.append(fit.diameter_m - TRUE_D)
    return float(np.mean(errs)), float(np.std(errs))


def test_gradient_weighted_fits_beat_algebraic_on_short_arcs():
    """Pratt and Taubin must remove the Kasa short-arc bias.

    This is the evidence for promoting Pratt/Taubin into V1 rather than leaving
    them as "later candidates": occlusion makes short arcs the common case, and on
    a 45-degree arc the algebraic fit is biased by more than 10 cm on a 38 cm stem
    while the gradient-weighted fits stay within a few millimetres.
    """
    alg_bias, _ = _arc_bias(fit_circle_algebraic, 45.0)
    assert alg_bias < -0.05, f"expected a large negative Kasa bias, got {alg_bias:.4f} m"
    for fitter in (fit_circle_taubin, fit_circle_pratt, fit_circle_geometric):
        bias, _ = _arc_bias(fitter, 45.0)
        assert abs(bias) < 0.01, f"{fitter.__name__} bias {bias:.4f} m on a 45 deg arc"
        assert abs(bias) < abs(alg_bias) / 5


@pytest.mark.parametrize("arc_deg", [360.0, 180.0, 90.0])
def test_full_arc_fits_are_unbiased(arc_deg):
    for fitter in (fit_circle_taubin, fit_circle_pratt, fit_circle_geometric):
        bias, _ = _arc_bias(fitter, arc_deg)
        assert abs(bias) < 0.005


def test_uncertainty_grows_as_coverage_shrinks():
    """Scatter must increase on shorter arcs even though residuals stay small.

    This is the quantitative form of "a model fitted to a small visible arc can
    have excellent residual error and still give a wrong diameter".
    """
    _, std_full = _arc_bias(fit_circle_geometric, 360.0)
    _, std_half = _arc_bias(fit_circle_geometric, 180.0)
    _, std_short = _arc_bias(fit_circle_geometric, 60.0)
    assert std_full < std_half < std_short
    assert std_short > 5 * std_full
