"""Resampling stability of a fitted diameter.

A model whose diameter swings under small changes to the point sample is
geometrically unstable, regardless of how good its residuals look. This is the
main quantitative defence against the "excellent RMSE on a short arc" failure:
residuals say how well the model describes the points it was given, while the
bootstrap says how much the answer depends on *which* points those were.
"""
from __future__ import annotations

from typing import Callable

import numpy as np


def bootstrap_diameter(xy: np.ndarray, fitter: Callable, n_resamples: int = 200,
                       seed: int = 43, sample_fraction: float = 1.0,
                       max_points: int = 5000) -> dict:
    """Refit ``fitter`` on resampled point sets and summarise the diameters.

    ``sample_fraction`` of 1.0 with replacement is the classic bootstrap. Point
    sets are capped at ``max_points`` (seeded subsample) because a terrestrial
    slice can hold tens of thousands of points and the sampling distribution is
    already well estimated long before that.
    """
    xy = np.asarray(xy, dtype=float)
    out = {
        "n_resamples": int(n_resamples),
        "n_success": 0,
        "mean_m": float("nan"),
        "median_m": float("nan"),
        "std_m": float("nan"),
        "p2_5_m": float("nan"),
        "p97_5_m": float("nan"),
        "seed": int(seed),
    }
    n = len(xy)
    if n < 6 or n_resamples < 2:
        return out
    rng = np.random.default_rng(seed)
    if n > max_points:
        xy = xy[rng.choice(n, size=max_points, replace=False)]
        n = max_points
        out["subsampled_to"] = int(max_points)
    k = max(6, int(round(sample_fraction * n)))

    diams = []
    for _ in range(int(n_resamples)):
        idx = rng.integers(0, n, size=k)
        try:
            fit = fitter(xy[idx])
        except Exception:
            continue
        if fit is not None and fit.diameter_m is not None and np.isfinite(fit.diameter_m):
            diams.append(float(fit.diameter_m))
    if len(diams) < 2:
        return out
    d = np.asarray(diams)
    out.update({
        "n_success": int(len(d)),
        "mean_m": float(d.mean()),
        "median_m": float(np.median(d)),
        "std_m": float(d.std(ddof=1)),
        "p2_5_m": float(np.percentile(d, 2.5)),
        "p97_5_m": float(np.percentile(d, 97.5)),
    })
    return out
