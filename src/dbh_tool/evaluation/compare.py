"""Cross-model comparison and shape attribution.

Two distinct jobs live here.

**Disagreement.** Competing models are compared quantitatively. Small
disagreement is evidence that the answer does not depend on the modelling choice;
large disagreement means geometry matters for this stem and confidence must drop.

**Shape attribution.** An elliptical horizontal section has two very different
causes: a genuinely oval stem, or a circular stem that leans. A horizontal cut
through a cylinder tilted by ``t`` is an ellipse with axis ratio exactly
``1 / cos(t)``, with its major axis along the lean azimuth. So comparing the
observed axis ratio against ``1 / cos(tilt)``, *and* the observed major-axis
direction against the lean azimuth, separates the two causes. Without this the
tool would record leaning circular stems as oval and carry a systematic bias into
any shape-stratified accuracy report. This diagnostic is an addition to the
original handover plan (docs DEC-008).
"""
from __future__ import annotations

from itertools import combinations

import numpy as np

# Provisional. A leaning-stem explanation is accepted when the observed axis ratio
# sits within this tolerance of 1/cos(tilt) and the axes agree in direction.
DEFAULT_RATIO_TOLERANCE = 0.03
DEFAULT_AZIMUTH_TOLERANCE_DEG = 25.0


def compare_diameters(fits: dict, exclude=()) -> dict:
    """Summarise agreement among the valid models in ``fits``.

    ``fits`` maps model name to :class:`FitResult`. Only valid fits with finite
    diameters take part; the names of the excluded ones are reported so that
    "models agree" can never be read off a comparison of one surviving model.

    ``exclude`` names models that are diagnostics rather than competing
    interpretations. Mixing those into the disagreement statistic would conflate
    "the models disagree" with "cleaning the points changed the answer", which are
    different findings.
    """
    exclude = set(exclude)
    usable = {k: f for k, f in fits.items()
              if k not in exclude and f is not None and f.valid
              and f.diameter_m is not None and np.isfinite(f.diameter_m)}
    excluded = sorted(set(fits) - set(usable) - exclude)
    out = {
        "models_compared": sorted(usable),
        "models_excluded": excluded,
        "models_diagnostic_only": sorted(exclude & set(fits)),
        "n_models_compared": len(usable),
        "max_pairwise_difference_m": None,
        "max_pairwise_difference_percent": None,
        "max_pairwise_pair": None,
        "diameter_spread_std_m": None,
        "median_diameter_m": None,
        "pairwise": {},
    }
    if len(usable) < 2:
        if len(usable) == 1:
            only = next(iter(usable.values()))
            out["median_diameter_m"] = float(only.diameter_m)
        return out

    diams = {k: float(f.diameter_m) for k, f in usable.items()}
    worst, worst_pair = -1.0, None
    for a, b in combinations(sorted(diams), 2):
        diff = abs(diams[a] - diams[b])
        out["pairwise"][f"{a}|{b}"] = float(diff)
        if diff > worst:
            worst, worst_pair = diff, (a, b)
    med = float(np.median(list(diams.values())))
    out.update({
        "max_pairwise_difference_m": float(worst),
        "max_pairwise_difference_percent": float(100.0 * worst / med) if med > 0 else None,
        "max_pairwise_pair": list(worst_pair),
        "diameter_spread_std_m": float(np.std(list(diams.values()), ddof=1)),
        "median_diameter_m": med,
        "diameters_m": diams,
    })
    return out


def attribute_ellipticity(ellipse_fit, axis, circular_ratio_max: float = 1.10,
                          ratio_tolerance: float = DEFAULT_RATIO_TOLERANCE,
                          azimuth_tolerance_deg: float = DEFAULT_AZIMUTH_TOLERANCE_DEG,
                          geometry: str = "horizontal") -> dict:
    """Decide whether observed ellipticity is explained by stem lean.

    Returns a verdict of ``CIRCULAR``, ``LEAN_EXPLAINS_ELLIPTICITY``,
    ``GENUINELY_OVAL``, ``OVAL_BEYOND_LEAN`` or ``INCONCLUSIVE`` together with the
    numbers behind it.
    """
    out = {
        "verdict": "INCONCLUSIVE",
        "observed_axis_ratio": None,
        "expected_ratio_from_lean": None,
        "ratio_excess": None,
        "major_axis_deg": None,
        "lean_azimuth_deg": None,
        "azimuth_difference_deg": None,
        "geometry": geometry,
    }
    if ellipse_fit is None or not ellipse_fit.valid:
        out["verdict"] = "NO_ELLIPSE_FIT"
        return out
    ratio = float(ellipse_fit.extra.get("axis_ratio", float("nan")))
    major_deg = float(ellipse_fit.extra.get("rotation_deg", float("nan")))
    out["observed_axis_ratio"] = ratio
    out["major_axis_deg"] = major_deg

    if not np.isfinite(ratio):
        return out
    if ratio <= circular_ratio_max:
        out["verdict"] = "CIRCULAR"
        return out

    # A stem-normal section has already removed the lean, so any remaining
    # ellipticity is shape, not geometry.
    if geometry == "stem_normal":
        out["verdict"] = "GENUINELY_OVAL"
        out["expected_ratio_from_lean"] = 1.0
        out["ratio_excess"] = float(ratio - 1.0)
        return out

    if axis is None or not getattr(axis, "valid", False):
        out["verdict"] = "INCONCLUSIVE"
        return out

    tilt = float(axis.tilt_deg)
    expected = float(1.0 / np.cos(np.radians(tilt))) if tilt < 89.0 else float("inf")
    out["expected_ratio_from_lean"] = expected
    out["ratio_excess"] = float(ratio - expected)
    lean_az = float(axis.azimuth_deg)
    out["lean_azimuth_deg"] = lean_az
    # Axis orientations are modulo 180 degrees.
    dd = abs((major_deg - lean_az + 90.0) % 180.0 - 90.0)
    out["azimuth_difference_deg"] = float(dd)

    aligned = dd <= azimuth_tolerance_deg
    ratio_matches = abs(ratio - expected) <= ratio_tolerance
    if ratio_matches and aligned:
        out["verdict"] = "LEAN_EXPLAINS_ELLIPTICITY"
    elif ratio > expected + ratio_tolerance:
        out["verdict"] = "OVAL_BEYOND_LEAN" if tilt > 2.0 else "GENUINELY_OVAL"
    elif not aligned:
        out["verdict"] = "GENUINELY_OVAL"
    return out


def classify_radial_anomaly(xy, ransac_fit, outline_fit,
                            outlier_fraction_min: float = 0.05,
                            outside_fraction_min: float = 0.75,
                            angular_coverage_max: float = 0.40,
                            min_lobes: int = 3,
                            sector_spread_max_m: float = 0.015,
                            thick_sector_fraction_max: float = 0.25,
                            radial_excess_max_m: float = 0.010,
                            n_sectors: int = 72) -> dict:
    """Distinguish a contaminated section from a genuinely irregular stem.

    Docs 03 lists "definition of an irregular stem vs contaminated data" as an open
    scientific question, and it is not academic: on real data a liana or a clump of
    attached vegetation produces exactly the symptoms of a fluted stem -- large
    residuals, a non-convex outline, and radial roughness -- so an outline model
    will happily trace the contamination and report it as stem shape.

    The strongest separator found on the sample data is *shell thickness*, not
    angular concentration. A stem surface is a thin shell: at any given angle the
    returns span only bark roughness plus sensor noise, a few millimetres, and that
    stays true for a fluted or buttressed stem, which is still a surface. Attached
    vegetation is volumetric: at a given angle its returns spread over centimetres
    in radius. So per-sector radial spread tells a rough *surface* from a *cloud*,
    whereas an angular-concentration test alone does not: a large liana can wrap a
    wide arc and then looks exactly like fluting.

Direction is the second signal, and on the sample data it caught a case the
    thickness test missed: a stem whose shell was thin almost everywhere but which
    carried a diffuse population sitting a median of 2.5 cm *outside* the robust
    circle over roughly 55 degrees of arc. Flutes and bark fissures deviate in
    *both* directions about the dominant surface that RANSAC locks onto, so
    anomalies that are almost entirely outward, and further out than bark roughness
    can account for, indicate attached material:

    contamination
        thick sectors, or anomalies overwhelmingly *outside* the robust circle by
        more than bark roughness, because vegetation grows outward from the stem.
    genuine flutes or buttressing
        thin sectors, with radius varying around the whole circumference and
        dipping below the dominant surface as well as rising above it.

    This is a diagnostic, not a resolution of the open question: it reports the
    evidence and its own verdict, and every threshold is provisional.
    """
    out = {
        "verdict": "AMBIGUOUS",
        "outlier_fraction": None,
        "outlier_fraction_outside": None,
        "outlier_angular_coverage": None,
        "median_radial_excess_m": None,
        "n_lobes_estimate": None,
        "sector_fraction_above_median": None,
        "median_sector_radial_iqr_m": None,
        "thick_sector_fraction": None,
        "sector_spread_threshold_m": float(sector_spread_max_m),
        "radial_excess_threshold_m": float(radial_excess_max_m),
    }
    if ransac_fit is None or ransac_fit.center_xy is None:
        out["verdict"] = "NO_ROBUST_FIT"
        return out
    xy = np.asarray(xy, dtype=float)
    cx, cy = ransac_fit.center_xy
    r_rob = float(ransac_fit.extra.get("radius_m", np.nan))
    thr = float(ransac_fit.extra.get("residual_threshold_m", 0.01))
    if not np.isfinite(r_rob) or len(xy) == 0:
        out["verdict"] = "NO_ROBUST_FIT"
        return out

    r = np.hypot(xy[:, 0] - cx, xy[:, 1] - cy)
    dev = r - r_rob
    outlier = np.abs(dev) > thr
    n_out = int(outlier.sum())
    out["outlier_fraction"] = float(n_out / len(xy))

    # Shell thickness: per-sector radial spread, computed from the points directly
    # so it stays available even when the outline model was rejected.
    ang_all = np.degrees(np.arctan2(xy[:, 1] - cy, xy[:, 0] - cx)) % 360.0
    width = 360.0 / n_sectors
    sec = np.minimum((ang_all / width).astype(int), n_sectors - 1)
    order = np.argsort(sec, kind="stable")
    sec_s, r_s = sec[order], r[order]
    edges = np.searchsorted(sec_s, np.arange(n_sectors + 1))
    iqrs = []
    for k in range(n_sectors):
        seg = r_s[edges[k]:edges[k + 1]]
        if seg.size >= 5:
            q1, q3 = np.percentile(seg, [25, 75])
            iqrs.append(q3 - q1)
    if iqrs:
        arr = np.asarray(iqrs)
        out["median_sector_radial_iqr_m"] = float(np.median(arr))
        out["thick_sector_fraction"] = float(np.mean(arr > sector_spread_max_m))

    if n_out == 0:
        out["verdict"] = "CLEAN"
        return out

    out["outlier_fraction_outside"] = float(np.mean(dev[outlier] > 0))
    out["median_radial_excess_m"] = float(np.median(dev[outlier]))
    cov = angular_coverage_of(xy[outlier], (cx, cy))
    out["outlier_angular_coverage"] = cov

    # Lobe structure from the reconstructed outline, when one is available.
    if outline_fit is not None and outline_fit.extra.get("sector_radius_m") is not None:
        sr = np.asarray(outline_fit.extra["sector_radius_m"], dtype=float)
        occ = np.asarray(outline_fit.extra.get("sector_occupied",
                                               np.ones(len(sr), bool)), dtype=bool)
        if occ.sum() >= 8:
            med = float(np.median(sr[occ]))
            sign = np.sign(sr - med)
            sign = sign[occ]
            sign = sign[sign != 0]
            if sign.size > 2:
                crossings = int(np.count_nonzero(np.diff(sign) != 0))
                # A closed curve crosses its own median an even number of times;
                # each lobe contributes two crossings.
                out["n_lobes_estimate"] = int(max(1, round(crossings / 2)))
            out["sector_fraction_above_median"] = float(np.mean(sr[occ] > med))

    if out["outlier_fraction"] < outlier_fraction_min:
        out["verdict"] = "CLEAN"
        return out

    # Primary test: is this section a surface, or a cloud?
    thick = out["thick_sector_fraction"]
    if thick is not None and thick > thick_sector_fraction_max:
        out["verdict"] = "CONTAMINATION_SUSPECTED"
        return out

    # Second test: are the anomalies one-sidedly outward, and further out than
    # bark roughness explains? Flutes deviate both ways about the fitted surface.
    one_sided = out["outlier_fraction_outside"] >= outside_fraction_min
    excess = out["median_radial_excess_m"]
    if one_sided and excess is not None and excess > radial_excess_max_m:
        out["verdict"] = "CONTAMINATION_SUSPECTED"
        return out

    concentrated = cov <= angular_coverage_max
    if concentrated and one_sided:
        out["verdict"] = "CONTAMINATION_SUSPECTED"
        return out
    lobes = out["n_lobes_estimate"]
    frac_above = out["sector_fraction_above_median"]
    if (lobes is not None and lobes >= min_lobes and frac_above is not None
            and 0.25 <= frac_above <= 0.75):
        out["verdict"] = "IRREGULAR_SHAPE"
        return out
    if one_sided:
        out["verdict"] = "CONTAMINATION_SUSPECTED"
    return out


def angular_coverage_of(xy, center, bin_deg: float = 5.0) -> float:
    """Fraction of angular bins about ``center`` that contain at least one point."""
    xy = np.asarray(xy, dtype=float)
    if len(xy) == 0:
        return 0.0
    n_bins = int(round(360.0 / bin_deg))
    ang = np.degrees(np.arctan2(xy[:, 1] - center[1], xy[:, 0] - center[0])) % 360.0
    idx = np.minimum((ang / bin_deg).astype(int), n_bins - 1)
    return float(np.bincount(idx, minlength=n_bins).astype(bool).mean())


def lean_bias_estimate(diameter_m: float, tilt_deg: float) -> dict:
    """Expected horizontal-section bias for a circular stem leaning ``tilt_deg``.

    Reported so that the size of the effect is visible in the output rather than
    being an argument in a document. The best-fit circle to a full ellipse of
    semi-axes (a, b) has radius close to (a + b) / 2 to first order, hence the
    mean-axes form used here.
    """
    if not np.isfinite(tilt_deg) or tilt_deg <= 0:
        return {"tilt_deg": float(tilt_deg), "major_axis_bias_m": 0.0,
                "circle_fit_bias_m": 0.0, "circle_fit_bias_percent": 0.0}
    c = float(np.cos(np.radians(tilt_deg)))
    major = diameter_m / c
    circle_equiv = 0.5 * (major + diameter_m)
    return {
        "tilt_deg": float(tilt_deg),
        "major_axis_bias_m": float(major - diameter_m),
        "circle_fit_bias_m": float(circle_equiv - diameter_m),
        "circle_fit_bias_percent": float(100.0 * (circle_equiv - diameter_m) / diameter_m),
    }
