"""Small reusable widgets for the panels.

Same set as the sibling 360-pointcloud-tool picker, and for the same reasons: a
fixed-width side panel that scrolls when the window is short, cards that read as
sections without four lines drawn round them, and a collapsible log strip whose
header always carries the latest line.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


def card(parent, title: str, pady=(10, 0)) -> ttk.LabelFrame:
    f = ttk.LabelFrame(parent, text=title, padding=8)
    f.pack(fill="x", padx=8, pady=pady)
    return f


def kv(parent, row: int, key: str, value: str = "-", colour: str | None = None):
    """A label/value pair on a grid row. Returns the value label, to update later."""
    ttk.Label(parent, text=key, style="Dim.TLabel").grid(
        row=row, column=0, sticky="w", padx=(0, 8))
    val = ttk.Label(parent, text=value, style="Mono.TLabel",
                    foreground=colour or theme.TEXT)
    val.grid(row=row, column=1, sticky="w")
    return val


def hint(parent, text: str, width: int = 306) -> ttk.Label:
    """Explanatory small print.

    wraplength rather than hard-coded newlines: manual breaks assume a pixel width the
    panel does not actually have, and the tail of each line ends up clipped.
    """
    lbl = ttk.Label(parent, text=text, style="Hint.TLabel", justify="left",
                    wraplength=width - 56)
    lbl.pack(anchor="w", fill="x", pady=(2, 0))
    return lbl


def rule(parent) -> ttk.Frame:
    return ttk.Frame(parent, style="Rule.TFrame", width=1)


def row(parent, pady=1) -> ttk.Frame:
    f = ttk.Frame(parent, style="Card.TFrame")
    f.pack(fill="x", pady=pady)
    return f


def scroll_panel(parent, width: int):
    """Fixed-width side panel whose contents scroll when the window is too short.

    Without this the last section is simply cut off at the bottom edge on a laptop
    screen -- and the section that gets cut is whichever happens to be last, which is
    not a decision worth leaving to the window height.
    """
    outer = ttk.Frame(parent, width=width)
    outer.pack_propagate(False)
    # Grid, not pack. Packed after an expanding canvas, the scrollbar is left with no
    # space at all and Tk quietly unmaps it -- the panel scrolls but nothing says so.
    outer.grid_rowconfigure(0, weight=1)
    outer.grid_columnconfigure(0, weight=1)

    canvas = tk.Canvas(outer, bg=theme.PANEL, highlightthickness=0, width=width - 16)
    canvas.grid(row=0, column=0, sticky="nsew")
    bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    bar.grid(row=0, column=1, sticky="ns")
    bar.grid_remove()
    canvas.configure(yscrollcommand=bar.set)

    inner = ttk.Frame(canvas)
    win = canvas.create_window(0, 0, anchor="nw", window=inner)

    def resize(_ev=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfigure(win, width=canvas.winfo_width())
        # Only show the bar when it can do something. The two thresholds are
        # deliberate: showing the bar narrows the canvas, which re-wraps hint text and
        # makes the content taller, so a single threshold flips back and forth forever.
        h = canvas.winfo_height()
        if inner.winfo_reqheight() > h and not bar.winfo_ismapped():
            bar.grid()
        elif bar.winfo_ismapped() and inner.winfo_reqheight() + 24 < h:
            bar.grid_remove()

    inner.bind("<Configure>", resize)
    canvas.bind("<Configure>", resize)
    outer._dbh_scroll = (canvas, inner)
    return outer, inner


def bind_wheel(outer) -> None:
    """Route wheel events anywhere in a scroll panel to that panel.

    Tk delivers a wheel event to the widget under the pointer, so binding the container
    alone means the wheel does nothing over the controls -- which is most of the panel.
    Returning "break" also stops the class binding, otherwise rolling the wheel over a
    combobox silently changes its value while you are only trying to scroll.
    """
    canvas, inner = outer._dbh_scroll

    def wheel(ev):
        if inner.winfo_reqheight() > canvas.winfo_height():
            canvas.yview_scroll(-1 if ev.delta > 0 else 1, "units")
        return "break"

    def walk(w):
        w.bind("<MouseWheel>", wheel, add="+")
        for child in w.winfo_children():
            walk(child)

    walk(outer)


class LogStrip:
    """A one-line summary that expands into the full log.

    Collapsed by default -- most of what goes past is progress noise -- but the header
    always carries the latest line, and anything that needs acting on opens the pane by
    itself rather than hiding a warning behind a disclosure triangle.
    """

    def __init__(self, parent):
        self.frame = ttk.Frame(parent, style="Bg.TFrame")
        self.open = False
        self._last = ""
        self.btn = ttk.Button(self.frame, style="Log.TButton", command=self.toggle)
        self.btn.pack(fill="x")

        self.body = ttk.Frame(self.frame, style="Bg.TFrame")
        self.text = tk.Text(self.body, height=9, wrap="word", state="disabled")
        theme.style_text(self.text)
        sb = ttk.Scrollbar(self.body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, col in (("err", theme.DANGER), ("warn", theme.WARN),
                         ("ok", theme.OK), ("head", theme.TEXT)):
            self.text.tag_configure(tag, foreground=col)
        self._draw_header()

    def toggle(self):
        self.open = not self.open
        if self.open:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.forget()
        self._draw_header()

    def _draw_header(self):
        tail = self._last
        if len(tail) > 120:
            tail = tail[:117] + "..."
        # The small triangles (U+25BE/U+25B8) render as near-invisible specks at 8pt in
        # Segoe UI; the full-size ones actually read as a disclosure control.
        arrow = "▼" if self.open else "▶"
        self.btn.configure(text=f"{arrow}  Log     {tail}")

    def __call__(self, msg):
        """Append a line, classifying it so errors and warnings are visible."""
        text = str(msg).rstrip()
        low = text.lower()
        tag = ("err" if "error" in low or low.startswith("cannot") else
               "warn" if "warning" in low or "refus" in low else
               "ok" if ("saved" in low or "wrote" in low or " done" in low
                        or low.startswith("ready")) else None)
        self.text.configure(state="normal")
        self.text.insert("end", text + "\n", tag or ())
        self.text.see("end")
        self.text.configure(state="disabled")

        for line in reversed(text.splitlines()):
            if line.strip():
                self._last = line.strip()
                break
        self._draw_header()
        # A warning or an error is the whole reason to have a log; do not make it a
        # thing you have to think to go and look for.
        if tag in ("err", "warn") and not self.open:
            self.toggle()
