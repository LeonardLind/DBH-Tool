"""Cross-section construction: horizontal and stem-normal.

Both geometries are produced for every measurement so that the difference between
them is observable rather than assumed (docs DEC-007). The horizontal section is
what the forestry convention describes and what most published TLS work fits; the
stem-normal section is the geometrically correct cut through a leaning stem. Which
one becomes the reported DBH is a validation question, not an implementation
choice, so both are recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .axis import StemAxis, plane_basis


@dataclass
class TreeCrossSection:
    """Points forming one cross-section, plus how it was constructed."""

    tree_id: str
    geometry: str                     # "horizontal" | "stem_normal"
    target_height_m: float            # nominal HAG of the section
    band_thickness_m: float
    local_ground_z_m: float
    points_xy: np.ndarray             # (N, 2) section-plane coordinates
    points_xyz: np.ndarray            # (N, 3) the same points in world coordinates
    origin_xy: tuple[float, float]    # world XY that maps to (0, 0) in points_xy
    basis: tuple[np.ndarray, np.ndarray] | None = None   # e1, e2 for stem-normal
    metadata: dict = field(default_factory=dict)

    @property
    def source_point_count(self) -> int:
        return int(len(self.points_xy))

    def world_xy(self, section_xy) -> np.ndarray:
        """Map section-plane coordinates back to world XY (for plotting overlays)."""
        section_xy = np.atleast_2d(np.asarray(section_xy, dtype=float))
        if self.basis is None:
            return section_xy + np.asarray(self.origin_xy)
        e1, e2 = self.basis
        return (np.asarray(self.origin_xy)
                + section_xy[:, :1] * e1[None, :2] + section_xy[:, 1:2] * e2[None, :2])

    def to_meta(self) -> dict:
        return {
            "tree_id": self.tree_id,
            "geometry": self.geometry,
            "target_height_m": float(self.target_height_m),
            "band_thickness_m": float(self.band_thickness_m),
            "local_ground_z_m": float(self.local_ground_z_m),
            "point_count": self.source_point_count,
            "origin_xy": [float(v) for v in self.origin_xy],
            **self.metadata,
        }


def horizontal_section(tree_id: str, xyz: np.ndarray, height_m: np.ndarray,
                       target_height_m: float, thickness_m: float,
                       center_xy, max_radius_m: float,
                       local_ground_z_m: float) -> TreeCrossSection:
    """Extract a horizontal slice around a stem.

    ``height_m`` must be height above a *single scalar ground datum for this stem*
    (``z - local_ground_z``), not per-point height above the ground raster.

    The distinction is not cosmetic. Selecting on per-point height above ground
    makes the slab follow the terrain, so on sloped ground the cut plane is tilted
    by the ground slope, and that tilt adds to the stem lean: on a 15 degree slope a
    stem leaning 20 degrees downhill was cut at an effective 35 degrees, inflating
    the observed axis ratio from 1.06 to 1.19 and breaking the lean-versus-ovality
    diagnostic that assumes ``1 / cos(tilt)``. A horizontal cross-section has to be
    geometrically horizontal; height above ground selects the *datum*, and does not
    shape the cut plane (docs DEC-010).

    ``max_radius_m`` bounds how far from the seed centre points are accepted, which
    keeps a neighbouring stem or understory clump out of the section.
    """
    xyz = np.asarray(xyz, dtype=float)
    height_m = np.asarray(height_m, dtype=float)
    half = thickness_m / 2.0
    cx, cy = float(center_xy[0]), float(center_xy[1])
    with np.errstate(invalid="ignore"):
        m = (np.isfinite(height_m) & (height_m >= target_height_m - half)
             & (height_m <= target_height_m + half))
        m &= ((xyz[:, 0] - cx) ** 2 + (xyz[:, 1] - cy) ** 2) <= max_radius_m ** 2
    sel = xyz[m]
    return TreeCrossSection(
        tree_id=tree_id, geometry="horizontal",
        target_height_m=float(target_height_m), band_thickness_m=float(thickness_m),
        local_ground_z_m=float(local_ground_z_m),
        points_xy=sel[:, :2] - np.array([cx, cy]),
        points_xyz=sel, origin_xy=(cx, cy),
        metadata={"max_radius_m": float(max_radius_m),
                  "height_datum": "scalar local ground at stem centre",
                  "height_min_m": float(np.min(height_m[m])) if m.any() else None,
                  "height_max_m": float(np.max(height_m[m])) if m.any() else None},
    )


def stem_normal_section(tree_id: str, xyz: np.ndarray, height_m: np.ndarray, axis: StemAxis,
                        target_height_m: float, thickness_m: float,
                        max_radius_m: float, local_ground_z_m: float) -> TreeCrossSection:
    """Extract a slice in the plane normal to the stem axis.

    The section is centred on the axis point at HAG ``target_height_m``, and the
    band thickness is measured *along the axis*, so a leaning stem is cut
    perpendicular to itself rather than obliquely.
    """
    xyz = np.asarray(xyz, dtype=float)
    height_m = np.asarray(height_m, dtype=float)
    d = np.asarray(axis.direction, dtype=float)
    d = d / np.linalg.norm(d)
    e1, e2 = plane_basis(d)

    # Axis point at the requested HAG. The axis is parameterised by HAG through
    # its own reference height, which already accounts for local ground.
    ax, ay = axis.xy_at_hag(target_height_m)
    s_ref = (target_height_m - axis.reference_hag_m) / d[2] if abs(d[2]) > 1e-9 else 0.0
    center = np.asarray(axis.point_xyz, dtype=float) + s_ref * d

    rel = xyz - center
    s = rel @ d                              # signed distance along the axis
    perp = rel - s[:, None] * d[None, :]
    r = np.linalg.norm(perp, axis=1)
    half = thickness_m / 2.0
    m = (np.abs(s) <= half) & (r <= max_radius_m)
    sel = xyz[m]
    rel_sel = rel[m]
    section_xy = np.column_stack([rel_sel @ e1, rel_sel @ e2])
    return TreeCrossSection(
        tree_id=tree_id, geometry="stem_normal",
        target_height_m=float(target_height_m), band_thickness_m=float(thickness_m),
        local_ground_z_m=float(local_ground_z_m),
        points_xy=section_xy, points_xyz=sel,
        origin_xy=(float(center[0]), float(center[1])),
        basis=(e1, e2),
        metadata={
            "max_radius_m": float(max_radius_m),
            "axis_tilt_deg": float(axis.tilt_deg),
            "axis_azimuth_deg": float(axis.azimuth_deg),
            "axis_center_xyz": [float(v) for v in center],
            "e1": [float(v) for v in e1],
            "e2": [float(v) for v in e2],
            "height_at_axis_center_m": float(target_height_m),
            "mean_height_of_points_m": float(np.mean(height_m[m])) if m.any() else None,
        },
    )


def remove_isolated(xy: np.ndarray, radius_m: float = 0.05,
                    min_neighbours: int = 3) -> np.ndarray:
    """Boolean mask dropping points with too few neighbours within ``radius_m``.

    Aimed at sparse scanner noise floating off the stem surface. Kept mild on
    purpose: aggressive cleaning would also delete the genuinely thin coverage on
    the far side of a stem, which is exactly the geometry the coverage metric
    needs to see.
    """
    from scipy.spatial import cKDTree

    xy = np.asarray(xy, dtype=float)
    if len(xy) <= min_neighbours:
        return np.ones(len(xy), dtype=bool)
    tree = cKDTree(xy)
    counts = np.asarray(tree.query_ball_point(xy, radius_m, return_length=True))
    return counts >= (min_neighbours + 1)   # +1: the point finds itself
