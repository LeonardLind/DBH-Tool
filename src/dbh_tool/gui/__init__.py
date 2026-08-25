"""Tk review interface.

Imported lazily by the CLI so that a headless or minimal install -- one without a
working tkinter, which is common on trimmed Linux Pythons -- can still run every
measurement command. Nothing in the scientific core imports this package.
"""
from __future__ import annotations


def launch(*args, **kwargs):
    """Open the review window. Thin re-export so ``from dbh_tool.gui import launch``
    does not pull matplotlib's Tk backend in at import time."""
    from .app import launch as _launch
    return _launch(*args, **kwargs)
