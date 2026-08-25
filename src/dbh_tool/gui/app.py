"""The review window.

Why a GUI at all, given a perfectly good CLI: because the thing this tool most needs a
person to do is *look at the section*. Every document in `docs/` says some version of
"a number you have not looked at a section for is a number you know nothing about", and
the CLI's answer to that was to write PNGs into a directory and hope. Reviewing ten
trees meant ten file-manager round trips, and reviewing a plot meant fifty.

So the window is built around one loop: pick a tree from the list, look at its section
with every model drawn on it, decide. Everything else -- import, targets, settings,
export -- is scaffolding for that loop.

Layout is three columns, matching the sibling 360-pointcloud-tool picker: what you are
measuring on the left, what you are looking at in the middle, what you decide about it
on the right.

**Nothing that touches the point cloud runs on the Tk thread.** Opening a 0.92 GB scan
means a full pass to build the plan raster and another for the ground surface;
measuring ten trees takes tens of seconds. Both run on a worker and report back through
a queue the main thread polls. Tkinter is not thread-safe -- touching widgets from the
worker would crash or hang intermittently -- so workers only ever post plain data.

Three rules from `CLAUDE.md` shape this file more than any layout decision:

* **A hard failure must not show a headline diameter** (non-negotiable 7). The result
  list and the detail panel say "refused" and explain why. There is deliberately no
  control anywhere that turns a refusal into a number.
* **Confidence is a qualitative band, never a percentage.** It is shown as a coloured
  word. No bars, no meters, nothing implying a scale that does not exist.
* **The selected model is a recommendation.** It is labelled as one everywhere it
  appears, because `decision.automatic_selection` is false by default.
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from .. import __version__
from ..config import PROVISIONAL_PARAMETERS, RunConfig
from ..export.tables import write_csv, write_json
from ..ground.dtm import build_ground_grid
from ..ground.normalize import height_above_ground
from ..io.las import LasSource, inspect_las
from ..measure import measure_tree
from ..stems.candidates import find_stem_candidates
from ..visualization.cross_section import plot_ground_check, plot_measurement
from . import plots, review, theme, widgets

LEFT_W = 318
RIGHT_W = 340

CLOUD_FILETYPES = [
    ("Point clouds", "*.las *.laz"),
    ("LAS", "*.las"),
    ("LAZ (compressed)", "*.laz"),
    ("All files", "*.*"),
]

MODEL_ORDER = ("circle_ransac", "circle_geometric", "circle_pratt", "circle_taubin",
               "circle_algebraic", "ellipse", "outline_radial_median",
               "outline_radial_median_inliers")

# Compact labels for the results list. The full strings do not fit a 340 px panel and
# were being clipped mid-word ("Invalid Measurem"), which is worse than an
# abbreviation. Colour carries the accepted/review/failed distinction, and the full
# status is always spelled out in the detail panel below.
STATUS_SHORT = {
    "ACCEPTED_CIRCULAR": "circular",
    "ACCEPTED_ELLIPTICAL": "elliptical",
    "ACCEPTED_IRREGULAR": "irregular",
    "REVIEW_REQUIRED": "review",
    "INVALID_MEASUREMENT_HEIGHT": "bad height",
    "FAILED_INSUFFICIENT_DATA": "no data",
}
BAND_SHORT = {"HIGH": "high", "MEDIUM": "med", "LOW": "low",
              "REVIEW_REQUIRED": "rev", "FAILED": "fail"}
REVIEW_SHORT = {"PENDING": "–", "APPROVED": "appr", "REJECTED": "rej",
                "OVERRIDDEN": "ovr"}


def _fmt_cm(v, nd=1):
    return "-" if v is None or not np.isfinite(v) else f"{v:.{nd}f} cm"


class App:
    def __init__(self, cloud=None, outdir="out", targets=None, config=None):
        self.outdir = Path(outdir)
        self.cfg = RunConfig.load(config) if config else RunConfig()
        self.cfg_path = str(config) if config else ""
        self.jobs: queue.Queue = queue.Queue()
        self.busy = False

        # Cloud state, replaced wholesale by _install_cloud.
        self.las_path: Path | None = None
        self.info = None
        self.src: LasSource | None = None
        self.grid = None
        self.raster = None

        self.targets: list[tuple[str, float, float]] = []
        self.results: dict = {}          # tree_id -> TreeMeasurement
        self.sections: dict = {}         # tree_id -> (N, 2) section points
        self.selected: str | None = None
        self.store = review.ReviewStore(self.outdir)

        self._needs_cloud: list = []
        self._needs_result: list = []

        self._build_ui()
        self.root.after(120, self._poll)

        if targets:
            self._load_targets_file(targets, quiet=True)
        if cloud:
            self._begin_load(cloud)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("dbh-tool — measurement review")
        self.root.geometry("1640x980")
        self.root.minsize(1180, 720)
        theme.apply(self.root)

        outer = ttk.Frame(self.root, style="Bg.TFrame")
        outer.pack(fill="both", expand=True)

        main = ttk.Frame(outer, style="Bg.TFrame")
        main.pack(fill="both", expand=True)

        left_outer, left = widgets.scroll_panel(main, LEFT_W)
        left_outer.pack(side="left", fill="y")
        widgets.rule(main).pack(side="left", fill="y")

        right_outer, right = widgets.scroll_panel(main, RIGHT_W)
        right_outer.pack(side="right", fill="y")
        widgets.rule(main).pack(side="right", fill="y")

        centre = ttk.Frame(main, style="Bg.TFrame")
        centre.pack(side="left", fill="both", expand=True)

        self._build_left(left)
        self._build_centre(centre)
        self._build_right(right)

        self.log = widgets.LogStrip(outer)
        self.log.frame.pack(fill="x", side="bottom")

        widgets.bind_wheel(left_outer)
        widgets.bind_wheel(right_outer)

        theme.dark_titlebar(self.root)
        self._set_enabled(False)
        self._set_view("plan")
        self.log("Import a LAS or LAZ point cloud to begin.")
        self.log(f"{len(PROVISIONAL_PARAMETERS)} of the run configuration's thresholds "
                 f"are provisional — nothing here is calibrated against field data.")
        self._show_empty()

    # -- left panel: what you are measuring ---------------------------------

    def _build_left(self, left):
        self.btn_import = ttk.Button(left, text="Import point cloud",
                                     style="Accent.TButton", command=self._do_import)
        self.btn_import.pack(fill="x", padx=8, pady=(8, 0))
        self.lbl_file = ttk.Label(left, text="no cloud loaded", style="Hint.TLabel",
                                  background=theme.PANEL, anchor="center")
        self.lbl_file.pack(fill="x", padx=8, pady=(3, 0))

        c = widgets.card(left, "Cloud")
        g = ttk.Frame(c, style="Card.TFrame")
        g.pack(fill="x")
        self.kv_points = widgets.kv(g, 0, "points")
        self.kv_extent = widgets.kv(g, 1, "extent")
        self.kv_relief = widgets.kv(g, 2, "relief")
        self.kv_crs = widgets.kv(g, 3, "CRS")
        self.kv_class = widgets.kv(g, 4, "classified")
        self.lbl_warn = ttk.Label(c, text="", style="Hint.TLabel", justify="left",
                                  wraplength=LEFT_W - 56, foreground=theme.WARN)
        self.lbl_warn.pack(anchor="w", fill="x", pady=(6, 0))

        c = widgets.card(left, "Targets")
        r = widgets.row(c)
        b = ttk.Button(r, text="Load JSON", style="Small.TButton",
                       command=self._do_load_targets)
        b.pack(side="left", expand=True, fill="x", padx=(0, 3))
        self.btn_detect = ttk.Button(r, text="Detect (unvalidated)",
                                     style="Small.TButton", command=self._do_detect)
        self.btn_detect.pack(side="left", expand=True, fill="x")
        self._needs_cloud.append(self.btn_detect)

        r = widgets.row(c, pady=(6, 1))
        ttk.Label(r, text="add at", width=6, style="Dim.TLabel").pack(side="left")
        self.var_x = tk.StringVar()
        self.var_y = tk.StringVar()
        self.var_id = tk.StringVar()
        for var, w in ((self.var_x, 6), (self.var_y, 6), (self.var_id, 6)):
            ttk.Entry(r, textvariable=var, width=w).pack(side="left", padx=(0, 3))
        ttk.Button(r, text="+", style="Small.TButton", width=3,
                   command=self._add_target).pack(side="left")
        widgets.hint(c, "X, Y and an optional id. Or click the plan view to place one. "
                        "The detector's precision and recall have never been measured; "
                        "hand-check anything it finds.", LEFT_W)

        self.lst_targets = tk.Listbox(c, height=7, activestyle="none",
                                      exportselection=False)
        self.lst_targets.configure(background=theme.INPUT, foreground=theme.TEXT,
                                   selectbackground=theme.ACCENT_LO,
                                   selectforeground=theme.TEXT, relief="flat",
                                   borderwidth=0, highlightthickness=0,
                                   font=theme.MONO_SMALL)
        self.lst_targets.pack(fill="x", pady=(6, 0))
        self.lst_targets.bind("<<ListboxSelect>>", self._on_target_pick)
        r = widgets.row(c, pady=(4, 1))
        ttk.Button(r, text="Remove", style="Small.TButton",
                   command=self._remove_target).pack(side="left", expand=True,
                                                     fill="x", padx=(0, 3))
        ttk.Button(r, text="Clear all", style="Small.TButton",
                   command=self._clear_targets).pack(side="left", expand=True, fill="x")

        c = widgets.card(left, "Measurement settings")
        r = widgets.row(c)
        ttk.Label(r, text="height", width=8, style="Dim.TLabel").pack(side="left")
        self.var_height = tk.StringVar(value=f"{self.cfg.slice.target_height_m:.2f}")
        ttk.Entry(r, textvariable=self.var_height, width=7).pack(side="left")
        ttk.Label(r, text="m above local ground", style="Hint.TLabel").pack(
            side="left", padx=(4, 0))

        r = widgets.row(c)
        ttk.Label(r, text="ROI", width=8, style="Dim.TLabel").pack(side="left")
        self.var_roi = tk.StringVar(value="4.0")
        ttk.Entry(r, textvariable=self.var_roi, width=7).pack(side="left")
        ttk.Label(r, text="m crop radius per stem", style="Hint.TLabel").pack(
            side="left", padx=(4, 0))

        r = widgets.row(c, pady=(6, 1))
        ttk.Label(r, text="geometry", width=8, style="Dim.TLabel").pack(side="left")
        self.var_geom = tk.StringVar(value=self.cfg.slice.primary_geometry)
        ttk.Combobox(r, textvariable=self.var_geom, width=12, state="readonly",
                     values=("horizontal", "stem_normal")).pack(side="left")

        r = widgets.row(c)
        ttk.Label(r, text="source", width=8, style="Dim.TLabel").pack(side="left")
        self.var_src = tk.StringVar(value=self.cfg.decision.primary_dbh_source)
        ttk.Combobox(r, textvariable=self.var_src, width=18, state="readonly",
                     values=("single_slice", "profile_median",
                             "taper_interpolated")).pack(side="left")

        r = widgets.row(c, pady=(6, 1))
        ttk.Button(r, text="Load config…", style="Small.TButton",
                   command=self._do_load_config).pack(side="left", expand=True,
                                                      fill="x", padx=(0, 3))
        ttk.Button(r, text="Save config…", style="Small.TButton",
                   command=self._do_save_config).pack(side="left", expand=True,
                                                      fill="x")
        self.lbl_cfg = ttk.Label(c, text="built-in defaults", style="Hint.TLabel")
        self.lbl_cfg.pack(anchor="w", pady=(3, 0))
        widgets.hint(c, f"{len(PROVISIONAL_PARAMETERS)} thresholds are provisional and "
                        f"uncalibrated. Every export records that. Changing a value "
                        f"here changes the answer; it does not make it more correct.",
                     LEFT_W)

    # -- centre: what you are looking at ------------------------------------

    def _build_centre(self, centre):
        bar = ttk.Frame(centre, style="Bg.TFrame")
        bar.pack(fill="x", padx=6, pady=(6, 0))
        self.btn_plan = ttk.Button(bar, text="Plan", style="SegOn.TButton",
                                   command=lambda: self._set_view("plan"))
        self.btn_plan.pack(side="left")
        self.btn_sect = ttk.Button(bar, text="Section", style="Seg.TButton",
                                   command=lambda: self._set_view("section"))
        self.btn_sect.pack(side="left", padx=(2, 0))
        self.btn_prof = ttk.Button(bar, text="Profile", style="Seg.TButton",
                                   command=lambda: self._set_view("profile"))
        self.btn_prof.pack(side="left", padx=(2, 0))

        self.lbl_view = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_view.pack(side="right", padx=(0, 4))

        holder = ttk.Frame(centre, style="Bg.TFrame")
        holder.pack(fill="both", expand=True, padx=6, pady=6)
        self.fig = plots.EmbeddedFigure(holder, figsize=(8.6, 7.0))
        self.fig.pack(fill="both", expand=True)
        self.fig.canvas.mpl_connect("button_press_event", self._on_plot_click)

        opts = ttk.Frame(centre, style="Bg.TFrame")
        opts.pack(fill="x", padx=6, pady=(0, 4))
        self.var_declined = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(opts, text="draw declined models (dashed)",
                             variable=self.var_declined, command=self._redraw,
                             style="TCheckbutton")
        cb.configure(style="TCheckbutton")
        cb.pack(side="left")
        self.var_outliers = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="mark points outside the RANSAC band",
                        variable=self.var_outliers,
                        command=self._redraw).pack(side="left", padx=(12, 0))

    # -- right panel: what you decide ---------------------------------------

    def _build_right(self, right):
        c = widgets.card(right, "Measure", pady=(8, 0))
        self.btn_measure = ttk.Button(c, text="Measure all targets",
                                      style="Accent.TButton", command=self._do_measure)
        self.btn_measure.pack(fill="x")
        self._needs_cloud.append(self.btn_measure)
        self.lbl_progress = ttk.Label(c, text="", style="Hint.TLabel")
        self.lbl_progress.pack(anchor="w", pady=(3, 0))

        c = widgets.card(right, "Results")
        cols = ("tree", "dbh", "status", "band", "review")
        self.tree = ttk.Treeview(c, columns=cols, show="headings", height=11,
                                 selectmode="browse")
        for col, w, txt in (("tree", 46, "tree"), ("dbh", 66, "DBH"),
                            ("status", 88, "status"), ("band", 46, "conf"),
                            ("review", 48, "rev")):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="w", stretch=(col == "status"))
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._on_result_pick)
        for state, col in theme.REVIEW_COLOUR.items():
            self.tree.tag_configure(f"rv_{state}", foreground=col)
        self.tree.tag_configure("refused", foreground=theme.TEXT_FAINT)
        self.tree.tag_configure("stale", background="#2a1f10")
        self.lbl_counts = ttk.Label(c, text="", style="Hint.TLabel", justify="left",
                                    wraplength=RIGHT_W - 56)
        self.lbl_counts.pack(anchor="w", fill="x", pady=(4, 0))

        c = widgets.card(right, "This tree")
        g = ttk.Frame(c, style="Card.TFrame")
        g.pack(fill="x")
        self.kv_dbh = widgets.kv(g, 0, "reported")
        self.kv_status = widgets.kv(g, 1, "status")
        self.kv_band = widgets.kv(g, 2, "confidence")
        self.kv_model = widgets.kv(g, 3, "model")
        self.kv_shape = widgets.kv(g, 4, "shape")
        self.kv_anom = widgets.kv(g, 5, "anomaly")
        self.kv_ground = widgets.kv(g, 6, "ground")
        self.kv_tilt = widgets.kv(g, 7, "lean")
        self.kv_disagree = widgets.kv(g, 8, "disagreement")

        self.txt_reasons = tk.Text(c, height=6, wrap="word", state="disabled")
        theme.style_text(self.txt_reasons)
        self.txt_reasons.configure(background=theme.INPUT)
        self.txt_reasons.pack(fill="x", pady=(6, 0))
        self.txt_reasons.tag_configure("warn", foreground=theme.WARN)
        self.txt_reasons.tag_configure("err", foreground=theme.DANGER)
        self.txt_reasons.tag_configure("head", foreground=theme.TEXT_DIM)

        r = widgets.row(c, pady=(6, 1))
        b = ttk.Button(r, text="Model table", style="Small.TButton",
                       command=self._show_models)
        b.pack(side="left", expand=True, fill="x", padx=(0, 3))
        self._needs_result.append(b)
        b = ttk.Button(r, text="Full report PNG", style="Small.TButton",
                       command=self._export_png)
        b.pack(side="left", expand=True, fill="x")
        self._needs_result.append(b)

        c = widgets.card(right, "Review (M7)")
        r = widgets.row(c)
        ttk.Label(r, text="reviewer", width=9, style="Dim.TLabel").pack(side="left")
        self.var_reviewer = tk.StringVar(value=self.store.reviewer)
        e = ttk.Entry(r, textvariable=self.var_reviewer)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<FocusOut>", lambda _e: self._set_reviewer())

        ttk.Label(c, text="note (required to reject or override)",
                  style="Dim.TLabel").pack(anchor="w", pady=(6, 2))
        self.txt_note = tk.Text(c, height=3, wrap="word")
        theme.style_text(self.txt_note)
        self.txt_note.configure(background=theme.INPUT, foreground=theme.TEXT)
        self.txt_note.pack(fill="x")

        r = widgets.row(c, pady=(6, 1))
        ttk.Label(r, text="override", width=9, style="Dim.TLabel").pack(side="left")
        self.var_override = tk.StringVar()
        self.cmb_override = ttk.Combobox(r, textvariable=self.var_override,
                                         state="readonly", width=22)
        self.cmb_override.pack(side="left", fill="x", expand=True)

        r = widgets.row(c, pady=(6, 1))
        for txt, style_, cmd in (("Approve", "Small.TButton", self._approve),
                                 ("Reject", "Small.TButton", self._reject),
                                 ("Override", "Small.TButton", self._override),
                                 ("Reset", "Small.TButton", self._reset_review)):
            b = ttk.Button(r, text=txt, style=style_, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=(0, 2))
            self._needs_result.append(b)
        self.lbl_review = ttk.Label(c, text="", style="Hint.TLabel", justify="left",
                                    wraplength=RIGHT_W - 56)
        self.lbl_review.pack(anchor="w", pady=(4, 0))
        widgets.hint(c, "Approving a refusal records agreement that there is no "
                        "number. It does not create one — nothing here can.", RIGHT_W)

        c = widgets.card(right, "Export")
        r = widgets.row(c)
        ttk.Button(r, text="Measurements CSV/JSON", style="Small.TButton",
                   command=self._export_tables).pack(side="left", expand=True,
                                                      fill="x", padx=(0, 3))
        ttk.Button(r, text="Open out/", style="Small.TButton",
                   command=self._open_outdir).pack(side="left", expand=True, fill="x")
        self.lbl_out = ttk.Label(c, text=str(self.outdir), style="Hint.TLabel",
                                 wraplength=RIGHT_W - 56)
        self.lbl_out.pack(anchor="w", pady=(3, 0))

    # -------------------------------------------------------------- state --

    def _set_enabled(self, on: bool):
        for w in self._needs_cloud:
            w.configure(state="normal" if on else "disabled")

    def _set_result_enabled(self, on: bool):
        for w in self._needs_result:
            w.configure(state="normal" if on else "disabled")

    def _set_view(self, view: str):
        self.view = view
        for name, btn in (("plan", self.btn_plan), ("section", self.btn_sect),
                          ("profile", self.btn_prof)):
            btn.configure(style="SegOn.TButton" if name == view else "Seg.TButton")
        self._redraw()

    def _show_empty(self):
        self.fig.message("Import a point cloud, add targets, then measure.\n\n"
                         "Roughly half of a detector's targets are normally refused. "
                         "That is the tool working, not failing.")

    # ------------------------------------------------------------- import --

    def _do_import(self):
        path = filedialog.askopenfilename(
            title="Select a LAS / LAZ point cloud", parent=self.root,
            initialdir=str(Path.cwd()), filetypes=CLOUD_FILETYPES)
        if path:
            self._begin_load(path)

    def _begin_load(self, path):
        if self.busy:
            messagebox.showinfo("Busy", "Something is already running.")
            return
        self.busy = True
        self.btn_import.configure(state="disabled")
        self.lbl_file.configure(text=f"opening {Path(path).name} …")
        self.log(f"Opening {path}")

        def work():
            try:
                info = inspect_las(path)
                src = LasSource(path)
                self.jobs.put(("log", "building plan raster (one pass)"))
                raster = plots.plan_raster(
                    src.iter_xyz(),
                    (info.mins[0], info.maxs[0], info.mins[1], info.maxs[1]))
                self.jobs.put(("cloud", (Path(path), info, src, raster)))
            except Exception as exc:
                self.jobs.put(("loaderr", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _forget_cloud(self):
        """Return to the no-cloud state. Used when an install fails part way."""
        self.las_path = self.info = self.src = self.raster = self.grid = None
        self.results.clear()
        self.sections.clear()
        self.selected = None
        self.lbl_file.configure(text="no cloud loaded")
        for lbl in (self.kv_points, self.kv_extent, self.kv_relief, self.kv_crs,
                    self.kv_class):
            lbl.configure(text="-", foreground=theme.TEXT_DIM)
        self.lbl_warn.configure(text="")
        self._set_enabled(False)
        self._refresh_results()
        self._refresh_detail()
        self._show_empty()

    def _install_cloud(self, path, info, src, raster):
        self.las_path, self.info, self.src, self.raster = path, info, src, raster
        self.grid = None
        self.results.clear()
        self.sections.clear()
        self.selected = None
        self._refresh_results()
        self._refresh_detail()

        # LasInfo.extent is a property returning (dx, dy, dz); "extent_m" is only the
        # key it uses in to_dict(), not an attribute.
        dx, dy, dz = info.extent
        self.lbl_file.configure(text=path.name)
        self.kv_points.configure(text=f"{info.point_count:,}")
        self.kv_extent.configure(text=f"{dx:.1f} x {dy:.1f} m")
        self.kv_relief.configure(text=f"{dz:.1f} m")
        self.kv_crs.configure(
            text="none" if not info.crs_wkt else "present",
            foreground=theme.WARN if not info.crs_wkt else theme.OK)
        self.kv_class.configure(
            text="no" if not info.has_classification else "yes",
            foreground=theme.WARN if not info.has_classification else theme.OK)
        self.lbl_warn.configure(text="\n".join(f"• {w}" for w in info.warnings))
        for w in info.warnings:
            self.log(f"warning: {w}")
        self.log(f"Ready. {info.point_count:,} points, {dx:.1f} x {dy:.1f} m, "
                 f"{dz:.1f} m relief.")
        self._set_enabled(True)
        self._set_view("plan")

    # ------------------------------------------------------------ targets --

    def _do_load_targets(self):
        path = filedialog.askopenfilename(
            title="Targets JSON", parent=self.root, initialdir=str(Path.cwd()),
            filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self._load_targets_file(path)

    def _load_targets_file(self, path, quiet=False):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            new = [(str(t.get("tree_id") or f"T{i + 1:02d}"),
                    float(t["x"]), float(t["y"])) for i, t in enumerate(raw)]
        except Exception as exc:
            self.log(f"cannot read targets: {type(exc).__name__}: {exc}")
            if not quiet:
                messagebox.showerror("Bad targets file", str(exc))
            return
        self.targets = new
        self._refresh_targets()
        self.log(f"Loaded {len(new)} target(s) from {Path(path).name}")

    def _do_detect(self):
        if self.busy or self.src is None:
            return
        if not messagebox.askokcancel(
                "Stem detection is unvalidated",
                "Detection precision and recall have never been measured. On the "
                "sample scan it accepted 51 candidates and measurement then refused "
                "half of the ten inspected, with two targets on the same stem.\n\n"
                "Use the output as a list of places to look, and hand-check it. "
                "Continue?"):
            return
        self.busy = True
        self.log("Detecting stem candidates (unvalidated) …")

        cfg = self.cfg

        def work():
            try:
                self.jobs.put(("prog", "loading a decimated cloud"))
                xyz = self.src.decimate(10).astype(float)
                self.jobs.put(("prog", "building ground surface"))
                grid = build_ground_grid(
                    [xyz], cell_m=cfg.ground.cell_m,
                    bounds=(self.info.mins[0], self.info.maxs[0],
                            self.info.mins[1], self.info.maxs[1]))
                self.jobs.put(("prog", "clustering the breast-height band"))
                hag = height_above_ground(xyz, grid)
                cands = find_stem_candidates(
                    xyz, hag, target_height_m=cfg.slice.target_height_m)
                self.jobs.put(("detect", cands))
            except Exception as exc:
                self.jobs.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _add_target(self, x=None, y=None):
        try:
            x = float(self.var_x.get()) if x is None else float(x)
            y = float(self.var_y.get()) if y is None else float(y)
        except ValueError:
            self.log("cannot add target: X and Y must be numbers")
            return
        tid = self.var_id.get().strip() or f"T{len(self.targets) + 1:02d}"
        if any(t[0] == tid for t in self.targets):
            tid = f"{tid}_{len(self.targets) + 1}"
        self.targets.append((tid, x, y))
        self.var_x.set("")
        self.var_y.set("")
        self.var_id.set("")
        self._refresh_targets()
        self.log(f"Added {tid} at ({x:.2f}, {y:.2f})")

    def _remove_target(self):
        sel = self.lst_targets.curselection()
        if not sel:
            return
        tid = self.targets[sel[0]][0]
        del self.targets[sel[0]]
        self.results.pop(tid, None)
        self.sections.pop(tid, None)
        self._refresh_targets()
        self._refresh_results()

    def _clear_targets(self):
        self.targets.clear()
        self.results.clear()
        self.sections.clear()
        self.selected = None
        self._refresh_targets()
        self._refresh_results()
        self._refresh_detail()

    def _refresh_targets(self):
        self.lst_targets.delete(0, "end")
        for tid, x, y in self.targets:
            self.lst_targets.insert("end", f"{tid:<8} {x:8.2f} {y:8.2f}")
        self._redraw()

    def _on_target_pick(self, _ev=None):
        sel = self.lst_targets.curselection()
        if sel:
            self.selected = self.targets[sel[0]][0]
            self._refresh_detail()
            self._redraw()

    def _on_plot_click(self, ev):
        if self.view != "plan" or ev.inaxes is None or self.raster is None:
            return
        if ev.xdata is None or ev.ydata is None:
            return
        if ev.dblclick:
            self._add_target(ev.xdata, ev.ydata)
            return
        # Single click selects the nearest existing target, so the plan view drives the
        # review loop as well as target placement.
        if self.targets:
            d = [(np.hypot(x - ev.xdata, y - ev.ydata), tid)
                 for tid, x, y in self.targets]
            dist, tid = min(d)
            if dist < 2.0:
                self.selected = tid
                self._select_in_list(tid)
                self._refresh_detail()
                self._redraw()

    def _select_in_list(self, tid):
        for i, (t, _, _) in enumerate(self.targets):
            if t == tid:
                self.lst_targets.selection_clear(0, "end")
                self.lst_targets.selection_set(i)
                break
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] == tid:
                self.tree.selection_set(iid)
                break

    # ------------------------------------------------------------ measure --

    def _apply_settings(self):
        """Push the panel's editable settings into the run config."""
        try:
            self.cfg.slice.target_height_m = float(self.var_height.get())
        except ValueError:
            self.log("warning: measurement height is not a number, keeping "
                     f"{self.cfg.slice.target_height_m:.2f} m")
            self.var_height.set(f"{self.cfg.slice.target_height_m:.2f}")
        self.cfg.slice.primary_geometry = self.var_geom.get()
        self.cfg.decision.primary_dbh_source = self.var_src.get()
        try:
            return max(1.0, float(self.var_roi.get()))
        except ValueError:
            self.var_roi.set("4.0")
            return 4.0

    def _do_measure(self):
        if self.busy or self.src is None:
            return
        if not self.targets:
            messagebox.showinfo("No targets", "Add or load at least one target first.")
            return
        roi = self._apply_settings()
        self.busy = True
        self.btn_measure.configure(state="disabled")
        self.lbl_progress.configure(text="cropping …")
        self.log(f"Measuring {len(self.targets)} target(s), ROI {roi:.1f} m")
        targets = list(self.targets)
        cfg = self.cfg
        need_grid = self.grid is None

        def work():
            try:
                self.jobs.put(("prog", "cropping all targets in one pass"))
                clouds = self.src.crop_many([(x, y) for _, x, y in targets], roi)
                grid = self.grid
                if need_grid:
                    self.jobs.put(("prog", "building ground surface"))
                    grid = build_ground_grid(
                        self.src.iter_xyz(), cell_m=cfg.ground.cell_m,
                        bounds=(self.info.mins[0], self.info.maxs[0],
                                self.info.mins[1], self.info.maxs[1]))
                    self.jobs.put(("grid", grid))
                out = []
                for i, ((tid, x, y), cloud) in enumerate(zip(targets, clouds), 1):
                    self.jobs.put(("prog", f"{tid}  ({i}/{len(targets)})"))
                    if len(cloud) == 0:
                        self.jobs.put(("log", f"{tid}: no points within {roi} m"))
                        continue
                    m = measure_tree(cloud, grid, tid, (x, y), cfg,
                                     roi_radius_m=min(roi, 1.5))
                    sec = self._section_points(m)
                    out.append((m, sec))
                self.jobs.put(("measured", out))
            except Exception as exc:
                self.jobs.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _section_points(self, m):
        """The cleaned target-height section the models were actually fitted to.

        ``measure_tree`` keeps the live arrays on a private ``_sections_internal``
        attribute and strips them from the public ``sections`` dict, because they are
        far too bulky to export. Same access path the CLI uses for its review PNGs, so
        the GUI is looking at the identical points.
        """
        internal = getattr(m, "_sections_internal", {}) or {}
        by_h = internal.get(m.primary_geometry, {})
        meta = by_h.get(f"{self.cfg.slice.target_height_m:.2f}", {})
        xy = meta.get("_xy")
        if xy is None:
            # Fall back to whichever height was flagged as the target, then to any
            # section at all: a height string that does not match to two decimals is a
            # formatting mismatch, not a reason to show the reviewer nothing.
            for meta in by_h.values():
                if meta.get("is_target_height") and meta.get("_xy") is not None:
                    xy = meta["_xy"]
                    break
        if xy is None:
            for geom in internal.values():
                for meta in geom.values():
                    if meta.get("_xy") is not None:
                        xy = meta["_xy"]
                        break
                if xy is not None:
                    break
        return None if xy is None else np.asarray(xy, dtype=float)

    def _finish_measure(self, out):
        for m, sec in out:
            self.results[m.tree_id] = m
            if sec is not None:
                self.sections[m.tree_id] = sec
        stale = self.store.check_against([m for m, _ in out])
        self._refresh_results()
        reported = sum(1 for m, _ in out if m.dbh_m is not None)
        self.log(f"Measured {len(out)} tree(s): {reported} report a diameter, "
                 f"{len(out) - reported} refused because the geometry does not "
                 f"support one.")
        if stale:
            self.log(f"warning: review decisions for {', '.join(stale)} were recorded "
                     f"against a different result and are marked stale.")
        if out:
            first = out[0][0].tree_id
            self.selected = self.selected if self.selected in self.results else first
            self._select_in_list(self.selected)
            self._refresh_detail()
            self._set_view("section")

    # ------------------------------------------------------------- results --

    def _refresh_results(self):
        self.tree.delete(*self.tree.get_children())
        for tid, _, _ in self.targets:
            m = self.results.get(tid)
            if m is None:
                self.tree.insert("", "end", values=(tid, "", "not measured", "", ""),
                                 tags=("refused",))
                continue
            dec = self.store.get(tid)
            state = dec.state if dec else review.PENDING
            cm, _ = review.resolved_diameter_cm(m, dec)
            # There is no number here, and the word says *who* decided that. An empty
            # cell reads as a bug, "0.0" would be a lie, and a number would break
            # non-negotiable 7 -- but "refused" alone conflated two different things:
            # the tool declining to measure, and a person rejecting a measurement the
            # tool did make.
            if cm is not None:
                dbh = f"{cm:.1f}"
            elif state == review.REJECTED:
                dbh = "rejected"
            elif m.dbh_m is None:
                dbh = "refused"
            else:
                dbh = "withdrawn"       # reported, but the review resolves to nothing
            tags = [f"rv_{state}"]
            if cm is None:
                tags.append("refused")
            if dec is not None and dec.is_stale:
                tags.append("stale")
            self.tree.insert("", "end", values=(
                tid, dbh,
                STATUS_SHORT.get(m.status, m.status.lower()),
                BAND_SHORT.get(m.confidence_band, m.confidence_band.lower()),
                REVIEW_SHORT.get(state, state.lower())), tags=tuple(tags))
        c = self.store.counts()
        n_ref = sum(1 for tid in self.results
                    if review.resolved_diameter_cm(
                        self.results[tid], self.store.get(tid))[0] is not None)
        decided = c["APPROVED"] + c["REJECTED"] + c["OVERRIDDEN"]
        self.lbl_counts.configure(
            text=f"{len(self.results)} measured · {n_ref} carry a diameter\n"
                 f"{c['APPROVED']} approved · {c['REJECTED']} rejected · "
                 f"{c['OVERRIDDEN']} overridden · "
                 f"{max(0, len(self.results) - decided)} pending")

    def _on_result_pick(self, _ev=None):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected = self.tree.item(sel[0], "values")[0]
        self._refresh_detail()
        self._redraw()

    def _refresh_detail(self):
        m = self.results.get(self.selected or "")
        self._set_result_enabled(m is not None)
        if m is None:
            for lbl in (self.kv_dbh, self.kv_status, self.kv_band, self.kv_model,
                        self.kv_shape, self.kv_anom, self.kv_ground, self.kv_tilt,
                        self.kv_disagree):
                lbl.configure(text="-", foreground=theme.TEXT_DIM)
            self._set_text(self.txt_reasons, "")
            self.cmb_override.configure(values=())
            self.lbl_review.configure(text="")
            return

        dec = self.store.get(m.tree_id)
        cm, prov = review.resolved_diameter_cm(m, dec)
        if cm is None:
            self.kv_dbh.configure(text="refused", foreground=theme.WARN)
        else:
            self.kv_dbh.configure(text=f"{cm:.1f} cm", foreground=theme.TEXT)
        self.kv_status.configure(
            text=m.status.replace("_", " ").lower(),
            foreground=theme.STATUS_COLOUR.get(m.status, theme.TEXT))
        self.kv_band.configure(
            text=m.confidence_band.lower(),
            foreground=theme.BAND_COLOUR.get(m.confidence_band, theme.TEXT))
        # The full "(recommendation)" pushed this past the panel width and was clipped
        # mid-word. It still has to be marked, though -- a recommendation is not a
        # decision (DEC-013) -- so it is abbreviated rather than dropped, and the
        # provenance line under the review buttons spells it out.
        self.kv_model.configure(
            text=(m.selected_model or "-")
            + ("  (rec)" if m.selection_is_recommendation else ""))
        self.kv_shape.configure(text=str(m.ellipticity.get("verdict", "-")).lower())
        anom = (m.comparison or {}).get("radial_anomaly") or {}
        anom_v = str(anom.get("verdict", "-"))
        self.kv_anom.configure(
            text=anom_v.lower(),
            foreground=theme.WARN if anom_v == "CONTAMINATION_SUSPECTED"
            else theme.TEXT_DIM)
        lg = m.local_ground
        self.kv_ground.configure(
            text="-" if lg is None else f"{lg.quality.lower()}, {lg.slope_deg:.0f}° slope",
            foreground=theme.WARN if (lg and lg.quality != "GOOD") else theme.TEXT_DIM)
        ax = m.axis
        self.kv_tilt.configure(text="-" if ax is None or not ax.valid
                               else f"{ax.tilt_deg:.1f}° toward {ax.azimuth_deg:.0f}°")
        self.kv_disagree.configure(
            text=_fmt_cm((m.comparison.get("max_pairwise_difference_m") or 0) * 100
                         if m.comparison.get("max_pairwise_difference_m") is not None
                         else None, 2))

        lines = []
        if m.dbh_m is None:
            lines.append(("head", "NO DIAMETER REPORTED — the geometry does not "
                                  "constrain one. Every candidate fit is still "
                                  "exported so you can see what the data implied.\n"))
        for w in (m.warnings or []):
            lines.append(("warn", f"! {w}\n"))
        for r in (m.reasons or []):
            lines.append((None, f"· {r}\n"))
        if not lines:
            lines.append((None, "no caveats recorded\n"))
        self._set_text(self.txt_reasons, lines)

        # Override choices: only models that actually produced a diameter. A model with
        # no number is not a thing you can override to.
        opts = [f.model for f in sorted(m.candidate_results, key=lambda f: f.model)
                if f.diameter_m is not None and np.isfinite(f.diameter_m)]
        self.cmb_override.configure(values=tuple(opts))
        if dec and dec.override_model in opts:
            self.var_override.set(dec.override_model)
        elif m.selected_model in opts:
            self.var_override.set(m.selected_model)

        txt = prov
        if dec:
            if dec.note:
                txt += f"\nnote: {dec.note}"
            if dec.is_stale:
                txt += ("\nSTALE: recorded against a different result for this tree. "
                        "Re-review it.")
        self.lbl_review.configure(
            text=txt,
            foreground=theme.WARN if (dec and dec.is_stale) else theme.TEXT_FAINT)
        self._set_text(self.txt_note, (dec.note if dec else ""), editable=True)

    def _set_text(self, widget, content, editable=False):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if isinstance(content, str):
            widget.insert("1.0", content)
        else:
            for tag, chunk in content:
                widget.insert("end", chunk, (tag,) if tag else ())
        if not editable:
            widget.configure(state="disabled")

    # -------------------------------------------------------------- review --

    def _set_reviewer(self):
        name = self.var_reviewer.get().strip()
        if name != self.store.reviewer:
            self.store.reviewer = name
            self.store.save()

    def _record(self, state, override_model=None):
        m = self.results.get(self.selected or "")
        if m is None:
            return
        self._set_reviewer()
        note = self.txt_note.get("1.0", "end").strip()
        try:
            self.store.record(m, state, note=note, override_model=override_model)
        except ValueError as exc:
            messagebox.showwarning("Cannot record that", str(exc))
            self.log(f"warning: {exc}")
            return
        self.log(f"{m.tree_id}: {state.lower()}"
                 + (f" to {override_model}" if override_model else ""))
        self._refresh_results()
        self._select_in_list(m.tree_id)
        self._refresh_detail()

    def _approve(self):
        self._record(review.APPROVED)

    def _reject(self):
        self._record(review.REJECTED)

    def _override(self):
        model = self.var_override.get().strip()
        if not model:
            messagebox.showwarning("Pick a model",
                                   "Choose which model to override to.")
            return
        self._record(review.OVERRIDDEN, override_model=model)

    def _reset_review(self):
        if self.selected:
            self.store.clear(self.selected)
            self.log(f"{self.selected}: review reset to pending")
            self._refresh_results()
            self._select_in_list(self.selected)
            self._refresh_detail()

    # -------------------------------------------------------------- output --

    def _show_models(self):
        m = self.results.get(self.selected or "")
        if m is None:
            return
        win = tk.Toplevel(self.root)
        win.title(f"{m.tree_id} — every candidate model")
        win.configure(background=theme.BG)
        win.geometry("900x420")
        cols = ("model", "D", "definition", "rmse", "cov", "gap", "inlier", "boot",
                "valid", "why not")
        tv = ttk.Treeview(win, columns=cols, show="headings", height=14)
        widths = (200, 70, 150, 66, 50, 50, 56, 60, 46, 300)
        for col, w in zip(cols, widths):
            tv.heading(col, text=col)
            tv.column(col, width=w, anchor="w", stretch=(col == "why not"))
        tv.tag_configure("no", foreground=theme.TEXT_FAINT)
        tv.tag_configure("sel", foreground=theme.OK)
        fits = {f.model: f for f in m.candidate_results}
        for name in [n for n in MODEL_ORDER if n in fits] + \
                    [n for n in sorted(fits) if n not in MODEL_ORDER]:
            f = fits[name]
            tags = ("sel",) if name == m.selected_model else () if f.valid else ("no",)
            tv.insert("", "end", values=(
                name,
                "-" if f.diameter_m is None else f"{f.diameter_m * 100:.1f} cm",
                (f.diameter_definition or "")[:60],
                "-" if f.rmse_m is None or not np.isfinite(f.rmse_m)
                else f"{f.rmse_m * 1000:.1f} mm",
                "-" if f.angular_coverage is None else f"{f.angular_coverage:.0%}",
                "-" if f.largest_gap_deg is None else f"{f.largest_gap_deg:.0f}°",
                "-" if f.inlier_fraction is None else f"{f.inlier_fraction:.0%}",
                "-" if f.bootstrap_std_m is None
                else f"{f.bootstrap_std_m * 1000:.1f} mm",
                "yes" if f.valid else "NO",
                "" if f.valid else "; ".join(f.warnings),
            ), tags=tags)
        sb = ttk.Scrollbar(win, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True, padx=6, pady=6)
        ttk.Label(win, text="Every model is fitted before any is judged, and a "
                            "declined fit is evidence — that is why its numbers are "
                            "still here.", style="Status.TLabel").pack(
            anchor="w", padx=8, pady=(0, 6))

    def _export_png(self):
        """Write the full four-panel report PNG, the same one the CLI produces.

        The embedded section view is deliberately leaner than this: it shows what you
        need to judge a fit. The report is what goes in a document, so it stays
        light-themed and keeps the diagnostics table.
        """
        m = self.results.get(self.selected or "")
        xy = self.sections.get(self.selected or "")
        if m is None or xy is None:
            self.log("warning: no section points were kept for this tree")
            return
        if self.busy:
            self.log("warning: busy — try again when the current run finishes")
            return
        self.busy = True
        self.lbl_progress.configure(text=f"writing {m.tree_id} report …")
        outdir = self.outdir
        tid = review.safe_id(m.tree_id)
        roi = self._apply_settings()
        grid = self.grid
        centre = m.center_xy or (0.0, 0.0)
        height = self.cfg.slice.target_height_m

        def work():
            written = []
            try:
                outdir.mkdir(parents=True, exist_ok=True)
                written.append(plot_measurement(m, xy, outdir / f"{tid}_review.png"))
                # The ground figure needs the ROI cloud back, which means a read. Worth
                # it: a wrong diameter is very often a wrong ground datum, and this is
                # the figure that shows which.
                if grid is not None and self.src is not None:
                    xyz = self.src.crop_cylinder(centre, roi)
                    if len(xyz):
                        hag = height_above_ground(xyz, grid)
                        written.append(plot_ground_check(
                            grid, xyz, hag, centre, outdir / f"{tid}_ground.png",
                            radius_m=roi, target_height_m=height))
                self.jobs.put(("png", written))
            except Exception as exc:
                self.jobs.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _export_tables(self):
        if not self.results:
            return
        try:
            self.outdir.mkdir(parents=True, exist_ok=True)
            ms = [self.results[t] for t, _, _ in self.targets if t in self.results]
            run_meta = {
                "software": "dbh-tool", "version": __version__,
                "interface": "gui",
                "input": self.info.to_dict() if self.info else None,
                "config": self.cfg.to_dict(),
                "ground": self.grid.to_meta() if self.grid is not None else None,
                # The review file is a separate artefact on purpose: a measurement
                # table records what the tool found, not what a person concluded.
                "review_decisions": str(self.store.path),
            }
            c = write_csv(ms, self.outdir / "measurements.csv")
            j = write_json(ms, self.outdir / "measurements.json", run_meta=run_meta)
            self.cfg.to_yaml(self.outdir / "run_config.yaml")
            p = self.store.save()
            for path in (c, j, self.outdir / "run_config.yaml", p):
                self.log(f"wrote {path}")
        except Exception as exc:
            self.log(f"cannot export: {type(exc).__name__}: {exc}")
            messagebox.showerror("Export failed", str(exc))

    def _open_outdir(self):
        self.outdir.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.startfile(str(self.outdir))       # noqa: S606  (Windows only)
        except Exception:
            self.log(f"output directory: {self.outdir.resolve()}")

    def _do_load_config(self):
        path = filedialog.askopenfilename(
            title="Run configuration", parent=self.root,
            filetypes=[("YAML / JSON", "*.yaml *.yml *.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.cfg = RunConfig.load(path)
        except Exception as exc:
            messagebox.showerror("Bad configuration", str(exc))
            self.log(f"cannot load config: {exc}")
            return
        self.cfg_path = path
        self.var_height.set(f"{self.cfg.slice.target_height_m:.2f}")
        self.var_geom.set(self.cfg.slice.primary_geometry)
        self.var_src.set(self.cfg.decision.primary_dbh_source)
        self.lbl_cfg.configure(text=Path(path).name)
        self.grid = None
        self.log(f"Loaded configuration {Path(path).name}. Re-measure to apply it.")

    def _do_save_config(self):
        path = filedialog.asksaveasfilename(
            title="Save run configuration", parent=self.root,
            defaultextension=".yaml", initialfile="run_config.yaml",
            filetypes=[("YAML", "*.yaml"), ("All files", "*.*")])
        if not path:
            return
        self._apply_settings()
        self.cfg.to_yaml(path)
        self.log(f"wrote {path}")

    # ---------------------------------------------------------------- draw --

    def _redraw(self):
        if self.raster is None:
            self._show_empty()
            return
        measured = {t: m.status for t, m in self.results.items()}
        if self.view == "plan":
            plots.draw_plan(self.fig, self.raster, self.targets,
                            selected=self.selected, measured=measured)
            self.lbl_view.configure(
                text="double-click to place a target · click to select one")
            return

        m = self.results.get(self.selected or "")
        if m is None:
            self.fig.message("Select a measured tree to review its section.")
            self.lbl_view.configure(text="")
            return
        if self.view == "profile":
            plots.draw_profile(self.fig, m)
            self.lbl_view.configure(text=f"{m.tree_id} · multi-height profile")
            return

        xy = self.sections.get(m.tree_id)
        if xy is None or len(xy) == 0:
            self.fig.message(f"{m.tree_id}: no section points were kept.\n"
                             f"status {m.status}")
            return
        fits = {f.model: f for f in m.candidate_results}
        show = {n for n, f in fits.items() if f.valid}
        if self.var_declined.get():
            show |= set(fits)
        plots.draw_section(self.fig, m, xy, show=show,
                           show_outliers=self.var_outliers.get())
        self.lbl_view.configure(
            text=f"{m.tree_id} · {len(xy)} points · "
                 f"{'dashed = declined' if self.var_declined.get() else 'valid models only'}")

    # ---------------------------------------------------------------- pump --

    def _poll(self):
        """Drain worker messages. Must never stop rescheduling itself.

        An exception escaping here would kill the loop outright: results would never be
        collected, ``busy`` would stay True, and every later run would be refused while
        the window looked perfectly healthy. The reschedule lives in a finally block for
        exactly that reason.
        """
        try:
            while True:
                try:
                    kind, payload = self.jobs.get_nowait()
                except queue.Empty:
                    break
                if kind == "log":
                    self.log("  " + str(payload))
                elif kind == "prog":
                    self.lbl_progress.configure(text=str(payload))
                elif kind == "grid":
                    self.grid = payload
                elif kind == "cloud":
                    self.busy = False
                    self.btn_import.configure(state="normal")
                    try:
                        self._install_cloud(*payload)
                    except Exception as exc:
                        # A half-installed cloud is the dangerous outcome: the plan
                        # view can render while the header panel stays empty and the
                        # Measure button stays disabled, which reads as "still
                        # loading" rather than "broken". Go back to a clean no-cloud
                        # state instead.
                        self._forget_cloud()
                        self.log(f"error while opening: {type(exc).__name__}: {exc}")
                        messagebox.showerror("Could not open that cloud", str(exc))
                elif kind == "loaderr":
                    self.busy = False
                    self.btn_import.configure(state="normal")
                    self.lbl_file.configure(text="no cloud loaded")
                    self.log("error: " + str(payload))
                    messagebox.showerror("Could not open that cloud", str(payload))
                elif kind == "measured":
                    self.busy = False
                    self.btn_measure.configure(state="normal")
                    self.lbl_progress.configure(text="")
                    try:
                        self._finish_measure(payload)
                    except Exception as exc:
                        self.log(f"error after measuring: "
                                 f"{type(exc).__name__}: {exc}")
                elif kind == "detect":
                    self.busy = False
                    self._finish_detect(payload)
                elif kind == "png":
                    self.busy = False
                    self.lbl_progress.configure(text="")
                    for p in payload:
                        self.log(f"wrote {p}")
                elif kind == "error":
                    self.busy = False
                    self.btn_measure.configure(state="normal")
                    self.lbl_progress.configure(text="")
                    self.log("error: " + str(payload))
                    messagebox.showerror("That did not work", str(payload))
        finally:
            self.root.after(120, self._poll)

    def _finish_detect(self, cands):
        self.lbl_progress.configure(text="")
        accepted = [c for c in cands if c.accepted]
        added = 0
        for i, c in enumerate(accepted, 1):
            tid = f"D{i:02d}"
            if any(t[0] == tid for t in self.targets):
                continue
            self.targets.append((tid, float(c.center_xy[0]), float(c.center_xy[1])))
            added += 1
        self._refresh_targets()
        self.log(f"Detector examined {len(cands)} cluster(s), accepted "
                 f"{len(accepted)}, added {added} target(s).")
        self.log("warning: detection is unvalidated — precision and recall have never "
                 "been measured. Hand-check every one of these before using them.")

    # ----------------------------------------------------------------- run --

    def run(self):
        self.root.mainloop()


def launch(cloud=None, outdir="out", targets=None, config=None):
    """Open the review window."""
    App(cloud=cloud, outdir=outdir, targets=targets, config=config).run()
