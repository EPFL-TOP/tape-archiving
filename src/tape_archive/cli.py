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

    return 0


if __name__ == "__main__":
    sys.exit(main())
