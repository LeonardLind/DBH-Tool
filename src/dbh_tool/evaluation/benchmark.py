"""Field-reference benchmarking (milestone M5).

Three rules from the handover are enforced here rather than left to the person
reading the numbers.

**The review rate is always disclosed.** A tool that reaches a low RMSE by
discarding every difficult tree has not solved the problem, so every report states
how many trees were attempted, how many produced a number, and what was refused.
:meth:`BenchmarkReport.summary` refuses to print accuracy without it.

**The comparator must match how the field measurement was taken.** A tape bridges
flutes, so tape DBH corresponds to the convex perimeter of the cross-section, not
to its area (DEC-009). Scoring an area-equivalent diameter against a tape reading
charges a real geometric difference to the tool, and biases it low on exactly the
irregular stems that are hardest. Each reference row therefore carries its
measurement method, and the comparator is chosen per tree and reported.

**Development and held-out sets stay separate.** ``split`` is a column in the
reference table, and :func:`compare_to_reference` filters on it. Thresholds are
tuned on ``dev`` only; ``holdout`` is scored once, at the end, and repeated
tuning against it is how a benchmark quietly becomes meaningless.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REQUIRED_COLUMNS = ("tree_id", "field_dbh_cm")
OPTIONAL_COLUMNS = (
    "measurement_height_m", "measurement_method", "shape_class", "buttressed",
    "leaning", "split", "x", "y", "notes",
)

# How a field measurement maps onto a modelled quantity. Keys are the values
# allowed in the reference table's measurement_method column.
COMPARATOR_FOR_METHOD = {
    # A tape follows the convex outline of the stem, bridging flutes and fissures.
    "tape": "diameter_convex_perimeter_equiv_m",
    "girth": "diameter_convex_perimeter_equiv_m",
    # Crossed caliper readings are averaged, which is the mean of two roughly
    # perpendicular diameters: the ellipse mean-axes diameter.
    "caliper_crossed": "diameter_mean_axes_m",
    # A single caliper reading is one chord and is not a well-defined summary of a
    # non-circular stem; the selected model is the fairest available comparator.
    "caliper_single": "selected",
    "caliper": "diameter_mean_axes_m",
    "unknown": "selected",
    "": "selected",
}

SIZE_CLASS_EDGES_CM = (0.0, 20.0, 40.0, 80.0, np.inf)
SIZE_CLASS_LABELS = ("under_20cm", "20_40cm", "40_80cm", "over_80cm")
COVERAGE_CLASS_EDGES = (0.0, 0.6, 0.85, 1.01)
COVERAGE_CLASS_LABELS = ("coverage_under_60", "coverage_60_85", "coverage_over_85")


@dataclass
class ReferenceTree:
    """One field-measured tree."""

    tree_id: str
    field_dbh_cm: float
    measurement_height_m: float | None = None
    measurement_method: str = "unknown"
    shape_class: str = "unknown"
    buttressed: bool = False
    leaning: bool = False
    split: str = "dev"
    x: float | None = None
    y: float | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "tree_id": self.tree_id,
            "field_dbh_cm": self.field_dbh_cm,
            "measurement_height_m": self.measurement_height_m,
            "measurement_method": self.measurement_method,
            "shape_class": self.shape_class,
            "buttressed": self.buttressed,
            "leaning": self.leaning,
            "split": self.split,
            "x": self.x,
            "y": self.y,
            "notes": self.notes,
        }


def _as_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


def _as_float(v):
    s = str(v).strip()
    if s == "":
        return None
    return float(s)


def load_reference_table(path: str | Path) -> dict[str, ReferenceTree]:
    """Read and validate a reference-tree CSV.

    Raises on a missing required column or an unparseable DBH, because a silently
    dropped reference tree turns into an inflated-looking accuracy figure.
    """
    p = Path(path)
    with open(p, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{p} contains no data rows")
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise ValueError(f"{p} is missing required column(s): {missing}")

    out: dict[str, ReferenceTree] = {}
    for i, r in enumerate(rows, start=2):
        tid = (r.get("tree_id") or "").strip()
        if not tid:
            raise ValueError(f"{p} line {i}: empty tree_id")
        if tid in out:
            raise ValueError(f"{p} line {i}: duplicate tree_id {tid!r}")
        try:
            dbh = _as_float(r.get("field_dbh_cm"))
        except ValueError:
            dbh = None
        if dbh is None or not np.isfinite(dbh) or dbh <= 0:
            # Name the line: a typo in a hand-entered field table is the most
            # likely failure here, and a bare conversion error does not say where.
            raise ValueError(
                f"{p} line {i}: field_dbh_cm must be a positive number, got "
                f"{r.get('field_dbh_cm')!r}")
        method = (r.get("measurement_method") or "unknown").strip().lower()
        if method not in COMPARATOR_FOR_METHOD:
            raise ValueError(
                f"{p} line {i}: measurement_method {method!r} is not one of "
                f"{sorted(k for k in COMPARATOR_FOR_METHOD if k)}")
        out[tid] = ReferenceTree(
            tree_id=tid,
            field_dbh_cm=float(dbh),
            measurement_height_m=_as_float(r.get("measurement_height_m")),
            measurement_method=method,
            shape_class=(r.get("shape_class") or "unknown").strip().lower(),
            buttressed=_as_bool(r.get("buttressed")),
            leaning=_as_bool(r.get("leaning")),
            split=(r.get("split") or "dev").strip().lower(),
            x=_as_float(r.get("x")),
            y=_as_float(r.get("y")),
            notes=(r.get("notes") or "").strip(),
        )
    return out


def select_comparator(measurement, method: str) -> tuple[float | None, str]:
    """Pick the modelled quantity that corresponds to how the field value was taken.

    Returns ``(value_in_metres, name)``. Falls back to the reported DBH, with the
    fallback named in the result, so a report can never silently compare the wrong
    two quantities.
    """
    want = COMPARATOR_FOR_METHOD.get(method, "selected")
    if want == "selected":
        return measurement.dbh_m, "reported_dbh"

    fits = {f.model: f for f in measurement.candidate_results}
    if want == "diameter_convex_perimeter_equiv_m":
        for name in ("outline_radial_median", "outline_radial_median_inliers"):
            f = fits.get(name)
            if f is not None and f.valid:
                v = f.extra.get(want)
                if v is not None and np.isfinite(v):
                    return float(v), f"{name}.{want}"
    if want == "diameter_mean_axes_m":
        f = fits.get("ellipse")
        if f is not None and f.valid:
            v = f.extra.get(want)
            if v is not None and np.isfinite(v):
                return float(v), f"ellipse.{want}"
    return measurement.dbh_m, f"reported_dbh_fallback_for_{want}"


def _metrics(errors_cm: np.ndarray, field_cm: np.ndarray) -> dict:
    if errors_cm.size == 0:
        return {"n": 0, "bias_cm": None, "mae_cm": None, "rmse_cm": None,
                "relative_rmse_percent": None, "max_abs_error_cm": None}
    rmse = float(np.sqrt(np.mean(errors_cm ** 2)))
    mean_field = float(np.mean(field_cm))
    return {
        "n": int(errors_cm.size),
        "bias_cm": float(np.mean(errors_cm)),
        "mae_cm": float(np.mean(np.abs(errors_cm))),
        "rmse_cm": rmse,
        "relative_rmse_percent": (float(100.0 * rmse / mean_field)
                                  if mean_field > 0 else None),
        "max_abs_error_cm": float(np.max(np.abs(errors_cm))),
    }


def _class_of(value, edges, labels, default="unknown"):
    if value is None or not np.isfinite(value):
        return default
    for i in range(len(labels)):
        if edges[i] <= value < edges[i + 1]:
            return labels[i]
    return default


@dataclass
class BenchmarkReport:
    """Accuracy against field reference, with the review rate attached."""

    n_reference: int
    n_matched: int
    n_reported: int
    n_refused: int
    unmatched_reference_ids: list = field(default_factory=list)
    unmatched_measurement_ids: list = field(default_factory=list)
    overall: dict = field(default_factory=dict)
    by_status: dict = field(default_factory=dict)
    by_shape_class: dict = field(default_factory=dict)
    by_size_class: dict = field(default_factory=dict)
    by_coverage_class: dict = field(default_factory=dict)
    by_confidence_band: dict = field(default_factory=dict)
    per_tree: list = field(default_factory=list)
    split: str = "all"
    comparator_counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def review_rate(self) -> float | None:
        """Share of matched reference trees for which no diameter was reported."""
        if self.n_matched == 0:
            return None
        return float(self.n_refused / self.n_matched)

    def to_dict(self) -> dict:
        return {
            "split": self.split,
            "n_reference": self.n_reference,
            "n_matched": self.n_matched,
            "n_reported": self.n_reported,
            "n_refused": self.n_refused,
            "review_rate": self.review_rate,
            "unmatched_reference_ids": self.unmatched_reference_ids,
            "unmatched_measurement_ids": self.unmatched_measurement_ids,
            "overall": self.overall,
            "by_status": self.by_status,
            "by_shape_class": self.by_shape_class,
            "by_size_class": self.by_size_class,
            "by_coverage_class": self.by_coverage_class,
            "by_confidence_band": self.by_confidence_band,
            "comparator_counts": self.comparator_counts,
            "warnings": self.warnings,
            "per_tree": self.per_tree,
        }

    def summary(self) -> str:
        """A text report. Accuracy is never shown without the review rate."""
        lines = [
            f"Benchmark against field reference (split={self.split})",
            f"  reference trees: {self.n_reference}   matched to measurements: "
            f"{self.n_matched}",
            f"  reported a diameter: {self.n_reported}   refused: {self.n_refused}"
            + (f"   review rate: {self.review_rate:.0%}"
               if self.review_rate is not None else ""),
        ]
        if self.n_reported == 0:
            lines.append("  no accuracy statistics: nothing was reported")
            return "\n".join(lines)
        o = self.overall
        lines += [
            "",
            f"  {'bias':>10}{'MAE':>10}{'RMSE':>10}{'rel RMSE':>11}{'max |e|':>10}",
            f"  {o['bias_cm']:+10.2f}{o['mae_cm']:10.2f}{o['rmse_cm']:10.2f}"
            f"{o['relative_rmse_percent']:10.1f}%{o['max_abs_error_cm']:10.2f}"
            "   [cm]",
            "",
            "  NOTE: these statistics describe only the "
            f"{self.n_reported}/{self.n_matched} trees that produced a number. "
            "The refused trees are not errors of zero.",
        ]
        for title, table in (("by status", self.by_status),
                             ("by shape class", self.by_shape_class),
                             ("by size class", self.by_size_class),
                             ("by coverage class", self.by_coverage_class)):
            rows = {k: v for k, v in table.items() if v.get("n")}
            if not rows:
                continue
            lines.append(f"\n  {title}:")
            lines.append(f"    {'group':<28}{'n':>4}{'bias':>9}{'MAE':>8}{'RMSE':>8}")
            for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["n"]):
                lines.append(f"    {k:<28}{v['n']:>4}{v['bias_cm']:+9.2f}"
                             f"{v['mae_cm']:8.2f}{v['rmse_cm']:8.2f}")
        if self.comparator_counts:
            lines.append("\n  comparators used: " + ", ".join(
                f"{k} x{v}" for k, v in sorted(self.comparator_counts.items())))
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)


def compare_to_reference(measurements, reference: dict, split: str | None = None
                         ) -> BenchmarkReport:
    """Score measurements against field reference and stratify the errors.

    ``split`` filters the reference table (``"dev"``, ``"holdout"``, or None for
    all). Trees present in the reference but not measured, and vice versa, are
    listed rather than dropped quietly.
    """
    ref = {k: v for k, v in reference.items()
           if split is None or v.split == split}
    by_id = {m.tree_id: m for m in measurements}
    matched = [t for t in ref if t in by_id]

    rep = BenchmarkReport(
        n_reference=len(ref), n_matched=len(matched), n_reported=0, n_refused=0,
        unmatched_reference_ids=sorted(set(ref) - set(by_id)),
        unmatched_measurement_ids=sorted(set(by_id) - set(ref)),
        split=split or "all",
    )
    if rep.unmatched_reference_ids:
        rep.warnings.append(
            f"{len(rep.unmatched_reference_ids)} reference tree(s) had no matching "
            "measurement and are excluded from every statistic")

    errs, fields_ = [], []
    groups: dict[str, dict[str, list]] = {
        "status": {}, "shape": {}, "size": {}, "coverage": {}, "band": {}}

    for tid in matched:
        r = ref[tid]
        m = by_id[tid]
        value_m, comparator = select_comparator(m, r.measurement_method)
        rep.comparator_counts[comparator] = rep.comparator_counts.get(comparator, 0) + 1
        sel = next((f for f in m.candidate_results if f.model == m.selected_model), None)
        cov = sel.angular_coverage if sel is not None else None
        row = {
            "tree_id": tid,
            "field_dbh_cm": r.field_dbh_cm,
            "field_method": r.measurement_method,
            "field_shape_class": r.shape_class,
            "predicted_cm": None if value_m is None else round(value_m * 100.0, 3),
            "comparator": comparator,
            "error_cm": None,
            "status": m.status,
            "confidence_band": m.confidence_band,
            "selected_model": m.selected_model,
            "coverage_fraction": cov,
            "reported": value_m is not None,
        }
        if value_m is None:
            rep.n_refused += 1
        else:
            rep.n_reported += 1
            e = value_m * 100.0 - r.field_dbh_cm
            row["error_cm"] = round(float(e), 3)
            errs.append(e)
            fields_.append(r.field_dbh_cm)
            groups["status"].setdefault(m.status, []).append((e, r.field_dbh_cm))
            groups["shape"].setdefault(r.shape_class, []).append((e, r.field_dbh_cm))
            groups["size"].setdefault(
                _class_of(r.field_dbh_cm, SIZE_CLASS_EDGES_CM, SIZE_CLASS_LABELS),
                []).append((e, r.field_dbh_cm))
            groups["coverage"].setdefault(
                _class_of(cov, COVERAGE_CLASS_EDGES, COVERAGE_CLASS_LABELS),
                []).append((e, r.field_dbh_cm))
            groups["band"].setdefault(m.confidence_band, []).append((e, r.field_dbh_cm))
        rep.per_tree.append(row)

    rep.overall = _metrics(np.asarray(errs, dtype=float), np.asarray(fields_, dtype=float))

    def tabulate(d):
        return {k: _metrics(np.asarray([a for a, _ in v], dtype=float),
                            np.asarray([b for _, b in v], dtype=float))
                for k, v in d.items()}

    rep.by_status = tabulate(groups["status"])
    rep.by_shape_class = tabulate(groups["shape"])
    rep.by_size_class = tabulate(groups["size"])
    rep.by_coverage_class = tabulate(groups["coverage"])
    rep.by_confidence_band = tabulate(groups["band"])

    fallbacks = sum(v for k, v in rep.comparator_counts.items() if "fallback" in k)
    if fallbacks:
        rep.warnings.append(
            f"{fallbacks} tree(s) fell back to the reported DBH because the "
            "method-appropriate comparator was unavailable; those errors mix two "
            "different quantities (see DEC-009)")
    if rep.n_reported and rep.n_reported < 10:
        rep.warnings.append(
            f"only {rep.n_reported} tree(s) contribute to these statistics, which is "
            "far too few to characterise accuracy")
    return rep
