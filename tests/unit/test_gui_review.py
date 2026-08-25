"""Review persistence and the rules that stop a review laundering a measurement (M7).

The GUI itself is not unit-tested -- Tk needs a display and the interesting logic is
not in the widgets. What *is* tested is the part that can quietly produce a wrong
number: the decision store, and the single function that decides what a reviewed tree
reports.

The rule these tests exist to protect is non-negotiable 7: a hard failure emits no
headline diameter, and nothing downstream -- including a human override -- may turn
that refusal into a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from dbh_tool.fitting.common import FitResult
from dbh_tool.gui import review
from dbh_tool.visualization.cross_section import model_boundary_xy


@dataclass
class FakeMeasurement:
    """Just the attributes the review store reads."""

    tree_id: str = "S01"
    status: str = "ACCEPTED_CIRCULAR"
    selected_model: str | None = "circle_ransac"
    dbh_m: float | None = 0.40
    candidate_results: list = field(default_factory=list)


def _fits(**diam):
    out = []
    for name, d in diam.items():
        f = FitResult(model=name, diameter_m=d, valid=d is not None)
        out.append(f)
    return out


def reported(tree_id="S01"):
    return FakeMeasurement(
        tree_id=tree_id, status="ACCEPTED_CIRCULAR", selected_model="circle_ransac",
        dbh_m=0.40, candidate_results=_fits(circle_ransac=0.40, ellipse=0.44,
                                            circle_geometric=0.41))


def refused(tree_id="S07"):
    """A hard failure: every candidate still fitted, but no headline diameter."""
    return FakeMeasurement(
        tree_id=tree_id, status="REVIEW_REQUIRED", selected_model="circle_algebraic",
        dbh_m=None, candidate_results=_fits(circle_algebraic=3.62, ellipse=1.01))


# ------------------------------------------------- the diameter resolution rule
def test_pending_reports_the_tool_number():
    cm, prov = review.resolved_diameter_cm(reported(), None)
    assert cm == pytest.approx(40.0)
    assert "not reviewed" in prov


def test_pending_on_a_refusal_reports_nothing():
    cm, _ = review.resolved_diameter_cm(refused(), None)
    assert cm is None


def test_rejection_withdraws_the_number(tmp_path):
    store = review.ReviewStore(tmp_path)
    m = reported()
    dec = store.record(m, review.REJECTED, note="seed is on a liana, not the stem")
    cm, prov = review.resolved_diameter_cm(m, dec)
    assert cm is None
    assert "rejected" in prov


def test_approval_of_a_refusal_records_agreement_not_a_number(tmp_path):
    """The single most important rule in this module.

    A reviewer confirming a refusal is agreeing that there is no diameter. If that
    produced one, the tool's refusal to answer would be defeated by the very step
    meant to scrutinise it.
    """
    store = review.ReviewStore(tmp_path)
    m = refused()
    dec = store.record(m, review.APPROVED)
    cm, prov = review.resolved_diameter_cm(m, dec)
    assert cm is None
    assert "refusal confirmed" in prov
    assert "does not support" in prov


def test_override_cannot_create_a_diameter_from_a_refusal(tmp_path):
    """A refused section has candidate fits with numbers in them. Overriding to one of
    those would extract a diameter the geometry does not support -- exactly the
    silent-wrong-answer this project exists to prevent.
    """
    store = review.ReviewStore(tmp_path)
    m = refused()
    dec = store.record(m, review.OVERRIDDEN, note="looks fine to me",
                       override_model="circle_algebraic")
    # The decision is recorded -- the reviewer's opinion is real and is kept --
    # but it resolves to no number.
    assert dec.state == review.OVERRIDDEN
    assert dec.override_dbh_cm == pytest.approx(362.0)
    cm, prov = review.resolved_diameter_cm(m, dec)
    assert cm is None
    assert "cannot create one" in prov
    # And the stored note says so, so nobody reads the record as an approved 3.62 m.
    assert "cannot produce one" in dec.note


def test_override_on_a_reported_tree_uses_the_chosen_model(tmp_path):
    store = review.ReviewStore(tmp_path)
    m = reported()
    dec = store.record(m, review.OVERRIDDEN, override_model="ellipse",
                       note="section is genuinely oval, circle understates it")
    cm, prov = review.resolved_diameter_cm(m, dec)
    assert cm == pytest.approx(44.0)
    assert "ellipse" in prov


# ------------------------------------------------------------- input validation
@pytest.mark.parametrize("state", [review.REJECTED, review.OVERRIDDEN])
def test_disagreeing_with_the_tool_requires_a_note(tmp_path, state):
    store = review.ReviewStore(tmp_path)
    with pytest.raises(ValueError, match="note"):
        store.record(reported(), state, note="  ", override_model="ellipse")


def test_approval_needs_no_note(tmp_path):
    store = review.ReviewStore(tmp_path)
    assert store.record(reported(), review.APPROVED).state == review.APPROVED


def test_override_must_name_a_model_that_was_fitted(tmp_path):
    store = review.ReviewStore(tmp_path)
    with pytest.raises(ValueError, match="not fitted"):
        store.record(reported(), review.OVERRIDDEN, note="because",
                     override_model="circle_nonexistent")


def test_cannot_override_to_a_model_with_no_diameter(tmp_path):
    store = review.ReviewStore(tmp_path)
    m = reported()
    m.candidate_results.append(FitResult(model="outline_radial_median",
                                         diameter_m=None, valid=False))
    with pytest.raises(ValueError, match="no diameter"):
        store.record(m, review.OVERRIDDEN, note="because",
                     override_model="outline_radial_median")


def test_unknown_state_is_refused(tmp_path):
    store = review.ReviewStore(tmp_path)
    with pytest.raises(ValueError, match="unknown review state"):
        store.record(reported(), "PROBABLY_FINE")


# ------------------------------------------------------------------ persistence
def test_decisions_survive_a_reload(tmp_path):
    store = review.ReviewStore(tmp_path)
    store.reviewer = "LL"
    store.record(reported("A"), review.APPROVED)
    store.record(reported("B"), review.REJECTED, note="wrong stem entirely")
    store.record(reported("C"), review.OVERRIDDEN, override_model="ellipse",
                 note="oval beyond lean")

    again = review.ReviewStore(tmp_path)
    assert again.reviewer == "LL"
    assert again.state_of("A") == review.APPROVED
    assert again.state_of("B") == review.REJECTED
    assert again.get("C").override_model == "ellipse"
    assert again.state_of("never-seen") == review.PENDING
    assert again.counts()[review.APPROVED] == 1


def test_saved_after_every_change_not_at_exit(tmp_path):
    """A GUI that loses an afternoon of review to a crash is worse than no
    persistence, because the reviewer believes the work is saved."""
    store = review.ReviewStore(tmp_path)
    store.record(reported(), review.APPROVED)
    assert (tmp_path / "review.json").exists()


def test_store_targets_a_directory_that_does_not_exist_yet(tmp_path):
    """The first run is the case that matters: the output directory has not been
    created. Deciding "is this a directory?" by testing the filesystem meant the store
    wrote itself *as* a file named `out`, after which nothing could create the
    directory at all.
    """
    fresh = tmp_path / "out_not_created_yet"
    store = review.ReviewStore(fresh)
    assert store.path == fresh / "review.json"
    store.record(reported(), review.APPROVED)
    assert fresh.is_dir(), "the output path must still be usable as a directory"
    assert (fresh / "review.json").is_file()


def test_store_accepts_an_explicit_json_path(tmp_path):
    p = tmp_path / "somewhere" / "my_review.json"
    store = review.ReviewStore(p)
    assert store.path == p
    store.record(reported(), review.APPROVED)
    assert p.is_file()
    assert review.ReviewStore(p).state_of("S01") == review.APPROVED


def test_clear_returns_a_tree_to_pending(tmp_path):
    store = review.ReviewStore(tmp_path)
    store.record(reported(), review.APPROVED)
    store.clear("S01")
    assert store.state_of("S01") == review.PENDING
    assert review.ReviewStore(tmp_path).state_of("S01") == review.PENDING


def test_unreadable_store_raises_rather_than_silently_losing_decisions(tmp_path):
    (tmp_path / "review.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read"):
        review.ReviewStore(tmp_path)


# --------------------------------------------------------------------- staleness
def test_a_decision_goes_stale_when_the_measurement_changes(tmp_path):
    """An approval describes one specific measurement. Re-running with different
    settings must not let it silently carry over to a different number."""
    store = review.ReviewStore(tmp_path)
    m = reported()
    store.record(m, review.APPROVED)

    unchanged = store.check_against([reported()])
    assert unchanged == []

    moved = reported()
    moved.dbh_m = 0.44                      # 4 cm is not a rounding difference
    assert store.check_against([moved]) == ["S01"]
    assert store.get("S01").is_stale

    flipped = reported()
    flipped.status = "REVIEW_REQUIRED"
    assert store.check_against([flipped]) == ["S01"]

    gone = reported()
    gone.dbh_m = None                       # now refuses where it once reported
    assert store.check_against([gone]) == ["S01"]


def test_pending_trees_are_never_stale(tmp_path):
    store = review.ReviewStore(tmp_path)
    moved = reported()
    moved.dbh_m = 1.20
    assert store.check_against([moved]) == []


def test_safe_id_survives_a_hostile_tree_id():
    assert review.safe_id("plot 3/tree #7") == "plot_3_tree_7"
    assert review.safe_id("") == "tree"
    assert review.safe_id("S01") == "S01"


# ------------------------------------------- shared model-boundary geometry ----
def test_boundary_is_shared_and_closed_for_every_drawable_model():
    """The GUI and the export figure must draw the same curve. One implementation."""
    circ = FitResult(model="circle_ransac", diameter_m=0.4, valid=True,
                     center_xy=(1.0, 2.0), extra={"radius_m": 0.2})
    b = model_boundary_xy("circle_ransac", circ)
    assert b.shape[1] == 2
    r = ((b[:, 0] - 1.0) ** 2 + (b[:, 1] - 2.0) ** 2) ** 0.5
    assert r.min() == pytest.approx(0.2, abs=1e-9)
    assert r.max() == pytest.approx(0.2, abs=1e-9)

    ell = FitResult(model="ellipse", diameter_m=0.4, valid=True, center_xy=(0.0, 0.0),
                    extra={"semi_major_m": 0.3, "semi_minor_m": 0.2,
                           "rotation_deg": 0.0})
    b = model_boundary_xy("ellipse", ell)
    assert b[:, 0].max() == pytest.approx(0.3, abs=1e-6)
    assert b[:, 1].max() == pytest.approx(0.2, abs=1e-6)

    out = FitResult(model="outline_radial_median", diameter_m=0.4, valid=True,
                   center_xy=(0.0, 0.0),
                   extra={"outline_xy": [[1, 0], [0, 1], [-1, 0], [0, -1]]})
    b = model_boundary_xy("outline_radial_median", out)
    assert b[0].tolist() == b[-1].tolist(), "the outline ring must be closed"


def test_a_declined_fit_still_yields_a_boundary():
    """Judging a refusal means seeing what the refused model claimed."""
    ell = FitResult(model="ellipse", diameter_m=1.16, valid=False,
                    center_xy=(0.0, 0.0),
                    warnings=["ellipse_angular_coverage_0.26_below_0.70"],
                    extra={"semi_major_m": 0.7, "semi_minor_m": 0.35,
                           "rotation_deg": 20.0})
    assert model_boundary_xy("ellipse", ell) is not None


def test_boundary_is_none_when_there_is_nothing_to_draw():
    assert model_boundary_xy("circle_ransac", None) is None
    assert model_boundary_xy("circle_ransac",
                             FitResult(model="circle_ransac")) is None
    # A centre but no geometry: an ellipse fit that failed before conversion.
    assert model_boundary_xy("ellipse", FitResult(model="ellipse",
                                                 center_xy=(0.0, 0.0))) is None
    assert model_boundary_xy("outline_radial_median",
                             FitResult(model="outline_radial_median",
                                       center_xy=(0.0, 0.0),
                                       extra={"outline_xy": [[0, 0]]})) is None


# --------------------------------------------------- graceful degradation ----
def test_gui_declines_cleanly_without_tkinter(monkeypatch, capsys):
    """A Python without tkinter must lose the GUI and nothing else.

    This caught a real bug. `dbh_tool.gui.launch` defers importing the window until
    it is called, so wrapping `from .gui import launch` in the handler caught
    nothing -- the ImportError surfaced from inside the call, one line later and
    outside the try. The CLI now probes tkinter itself.
    """
    import builtins
    import sys

    from dbh_tool.cli import build_parser, cmd_gui

    real_import = builtins.__import__

    def no_tkinter(name, *args, **kwargs):
        if name.split(".")[0] == "tkinter":
            raise ImportError("No module named tkinter")
        return real_import(name, *args, **kwargs)

    for mod in [m for m in list(sys.modules)
                if m.startswith(("tkinter", "dbh_tool.gui",
                                 "matplotlib.backends.backend_tkagg"))]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", no_tkinter)

    rc = cmd_gui(build_parser().parse_args(["gui"]))
    assert rc == 2, "a missing GUI must be a clean exit code, not a traceback"
    err = capsys.readouterr().err
    assert "tkinter" in err
    assert "every other command works without it" in err
