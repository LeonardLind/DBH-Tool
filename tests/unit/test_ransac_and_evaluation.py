"""RANSAC determinism and robustness, bootstrap, profiles, and comparison logic."""
from __future__ import annotations

import numpy as np
import pytest

from dbh_tool.evaluation.bootstrap import bootstrap_diameter
from dbh_tool.evaluation.compare import (
    attribute_ellipticity,
    classify_radial_anomaly,
    compare_diameters,
    lean_bias_estimate,
)
from dbh_tool.evaluation.profile import diameter_profile
from dbh_tool.fitting.circle import fit_circle_geometric
from dbh_tool.fitting.ellipse import fit_ellipse
from dbh_tool.fitting.outline import fit_outline_radial_median
from dbh_tool.fitting.ransac_circle import fit_circle_ransac
from dbh_tool.stems.axis import StemAxis, vertical_axis
from dbh_tool.synthetic import circle_section, fluted_section, with_neighbour_cluster

TRUE_D = 0.40


# ---------------------------------------------------------------- RANSAC -----
def test_ransac_is_deterministic():
    s = circle_section(diameter_m=TRUE_D, noise_m=0.004, outlier_fraction=0.2, seed=3)
    a = fit_circle_ransac(s.xy, seed=42)
    b = fit_circle_ransac(s.xy, seed=42)
    assert a.diameter_m == b.diameter_m
    assert a.inlier_count == b.inlier_count
    assert a.center_xy == b.center_xy


def test_ransac_beats_least_squares_under_contamination():
    """The headline robustness claim, measured rather than asserted."""
    ls_err, rs_err = [], []
    for trial in range(30):
        s = circle_section(diameter_m=TRUE_D, n_points=800, noise_m=0.004,
                           outlier_fraction=0.25, outlier_offset_m=0.10,
                           seed=200 + trial)
        ls = fit_circle_geometric(s.xy)
        rs = fit_circle_ransac(s.xy, residual_threshold_m=0.012, seed=42)
        ls_err.append(abs(ls.diameter_m - TRUE_D))
        rs_err.append(abs(rs.diameter_m - TRUE_D))
    assert np.mean(rs_err) < np.mean(ls_err) / 3
    assert np.mean(rs_err) < 0.01


def test_least_squares_is_pulled_outward_by_outside_contamination():
    """Contaminants outside the stem inflate an ordinary fit, as documented."""
    biases = []
    for trial in range(30):
        s = circle_section(diameter_m=TRUE_D, n_points=800, noise_m=0.003,
                           outlier_fraction=0.25, outlier_offset_m=0.10,
                           seed=300 + trial)
        biases.append(fit_circle_geometric(s.xy).diameter_m - TRUE_D)
    assert np.mean(biases) > 0.01


def test_ransac_ignores_a_neighbouring_cluster():
    s = with_neighbour_cluster(
        circle_section(diameter_m=TRUE_D, n_points=1500, noise_m=0.003, seed=5),
        offset_m=(0.42, 0.0), n_points=400, spread_m=0.03, seed=6)
    fit = fit_circle_ransac(s.xy, residual_threshold_m=0.010, seed=42)
    assert fit.diameter_m == pytest.approx(TRUE_D, abs=0.01)
    assert fit.inlier_fraction < 1.0


def test_ransac_reports_coverage_not_just_inliers():
    """A clean short arc gives high inlier fraction and low coverage.

    Both must be visible, because inlier fraction alone would present this as a
    high-quality fit.
    """
    s = circle_section(diameter_m=TRUE_D, n_points=600, noise_m=0.003, arc_deg=90.0)
    fit = fit_circle_ransac(s.xy, seed=42)
    from dbh_tool.evaluation.coverage import attach_coverage
    attach_coverage(fit, s.xy)
    assert fit.inlier_fraction > 0.8
    assert fit.angular_coverage < 0.3
    assert fit.largest_gap_deg > 200


def test_ransac_rejects_a_negative_threshold():
    s = circle_section()
    with pytest.raises(ValueError):
        fit_circle_ransac(s.xy, residual_threshold_m=-1.0)


# ------------------------------------------------------------- bootstrap -----
def test_bootstrap_std_grows_as_coverage_shrinks():
    full = circle_section(diameter_m=TRUE_D, n_points=600, noise_m=0.004, arc_deg=360.0)
    part = circle_section(diameter_m=TRUE_D, n_points=600, noise_m=0.004, arc_deg=70.0)
    b_full = bootstrap_diameter(full.xy, fit_circle_geometric, n_resamples=80, seed=1)
    b_part = bootstrap_diameter(part.xy, fit_circle_geometric, n_resamples=80, seed=1)
    assert b_full["n_success"] > 70
    assert b_part["std_m"] > 4 * b_full["std_m"]


def test_bootstrap_is_reproducible():
    s = circle_section(diameter_m=TRUE_D, noise_m=0.004, seed=11)
    a = bootstrap_diameter(s.xy, fit_circle_geometric, n_resamples=40, seed=7)
    b = bootstrap_diameter(s.xy, fit_circle_geometric, n_resamples=40, seed=7)
    assert a["std_m"] == b["std_m"]


# --------------------------------------------------------------- profile -----
def test_profile_interpolates_a_tapering_stem_at_the_target_height():
    heights = [1.20, 1.25, 1.30, 1.35, 1.40]
    # 2 cm per metre of taper, so the diameter at 1.30 m is exactly 0.400.
    diams = [0.400 - 0.02 * (h - 1.30) for h in heights]
    prof = diameter_profile(heights, diams, target_height_m=1.30)
    assert prof["interpolated_at_target_m"] == pytest.approx(0.400, abs=1e-9)
    assert prof["taper_per_m"] == pytest.approx(-0.02, abs=1e-9)
    assert prof["anomalous_heights_m"] == []


def test_profile_flags_a_single_bad_slice():
    heights = [1.20, 1.25, 1.30, 1.35, 1.40]
    diams = [0.400, 0.399, 0.560, 0.397, 0.396]   # 1.30 m cut a branch scar
    prof = diameter_profile(heights, diams, target_height_m=1.30)
    assert 1.30 in prof["anomalous_heights_m"]


def test_profile_flags_diameter_increasing_with_height_as_a_deformity():
    heights = [1.20, 1.25, 1.30, 1.35, 1.40]
    diams = [0.40, 0.45, 0.50, 0.55, 0.60]
    prof = diameter_profile(heights, diams, target_height_m=1.30,
                            buttress_taper_threshold_per_m=0.10)
    assert any("buttress" in w for w in prof["warnings"])


def test_profile_handles_missing_heights():
    prof = diameter_profile([1.2, 1.3, 1.4], [None, 0.4, None], target_height_m=1.3)
    assert prof["n_heights"] == 1
    assert prof["interpolated_at_target_m"] == pytest.approx(0.4)


# ------------------------------------------------------------ comparison -----
def test_compare_reports_worst_pair_and_excludes_diagnostics():
    fits = {}
    for name, d in (("a", 0.40), ("b", 0.41), ("c", 0.45), ("diag", 0.90)):
        s = circle_section(diameter_m=d, n_points=400)
        f = fit_circle_geometric(s.xy)
        f.model = name
        fits[name] = f
    cmp_ = compare_diameters(fits, exclude=("diag",))
    assert cmp_["n_models_compared"] == 3
    assert cmp_["max_pairwise_difference_m"] == pytest.approx(0.05, abs=1e-3)
    assert set(cmp_["max_pairwise_pair"]) == {"a", "c"}
    assert cmp_["models_diagnostic_only"] == ["diag"]


def test_compare_does_not_claim_agreement_from_one_model():
    s = circle_section()
    fits = {"only": fit_circle_geometric(s.xy)}
    cmp_ = compare_diameters(fits)
    assert cmp_["max_pairwise_difference_m"] is None
    assert cmp_["n_models_compared"] == 1


def test_lean_bias_estimate_matches_the_closed_form():
    b = lean_bias_estimate(0.40, 20.0)
    assert b["major_axis_bias_m"] == pytest.approx(0.40 / np.cos(np.radians(20)) - 0.40)
    assert b["circle_fit_bias_percent"] > 3.0
    assert lean_bias_estimate(0.4, 0.0)["circle_fit_bias_m"] == 0.0


def test_ellipticity_attributed_to_lean_when_ratio_matches():
    tilt = 15.0
    ratio = 1.0 / np.cos(np.radians(tilt))
    s_major = 0.40 * ratio
    from dbh_tool.synthetic import ellipse_section
    s = ellipse_section(major_m=s_major, minor_m=0.40, rotation_deg=30.0, n_points=800)
    ell = fit_ellipse(s.xy)
    axis = StemAxis(point_xyz=np.zeros(3), direction=np.array([0.0, 0.0, 1.0]),
                    reference_hag_m=1.3, tilt_deg=tilt, azimuth_deg=30.0,
                    straightness_m=0.0, n_bins_used=5, valid=True)
    out = attribute_ellipticity(ell, axis, circular_ratio_max=1.02)
    assert out["verdict"] == "LEAN_EXPLAINS_ELLIPTICITY"
    assert out["expected_ratio_from_lean"] == pytest.approx(ratio, rel=1e-6)


def test_ellipticity_called_oval_when_ratio_exceeds_lean():
    from dbh_tool.synthetic import ellipse_section
    s = ellipse_section(major_m=0.60, minor_m=0.40, rotation_deg=30.0, n_points=800)
    ell = fit_ellipse(s.xy)
    axis = StemAxis(point_xyz=np.zeros(3), direction=np.array([0.0, 0.0, 1.0]),
                    reference_hag_m=1.3, tilt_deg=5.0, azimuth_deg=30.0,
                    straightness_m=0.0, n_bins_used=5, valid=True)
    out = attribute_ellipticity(ell, axis)
    assert out["verdict"] == "OVAL_BEYOND_LEAN"
    assert out["ratio_excess"] > 0.4


def test_ellipticity_circular_when_axes_are_equal():
    s = circle_section(diameter_m=0.4, n_points=600, noise_m=0.002)
    out = attribute_ellipticity(fit_ellipse(s.xy), vertical_axis((0, 0), 0.0))
    assert out["verdict"] == "CIRCULAR"


def test_shape_is_unattributed_when_the_ellipse_is_declined():
    """The documented knock-on of DEC-016.

    On a short arc the ellipse used to return a large axis ratio, which
    ``attribute_ellipticity`` then read as genuine ovality. Now the ellipse is
    declined and the shape is honestly unattributed -- which pushes the tree to
    review instead of recording a circular stem as oval.
    """
    s = circle_section(diameter_m=0.40, n_points=600, noise_m=0.004, arc_deg=120.0)
    ell = fit_ellipse(s.xy)
    assert not ell.valid
    assert ell.extra["axis_ratio"] > 1.3, "the misleading ratio is still exported"
    out = attribute_ellipticity(ell, vertical_axis((0, 0), 0.0))
    assert out["verdict"] == "NO_ELLIPSE_FIT"


# ------------------------------------------------- contamination vs shape ----
def _anomaly_for(section, threshold=0.010):
    ransac = fit_circle_ransac(section.xy, residual_threshold_m=threshold, seed=42)
    outline = fit_outline_radial_median(section.xy, ransac.center_xy, n_sectors=72)
    return classify_radial_anomaly(section.xy, ransac, outline)


def test_clean_stem_is_classified_clean():
    s = circle_section(diameter_m=0.4, n_points=3000, noise_m=0.002)
    assert _anomaly_for(s)["verdict"] == "CLEAN"


def test_attached_cluster_is_called_contamination_not_shape():
    """The distinction the tool must not get wrong.

    An outline model will trace an attached liana and report it as stem shape, so
    the classifier has to separate volumetric, outward-sitting anomalies from
    genuine flutes.
    """
    s = with_neighbour_cluster(
        circle_section(diameter_m=0.40, n_points=3000, noise_m=0.003, seed=21),
        offset_m=(0.26, 0.0), n_points=900, spread_m=0.05, seed=22)
    assert _anomaly_for(s)["verdict"] == "CONTAMINATION_SUSPECTED"


def test_fluted_stem_is_called_irregular_shape():
    s = fluted_section(mean_diameter_m=0.5, n_lobes=6, flute_amplitude=0.10,
                       n_points=6000, noise_m=0.002)
    out = _anomaly_for(s)
    assert out["verdict"] == "IRREGULAR_SHAPE"
    assert out["n_lobes_estimate"] >= 3
    # A flute is still a surface: sectors stay thin.
    assert out["median_sector_radial_iqr_m"] < 0.015


def test_anomaly_needs_a_robust_fit():
    out = classify_radial_anomaly(np.zeros((10, 2)), None, None)
    assert out["verdict"] == "NO_ROBUST_FIT"
