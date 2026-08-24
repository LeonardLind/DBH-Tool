"""Local stem axis estimation.

A horizontal cut through a stem that leans by angle ``t`` from vertical produces
an ellipse whose major axis is ``D / cos(t)``. That is a *systematic* upward bias,
not noise: it does not average out over repeated measurements, and it grows
quickly enough to matter (roughly +1.5% at 10 degrees, +6.4% at 20 degrees on the
major axis). On sloped terrain leaning stems are the norm rather than the
exception, which is why the axis is estimated in V1 rather than deferred
(docs DEC-007).

Estimating the axis also resolves an otherwise irreducible ambiguity: an
elliptical horizontal section can mean a genuinely oval stem *or* a circular stem
that leans. Comparing the observed axis ratio with ``1 / cos(tilt)`` separates the
two, and that comparison lives in evaluation/compare.py.

Per-bin centres come from circle fits rather than centroids. The centroid of a
partially observed cylindrical shell is pulled towards the visible side, so
centroid-based axes lean towards the scanner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..fitting.circle import fit_circle_taubin


@dataclass
class StemAxis:
    """A straight-line stem axis over a short vertical neighbourhood."""

    point_xyz: np.ndarray               # a point on the axis (at reference HAG)
    direction: np.ndarray               # unit vector, always pointing upward
    reference_hag_m: float
    tilt_deg: float
    azimuth_deg: float                  # downslope-style azimuth of the lean, 0 = +X
    straightness_m: float               # RMS of bin centres about the fitted line
    n_bins_used: int
    bin_hag_m: list = field(default_factory=list)
    bin_center_xy: list = field(default_factory=list)
    bin_diameter_m: list = field(default_factory=list)
    bin_point_count: list = field(default_factory=list)
    valid: bool = False
    warnings: list = field(default_factory=list)

    @property
    def is_vertical(self) -> bool:
        return bool(self.valid and self.tilt_deg < 1e-6)

    def xy_at_hag(self, hag_m: float) -> tuple[float, float]:
        """XY of the axis at a given HAG, assuming locally planar ground."""
        d = self.direction
        if abs(d[2]) < 1e-9:
            return float(self.point_xyz[0]), float(self.point_xyz[1])
        s = (hag_m - self.reference_hag_m) / d[2]
        p = self.point_xyz + s * d
        return float(p[0]), float(p[1])

    def to_dict(self) -> dict:
        return {
            "point_xyz": [float(v) for v in self.point_xyz],
            "direction": [float(v) for v in self.direction],
            "reference_hag_m": float(self.reference_hag_m),
            "tilt_deg": float(self.tilt_deg),
            "azimuth_deg": float(self.azimuth_deg),
            "straightness_m": float(self.straightness_m),
            "n_bins_used": int(self.n_bins_used),
            "bin_hag_m": [float(v) for v in self.bin_hag_m],
            "bin_diameter_m": [float(v) for v in self.bin_diameter_m],
            "bin_point_count": [int(v) for v in self.bin_point_count],
            "valid": bool(self.valid),
            "warnings": list(self.warnings),
        }


def vertical_axis(center_xy, ground_z: float, reference_hag_m: float = 1.30) -> StemAxis:
    """A perfectly vertical axis. Used as the fallback and as the null hypothesis."""
    p = np.array([float(center_xy[0]), float(center_xy[1]),
                  float(ground_z) + float(reference_hag_m)])
    return StemAxis(point_xyz=p, direction=np.array([0.0, 0.0, 1.0]),
                    reference_hag_m=float(reference_hag_m), tilt_deg=0.0,
                    azimuth_deg=0.0, straightness_m=0.0, n_bins_used=0,
                    valid=True, warnings=["assumed_vertical"])


def plane_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two orthonormal vectors spanning the plane normal to ``direction``.

    ``e1`` is chosen to be the horizontal-ish direction in the plane containing
    the lean, so that section coordinates stay interpretable: ``e1`` points along
    the lean azimuth and ``e2`` is horizontal and perpendicular to it.
    """
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(d @ up)) > 1 - 1e-12:
        return np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    e2 = np.cross(up, d)
    e2 /= np.linalg.norm(e2)
    e1 = np.cross(d, e2)
    e1 /= np.linalg.norm(e1)
    return e1, e2


def estimate_stem_axis(xyz: np.ndarray, height_m: np.ndarray, seed_center_xy,
                       seed_radius_m: float, lower_hag_m: float = 0.80,
                       upper_hag_m: float = 2.20, n_bins: int = 8,
                       min_points_per_bin: int = 30, reference_hag_m: float = 1.30,
                       search_radius_factor: float = 2.0, n_iterations: int = 3,
                       max_tilt_deg: float = 45.0) -> StemAxis:
    """Estimate the local stem axis from circle-fit centres in height bins.

    ``height_m`` is height above the stem scalar ground datum, for the same reason
    the sections use it: bins must be geometrically horizontal slabs.
    """
    xyz = np.asarray(xyz, dtype=float)
    height_m = np.asarray(height_m, dtype=float)
    cx, cy = float(seed_center_xy[0]), float(seed_center_xy[1])
    band = (np.isfinite(height_m) & (height_m >= lower_hag_m)
            & (height_m <= upper_hag_m))
    axis = StemAxis(
        point_xyz=np.array([cx, cy, np.nan]), direction=np.array([0.0, 0.0, 1.0]),
        reference_hag_m=float(reference_hag_m), tilt_deg=0.0, azimuth_deg=0.0,
        straightness_m=float("nan"), n_bins_used=0,
    )
    if band.sum() < min_points_per_bin * 2:
        axis.warnings.append("too_few_points_in_axis_band")
        return axis

    p = xyz[band]
    h = height_m[band]
    edges = np.linspace(lower_hag_m, upper_hag_m, int(n_bins) + 1)
    which = np.clip(np.digitize(h, edges) - 1, 0, int(n_bins) - 1)

    # Current axis estimate as (x, y) = base + slope * hag; start vertical.
    base = np.array([cx, cy])
    slope = np.zeros(2)
    radius = float(seed_radius_m)

    # Bin elevations from the last *successful* iteration. Keeping this separate
    # from the working list matters: an iteration that finds too few bins breaks
    # out, and reading a half-filled working list alongside the previous
    # iteration's bin heights is a length mismatch waiting to happen.
    good_bin_z: list[float] = []
    for _ in range(max(1, int(n_iterations))):
        heights, centers, diameters, counts, bin_z = [], [], [], [], []
        for b in range(int(n_bins)):
            sel = which == b
            if sel.sum() < min_points_per_bin:
                continue
            hb = float(np.mean(h[sel]))
            pred = base + slope * hb
            sub = p[sel]
            d2 = (sub[:, 0] - pred[0]) ** 2 + (sub[:, 1] - pred[1]) ** 2
            near = d2 <= (search_radius_factor * radius) ** 2
            if near.sum() < min_points_per_bin:
                continue
            fit = fit_circle_taubin(sub[near][:, :2])
            if not fit.valid or fit.center_xy is None:
                continue
            heights.append(hb)
            centers.append(fit.center_xy)
            diameters.append(fit.diameter_m)
            counts.append(int(near.sum()))
            bin_z.append(float(np.mean(sub[near][:, 2])))
        if len(heights) < 3:
            axis.warnings.append(f"only_{len(heights)}_usable_axis_bins")
            break
        H = np.asarray(heights)
        C = np.asarray(centers)
        W = np.asarray(counts, dtype=float)
        W = W / W.sum()
        # Weighted linear regression of x and y on HAG. Valid because the stem is
        # near-vertical; tilts beyond max_tilt_deg are rejected below.
        A = np.column_stack([H, np.ones_like(H)])
        Aw = A * np.sqrt(W)[:, None]
        sol, *_ = np.linalg.lstsq(Aw, C * np.sqrt(W)[:, None], rcond=None)
        slope = sol[0]
        base = sol[1]
        radius = float(np.median(diameters) / 2.0)
        resid = C - (base + np.outer(H, slope))
        axis.straightness_m = float(np.sqrt(np.mean(np.sum(resid ** 2, axis=1))))
        axis.bin_hag_m = [float(v) for v in H]
        axis.bin_center_xy = [tuple(map(float, c)) for c in C]
        axis.bin_diameter_m = [float(v) for v in diameters]
        axis.bin_point_count = [int(v) for v in counts]
        axis.n_bins_used = len(heights)
        good_bin_z = list(bin_z)

    if axis.n_bins_used < 3:
        return axis

    # Direction: d(x, y)/d(hag) gives the horizontal drift per unit height.
    direction = np.array([slope[0], slope[1], 1.0])
    direction /= np.linalg.norm(direction)
    tilt = float(np.degrees(np.arccos(abs(direction[2]))))
    ref_xy = base + slope * reference_hag_m
    # Reference Z comes from the bin mean elevations, so the axis point sits
    # inside the cloud rather than on an extrapolated ground plane.
    if len(good_bin_z) != len(axis.bin_hag_m) or not good_bin_z:
        axis.warnings.append("bin_elevations_unavailable_for_reference_height")
        return axis
    z_at_ref = float(np.interp(reference_hag_m, np.asarray(axis.bin_hag_m),
                               np.asarray(good_bin_z)))

    axis.point_xyz = np.array([ref_xy[0], ref_xy[1], z_at_ref])
    axis.direction = direction
    axis.tilt_deg = tilt
    axis.azimuth_deg = float(np.degrees(np.arctan2(slope[1], slope[0])) % 360.0)
    axis.valid = True
    if tilt > max_tilt_deg:
        axis.valid = False
        axis.warnings.append(f"tilt_{tilt:.0f}deg_exceeds_limit")
    if axis.straightness_m > 0.05:
        axis.warnings.append(
            f"axis_centres_scatter_{axis.straightness_m:.3f}m_stem_may_be_curved")
    return axis
