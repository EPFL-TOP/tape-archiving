"""Build an editable archive plan from a source tree.

Strategies:
  top        - one archive per top-level directory
  experiment - one archive per second-level directory
  position   - one archive per leaf-ish subtree >= position_min_size_gb, plus
               one "metadata" bundle per experiment for small siblings
  auto       - size bin-packing (target/max), same heuristic as compress.plan_auto

The output YAML is meant to be reviewed and edited before running compress.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scan import walk_tree

LEVELS = ("top", "experiment", "position", "auto")


def make_plan(
    source_root: Path,
    *,
    level: str = "position",
    position_min_size_gb: float = 50.0,
    target_size_gb: float = 1000.0,
    max_size_gb: float = 3000.0,
) -> dict:
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}; valid: {LEVELS}")

    source_root = Path(source_root).resolve()
    tree, _ = walk_tree(source_root)

    if level == "top":
        archives = _plan_top(tree)
    elif level == "experiment":
        archives = _plan_experiment(tree)
    elif level == "position":
        archives = _plan_position(
            tree,
            position_min_size_bytes=int(position_min_size_gb * (1 << 30)),
            max_size_bytes=int(max_size_gb * (1 << 30)),
        )
    else:  # auto
        archives = _plan_auto(
            tree,
            target_bytes=int(target_size_gb * (1 << 30)),
            max_bytes=int(max_size_gb * (1 << 30)),
        )

    return {
        "source_root": str(source_root),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "strategy": level,
        "parameters": {
            "position_min_size_gb": position_min_size_gb,
            "target_size_gb": target_size_gb,
            "max_size_gb": max_size_gb,
        },
        "total_size_bytes": tree["size_subtree"],
        "total_file_count": tree["file_count_subtree"],
        "total_archives": len(archives),
        "archives": archives,
    }


# ---------- strategies ----------

def _plan_top(tree: dict) -> list[dict]:
    archives = []
    for child in tree["children"]:
        archives.append(_archive_from_node(child["name"], [child], []))
    return archives


def _plan_experiment(tree: dict) -> list[dict]:
    archives = []
    for top in tree["children"]:
        top_name = _safe(top["name"])
        for exp in top["children"]:
            archives.append(_archive_from_node(_safe(exp["name"]), [exp], [top_name]))
        if top["file_count_direct"] > 0:
            archives.append({
                "name": f"{top_name}__top_loose",
                "members": [{"path": top["path"], "mode": "files_only"}],
                "est_size_bytes": top["size_direct"],
                "est_file_count": top["file_count_direct"],
                "notes": "loose files at top level (not in any experiment subfolder)",
            })
    return archives


def _plan_position(tree: dict, *, position_min_size_bytes: int, max_size_bytes: int) -> list[dict]:
    archives = []
    for top in tree["children"]:
        top_name = _safe(top["name"])
        for exp in top["children"]:
            exp_name = _safe(exp["name"])
            big: list[dict] = []
            small: list[dict] = []
            _classify_position(exp, position_min_size_bytes, big, small)
            for b in big:
                rel = _path_under(b["path"], exp["path"])
                arch_name = f"{top_name}__{exp_name}__{_safe(rel)}"
                archive = _archive_from_node(arch_name, [b], [])
                if b["size_subtree"] > max_size_bytes:
                    archive["notes"] = (
                        f"exceeds max_size ({_human(b['size_subtree'])} > "
                        f"{_human(max_size_bytes)}); consider splitting"
                    )
                archives.append(archive)
            if small or exp["file_count_direct"] > 0:
                members: list[Any] = [s["path"] for s in small]
                est_size = sum(s["size_subtree"] for s in small)
                est_files = sum(s["file_count_subtree"] for s in small)
                if exp["file_count_direct"] > 0:
                    members.append({"path": exp["path"], "mode": "files_only"})
                    est_size += exp["size_direct"]
                    est_files += exp["file_count_direct"]
                archives.append({
                    "name": f"{top_name}__{exp_name}__metadata",
                    "members": members,
                    "est_size_bytes": est_size,
                    "est_file_count": est_files,
                })
    return archives


def _classify_position(node: dict, threshold: int, big: list, small: list) -> None:
    """Recursively classify subtrees.
    A subtree is `big` (own archive) if it's >= threshold AND looks leaf-ish.
    A subtree is `small` (bundle into metadata) if it's < threshold.
    Non-leaf-ish big subtrees are descended into.
    """
    sz = node["size_subtree"]
    if sz < threshold:
        small.append(node)
        return
    # Leaf-ish heuristic: no subdirs, or >= 50% of bytes are direct files at this level
    is_leafish = node["subdir_count"] == 0 or (
        sz > 0 and (node["size_direct"] / sz) >= 0.5
    )
    if is_leafish:
        big.append(node)
        return
    # Non-leaf-ish: descend
    for c in node["children"]:
        _classify_position(c, threshold, big, small)
    # If this dir also has significant direct files (>= threshold), capture as files-only archive
    if node["size_direct"] >= threshold:
        big.append({
            "name": f"{node['name']}__loose",
            "path": node["path"],
            "size_subtree": node["size_direct"],
            "file_count_subtree": node["file_count_direct"],
            "_files_only": True,
        })


def _plan_auto(tree: dict, *, target_bytes: int, max_bytes: int) -> list[dict]:
    """Greedy DFS bin-packing on the children of root.
    Same semantics as compress.plan_auto, but emits archives in plan format.
    """
    archives: list[dict] = []
    _walk_auto(tree, target_bytes, max_bytes, archives)
    return archives


def _walk_auto(node: dict, target: int, max_size: int, archives: list) -> None:
    if node["size_subtree"] <= max_size:
        archives.append(_archive_from_node(_safe(node["name"]), [node], []))
        return
    bucket: list[dict] = []
    bucket_size = 0
    for c in node["children"]:
        if not _is_dir_node(c):
            continue
        csize = c["size_subtree"]
        if csize > max_size:
            if bucket:
                archives.append(_archive_from_bucket(bucket))
                bucket, bucket_size = [], 0
            _walk_auto(c, target, max_size, archives)
            continue
        if bucket_size + csize > max_size:
            archives.append(_archive_from_bucket(bucket))
            bucket = [c]
            bucket_size = csize
        else:
            bucket.append(c)
            bucket_size += csize
    if bucket:
        archives.append(_archive_from_bucket(bucket))


def _is_dir_node(node: dict) -> bool:
    # All nodes in the scan tree are dirs (files aren't surfaced as nodes).
    return True


# ---------- archive entry builder ----------

def _archive_from_node(name: str, nodes: list[dict], prefix_parts: list[str]) -> dict:
    members: list[Any] = []
    est_size = 0
    est_files = 0
    for n in nodes:
        if n.get("_files_only"):
            members.append({"path": n["path"], "mode": "files_only"})
        else:
            members.append(n["path"])
        est_size += n["size_subtree"]
        est_files += n["file_count_subtree"]
    full_name = "__".join([*prefix_parts, name]) if prefix_parts else name
    return {
        "name": _safe(full_name),
        "members": members,
        "est_size_bytes": est_size,
        "est_file_count": est_files,
    }


def _archive_from_bucket(bucket: list[dict]) -> dict:
    if len(bucket) == 1:
        return _archive_from_node(_safe(bucket[0]["name"]), bucket, [])
    first = _safe(bucket[0]["name"])
    return _archive_from_node(f"{first}_plus{len(bucket) - 1}", bucket, [])


def _path_under(child_path: str, parent_path: str) -> str:
    """Return the portion of child_path that lies under parent_path (slash-separated)."""
    if child_path == parent_path:
        return Path(parent_path).name
    if child_path.startswith(parent_path + "/"):
        return child_path[len(parent_path) + 1:]
    return child_path


# ---------- name sanitizer ----------

_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _safe(s: str) -> str:
    """Make a string safe to use as a filename component."""
    s = s.replace(" ", "_").replace("/", "__")
    out = "".join(c if c in _SAFE_CHARS else "_" for c in s)
    out = out.strip("._-")
    while "__" in out:
        # collapse consecutive underscores from double-replacement
        prev = out
        out = out.replace("___", "__")
        if out == prev:
            break
    return out or "root"


# ---------- formatting helpers ----------

def _human(b: float) -> str:
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"
