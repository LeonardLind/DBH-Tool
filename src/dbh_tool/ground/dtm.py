"""Local ground surface from lowest points, with low-outlier rejection.

This is the primary ground model for dense terrestrial scans (docs DEC-006). The
approach is deliberately transparent:

1. lowest Z per grid cell,
2. reject cells that sit implausibly far below their neighbourhood (scanner noise
   and multipath returns below the true surface, which a plain minimum would
   happily adopt as ground),
3. fill rejected and empty cells by interpolation from surviving neighbours,
4. light median smoothing.

Every cell keeps a quality record, because a DBH is only as trustworthy as the
ground estimate under that particular stem. A tree standing on interpolated
ground is not the same measurement as a tree standing on observed ground, and the
difference must reach the output.

The alternative classifiers (PDAL CSF/SMRF) are not implemented here; the module
boundary is the ``GroundGrid`` contract, so they can be added as competing
implementations and benchmarked on final DBH error.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage


@dataclass
class GroundGrid:
    """A rasterised ground surface with per-cell provenance.

    ``z`` holds the surface elevation; ``observed`` marks cells whose elevation
    came from real low points rather than interpolation.
    """

    z: np.ndarray                 # (nx, ny) surface elevation, NaN where unknown
    observed: np.ndarray          # (nx, ny) bool: elevation from observed points
    count: np.ndarray             # (nx, ny) int: points that fell in the cell
    origin: tuple[float, float]   # (x, y) of the lower-left cell corner
    cell_m: float
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape

    def cell_index(self, x, y):
        """Fractional cell coordinates for world positions."""
        fx = (np.asarray(x, dtype=float) - self.origin[0]) / self.cell_m - 0.5
        fy = (np.asarray(y, dtype=float) - self.origin[1]) / self.cell_m - 0.5
        return fx, fy

    def elevation(self, x, y) -> np.ndarray:
        """Bilinearly interpolated ground elevation at world (x, y).

        Cell values are treated as samples at cell centres. Positions outside the
        grid are clamped to the edge, which is safe here because callers only ask
        about locations inside the scanned area.
        """
        fx, fy = self.cell_index(x, y)
        nx, ny = self.z.shape
        fx = np.clip(fx, 0, nx - 1)
        fy = np.clip(fy, 0, ny - 1)
        x0 = np.floor(fx).astype(int)
        y0 = np.floor(fy).astype(int)
        x1 = np.minimum(x0 + 1, nx - 1)
        y1 = np.minimum(y0 + 1, ny - 1)
        tx, ty = fx - x0, fy - y0
        z = self.z
        # NaN-tolerant bilinear blend: weights of unknown corners are dropped.
        vals = np.stack([z[x0, y0], z[x1, y0], z[x0, y1], z[x1, y1]])
        wts = np.stack([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty])
        good = np.isfinite(vals)
        wts = np.where(good, wts, 0.0)
        tot = wts.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(tot > 0, np.nansum(np.where(good, vals * wts, 0.0), axis=0) / tot,
                           np.nan)
        return out

    def observed_fraction(self, x, y, radius_m: float) -> float:
        """Fraction of cells within ``radius_m`` of (x, y) that were observed."""
        fx, fy = self.cell_index(x, y)
        rad = max(1, int(np.ceil(radius_m / self.cell_m)))
        nx, ny = self.z.shape
        i0, i1 = max(0, int(fx) - rad), min(nx, int(fx) + rad + 1)
        j0, j1 = max(0, int(fy) - rad), min(ny, int(fy) + rad + 1)
        window = self.observed[i0:i1, j0:j1]
        return float(window.mean()) if window.size else 0.0

    def slope_deg(self, x, y, radius_m: float = 1.0) -> float:
        """Local slope from a plane fitted to nearby grid cells."""
        pts = self.cells_near(x, y, radius_m)
        if len(pts) < 4:
            return float("nan")
        A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
        try:
            coef, *_ = np.linalg.lstsq(A, pts[:, 2], rcond=None)
        except np.linalg.LinAlgError:
            return float("nan")
        return float(np.degrees(np.arctan(np.hypot(coef[0], coef[1]))))

    def cells_near(self, x, y, radius_m: float) -> np.ndarray:
        """World-coordinate (x, y, z) of grid cell centres within a radius."""
        rad = max(1, int(np.ceil(radius_m / self.cell_m)))
        fx, fy = self.cell_index(x, y)
        nx, ny = self.z.shape
        i0, i1 = max(0, int(fx) - rad), min(nx, int(fx) + rad + 1)
        j0, j1 = max(0, int(fy) - rad), min(ny, int(fy) + rad + 1)
        ii, jj = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1), indexing="ij")
        zz = self.z[i0:i1, j0:j1]
        wx = self.origin[0] + (ii + 0.5) * self.cell_m
        wy = self.origin[1] + (jj + 0.5) * self.cell_m
        keep = np.isfinite(zz) & (np.hypot(wx - x, wy - y) <= radius_m)
        return np.column_stack([wx[keep], wy[keep], zz[keep]])

    def to_meta(self) -> dict:
        obs = float(self.observed.mean())
        return {
            "method": self.meta.get("method", "local_minimum_grid"),
            "cell_m": self.cell_m,
            "shape": list(self.shape),
            "origin": list(self.origin),
            "observed_cell_fraction": obs,
            **{k: v for k, v in self.meta.items() if k != "method"},
        }


def build_ground_grid(source, cell_m: float = 0.5, despike_radius_cells: int = 2,
                      despike_tolerance_m: float = 0.35, smooth_iterations: int = 2,
                      bounds: tuple[float, float, float, float] | None = None,
                      method: str = "local_minimum_grid") -> GroundGrid:
    """Build a ground surface from a point source.

    ``source`` may be an (N, 3) array or any iterable of (N, 3) chunks, so the
    same code serves an in-memory crop and a streamed multi-gigabyte file.
    """
    chunks = [np.asarray(source, dtype=float)] if isinstance(source, np.ndarray) else source
    chunks = iter(chunks)
    first = next(chunks, None)
    if first is None or len(first) == 0:
        raise ValueError("no points supplied for ground grid")

    if bounds is None:
        # One extra pass is avoided by growing the grid lazily is not worth the
        # complexity; instead the caller passes bounds (from the LAS header) for
        # streamed input. For in-memory input the bounds are known directly.
        bounds = (first[:, 0].min(), first[:, 0].max(),
                  first[:, 1].min(), first[:, 1].max())
    xmin, xmax, ymin, ymax = (float(v) for v in bounds)
    nx = max(1, int(np.ceil((xmax - xmin) / cell_m)) + 1)
    ny = max(1, int(np.ceil((ymax - ymin) / cell_m)) + 1)

    zmin = np.full(nx * ny, np.inf)
    count = np.zeros(nx * ny, dtype=np.int64)

    def accumulate(xyz):
        x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        ix = np.clip(((x - xmin) / cell_m).astype(np.int64), 0, nx - 1)
        iy = np.clip(((y - ymin) / cell_m).astype(np.int64), 0, ny - 1)
        flat = ix * ny + iy
        np.minimum.at(zmin, flat, z)
        np.add.at(count, flat, 1)

    accumulate(first)
    for chunk in chunks:
        if len(chunk):
            accumulate(np.asarray(chunk, dtype=float))

    zmin = zmin.reshape(nx, ny)
    count = count.reshape(nx, ny)
    filled = count > 0
    z = np.where(filled, zmin, np.nan)

    # --- despike: reject cells far below their neighbourhood median ---------
    # A plain per-cell minimum adopts any sub-surface noise return as ground.
    # Comparing against a robust neighbourhood level catches those without
    # flattening genuine terrain, because real slopes vary smoothly.
    size = 2 * int(despike_radius_cells) + 1
    big = np.where(filled, z, np.nanmax(z[filled]) if filled.any() else 0.0)
    neigh_med = ndimage.median_filter(big, size=size, mode="nearest")
    spike = filled & (z < neigh_med - despike_tolerance_m)
    observed = filled & ~spike
    n_spikes = int(spike.sum())

    # --- fill unobserved cells by nearest-observed elevation ---------------
    z_obs = np.where(observed, z, np.nan)
    if not observed.any():
        raise ValueError("no observed ground cells survived despiking")
    idx = ndimage.distance_transform_edt(~observed, return_distances=False,
                                         return_indices=True)
    z_filled = z_obs[tuple(idx)]
    fill_distance_cells = ndimage.distance_transform_edt(~observed)

    # --- smooth, but only using observed-or-filled values ------------------
    z_smooth = z_filled
    for _ in range(max(0, int(smooth_iterations))):
        z_smooth = ndimage.median_filter(z_smooth, size=3, mode="nearest")
    # Observed cells keep more of their own value than smoothing would allow:
    # blending 50/50 preserves real micro-relief while removing raster noise.
    z_final = np.where(observed, 0.5 * z_filled + 0.5 * z_smooth, z_smooth)

    return GroundGrid(
        z=z_final, observed=observed, count=count,
        origin=(xmin, ymin), cell_m=float(cell_m),
        meta={
            "method": method,
            "despike_radius_cells": int(despike_radius_cells),
            "despike_tolerance_m": float(despike_tolerance_m),
            "smooth_iterations": int(smooth_iterations),
            "n_spike_cells_rejected": n_spikes,
            "n_cells_with_points": int(filled.sum()),
            "max_fill_distance_cells": float(np.nanmax(fill_distance_cells)),
            "bounds": [xmin, xmax, ymin, ymax],
        },
    )
