"""The scientific decision layer: status, confidence band, and recommendation.

Design position, and a small refinement of the original plan. Docs 03 phase 1 asks
for "no automatic winner, require manual selection". Taken literally that makes
every tree ``REVIEW_REQUIRED``, which destroys the signal that the status field is
supposed to carry. So three concerns are separated instead:

``status``
    the scientific verdict on the evidence: what the data supports.
``review_state``
    the human workflow state, always ``PENDING`` until a person acts.
``selected_model`` + ``selection_is_recommendation``
    the preferred interpretation, explicitly flagged as a recommendation whenever
    ``decision.automatic_selection`` is false.

So nothing is ever silently auto-accepted, while the status still says what the
tool actually concluded. Confidence is reported as a qualitative band, never as a
percentage: no calibration set exists yet, so a numeric probability would be
false precision (docs 02, section 16).

Every threshold used here comes from :class:`~dbh_tool.config.DecisionConfig` and
is listed in ``PROVISIONAL_PARAMETERS``. Hard failures (coverage, insufficient
data, invalid measurement height) are rules; everything else contributes to a
band.
"""
from __future__ import annotations

import numpy as np

STATUS_ACCEPTED_CIRCULAR = "ACCEPTED_CIRCULAR"
STATUS_ACCEPTED_ELLIPTICAL = "ACCEPTED_ELLIPTICAL"
STATUS_ACCEPTED_IRREGULAR = "ACCEPTED_IRREGULAR"
STATUS_REVIEW = "REVIEW_REQUIRED"
STATUS_INVALID_HEIGHT = "INVALID_MEASUREMENT_HEIGHT"
STATUS_FAILED = "FAILED_INSUFFICIENT_DATA"

BAND_HIGH = "HIGH"
BAND_MEDIUM = "MEDIUM"
BAND_LOW = "LOW"
BAND_REVIEW = "REVIEW_REQUIRED"
BAND_FAILED = "FAILED"


def assess(fits: dict, comparison: dict, ellipticity: dict, profiles_by_model: dict,
           local_ground, axis, cfg, coverage_cfg, n_points: int,
           min_points: int, anomaly: dict | None = None) -> dict:
    """Produce status, confidence band, recommended model and the reasons for them.

    ``fits`` maps model name to :class:`FitResult` for the primary section.
    ``profiles_by_model`` maps model name to its multi-height profile; the profile
    judged is the one belonging to the model actually being recommended, so height
    stability is assessed for the answer being reported rather than for a fixed
    reference model.

    ``anomaly`` is the radial-anomaly classification. It matters because an
    irregular-stem verdict and a contaminated-section verdict have the same
    residual signature, and only one of them justifies reporting an outline-derived
    diameter.
    """
    anomaly = anomaly or {}
    reasons: list[str] = []
    hard_failures: list[str] = []

    # ---- hard failures: no number should be emitted at all ------------------
    if n_points < min_points:
        hard_failures.append(f"only_{n_points}_points_in_section")
    valid = {k: f for k, f in fits.items() if f is not None and f.valid}
    if not valid:
        hard_failures.append("no_model_produced_a_valid_fit")
    recommended = _recommend(valid, ellipticity, cfg, anomaly)
    profile = profiles_by_model.get(recommended or "", {}) or {}

    # Coverage is judged on the best-covered valid model: if no model saw enough
    # of the circumference, the diameter is not constrained by the data.
    best_cov = max((f.angular_coverage or 0.0) for f in valid.values()) if valid else 0.0
    worst_gap = min((f.largest_gap_deg if f.largest_gap_deg is not None else 360.0)
                    for f in valid.values()) if valid else 360.0
    if valid and best_cov < coverage_cfg.min_coverage_fraction:
        hard_failures.append(
            f"angular_coverage_{best_cov:.0%}_below_{coverage_cfg.min_coverage_fraction:.0%}")
    if valid and worst_gap > coverage_cfg.max_gap_deg:
        hard_failures.append(
            f"largest_angular_gap_{worst_gap:.0f}deg_above_{coverage_cfg.max_gap_deg:.0f}deg")

    if hard_failures and not valid:
        return _result(STATUS_FAILED, BAND_FAILED, None, hard_failures, cfg,
                       report_diameter=False)

    # Data adequacy is judged before measurement-height validity, and the order
    # matters. A deformity verdict is a claim about the *tree*, and it can only be
    # supported by diameters that are themselves trustworthy at several heights.
    # Checking it first labelled contaminated vegetation clumps
    # INVALID_MEASUREMENT_HEIGHT, which asserts something specific about a stem
    # whose data had already been rejected.
    if hard_failures:
        # A hard failure means the diameter is not constrained by the observed
        # geometry, so no headline number is emitted. The recommended model is
        # still named, and every candidate fit is still exported, so a reviewer can
        # see what the data would have implied.
        return _result(STATUS_REVIEW, BAND_REVIEW, recommended, hard_failures, cfg,
                       report_diameter=False)

    # ---- measurement height validity ---------------------------------------
    # A buttress or deformity through breast height invalidates the conventional
    # measurement location: the tool must not invent a DBH for it.
    height_problems = [w for w in profile.get("warnings", [])
                       if "buttress" in w or "deformity" in w]
    if height_problems and profile.get("n_heights", 0) >= 3:
        return _result(STATUS_INVALID_HEIGHT, BAND_REVIEW, None,
                       height_problems + reasons, cfg, report_diameter=False)
    if height_problems:
        reasons.append("deformity_suspected_but_too_few_heights_to_conclude")

    # ---- soft quality signals ----------------------------------------------
    penalties = 0
    disagreement = comparison.get("max_pairwise_difference_m")
    if disagreement is not None and disagreement > cfg.max_model_disagreement_m:
        penalties += 1
        reasons.append(
            f"models_disagree_by_{disagreement * 100:.1f}cm"
            f"_above_{cfg.max_model_disagreement_m * 100:.1f}cm")
    if comparison.get("n_models_compared", 0) < 2:
        penalties += 1
        reasons.append("fewer_than_two_models_available_for_comparison")

    cross_std = profile.get("std_m")
    if cross_std is not None and cross_std > cfg.max_cross_height_std_m:
        penalties += 1
        reasons.append(
            f"cross_height_std_{cross_std * 100:.1f}cm"
            f"_above_{cfg.max_cross_height_std_m * 100:.1f}cm")
    if profile.get("anomalous_heights_m"):
        penalties += 1
        reasons.append(f"anomalous_heights_{profile['anomalous_heights_m']}")

    boots = [f.bootstrap_std_m for f in valid.values() if f.bootstrap_std_m is not None]
    if boots and min(boots) > cfg.max_bootstrap_std_m:
        penalties += 1
        reasons.append(
            f"bootstrap_std_{min(boots) * 100:.1f}cm"
            f"_above_{cfg.max_bootstrap_std_m * 100:.1f}cm")

    rmses = [f.rmse_m for f in valid.values() if f.rmse_m is not None and np.isfinite(f.rmse_m)]
    if rmses and min(rmses) > cfg.max_rmse_m:
        penalties += 1
        reasons.append(f"best_rmse_{min(rmses) * 100:.1f}cm_above_{cfg.max_rmse_m * 100:.1f}cm")

    if best_cov < 0.85:
        penalties += 1
        reasons.append(f"partial_angular_coverage_{best_cov:.0%}")

    if local_ground is not None and getattr(local_ground, "quality", "") != "GOOD":
        penalties += 1
        reasons.append(f"local_ground_quality_{getattr(local_ground, 'quality', 'UNKNOWN')}")
        reasons.extend(f"ground:{w}" for w in getattr(local_ground, "warnings", []))

    if axis is not None and getattr(axis, "valid", False) and axis.tilt_deg > 10.0:
        reasons.append(f"stem_leans_{axis.tilt_deg:.0f}deg")

    anomaly_verdict = anomaly.get("verdict", "AMBIGUOUS")
    if anomaly_verdict == "CONTAMINATION_SUSPECTED":
        penalties += 1
        reasons.append(
            f"contamination_suspected_outliers_{anomaly.get('outlier_fraction', 0):.0%}"
            f"_in_{anomaly.get('outlier_angular_coverage', 0):.0%}_of_circumference")
    shift = comparison.get("outline_cleaning_shift_m")
    if shift is not None and shift > cfg.max_model_disagreement_m:
        reasons.append(f"robust_cleaning_moves_outline_by_{shift * 100:.1f}cm")

    # ---- shape verdict, tied to the model actually being recommended --------
    # Status and reported model must agree. Deriving the status from shape evidence
    # independently of the recommendation produced contradictions such as
    # "ACCEPTED_IRREGULAR" beside a reported circle diameter.
    verdict = ellipticity.get("verdict", "INCONCLUSIVE")
    outline = fits.get("outline_radial_median")
    if anomaly_verdict == "CONTAMINATION_SUSPECTED":
        # The ellipse and the outline are both fitted to all section points, so
        # under contamination their shape evidence describes the contaminant as
        # much as the stem. The class is left unresolved and a robust diameter is
        # offered for review instead.
        status = STATUS_REVIEW
        reasons.append("shape_class_unresolved_under_contamination")
    elif recommended == "outline_radial_median":
        status = STATUS_ACCEPTED_IRREGULAR
        deficit = (outline.extra.get("convexity_deficit")
                   if outline is not None else None)
        reasons.append("outline_preferred_convexity_deficit_"
                       + ("unknown" if deficit is None else f"{deficit:.3f}"))
    elif recommended == "ellipse":
        status = STATUS_ACCEPTED_ELLIPTICAL
    elif verdict in ("GENUINELY_OVAL", "OVAL_BEYOND_LEAN"):
        # The section looks oval but a circle model is being recommended. That
        # combination is for a person to settle, not for the tool to paper over.
        status = STATUS_REVIEW
        reasons.append("section_appears_oval_but_a_circle_model_is_preferred")
    elif verdict in ("CIRCULAR", "LEAN_EXPLAINS_ELLIPTICITY"):
        status = STATUS_ACCEPTED_CIRCULAR
    else:
        status = STATUS_REVIEW
        reasons.append(f"shape_attribution_{verdict.lower()}")

    # A non-convex outline that could not be attributed to either shape or
    # contamination is unresolved evidence, not an answer.
    if (outline is not None and outline.valid and _looks_irregular(outline, cfg)
            and anomaly_verdict not in ("IRREGULAR_SHAPE", "CONTAMINATION_SUSPECTED")):
        status = STATUS_REVIEW
        reasons.append("non_convex_outline_but_shape_versus_contamination_unresolved")

    band = BAND_HIGH if penalties == 0 else BAND_MEDIUM if penalties == 1 else BAND_LOW
    if penalties >= 4:
        status = STATUS_REVIEW
        band = BAND_REVIEW
    return _result(status, band, recommended, reasons, cfg,
                   penalties=penalties, coverage=best_cov, largest_gap_deg=worst_gap)


def _looks_irregular(outline, cfg) -> bool:
    """Whether an outline is measurably non-convex and rough for its own size."""
    deficit = outline.extra.get("convexity_deficit")
    rough = outline.extra.get("radial_roughness_m")
    radius = 0.5 * (outline.diameter_m or 0.0)
    return bool(deficit is not None and deficit > cfg.irregular_convexity_deficit_min
                and radius > 0 and rough is not None
                and rough / radius > cfg.irregular_roughness_ratio_min)


def _recommend(valid: dict, ellipticity: dict, cfg, anomaly: dict | None = None) -> str | None:
    """Pick the most defensible interpretation among the valid models.

    Order of preference is deliberately conservative: the simplest model that the
    evidence does not contradict wins. A more complex model has to earn its extra
    degrees of freedom, since on partial or noisy data extra freedom mostly buys
    overfitting.
    """
    if not valid:
        return None
    anomaly = anomaly or {}
    verdict = ellipticity.get("verdict", "INCONCLUSIVE")
    # Contamination first: when the anomalous points look like an attached liana or
    # branch, the robust circle is the only model that ignores them. An outline or
    # ordinary least-squares fit would trace the contaminant and report it as stem.
    if anomaly.get("verdict") == "CONTAMINATION_SUSPECTED" and "circle_ransac" in valid:
        return "circle_ransac"
    # Note: no test on the ellipse verdict here. A symmetric fluted stem has an
    # almost circular best-fit ellipse, so requiring a non-circular ellipse would
    # rule the outline out for exactly the shape it exists to describe.
    if ("outline_radial_median" in valid
            and anomaly.get("verdict") == "IRREGULAR_SHAPE"
            and _looks_irregular(valid["outline_radial_median"], cfg)):
        return "outline_radial_median"
    if verdict in ("GENUINELY_OVAL", "OVAL_BEYOND_LEAN") and "ellipse" in valid:
        return "ellipse"
    for name in ("circle_ransac", "circle_geometric", "circle_pratt", "circle_taubin",
                 "ellipse", "outline_radial_median", "circle_algebraic"):
        if name in valid:
            return name
    return sorted(valid)[0]


def _result(status: str, band: str, recommended: str | None, reasons: list[str], cfg,
            report_diameter: bool = True, **extra) -> dict:
    """Assemble the verdict.

    ``report_diameter`` distinguishes the two kinds of review. A soft flag means
    "here is a number, please check it"; a hard failure means "the data does not
    support a number", and forcing one out anyway is exactly the silent-wrong-answer
    behaviour the tool exists to avoid.
    """
    return {
        "status": status,
        "confidence_band": band,
        "recommended_model": recommended,
        "report_diameter": bool(report_diameter),
        "selection_is_recommendation": not bool(getattr(cfg, "automatic_selection", False)),
        "review_state": "PENDING",
        "reasons": list(dict.fromkeys(reasons)),
        **extra,
    }
