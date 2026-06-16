"""Directory scan: produce a summary suitable for deciding archive granularity."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def scan(
    root: Path,
    *,
    max_tree_depth: int = 4,
    candidate_size_gb: float = 10.0,
    candidate_min_files: int = 10,
) -> dict:
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    ext_totals: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0})
    tree = _walk(root, root, ext_totals)
    candidates = _flag_candidates(tree, int(candidate_size_gb * (1 << 30)), candidate_min_files)
    hotspots = _top_dirs_by_size(tree, n=15)
    _prune_tree(tree, max_tree_depth)

    return {
        "root": str(root),
        "scanned_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_size_bytes": tree["size_subtree"],
        "total_file_count": tree["file_count_subtree"],
        "extensions": {k: dict(v) for k, v in ext_totals.items()},
        "tree": tree,
        "candidates": candidates,
        "hotspots": hotspots,
    }


def _walk(node_path: Path, root: Path, ext_totals: dict) -> dict:
    rel = node_path.relative_to(root)
    rel_s = "." if str(rel) == "." else rel.as_posix()
    node = {
        "path": rel_s,
        "name": node_path.name if rel_s != "." else ".",
        "size_subtree": 0,
        "size_direct": 0,
        "file_count_direct": 0,
        "file_count_subtree": 0,
        "subdir_count": 0,
        "extensions": {},
        "largest_file": None,
        "children": [],
    }
    try:
        entries = list(os.scandir(node_path))
    except (PermissionError, OSError):
        return node
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name))
    for e in entries:
        try:
            if e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                child = _walk(Path(e.path), root, ext_totals)
                node["children"].append(child)
                node["subdir_count"] += 1
                node["size_subtree"] += child["size_subtree"]
                node["file_count_subtree"] += child["file_count_subtree"]
                for ext, c in child["extensions"].items():
                    node["extensions"][ext] = node["extensions"].get(ext, 0) + c
            elif e.is_file(follow_symlinks=False):
                sz = e.stat(follow_symlinks=False).st_size
                ext = Path(e.name).suffix.lower()
                node["size_direct"] += sz
                node["size_subtree"] += sz
                node["file_count_direct"] += 1
                node["file_count_subtree"] += 1
                node["extensions"][ext] = node["extensions"].get(ext, 0) + 1
                ext_totals[ext]["count"] += 1
                ext_totals[ext]["size"] += sz
                if node["largest_file"] is None or sz > node["largest_file"]["size"]:
                    node["largest_file"] = {"name": e.name, "size": sz}
        except OSError:
            continue
    return node


def _flag_candidates(
    node: dict, threshold: int, min_files: int, out: list | None = None
) -> list:
    """A directory is a candidate if its subtree >= threshold AND it's
    leaf-ish (no subdirs, or >50% of bytes are direct files). Stop recursing
    once a directory is flagged."""
    if out is None:
        out = []
    is_leafish = node["subdir_count"] == 0 or (
        node["size_subtree"] > 0
        and (node["size_direct"] / node["size_subtree"]) > 0.5
    )
    if (
        node["size_subtree"] >= threshold
        and node["file_count_subtree"] >= min_files
        and is_leafish
    ):
        out.append({
            "path": node["path"],
            "size_bytes": node["size_subtree"],
            "file_count": node["file_count_subtree"],
            "subdir_count": node["subdir_count"],
            "top_extensions": _top_n(node["extensions"], 3),
        })
        return out
    for c in node["children"]:
        _flag_candidates(c, threshold, min_files, out)
    return out


def _top_dirs_by_size(node: dict, n: int = 15) -> list:
    flat: list[tuple[str, int, int]] = []
    _flatten_dirs(node, flat)
    flat.sort(key=lambda x: -x[1])
    return [
        {"path": p, "size_bytes": s, "file_count": f}
        for p, s, f in flat[:n]
    ]


def _flatten_dirs(node: dict, out: list) -> None:
    out.append((node["path"], node["size_subtree"], node["file_count_subtree"]))
    for c in node["children"]:
        _flatten_dirs(c, out)


def _prune_tree(node: dict, max_depth: int, depth: int = 0) -> None:
    if depth >= max_depth:
        node["children"] = []
        return
    for c in node["children"]:
        _prune_tree(c, max_depth, depth + 1)


def _top_n(d: dict, n: int) -> list[tuple[str, int]]:
    return sorted(d.items(), key=lambda x: -x[1])[:n]


def to_json(result: dict) -> str:
    return json.dumps(result, indent=2)


def to_markdown(result: dict) -> str:
    L: list[str] = []
    L.append(f"# Scan: `{result['root']}`")
    L.append("")
    L.append(f"- Scanned: `{result['scanned_at']}`")
    L.append(f"- Total size: **{_fmt_size(result['total_size_bytes'])}**")
    L.append(f"- Total files: **{result['total_file_count']:,}**")
    L.append("")

    L.append("## Extensions (top 10 by total size)")
    L.append("")
    L.append("| Extension | File count | Total size |")
    L.append("|---|---:|---:|")
    for ext, stats in sorted(result["extensions"].items(), key=lambda x: -x[1]["size"])[:10]:
        L.append(f"| `{ext or '(none)'}` | {stats['count']:,} | {_fmt_size(stats['size'])} |")
    L.append("")

    L.append("## Size hotspots (top 15 directories by subtree size)")
    L.append("")
    L.append("| Path | Size | Files |")
    L.append("|---|---:|---:|")
    for h in result["hotspots"]:
        L.append(f"| `{h['path']}` | {_fmt_size(h['size_bytes'])} | {h['file_count']:,} |")
    L.append("")

    L.append("## Suggested archive candidates")
    L.append("")
    L.append("_Folders ≥ threshold whose bytes are mostly direct files (leaf-ish)._")
    L.append("")
    if not result["candidates"]:
        L.append("_None matched the criteria._")
    else:
        L.append("| Path | Size | Files | Top extensions |")
        L.append("|---|---:|---:|---|")
        for c in sorted(result["candidates"], key=lambda x: -x["size_bytes"]):
            ext_str = ", ".join(f"`{e[0] or 'none'}`×{e[1]}" for e in c["top_extensions"])
            L.append(f"| `{c['path']}` | {_fmt_size(c['size_bytes'])} | {c['file_count']:,} | {ext_str} |")
    L.append("")

    L.append("## Tree (depth-limited)")
    L.append("")
    L.append("```")
    _render_tree(result["tree"], L)
    L.append("```")
    L.append("")
    return "\n".join(L)


def _render_tree(node: dict, lines: list, prefix: str = "", is_root: bool = True) -> None:
    if is_root:
        lines.append(
            f"{node['name']}/   [{_fmt_size(node['size_subtree'])}, "
            f"{node['file_count_subtree']:,} files]"
        )
    children = node.get("children", [])
    for i, c in enumerate(children):
        last = i == len(children) - 1
        branch = "└── " if last else "├── "
        # Show direct-file count only when it differs from subtree (interior nodes with loose files)
        extra = ""
        if c["subdir_count"] > 0 and c["file_count_direct"] > 0:
            extra = f"; {c['file_count_direct']} loose"
        lines.append(
            f"{prefix}{branch}{c['name']}/   "
            f"[{_fmt_size(c['size_subtree'])}, {c['file_count_subtree']:,} files{extra}]"
        )
        new_prefix = prefix + ("    " if last else "│   ")
        _render_tree(c, lines, new_prefix, is_root=False)


def _fmt_size(b: float) -> str:
    b = float(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
