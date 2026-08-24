"""End-to-end: write a synthetic LAS, then measure known trees through the CLI path.

The fixture is generated rather than committed, so the repository carries no large
or proprietary point clouds while still exercising the real I/O path.

The scene is deliberately awkward: a 15 degree slope, one upright stem, one leaning
stem, one stem seen from a single side, and one fluted stem. Ground truth is known
for all four, so this test measures accuracy, not just absence of crashes.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from dbh_tool.cli import main
from dbh_tool.config import RunConfig
from dbh_tool.ground.dtm import build_ground_grid
from dbh_tool.io.las import LasSource, inspect_las, write_points
from dbh_tool.measure import measure_tree
from dbh_tool.synthetic import fluted_section, sloped_ground, tilted_cylinder

SLOPE_DEG = 15.0

# (id, xy, diameter, tilt, azimuth, arc)
TREES = [
    ("upright", (-6.0, -6.0), 0.42, 0.0, 0.0, 360.0),
    ("leaning", (6.0, -6.0), 0.38, 18.0, 45.0, 360.0),
    ("one_sided", (-6.0, 6.0), 0.34, 0.0, 0.0, 110.0),
    ("fluted", (6.0, 6.0), 0.50, 0.0, 0.0, 360.0),
]


def _fluted_stem(center_xy, ground_z, mean_d, n_points=90_000, seed=0):
    """A vertical fluted stem: stacked fluted cross-sections."""
    rng = np.random.default_rng(seed)
    per = 220
    out = []
    for z in np.arange(0.05, 3.0, 0.01):
        s = fluted_section(mean_diameter_m=mean_d, n_lobes=6, flute_amplitude=0.12,
                           n_points=per, noise_m=0.002, center=center_xy,
                           seed=int(rng.integers(0, 1 << 30)))
        out.append(np.column_stack([s.xy, np.full(len(s.xy), ground_z + z)]))
    xyz = np.vstack(out)
    truth = fluted_section(mean_diameter_m=mean_d, n_lobes=6, flute_amplitude=0.12,
                           n_points=10, center=center_xy, seed=1).truth
    return xyz, truth


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    """Build the LAS fixture once and return (path, ground-truth dict)."""
    d = tmp_path_factory.mktemp("scene")
    path = d / "synthetic_plot.las"
    ground, gtruth = sloped_ground(x_range=(-12.0, 12.0), y_range=(-12.0, 12.0),
                                   slope_deg=SLOPE_DEG, slope_azimuth_deg=30.0,
                                   spacing_m=0.08, roughness_m=0.010, seed=1)
    gz = gtruth["ground_z_fn"]
    parts = [ground]
    truth = {}
    for i, (tid, xy, diam, tilt, az, arc) in enumerate(TREES):
        base = float(gz(xy[0], xy[1]))
        if tid == "fluted":
            stem, t = _fluted_stem(xy, base, diam, seed=50 + i)
            t = dict(t)
            t["diameter_m"] = diam
            t["tilt_deg"] = 0.0
        else:
            stem, t = tilted_cylinder(
                diameter_m=diam, tilt_deg=tilt, azimuth_deg=az, height_m=3.2,
                n_points=110_000, base_xy=xy, base_z=base - 0.3, noise_m=0.004,
                arc_deg=arc, arc_start_deg=20.0 * i, seed=100 + i)
        stem = stem[stem[:, 2] > gz(stem[:, 0], stem[:, 1]) + 0.02]
        parts.append(stem)
        t["xy"] = xy
        t["ground_z"] = base
        truth[tid] = t
    write_points(path, np.vstack(parts))
    return path, truth, gtruth


def test_las_roundtrip_and_validation(scene):
    path, _, _ = scene
    info = inspect_las(path)
    assert info.point_count > 300_000
    assert info.version == "1.2"
    # No CRS and no classification: both must be reported, not assumed away.
    assert any("no CRS" in w for w in info.warnings)
    assert any("classification" in w for w in info.warnings)
    assert not info.has_classification


def test_crop_cylinder_returns_only_nearby_points(scene):
    path, truth, _ = scene
    src = LasSource(path, chunk_size=400_000)
    xyz = src.crop_cylinder(truth["upright"]["xy"], 2.0)
    assert len(xyz) > 10_000
    d = np.hypot(xyz[:, 0] - truth["upright"]["xy"][0],
                 xyz[:, 1] - truth["upright"]["xy"][1])
    assert d.max() <= 2.0 + 1e-9


@pytest.fixture(scope="module")
def measured(scene):
    """Measure every fixture tree once; the pipeline pass is shared by the tests."""
    path, truth, _ = scene
    cfg = RunConfig()
    src = LasSource(path, chunk_size=500_000)
    grid = build_ground_grid(src.iter_xyz(), cell_m=cfg.ground.cell_m,
                            bounds=(src.info.mins[0], src.info.maxs[0],
                                    src.info.mins[1], src.info.maxs[1]))
    out = {}
    for tid, t in truth.items():
        xyz = src.crop_cylinder(t["xy"], 3.0)
        out[tid] = measure_tree(xyz, grid, tid, t["xy"], cfg, roi_radius_m=1.0)
    return out, truth, grid


def test_ground_datum_is_recovered_on_a_slope(measured):
    results, truth, _ = measured
    for tid, m in results.items():
        assert m.local_ground_z_m == pytest.approx(truth[tid]["ground_z"], abs=0.10), tid


def test_upright_stem_diameter_is_accurate(measured):
    results, truth, _ = measured
    m = results["upright"]
    assert m.dbh_m is not None
    assert m.dbh_m == pytest.approx(truth["upright"]["diameter_m"], abs=0.01)
    assert m.axis.tilt_deg < 3.0
    sel = next(f for f in m.candidate_results if f.model == m.selected_model)
    assert sel.angular_coverage > 0.95


def test_all_candidate_models_are_reported_for_every_tree(measured):
    """No geometry may be chosen early: every eligible model must be present."""
    results, _, _ = measured
    expected = {"circle_algebraic", "circle_taubin", "circle_pratt", "circle_geometric",
                "circle_ransac", "ellipse", "outline_radial_median"}
    for tid, m in results.items():
        names = {f.model for f in m.candidate_results}
        assert expected <= names, f"{tid} missing {expected - names}"


def test_leaning_stem_lean_is_detected_and_both_geometries_reported(measured):
    results, truth, _ = measured
    m = results["leaning"]
    assert m.axis.valid
    assert m.axis.tilt_deg == pytest.approx(truth["leaning"]["tilt_deg"], abs=3.0)
    # The horizontal section reads larger than the stem-normal one, and the
    # stem-normal answer is the one close to truth.
    assert m.dbh_stem_normal_m is not None
    assert m.dbh_stem_normal_m == pytest.approx(truth["leaning"]["diameter_m"], abs=0.015)
    assert m.dbh_single_slice_m > m.dbh_stem_normal_m
    assert m.comparison["horizontal_minus_stem_normal_m"] > 0.002
    # And the ellipticity is attributed to the lean rather than to stem shape.
    assert m.ellipticity["verdict"] in ("LEAN_EXPLAINS_ELLIPTICITY", "CIRCULAR")


def test_one_sided_stem_is_flagged_rather_than_answered(measured):
    """Partial coverage must degrade the verdict, however good the residuals look."""
    results, _, _ = measured
    m = results["one_sided"]
    sel_cov = max((f.angular_coverage or 0.0) for f in m.candidate_results if f.valid)
    assert sel_cov < 0.6
    assert m.status in ("REVIEW_REQUIRED", "FAILED_INSUFFICIENT_DATA",
                        "INVALID_MEASUREMENT_HEIGHT")
    assert m.confidence_band in ("REVIEW_REQUIRED", "FAILED", "LOW")
    assert any("coverage" in r or "gap" in r for r in m.reasons)


def test_fluted_stem_area_equivalent_and_tape_comparator_differ(measured):
    """A fluted stem must expose both equivalent diameters, since a tape bridges flutes."""
    results, truth, _ = measured
    m = results["fluted"]
    outline = next((f for f in m.candidate_results
                    if f.model == "outline_radial_median"), None)
    assert outline is not None and outline.valid
    d_area = outline.extra["diameter_area_equiv_m"]
    d_tape = outline.extra["diameter_convex_perimeter_equiv_m"]
    assert d_area == pytest.approx(truth["fluted"]["diameter_area_equiv_m"], rel=0.05)
    assert d_tape > d_area
    assert outline.extra["convexity_deficit"] > 0.0


def test_provenance_records_config_and_calibration_status(measured):
    results, _, _ = measured
    m = results["upright"]
    assert m.provenance["version"]
    assert "UNCALIBRATED" in m.provenance["calibration_status"]
    assert "slice.thickness_m" in m.provenance["provisional_parameters"]
    assert m.provenance["config"]["slice"]["target_height_m"] == 1.30
    assert m.provenance["measurement_convention"]["nominal_height_m"] == 1.30


def test_selection_is_marked_as_a_recommendation_by_default(measured):
    results, _, _ = measured
    for m in results.values():
        assert m.selection_is_recommendation is True
        assert m.review_state == "PENDING"


def test_measurement_is_reproducible(scene):
    """The same input and config must give bit-identical diameters."""
    path, truth, _ = scene
    cfg = RunConfig()
    src = LasSource(path, chunk_size=500_000)
    grid = build_ground_grid(src.iter_xyz(), cell_m=cfg.ground.cell_m,
                            bounds=(src.info.mins[0], src.info.maxs[0],
                                    src.info.mins[1], src.info.maxs[1]))
    xyz = src.crop_cylinder(truth["upright"]["xy"], 3.0)
    a = measure_tree(xyz, grid, "upright", truth["upright"]["xy"], cfg, roi_radius_m=1.0)
    b = measure_tree(xyz, grid, "upright", truth["upright"]["xy"], cfg, roi_radius_m=1.0)
    assert a.dbh_m == b.dbh_m
    assert [f.diameter_m for f in a.candidate_results] == \
           [f.diameter_m for f in b.candidate_results]


def test_cli_measure_writes_csv_json_and_plots(scene, tmp_path):
    path, truth, _ = scene
    outdir = tmp_path / "cli_out"
    x, y = truth["upright"]["xy"]
    rc = main(["measure", str(path), "--at", str(x), str(y), "--tree-id", "upright",
               "--roi", "3", "--outdir", str(outdir)])
    assert rc == 0
    csv_path = outdir / "measurements.csv"
    json_path = outdir / "measurements.json"
    assert csv_path.exists() and json_path.exists()
    assert (outdir / "run_config.yaml").exists()
    assert (outdir / "upright_review.png").exists()
    assert (outdir / "upright_ground.png").exists()

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    for col in ("dbh_cm", "status", "coverage_fraction", "calibration_status",
                "dbh_stem_normal_cm", "radial_anomaly_verdict"):
        assert col in header

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["n_trees"] == 1
    rec = payload["measurements"][0]
    # The complete record keeps every model at every height in both geometries.
    assert set(rec["provenance"]["all_fits"]) == {"horizontal", "stem_normal"}
    assert "1.30" in rec["provenance"]["all_fits"]["horizontal"]
    assert len(rec["provenance"]["all_fits"]["horizontal"]) >= 5


def test_cli_inspect_and_config(scene, capsys):
    path, _, _ = scene
    assert main(["inspect", str(path)]) == 0
    assert "point_count" in capsys.readouterr().out
    assert main(["config"]) == 0
    assert "target_height_m" in capsys.readouterr().out


# ---------------------------------------------------------------- M5 harness --
# These use the synthetic scene, where the true diameters are known by
# construction. That validates the benchmark machinery; it is NOT a field
# accuracy result, and the reference rows below are simulated, not measured.

def _reference_csv(tmp_path, truth, method="unknown", split="dev"):
    p = tmp_path / "reference_trees.csv"
    lines = ["tree_id,field_dbh_cm,measurement_height_m,measurement_method,"
             "shape_class,buttressed,leaning,split,x,y,notes"]
    for tid, t in truth.items():
        shape = "fluted" if tid == "fluted" else "regular"
        lean = "true" if t.get("tilt_deg", 0) > 5 else "false"
        lines.append(f"{tid},{t['diameter_m'] * 100:.2f},1.30,{method},{shape},"
                     f"false,{lean},{split},{t['xy'][0]},{t['xy'][1]},synthetic")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_benchmark_scores_against_reference_and_discloses_review_rate(measured, tmp_path):
    from dbh_tool.evaluation.benchmark import compare_to_reference, load_reference_table
    results, truth, _ = measured
    ref = load_reference_table(_reference_csv(tmp_path, truth))
    rep = compare_to_reference(list(results.values()), ref)

    assert rep.n_reference == len(truth)
    assert rep.n_matched == len(truth)
    assert rep.n_reported + rep.n_refused == rep.n_matched
    # The one-sided stem must be refused, so the review rate cannot be zero.
    assert rep.n_refused >= 1
    assert 0.0 < rep.review_rate <= 1.0
    text = rep.summary()
    assert "review rate" in text
    assert "not errors of zero" in text
    # Accuracy on the stems that did report should be good on synthetic data.
    assert rep.overall["mae_cm"] < 3.0
    assert rep.by_size_class
    per = {r["tree_id"]: r for r in rep.per_tree}
    assert per["one_sided"]["reported"] is False
    assert per["one_sided"]["error_cm"] is None
    assert abs(per["upright"]["error_cm"]) < 1.5


def test_benchmark_uses_the_convex_perimeter_for_a_tape_reference(measured, tmp_path):
    """DEC-009 wired end to end: a tape reference must not be scored on area."""
    from dbh_tool.evaluation.benchmark import compare_to_reference, load_reference_table
    results, truth, _ = measured
    ref = load_reference_table(_reference_csv(tmp_path, truth, method="tape"))
    rep = compare_to_reference(list(results.values()), ref)
    comparators = {r["tree_id"]: r["comparator"] for r in rep.per_tree}
    assert "convex_perimeter" in comparators["fluted"]
    assert any("convex_perimeter" in k for k in rep.comparator_counts)


def test_benchmark_split_filtering_end_to_end(measured, tmp_path):
    from dbh_tool.evaluation.benchmark import compare_to_reference, load_reference_table
    results, truth, _ = measured
    ref = load_reference_table(_reference_csv(tmp_path, truth, split="holdout"))
    assert compare_to_reference(list(results.values()), ref, split="dev").n_matched == 0
    assert compare_to_reference(list(results.values()), ref,
                                split="holdout").n_matched == len(truth)


def test_cli_benchmark_runs_and_writes_reports(scene, tmp_path):
    path, truth, _ = scene
    ref = _reference_csv(tmp_path, truth)
    outdir = tmp_path / "bench_out"
    rc = main(["benchmark", str(path), "--reference", str(ref), "--roi", "3",
               "--outdir", str(outdir)])
    assert rc == 0
    assert (outdir / "benchmark_all.json").exists()
    assert (outdir / "benchmark_dev.json").exists()
    assert (outdir / "benchmark_measurements.csv").exists()
    payload = json.loads((outdir / "benchmark_all.json").read_text(encoding="utf-8"))
    assert payload["n_matched"] == len(truth)
    assert payload["review_rate"] is not None
    assert "per_tree" in payload


def test_cli_experiment_runs_a_sweep_and_the_free_experiments(scene, tmp_path):
    path, truth, _ = scene
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps(
        [{"tree_id": tid, "x": t["xy"][0], "y": t["xy"][1]}
         for tid, t in truth.items() if tid in ("upright", "leaning")]),
        encoding="utf-8")
    outdir = tmp_path / "exp_out"
    rc = main(["experiment", str(path), "--targets", str(targets),
               "-e", "geometry", "-e", "slice_thickness", "--roi", "3",
               "--outdir", str(outdir)])
    assert rc == 0
    payload = json.loads((outdir / "experiments.json").read_text(encoding="utf-8"))
    assert "geometry" in payload and "slice_thickness" in payload
    sweep = payload["slice_thickness"]
    assert sweep["param"] == "slice.thickness_m"
    assert len(sweep["per_variant"]) == 4
    assert sweep["reference_available"] is False
    assert sweep["sensitivity"]["n_trees"] == 2
    # The leaning stem's horizontal section should exceed its stem-normal one.
    rows = {r["tree_id"]: r for r in payload["geometry"]["per_tree"]}
    assert rows["leaning"]["difference_cm"] > 0


def test_crop_many_matches_individual_crops(scene):
    """The single-pass crop must return exactly what per-tree crops return."""
    path, truth, _ = scene
    src = LasSource(path, chunk_size=400_000)
    centers = [t["xy"] for t in truth.values()]
    many = src.crop_many(centers, 2.0)
    for c, got in zip(centers, many):
        one = src.crop_cylinder(c, 2.0)
        assert len(got) == len(one)
        assert np.allclose(np.sort(got, axis=0), np.sort(one, axis=0))
