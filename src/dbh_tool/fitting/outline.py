"""Irregular-outline model: angular radial-median polygon.

For fluted, buttressed or otherwise irregular stems neither a circle nor an
ellipse is scientifically honest (docs 02, section 8). This model reconstructs a
closed cross-sectional outline directly from the points and reduces it to
equivalent diameters.

Three equivalent diameters are emitted, and they are *different quantities*:

``diameter_area_equiv_m``
    2*sqrt(A/pi) from the polar area A = 0.5*integral(r^2 dtheta). This is the
    right quantity for basal area and biomass work, and it is the primary summary.

``diameter_perimeter_equiv_m``
    P/pi from the outline perimeter. Highly sensitive to bark roughness,
    concavities and sector resolution, so it is a diagnostic only.

``diameter_convex_perimeter_equiv_m``
    P_convex/pi from the *convex hull* perimeter. This is the correct comparator
    for a field tape: a tape bridges flutes and concavities rather than following
    them, so tape DBH on a fluted stem corresponds to the convex perimeter, not to
    the true cross-sectional area. Validation against tape measurements must use
    this column, otherwise a real geometric difference is scored as tool error.

Area is computed as the polar integral rather than by the shoelace formula on the
polygon vertices, because the polar form is exact for a star-shaped region and
does not suffer the corner-cutting deficit of an inscribed polygon.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from .common import (
    FitResult,
    area_equivalent_diameter,
    as_xy,
    check_plausible_diameter,
    perimeter_equivalent_diameter,
    residual_stats,
)

DEFAULT_N_SECTORS = 72          # 5-degree sectors
DEFAULT_MIN_POINTS_PER_SECTOR = 3
# Never invent geometry across a gap wider than this: a bridged gap is modelled,
# not observed, and bridging a large gap silently fabricates the outline.
DEFAULT_MAX_BRIDGE_GAP_DEG = 20.0
DEFAULT_MIN_OCCUPIED_FRACTION = 0.75


def _polygon_perimeter(xy: np.ndarray) -> float:
    d = np.diff(np.vstack([xy, xy[:1]]), axis=0)
    return float(np.sum(np.hypot(d[:, 0], d[:, 1])))


def _shoelace_area(xy: np.ndarray) -> float:
    x, y = xy[:, 0], xy[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def fit_outline_radial_median(points, center: tuple[float, float],
                              n_sectors: int = DEFAULT_N_SECTORS,
                              min_points_per_sector: int = DEFAULT_MIN_POINTS_PER_SECTOR,
                              max_bridge_gap_deg: float = DEFAULT_MAX_BRIDGE_GAP_DEG,
                              min_occupied_fraction: float = DEFAULT_MIN_OCCUPIED_FRACTION
                              ) -> FitResult:
    """Reconstruct a closed outline as a robust radius per angular sector.

    ``center`` must come from a prior consensus fit (circle or ellipse): the
    outline is defined in polar coordinates and is therefore centre-dependent.
    """
    xy = as_xy(points)
    fit = FitResult(
        model="outline_radial_median",
        diameter_definition=(
            "area-equivalent diameter 2*sqrt(A/pi) of the radial-median outline"),
        point_count=int(len(xy)),
        center_xy=(float(center[0]), float(center[1])),
        extra={
            "outline_method": "radial_median_polygon",
            "n_sectors": int(n_sectors),
            "sector_width_deg": float(360.0 / n_sectors),
            "min_points_per_sector": int(min_points_per_sector),
            "max_bridge_gap_deg": float(max_bridge_gap_deg),
        },
    )
    if n_sectors < 8:
        raise ValueError("n_sectors must be at least 8")
    if len(xy) < min_points_per_sector * 8:
        return fit.invalidate("too_few_points")

    dx, dy = xy[:, 0] - center[0], xy[:, 1] - center[1]
    r = np.hypot(dx, dy)
    ang = np.arctan2(dy, dx) % (2.0 * np.pi)
    width = 2.0 * np.pi / n_sectors
    sec = np.minimum((ang / width).astype(int), n_sectors - 1)

    counts = np.bincount(sec, minlength=n_sectors)
    occupied = counts >= min_points_per_sector
    radius = np.full(n_sectors, np.nan)
    # Robust radius per sector: the median resists lianas and branch points that
    # sit outside the stem surface within the same sector.
    order = np.argsort(sec, kind="stable")
    sec_sorted, r_sorted = sec[order], r[order]
    edges = np.searchsorted(sec_sorted, np.arange(n_sectors + 1))
    for s in range(n_sectors):
        if occupied[s]:
            radius[s] = np.median(r_sorted[edges[s]:edges[s + 1]])

    occ_frac = float(occupied.mean())
    fit.extra.update({
        "occupied_sectors": int(occupied.sum()),
        "occupied_fraction": occ_frac,
        "points_per_sector_median": float(np.median(counts[occupied])) if occupied.any() else 0.0,
    })
    if occupied.sum() < 8:
        return fit.invalidate("too_few_occupied_sectors")

    # Largest unobserved angular run, on the sector grid.
    where = np.flatnonzero(occupied)
    nxt = np.roll(where, -1)
    empty_runs = (nxt - where - 1) % n_sectors
    largest_gap_deg = float(empty_runs.max() * np.degrees(width))
    fit.angular_coverage = occ_frac
    fit.largest_gap_deg = largest_gap_deg
    fit.n_arcs = int(np.count_nonzero(~occupied[(where - 1) % n_sectors])) or 1
    fit.extra["largest_gap_deg"] = largest_gap_deg

    # Bridge only small gaps, by linear interpolation of radius in angle.
    bridged = int(np.count_nonzero(~occupied))
    theta = (np.arange(n_sectors) + 0.5) * width
    filled = radius.copy()
    if bridged:
        known = np.flatnonzero(occupied)
        # Periodic interpolation: extend the known samples one turn either side.
        xp = np.concatenate([theta[known] - 2 * np.pi, theta[known], theta[known] + 2 * np.pi])
        fp = np.concatenate([radius[known]] * 3)
        filled = np.interp(theta, xp, fp)
    fit.extra["bridged_sectors"] = bridged
    fit.extra["bridged_fraction"] = float(bridged / n_sectors)

    # Polar area is exact for a star-shaped region; the shoelace polygon area is
    # kept alongside it as a cross-check on the reconstruction.
    area_polar = float(0.5 * np.sum(filled ** 2) * width)
    poly = np.column_stack([center[0] + filled * np.cos(theta),
                            center[1] + filled * np.sin(theta)])
    area_poly = _shoelace_area(poly)
    perim = _polygon_perimeter(poly)

    conv_area = conv_perim = float("nan")
    try:
        hull = ConvexHull(poly)
        hull_xy = poly[hull.vertices]
        conv_area = _shoelace_area(hull_xy)
        conv_perim = _polygon_perimeter(hull_xy)
        fit.extra["convex_hull_xy"] = hull_xy
    except (QhullError, ValueError):
        fit.warn("convex_hull_failed")

    d_area = area_equivalent_diameter(area_polar)
    fit.diameter_m = d_area
    fit.extra.update({
        "area_m2": area_polar,
        "area_polygon_shoelace_m2": area_poly,
        "perimeter_m": perim,
        "convex_area_m2": conv_area,
        "convex_perimeter_m": conv_perim,
        "diameter_area_equiv_m": d_area,
        "diameter_perimeter_equiv_m": perimeter_equivalent_diameter(perim),
        "diameter_convex_perimeter_equiv_m": perimeter_equivalent_diameter(conv_perim),
        "diameter_convex_area_equiv_m": area_equivalent_diameter(conv_area),
        "radius_min_m": float(np.nanmin(filled)),
        "radius_max_m": float(np.nanmax(filled)),
        "radius_median_m": float(np.nanmedian(filled)),
        "radial_roughness_m": float(np.nanstd(filled)),
        "convexity_deficit": (float(1.0 - area_polar / conv_area)
                              if np.isfinite(conv_area) and conv_area > 0 else None),
        "outline_xy": poly,
        "sector_radius_m": filled,
        "sector_occupied": occupied,
        "sector_theta_rad": theta,
    })

    # Residuals: radial distance from each point to its own sector radius, which
    # keeps the statistic comparable with the circle and ellipse models.
    res = r - filled[sec]
    for k_, v_ in residual_stats(res).items():
        setattr(fit, k_, v_)

    fit.valid = True
    fit = check_plausible_diameter(fit)
    if largest_gap_deg > max_bridge_gap_deg:
        fit.invalidate(f"largest_gap_{largest_gap_deg:.0f}deg_exceeds_bridge_limit")
    if occ_frac < min_occupied_fraction:
        fit.invalidate(f"occupied_fraction_below_{min_occupied_fraction}")
    return fit
