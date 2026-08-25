"""Dark navy theme for the Tk interface.

**Shared with the sibling 360-pointcloud-tool project** (`pano360/theme.py`), kept
deliberately identical so the two tools read as one family. If you change a colour
here, change it there. It is copied rather than imported because the two are
separate repositories with separate dependency sets, and a GUI theme is not worth
coupling them over.

Tk's stock look is a light grey that actively fights the imagery this tool exists to
produce. Point clouds and panoramas are mostly dark, and bright chrome around them
wrecks the tonal judgement the preview is for -- your eye adapts to the panel, not the
picture. So: dark, and navy rather than neutral grey, because scan data is dominated by
greens and browns and a neutral grey panel sits right in the middle of them. Blue reads
as "chrome" and stays out of the way.

Every colour lives in this module. The plan view and the cross-section view are drawn
by matplotlib rather than by ttk, so without a single source of truth the plotted half
and the widget half drift apart on the first tweak. :func:`mpl_rc` exists to hand the
same palette to matplotlib.

ttk's clam theme is the base for one practical reason: it is the only bundled theme whose
elements are drawn by Tk itself, so configured colours actually take effect. On Windows
the default vista theme hands entries and buttons to the OS theme engine, which silently
ignores any background you set -- you get a dark window full of white text boxes.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

# -- palette ----------------------------------------------------------------

BG = "#080d16"           # window, behind everything
PANEL = "#0e1626"        # the two side panels
CARD = "#141f34"         # group boxes within a panel
CARD_HI = "#1a2842"      # hovered or raised card
INPUT = "#0a1120"        # editable fields, and the canvases
BORDER = "#25375b"       # field outlines and visible dividers
BORDER_SOFT = "#1a2740"  # separators that should barely register

TEXT = "#dbe5f4"
TEXT_DIM = "#94a5c1"     # labels and secondary information
TEXT_FAINT = "#61789b"   # hints nobody needs to read twice

ACCENT = "#3d8bfd"       # primary action, selection, focus
ACCENT_HI = "#5fa2ff"
ACCENT_LO = "#1d4a8a"
OK = "#4ade80"
WARN = "#fbbf24"
DANGER = "#ff5a5f"

MARKER = "#ff4d4f"       # requested target position crosshair
HEADING = "#ffc94d"      # fitted stem centre, where it differs from the request
GROUND = "#4da3ff"       # estimated ground level

# Status colours. These map the six measurement outcomes onto the palette above, and
# the mapping carries meaning that must not be prettified away: a refusal to report a
# diameter is *not* an error, and must not be red alongside genuine failures, or
# operators learn to ignore red. Amber is "look at this", red is "the data does not
# support a number", green is "the evidence supports what is reported".
STATUS_COLOUR = {
    "ACCEPTED_CIRCULAR": OK,
    "ACCEPTED_ELLIPTICAL": OK,
    "ACCEPTED_IRREGULAR": OK,
    "REVIEW_REQUIRED": WARN,
    "INVALID_MEASUREMENT_HEIGHT": DANGER,
    "FAILED_INSUFFICIENT_DATA": DANGER,
}

# Confidence bands are qualitative on purpose (docs 02 section 16: there is no
# calibration set, so a percentage would be false precision). Colour is the only
# encoding they get -- no bars, no meters, nothing that implies a scale.
BAND_COLOUR = {
    "HIGH": OK,
    "MEDIUM": WARN,
    "LOW": "#fb923c",
    "REVIEW_REQUIRED": WARN,
    "FAILED": DANGER,
}

REVIEW_COLOUR = {
    "PENDING": TEXT_FAINT,
    "APPROVED": OK,
    "REJECTED": DANGER,
    "OVERRIDDEN": ACCENT_HI,
}

# -- fonts ------------------------------------------------------------------

UI = ("Segoe UI", 9)
UI_BOLD = ("Segoe UI Semibold", 9)
UI_SMALL = ("Segoe UI", 8)
UI_TINY = ("Segoe UI", 7)
MONO = ("Consolas", 9)
MONO_SMALL = ("Consolas", 8)
MONO_TINY = ("Consolas", 7)


def rgb(colour: str) -> tuple[int, int, int]:
    """"#rrggbb" as an (r, g, b) tuple, for the PIL calls that need one."""
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def apply(root: tk.Misc) -> ttk.Style:
    """Restyle every ttk class the GUI uses. Call once, before building any widgets."""
    style = ttk.Style(root)
    style.theme_use("clam")

    root.configure(background=BG)
    # A combobox dropdown is a bare Tk listbox the widget creates for itself, so it
    # cannot be reached through ttk styling -- only through the option database.
    for opt, val in (
        ("*TCombobox*Listbox.background", INPUT),
        ("*TCombobox*Listbox.foreground", TEXT),
        ("*TCombobox*Listbox.selectBackground", ACCENT),
        ("*TCombobox*Listbox.selectForeground", "#ffffff"),
        ("*TCombobox*Listbox.borderWidth", "0"),
    ):
        root.option_add(opt, val)

    style.configure(
        ".",
        background=PANEL, foreground=TEXT, fieldbackground=INPUT,
        bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
        troughcolor=INPUT, focuscolor=ACCENT, font=UI,
        insertcolor=TEXT, selectbackground=ACCENT_LO, selectforeground=TEXT,
    )

    style.configure("TFrame", background=PANEL)
    style.configure("Bg.TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Rule.TFrame", background=BORDER_SOFT)

    style.configure("TLabel", background=PANEL, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Dim.TLabel", background=CARD, foreground=TEXT_DIM)
    style.configure("Hint.TLabel", background=CARD, foreground=TEXT_FAINT, font=UI_TINY)
    style.configure("Mono.TLabel", background=CARD, foreground=TEXT_DIM, font=MONO_SMALL)
    style.configure("Status.TLabel", background=BG, foreground=TEXT_DIM, font=MONO_SMALL)

    # Group boxes. clam boxes every section in a full rectangle; a flat card with a
    # coloured caption reads as a section without drawing four lines around it.
    style.configure("TLabelframe", background=CARD, bordercolor=BORDER_SOFT,
                    lightcolor=CARD, darkcolor=CARD, relief="flat", borderwidth=1)
    style.configure("TLabelframe.Label", background=CARD, foreground=ACCENT_HI,
                    font=UI_BOLD)

    style.configure("TButton", background=CARD_HI, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=CARD_HI, darkcolor=CARD_HI,
                    relief="flat", padding=(8, 5), anchor="center")
    style.map("TButton",
              background=[("pressed", ACCENT_LO), ("active", "#22344f"),
                          ("disabled", "#121b2c")],
              foreground=[("disabled", "#4a5a75")],
              bordercolor=[("active", ACCENT_LO)])

    # Tight buttons for rows of four in a 300px panel, where the default padding costs
    # more than the labels themselves and the last one gets its text clipped.
    style.configure("Small.TButton", background=CARD_HI, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=CARD_HI, darkcolor=CARD_HI,
                    relief="flat", padding=(2, 4), font=UI_SMALL, anchor="center")
    style.map("Small.TButton",
              background=[("pressed", ACCENT_LO), ("active", "#22344f"),
                          ("disabled", "#121b2c")],
              foreground=[("disabled", "#4a5a75")],
              bordercolor=[("active", ACCENT_LO)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    relief="flat", padding=(8, 7), font=UI_BOLD)
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_LO), ("active", ACCENT_HI),
                          ("disabled", "#1b2740")],
              foreground=[("disabled", "#54658a")],
              bordercolor=[("active", ACCENT_HI), ("disabled", "#1b2740")])

    # Segmented view switch: two latching buttons, so which view you are looking at is
    # visible rather than inferred.
    style.configure("Seg.TButton", background=BG, foreground=TEXT_DIM,
                    bordercolor=BORDER_SOFT, lightcolor=BG, darkcolor=BG,
                    relief="flat", padding=(15, 6), font=UI)
    style.map("Seg.TButton",
              background=[("pressed", ACCENT_LO), ("active", CARD_HI)],
              foreground=[("active", TEXT)])
    style.configure("SegOn.TButton", background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    relief="flat", padding=(15, 6), font=UI_BOLD)
    style.map("SegOn.TButton",
              background=[("pressed", ACCENT), ("active", ACCENT_HI)])

    # Same switch, tighter: the tool row fits four of these across the side panel, and at
    # the view switch's padding the labels came out clipped ("Brush" read as "Brusl").
    for name, base in (("Tool.TButton", "Seg.TButton"),
                       ("ToolOn.TButton", "SegOn.TButton")):
        style.configure(name, **{k: v for k, v in
                                 style.configure(base).items() if k != "padding"})
        style.configure(name, padding=(2, 6))
        style.map(name, **style.map(base))

    # The log strip header spans the whole width and should not look pressable.
    style.configure("Log.TButton", background=BG, foreground=TEXT_DIM,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG,
                    relief="flat", padding=(6, 3), font=UI_SMALL, anchor="w")
    style.map("Log.TButton",
              background=[("pressed", BG), ("active", "#101a2b")],
              foreground=[("active", TEXT)])

    for cls in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(cls, fieldbackground=INPUT, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BORDER_SOFT,
                        darkcolor=BORDER_SOFT, insertcolor=TEXT,
                        arrowcolor=TEXT_DIM, padding=(4, 3), relief="flat")
        style.map(cls,
                  bordercolor=[("focus", ACCENT), ("hover", ACCENT_LO)],
                  fieldbackground=[("readonly", INPUT), ("disabled", "#0c1322")],
                  foreground=[("disabled", "#48587a")],
                  arrowcolor=[("active", ACCENT_HI), ("disabled", "#3a4863")])
    # Without this a readonly combobox keeps a selection highlight across the whole
    # field, which looks like the control is stuck mid-edit.
    style.map("TCombobox",
              selectbackground=[("readonly", INPUT)],
              selectforeground=[("readonly", TEXT)])

    for cls in ("TRadiobutton", "TCheckbutton"):
        style.configure(cls, background=CARD, foreground=TEXT,
                        indicatorbackground=INPUT, indicatorforeground=ACCENT,
                        bordercolor=BORDER, focuscolor=CARD, padding=(2, 3))
        style.map(cls,
                  background=[("active", CARD)],
                  foreground=[("disabled", "#48587a")],
                  indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_LO),
                                  ("!selected", INPUT)],
                  bordercolor=[("active", ACCENT_LO), ("selected", ACCENT)])

    style.configure("TScale", background=ACCENT, troughcolor=INPUT,
                    bordercolor=BORDER_SOFT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("TScale",
              background=[("active", ACCENT_HI), ("disabled", "#2a3550")],
              troughcolor=[("disabled", "#0c1322")])

    style.configure("TScrollbar", background=CARD_HI, troughcolor=BG,
                    bordercolor=BG, arrowcolor=TEXT_DIM,
                    lightcolor=CARD_HI, darkcolor=CARD_HI, relief="flat")
    style.map("TScrollbar",
              background=[("active", ACCENT_LO), ("disabled", BG)],
              arrowcolor=[("disabled", BG)])

    style.configure("TSeparator", background=BORDER_SOFT)
    return style


def dark_titlebar(root: tk.Misc) -> None:
    """Ask Windows for a dark title bar. Silent no-op wherever that is unsupported.

    The caption is the one piece of chrome a Tk app cannot paint itself, and a white bar
    above an almost-black window is the first thing you notice. Attribute 20 is the
    documented value from Windows 10 20H1 onward and 19 was the undocumented one before
    that, so both are tried and the first that succeeds wins.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            return
        flag = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag)
            ) == 0:
                break
    except Exception:
        # Cosmetic only. A missing dwmapi or a locked-down build must never stop the
        # tool from opening.
        pass


def style_text(widget: tk.Text) -> None:
    """Colour a plain tk.Text, which has no ttk style to inherit from."""
    widget.configure(
        background=BG, foreground=TEXT_DIM, insertbackground=TEXT,
        selectbackground=ACCENT_LO, selectforeground=TEXT,
        relief="flat", borderwidth=0, highlightthickness=0,
        font=MONO_SMALL, padx=8, pady=4,
    )


def mpl_rc() -> dict:
    """matplotlib rcParams that put a figure on the same palette as the panels.

    Returned rather than applied globally: the export figures in
    ``visualization/cross_section.py`` are deliberately light, because they end up in
    reports and get printed, and a global rcParams change here would silently restyle
    them. Use with ``matplotlib.rc_context(theme.mpl_rc())``.
    """
    return {
        "figure.facecolor": INPUT,
        "axes.facecolor": INPUT,
        "savefig.facecolor": INPUT,
        "axes.edgecolor": BORDER,
        "axes.labelcolor": TEXT_DIM,
        "axes.titlecolor": TEXT,
        "text.color": TEXT,
        "xtick.color": TEXT_DIM,
        "ytick.color": TEXT_DIM,
        "grid.color": BORDER_SOFT,
        "legend.facecolor": CARD,
        "legend.edgecolor": BORDER,
        "legend.labelcolor": TEXT,
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
        "font.size": 8.0,
        "axes.titlesize": 9.0,
        "figure.autolayout": False,
    }
