"""Ellipse, outline and coverage metrics."""
from __future__ import annotations

import numpy as np
import pytest

from dbh_tool.evaluation.coverage import angular_coverage
from dbh_tool.fitting.common import (
    area_equivalent_diameter,
    perimeter_equivalent_diameter,
)
from dbh_tool.fitting.ellipse import ellipse_perimeter, fit_ellipse
from dbh_tool.fitting.outline import fit_outline_radial_median
from dbh_tool.synthetic import (
    circle_section,
    ellipse_section,
    fluted_section,
    with_neighbour_cluster,
)


# --------------------------------------------------------------- ellipse -----
def test_ellipse_parameter_recovery():
    s = ellipse_section(major_m=0.60, minor_m=0.40, rotation_deg=35.0, n_points=400)
    fit = fit_ellipse(s.xy)
    assert fit.valid
    assert fit.extra["major_diameter_m"] == pytest.approx(0.60, abs=1e-9)
    assert fit.extra["minor_diameter_m"] == pytest.approx(0.40, abs=1e-9)
    assert fit.extra["rotation_deg"] == pytest.approx(35.0, abs=1e-6)
    assert fit.extra["axis_ratio"] == pytest.approx(1.5, abs=1e-9)
    assert fit.diameter_m == pytest.approx(s.truth["diameter_area_equiv_m"], abs=1e-9)


@pytest.mark.parametrize("rotation_deg", [0.0, 17.0, 45.0, 90.0, 120.0, 179.0])
def test_ellipse_rotation_recovered_modulo_180(rotation_deg):
    s = ellipse_section(major_m=0.5, minor_m=0.3, rotation_deg=rotation_deg, n_points=500)
    fit = fit_ellipse(s.xy)
    assert fit.valid
    got = fit.extra["rotation_deg"] % 180.0
    expected = rotation_deg % 180.0
    diff = abs((got - expected + 90.0) % 180.0 - 90.0)
    assert diff < 1e-4


def test_ellipse_on_a_circle_has_unit_axis_ratio():
    s = circle_section(diameter_m=0.4, n_points=600, noise_m=0.0)
    fit = fit_ellipse(s.xy)
    assert fit.valid
    assert fit.extra["axis_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert fit.diameter_m == pytest.approx(0.4, abs=1e-8)


def test_ellipse_rejects_implausible_axis_ratio():
    """An under-constrained ellipse must be rejected, not returned as a shape."""
    s = ellipse_section(major_m=1.2, minor_m=0.10, rotation_deg=10.0, n_points=300)
    fit = fit_ellipse(s.xy, max_axis_ratio=3.0)
    assert not fit.valid
    assert any("axis_ratio_above" in w for w in fit.warnings)


# ------------------------------------------- ellipse acceptance gates --------
# DEC-016. Docs 02 section 6 required from the start that "ellipse acceptance
# should require sufficient angular coverage and should be compared with the
# circle"; only the axis-ratio half was implemented, so the model returned a
# confident shape from a quarter arc or a vegetation clump. Measured behaviour
# behind the thresholds is in docs 02 section 24.

def test_short_arc_is_declined_and_would_have_been_badly_wrong():
    """A 120-degree arc of a *circular* stem: the failure DEC-016 exists to stop.

    The fit is still computed and still exported, so the test can assert both
    halves: it is declined, and the number it would have reported is badly wrong.
    A circular stem is called a 1.4-ratio oval and the diameter is out by ~12 cm.
    """
    s = circle_section(diameter_m=0.40, n_points=600, noise_m=0.004, arc_deg=120.0)
    fit = fit_ellipse(s.xy)
    assert not fit.valid
    assert any("ellipse_angular_coverage" in w or "ellipse_angular_gap" in w
               for w in fit.warnings)
    # The rejected geometry is still there for a reviewer to inspect.
    assert fit.extra["axis_ratio"] > 1.3
    assert abs(fit.diameter_m - 0.40) > 0.05


def test_wide_gap_is_declined_even_when_both_arcs_are_clean():
    """Two clean arcs with a wide gap between them: the ellipse across the gap is
    extrapolated, not measured -- the same argument as outline.max_bridge_gap_deg.
    """
    a = circle_section(diameter_m=0.40, n_points=300, noise_m=0.004,
                       arc_deg=100.0, start_deg=0.0, seed=1)
    b = circle_section(diameter_m=0.40, n_points=300, noise_m=0.004,
                       arc_deg=100.0, start_deg=210.0, seed=2)
    xy = np.vstack([a.xy, b.xy])
    fit = fit_ellipse(xy)
    assert not fit.valid
    assert any("ellipse_angular_gap" in w for w in fit.warnings)
    assert fit.largest_gap_deg > 100.0


def test_volumetric_clutter_is_declined_by_the_shell_gate():
    """A clump attached to the stem gives full angular coverage, so only the
    shell-thinness gate can decline it. Before DEC-016 nothing did.
    """
    s = with_neighbour_cluster(
        circle_section(diameter_m=0.40, n_points=600, noise_m=0.004, seed=3),
        offset_m=(0.28, 0.0), n_points=300, spread_m=0.05, seed=3)
    fit = fit_ellipse(s.xy)
    assert not fit.valid
    assert any("ellipse_normalised_residual" in w for w in fit.warnings)
    # Coverage cannot catch this one: the contaminant is *inside* the circumference.
    assert fit.angular_coverage > 0.95


def test_gates_do_not_reject_ordinary_well_observed_stems():
    """The gates must cost real measurements nothing. A conservative model that
    declines everything is as useless as a permissive one that accepts everything.
    """
    good = [
        circle_section(diameter_m=0.40, n_points=600, noise_m=0.002, seed=4),
        circle_section(diameter_m=0.40, n_points=600, noise_m=0.008, seed=5),
        circle_section(diameter_m=0.40, n_points=600, noise_m=0.015, seed=6),
        circle_section(diameter_m=0.40, n_points=600, noise_m=0.004,
                       arc_deg=270.0, seed=7),
        ellipse_section(major_m=0.46, minor_m=0.38, rotation_deg=30.0,
                        n_points=600, noise_m=0.004, seed=8),
        ellipse_section(major_m=0.46, minor_m=0.38, rotation_deg=30.0,
                        n_points=600, noise_m=0.004, arc_deg=300.0, seed=9),
        fluted_section(mean_diameter_m=0.50, n_lobes=6, flute_amplitude=0.08,
                       n_points=1200, noise_m=0.003, seed=10),
    ]
    for s in good:
        fit = fit_ellipse(s.xy)
        assert fit.valid, f"{s.label} was declined: {fit.warnings}"


def test_heavy_fluting_is_declined_because_it_is_not_an_ellipse():
    """The shell gate is a model-adequacy test, not a contamination verdict: a
    deeply fluted stem is genuine geometry that an ellipse cannot describe, and
    the outline model exists for exactly that case.
    """
    s = fluted_section(mean_diameter_m=0.50, n_lobes=6, flute_amplitude=0.20,
                       n_points=1200, noise_m=0.003, seed=11)
    fit = fit_ellipse(s.xy)
    assert not fit.valid
    assert any("ellipse_normalised_residual" in w for w in fit.warnings)


def test_a_declined_ellipse_still_reports_its_geometry_and_its_reasons():
    """No geometry is chosen early: a rejected model is evidence, not an absence."""
    s = circle_section(diameter_m=0.40, n_points=600, noise_m=0.004, arc_deg=90.0)
    fit = fit_ellipse(s.xy)
    assert not fit.valid
    assert fit.warnings
    assert fit.center_xy is not None
    assert fit.diameter_m is not None and np.isfinite(fit.diameter_m)
    for key in ("semi_major_m", "semi_minor_m", "axis_ratio", "rotation_deg",
                "normalised_radial_residual"):
        assert key in fit.extra


def test_each_gate_is_configurable_and_is_what_caused_the_rejection():
    """Loosening a gate must re-admit the case, which proves the rejection came
    from the threshold rather than from a fitting failure.
    """
    short = circle_section(diameter_m=0.40, n_points=600, noise_m=0.004,
                           arc_deg=120.0, seed=12)
    assert not fit_ellipse(short.xy).valid
    assert fit_ellipse(short.xy, min_coverage_fraction=0.0, max_gap_deg=360.0,
                       max_normalised_residual=1.0).valid

    clump = with_neighbour_cluster(
        circle_section(diameter_m=0.40, n_points=600, noise_m=0.004, seed=13),
        offset_m=(0.28, 0.0), n_points=300, spread_m=0.05, seed=13)
    assert not fit_ellipse(clump.xy).valid
    assert fit_ellipse(clump.xy, max_normalised_residual=1.0).valid


def test_gate_records_the_thresholds_it_applied_and_agrees_with_reported_coverage():
    """A stored fit must say which thresholds produced its verdict, and the
    coverage the gate used must be the coverage the export shows.
    """
    s = circle_section(diameter_m=0.40, n_points=600, noise_m=0.004, seed=14)
    fit = fit_ellipse(s.xy, min_coverage_fraction=0.65, max_gap_deg=95.0,
                      max_normalised_residual=0.04)
    assert fit.valid
    assert fit.extra["gate_min_coverage_fraction"] == pytest.approx(0.65)
    assert fit.extra["gate_max_gap_deg"] == pytest.approx(95.0)
    assert fit.extra["gate_max_normalised_residual"] == pytest.approx(0.04)
    assert fit.extra["coverage"]["coverage_fraction"] == pytest.approx(
        fit.angular_coverage)
    assert fit.extra["normalised_radial_residual"] == pytest.approx(
        fit.rmse_m / fit.diameter_m)


def test_coverage_gate_is_independent_of_eccentricity():
    """The coverage gate must not act as a hidden axis-ratio gate. A complete
    outline of a very eccentric ellipse still surrounds its centre, so coverage
    stays high and only the explicit axis-ratio gate may reject it.
    """
    phi = np.linspace(0.0, 2.0 * np.pi, 1500, endpoint=False)
    a, b = 0.50, 0.20        # ratio 2.5, comfortably under max_axis_ratio
    r = a * b / np.hypot(b * np.cos(phi), a * np.sin(phi))
    xy = np.column_stack([r * np.cos(phi), r * np.sin(phi)])
    fit = fit_ellipse(xy)
    assert fit.angular_coverage == pytest.approx(1.0)
    assert fit.extra["axis_ratio"] == pytest.approx(2.5, rel=1e-6)
    assert fit.valid


def test_ellipse_perimeter_matches_circle_when_axes_equal():
    assert ellipse_perimeter(0.2, 0.2) == pytest.approx(2 * np.pi * 0.2, rel=1e-9)


def test_equivalent_diameter_helpers():
    assert area_equivalent_diameter(np.pi * 0.25) == pytest.approx(1.0)
    assert perimeter_equivalent_diameter(np.pi) == pytest.approx(1.0)
    assert np.isnan(area_equivalent_diameter(-1.0))


# --------------------------------------------------------------- outline -----
def test_outline_recovers_a_circle_area():
    """The polar area integral must be exact for a circle, not corner-cut."""
    s = circle_section(diameter_m=0.5, n_points=4000, noise_m=0.0)
    fit = fit_outline_radial_median(s.xy, (0.0, 0.0), n_sectors=72)
    assert fit.valid
    assert fit.extra["area_m2"] == pytest.approx(s.truth["area_m2"], rel=2e-3)
    assert fit.diameter_m == pytest.approx(0.5, rel=2e-3)


def test_outline_recovers_fluted_area_equivalent_diameter():
    s = fluted_section(mean_diameter_m=0.5, n_lobes=6, flute_amplitude=0.12,
                       n_points=6000, noise_m=0.001)
    fit = fit_outline_radial_median(s.xy, (0.0, 0.0), n_sectors=72)
    assert fit.valid
    assert fit.diameter_m == pytest.approx(s.truth["diameter_area_equiv_m"], rel=0.02)


def test_fluted_stem_convex_perimeter_exceeds_area_equivalent():
    """A tape bridges flutes, so the two equivalent diameters must differ.

    This is the reason both are exported: validating an area-equivalent diameter
    against a tape measurement would compare two different quantities.
    """
    s = fluted_section(mean_diameter_m=0.5, n_lobes=6, flute_amplitude=0.15,
                       n_points=8000, noise_m=0.0)
    fit = fit_outline_radial_median(s.xy, (0.0, 0.0), n_sectors=90)
    assert fit.valid
    d_area = fit.extra["diameter_area_equiv_m"]
    d_convex = fit.extra["diameter_convex_perimeter_equiv_m"]
    assert d_convex > d_area
    assert fit.extra["convexity_deficit"] > 0.0


def test_outline_refuses_to_bridge_a_large_gap():
    """An outline over an unobserved arc is modelled, not measured."""
    s = circle_section(diameter_m=0.4, n_points=2000, arc_deg=200.0)
    fit = fit_outline_radial_median(s.xy, (0.0, 0.0), n_sectors=72,
                                    max_bridge_gap_deg=20.0)
    assert not fit.valid
    assert any("exceeds_bridge_limit" in w or "occupied_fraction_below" in w
               for w in fit.warnings)
    assert fit.extra["bridged_fraction"] > 0.3


# -------------------------------------------------------------- coverage -----
@pytest.mark.parametrize("arc_deg,expected", [(360.0, 1.0), (180.0, 0.5), (90.0, 0.25)])
def test_coverage_fraction_tracks_arc_length(arc_deg, expected):
    t = np.linspace(0, np.radians(arc_deg), 1000, endpoint=False)
    xy = np.column_stack([np.cos(t), np.sin(t)])
    cov = angular_coverage(xy, (0.0, 0.0), bin_deg=5.0)
    assert cov["coverage_fraction"] == pytest.approx(expected, abs=0.02)


def test_largest_gap_and_arc_count():
    t = np.concatenate([np.linspace(0.0, 0.5, 300), np.linspace(3.0, 3.5, 300)])
    xy = np.column_stack([np.cos(t), np.sin(t)])
    cov = angular_coverage(xy, (0.0, 0.0), bin_deg=5.0)
    assert cov["n_arcs"] == 2
    assert 100.0 < cov["largest_gap_deg"] < 160.0


def test_full_circle_has_no_gap():
    t = np.linspace(0, 2 * np.pi, 2000, endpoint=False)
    xy = np.column_stack([np.cos(t), np.sin(t)])
    cov = angular_coverage(xy, (0.0, 0.0))
    assert cov["largest_gap_deg"] == 0.0
    assert cov["n_arcs"] == 1
    assert cov["coverage_fraction"] == 1.0


def test_empty_input_reports_no_coverage():
    cov = angular_coverage(np.empty((0, 2)), (0.0, 0.0))
    assert cov["coverage_fraction"] == 0.0
    assert cov["largest_gap_deg"] == 360.0
    assert cov["n_arcs"] == 0
