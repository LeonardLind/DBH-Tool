"""Reproducible run configuration.

Docs 03 section 9 asks that uncalibrated parameters be left ``null`` rather than
filled with invented production defaults. A literally null-valued config cannot
run, so this module takes the equivalent but executable position:

* every threshold has a documented working default, and
* every default that has **not** been validated against field measurements is
  listed in :data:`PROVISIONAL_PARAMETERS` and stamped into the provenance of
  every export.

So a result can never be read as calibrated when it is not. Removing a name from
:data:`PROVISIONAL_PARAMETERS` is a deliberate act that belongs in the decisions
log together with the validation evidence.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GroundConfig:
    """Local ground surface estimation."""

    method: str = "local_minimum_grid"     # local_minimum_grid | csf | smrf (see DEC-006)
    cell_m: float = 0.50                   # grid cell for the lowest-point surface
    despike_radius_cells: int = 2          # neighbourhood for low-outlier rejection
    despike_tolerance_m: float = 0.35      # cell is a spike if this far below neighbours
    smooth_iterations: int = 2             # median smoothing passes over the surface
    local_plane_radius_m: float = 2.0      # radius for the per-stem robust plane fit
    local_plane_min_points: int = 40
    max_local_slope_deg: float = 45.0      # beyond this, ground quality is degraded
    max_ground_roughness_m: float = 0.15   # residual scatter above which ground is uncertain


@dataclass
class SliceConfig:
    """Cross-section extraction."""

    target_height_m: float = 1.30
    thickness_m: float = 0.10
    supporting_heights_m: list[float] = field(
        default_factory=lambda: [1.20, 1.25, 1.30, 1.35, 1.40])
    geometry: str = "both"                 # horizontal | stem_normal | both (DEC-007)
    # Which geometry supplies the reported DBH. "horizontal" matches the forestry
    # convention and most published TLS work; "stem_normal" is geometrically
    # correct for a leaning stem. Both are always computed and exported; this only
    # chooses the headline number. Open question in docs 03.
    primary_geometry: str = "horizontal"
    # Section points are kept within this multiple of the seeded stem radius.
    # 1.5 admits a 50% flute amplitude while excluding most neighbouring clutter.
    section_radius_factor: float = 1.5
    min_points: int = 60


@dataclass
class AxisConfig:
    """Local stem axis estimation, used for lean correction and diagnostics."""

    enabled: bool = True
    lower_height_m: float = 0.80
    upper_height_m: float = 2.20
    n_bins: int = 8
    min_points_per_bin: int = 30
    max_tilt_deg: float = 45.0             # beyond this the axis estimate is not trusted


@dataclass
class RansacConfig:
    residual_threshold_m: float = 0.010
    max_trials: int = 500
    min_inlier_fraction: float = 0.30
    random_seed: int = 42
    max_scoring_points: int = 20000
    refit: str = "geometric"


@dataclass
class CoverageConfig:
    angular_bin_deg: float = 5.0
    min_points_per_bin: int = 1
    min_coverage_fraction: float = 0.60    # below this: REVIEW_REQUIRED
    max_gap_deg: float = 120.0             # above this: REVIEW_REQUIRED


@dataclass
class OutlineConfig:
    method: str = "radial_median_polygon"
    n_sectors: int = 72
    min_points_per_sector: int = 3
    max_bridge_gap_deg: float = 20.0
    min_occupied_fraction: float = 0.75


@dataclass
class BootstrapConfig:
    enabled: bool = True
    n_resamples: int = 200
    sample_fraction: float = 1.0           # 1.0 with replacement is the classic bootstrap
    random_seed: int = 43


@dataclass
class DecisionConfig:
    """Model-selection behaviour. Phase 1 recommends; it does not decide."""

    automatic_selection: bool = False
    # Which of the three height strategies supplies the reported DBH. All three are
    # always computed and exported, so this is the switch for docs 02 experiment D:
    #   single_slice       the target-height section on its own (the convention)
    #   profile_median     median across the supporting heights
    #   taper_interpolated local linear taper evaluated at exactly the target height
    primary_dbh_source: str = "single_slice"
    max_model_disagreement_m: float = 0.02       # 2 cm across models -> low confidence
    max_cross_height_std_m: float = 0.015
    max_bootstrap_std_m: float = 0.010
    max_rmse_m: float = 0.020
    ellipse_axis_ratio_circular_max: float = 1.10   # at/below this, treat as circular
    lean_explains_ellipse_tolerance: float = 0.03   # axis-ratio agreement with 1/cos(tilt)
    buttress_taper_threshold_per_m: float = 0.10    # |dD/dh| above this flags a deformity
    # Shape attribution. An outline is only called irregular when it is both
    # measurably non-convex and rough relative to its own radius. On the sample
    # scan these provisional values fire for ordinary bark roughness, so they are
    # prime calibration candidates (docs 03 known issues).
    irregular_convexity_deficit_min: float = 0.02
    irregular_roughness_ratio_min: float = 0.05
    # Contamination detection: outliers that are mostly outside a robust circle and
    # confined to a limited angular sector look like an attached liana or branch
    # rather than stem shape.
    contamination_outlier_fraction_min: float = 0.05
    contamination_outside_fraction_min: float = 0.75
    contamination_angular_coverage_max: float = 0.40
    contamination_min_lobes: int = 3
    # Shell-thickness test: a stem surface spans only bark roughness plus sensor
    # noise in radius at a given angle, while vegetation spans centimetres.
    contamination_sector_spread_m: float = 0.015
    contamination_thick_sector_fraction_max: float = 0.25
    # Outward radial excess beyond what bark roughness explains.
    contamination_radial_excess_m: float = 0.010


@dataclass
class PreprocessConfig:
    remove_isolated_points: bool = True
    isolation_radius_m: float = 0.05
    isolation_min_neighbours: int = 3


@dataclass
class RunConfig:
    """Top-level, serialisable configuration for one run."""

    units: str = "meters"
    ground: GroundConfig = field(default_factory=GroundConfig)
    slice: SliceConfig = field(default_factory=SliceConfig)
    axis: AxisConfig = field(default_factory=AxisConfig)
    ransac_circle: RansacConfig = field(default_factory=RansacConfig)
    coverage: CoverageConfig = field(default_factory=CoverageConfig)
    outline: OutlineConfig = field(default_factory=OutlineConfig)
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)
    decision: DecisionConfig = field(default_factory=DecisionConfig)
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_yaml(self, path: str | Path | None = None) -> str:
        text = yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    @classmethod
    def from_dict(cls, data: dict | None) -> RunConfig:
        return _build(cls, data or {})

    @classmethod
    def load(cls, path: str | Path) -> RunConfig:
        p = Path(path)
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw) if p.suffix.lower() == ".json" else yaml.safe_load(raw)
        return cls.from_dict(data)


def _build(dc_type, data: dict):
    """Recursively construct nested dataclasses, rejecting unknown keys."""
    kwargs = {}
    known = {f.name: f for f in fields(dc_type)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown config keys for {dc_type.__name__}: {sorted(unknown)}")
    for name, f in known.items():
        if name not in data:
            continue
        value = data[name]
        if is_dataclass(f.type) and isinstance(value, dict):
            kwargs[name] = _build(f.type, value)
        elif isinstance(value, dict) and name in _NESTED:
            kwargs[name] = _build(_NESTED[name], value)
        else:
            kwargs[name] = value
    return dc_type(**kwargs)


_NESTED: dict[str, Any] = {
    "ground": GroundConfig,
    "slice": SliceConfig,
    "axis": AxisConfig,
    "ransac_circle": RansacConfig,
    "coverage": CoverageConfig,
    "outline": OutlineConfig,
    "bootstrap": BootstrapConfig,
    "decision": DecisionConfig,
    "preprocess": PreprocessConfig,
}


# Parameters whose defaults are working guesses, not validated results. Every
# export carries this list so that no downstream reader can mistake a provisional
# threshold for a calibrated one. Entries are removed only with evidence, and the
# removal is recorded in docs/03 decisions log.
PROVISIONAL_PARAMETERS: tuple[str, ...] = (
    "ground.cell_m",
    "ground.despike_tolerance_m",
    "ground.local_plane_radius_m",
    "ground.max_ground_roughness_m",
    "slice.thickness_m",
    "slice.supporting_heights_m",
    "slice.primary_geometry",
    "slice.section_radius_factor",
    "decision.primary_dbh_source",
    "axis.lower_height_m",
    "axis.upper_height_m",
    "ransac_circle.residual_threshold_m",
    "ransac_circle.min_inlier_fraction",
    "coverage.angular_bin_deg",
    "coverage.min_coverage_fraction",
    "coverage.max_gap_deg",
    "outline.n_sectors",
    "outline.max_bridge_gap_deg",
    "outline.min_occupied_fraction",
    "decision.max_model_disagreement_m",
    "decision.max_cross_height_std_m",
    "decision.max_bootstrap_std_m",
    "decision.max_rmse_m",
    "decision.ellipse_axis_ratio_circular_max",
    "decision.buttress_taper_threshold_per_m",
    "decision.irregular_convexity_deficit_min",
    "decision.irregular_roughness_ratio_min",
    "decision.contamination_outlier_fraction_min",
    "decision.contamination_outside_fraction_min",
    "decision.contamination_angular_coverage_max",
    "decision.contamination_sector_spread_m",
    "decision.contamination_thick_sector_fraction_max",
    "decision.contamination_radial_excess_m",
)

# The measurement convention actually applied. Recorded with every measurement so
# that a stored DBH is never ambiguous about what it means (docs 02, section 1).
MEASUREMENT_CONVENTION = {
    "nominal_height_m": 1.30,
    "height_reference": (
        "one scalar datum per stem: the robust local ground plane evaluated at the "
        "stem centre, not a global Z plane and not per-point height above the "
        "ground raster"),
    "section_plane": (
        "the horizontal section is geometrically horizontal at datum + target "
        "height; the stem-normal section is perpendicular to the fitted stem axis"),
    "diameter_reference": "over-bark, from the outer point surface as scanned",
    "section_geometry": "reported for both horizontal and stem-normal cuts",
    "buttress_rule": (
        "no DBH is reported when a deformity is detected at breast height; the tree "
        "is flagged INVALID_MEASUREMENT_HEIGHT for operator decision"),
}
