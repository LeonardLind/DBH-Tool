"""Stem candidate detection (preview of milestone M6).

The development order in the handover is deliberate: measurement science first,
automatic detection afterwards, so that detection errors and measurement errors
are never confounded. This module is therefore *not* the finished detector. It
exists so that an operator can find plausible stems in a real scan to test the
measurement path on, and every candidate carries the reasons it was kept or
rejected.

Detection works on the height-normalised breast-height band: stems appear there
as compact, roughly circular clusters that also have points above and below. The
vertical-continuity test is what separates a stem from a boulder, a log, or a
clump of understory.

Precision and recall against a manually annotated stem list are required before
this is used for anything but exploration (docs 03, M6).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage


@dataclass
class StemCandidate:
    """One candidate stem location with the evidence behind it."""

    candidate_id: str
    center_xy: tuple[float, float]
    n_points: int
    extent_x_m: float
    extent_y_m: float
    approx_diameter_m: float
    elongation: float
    continuity_fraction: float
    continuity_heights_m: list = field(default_factory=list)
    accepted: bool = False
    rejection_reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "center_xy": [float(v) for v in self.center_xy],
            "n_points": self.n_points,
            "extent_x_m": self.extent_x_m,
            "extent_y_m": self.extent_y_m,
            "approx_diameter_m": self.approx_diameter_m,
            "elongation": self.elongation,
            "continuity_fraction": self.continuity_fraction,
            "continuity_heights_m": [float(v) for v in self.continuity_heights_m],
            "accepted": self.accepted,
            "rejection_reasons": list(self.rejection_reasons),
        }


def find_stem_candidates(xyz: np.ndarray, hag: np.ndarray, target_height_m: float = 1.30,
                         band_thickness_m: float = 0.20, cell_m: float = 0.05,
                         min_points: int = 150, min_diameter_m: float = 0.08,
                         max_diameter_m: float = 2.00, max_elongation: float = 3.0,
                         continuity_heights_m=(0.60, 1.00, 1.80, 2.60),
                         continuity_radius_m: float = 0.50,
                         min_continuity_fraction: float = 0.75,
                         max_candidates: int = 500) -> list[StemCandidate]:
    """Find compact, vertically continuous clusters in the breast-height band."""
    xyz = np.asarray(xyz, dtype=float)
    hag = np.asarray(hag, dtype=float)
    half = band_thickness_m / 2.0
    with np.errstate(invalid="ignore"):
        band = (np.isfinite(hag) & (hag >= target_height_m - half)
                & (hag <= target_height_m + half))
    if band.sum() == 0:
        return []

    pts = xyz[band]
    xmin, ymin = pts[:, 0].min(), pts[:, 1].min()
    nx = int(np.ceil((pts[:, 0].max() - xmin) / cell_m)) + 2
    ny = int(np.ceil((pts[:, 1].max() - ymin) / cell_m)) + 2
    ix = np.clip(((pts[:, 0] - xmin) / cell_m).astype(int), 0, nx - 1)
    iy = np.clip(((pts[:, 1] - ymin) / cell_m).astype(int), 0, ny - 1)
    counts = np.zeros((nx, ny), dtype=np.int64)
    np.add.at(counts, (ix, iy), 1)
    occ = counts > 0

    labels, n_lab = ndimage.label(occ, structure=np.ones((3, 3), dtype=int))
    if n_lab == 0:
        return []
    point_labels = labels[ix, iy]

    candidates: list[StemCandidate] = []
    for lab in range(1, n_lab + 1):
        sel = point_labels == lab
        n = int(sel.sum())
        if n == 0:
            continue
        sub = pts[sel]
        ex = float(sub[:, 0].max() - sub[:, 0].min())
        ey = float(sub[:, 1].max() - sub[:, 1].min())
        approx_d = float(max(ex, ey))
        elong = float(max(ex, ey) / max(min(ex, ey), 1e-6))
        cx, cy = float(np.median(sub[:, 0])), float(np.median(sub[:, 1]))
        cand = StemCandidate(
            candidate_id=f"cand_{lab:04d}", center_xy=(cx, cy), n_points=n,
            extent_x_m=ex, extent_y_m=ey, approx_diameter_m=approx_d,
            elongation=elong, continuity_fraction=0.0,
            continuity_heights_m=list(continuity_heights_m))
        if n < min_points:
            cand.rejection_reasons.append(f"only_{n}_points")
        if approx_d < min_diameter_m:
            cand.rejection_reasons.append(f"extent_{approx_d:.3f}m_below_minimum")
        if approx_d > max_diameter_m:
            cand.rejection_reasons.append(f"extent_{approx_d:.2f}m_above_maximum")
        if elong > max_elongation:
            cand.rejection_reasons.append(f"elongation_{elong:.1f}_above_limit")
        candidates.append(cand)

    # Vertical continuity, evaluated only for clusters that survived so far, since
    # it needs a pass over the wider cloud per candidate.
    survivors = [c for c in candidates if not c.rejection_reasons]
    survivors.sort(key=lambda c: -c.n_points)
    survivors = survivors[:max_candidates]
    for cand in survivors:
        hits = 0
        for h in continuity_heights_m:
            with np.errstate(invalid="ignore"):
                m = (np.isfinite(hag) & (np.abs(hag - h) <= half)
                     & (np.abs(xyz[:, 0] - cand.center_xy[0]) <= continuity_radius_m)
                     & (np.abs(xyz[:, 1] - cand.center_xy[1]) <= continuity_radius_m))
            if int(m.sum()) >= max(20, min_points // 8):
                hits += 1
        cand.continuity_fraction = float(hits / len(continuity_heights_m))
        if cand.continuity_fraction < min_continuity_fraction:
            cand.rejection_reasons.append(
                f"vertical_continuity_{cand.continuity_fraction:.0%}_below_"
                f"{min_continuity_fraction:.0%}")
        cand.accepted = not cand.rejection_reasons

    return sorted(candidates, key=lambda c: (not c.accepted, -c.n_points))
