"""Shared contract for all cross-section models.

Every candidate model returns a :class:`FitResult` so that model comparison and
benchmark tables can be built without special-casing individual models.

Units are SI throughout the scientific core: metres and radians. Conversion to
centimetres/degrees happens only at export and display boundaries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

# Diameter sanity envelope. These are hard geometric-impossibility rejections,
# not statistical thresholds: outside this range the fit is not a tree stem.
MIN_PLAUSIBLE_DIAMETER_M = 0.02
MAX_PLAUSIBLE_DIAMETER_M = 4.00


@dataclass
class FitResult:
    """Outcome of fitting one geometric model to one cross-section.

    ``diameter_m`` is the model single-number diameter summary. Its meaning is
    model-dependent and is documented in ``diameter_definition``; consumers must
    never assume that two models ``diameter_m`` values are the same quantity.
    """

    model: str
    diameter_m: float | None = None
    diameter_definition: str = ""
    center_xy: tuple[float, float] | None = None
    rmse_m: float | None = None
    median_abs_residual_m: float | None = None
    max_abs_residual_m: float | None = None
    point_count: int = 0
    inlier_count: int | None = None
    inlier_fraction: float | None = None
    angular_coverage: float | None = None
    largest_gap_deg: float | None = None
    n_arcs: int | None = None
    bootstrap_std_m: float | None = None
    valid: bool = False
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["extra"] = jsonable(self.extra)
        if self.center_xy is not None:
            d["center_xy"] = [float(self.center_xy[0]), float(self.center_xy[1])]
        return d

    def invalidate(self, reason: str) -> FitResult:
        self.valid = False
        if reason not in self.warnings:
            self.warnings.append(reason)
        return self

    def warn(self, reason: str) -> FitResult:
        """Attach a caveat without invalidating the fit."""
        if reason not in self.warnings:
            self.warnings.append(reason)
        return self


def jsonable(obj):
    """Recursively convert numpy scalars and arrays so json.dump succeeds."""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def radial_residuals(xy: np.ndarray, cx: float, cy: float, r: float) -> np.ndarray:
    """Signed distance of each point from a circle of radius ``r`` about (cx, cy)."""
    return np.hypot(xy[:, 0] - cx, xy[:, 1] - cy) - r


def residual_stats(res: np.ndarray) -> dict[str, float]:
    """RMSE plus robust residual summaries.

    RMSE alone is a poor quality signal for partial arcs (docs 02, section 7), so
    robust companions are always reported alongside it.
    """
    res = np.asarray(res, dtype=float)
    if res.size == 0:
        return {
            "rmse_m": float("nan"),
            "median_abs_residual_m": float("nan"),
            "max_abs_residual_m": float("nan"),
        }
    a = np.abs(res)
    return {
        "rmse_m": float(np.sqrt(np.mean(res ** 2))),
        "median_abs_residual_m": float(np.median(a)),
        "max_abs_residual_m": float(a.max()),
    }


def check_plausible_diameter(fit: FitResult) -> FitResult:
    """Reject geometrically impossible diameters (docs 01: reject impossible models)."""
    d = fit.diameter_m
    if d is None or not np.isfinite(d):
        return fit.invalidate("diameter_not_finite")
    if d < MIN_PLAUSIBLE_DIAMETER_M:
        return fit.invalidate(f"diameter_below_{MIN_PLAUSIBLE_DIAMETER_M}m")
    if d > MAX_PLAUSIBLE_DIAMETER_M:
        return fit.invalidate(f"diameter_above_{MAX_PLAUSIBLE_DIAMETER_M}m")
    return fit


def as_xy(points) -> np.ndarray:
    """Validate and normalise a point set to a contiguous (N, 2) float array."""
    xy = np.asarray(points, dtype=float)
    if xy.ndim != 2 or xy.shape[1] < 2:
        raise ValueError(f"expected an (N, 2) point array, got shape {xy.shape}")
    xy = np.ascontiguousarray(xy[:, :2])
    if not np.all(np.isfinite(xy)):
        raise ValueError("point array contains non-finite values")
    return xy


def area_equivalent_diameter(area_m2: float) -> float:
    """Diameter of the circle with the same cross-sectional area."""
    if not np.isfinite(area_m2) or area_m2 <= 0:
        return float("nan")
    return 2.0 * float(np.sqrt(area_m2 / np.pi))


def perimeter_equivalent_diameter(perimeter_m: float) -> float:
    """Diameter of the circle with the same perimeter."""
    if not np.isfinite(perimeter_m) or perimeter_m <= 0:
        return float("nan")
    return float(perimeter_m / np.pi)
