"""Ground surface, height normalisation, stem axis and section construction.

These use synthetic 3D scenes with known ground truth, so a failure points at one
pipeline stage rather than at the whole measurement.
"""
from __future__ import annotations

import numpy as np
import pytest

from dbh_tool.evaluation.compare import attribute_ellipticity
from dbh_tool.fitting.circle import fit_circle_geometric
from dbh_tool.fitting.ellipse import fit_ellipse
from dbh_tool.ground.dtm import build_ground_grid
from dbh_tool.ground.local_plane import local_ground_at
from dbh_tool.ground.normalize import height_above_ground, select_height_band
from dbh_tool.stems.axis import estimate_stem_axis, plane_basis, vertical_axis
from dbh_tool.stems.slices import horizontal_section, stem_normal_section
from dbh_tool.synthetic import sloped_ground, tilted_cylinder

SLOPE_DEG = 15.0


def stem_datum_height(xyz, grid, at_xy=(0.0, 0.0)):
    """Height above the single scalar ground datum at the stem, as measure_tree uses."""
    lg = local_ground_at(grid, at_xy[0], at_xy[1], radius_m=2.0)
    return xyz[:, 2] - lg.z_m


def build_scene(tilt_deg: float = 0.0, diameter_m: float = 0.40, azimuth_deg: float = 0.0,
                arc_deg: float = 360.0, seed: int = 0):
    """A sloped ground patch with one cylinder standing on it."""
    ground, gtruth = sloped_ground(slope_deg=SLOPE_DEG, spacing_m=0.06,
                                   roughness_m=0.008, seed=seed)
    gz = gtruth["ground_z_fn"]
    base_z = float(gz(0.0, 0.0)) - 0.3
    stem, struth = tilted_cylinder(
        diameter_m=diameter_m, tilt_deg=tilt_deg, azimuth_deg=azimuth_deg,
        height_m=3.0, n_points=120_000, base_xy=(0.0, 0.0), base_z=base_z,
        noise_m=0.003, arc_deg=arc_deg, seed=seed + 1)
    # Drop the part of the cylinder that would be underground.
    stem = stem[stem[:, 2] > gz(stem[:, 0], stem[:, 1]) + 0.02]
    xyz = np.vstack([ground, stem])
    struth["ground_z_at_stem"] = float(gz(0.0, 0.0))
    struth["ground_z_fn"] = gz
    return xyz, struth


def test_ground_grid_follows_a_slope():
    xyz, truth = build_scene()
    grid = build_ground_grid(xyz, cell_m=0.5)
    gz = truth["ground_z_fn"]
    for x, y in [(-4.0, -4.0), (0.0, 3.0), (5.0, -2.0), (3.0, 3.0)]:
        est = float(np.asarray(grid.elevation(x, y)).item())
        assert est == pytest.approx(float(gz(x, y)), abs=0.10), f"at ({x}, {y})"


def test_ground_grid_rejects_a_subsurface_noise_spike():
    """A plain per-cell minimum would adopt a noise return below the surface."""
    xyz, truth = build_scene()
    spike = np.array([[2.0, 2.0, float(truth["ground_z_fn"](2.0, 2.0)) - 3.0]])
    grid = build_ground_grid(np.vstack([xyz, spike]), cell_m=0.5,
                             despike_tolerance_m=0.35)
    est = float(np.asarray(grid.elevation(2.0, 2.0)).item())
    assert est == pytest.approx(float(truth["ground_z_fn"](2.0, 2.0)), abs=0.15)
    assert grid.meta["n_spike_cells_rejected"] >= 1


def test_height_above_ground_is_flat_on_a_slope():
    """The point of normalisation: a fixed HAG band is a horizontal stripe."""
    xyz, truth = build_scene()
    grid = build_ground_grid(xyz, cell_m=0.5)
    hag = height_above_ground(xyz, grid)
    band = select_height_band(xyz, hag, 1.30, 0.10)
    assert band.sum() > 500
    z_in_band = xyz[band][:, 2]
    # Raw Z inside the band spans the terrain relief, while HAG does not.
    assert z_in_band.max() - z_in_band.min() > 0.15
    assert hag[band].max() - hag[band].min() == pytest.approx(0.10, abs=0.01)


def test_local_ground_reports_slope_and_quality():
    xyz, truth = build_scene()
    grid = build_ground_grid(xyz, cell_m=0.5)
    lg = local_ground_at(grid, 0.0, 0.0, radius_m=2.0)
    assert lg.z_m == pytest.approx(truth["ground_z_at_stem"], abs=0.10)
    assert lg.slope_deg == pytest.approx(SLOPE_DEG, abs=3.0)
    assert lg.quality in ("GOOD", "FAIR")
    assert lg.roughness_m < 0.10


def test_plane_basis_is_orthonormal_and_normal_to_the_axis():
    d = np.array([0.2, -0.1, 1.0])
    d = d / np.linalg.norm(d)
    e1, e2 = plane_basis(d)
    assert np.linalg.norm(e1) == pytest.approx(1.0)
    assert np.linalg.norm(e2) == pytest.approx(1.0)
    assert e1 @ e2 == pytest.approx(0.0, abs=1e-12)
    assert e1 @ d == pytest.approx(0.0, abs=1e-12)
    assert e2 @ d == pytest.approx(0.0, abs=1e-12)


def test_vertical_axis_helper():
    ax = vertical_axis((1.0, 2.0), ground_z=5.0, reference_hag_m=1.3)
    assert ax.tilt_deg == 0.0
    assert ax.point_xyz[2] == pytest.approx(6.3)
    assert ax.xy_at_hag(2.5) == (1.0, 2.0)


@pytest.mark.parametrize("tilt_deg", [0.0, 10.0, 20.0])
def test_axis_tilt_is_recovered(tilt_deg):
    xyz, truth = build_scene(tilt_deg=tilt_deg, azimuth_deg=40.0, seed=10)
    grid = build_ground_grid(xyz, cell_m=0.5)
    height = stem_datum_height(xyz, grid)
    axis = estimate_stem_axis(xyz, height, (0.0, 0.0), 0.20, reference_hag_m=1.30)
    assert axis.valid
    assert axis.tilt_deg == pytest.approx(tilt_deg, abs=2.0)
    if tilt_deg > 5.0:
        assert axis.azimuth_deg == pytest.approx(40.0, abs=15.0)


def test_horizontal_section_of_a_leaning_stem_is_elliptical():
    """A horizontal cut through a tilted cylinder is an ellipse of ratio 1/cos(t).

    This is the systematic bias that motivates computing stem-normal sections in
    V1 instead of deferring them.
    """
    tilt = 20.0
    xyz, truth = build_scene(tilt_deg=tilt, azimuth_deg=0.0, seed=20)
    grid = build_ground_grid(xyz, cell_m=0.5)
    height = stem_datum_height(xyz, grid)
    axis = estimate_stem_axis(xyz, height, (0.0, 0.0), 0.20, reference_hag_m=1.30)
    cx, cy = axis.xy_at_hag(1.30)

    sec = horizontal_section("t", xyz, height, 1.30, 0.06, (cx, cy), 0.40, 0.0)
    ell = fit_ellipse(sec.points_xy)
    assert ell.valid
    expected_ratio = 1.0 / np.cos(np.radians(tilt))
    assert ell.extra["axis_ratio"] == pytest.approx(expected_ratio, abs=0.04)
    assert ell.extra["major_diameter_m"] == pytest.approx(
        truth["expected_horizontal_major_m"], abs=0.02)

    verdict = attribute_ellipticity(ell, axis, circular_ratio_max=1.02,
                                    ratio_tolerance=0.05)
    assert verdict["verdict"] == "LEAN_EXPLAINS_ELLIPTICITY"


def test_stem_normal_section_removes_the_lean_bias():
    """The stem-normal cut recovers the true diameter; the horizontal cut does not."""
    tilt = 20.0
    true_d = 0.40
    xyz, truth = build_scene(tilt_deg=tilt, azimuth_deg=0.0, diameter_m=true_d, seed=30)
    grid = build_ground_grid(xyz, cell_m=0.5)
    height = stem_datum_height(xyz, grid)
    axis = estimate_stem_axis(xyz, height, (0.0, 0.0), 0.20, reference_hag_m=1.30)
    assert axis.valid

    cx, cy = axis.xy_at_hag(1.30)
    horiz = fit_circle_geometric(
        horizontal_section("t", xyz, height, 1.30, 0.06, (cx, cy), 0.40, 0.0).points_xy)
    normal = fit_circle_geometric(
        stem_normal_section("t", xyz, height, axis, 1.30, 0.06, 0.40, 0.0).points_xy)

    assert normal.diameter_m == pytest.approx(true_d, abs=0.01)
    assert horiz.diameter_m > normal.diameter_m
    # Predicted circle-fit bias for a full ellipse is about (major + minor)/2.
    predicted = 0.5 * (true_d / np.cos(np.radians(tilt)) + true_d)
    assert horiz.diameter_m == pytest.approx(predicted, abs=0.012)


def test_vertical_stem_agrees_between_both_geometries():
    """With no lean the two geometries must give the same answer."""
    true_d = 0.36
    xyz, _ = build_scene(tilt_deg=0.0, diameter_m=true_d, seed=40)
    grid = build_ground_grid(xyz, cell_m=0.5)
    height = stem_datum_height(xyz, grid)
    axis = estimate_stem_axis(xyz, height, (0.0, 0.0), 0.18, reference_hag_m=1.30)
    cx, cy = axis.xy_at_hag(1.30)
    horiz = fit_circle_geometric(
        horizontal_section("t", xyz, height, 1.30, 0.08, (cx, cy), 0.40, 0.0).points_xy)
    normal = fit_circle_geometric(
        stem_normal_section("t", xyz, height, axis, 1.30, 0.08, 0.40, 0.0).points_xy)
    assert horiz.diameter_m == pytest.approx(true_d, abs=0.008)
    assert normal.diameter_m == pytest.approx(horiz.diameter_m, abs=0.005)


def test_axis_survives_an_iteration_that_loses_its_bins():
    """A later iteration finding too few usable bins must not corrupt the result.

    Regression test. The axis is refined over several iterations; when a later pass
    tightened its search and dropped below three usable bins it broke out of the
    loop, leaving the working bin-elevation list shorter than the bin heights kept
    from the previous pass. Interpolating the reference elevation then raised
    "fp and xp are not of the same length". Found by a parameter sweep, not by the
    original tests, because it needs a setting that makes a later pass fail.
    """
    xyz, _ = build_scene(tilt_deg=0.0, diameter_m=0.40, seed=70)
    grid = build_ground_grid(xyz, cell_m=0.5)
    height = stem_datum_height(xyz, grid)
    # A tiny seed radius makes the tightened search collapse on later iterations.
    axis = estimate_stem_axis(xyz, height, (0.0, 0.0), 0.004, reference_hag_m=1.30,
                              min_points_per_bin=30, n_iterations=3)
    assert isinstance(axis.valid, bool)          # must return, not raise
    if axis.valid:
        assert np.isfinite(axis.point_xyz[2])
    else:
        assert axis.warnings
