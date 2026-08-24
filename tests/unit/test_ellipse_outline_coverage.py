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
from dbh_tool.synthetic import circle_section, ellipse_section, fluted_section


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
