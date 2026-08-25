"""Embedded matplotlib views: the plan view and the cross-section view.

matplotlib rather than hand-drawn Tk canvases, for one reason that outweighs the
convenience of either: the model overlays here must be the same curves the exported
review figure draws, and :func:`~dbh_tool.visualization.cross_section.model_boundary_xy`
is shared with it. A second implementation of "where is the fitted ellipse" would
eventually disagree with the first, and a review view you cannot trust is worse than
no review view.

The palette comes from :func:`dbh_tool.gui.theme.mpl_rc`, applied through
``rc_context`` so the light export figures are unaffected.

Nothing in this module reads a file or fits anything. Both views take arrays that a
worker thread has already prepared.
"""
from __future__ import annotations

import numpy as np
from matplotlib import rc_context
from matplotlib.patches import Circle
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from ..visualization.cross_section import MODEL_STYLE, model_boundary_xy
from . import theme

# Draw order for the section view. The robust circle and the outline go on top because
# they are the models most likely to be recommended, and an overlay you cannot see is
# an overlay that does not help.
DRAW_ORDER = ("circle_algebraic", "circle_taubin", "circle_pratt", "circle_geometric",
              "ellipse", "outline_radial_median_inliers", "outline_radial_median",
              "circle_ransac")

# Plan-view raster resolution. 1200 cells across the longer axis is fine enough to
# place a target on a 20 cm stem in a 60 m plot and cheap enough to rebuild on demand.
PLAN_CELLS = 1200


class EmbeddedFigure:
    """A matplotlib figure packed into a Tk frame, with the panel palette applied."""

    def __init__(self, parent, figsize=(7.4, 6.2), dpi=100):
        self.fig = Figure(figsize=figsize, dpi=dpi, facecolor=theme.INPUT)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.widget = self.canvas.get_tk_widget()
        self.widget.configure(background=theme.INPUT, highlightthickness=0, bd=0)

    def pack(self, **kw):
        self.widget.pack(**kw)
        return self

    def clear(self):
        self.fig.clear()

    def draw(self):
        self.canvas.draw_idle()

    def message(self, text: str, colour: str | None = None):
        """Replace the figure with a single centred line. Used for empty states."""
        with rc_context(theme.mpl_rc()):
            self.fig.clear()
            ax = self.fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True,
                    color=colour or theme.TEXT_FAINT, fontsize=10,
                    transform=ax.transAxes)
            self.draw()


def plan_raster(iter_chunks, bounds, cells: int = PLAN_CELLS):
    """Top-down point-density raster, accumulated chunk by chunk.

    Density rather than height: what an operator needs from a plan view is "where are
    the stems", and a stem is a vertical column of returns, so it shows up as a dense
    spot in plan whatever its height. A max-Z raster shows the canopy instead, which is
    exactly what is in the way.

    ``iter_chunks`` yields (N, 3) arrays. Runs on a worker thread -- there is no Tk and
    no pyplot in here.
    """
    xmin, xmax, ymin, ymax = bounds
    span_x, span_y = max(xmax - xmin, 1e-6), max(ymax - ymin, 1e-6)
    if span_x >= span_y:
        nx = int(cells)
        ny = max(8, int(round(cells * span_y / span_x)))
    else:
        ny = int(cells)
        nx = max(8, int(round(cells * span_x / span_y)))

    acc = np.zeros((ny, nx), dtype=np.float64)
    n_total = 0
    for xyz in iter_chunks:
        if len(xyz) == 0:
            continue
        n_total += len(xyz)
        h, _, _ = np.histogram2d(
            xyz[:, 1], xyz[:, 0], bins=(ny, nx),
            range=((ymin, ymax), (xmin, xmax)))
        acc += h
    return {
        "counts": acc,
        "extent": (xmin, xmax, ymin, ymax),
        "n_points": n_total,
    }


def draw_plan(ef: EmbeddedFigure, raster: dict, targets, selected: str | None = None,
              measured: dict | None = None):
    """Plan view with target markers.

    ``targets`` is a list of ``(tree_id, x, y)``. ``measured`` maps tree id to the
    status string, so a marker's colour says what happened there -- the plan view
    doubles as the overview of a whole run.
    """
    counts = raster["counts"]
    extent = raster["extent"]
    # Log stretch: point density in a terrestrial scan spans several orders of
    # magnitude between open ground and a stem, and a linear ramp shows only the
    # densest few cells.
    shown = np.log1p(counts)
    with rc_context(theme.mpl_rc()):
        ef.fig.clear()
        ax = ef.fig.add_subplot(111)
        if shown.max() > 0:
            ax.imshow(shown, origin="lower", extent=extent, cmap="bone",
                      interpolation="nearest", aspect="equal",
                      vmax=float(np.percentile(shown[shown > 0], 99.5)))
        ax.set_xlabel("X [m]")
        ax.set_ylabel("Y [m]")
        ax.set_title(f"plan view - {raster['n_points']:,} points, "
                     f"log point density", fontsize=9)

        for tid, x, y in targets:
            status = (measured or {}).get(tid)
            col = theme.STATUS_COLOUR.get(status, theme.MARKER) if status else theme.MARKER
            is_sel = tid == selected
            ax.plot(x, y, marker="+", ms=13 if is_sel else 9,
                    mew=2.0 if is_sel else 1.2, color=col, zorder=5)
            if is_sel:
                ax.add_artist(Circle((x, y), radius=0.8, fill=False,
                                     ec=theme.ACCENT_HI, lw=1.4, zorder=4))
            ax.annotate(tid, (x, y), textcoords="offset points", xytext=(7, 4),
                        fontsize=7.5,
                        color=theme.TEXT if is_sel else theme.TEXT_DIM, zorder=6)
        ef.fig.set_layout_engine('constrained')
        ef.draw()


def draw_section(ef: EmbeddedFigure, measurement, xy: np.ndarray,
                 show: set[str] | None = None, show_outliers: bool = True):
    """The section, every requested model overlaid, plus the coverage rose.

    Rejected models are drawn dashed-faint rather than hidden. A reviewer's job is to
    judge the tool's refusals as well as its answers, and "the ellipse was declined" is
    a claim you can only check by seeing what it claimed.
    """
    fits = {f.model: f for f in measurement.candidate_results}
    show = show if show is not None else {n for n, f in fits.items() if f.valid}

    with rc_context(theme.mpl_rc()):
        ef.fig.clear()
        gs = ef.fig.add_gridspec(1, 2, width_ratios=[1.55, 1.0], wspace=0.24)
        ax = ef.fig.add_subplot(gs[0, 0])
        ax.scatter(xy[:, 0], xy[:, 1], s=1.6, c=theme.TEXT_FAINT, alpha=0.5,
                   linewidths=0, rasterized=True, label=f"section ({len(xy)} pts)")

        ransac = fits.get("circle_ransac")
        if (show_outliers and ransac is not None and ransac.center_xy is not None
                and "radius_m" in ransac.extra):
            d = np.hypot(xy[:, 0] - ransac.center_xy[0], xy[:, 1] - ransac.center_xy[1])
            thr = ransac.extra.get("residual_threshold_m", 0.01)
            outl = np.abs(d - ransac.extra["radius_m"]) > thr
            if outl.any():
                ax.scatter(xy[outl, 0], xy[outl, 1], s=7, facecolors="none",
                           edgecolors=theme.DANGER, linewidths=0.45, rasterized=True,
                           label=f"outside RANSAC band ({int(outl.sum())})")

        for name in DRAW_ORDER:
            f = fits.get(name)
            if f is None or name not in show:
                continue
            b = model_boundary_xy(name, f)
            if b is None:
                continue
            st = dict(MODEL_STYLE.get(name, dict(color=theme.TEXT, ls="-", lw=1.0)))
            d_cm = "n/a" if f.diameter_m is None else f"{f.diameter_m * 100:.1f} cm"
            if f.valid:
                lbl = f"{name}  {d_cm}"
            else:
                # Declined: faint and dashed, and say so in the legend. Never silently
                # styled the same as an accepted model.
                st.update(ls=(0, (2, 2)), lw=1.0, alpha=0.55)
                lbl = f"{name}  {d_cm}  (DECLINED)"
            ax.plot(b[:, 0], b[:, 1], label=lbl, **st)
            ax.plot(*f.center_xy, marker="+", ms=5,
                    color=st["color"], alpha=st.get("alpha", 1.0))

        ax.set_aspect("equal")
        ax.set_xlabel("section X [m]")
        ax.set_ylabel("section Y [m]")
        ax.set_title(f"{measurement.tree_id} - {measurement.primary_geometry} section "
                     f"at {measurement.measurement_height_m:.2f} m", fontsize=9)
        ax.grid(alpha=0.18)
        ax.legend(fontsize=6.5, loc="upper right", framealpha=0.85)

        # -- coverage rose ---------------------------------------------------
        ax2 = ef.fig.add_subplot(gs[0, 1], projection="polar")
        ref = fits.get(measurement.selected_model or "") or next(
            (f for f in fits.values() if f.valid and f.center_xy is not None), None)
        if ref is not None and ref.center_xy is not None:
            ang = np.arctan2(xy[:, 1] - ref.center_xy[1], xy[:, 0] - ref.center_xy[0])
            nb = 72
            h, edges = np.histogram(ang % (2 * np.pi), bins=nb, range=(0, 2 * np.pi))
            occupied = h > 0
            # Empty bins are the story, so they get drawn as a faint full-height ring
            # rather than left blank -- an absent bar and a short bar look alike.
            if h.max() > 0:
                ax2.bar(edges[:-1][~occupied] + np.pi / nb, np.full((~occupied).sum(),
                        h.max()), width=2 * np.pi / nb, color=theme.DANGER, alpha=0.16)
            ax2.bar(edges[:-1][occupied] + np.pi / nb, h[occupied],
                    width=2 * np.pi / nb, color=theme.ACCENT, alpha=0.85)
            cov = ref.angular_coverage or 0.0
            gap = ref.largest_gap_deg if ref.largest_gap_deg is not None else 360.0
            ax2.set_title(f"coverage {cov:.0%}, largest gap {gap:.0f}°\n"
                          f"shaded = unobserved circumference", fontsize=8)
        else:
            ax2.set_title("no fit to centre the rose on", fontsize=8)
        ax2.set_yticklabels([])
        ax2.tick_params(labelsize=6.5)
        ax2.grid(alpha=0.2)

        ef.fig.set_layout_engine('constrained')
        ef.draw()


def draw_profile(ef: EmbeddedFigure, measurement):
    """Multi-height diameter profile for the models that carry one."""
    prof = measurement.profiles.get(measurement.primary_geometry, {}) or {}
    with rc_context(theme.mpl_rc()):
        ef.fig.clear()
        ax = ef.fig.add_subplot(111)
        drawn = False
        for name in ("circle_geometric", "circle_ransac", "ellipse",
                     "outline_radial_median"):
            p = prof.get(name)
            if not p:
                continue
            ds = [np.nan if v is None else v * 100 for v in p["diameters_m"]]
            if not np.any(np.isfinite(ds)):
                continue
            st = dict(MODEL_STYLE.get(name, {}))
            ax.plot(ds, p["heights_m"], marker="o", ms=3.2, label=name, **st)
            drawn = True
        ax.axhline(measurement.measurement_height_m, color=theme.TEXT_DIM, lw=0.8,
                   ls="--", alpha=0.7)
        ax.set_xlabel("diameter [cm]")
        ax.set_ylabel("height above local ground [m]")
        ax.set_title("multi-height profile — a stem that changes diameter "
                     "sharply is a deformity, not a measurement", fontsize=8)
        ax.grid(alpha=0.2)
        if drawn:
            ax.legend(fontsize=6.5)
        else:
            ax.text(0.5, 0.5, "no profile available", ha="center", va="center",
                    color=theme.TEXT_FAINT, transform=ax.transAxes)
        ef.fig.set_layout_engine('constrained')
        ef.draw()
