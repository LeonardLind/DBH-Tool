"""Height normalisation: height above local ground.

    HAG(p) = Z(p) - Z_ground(X(p), Y(p))

Every height used downstream is a HAG, never a raw Z. On this project's test data
the ground spans roughly 30 m of elevation across a 60 m plot, so a global Z slice
at 1.30 m would miss almost every stem.
"""
from __future__ import annotations

import numpy as np

from .dtm import GroundGrid


def height_above_ground(xyz: np.ndarray, grid: GroundGrid) -> np.ndarray:
    """Return HAG for each point. NaN where the ground is unknown."""
    xyz = np.asarray(xyz, dtype=float)
    if len(xyz) == 0:
        return np.empty(0)
    gz = grid.elevation(xyz[:, 0], xyz[:, 1])
    return xyz[:, 2] - gz


def select_height_band(xyz: np.ndarray, hag: np.ndarray, target_m: float,
                       thickness_m: float) -> np.ndarray:
    """Boolean mask for points within a HAG band centred on ``target_m``."""
    half = thickness_m / 2.0
    with np.errstate(invalid="ignore"):
        return np.isfinite(hag) & (hag >= target_m - half) & (hag <= target_m + half)
