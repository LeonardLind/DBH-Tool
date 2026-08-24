"""Measurement orchestration: from a stem location to a TreeMeasurement.

This is the only module that knows the order of the pipeline. Each stage is a
plain function elsewhere, taking and returning explicit data, so the whole
measurement runs headless and is testable without a UI.

The deliberate product rule from the handover is enforced here: for every stem,
every eligible model is fitted at every requested height in both section
geometries, and *then* the evidence is judged. No geometry is chosen early.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import __version__
from .config import MEASUREMENT_CONVENTION, PROVISIONAL_PARAMETERS, RunConfig
from .evaluation.bootstrap import bootstrap_diameter
from .evaluation.compare import (
    attribute_ellipticity, classify_radial_anomaly, compare_diameters, lean_bias_estimate,
)
from .evaluation.confidence import assess
from .evaluation.coverage import attach_coverage
from .evaluation.profile import cross_height_agreement, diameter_profile
from .fitting.circle import (
    fit_circle_algebraic, fit_circle_geometric, fit_circle_pratt, fit_circle_taubin,
)
from .fitting.common import FitResult, jsonable
from .fitting.ellipse import fit_ellipse
from .fitting.outline import fit_outline_radial_median
from .fitting.ransac_circle import fit_circle_ransac
from .ground.local_plane import LocalGround, local_ground_at
from .ground.normalize import height_above_ground
from .stems.axis import estimate_stem_axis, vertical_axis
from .stems.slices import horizontal_section, remove_isolated, stem_normal_section

# Models fitted at every height. Order is display order in reports.
CIRCLE_MODELS = ("circle_algebraic", "circle_taubin", "circle_pratt", "circle_geometric")

# Fitted and exported, but kept out of the cross-model disagreement statistic: it
# answers "how much does robust cleaning move the outline?", not "which geometric
# interpretation is right?".
DIAGNOSTIC_ONLY_MODELS = ("outline_radial_median_inliers",)


@dataclass
class TreeMeasurement:
    """Everything known about one tree, including every candidate model."""

    tree_id: str
    dbh_m: float | None
    status: str
    confidence_band: str
    selected_model: str | None
    recommended_model: str | None
    selection_is_recommendation: bool
    review_state: str
    measurement_height_m: float
    primary_geometry: str
    dbh_source: str
    local_ground_z_m: float
    center_xy: tuple[float, float]
    dbh_single_slice_m: float | None = None
    dbh_profile_median_m: float | None = None
    dbh_taper_interpolated_m: float | None = None
    dbh_stem_normal_m: float | None = None
    candidate_results: list[FitResult] = field(default_factory=list)
    local_ground: LocalGround | None = None
    axis: Any = None
    comparison: dict = field(default_factory=dict)
    ellipticity: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def to_dict(self, include_points: bool = False) -> dict:
        d = {
            "tree_id": self.tree_id,
            "dbh_m": self.dbh_m,
            "dbh_cm": None if self.dbh_m is None else round(self.dbh_m * 100.0, 2),
            "status": self.status,
            "confidence_band": self.confidence_band,
            "selected_model": self.selected_model,
            "recommended_model": self.recommended_model,
            "selection_is_recommendation": self.selection_is_recommendation,
            "review_state": self.review_state,
            "measurement_height_m": self.measurement_height_m,
            "primary_geometry": self.primary_geometry,
            "dbh_source": self.dbh_source,
            "local_ground_z_m": self.local_ground_z_m,
            "center_xy": [float(v) for v in self.center_xy],
            "dbh_variants_m": {
                "single_slice": self.dbh_single_slice_m,
                "profile_median": self.dbh_profile_median_m,
                "taper_interpolated": self.dbh_taper_interpolated_m,
                "stem_normal_single_slice": self.dbh_stem_normal_m,
            },
            "local_ground": None if self.local_ground is None else self.local_ground.to_dict(),
            "axis": None if self.axis is None else self.axis.to_dict(),
            "comparison": jsonable(self.comparison),
            "ellipticity": jsonable(self.ellipticity),
            "profiles": jsonable(self.profiles),
            "sections": jsonable(self.sections),
            "candidate_results": [_strip_arrays(f.to_dict(), include_points)
                                  for f in self.candidate_results],
            "reasons": self.reasons,
            "warnings": self.warnings,
            "provenance": jsonable(self.provenance),
        }
        return d


def _strip_arrays(d: dict, keep: bool) -> dict:
    """Drop bulky per-point arrays from an exported fit unless asked to keep them."""
    if keep:
        return d
    extra = dict(d.get("extra", {}))
    for k in ("outline_xy", "convex_hull_xy", "sector_radius_m", "sector_occupied",
              "sector_theta_rad", "conic_coeffs_normalised", "inlier_mask"):
        extra.pop(k, None)
    d["extra"] = extra
    return d


def fit_all_models(xy: np.ndarray, cfg: RunConfig, bootstrap: bool = False) -> dict:
    """Fit every eligible model to one cross-section and attach diagnostics.

    Returns a dict of model name to :class:`FitResult`. Models that fail are kept
    with ``valid=False`` and their reason, because a missing model and a rejected
    model are different pieces of evidence.
    """
    fits: dict[str, FitResult] = {}
    fits["circle_algebraic"] = fit_circle_algebraic(xy)
    fits["circle_taubin"] = fit_circle_taubin(xy)
    fits["circle_pratt"] = fit_circle_pratt(xy)
    fits["circle_geometric"] = fit_circle_geometric(xy)
    fits["ellipse"] = fit_ellipse(
        xy, max_axis_ratio=3.0)
    rc = cfg.ransac_circle
    fits["circle_ransac"] = fit_circle_ransac(
        xy, residual_threshold_m=rc.residual_threshold_m, max_trials=rc.max_trials,
        min_inlier_fraction=rc.min_inlier_fraction, seed=rc.random_seed,
        max_scoring_points=rc.max_scoring_points, refit=rc.refit)

    # The outline needs a centre from a prior consensus fit; prefer the robust one.
    center = None
    for name in ("circle_ransac", "circle_geometric", "circle_taubin", "ellipse"):
        f = fits.get(name)
        if f is not None and f.valid and f.center_xy is not None:
            center = f.center_xy
            break
    if center is not None:
        oc = cfg.outline
        fits["outline_radial_median"] = fit_outline_radial_median(
            xy, center, n_sectors=oc.n_sectors,
            min_points_per_sector=oc.min_points_per_sector,
            max_bridge_gap_deg=oc.max_bridge_gap_deg,
            min_occupied_fraction=oc.min_occupied_fraction)
        # The same outline restricted to the RANSAC inliers. Comparing the two
        # separates stem shape from attached contamination: a genuine flute
        # survives cleaning, a liana does not. Fitted only when RANSAC produced a
        # usable inlier set.
        rf = fits.get("circle_ransac")
        mask = rf.extra.get("inlier_mask") if rf is not None else None
        if mask is not None and int(np.count_nonzero(mask)) >= 8 * oc.min_points_per_sector:
            inl = fit_outline_radial_median(
                xy[mask], rf.center_xy, n_sectors=oc.n_sectors,
                min_points_per_sector=oc.min_points_per_sector,
                max_bridge_gap_deg=oc.max_bridge_gap_deg,
                min_occupied_fraction=oc.min_occupied_fraction)
            inl.model = "outline_radial_median_inliers"
            inl.diameter_definition += " (RANSAC inliers only)"
            fits["outline_radial_median_inliers"] = inl

    cc = cfg.coverage
    for f in fits.values():
        attach_coverage(f, xy, bin_deg=cc.angular_bin_deg,
                        min_points_per_bin=cc.min_points_per_bin)

    if bootstrap and cfg.bootstrap.enabled:
        bc = cfg.bootstrap
        fitters = {
            "circle_geometric": fit_circle_geometric,
            "circle_pratt": fit_circle_pratt,
            "ellipse": fit_ellipse,
            "circle_ransac": lambda p: fit_circle_ransac(
                p, residual_threshold_m=rc.residual_threshold_m,
                max_trials=max(60, rc.max_trials // 5),
                min_inlier_fraction=rc.min_inlier_fraction, seed=rc.random_seed,
                max_scoring_points=rc.max_scoring_points, refit=rc.refit),
        }
        for name, fn in fitters.items():
            f = fits.get(name)
            if f is None or not f.valid:
                continue
            b = bootstrap_diameter(xy, fn, n_resamples=bc.n_resamples, seed=bc.random_seed,
                                   sample_fraction=bc.sample_fraction)
            f.bootstrap_std_m = b["std_m"] if np.isfinite(b["std_m"]) else None
            f.extra["bootstrap"] = b
    return fits


def _seed_center(xyz: np.ndarray, height_m: np.ndarray, cfg: RunConfig, center_xy,
                 roi_radius_m: float) -> tuple[tuple[float, float], float, list[str]]:
    """Locate the stem inside a generous slice and return its centre and radius.

    Seeding has to be robust, not accurate. A breast-height slice taken over a
    metre-scale radius in dense forest is mostly *not* the stem: it is understory,
    lianas, branches and neighbouring trunks. A least-squares circle on that
    mixture is meaningless, and every later stage inherits the error, so the seed
    is found with RANSAC, whose whole purpose is to pick out the dominant circular
    structure in contaminated data.

    Validity thresholds are deliberately relaxed here: a seed circle only has to
    be geometrically plausible and near the requested location. Judging quality is
    the job of the final fits on the tightened section.
    """
    warnings: list[str] = []
    requested = (float(center_xy[0]), float(center_xy[1]))
    c = requested
    rc = cfg.ransac_circle
    radius = None
    search = roi_radius_m
    for i in range(3):
        sec = horizontal_section("seed", xyz, height_m, cfg.slice.target_height_m,
                                 cfg.slice.thickness_m, c, search, 0.0)
        if sec.source_point_count < cfg.slice.min_points:
            warnings.append(f"seed_pass_{i}_only_{sec.source_point_count}_points")
            break
        fit = fit_circle_ransac(
            sec.points_xy, residual_threshold_m=rc.residual_threshold_m,
            max_trials=rc.max_trials, min_inlier_fraction=0.0, seed=rc.random_seed,
            max_scoring_points=rc.max_scoring_points, refit=rc.refit)
        if fit.center_xy is None or fit.extra.get("radius_m") is None:
            warnings.append(f"seed_pass_{i}_ransac_found_no_circle")
            break
        c = (sec.origin_xy[0] + fit.center_xy[0], sec.origin_xy[1] + fit.center_xy[1])
        radius = float(fit.extra["radius_m"])
        # Tighten the search around the stem found so far, leaving room for bark
        # irregularity and flutes.
        search = min(roi_radius_m, cfg.slice.section_radius_factor * radius + 0.03)

    if radius is None:
        # Fall back to a non-robust fit so the caller still gets a usable scale.
        sec = horizontal_section("seed", xyz, height_m, cfg.slice.target_height_m,
                                 cfg.slice.thickness_m, requested, roi_radius_m, 0.0)
        fit = fit_circle_taubin(sec.points_xy) if sec.source_point_count >= 3 else None
        if fit is not None and fit.center_xy is not None:
            c = (sec.origin_xy[0] + fit.center_xy[0], sec.origin_xy[1] + fit.center_xy[1])
            radius = float(fit.extra["radius_m"])
            warnings.append("seed_fell_back_to_non_robust_fit")
        else:
            radius = 0.15
            c = requested
            warnings.append("seed_failed_using_default_radius")

    drift = float(np.hypot(c[0] - requested[0], c[1] - requested[1]))
    if drift > max(0.5 * roi_radius_m, 2.0 * radius):
        # The seed may have locked onto a neighbouring stem rather than the one
        # the operator pointed at. Say so instead of quietly measuring the wrong
        # tree.
        warnings.append(f"seed_drifted_{drift:.2f}m_from_requested_location")
    return c, radius, warnings


def measure_tree(xyz: np.ndarray, grid, tree_id: str, center_xy, cfg: RunConfig | None = None,
                 roi_radius_m: float = 1.0, source_info: dict | None = None) -> TreeMeasurement:
    """Measure one tree from a local point crop and a ground surface.

    ``xyz``    points around the tree, world coordinates (a generous crop).
    ``grid``   a :class:`~dbh_tool.ground.dtm.GroundGrid` covering the crop.
    ``center_xy`` an approximate stem location, from an operator or a detector.
    """
    cfg = cfg or RunConfig()
    xyz = np.asarray(xyz, dtype=float)
    warnings: list[str] = []

    # --- ground at this stem ------------------------------------------------
    # Every height used for this stem is measured from ONE scalar datum: the local
    # ground plane evaluated at the stem centre. Per-point height above the ground
    # raster is right for detection over a whole plot, but using it to cut a
    # cross-section tilts the cut plane with the terrain (see slices.py, DEC-010).
    gcfg = cfg.ground
    center_xy = (float(center_xy[0]), float(center_xy[1]))

    def ground_at(cx_, cy_):
        return local_ground_at(grid, cx_, cy_, radius_m=gcfg.local_plane_radius_m,
                               min_points=gcfg.local_plane_min_points,
                               max_slope_deg=gcfg.max_local_slope_deg,
                               max_roughness_m=gcfg.max_ground_roughness_m)

    lg = ground_at(center_xy[0], center_xy[1])

    # --- locate the stem and get a working radius --------------------------
    center, radius, seed_warn = _seed_center(
        xyz, xyz[:, 2] - lg.z_m, cfg, center_xy, roi_radius_m)
    warnings.extend(seed_warn)
    # Re-estimate the datum at the refined stem centre: on a slope, a metre of
    # horizontal shift is a real change in ground elevation.
    if np.hypot(center[0] - center_xy[0], center[1] - center_xy[1]) > 0.5 * gcfg.cell_m:
        lg = ground_at(center[0], center[1])
    height = xyz[:, 2] - lg.z_m
    max_radius = max(0.10, min(roi_radius_m,
                               cfg.slice.section_radius_factor * radius + 0.03))

    # --- stem axis ---------------------------------------------------------
    acfg = cfg.axis
    if acfg.enabled:
        axis = estimate_stem_axis(
            xyz, height, center, radius, lower_hag_m=acfg.lower_height_m,
            upper_hag_m=acfg.upper_height_m, n_bins=acfg.n_bins,
            min_points_per_bin=acfg.min_points_per_bin,
            reference_hag_m=cfg.slice.target_height_m, max_tilt_deg=acfg.max_tilt_deg)
        if not axis.valid:
            warnings.append("axis_estimation_failed_using_vertical")
            axis = vertical_axis(center, lg.z_m, cfg.slice.target_height_m)
    else:
        axis = vertical_axis(center, lg.z_m, cfg.slice.target_height_m)

    # --- sections, all heights, both geometries ---------------------------
    heights = sorted(set([cfg.slice.target_height_m] + list(cfg.slice.supporting_heights_m)))
    geometries = (["horizontal", "stem_normal"] if cfg.slice.geometry == "both"
                  else [cfg.slice.geometry])
    target = cfg.slice.target_height_m

    all_fits: dict[str, dict[float, dict]] = {}
    sections_meta: dict[str, dict[str, dict]] = {}
    for geom in geometries:
        all_fits[geom] = {}
        sections_meta[geom] = {}
        for h in heights:
            if geom == "horizontal":
                sec = horizontal_section(tree_id, xyz, height, h, cfg.slice.thickness_m,
                                         center, max_radius, lg.z_m)
            else:
                sec = stem_normal_section(tree_id, xyz, height, axis, h,
                                          cfg.slice.thickness_m, max_radius, lg.z_m)
            xy = sec.points_xy
            if cfg.preprocess.remove_isolated_points and len(xy) > 10:
                keep = remove_isolated(xy, cfg.preprocess.isolation_radius_m,
                                       cfg.preprocess.isolation_min_neighbours)
                n_dropped = int((~keep).sum())
                xy = xy[keep]
                sec.metadata["isolated_points_removed"] = n_dropped
            if len(xy) < cfg.slice.min_points:
                sections_meta[geom][f"{h:.2f}"] = {
                    **sec.to_meta(), "skipped": "too_few_points",
                    "points_after_cleaning": int(len(xy))}
                continue
            fits = fit_all_models(xy, cfg, bootstrap=(h == target))
            all_fits[geom][h] = fits
            sections_meta[geom][f"{h:.2f}"] = {
                **sec.to_meta(), "points_after_cleaning": int(len(xy))}
            if h == target:
                sections_meta[geom][f"{h:.2f}"]["is_target_height"] = True
                # Keep the cleaned section points for plotting/review.
                sections_meta[geom][f"{h:.2f}"]["_section"] = sec
                sections_meta[geom][f"{h:.2f}"]["_xy"] = xy

    primary_geom = cfg.slice.primary_geometry
    if primary_geom not in all_fits or target not in all_fits.get(primary_geom, {}):
        # Fall back to any geometry that produced a target-height fit.
        for g in geometries:
            if target in all_fits.get(g, {}):
                warnings.append(f"primary_geometry_{primary_geom}_unavailable_using_{g}")
                primary_geom = g
                break

    target_fits = all_fits.get(primary_geom, {}).get(target, {})
    if not target_fits:
        return TreeMeasurement(
            tree_id=tree_id, dbh_m=None, status="FAILED_INSUFFICIENT_DATA",
            confidence_band="FAILED", selected_model=None, recommended_model=None,
            selection_is_recommendation=True, review_state="PENDING",
            measurement_height_m=target, primary_geometry=primary_geom,
            dbh_source=cfg.decision.primary_dbh_source, local_ground_z_m=lg.z_m,
            center_xy=center, local_ground=lg, axis=axis,
            sections=_public_sections(sections_meta),
            warnings=warnings + ["no_section_at_target_height"],
            reasons=["no_section_at_target_height"],
            provenance=_provenance(cfg, source_info))

    # --- profiles per model, per geometry ---------------------------------
    profiles: dict[str, dict[str, dict]] = {}
    for geom, by_h in all_fits.items():
        profiles[geom] = {}
        model_names = sorted({m for f in by_h.values() for m in f})
        for m in model_names:
            hs, ds = [], []
            for h in sorted(by_h):
                f = by_h[h].get(m)
                hs.append(h)
                ds.append(f.diameter_m if (f is not None and f.valid) else None)
            profiles[geom][m] = diameter_profile(
                hs, ds, target_height_m=target,
                buttress_taper_threshold_per_m=cfg.decision.buttress_taper_threshold_per_m)

    # --- comparison and shape attribution at the target height ------------
    comparison = compare_diameters(target_fits, exclude=DIAGNOSTIC_ONLY_MODELS)
    dcfg = cfg.decision
    target_xy = (sections_meta.get(primary_geom, {})
                 .get(f"{target:.2f}", {}).get("_xy"))
    anomaly = classify_radial_anomaly(
        target_xy if target_xy is not None else np.empty((0, 2)),
        target_fits.get("circle_ransac"), target_fits.get("outline_radial_median"),
        outlier_fraction_min=dcfg.contamination_outlier_fraction_min,
        outside_fraction_min=dcfg.contamination_outside_fraction_min,
        angular_coverage_max=dcfg.contamination_angular_coverage_max,
        min_lobes=dcfg.contamination_min_lobes,
        sector_spread_max_m=dcfg.contamination_sector_spread_m,
        thick_sector_fraction_max=dcfg.contamination_thick_sector_fraction_max,
        radial_excess_max_m=dcfg.contamination_radial_excess_m,
        n_sectors=cfg.outline.n_sectors)
    comparison["radial_anomaly"] = anomaly
    o_all = target_fits.get("outline_radial_median")
    o_inl = target_fits.get("outline_radial_median_inliers")
    if (o_all is not None and o_inl is not None and o_all.diameter_m is not None
            and o_inl.diameter_m is not None):
        comparison["outline_cleaning_shift_m"] = float(
            abs(o_all.diameter_m - o_inl.diameter_m))
    ellipticity = attribute_ellipticity(
        target_fits.get("ellipse"), axis,
        circular_ratio_max=cfg.decision.ellipse_axis_ratio_circular_max,
        ratio_tolerance=cfg.decision.lean_explains_ellipse_tolerance,
        geometry=primary_geom)
    if axis is not None and axis.valid and comparison.get("median_diameter_m"):
        comparison["lean_bias_estimate"] = lean_bias_estimate(
            comparison["median_diameter_m"], axis.tilt_deg)

    n_points = max((f.point_count for f in target_fits.values()), default=0)
    verdict = assess(target_fits, comparison, ellipticity,
                     profiles.get(primary_geom, {}), lg, axis, cfg.decision,
                     cfg.coverage, n_points, cfg.slice.min_points, anomaly=anomaly)

    recommended = verdict["recommended_model"]
    selected = recommended if cfg.decision.automatic_selection else recommended
    chosen = target_fits.get(selected) if selected else None
    single = float(chosen.diameter_m) if chosen is not None and chosen.valid else None
    prof = profiles.get(primary_geom, {}).get(selected or "", {})
    prof_median = prof.get("median_m")
    prof_interp = prof.get("interpolated_at_target_m")

    source = cfg.decision.primary_dbh_source
    dbh = {"single_slice": single, "profile_median": prof_median,
           "taper_interpolated": prof_interp}.get(source, single)
    if not verdict.get("report_diameter", True):
        # The per-model diameters stay in candidate_results and dbh_variants for
        # review; only the headline value is withheld.
        dbh = None

    sn_fits = all_fits.get("stem_normal", {}).get(target, {})
    sn = sn_fits.get(selected) if selected else None
    dbh_sn = float(sn.diameter_m) if sn is not None and sn.valid else None

    agree = cross_height_agreement(prof, single)
    comparison["cross_height"] = agree
    if selected and chosen is not None and prof.get("std_m") is not None:
        chosen.extra["cross_height_std_m"] = prof["std_m"]
    if dbh_sn is not None and single is not None:
        comparison["horizontal_minus_stem_normal_m"] = float(single - dbh_sn)

    measurement = TreeMeasurement(
        tree_id=tree_id,
        dbh_m=dbh,
        status=verdict["status"],
        confidence_band=verdict["confidence_band"],
        selected_model=selected,
        recommended_model=recommended,
        selection_is_recommendation=verdict["selection_is_recommendation"],
        review_state=verdict["review_state"],
        measurement_height_m=target,
        primary_geometry=primary_geom,
        dbh_source=source,
        local_ground_z_m=lg.z_m,
        center_xy=center,
        dbh_single_slice_m=single,
        dbh_profile_median_m=prof_median,
        dbh_taper_interpolated_m=prof_interp,
        dbh_stem_normal_m=dbh_sn,
        candidate_results=[target_fits[k] for k in sorted(target_fits)],
        local_ground=lg,
        axis=axis,
        comparison=comparison,
        ellipticity=ellipticity,
        profiles=profiles,
        sections=_public_sections(sections_meta),
        reasons=verdict["reasons"],
        warnings=warnings,
        provenance=_provenance(cfg, source_info),
    )
    measurement.provenance["all_fits"] = {
        geom: {f"{h:.2f}": {m: _strip_arrays(f.to_dict(), False)
                            for m, f in fits.items()}
               for h, fits in by_h.items()}
        for geom, by_h in all_fits.items()
    }
    # Keep the live objects for the review plot; not exported.
    measurement._sections_internal = sections_meta  # type: ignore[attr-defined]
    return measurement


def _public_sections(sections_meta: dict) -> dict:
    """Section metadata with the internal live objects removed."""
    return {geom: {h: {k: v for k, v in meta.items() if not k.startswith("_")}
                   for h, meta in by_h.items()}
            for geom, by_h in sections_meta.items()}


def _provenance(cfg: RunConfig, source_info: dict | None) -> dict:
    return {
        "software": "dbh-tool",
        "version": __version__,
        "config": cfg.to_dict(),
        "provisional_parameters": list(PROVISIONAL_PARAMETERS),
        "measurement_convention": MEASUREMENT_CONVENTION,
        "calibration_status": (
            "UNCALIBRATED: thresholds are working defaults, not validated against "
            "field measurements. Confidence bands are qualitative."),
        "source": source_info or {},
    }
