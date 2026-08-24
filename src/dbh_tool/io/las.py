"""LAS/LAZ reading with explicit unit and coordinate validation.

The tool never assumes metres. A point cloud in feet, or one carrying a
projected CRS with a vertical unit different from the horizontal one, would
silently produce a wrong 1.30 m slice, so :func:`inspect_las` reports what can be
determined and flags what cannot.

Large files are handled by chunked passes: cropping a region of interest requires
a full pass because terrestrial exports are generally not spatially sorted, but a
pass is cheap (a 35 M point, 0.9 GB file reads in a few seconds) and needs only
chunk-sized memory.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import laspy
import numpy as np

DEFAULT_CHUNK = 5_000_000

# A terrestrial or mobile scan of a forest plot has an extent of tens of metres.
# An extent far outside this range suggests the units are not metres.
PLAUSIBLE_EXTENT_M = (1.0, 5000.0)


@dataclass
class LasInfo:
    """Header summary plus validation findings for one LAS/LAZ file."""

    path: str
    point_count: int
    version: str
    point_format: int
    mins: tuple[float, float, float]
    maxs: tuple[float, float, float]
    scales: tuple[float, float, float]
    offsets: tuple[float, float, float]
    crs_wkt: str | None
    has_classification: bool
    classifications: dict[int, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    file_sha256_head: str = ""

    @property
    def extent(self) -> tuple[float, float, float]:
        return tuple(float(a - b) for a, b in zip(self.maxs, self.mins))

    def to_dict(self) -> dict:
        d = {
            "path": self.path,
            "point_count": self.point_count,
            "version": self.version,
            "point_format": self.point_format,
            "mins": list(self.mins),
            "maxs": list(self.maxs),
            "extent_m": list(self.extent),
            "scales": list(self.scales),
            "offsets": list(self.offsets),
            "crs_wkt": self.crs_wkt,
            "has_classification": self.has_classification,
            "classifications": {str(k): v for k, v in self.classifications.items()},
            "warnings": self.warnings,
            "file_sha256_head": self.file_sha256_head,
        }
        return d


def _hash_head(path: Path, n_bytes: int = 1 << 20) -> str:
    """Hash the header plus the first megabyte of points.

    A full-file hash of a multi-gigabyte cloud costs more than it is worth for
    provenance; a head hash plus size is enough to detect a changed input, and the
    truncation is stated in the field name.
    """
    h = hashlib.sha256()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(n_bytes))
    return h.hexdigest()[:32]


def inspect_las(path: str | Path, sample_points: int = 2_000_000) -> LasInfo:
    """Read the header, sample classifications, and validate units/coordinates."""
    p = Path(path)
    with laspy.open(str(p)) as f:
        h = f.header
        crs = None
        try:
            c = h.parse_crs()
            crs = c.to_wkt() if c is not None else None
        except Exception:  # pragma: no cover - laspy raises various CRS errors
            crs = None
        info = LasInfo(
            path=str(p),
            point_count=int(h.point_count),
            version=f"{h.version.major}.{h.version.minor}",
            point_format=int(h.point_format.id),
            mins=tuple(float(v) for v in h.mins),
            maxs=tuple(float(v) for v in h.maxs),
            scales=tuple(float(v) for v in h.scales),
            offsets=tuple(float(v) for v in h.offsets),
            crs_wkt=crs,
            has_classification=False,
            file_sha256_head=_hash_head(p),
        )
        n = min(sample_points, info.point_count)
        if n > 0:
            chunk = next(f.chunk_iterator(n))
            cls = np.asarray(chunk.classification)
            vals, counts = np.unique(cls, return_counts=True)
            info.classifications = {int(v): int(c) for v, c in zip(vals, counts)}
            info.has_classification = bool(np.any(cls != 0))

    ex = info.extent
    if crs is None:
        info.warnings.append(
            "no CRS in file: units cannot be confirmed from metadata, assuming metres")
    if not (PLAUSIBLE_EXTENT_M[0] <= max(ex[0], ex[1]) <= PLAUSIBLE_EXTENT_M[1]):
        info.warnings.append(
            f"horizontal extent {max(ex[0], ex[1]):.1f} is outside the plausible "
            f"range {PLAUSIBLE_EXTENT_M} for a metre-unit forest plot: check units")
    if ex[2] > max(ex[0], ex[1]) * 2:
        info.warnings.append(
            "vertical extent greatly exceeds horizontal extent: check axis order/units")
    if not info.has_classification:
        info.warnings.append(
            "classification field is empty: ground must be derived by this tool")
    return info


class LasSource:
    """Chunked access to a LAS/LAZ file.

    Every method makes one pass over the file and holds only chunk-sized arrays,
    so memory use is independent of file size.
    """

    def __init__(self, path: str | Path, chunk_size: int = DEFAULT_CHUNK):
        self.path = Path(path)
        self.chunk_size = int(chunk_size)
        self.info = inspect_las(self.path)

    def iter_xyz(self):
        """Yield (N, 3) float64 arrays of scaled coordinates, chunk by chunk."""
        with laspy.open(str(self.path)) as f:
            for chunk in f.chunk_iterator(self.chunk_size):
                yield np.column_stack([np.asarray(chunk.x), np.asarray(chunk.y),
                                       np.asarray(chunk.z)])

    def crop_cylinder(self, center_xy, radius_m: float,
                      z_range: tuple[float, float] | None = None) -> np.ndarray:
        """Return all points within a vertical cylinder about ``center_xy``.

        A cylinder rather than a bounding box: the region of interest around a
        stem is radial, and a box would bias which neighbours are included by
        direction.
        """
        cx, cy = float(center_xy[0]), float(center_xy[1])
        r2 = float(radius_m) ** 2
        out = []
        for xyz in self.iter_xyz():
            dx, dy = xyz[:, 0] - cx, xyz[:, 1] - cy
            keep = (dx * dx + dy * dy) <= r2
            if z_range is not None:
                keep &= (xyz[:, 2] >= z_range[0]) & (xyz[:, 2] <= z_range[1])
            if keep.any():
                out.append(xyz[keep])
        if not out:
            return np.empty((0, 3))
        return np.vstack(out)

    def crop_many(self, centers_xy, radius_m: float) -> list[np.ndarray]:
        """Crop several cylinders in a **single pass** over the file.

        Cropping one tree at a time costs one full pass each, which is fine for a
        handful of trees and wasteful for a plot or for a parameter sweep that
        re-measures the same trees many times. This collects every region of
        interest in one pass, so cost is one read regardless of tree count.

        Returns one array per centre, in the order given.
        """
        cent = np.asarray(centers_xy, dtype=float).reshape(-1, 2)
        if len(cent) == 0:
            return []
        r2 = float(radius_m) ** 2
        buckets: list[list[np.ndarray]] = [[] for _ in range(len(cent))]
        for xyz in self.iter_xyz():
            for i, (cx, cy) in enumerate(cent):
                dx, dy = xyz[:, 0] - cx, xyz[:, 1] - cy
                keep = (dx * dx + dy * dy) <= r2
                if keep.any():
                    buckets[i].append(xyz[keep])
        return [np.vstack(b) if b else np.empty((0, 3)) for b in buckets]

    def crop_box(self, xmin, xmax, ymin, ymax) -> np.ndarray:
        """Return all points inside an XY bounding box."""
        out = []
        for xyz in self.iter_xyz():
            keep = ((xyz[:, 0] >= xmin) & (xyz[:, 0] <= xmax)
                    & (xyz[:, 1] >= ymin) & (xyz[:, 1] <= ymax))
            if keep.any():
                out.append(xyz[keep])
        if not out:
            return np.empty((0, 3))
        return np.vstack(out)

    def decimate(self, step: int = 20) -> np.ndarray:
        """Every ``step``-th point, as a float32 array for interactive work.

        Deterministic stride rather than random sampling, so repeated runs give
        identical previews. Not for final fits: decimation biases point density.
        """
        return np.vstack([xyz[::step].astype(np.float32) for xyz in self.iter_xyz()])


def load_points(path: str | Path) -> np.ndarray:
    """Load an entire small cloud as (N, 3) float64. For fixtures and tests."""
    with laspy.open(str(path)) as f:
        las = f.read()
    return np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)])


def write_points(path: str | Path, xyz: np.ndarray, scale: float = 0.001) -> None:
    """Write an (N, 3) array to LAS 1.2 point format 2. Used to build fixtures."""
    xyz = np.asarray(xyz, dtype=float)
    header = laspy.LasHeader(version="1.2", point_format=2)
    header.scales = [scale, scale, scale]
    header.offsets = xyz.min(axis=0) if len(xyz) else [0.0, 0.0, 0.0]
    las = laspy.LasData(header)
    las.x, las.y, las.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    las.write(str(path))
