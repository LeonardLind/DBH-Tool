"""Review figure for one measured tree.

The point of this figure is inspectability: an operator has to be able to see
*why* the models disagree, not just that they do. So the panels are chosen to
expose the failure modes the tool is guarding against -- partial coverage, radial
contamination, lean, and height instability -- rather than to look tidy.

matplotlib is used with the Agg backend so the same code runs headless in tests
and in batch export.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

MODEL_STYLE = {
    "outline_radial_median_inliers": dict(color="#00897b", ls="--", lw=1.4),
    "circle_algebraic": dict(color="#9e9e9e", ls=":", lw=1.2),
    "circle_taubin": dict(color="#7e57c2", ls="-.", lw=1.2),
    "circle_pratt": dict(color="#26a69a", ls="--", lw=1.3),
    "circle_geometric": dict(color="#1e88e5", ls="-", lw=1.8),
    "circle_ransac": dict(color="#e53935", ls="-", lw=1.8),
    "ellipse": dict(color="#fb8c00", ls="-", lw=1.8),
    "outline_radial_median": dict(color="#43a047", ls="-", lw=1.8),
}


def _circle_xy(cx, cy, r, n=361):
    t = np.linspace(0, 2 * np.pi, n)
    return cx + r * np.cos(t), cy + r * np.sin(t)


def model_boundary_xy(name: str, fit) -> np.ndarray | None:
    """Closed boundary polyline of one fitted model, as an (N, 2) array.

    Shared by the export figure and the GUI's section view. Both need to draw exactly
    the curve the model represents, and two implementations of "where is the ellipse"
    would eventually disagree about it -- which is precisely the kind of drift that
    makes a review figure untrustworthy.

    Returns ``None`` when the fit has no drawable boundary (no centre, or a model whose
    geometry is not a closed curve). A *rejected* fit still has a boundary and still
    returns one: seeing what a declined model claimed is the point of exporting it.
    """
    if fit is None or fit.center_xy is None:
        return None
    cx, cy = fit.center_xy
    if name == "ellipse":
        need = ("semi_major_m", "semi_minor_m", "rotation_deg")
        if not all(k in fit.extra for k in need):
            return None
        from ..fitting.ellipse import ellipse_boundary
        return ellipse_boundary(cx, cy, fit.extra["semi_major_m"],
                                fit.extra["semi_minor_m"],
                                np.radians(fit.extra["rotation_deg"]))
    if name.startswith("outline"):
        poly = fit.extra.get("outline_xy")
        if poly is None:
            return None
        poly = np.asarray(poly, dtype=float)
        if poly.ndim != 2 or len(poly) < 3:
            return None
        return np.vstack([poly, poly[:1]])       # close the ring
    if "radius_m" in fit.extra:
        bx, by = _circle_xy(cx, cy, fit.extra["radius_m"])
        return np.column_stack([bx, by])
    return None


def plot_measurement(measurement, xy: np.ndarray, out_path: str | Path,
                     title: str | None = None) -> Path:
    """Write a four-panel review figure for one measurement.

    ``xy`` is the cleaned target-height section in section-plane coordinates, the
    same array the models were fitted to.
    """
    fits = {f.model: f for f in measurement.candidate_results}
    fig = plt.figure(figsize=(16.5, 9.5))
    gs = fig.add_gridspec(2, 3, width_ratios=[1.35, 1.0, 1.0], height_ratios=[1.0, 1.0],
                          hspace=0.28, wspace=0.26)

    # ---------------- panel 1: section with every model overlaid ------------
    ax = fig.add_subplot(gs[:, 0])
    ax.scatter(xy[:, 0], xy[:, 1], s=1.5, c="#37474f", alpha=0.45, label="section points",
               rasterized=True)
    ransac = fits.get("circle_ransac")
    if ransac is not None and ransac.valid and ransac.center_xy is not None:
        d = np.hypot(xy[:, 0] - ransac.center_xy[0], xy[:, 1] - ransac.center_xy[1])
        thr = ransac.extra.get("residual_threshold_m", 0.01)
        outl = np.abs(d - ransac.extra["radius_m"]) > thr
        if outl.any():
            ax.scatter(xy[outl, 0], xy[outl, 1], s=4, facecolors="none",
                       edgecolors="#e53935", linewidths=0.4,
                       label=f"RANSAC outliers ({int(outl.sum())})", rasterized=True)

    for name, f in fits.items():
        if not f.valid or f.center_xy is None:
            continue
        st = MODEL_STYLE.get(name, dict(color="k", ls="-", lw=1.0))
        lbl = f"{name}  D={f.diameter_m * 100:.1f} cm"
        b = model_boundary_xy(name, f)
        if b is None:
            continue
        ax.plot(b[:, 0], b[:, 1], label=lbl, **st)
        ax.plot(*f.center_xy, marker="+", ms=6, color=st["color"])

    ax.set_aspect("equal")
    ax.set_xlabel("section X [m]")
    ax.set_ylabel("section Y [m]")
    ax.set_title(f"{measurement.tree_id} - {measurement.primary_geometry} section at "
                 f"{measurement.measurement_height_m:.2f} m above local ground")
    ax.legend(fontsize=7, loc="upper right", framealpha=0.9)
    ax.grid(alpha=0.2)

    # ---------------- panel 2: angular coverage rose ------------------------
    ax2 = fig.add_subplot(gs[0, 1], projection="polar")
    ref = fits.get(measurement.selected_model or "") or next(
        (f for f in fits.values() if f.valid and f.center_xy is not None), None)
    if ref is not None and ref.center_xy is not None:
        ang = np.arctan2(xy[:, 1] - ref.center_xy[1], xy[:, 0] - ref.center_xy[0])
        nb = 72
        h, edges = np.histogram(ang % (2 * np.pi), bins=nb, range=(0, 2 * np.pi))
        ax2.bar(edges[:-1] + np.pi / nb, h, width=2 * np.pi / nb, color="#546e7a",
                alpha=0.85)
        cov = ref.angular_coverage
        gap = ref.largest_gap_deg
        ax2.set_title(f"angular coverage {cov:.0%}, largest gap {gap:.0f}deg\n"
                      f"(bins with no points are unobserved circumference)",
                      fontsize=8.5, pad=14)
    ax2.set_yticklabels([])
    ax2.tick_params(labelsize=7)

    # ---------------- panel 3: multi-height profile -------------------------
    ax3 = fig.add_subplot(gs[0, 2])
    prof = measurement.profiles.get(measurement.primary_geometry, {})
    for name in ("circle_geometric", "circle_ransac", "ellipse", "outline_radial_median"):
        p = prof.get(name)
        if not p:
            continue
        hs = p["heights_m"]
        ds = [np.nan if v is None else v * 100 for v in p["diameters_m"]]
        st = MODEL_STYLE.get(name, {})
        ax3.plot(ds, hs, marker="o", ms=3.5, label=name, **st)
    ax3.axhline(measurement.measurement_height_m, color="k", lw=0.8, ls="--", alpha=0.6)
    sel_prof = prof.get(measurement.selected_model or "", {})
    if sel_prof.get("interpolated_at_target_m"):
        ax3.plot([sel_prof["interpolated_at_target_m"] * 100],
                 [measurement.measurement_height_m], marker="*", ms=13, color="#d81b60",
                 label="taper-interpolated", zorder=5)
    ax3.set_xlabel("diameter [cm]")
    ax3.set_ylabel("height above local ground [m]")
    ax3.set_title("multi-height profile", fontsize=9)
    ax3.legend(fontsize=6.5)
    ax3.grid(alpha=0.25)

    # ---------------- panel 4: diagnostics table ---------------------------
    ax4 = fig.add_subplot(gs[1, 1:])
    ax4.axis("off")
    rows = [("model", "D [cm]", "RMSE", "cov", "gap", "inlier", "boot", "valid")]
    for name in sorted(fits):
        f = fits[name]
        rows.append((
            name,
            "-" if f.diameter_m is None else f"{f.diameter_m * 100:.1f}",
            "-" if f.rmse_m is None or not np.isfinite(f.rmse_m) else f"{f.rmse_m * 1000:.1f}mm",
            "-" if f.angular_coverage is None else f"{f.angular_coverage:.0%}",
            "-" if f.largest_gap_deg is None else f"{f.largest_gap_deg:.0f}",
            "-" if f.inlier_fraction is None else f"{f.inlier_fraction:.0%}",
            "-" if f.bootstrap_std_m is None else f"{f.bootstrap_std_m * 1000:.1f}mm",
            "yes" if f.valid else "NO",
        ))
    tbl = ax4.table(cellText=rows[1:], colLabels=rows[0], loc="upper center",
                    cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(7.5)
    tbl.scale(1.0, 1.18)
    for j in range(len(rows[0])):
        tbl[0, j].set_facecolor("#eceff1")

    lg = measurement.local_ground
    ax_ = measurement.axis
    cmp_ = measurement.comparison
    lines = [
        f"status: {measurement.status}    confidence: {measurement.confidence_band}"
        f"    review: {measurement.review_state}",
        f"reported DBH: "
        f"{'n/a' if measurement.dbh_m is None else f'{measurement.dbh_m * 100:.1f} cm'}"
        f"  via {measurement.selected_model} / {measurement.dbh_source}"
        f"{'  (RECOMMENDATION, not accepted)' if measurement.selection_is_recommendation else ''}",
        f"ground: z={lg.z_m:.3f} m  slope={lg.slope_deg:.1f}deg  "
        f"roughness={lg.roughness_m * 100:.1f} cm  quality={lg.quality}"
        if lg else "ground: n/a",
        f"axis: tilt={ax_.tilt_deg:.1f}deg  azimuth={ax_.azimuth_deg:.0f}deg  "
        f"straightness={ax_.straightness_m * 100:.1f} cm  bins={ax_.n_bins_used}"
        if ax_ else "axis: n/a",
        f"ellipticity: {measurement.ellipticity.get('verdict')}  "
        f"observed ratio={_fmt(measurement.ellipticity.get('observed_axis_ratio'))}  "
        f"expected from lean={_fmt(measurement.ellipticity.get('expected_ratio_from_lean'))}",
        f"model disagreement: "
        f"{_fmt_cm(cmp_.get('max_pairwise_difference_m'))}"
        f"  ({cmp_.get('max_pairwise_pair')})",
        f"horizontal - stem_normal: {_fmt_cm(cmp_.get('horizontal_minus_stem_normal_m'))}",
    ]
    reasons = measurement.reasons[:6]
    if reasons:
        lines.append("reasons: " + "; ".join(reasons))
    ax4.text(0.0, 0.30, "\n".join(lines), fontsize=8.2, va="top", family="monospace",
             transform=ax4.transAxes)

    fig.suptitle(title or f"DBH review - {measurement.tree_id}", fontsize=12)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out


def _fmt(v, nd: int = 3) -> str:
    return "n/a" if v is None or not np.isfinite(v) else f"{v:.{nd}f}"


def _fmt_cm(v) -> str:
    return "n/a" if v is None or not np.isfinite(v) else f"{v * 100:.2f} cm"


def plot_ground_check(grid, xyz: np.ndarray, hag: np.ndarray, center_xy,
                      out_path: str | Path, radius_m: float = 6.0,
                      target_height_m: float = 1.30) -> Path:
    """Vertical-profile figure verifying that normalisation follows the terrain.

    Panel 1 is a raw-Z side view with the ground surface drawn through it; panel 2
    is the same points in HAG. If normalisation is correct the breast-height band
    is a horizontal stripe in panel 2 even where the terrain is steep.
    """
    xyz = np.asarray(xyz)
    cx, cy = float(center_xy[0]), float(center_xy[1])
    sel = (np.abs(xyz[:, 1] - cy) <= 1.0) & (np.abs(xyz[:, 0] - cx) <= radius_m)
    if sel.sum() > 400_000:
        sel = np.flatnonzero(sel)[:: max(1, int(sel.sum() // 400_000))]
    p, h = xyz[sel], hag[sel]

    fig, axs = plt.subplots(1, 2, figsize=(15, 5.2))
    axs[0].scatter(p[:, 0], p[:, 2], s=0.8, c="#455a64", alpha=0.4, rasterized=True)
    gx = np.linspace(cx - radius_m, cx + radius_m, 240)
    gz = grid.elevation(gx, np.full_like(gx, cy))
    axs[0].plot(gx, gz, color="#e53935", lw=1.8, label="ground surface")
    axs[0].plot(gx, gz + target_height_m, color="#1e88e5", lw=1.2, ls="--",
                label=f"ground + {target_height_m} m")
    axs[0].set_xlabel("X [m]")
    axs[0].set_ylabel("raw Z [m]")
    axs[0].set_title("raw elevation: a global Z slice would cut the wrong height")
    axs[0].legend(fontsize=8)
    axs[0].grid(alpha=0.2)

    axs[1].scatter(p[:, 0], h, s=0.8, c="#455a64", alpha=0.4, rasterized=True)
    axs[1].axhline(0.0, color="#e53935", lw=1.5)
    axs[1].axhline(target_height_m, color="#1e88e5", lw=1.2, ls="--")
    axs[1].set_ylim(-1.0, 6.0)
    axs[1].set_xlabel("X [m]")
    axs[1].set_ylabel("height above local ground [m]")
    axs[1].set_title("after normalisation: breast height is a horizontal band")
    axs[1].grid(alpha=0.2)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out
