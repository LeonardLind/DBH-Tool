"""Command line interface.

Subcommands mirror the pipeline stages so that each one can be inspected on its
own:

    dbh inspect FILE                 header, units, classification, warnings
    dbh ground FILE                  build and describe a ground surface
    dbh detect FILE                  list stem candidates (M6 preview)
    dbh measure FILE --at X Y        measure one or more known trees
    dbh benchmark FILE --reference   score measurements against field reference
    dbh experiment FILE --experiment run a parameter sweep or a free experiment
    dbh config                       print the default run configuration
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from . import __version__
from .config import RunConfig
from .export.tables import print_model_table, write_csv, write_json
from .ground.dtm import build_ground_grid
from .ground.normalize import height_above_ground
from .io.las import LasSource, inspect_las
from .evaluation.benchmark import compare_to_reference, load_reference_table
from .evaluation.experiments import (
    SWEEPS, geometry_comparison, height_strategy_comparison, model_comparison, run_sweep,
)
from .measure import measure_tree
from .stems.candidates import find_stem_candidates


def _load_cfg(path: str | None) -> RunConfig:
    return RunConfig.load(path) if path else RunConfig()


def _build_grid(src: LasSource, cfg: RunConfig, verbose: bool = True):
    t0 = time.time()
    info = src.info
    bounds = (info.mins[0], info.maxs[0], info.mins[1], info.maxs[1])
    grid = build_ground_grid(
        src.iter_xyz(), cell_m=cfg.ground.cell_m,
        despike_radius_cells=cfg.ground.despike_radius_cells,
        despike_tolerance_m=cfg.ground.despike_tolerance_m,
        smooth_iterations=cfg.ground.smooth_iterations, bounds=bounds,
        method=cfg.ground.method)
    if verbose:
        meta = grid.to_meta()
        print(f"ground surface: {meta['shape'][0]}x{meta['shape'][1]} cells at "
              f"{meta['cell_m']} m, {meta['observed_cell_fraction']:.1%} observed, "
              f"{meta['n_spike_cells_rejected']} low-outlier cells rejected "
              f"({time.time() - t0:.1f} s)")
    return grid


def _load_targets(args) -> list[tuple[str, float, float]]:
    """Collect (tree_id, x, y) from --at/--tree-id pairs and/or a --targets file."""
    targets: list[tuple[str, float, float]] = []
    if getattr(args, "at", None):
        for i, (x, y) in enumerate(args.at):
            tid = (args.tree_id[i] if getattr(args, "tree_id", None)
                   and i < len(args.tree_id) else f"tree_{i + 1:03d}")
            targets.append((tid, float(x), float(y)))
    if getattr(args, "targets", None):
        rows = json.loads(Path(args.targets).read_text(encoding="utf-8"))
        for i, r in enumerate(rows):
            targets.append((str(r.get("tree_id", f"tree_{i + 1:03d}")),
                            float(r["x"]), float(r["y"])))
    return targets


def _crop_targets(src: LasSource, targets, radius_m: float, verbose: bool = True):
    """Crop every target in one pass. Returns {tree_id: xyz}, {tree_id: (x, y)}."""
    t0 = time.time()
    clouds = src.crop_many([(x, y) for _, x, y in targets], radius_m)
    points, xy = {}, {}
    for (tid, x, y), c in zip(targets, clouds):
        if len(c) == 0:
            print(f"  {tid}: no points within {radius_m} m, skipping", file=sys.stderr)
            continue
        points[tid] = c
        xy[tid] = (x, y)
    if verbose:
        total = sum(len(c) for c in points.values())
        print(f"cropped {len(points)} region(s), {total} points, one pass "
              f"({time.time() - t0:.1f} s)")
    return points, xy


def cmd_inspect(args) -> int:
    info = inspect_las(args.file)
    print(json.dumps(info.to_dict(), indent=2))
    if info.warnings:
        print("\nvalidation warnings:", file=sys.stderr)
        for w in info.warnings:
            print(f"  - {w}", file=sys.stderr)
    return 0


def cmd_config(args) -> int:
    cfg = _load_cfg(args.config)
    print(cfg.to_yaml())
    return 0


def cmd_ground(args) -> int:
    cfg = _load_cfg(args.config)
    src = LasSource(args.file)
    grid = _build_grid(src, cfg)
    meta = grid.to_meta()
    print(json.dumps(meta, indent=2, default=str))
    if args.out:
        np.savez_compressed(args.out, z=grid.z, observed=grid.observed,
                            count=grid.count, origin=np.asarray(grid.origin),
                            cell_m=grid.cell_m)
        print(f"wrote {args.out}")
    return 0


def cmd_detect(args) -> int:
    cfg = _load_cfg(args.config)
    src = LasSource(args.file)
    grid = _build_grid(src, cfg)
    print("loading decimated cloud for detection ...")
    xyz = src.decimate(args.decimate).astype(float)
    hag = height_above_ground(xyz, grid)
    cands = find_stem_candidates(
        xyz, hag, target_height_m=cfg.slice.target_height_m,
        band_thickness_m=args.band, min_points=args.min_points)
    acc = [c for c in cands if c.accepted]
    print(f"{len(cands)} clusters, {len(acc)} accepted as stem candidates "
          f"(decimation 1/{args.decimate})")
    print(f"{'id':<12}{'x':>9}{'y':>9}{'pts':>8}{'approx_D':>10}{'elong':>7}{'cont':>7}")
    for c in cands[:args.limit]:
        print(f"{c.candidate_id:<12}{c.center_xy[0]:9.2f}{c.center_xy[1]:9.2f}"
              f"{c.n_points:8d}{c.approx_diameter_m:10.2f}{c.elongation:7.1f}"
              f"{c.continuity_fraction:7.0%}"
              + ("" if c.accepted else "   REJECTED: " + "; ".join(c.rejection_reasons)))
    if args.out:
        Path(args.out).write_text(json.dumps([c.to_dict() for c in cands], indent=2),
                                  encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def cmd_measure(args) -> int:
    cfg = _load_cfg(args.config)
    src = LasSource(args.file)
    for w in src.info.warnings:
        print(f"input warning: {w}", file=sys.stderr)
    grid = _build_grid(src, cfg)

    targets = _load_targets(args)
    if not targets:
        print("no targets given: use --at X Y (repeatable) or --targets FILE.json",
              file=sys.stderr)
        return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    points, xy_of = _crop_targets(src, targets, args.roi)
    measurements = []
    for tree_id in points:
        x, y = xy_of[tree_id]
        xyz = points[tree_id]
        print(f"\n=== {tree_id} at ({x:.2f}, {y:.2f}) === {len(xyz)} points")
        m = measure_tree(xyz, grid, tree_id, (x, y), cfg,
                         roi_radius_m=min(args.roi, 1.5),
                         source_info={"file": str(src.path),
                                      "file_sha256_head": src.info.file_sha256_head,
                                      "point_count": src.info.point_count,
                                      "roi_radius_m": args.roi,
                                      "roi_point_count": int(len(xyz))})
        print(print_model_table(m))
        measurements.append(m)

        if not args.no_plots:
            from .visualization.cross_section import plot_ground_check, plot_measurement
            internal = getattr(m, "_sections_internal", {})
            meta = internal.get(m.primary_geometry, {}).get(
                f"{cfg.slice.target_height_m:.2f}", {})
            xy = meta.get("_xy")
            if xy is not None:
                p = plot_measurement(m, xy, outdir / f"{tree_id}_review.png")
                print(f"  wrote {p}")
            hag = height_above_ground(xyz, grid)
            p2 = plot_ground_check(grid, xyz, hag, (x, y),
                                   outdir / f"{tree_id}_ground.png",
                                   radius_m=args.roi,
                                   target_height_m=cfg.slice.target_height_m)
            print(f"  wrote {p2}")

    if measurements:
        run_meta = {
            "software": "dbh-tool", "version": __version__,
            "input": src.info.to_dict(), "config": cfg.to_dict(),
            "ground": grid.to_meta(),
        }
        c = write_csv(measurements, outdir / "measurements.csv")
        j = write_json(measurements, outdir / "measurements.json", run_meta=run_meta)
        cfg.to_yaml(outdir / "run_config.yaml")
        print(f"\nwrote {c}\nwrote {j}\nwrote {outdir / 'run_config.yaml'}")
    return 0


def cmd_benchmark(args) -> int:
    cfg = _load_cfg(args.config)
    try:
        reference = load_reference_table(args.reference)
    except (ValueError, OSError) as exc:
        # The header-only template is the expected starting state, so say what to
        # do about it rather than showing a traceback.
        print(f"cannot read reference table: {exc}", file=sys.stderr)
        for line in ("",
                     "The reference table needs at least one row of real field",
                     "measurements. See data/README.md for the schema, and note that",
                     "measurement_method decides which modelled quantity each row is",
                     "scored against."):
            print(line, file=sys.stderr)
        return 2
    if not reference:
        print(f"{args.reference} has no rows: nothing to benchmark", file=sys.stderr)
        return 2
    src = LasSource(args.file)
    grid = _build_grid(src, cfg)

    targets = _load_targets(args)
    if not targets:
        # Fall back to the reference table itself, which is the normal case.
        missing = [t for t in reference.values() if t.x is None or t.y is None]
        if missing:
            print(f"{len(missing)} reference tree(s) have no x/y; supply --targets "
                  f"or fill the columns", file=sys.stderr)
            return 2
        targets = [(t.tree_id, t.x, t.y) for t in reference.values()]

    points, xy = _crop_targets(src, targets, args.roi)
    measurements = [measure_tree(points[tid], grid, tid, xy[tid], cfg,
                                 roi_radius_m=min(args.roi, 1.5))
                    for tid in points]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for split in ([args.split] if args.split else [None, "dev", "holdout"]):
        rep = compare_to_reference(measurements, reference, split=split)
        if rep.n_matched == 0:
            continue
        print()
        print(rep.summary())
        name = f"benchmark_{split or 'all'}.json"
        (outdir / name).write_text(json.dumps(rep.to_dict(), indent=2, default=str),
                                   encoding="utf-8")
        print(f"  wrote {outdir / name}")
    write_csv(measurements, outdir / "benchmark_measurements.csv")
    return 0


def cmd_experiment(args) -> int:
    cfg = _load_cfg(args.config)
    try:
        reference = load_reference_table(args.reference) if args.reference else None
    except (ValueError, OSError) as exc:
        print(f"cannot read reference table: {exc}", file=sys.stderr)
        print("continuing with internal sensitivity only", file=sys.stderr)
        reference = None
    src = LasSource(args.file)
    targets = _load_targets(args)
    if not targets:
        print("no targets: use --at X Y or --targets FILE.json", file=sys.stderr)
        return 2

    points, xy = _crop_targets(src, targets, args.roi)
    if not points:
        return 2
    bounds = (src.info.mins[0], src.info.maxs[0], src.info.mins[1], src.info.maxs[1])
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results: dict = {}

    free = {"height_strategy", "geometry", "models"}
    wanted = args.experiment or (sorted(SWEEPS) + sorted(free))
    if "all" in wanted:
        wanted = sorted(SWEEPS) + sorted(free)

    if free & set(wanted):
        # The free experiments read one baseline run; no sweep needed.
        grid = _build_grid(src, cfg)
        base = [measure_tree(points[tid], grid, tid, xy[tid], cfg,
                             roi_radius_m=min(args.roi, 1.5)) for tid in points]
        if "height_strategy" in wanted:
            r = height_strategy_comparison(base, reference)
            results["height_strategy"] = r
            print("\nExperiment D: height strategy "
                  f"({r['evidence']}), {r['n_trees']} trees")
            for k, v in r["deviation_from_tree_median_cm"].items():
                print(f"  {k:<20} n={v['n']:<4} mean deviation from tree median "
                      f"{_num(v['mean'])} cm, std {_num(v['std'])} cm")
        if "geometry" in wanted:
            r = geometry_comparison(base)
            results["geometry"] = r
            print(f"\nExperiment B: horizontal vs stem-normal, {r['n_trees']} trees")
            print(f"  mean difference {_num(r['mean_difference_cm'])} cm, "
                  f"std {_num(r['std_difference_cm'])} cm, "
                  f"sign consistent: {r['sign_consistent']}")
            for row in r["per_tree"]:
                print(f"    {row['tree_id']:<6} tilt {_num(row['tilt_deg'],1):>6} deg  "
                      f"measured {_num(row['difference_cm']):>7} cm  "
                      f"predicted {_num(row['predicted_difference_cm']):>7} cm")
        if "models" in wanted:
            r = model_comparison(base)
            results["models"] = r
            print("\nExperiment A: model agreement (deviation from per-tree median)")
            for k, v in r["deviation_from_per_tree_median"].items():
                print(f"  {k:<32} n={v['n']:<4} median {_num(v['median_cm']):>7} cm, "
                      f"max |dev| {_num(v['max_abs_cm']):>7} cm")

    for name in [w for w in wanted if w in SWEEPS]:
        print(f"\nrunning sweep {name} ...")
        r = run_sweep(name, points, xy, cfg, bounds=bounds,
                      ground_source=src.iter_xyz, reference=reference,
                      roi_radius_m=min(args.roi, 1.5))
        results[name] = r.to_dict()
        print()
        print(r.summary())

    path = outdir / "experiments.json"
    path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


def _num(v, nd: int = 2) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):+.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def cmd_gui(args) -> int:
    """Open the Tk review window.

    tkinter is imported here rather than at module scope so that every measurement
    command still works on a Python without it -- a trimmed Linux build, or a
    container. A missing GUI must never break the CLI.
    """
    # Probe tkinter itself rather than wrapping the import of our own package.
    # `dbh_tool.gui.launch` defers importing the window until it is called, so
    # wrapping `from .gui import launch` caught nothing -- the ImportError surfaced
    # from inside the call, one line later and outside the handler. Checking the
    # actual missing dependency is both precise and honest: any *other* ImportError
    # from the GUI is a real bug and should still be a traceback.
    try:
        import tkinter                    # noqa: F401
    except ImportError as exc:            # pragma: no cover - environment specific
        print(f"the GUI needs tkinter, which this Python does not have: {exc}",
              file=sys.stderr)
        print("every other command works without it; on Debian/Ubuntu install "
              "python3-tk, or on Windows reinstall Python with the tcl/tk option",
              file=sys.stderr)
        return 2
    from .gui import launch
    launch(cloud=args.file, outdir=args.outdir, targets=args.targets,
           config=args.config)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dbh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"dbh-tool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("inspect", help="report header, units and validation warnings")
    pi.add_argument("file")
    pi.set_defaults(func=cmd_inspect)

    pc = sub.add_parser("config", help="print the run configuration")
    pc.add_argument("-c", "--config", help="YAML/JSON config to load and echo")
    pc.set_defaults(func=cmd_config)

    pg = sub.add_parser("ground", help="build the local ground surface")
    pg.add_argument("file")
    pg.add_argument("-c", "--config")
    pg.add_argument("-o", "--out", help="write the surface to an .npz file")
    pg.set_defaults(func=cmd_ground)

    pd = sub.add_parser("detect", help="list stem candidates (M6 preview, unvalidated)")
    pd.add_argument("file")
    pd.add_argument("-c", "--config")
    pd.add_argument("--decimate", type=int, default=10,
                    help="use every Nth point for detection (default 10)")
    pd.add_argument("--band", type=float, default=0.20,
                    help="band thickness in m for the detection slice")
    pd.add_argument("--min-points", type=int, default=150, dest="min_points")
    pd.add_argument("--limit", type=int, default=40)
    pd.add_argument("-o", "--out", help="write candidates to JSON")
    pd.set_defaults(func=cmd_detect)

    pm = sub.add_parser("measure", help="measure one or more known trees")
    pm.add_argument("file")
    pm.add_argument("-c", "--config")
    pm.add_argument("--at", nargs=2, action="append", metavar=("X", "Y"),
                    help="stem location, repeatable")
    pm.add_argument("--tree-id", action="append", dest="tree_id",
                    help="id for the corresponding --at, repeatable")
    pm.add_argument("--targets", help="JSON list of {tree_id, x, y}")
    pm.add_argument("--roi", type=float, default=6.0,
                    help="crop radius in m around each stem (default 6)")
    pm.add_argument("--outdir", default="out")
    pm.add_argument("--no-plots", action="store_true")
    pm.set_defaults(func=cmd_measure)

    pb = sub.add_parser("benchmark", help="score measurements against field reference")
    pb.add_argument("file")
    pb.add_argument("-c", "--config")
    pb.add_argument("-r", "--reference", required=True,
                    help="reference tree CSV (see data/README.md)")
    pb.add_argument("--at", nargs=2, action="append", metavar=("X", "Y"))
    pb.add_argument("--tree-id", action="append", dest="tree_id")
    pb.add_argument("--targets", help="JSON list of {tree_id, x, y}")
    pb.add_argument("--split", choices=["dev", "holdout"],
                    help="score only this split (default: all, dev and holdout)")
    pb.add_argument("--roi", type=float, default=4.0)
    pb.add_argument("--outdir", default="out")
    pb.set_defaults(func=cmd_benchmark)

    pe = sub.add_parser("experiment", help="parameter sweeps and docs 02 experiments")
    pe.add_argument("file")
    pe.add_argument("-c", "--config")
    pe.add_argument("-e", "--experiment", action="append",
                    help=("repeatable. Sweeps: " + ", ".join(sorted(SWEEPS))
                          + ". Free: height_strategy, geometry, models. Or: all"))
    pe.add_argument("-r", "--reference",
                    help="reference CSV; without it only internal sensitivity is reported")
    pe.add_argument("--at", nargs=2, action="append", metavar=("X", "Y"))
    pe.add_argument("--tree-id", action="append", dest="tree_id")
    pe.add_argument("--targets", help="JSON list of {tree_id, x, y}")
    pe.add_argument("--roi", type=float, default=4.0)
    pe.add_argument("--outdir", default="out")
    pe.set_defaults(func=cmd_experiment)

    pw = sub.add_parser("gui", help="open the review window (measure, inspect, approve)")
    pw.add_argument("file", nargs="?", help="LAS/LAZ to open on startup; optional")
    pw.add_argument("-c", "--config")
    pw.add_argument("--targets", help="JSON list of {tree_id, x, y} to preload")
    pw.add_argument("--outdir", default="out")
    pw.set_defaults(func=cmd_gui)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
