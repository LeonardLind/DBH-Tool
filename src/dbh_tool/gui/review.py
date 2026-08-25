"""Human review decisions, persisted (M7).

DEC-013 separated three things that were being conflated: ``status`` is the tool's
scientific verdict, ``selected_model`` is its recommendation, and ``review_state`` is
the human workflow state, always ``PENDING`` until a person acts. This module is the
part that was missing -- somewhere for the person's decision to live.

Design positions, all of which follow from the project's non-negotiables:

**A decision never overwrites a measurement.** The store is keyed by tree id and holds
only what the human contributed. Re-measuring a tree cannot silently invalidate a
recorded approval, and an approval cannot silently launder a measurement.

**An override must say what it overrode.** A reviewer choosing a different model is
making a claim against the tool's reasoning, so the record carries the tool's
recommendation *and* the human's choice *and* a required note. Anything less produces
a number with no provenance, which is the failure mode the whole project exists to
avoid.

**Approving a refusal does not create a diameter.** A hard failure reports no
``dbh_cm`` because the geometry does not constrain one (non-negotiable 7). A reviewer
can confirm that refusal, and doing so records agreement that there is no number -- it
must never be a route to publishing one. :func:`resolved_diameter_cm` is the single
place that decides what a reviewed tree reports, and it refuses on exactly the same
rule as the measurement did.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
OVERRIDDEN = "OVERRIDDEN"

STATES = (PENDING, APPROVED, REJECTED, OVERRIDDEN)

# Statuses for which the tool emitted no headline diameter. Kept as a name rather than
# inlined so the rule is greppable from both here and the GUI.
REFUSING_STATUSES = ("REVIEW_REQUIRED", "INVALID_MEASUREMENT_HEIGHT",
                     "FAILED_INSUFFICIENT_DATA")

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Decision:
    """One reviewer's verdict on one tree."""

    tree_id: str
    state: str = PENDING
    reviewer: str = ""
    note: str = ""
    # What the tool had recommended when the decision was taken. Stored so a later
    # re-measurement that changes the recommendation is detectable rather than silent.
    tool_status: str = ""
    tool_selected_model: str | None = None
    tool_dbh_cm: float | None = None
    tool_reported: bool = True
    # Only set for OVERRIDDEN.
    override_model: str | None = None
    override_dbh_cm: float | None = None
    decided_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Decision:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_stale(self) -> bool:
        """Whether this decision was recorded against a since-changed measurement.

        Set by :meth:`ReviewStore.check_against`, not computed here: staleness is a
        relation between a decision and a measurement, and the decision alone cannot
        know.
        """
        return bool(getattr(self, "_stale", False))


def validate_note(state: str, note: str) -> str | None:
    """Return an error message if ``note`` is inadequate for ``state``, else None.

    A rejection and an override are both disagreements with the tool, and a
    disagreement with no stated reason is unreviewable by the next person. An approval
    needs no note: it is agreement with reasoning already recorded in ``reasons``.
    """
    if state in (REJECTED, OVERRIDDEN) and len(note.strip()) < 3:
        return (f"{state.lower()} needs a note saying why -- it is a disagreement with "
                f"the tool's reasoning, and the next reader cannot see your screen.")
    return None


def resolved_diameter_cm(measurement, decision: Decision | None):
    """What a reviewed tree reports: ``(value_cm_or_None, provenance_string)``.

    The single place that answers "so what is the DBH, then". Rules, in order:

    * No decision, or ``PENDING`` -> the tool's own number, or None if it refused.
    * ``REJECTED`` -> None. The reviewer has said the measurement is wrong.
    * ``OVERRIDDEN`` -> the overriding model's diameter, but **only if the tool
      reported a number at all**. Overriding is choosing among models the data
      supports; it is not a way to extract a diameter from a section the tool refused
      to measure, and allowing that would defeat non-negotiable 7.
    * ``APPROVED`` -> the tool's number, or None where the tool refused. Approving a
      refusal records agreement that there is no number.
    """
    tool_cm = None if measurement.dbh_m is None else measurement.dbh_m * 100.0
    reported = measurement.dbh_m is not None
    if decision is None or decision.state == PENDING:
        return tool_cm, "tool recommendation, not reviewed"
    if decision.state == REJECTED:
        return None, f"rejected by {decision.reviewer or 'reviewer'}"
    if decision.state == OVERRIDDEN:
        if not reported:
            return None, ("override refused: the tool emitted no diameter for this "
                          "section, and a review cannot create one")
        return decision.override_dbh_cm, (
            f"overridden to {decision.override_model} by "
            f"{decision.reviewer or 'reviewer'}")
    if decision.state == APPROVED:
        if not reported:
            return None, (f"refusal confirmed by {decision.reviewer or 'reviewer'}: "
                          f"the data does not support a diameter")
        return tool_cm, f"approved by {decision.reviewer or 'reviewer'}"
    return tool_cm, "unknown review state"


class ReviewStore:
    """Review decisions for one output directory, backed by a JSON file.

    Written on every change rather than on exit: a GUI that loses an afternoon of
    review decisions to a crash is worse than no persistence at all, because the
    reviewer believes the work is saved.
    """

    FILENAME = "review.json"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        # A path without a .json suffix is taken as the output *directory*, whether or
        # not it exists yet. Testing `is_dir()` is wrong and was actively harmful: on a
        # first run the output directory does not exist, so the check failed and the
        # store happily wrote itself *as* a file with the directory's name -- after
        # which nothing else could create the directory at all.
        if self.path.suffix.lower() != ".json":
            self.path = self.path / self.FILENAME
        self.decisions: dict[str, Decision] = {}
        self.reviewer = ""
        self.load()

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {self.path}: {exc}") from exc
        self.reviewer = str(raw.get("reviewer", "") or "")
        for d in raw.get("decisions", []):
            try:
                dec = Decision.from_dict(d)
            except TypeError:
                continue
            if dec.tree_id:
                self.decisions[dec.tree_id] = dec

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "reviewer": self.reviewer,
            "saved_at": _now(),
            # Sorted so the file diffs cleanly if anyone version-controls it.
            "decisions": [self.decisions[k].to_dict() for k in sorted(self.decisions)],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return self.path

    # -- queries -------------------------------------------------------------

    def get(self, tree_id: str) -> Decision | None:
        return self.decisions.get(tree_id)

    def state_of(self, tree_id: str) -> str:
        d = self.decisions.get(tree_id)
        return d.state if d else PENDING

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATES}
        for d in self.decisions.values():
            out[d.state] = out.get(d.state, 0) + 1
        return out

    # -- mutation ------------------------------------------------------------

    def record(self, measurement, state: str, note: str = "",
               override_model: str | None = None,
               reviewer: str | None = None) -> Decision:
        """Record a decision and write the store. Raises ValueError on a bad request."""
        if state not in STATES:
            raise ValueError(f"unknown review state {state!r}")
        note = (note or "").strip()
        problem = validate_note(state, note)
        if problem:
            raise ValueError(problem)

        override_cm = None
        if state == OVERRIDDEN:
            if not override_model:
                raise ValueError("an override must name the model it overrides to")
            fit = next((f for f in measurement.candidate_results
                        if f.model == override_model), None)
            if fit is None:
                raise ValueError(f"{override_model!r} was not fitted for this tree")
            if fit.diameter_m is None:
                raise ValueError(f"{override_model} produced no diameter to override to")
            override_cm = fit.diameter_m * 100.0
            if measurement.dbh_m is None:
                # Not fatal here -- resolved_diameter_cm enforces it -- but recording
                # the attempt without saying so would be misleading.
                note = (note + "  [NOTE: the tool emitted no diameter for this "
                                "section; this override cannot produce one]").strip()

        dec = Decision(
            tree_id=measurement.tree_id,
            state=state,
            reviewer=(reviewer if reviewer is not None else self.reviewer) or "",
            note=note,
            tool_status=measurement.status,
            tool_selected_model=measurement.selected_model,
            tool_dbh_cm=None if measurement.dbh_m is None else measurement.dbh_m * 100.0,
            tool_reported=measurement.dbh_m is not None,
            override_model=override_model if state == OVERRIDDEN else None,
            override_dbh_cm=override_cm,
        )
        self.decisions[measurement.tree_id] = dec
        self.save()
        return dec

    def clear(self, tree_id: str) -> None:
        """Return a tree to PENDING, forgetting the decision entirely."""
        if self.decisions.pop(tree_id, None) is not None:
            self.save()

    def check_against(self, measurements) -> list[str]:
        """Flag decisions whose measurement has changed since they were recorded.

        A recorded approval describes a specific measurement. Re-running with different
        settings can change the status or the recommended model underneath it, and an
        approval that silently carries over to a different number is exactly the kind
        of laundering this store exists to prevent. Returns the affected tree ids.
        """
        stale = []
        for m in measurements:
            d = self.decisions.get(m.tree_id)
            if d is None or d.state == PENDING:
                continue
            changed = (d.tool_status != m.status
                       or d.tool_selected_model != m.selected_model
                       or d.tool_reported != (m.dbh_m is not None))
            if not changed and d.tool_dbh_cm is not None and m.dbh_m is not None:
                changed = abs(d.tool_dbh_cm - m.dbh_m * 100.0) > 0.05
            d._stale = changed
            if changed:
                stale.append(m.tree_id)
        return stale


def safe_id(text: str) -> str:
    """A filesystem-safe version of a tree id, for per-tree output files."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text) or "tree"
