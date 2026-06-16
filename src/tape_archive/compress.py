from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("tape_archive.compress")

ArchivePlan = list[tuple[str, list[Path]]]


def _sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_size_index(root: Path) -> dict[Path, int]:
    """Return total bytes for every dir under root and every file's own size."""
    sizes: dict[Path, int] = {}
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            sz = f.stat().st_size
        except FileNotFoundError:
            continue
        sizes[f] = sz
        p = f.parent
        while True:
            sizes[p] = sizes.get(p, 0) + sz
            if p == root:
                break
            p = p.parent
    sizes.setdefault(root, 0)
    return sizes


def _sanitize_rel(rel: Path) -> str:
    s = rel.as_posix()
    if s == ".":
        return "root"
    return s.replace("/", "__")


def _name_for(members: list[Path], root: Path, *, suffix: str = "") -> str:
    if len(members) == 1:
        base = _sanitize_rel(members[0].relative_to(root))
    else:
        first = _sanitize_rel(members[0].relative_to(root))
        base = f"{first}_plus{len(members) - 1}"
    if suffix:
        base = f"{base}_{suffix}"
    return f"{base}.tar.zst"


def plan_subfolder(source_dir: Path) -> ArchivePlan:
    subs = sorted(p for p in source_dir.iterdir() if p.is_dir())
    if subs:
        return [(_name_for([s], source_dir), [s]) for s in subs]
    return [(_name_for([source_dir], source_dir), [source_dir])]


def plan_whole(source_dir: Path) -> ArchivePlan:
    return [(_name_for([source_dir], source_dir), [source_dir])]


def plan_auto(source_dir: Path, target_bytes: int, max_bytes: int) -> ArchivePlan:
    """DFS + sibling bin-packing.

    For each directory: if its total size <= max_bytes, emit one archive.
    Otherwise descend; bundle adjacent small children into buckets up to
    max_bytes (closing a bucket once the next child would exceed it).
    Loose files at an intermediate level are grouped into their own archive.
    """
    sizes = _build_size_index(source_dir)
    archives: ArchivePlan = []
    _plan_dir_auto(source_dir, source_dir, sizes, target_bytes, max_bytes, archives)
    return archives


def _plan_dir_auto(
    node: Path,
    root: Path,
    sizes: dict[Path, int],
    target: int,
    max_size: int,
    archives: ArchivePlan,
) -> None:
    if sizes.get(node, 0) <= max_size:
        archives.append((_name_for([node], root), [node]))
        return
    items = sorted(node.iterdir())
    file_children = [i for i in items if i.is_file()]
    dir_children = [i for i in items if i.is_dir()]

    bucket: list[Path] = []
    bucket_size = 0
    for d in dir_children:
        dsize = sizes.get(d, 0)
        if dsize > max_size:
            if bucket:
                archives.append((_name_for(bucket, root), bucket))
                bucket, bucket_size = [], 0
            _plan_dir_auto(d, root, sizes, target, max_size, archives)
            continue
        if bucket_size + dsize > max_size:
            archives.append((_name_for(bucket, root), bucket))
            bucket, bucket_size = [d], dsize
        else:
            bucket.append(d)
            bucket_size += dsize
    if bucket:
        archives.append((_name_for(bucket, root), bucket))

    if file_children:
        archives.append((_name_for([node], root, suffix="loose"), file_children))


def plan_archives(
    source_dir: Path,
    granularity: str,
    *,
    target_size_mb: int = 1024,
    max_size_mb: int = 4096,
) -> ArchivePlan:
    if granularity == "subfolder":
        return plan_subfolder(source_dir)
    if granularity == "whole":
        return plan_whole(source_dir)
    if granularity == "auto":
        return plan_auto(source_dir, target_size_mb << 20, max_size_mb << 20)
    raise ValueError(f"unknown granularity: {granularity}")


def _write_archive_markers(mirror_dir: Path, results: list[dict], source_dir: Path) -> None:
    """Drop a _ARCHIVE_<name>.json file at each archive's root in the mirror tree.

    The marker filename is the archive name (with `_ARCHIVE_` prefix and `.json`
    suffix) so an `ls` reveals archive boundaries; its content carries the
    archive's metadata.
    """
    if not mirror_dir.exists():
        log.debug("mirror dir %s does not exist; skipping archive markers", mirror_dir)
        return
    for entry in results:
        payload = {
            "archive": entry["archive"],
            "archive_size": entry["size"],
            "archive_sha256": entry["sha256"],
            "zstd_level": entry.get("zstd_level"),
            "members": entry["members"],
            "file_count": entry.get("file_count", 0),
        }
        marker_name = f"_ARCHIVE_{entry['archive']}.json"
        # Place a marker at the root of each member's mirror path. For loose-
        # files archives the members are individual files; use their parent.
        roots_seen: set[Path] = set()
        for member_rel in entry["members"]:
            member_mirror = mirror_dir / member_rel
            if member_mirror.is_file():
                member_mirror = member_mirror.parent
            if not member_mirror.exists():
                member_mirror.mkdir(parents=True, exist_ok=True)
            if member_mirror in roots_seen:
                continue
            roots_seen.add(member_mirror)
            (member_mirror / marker_name).write_text(json.dumps(payload, indent=2))


def compress_archives(
    source_dir: Path,
    archive_dir: Path,
    *,
    granularity: str = "subfolder",
    level: int = 9,
    threads: int = 0,
    target_size_mb: int = 1024,
    max_size_mb: int = 4096,
    mirror_dir: Path | None = None,
    dry_run: bool = False,
) -> list[dict]:
    if not source_dir.exists():
        log.warning("no source data at %s; nothing to compress", source_dir)
        return []
    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_archives(
        source_dir, granularity, target_size_mb=target_size_mb, max_size_mb=max_size_mb
    )
    if not plan:
        log.warning("empty plan for %s", source_dir)
        return []

    results: list[dict] = []
    for name, paths in plan:
        out = archive_dir / name
        if out.exists() and not dry_run:
            log.info("skip existing archive: %s", out)
        else:
            members = [p.relative_to(source_dir).as_posix() for p in paths]
            tar_cmd = ["tar", "-cf", "-", "-C", str(source_dir), *members]
            zstd_cmd = ["zstd", f"-T{threads}", f"-{level}", "-q", "-o", str(out)]
            log.info("$ %s | %s", " ".join(tar_cmd), " ".join(zstd_cmd))
            if not dry_run:
                tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
                zstd = subprocess.Popen(zstd_cmd, stdin=tar.stdout)
                assert tar.stdout is not None
                tar.stdout.close()
                zstd.communicate()
                tar.wait()
                if tar.returncode != 0:
                    raise RuntimeError(f"tar failed (rc={tar.returncode}) for {name}")
                if zstd.returncode != 0:
                    raise RuntimeError(f"zstd failed (rc={zstd.returncode}) for {name}")
        size = out.stat().st_size if (not dry_run and out.exists()) else 0
        sha = _sha256_file(out) if (not dry_run and out.exists()) else ""
        file_count = sum(1 for p in paths for _ in (p.rglob("*") if p.is_dir() else [p]) if _.is_file()) if not dry_run else 0
        results.append({
            "archive": name,
            "members": [p.relative_to(source_dir).as_posix() for p in paths],
            "size": size,
            "sha256": sha,
            "zstd_level": level,
            "file_count": file_count,
        })
    if not dry_run:
        (archive_dir / "archives.json").write_text(json.dumps(results, indent=2))
        if mirror_dir is not None:
            _write_archive_markers(mirror_dir, results, source_dir)
    return results
