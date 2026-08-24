"""Benchmark statistics, comparator selection, and the experiment harness.

The benchmark is the thing that will decide whether this tool is trusted, so its
arithmetic and its disclosure rules are tested directly, with errors chosen so the
expected bias/MAE/RMSE can be computed by hand.
"""
from __future__ import annotations

import numpy as np
import pytest

from dbh_tool.config import RunConfig
from dbh_tool.evaluation.benchmark import (
    BenchmarkReport,
    compare_to_reference,
    load_reference_table,
    select_comparator,
)
from dbh_tool.evaluation.experiments import (
    geometry_comparison,
    get_config_value,
    height_strategy_comparison,
    model_comparison,
    set_config_value,
)
from dbh_tool.fitting.common import FitResult
from dbh_tool.measure import TreeMeasurement
from dbh_tool.stems.axis import vertical_axis

REF_HEADER = ("tree_id,field_dbh_cm,measurement_height_m,measurement_method,"
              "shape_class,buttressed,leaning,split,x,y,notes\n")


def _write_ref(tmp_path, rows: str, header: str = REF_HEADER):
    p = tmp_path / "reference_trees.csv"
    p.write_text(header + rows, encoding="utf-8")
    return p


def _measurement(tree_id: str, dbh_cm: float | None, status: str = "ACCEPTED_CIRCULAR",
                 band: str = "HIGH", model: str = "circle_ransac",
                 coverage: float = 0.98, extra: dict | None = None,
                 single: float | None = None, median: float | None = None,
                 taper: float | None = None, stem_normal: float | None = None,
                 tilt_deg: float = 0.0) -> TreeMeasurement:
    """A TreeMeasurement carrying only what the benchmark reads."""
    d_m = None if dbh_cm is None else dbh_cm / 100.0
    fit = FitResult(model=model, diameter_m=d_m, valid=d_m is not None,
                    angular_coverage=coverage, extra=dict(extra or {}))
    axis = vertical_axis((0.0, 0.0), 0.0)
    axis.tilt_deg = tilt_deg
    return TreeMeasurement(
        tree_id=tree_id, dbh_m=d_m, status=status, confidence_band=band,
        selected_model=model, recommended_model=model,
        selection_is_recommendation=True, review_state="PENDING",
        measurement_height_m=1.30, primary_geometry="horizontal",
        dbh_source="single_slice", local_ground_z_m=0.0, center_xy=(0.0, 0.0),
        dbh_single_slice_m=single if single is not None else d_m,
        dbh_profile_median_m=median, dbh_taper_interpolated_m=taper,
        dbh_stem_normal_m=stem_normal, candidate_results=[fit], axis=axis)


# ------------------------------------------------------- loading and validation
def test_load_reference_table(tmp_path):
    p = _write_ref(tmp_path,
                   "A,40.0,1.30,tape,regular,false,false,dev,1.0,2.0,fine\n"
                   "B,25.5,,caliper_crossed,elliptical,false,true,holdout,,,\n")
    ref = load_reference_table(p)
    assert set(ref) == {"A", "B"}
    assert ref["A"].field_dbh_cm == 40.0
    assert ref["A"].measurement_method == "tape"
    assert ref["A"].split == "dev"
    assert ref["B"].leaning is True
    assert ref["B"].split == "holdout"
    assert ref["B"].measurement_height_m is None


def test_reference_table_defaults_split_to_dev(tmp_path):
    p = _write_ref(tmp_path, "A,40.0,,,,,,,,,\n")
    assert load_reference_table(p)["A"].split == "dev"


def test_missing_required_column_is_rejected(tmp_path):
    p = _write_ref(tmp_path, "A,1.3\n", header="tree_id,measurement_height_m\n")
    with pytest.raises(ValueError, match="missing required column"):
        load_reference_table(p)


def test_empty_reference_table_is_rejected(tmp_path):
    p = _write_ref(tmp_path, "")
    with pytest.raises(ValueError, match="no data rows"):
        load_reference_table(p)


@pytest.mark.parametrize("row,match", [
    ("A,notanumber,,tape,,,,,,,\n", "positive number"),
    ("A,-5,,tape,,,,,,,\n", "positive number"),
    (",40,,tape,,,,,,,\n", "empty tree_id"),
    ("A,40,,laser_guess,,,,,,,\n", "measurement_method"),
])
def test_bad_reference_rows_are_rejected(tmp_path, row, match):
    with pytest.raises(ValueError, match=match):
        load_reference_table(_write_ref(tmp_path, row))


def test_duplicate_tree_id_is_rejected(tmp_path):
    p = _write_ref(tmp_path, "A,40,,tape,,,,,,,\nA,41,,tape,,,,,,,\n")
    with pytest.raises(ValueError, match="duplicate tree_id"):
        load_reference_table(p)


# --------------------------------------------------------- comparator selection
def test_tape_is_compared_against_the_convex_perimeter(tmp_path):
    """DEC-009: a tape bridges flutes, so it maps to the convex perimeter."""
    outline = FitResult(
        model="outline_radial_median", diameter_m=0.50, valid=True,
        extra={"diameter_convex_perimeter_equiv_m": 0.53,
               "diameter_area_equiv_m": 0.50})
    m = _measurement("A", 50.0)
    m.candidate_results.append(outline)
    value, name = select_comparator(m, "tape")
    assert value == pytest.approx(0.53)
    assert "convex_perimeter" in name


def test_crossed_caliper_maps_to_ellipse_mean_axes():
    ell = FitResult(model="ellipse", diameter_m=0.40, valid=True,
                    extra={"diameter_mean_axes_m": 0.42})
    m = _measurement("A", 40.0)
    m.candidate_results.append(ell)
    value, name = select_comparator(m, "caliper_crossed")
    assert value == pytest.approx(0.42)
    assert name.startswith("ellipse")


def test_unknown_method_uses_reported_dbh():
    m = _measurement("A", 40.0)
    value, name = select_comparator(m, "unknown")
    assert value == pytest.approx(0.40)
    assert name == "reported_dbh"


def test_missing_comparator_falls_back_and_says_so():
    """A fallback must be visible: it compares two different quantities."""
    m = _measurement("A", 40.0)      # no outline model present
    value, name = select_comparator(m, "tape")
    assert value == pytest.approx(0.40)
    assert "fallback" in name


# ------------------------------------------------------------------- statistics
def test_metrics_match_hand_computed_values(tmp_path):
    p = _write_ref(tmp_path,
                   "A,40.0,,unknown,,,,dev,,,\n"
                   "B,30.0,,unknown,,,,dev,,,\n"
                   "C,20.0,,unknown,,,,dev,,,\n")
    ref = load_reference_table(p)
    # errors: +2, -1, +3  ->  bias 4/3, MAE 2, RMSE sqrt(14/3)
    ms = [_measurement("A", 42.0), _measurement("B", 29.0), _measurement("C", 23.0)]
    rep = compare_to_reference(ms, ref)
    assert rep.n_matched == 3 and rep.n_reported == 3 and rep.n_refused == 0
    assert rep.overall["bias_cm"] == pytest.approx(4.0 / 3.0)
    assert rep.overall["mae_cm"] == pytest.approx(2.0)
    assert rep.overall["rmse_cm"] == pytest.approx(np.sqrt(14.0 / 3.0))
    assert rep.overall["max_abs_error_cm"] == pytest.approx(3.0)
    # relative RMSE is against the mean field value of 30 cm
    assert rep.overall["relative_rmse_percent"] == pytest.approx(
        100.0 * np.sqrt(14.0 / 3.0) / 30.0)
    assert rep.review_rate == 0.0


def test_refused_trees_count_towards_review_rate_not_error(tmp_path):
    """A refused tree is not an error of zero; it is a disclosed non-answer."""
    p = _write_ref(tmp_path,
                   "A,40.0,,unknown,,,,dev,,,\n"
                   "B,30.0,,unknown,,,,dev,,,\n")
    ref = load_reference_table(p)
    ms = [_measurement("A", 42.0),
          _measurement("B", None, status="REVIEW_REQUIRED", band="REVIEW_REQUIRED")]
    rep = compare_to_reference(ms, ref)
    assert rep.n_reported == 1 and rep.n_refused == 1
    assert rep.review_rate == pytest.approx(0.5)
    assert rep.overall["n"] == 1
    assert rep.overall["bias_cm"] == pytest.approx(2.0)
    text = rep.summary()
    assert "review rate: 50%" in text
    # Accuracy must never be shown without saying what it excludes.
    assert "not errors of zero" in text
    assert "1/2" in text


def test_summary_refuses_accuracy_when_nothing_was_reported(tmp_path):
    p = _write_ref(tmp_path, "A,40.0,,unknown,,,,dev,,,\n")
    ref = load_reference_table(p)
    rep = compare_to_reference([_measurement("A", None)], ref)
    text = rep.summary()
    assert "no accuracy statistics" in text
    assert "RMSE" not in text
    assert rep.review_rate == 1.0


def test_unmatched_trees_are_listed_not_dropped(tmp_path):
    p = _write_ref(tmp_path,
                   "A,40.0,,unknown,,,,dev,,,\n"
                   "MISSING,33.0,,unknown,,,,dev,,,\n")
    ref = load_reference_table(p)
    rep = compare_to_reference([_measurement("A", 40.0), _measurement("EXTRA", 20.0)], ref)
    assert rep.unmatched_reference_ids == ["MISSING"]
    assert rep.unmatched_measurement_ids == ["EXTRA"]
    assert rep.n_matched == 1
    assert any("no matching measurement" in w for w in rep.warnings)


def test_split_filtering(tmp_path):
    p = _write_ref(tmp_path,
                   "A,40.0,,unknown,,,,dev,,,\n"
                   "B,30.0,,unknown,,,,holdout,,,\n")
    ref = load_reference_table(p)
    ms = [_measurement("A", 41.0), _measurement("B", 32.0)]
    assert compare_to_reference(ms, ref, split="dev").overall["bias_cm"] == pytest.approx(1.0)
    assert compare_to_reference(ms, ref, split="holdout").overall["bias_cm"] == pytest.approx(2.0)
    assert compare_to_reference(ms, ref).n_matched == 2


def test_stratification_by_shape_size_and_coverage(tmp_path):
    p = _write_ref(tmp_path,
                   "A,15.0,,unknown,regular,,,dev,,,\n"
                   "B,50.0,,unknown,fluted,,,dev,,,\n")
    ref = load_reference_table(p)
    ms = [_measurement("A", 16.0, coverage=0.95),
          _measurement("B", 48.0, coverage=0.40)]
    rep = compare_to_reference(ms, ref)
    assert rep.by_shape_class["regular"]["n"] == 1
    assert rep.by_shape_class["fluted"]["bias_cm"] == pytest.approx(-2.0)
    assert rep.by_size_class["under_20cm"]["n"] == 1
    assert rep.by_size_class["40_80cm"]["n"] == 1
    assert rep.by_coverage_class["coverage_over_85"]["n"] == 1
    assert rep.by_coverage_class["coverage_under_60"]["n"] == 1


def test_small_sample_is_flagged(tmp_path):
    p = _write_ref(tmp_path, "A,40.0,,unknown,,,,dev,,,\n")
    rep = compare_to_reference([_measurement("A", 40.5)], load_reference_table(p))
    assert any("far too few" in w for w in rep.warnings)


def test_empty_report_review_rate_is_none():
    rep = BenchmarkReport(n_reference=0, n_matched=0, n_reported=0, n_refused=0)
    assert rep.review_rate is None


# ------------------------------------------------------------- config plumbing
def test_set_config_value_is_a_copy():
    cfg = RunConfig()
    other = set_config_value(cfg, "slice.thickness_m", 0.05)
    assert other.slice.thickness_m == 0.05
    assert cfg.slice.thickness_m == 0.10, "the original must not be mutated"
    assert get_config_value(other, "slice.thickness_m") == 0.05


def test_set_config_value_rejects_unknown_paths():
    cfg = RunConfig()
    with pytest.raises(ValueError):
        set_config_value(cfg, "slice.not_a_field", 1)
    with pytest.raises(ValueError):
        set_config_value(cfg, "nope.thickness_m", 1)


# -------------------------------------------------------- free experiments ----
def test_height_strategy_comparison_reads_existing_measurements():
    ms = [_measurement("A", 40.0, single=0.40, median=0.41, taper=0.405),
          _measurement("B", 20.0, single=0.20, median=0.21, taper=0.205)]
    out = height_strategy_comparison(ms)
    assert out["n_trees"] == 2
    dev = out["deviation_from_tree_median_cm"]
    # taper sits at the median of the three in both trees, so its deviation is 0
    assert dev["taper_interpolated"]["mean"] == pytest.approx(0.0, abs=1e-9)
    assert dev["profile_median"]["mean"] > 0
    assert dev["single_slice"]["mean"] < 0
    assert out["evidence"] == "internal agreement only"


def test_geometry_comparison_reports_predicted_lean_effect():
    """The predicted difference must be shown next to the measured one."""
    ms = [_measurement("A", 40.0, single=0.4131, stem_normal=0.40, tilt_deg=20.0)]
    out = geometry_comparison(ms)
    row = out["per_tree"][0]
    assert row["difference_cm"] == pytest.approx(1.31, abs=0.01)
    expected = 50.0 * 0.4131 * (1 / np.cos(np.radians(20.0)) - 1)
    assert row["predicted_difference_cm"] == pytest.approx(expected, abs=0.01)
    assert out["sign_consistent"] is True


def test_geometry_comparison_detects_inconsistent_signs():
    ms = [_measurement("A", 40.0, single=0.401, stem_normal=0.400, tilt_deg=8.0),
          _measurement("B", 30.0, single=0.299, stem_normal=0.300, tilt_deg=8.0)]
    assert geometry_comparison(ms)["sign_consistent"] is False


def test_model_comparison_measures_spread_between_models():
    a = FitResult(model="circle_geometric", diameter_m=0.40, valid=True)
    b = FitResult(model="circle_ransac", diameter_m=0.38, valid=True)
    c = FitResult(model="ellipse", diameter_m=0.42, valid=True)
    m = _measurement("A", 40.0)
    m.candidate_results = [a, b, c]
    out = model_comparison([m])
    dev = out["deviation_from_per_tree_median"]
    assert dev["circle_geometric"]["median_cm"] == pytest.approx(0.0, abs=1e-9)
    assert dev["circle_ransac"]["median_cm"] == pytest.approx(-2.0, abs=1e-9)
    diff = out["difference_from_circle_geometric"]
    assert diff["circle_ransac"]["mean_cm"] == pytest.approx(-2.0, abs=1e-9)
    assert "not which is right" in out["evidence"]
