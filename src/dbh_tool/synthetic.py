"""Synthetic cross-sections and stems with known ground truth.

These generators exist so that geometric correctness can be verified in isolation
from forest segmentation and ground-classification complexity (docs 03, M1). Every
generator takes an explicit ``seed`` so that failures are reproducible.

Angles are radians, lengths metres, and the returned ``truth`` dict always carries
the quantities a test is allowed to assert on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SyntheticSection:
    """A synthetic cross-section plus the ground truth that generated it."""

    xy: np.ndarray
    truth: dict = field(default_factory=dict)
    label: str = ""

    def __len__(self) -> int:
        return len(self.xy)


def _arc_angles(rng, n: int, arc_deg: float, start_deg: float = 0.0) -> np.ndarray:
    return np.radians(start_deg) + rng.uniform(0.0, np.radians(arc_deg), n)


def circle_section(diameter_m: float = 0.38, n_points: int = 600,
                   noise_m: float = 0.0, arc_deg: float = 360.0,
                   start_deg: float = 0.0, center: tuple[float, float] = (0.0, 0.0),
                   outlier_fraction: float = 0.0,
                   outlier_offset_m: float = 0.08, seed: int = 0) -> SyntheticSection:
    """Points on a circle, optionally noisy, partial, and contaminated.

    ``outlier_fraction`` points are pushed radially outward by roughly
    ``outlier_offset_m``, imitating branch or liana returns that sit outside the
    stem surface. Contamination is one-sided on purpose: real contaminants are
    almost always *outside* the stem, which is what biases ordinary fits outward.
    """
    rng = np.random.default_rng(seed)
    r = diameter_m / 2.0
    t = _arc_angles(rng, n_points, arc_deg, start_deg)
    xy = np.column_stack([center[0] + r * np.cos(t), center[1] + r * np.sin(t)])
    if noise_m > 0:
        xy = xy + rng.normal(0.0, noise_m, xy.shape)
    n_out = int(round(outlier_fraction * n_points))
    if n_out > 0:
        idx = rng.choice(n_points, size=n_out, replace=False)
        push = outlier_offset_m * (0.5 + rng.random(n_out))
        u = xy[idx] - np.asarray(center)
        u /= np.linalg.norm(u, axis=1, keepdims=True)
        xy[idx] = xy[idx] + u * push[:, None]
    return SyntheticSection(xy, {
        "shape": "circle",
        "diameter_m": float(diameter_m),
        "center_xy": tuple(float(c) for c in center),
        "arc_deg": float(arc_deg),
        "noise_m": float(noise_m),
        "outlier_fraction": float(outlier_fraction),
        "n_outliers": int(n_out),
        "area_m2": float(np.pi * r ** 2),
        "diameter_area_equiv_m": float(diameter_m),
        "diameter_convex_perimeter_equiv_m": float(diameter_m),
    }, label=f"circle_D{diameter_m}_arc{arc_deg:.0f}")


def ellipse_section(major_m: float = 0.44, minor_m: float = 0.32,
                    rotation_deg: float = 30.0, n_points: int = 600,
                    noise_m: float = 0.0, arc_deg: float = 360.0,
                    center: tuple[float, float] = (0.0, 0.0),
                    seed: int = 0) -> SyntheticSection:
    """Points on an ellipse, parameterised by full-axis diameters."""
    rng = np.random.default_rng(seed)
    a, b = major_m / 2.0, minor_m / 2.0
    th = np.radians(rotation_deg)
    t = _arc_angles(rng, n_points, arc_deg)
    u, v = a * np.cos(t), b * np.sin(t)
    ct, st = np.cos(th), np.sin(th)
    xy = np.column_stack([center[0] + u * ct - v * st, center[1] + u * st + v * ct])
    if noise_m > 0:
        xy = xy + rng.normal(0.0, noise_m, xy.shape)
    area = float(np.pi * a * b)
    return SyntheticSection(xy, {
        "shape": "ellipse",
        "major_diameter_m": float(major_m),
        "minor_diameter_m": float(minor_m),
        "axis_ratio": float(major_m / minor_m),
        "rotation_deg": float(rotation_deg) % 180.0,
        "center_xy": tuple(float(c) for c in center),
        "arc_deg": float(arc_deg),
        "area_m2": area,
        "diameter_area_equiv_m": float(2.0 * np.sqrt(a * b)),
        "diameter_mean_axes_m": float(a + b),
    }, label=f"ellipse_{major_m}x{minor_m}")


def fluted_section(mean_diameter_m: float = 0.50, n_lobes: int = 6,
                   flute_amplitude: float = 0.12, n_points: int = 1200,
                   noise_m: float = 0.002, arc_deg: float = 360.0,
                   center: tuple[float, float] = (0.0, 0.0),
                   seed: int = 0) -> SyntheticSection:
    """A fluted stem: radius r(theta) = R * (1 + amp * cos(n_lobes * theta)).

    Ground truth includes both the true polar area and the convex-hull perimeter,
    because those give *different* equivalent diameters and the distinction is
    exactly what a fluted stem is meant to test (see fitting/outline.py).
    """
    rng = np.random.default_rng(seed)
    R = mean_diameter_m / 2.0
    t = _arc_angles(rng, n_points, arc_deg)
    rr = R * (1.0 + flute_amplitude * np.cos(n_lobes * t))
    xy = np.column_stack([center[0] + rr * np.cos(t), center[1] + rr * np.sin(t)])
    if noise_m > 0:
        xy = xy + rng.normal(0.0, noise_m, xy.shape)
    # Exact polar area of r(theta) = R(1 + a cos(k theta)) over a full turn:
    # 0.5 * integral r^2 dtheta = pi * R^2 * (1 + a^2 / 2)
    area = float(np.pi * R ** 2 * (1.0 + flute_amplitude ** 2 / 2.0))
    dense_t = np.linspace(0.0, 2.0 * np.pi, 4000, endpoint=False)
    dense_r = R * (1.0 + flute_amplitude * np.cos(n_lobes * dense_t))
    dense = np.column_stack([dense_r * np.cos(dense_t), dense_r * np.sin(dense_t)])
    return SyntheticSection(xy, {
        "shape": "fluted",
        "mean_diameter_m": float(mean_diameter_m),
        "n_lobes": int(n_lobes),
        "flute_amplitude": float(flute_amplitude),
        "max_diameter_m": float(2.0 * R * (1.0 + flute_amplitude)),
        "min_diameter_m": float(2.0 * R * (1.0 - flute_amplitude)),
        "center_xy": tuple(float(c) for c in center),
        "arc_deg": float(arc_deg),
        "area_m2": area,
        "diameter_area_equiv_m": float(2.0 * np.sqrt(area / np.pi)),
        "boundary_xy": dense,
    }, label=f"fluted_D{mean_diameter_m}_k{n_lobes}")


def with_neighbour_cluster(section: SyntheticSection, offset_m: tuple[float, float] = (0.35, 0.0),
                           n_points: int = 200, spread_m: float = 0.04,
                           seed: int = 0) -> SyntheticSection:
    """Add a compact off-stem blob: a liana, a small neighbouring stem, or clutter."""
    rng = np.random.default_rng(seed)
    c = np.asarray(section.truth.get("center_xy", (0.0, 0.0)), dtype=float)
    blob = c + np.asarray(offset_m) + rng.normal(0.0, spread_m, (n_points, 2))
    truth = dict(section.truth)
    truth["contaminant_points"] = int(n_points)
    truth["contaminant_offset_m"] = tuple(float(v) for v in offset_m)
    return SyntheticSection(np.vstack([section.xy, blob]), truth,
                            label=section.label + "_with_neighbour")


def tilted_cylinder(diameter_m: float = 0.40, tilt_deg: float = 0.0,
                    azimuth_deg: float = 0.0, height_m: float = 3.0,
                    n_points: int = 200000, base_xy: tuple[float, float] = (0.0, 0.0),
                    base_z: float = 0.0, noise_m: float = 0.004,
                    arc_deg: float = 360.0, arc_start_deg: float = 0.0,
                    seed: int = 0) -> tuple[np.ndarray, dict]:
    """A 3D cylinder surface, optionally leaning, for testing height handling.

    Returns ``(xyz, truth)``. The cylinder axis passes through ``base_xy`` at
    ``base_z`` and is tilted ``tilt_deg`` from vertical towards ``azimuth_deg``.

    This is the generator behind the lean-bias check: a horizontal cut through a
    cylinder leaning by ``tilt_deg`` is an ellipse whose major axis is
    ``diameter_m / cos(tilt)``, so a horizontal-slice diameter is biased upward
    while a stem-normal slice is not.
    """
    rng = np.random.default_rng(seed)
    r = diameter_m / 2.0
    tilt, az = np.radians(tilt_deg), np.radians(azimuth_deg)
    axis = np.array([np.sin(tilt) * np.cos(az), np.sin(tilt) * np.sin(az), np.cos(tilt)])
    # Orthonormal frame spanning the circular cross-section.
    e1 = np.array([np.cos(az) * np.cos(tilt), np.sin(az) * np.cos(tilt), -np.sin(tilt)])
    e2 = np.cross(axis, e1)
    s = rng.uniform(0.0, height_m, n_points)          # distance along the axis
    t = np.radians(arc_start_deg) + rng.uniform(0.0, np.radians(arc_deg), n_points)
    base = np.array([base_xy[0], base_xy[1], base_z])
    xyz = (base + s[:, None] * axis
           + r * np.cos(t)[:, None] * e1 + r * np.sin(t)[:, None] * e2)
    if noise_m > 0:
        xyz = xyz + rng.normal(0.0, noise_m, xyz.shape)
    return xyz, {
        "diameter_m": float(diameter_m),
        "tilt_deg": float(tilt_deg),
        "azimuth_deg": float(azimuth_deg),
        "axis_unit": axis.tolist(),
        "base_xy": tuple(float(v) for v in base_xy),
        "base_z": float(base_z),
        "expected_horizontal_major_m": float(diameter_m / np.cos(tilt)),
        "expected_horizontal_axis_ratio": float(1.0 / np.cos(tilt)),
        "arc_deg": float(arc_deg),
    }


def sloped_ground(x_range=(-8.0, 8.0), y_range=(-8.0, 8.0), slope_deg: float = 15.0,
                  slope_azimuth_deg: float = 0.0, z0: float = 0.0,
                  spacing_m: float = 0.05, roughness_m: float = 0.01,
                  seed: int = 0) -> tuple[np.ndarray, dict]:
    """A planar sloped ground patch with roughness, sampled on a jittered grid.

    Used to verify that height normalisation follows local terrain instead of a
    global Z plane -- the failure mode that makes a naive 1.3 m slice wrong.
    """
    rng = np.random.default_rng(seed)
    gx = np.arange(x_range[0], x_range[1], spacing_m)
    gy = np.arange(y_range[0], y_range[1], spacing_m)
    X, Y = np.meshgrid(gx, gy)
    X = X.ravel() + rng.uniform(-spacing_m / 2, spacing_m / 2, X.size)
    Y = Y.ravel() + rng.uniform(-spacing_m / 2, spacing_m / 2, Y.size)
    slope = np.tan(np.radians(slope_deg))
    az = np.radians(slope_azimuth_deg)
    Z = z0 + slope * (X * np.cos(az) + Y * np.sin(az))
    if roughness_m > 0:
        Z = Z + rng.normal(0.0, roughness_m, Z.shape)
    xyz = np.column_stack([X, Y, Z])

    def ground_z(x, y):
        return z0 + slope * (np.asarray(x) * np.cos(az) + np.asarray(y) * np.sin(az))

    return xyz, {
        "slope_deg": float(slope_deg),
        "slope_azimuth_deg": float(slope_azimuth_deg),
        "z0": float(z0),
        "ground_z_fn": ground_z,
    }
