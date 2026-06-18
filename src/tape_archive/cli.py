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
            Path(args.output).write_text(text)
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
        Path(args.output).write_text(yaml.safe_dump(plan, sort_keys=False, width=120))
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
