"""Per-stem local ground estimate with an explicit quality verdict.

Using one plot-wide elevation is wrong on any slope, and even a good raster DTM
can be locally unreliable (a cell interpolated across an occluded patch, or a
patch of root buttress mistaken for terrain). So the ground under each stem is
re-estimated from the surface immediately around that stem, and the estimate
carries its own quality record.

The fit is a robust plane: iteratively reweighted least squares with Huber
weights, which resists a handful of contaminated cells without the cost of a full
RANSAC. Ground uncertainty propagates directly into DBH status, because a 10 cm
ground error moves the measurement height by 10 cm, which on a tapering stem is a
real diameter error.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .dtm import GroundGrid


@dataclass
class LocalGround:
    """Ground estimate directly at one stem location."""

    z_m: float
    slope_deg: float
    aspect_deg: float
    roughness_m: float               # robust residual scatter about the local plane
    n_cells: int
    observed_fraction: float         # share of nearby cells from real low points
    radius_m: float
    source: str
    quality: str = "UNKNOWN"         # GOOD | FAIR | POOR
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _huber_plane(pts: np.ndarray, n_iter: int = 5, k: float = 1.345):
    """Fit z = a*x + b*y + c with Huber-weighted IRLS. Returns (coef, residuals)."""
    A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
    z = pts[:, 2]
    w = np.ones(len(pts))
    coef = np.zeros(3)
    for _ in range(n_iter):
        Aw = A * w[:, None]
        try:
            coef, *_ = np.linalg.lstsq(Aw, z * w, rcond=None)
        except np.linalg.LinAlgError:
            return None, None
        res = z - A @ coef
        # Robust scale from the MAD; the 1.4826 factor makes it a sigma estimate.
        mad = np.median(np.abs(res - np.median(res)))
        scale = max(1.4826 * mad, 1e-6)
        u = np.abs(res) / scale
        w = np.where(u <= k, 1.0, k / np.maximum(u, 1e-12))
    return coef, z - A @ coef


def local_ground_at(grid: GroundGrid, x: float, y: float, radius_m: float = 2.0,
                    min_points: int = 40, max_slope_deg: float = 45.0,
                    max_roughness_m: float = 0.15) -> LocalGround:
    """Estimate ground elevation, slope and reliability at a stem location."""
    pts = grid.cells_near(x, y, radius_m)
    obs_frac = grid.observed_fraction(x, y, radius_m)
    lg = LocalGround(
        z_m=float("nan"), slope_deg=float("nan"), aspect_deg=float("nan"),
        roughness_m=float("nan"), n_cells=int(len(pts)),
        observed_fraction=obs_frac, radius_m=float(radius_m),
        source=grid.meta.get("method", "local_minimum_grid"),
    )
    if len(pts) < 3:
        lg.quality = "POOR"
        lg.warnings.append("too_few_ground_cells")
        # Fall back to the raster value so the caller still has a number to
        # inspect, clearly marked as low quality.
        lg.z_m = float(np.asarray(grid.elevation(x, y)).item())
        return lg

    coef, res = _huber_plane(pts)
    if coef is None:
        lg.quality = "POOR"
        lg.warnings.append("plane_fit_failed")
        lg.z_m = float(np.asarray(grid.elevation(x, y)).item())
        return lg

    a, b, c = coef
    lg.z_m = float(a * x + b * y + c)
    lg.slope_deg = float(np.degrees(np.arctan(np.hypot(a, b))))
    # Aspect: compass-style azimuth of the downslope direction, 0 = +Y, clockwise.
    lg.aspect_deg = float(np.degrees(np.arctan2(-a, -b)) % 360.0)
    mad = np.median(np.abs(res - np.median(res)))
    lg.roughness_m = float(1.4826 * mad)

    if len(pts) < min_points:
        lg.warnings.append(f"only_{len(pts)}_ground_cells")
    if lg.slope_deg > max_slope_deg:
        lg.warnings.append(f"local_slope_{lg.slope_deg:.0f}deg_exceeds_limit")
    if lg.roughness_m > max_roughness_m:
        lg.warnings.append(f"ground_roughness_{lg.roughness_m:.3f}m_exceeds_limit")
    if obs_frac < 0.5:
        lg.warnings.append(f"only_{obs_frac:.0%}_of_nearby_ground_cells_observed")

    # Quality verdict. Deliberately coarse: these are provisional bands, not a
    # calibrated probability (docs 02, section 16).
    if not lg.warnings:
        lg.quality = "GOOD"
    elif (lg.roughness_m <= max_roughness_m * 1.5 and obs_frac >= 0.3
          and lg.slope_deg <= max_slope_deg):
        lg.quality = "FAIR"
    else:
        lg.quality = "POOR"
    return lg
