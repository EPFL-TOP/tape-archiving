from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .pipeline import ALL_STEPS, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tape-archive")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the archiving pipeline")
    p_run.add_argument("config", help="Path to YAML config")
    p_run.add_argument(
        "--steps",
        default=",".join(ALL_STEPS),
        help=f"Comma-separated subset of steps (default: all). Valid: {','.join(ALL_STEPS)}",
    )
    p_run.add_argument("--force", action="store_true", help="Re-run steps even if outputs exist")
    p_run.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    p_run.add_argument("-v", "--verbose", action="store_true")

    p_scan = sub.add_parser(
        "scan",
        help="Walk a directory and emit a structure summary (markdown or JSON).",
    )
    p_scan.add_argument("path", help="Directory to scan")
    p_scan.add_argument(
        "--format", choices=["md", "json"], default="md", help="Output format (default: md)"
    )
    p_scan.add_argument(
        "--candidate-size-gb",
        type=float,
        default=10.0,
        help="Flag leaf-ish folders larger than this as archive candidates (default: 10)",
    )
    p_scan.add_argument(
        "--candidate-min-files",
        type=int,
        default=10,
        help="A candidate must also contain at least this many files (default: 10)",
    )
    p_scan.add_argument(
        "--max-tree-depth", type=int, default=4, help="Truncate the printed tree at this depth"
    )
    p_scan.add_argument("-o", "--output", help="Write to file instead of stdout")
    p_scan.add_argument("-v", "--verbose", action="store_true")

    p_plan = sub.add_parser(
        "plan",
        help="Build an editable archive plan from a source tree (YAML + optional HTML preview).",
    )
    p_plan.add_argument("path", help="Source directory to plan")
    p_plan.add_argument(
        "--level",
        choices=["top", "experiment", "position", "auto"],
        default="position",
        help="Granularity strategy (default: position).",
    )
    p_plan.add_argument(
        "--position-min-size-gb", type=float, default=50.0,
        help="position level: any leaf-ish folder >= this size becomes its own archive (default: 50).",
    )
    p_plan.add_argument(
        "--target-size-gb", type=float, default=1000.0,
        help="auto level: soft target archive size (default: 1000 = 1 TB).",
    )
    p_plan.add_argument(
        "--max-size-gb", type=float, default=3000.0,
        help="auto/position levels: hard cap; archives over this get a warning (default: 3000 = 3 TB).",
    )
    p_plan.add_argument("-o", "--output", default="plan.yaml", help="Write YAML plan here (default: plan.yaml)")
    p_plan.add_argument("--preview", help="Also emit a single-file HTML preview at this path")
    p_plan.add_argument("-v", "--verbose", action="store_true")

    p_planner = sub.add_parser(
        "planner",
        help="Build an interactive HTML page: browse the tree, click folders "
             "to mark archive roots, click Download to get plan.yaml.",
    )
    p_planner.add_argument("path", help="Source directory to plan")
    p_planner.add_argument(
        "-o", "--output", default="planner.html",
        help="Write the interactive HTML here (default: planner.html)",
    )
    p_planner.add_argument("-v", "--verbose", action="store_true")

    p_compress = sub.add_parser(
        "compress",
        help="Ingest a plan.yaml and build the archives + per-file manifests + catalog HTML.",
    )
    p_compress.add_argument("plan", help="Path to plan.yaml")
    p_compress.add_argument("-o", "--output", required=True, help="Output directory for archives/, manifests/, catalog.html")
    p_compress.add_argument("--source-root", help="Override source_root from plan.yaml (handy when paths differ between hosts)")
    p_compress.add_argument("--zstd-level", type=int, default=3, help="zstd compression level 1-22 (default: 3)")
    p_compress.add_argument("--archive", action="append", help="Only build this archive (repeatable). Default: all in plan.")
    p_compress.add_argument("--force", action="store_true", help="Rebuild archives even when their manifest already exists")
    p_compress.add_argument("--no-catalog", action="store_true", help="Skip catalog HTML generation at the end")
    p_compress.add_argument("--parallel", type=int, default=1,
                            help="Compress N archives concurrently (default 1 = sequential). Each archive is independent.")
    p_compress.add_argument("-v", "--verbose", action="store_true")

    p_catalog = sub.add_parser(
        "catalog",
        help="(Re)generate the browsable catalog HTML from existing manifests.",
    )
    p_catalog.add_argument("output_dir", help="Output directory containing manifests/ subdir")
    p_catalog.add_argument("-o", "--output", default=None, help="Output HTML path (default: <output_dir>/catalog.html)")
    p_catalog.add_argument("-v", "--verbose", action="store_true")

    p_index = sub.add_parser(
        "index",
        help="(Re)generate the master HTML index that links every collection's catalog.",
    )
    p_index.add_argument("catalog_root", help="Parent directory holding one subfolder per collection")
    p_index.add_argument("-o", "--output", default=None, help="Output HTML path (default: <catalog_root>/index.html)")
    p_index.add_argument("-v", "--verbose", action="store_true")

    p_verify = sub.add_parser(
        "verify",
        help="Re-hash every .tar in an archives dir and compare against its manifest's archive_sha256.",
    )
    p_verify.add_argument("output_dir", nargs="?",
                          help="A tape-archive compress output dir (with archives/ and manifests/ subdirs)")
    p_verify.add_argument("--archives", help="Archives dir to check (defaults to <output_dir>/archives)")
    p_verify.add_argument("--manifests", help="Manifests dir (defaults to <output_dir>/manifests)")
    p_verify.add_argument("-v", "--verbose", action="store_true")

    p_restore = sub.add_parser(
        "restore",
        help="Extract one .tar archive, decompress each per-file .zst, and verify each file's sha256.",
    )
    p_restore.add_argument("tar", help="Path to the .tar file (already pulled from tape)")
    p_restore.add_argument("-o", "--output", required=True,
                           help="Destination directory; tar contents land here.")
    p_restore.add_argument("--no-verify", action="store_true",
                           help="Skip per-file sha256 verification (still checks size).")
    p_restore.add_argument("--no-decompress", action="store_true",
                           help="Just extract the tar; leave .zst files in place.")
    p_restore.add_argument("--keep-compressed", action="store_true",
                           help="After verify, keep the .zst alongside the decompressed file.")
    p_restore.add_argument("--skip-existing", action="store_true",
                           help="Skip files whose decompressed copy already exists and verifies.")
    p_restore.add_argument("--manifest",
                           help="External manifest (default: _MANIFEST.json bundled in the tar)")
    p_restore.add_argument("--parallel", type=int, default=1,
                           help="Decompress N files concurrently (default 1).")
    p_restore.add_argument("-v", "--verbose", action="store_true")

    p_ship = sub.add_parser(
        "ship",
        help="Ship one collection's archives from NAS to tape via /work (rclone copy → verify → rsync → verify → delete).",
    )
    p_ship.add_argument("--nas", required=True,
                        help="rclone-style path to the collection on NAS, e.g. nas_rcp:upoates/common/lab-archives-catalog/Ece-thesis-movies")
    p_ship.add_argument("--work", required=True, help="Local staging dir on /work")
    p_ship.add_argument("--tape", required=True, help="Local destination on the tape mount")
    p_ship.add_argument("--archive", action="append",
                        help="Only ship this archive (without .tar). Repeatable. Default: every archive in the collection's summary.json.")
    p_ship.add_argument("--batch-budget-gb", type=float,
                        help="Refuse to start an archive that would push /work past this many GB. Safety check for the /work quota.")
    p_ship.add_argument("--rclone-transfers", type=int, default=4)
    p_ship.add_argument("--rclone-checkers", type=int, default=8)
    p_ship.add_argument("--dry-run", action="store_true")
    p_ship.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if args.cmd == "run":
        cfg = load_config(args.config)
        steps = [s.strip() for s in args.steps.split(",") if s.strip()]
        return run(cfg, steps=steps, force=args.force, dry_run=args.dry_run)

    if args.cmd == "scan":
        from .scan import scan, to_json, to_markdown
        result = scan(
            Path(args.path),
            max_tree_depth=args.max_tree_depth,
            candidate_size_gb=args.candidate_size_gb,
            candidate_min_files=args.candidate_min_files,
        )
        text = to_json(result) if args.format == "json" else to_markdown(result)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            print(text)
        return 0

    if args.cmd == "plan":
        import yaml
        from .plan import make_plan
        plan = make_plan(
            Path(args.path),
            level=args.level,
            position_min_size_gb=args.position_min_size_gb,
            target_size_gb=args.target_size_gb,
            max_size_gb=args.max_size_gb,
        )
        Path(args.output).write_text(yaml.safe_dump(plan, sort_keys=False, width=120), encoding="utf-8")
        print(
            f"wrote {args.output}: {plan['total_archives']} archives, "
            f"total {plan['total_size_bytes'] / (1 << 40):.2f} TB",
            file=sys.stderr,
        )
        if args.preview:
            from .plan_html import render_plan_html
            render_plan_html(plan, Path(args.preview))
            print(f"wrote {args.preview}", file=sys.stderr)
        return 0

    if args.cmd == "planner":
        from .planner_ui import render_planner
        out = Path(args.output)
        render_planner(Path(args.path), out)
        print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB)", file=sys.stderr)
        return 0

    if args.cmd == "compress":
        import shutil
        import yaml
        from .archive_builder import build_all, write_collection_summary
        from .catalog_html import render_catalog

        plan_path = Path(args.plan)
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        source_root = Path(args.source_root or plan["source_root"]).resolve()
        if not source_root.is_dir():
            raise SystemExit(f"source_root does not exist or is not a directory: {source_root}")

        out_dir = Path(args.output).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan_path, out_dir / "plan.yaml")

        manifests = build_all(
            plan,
            source_root,
            out_dir,
            zstd_level=args.zstd_level,
            force=args.force,
            only=args.archive,
            parallel=args.parallel,
        )
        # Aggregate summary (use all on-disk manifests, not just freshly-built ones,
        # so summary reflects the full collection even when --archive limits this run).
        import json as _json
        all_manifests = []
        for p in sorted((out_dir / "manifests").glob("*.json")):
            try:
                all_manifests.append(_json.loads(p.read_text(encoding="utf-8")))
            except _json.JSONDecodeError:
                continue
        if all_manifests:
            summary_path = write_collection_summary(out_dir, all_manifests, source_root)
            print(f"wrote {summary_path}", file=sys.stderr)
        if not args.no_catalog:
            html_out = out_dir / "catalog.html"
            render_catalog(out_dir, html_out)
            print(f"wrote {html_out}", file=sys.stderr)
        return 0

    if args.cmd == "catalog":
        from .catalog_html import render_catalog
        out_dir = Path(args.output_dir)
        html_out = Path(args.output) if args.output else (out_dir / "catalog.html")
        render_catalog(out_dir, html_out)
        print(f"wrote {html_out}", file=sys.stderr)
        return 0

    if args.cmd == "index":
        from .master_html import render_master_index
        catalog_root = Path(args.catalog_root)
        html_out = Path(args.output) if args.output else (catalog_root / "index.html")
        render_master_index(catalog_root, html_out)
        print(f"wrote {html_out}", file=sys.stderr)
        return 0

    if args.cmd == "verify":
        from .archive_builder import verify_archives
        if args.archives:
            archives = Path(args.archives)
            manifests = Path(args.manifests) if args.manifests else None
            if manifests is None:
                raise SystemExit("--manifests is required when --archives is given")
        else:
            if not args.output_dir:
                raise SystemExit("provide either output_dir or --archives/--manifests")
            archives = Path(args.output_dir) / "archives"
            manifests = Path(args.output_dir) / "manifests"
        checked, fails, failures = verify_archives(archives, manifests)
        print(f"checked {checked} archive(s), {fails} failure(s)", file=sys.stderr)
        for line in failures:
            print(f"  FAIL: {line}", file=sys.stderr)
        return 0 if fails == 0 else 1

    if args.cmd == "restore":
        from .restore import restore
        results = restore(
            Path(args.tar),
            Path(args.output),
            verify=not args.no_verify,
            decompress=not args.no_decompress,
            keep_compressed=args.keep_compressed,
            skip_existing=args.skip_existing,
            manifest_path=Path(args.manifest) if args.manifest else None,
            parallel=args.parallel,
        )
        return 0 if not results.get("failed") else 1

    if args.cmd == "ship":
        from .ship import ship
        results = ship(
            args.nas,
            Path(args.work),
            Path(args.tape),
            only=args.archive,
            batch_budget_gb=args.batch_budget_gb,
            rclone_transfers=args.rclone_transfers,
            rclone_checkers=args.rclone_checkers,
            dry_run=args.dry_run,
        )
        return 0 if not results["failed"] else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
