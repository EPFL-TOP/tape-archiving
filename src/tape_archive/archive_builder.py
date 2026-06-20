"""Build one .tar archive from a plan entry.

Inside the tar, every source file is stored as a single zstd-compressed entry
named "<original-path>.zst". The archive's own .tar is NOT compressed (so we
can read individual files without decompressing neighbors).

Per-file SHA-256 is computed during the same read pass as compression and
recorded in two manifests:
  - bundled inside the tar as _MANIFEST.json (survives if disk catalog is lost)
  - written next to the tar in <output_dir>/manifests/<name>.json
    (survives if tape is lost; carries the archive-level sha256 too)
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import zstandard

log = logging.getLogger("tape_archive.archive_builder")

CHUNK = 1 << 20  # 1 MiB read chunk


class _HashingFile:
    """File-object wrapper that streams writes through and hashes the bytes."""
    def __init__(self, fileobj):
        self._fp = fileobj
        self._sha = hashlib.sha256()
        self._bytes = 0

    def write(self, data):
        self._sha.update(data)
        self._bytes += len(data)
        return self._fp.write(data)

    def tell(self):
        return self._fp.tell()

    def flush(self):
        return self._fp.flush()

    def close(self):
        return self._fp.close()

    @property
    def sha256(self) -> str:
        return self._sha.hexdigest()

    @property
    def size(self) -> int:
        return self._bytes


def _collect_files(source_root: Path, members: list, excludes: set[str]) -> list[tuple[str, Path]]:
    """Return [(relative_path, absolute_path), ...] for files to include.
    Member specs are either a string (path under source_root) or a dict with
    {path, mode: "files_only"} for non-recursive include.
    """
    out: list[tuple[str, Path]] = []
    for m in members:
        files_only = False
        if isinstance(m, dict):
            rel = m["path"]
            files_only = m.get("mode") == "files_only"
        else:
            rel = m
        member_abs = source_root / rel
        if not member_abs.exists():
            log.warning("member does not exist on disk: %s", member_abs)
            continue
        if member_abs.is_file():
            out.append((rel, member_abs))
            continue
        if not member_abs.is_dir():
            log.warning("skipping non-file non-dir: %s", member_abs)
            continue
        if files_only:
            for f in sorted(member_abs.iterdir()):
                if f.is_file() and not f.is_symlink():
                    rel_f = f"{rel}/{f.name}" if rel else f.name
                    out.append((rel_f, f))
            continue
        # whole subtree
        for f in sorted(member_abs.rglob("*")):
            if not f.is_file() or f.is_symlink():
                continue
            rel_f = str(f.relative_to(source_root))
            if any(rel_f == ex or rel_f.startswith(ex + "/") for ex in excludes):
                continue
            out.append((rel_f, f))
    # de-dup, keep first occurrence
    seen = set()
    uniq = []
    for rel, p in out:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((rel, p))
    return uniq


def _compress_file_to_tmp(src: Path, tmp_path: Path, level: int) -> tuple[str, int]:
    """Single read pass: stream src → sha256 + zstd → tmp_path.
    Returns (sha256_hex, original_byte_count).

    We pass the source size to stream_writer so the decompressed size lands in
    the zstd frame header — this is what lets `zstandard.decompress(blob)`
    (one-shot) work at restore time.
    """
    sha = hashlib.sha256()
    size_orig = 0
    src_size = src.stat().st_size
    cctx = zstandard.ZstdCompressor(level=level, write_checksum=True)
    with src.open("rb") as fin, tmp_path.open("wb") as fout:
        with cctx.stream_writer(fout, size=src_size, closefd=False) as compressor:
            while True:
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                sha.update(chunk)
                size_orig += len(chunk)
                compressor.write(chunk)
    return sha.hexdigest(), size_orig


def build_archive(
    archive_entry: dict,
    source_root: Path,
    output_dir: Path,
    *,
    zstd_level: int = 3,
    force: bool = False,
) -> dict:
    """Build one archive from a plan entry. Returns the on-disk manifest dict."""
    name = archive_entry["name"]
    members = archive_entry["members"]
    excludes = set(archive_entry.get("excludes", []))

    archives_dir = output_dir / "archives"
    manifests_dir = output_dir / "manifests"
    archives_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    tar_path = archives_dir / f"{name}.tar"
    manifest_path = manifests_dir / f"{name}.json"

    if manifest_path.exists() and tar_path.exists() and not force:
        log.info("skip %s (manifest + tar already exist; use --force to rebuild)", name)
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    files = _collect_files(source_root, members, excludes)
    if not files:
        log.warning("archive %s has no files", name)
        return {}

    log.info("building %s: %d files to compress", name, len(files))
    start = time.time()

    file_manifests: list[dict] = []
    tmp_fd = tempfile.NamedTemporaryFile(prefix=f"tape-archive-{name}-", suffix=".zst", delete=False)
    tmp_fd.close()
    tmp_path = Path(tmp_fd.name)

    # Wrap the tar's underlying file with a streaming sha256 so we get archive
    # sha256 for free, no second read pass.
    raw_f = tar_path.open("wb")
    hashing = _HashingFile(raw_f)
    try:
        with tarfile.open(fileobj=hashing, mode="w") as tar:
            for i, (rel, full_path) in enumerate(files, 1):
                try:
                    st = full_path.stat()
                except OSError as e:
                    log.warning("stat failed for %s: %s", full_path, e)
                    continue
                sha_hex, size_orig = _compress_file_to_tmp(full_path, tmp_path, zstd_level)
                size_comp = tmp_path.stat().st_size
                info = tarfile.TarInfo(name=f"{rel}.zst")
                info.size = size_comp
                info.mtime = int(st.st_mtime)
                info.mode = 0o644
                with tmp_path.open("rb") as data:
                    tar.addfile(info, data)
                file_manifests.append({
                    "path": rel,
                    "size_bytes": size_orig,
                    "sha256": sha_hex,
                    "compressed_bytes": size_comp,
                    "mtime": st.st_mtime,
                })
                if i % 100 == 0 or i == len(files):
                    elapsed = time.time() - start
                    done_bytes = sum(f["size_bytes"] for f in file_manifests)
                    rate = done_bytes / max(elapsed, 0.001) / (1 << 20)
                    log.info(
                        "  %s: %d/%d files, %s read at %.1f MB/s",
                        name, i, len(files), _fmt_size(done_bytes), rate,
                    )

            bundle_manifest = {
                "archive": f"{name}.tar",
                "created_at": _iso_now(),
                "compression": {"format": "zstd", "level": zstd_level},
                "members": members,
                "excludes": sorted(excludes),
                "file_count": len(file_manifests),
                "total_uncompressed_bytes": sum(f["size_bytes"] for f in file_manifests),
                "total_compressed_bytes": sum(f["compressed_bytes"] for f in file_manifests),
                "files": file_manifests,
            }
            bundle_bytes = json.dumps(bundle_manifest, indent=2).encode()
            info = tarfile.TarInfo(name="_MANIFEST.json")
            info.size = len(bundle_bytes)
            info.mtime = int(time.time())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(bundle_bytes))
    finally:
        hashing.close()
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    archive_sha = hashing.sha256
    archive_size = hashing.size

    disk_manifest = {
        **bundle_manifest,
        "archive_sha256": archive_sha,
        "archive_size_bytes": archive_size,
        "source_root": str(source_root),
    }
    manifest_path.write_text(json.dumps(disk_manifest, indent=2), encoding="utf-8")

    elapsed = time.time() - start
    log.info(
        "done %s: %s → %s (%.1f%% of source) in %.1fs",
        name,
        _fmt_size(disk_manifest["total_uncompressed_bytes"]),
        _fmt_size(archive_size),
        100 * archive_size / max(disk_manifest["total_uncompressed_bytes"], 1),
        elapsed,
    )
    return disk_manifest


def build_all(
    plan: dict,
    source_root: Path,
    output_dir: Path,
    *,
    zstd_level: int = 3,
    force: bool = False,
    only: list[str] | None = None,
    parallel: int = 1,
) -> list[dict]:
    """Build every archive in the plan. Returns list of manifests.

    ``parallel`` controls how many archives are compressed concurrently in
    separate processes. Each archive is independent (separate tar, separate
    manifest, separate temp file), so this scales linearly until you hit your
    source-read bandwidth or CPU ceiling. Default 1 (sequential).
    """
    archives = plan["archives"]
    if only:
        wanted = set(only)
        archives = [a for a in archives if a["name"] in wanted]
        missing = wanted - {a["name"] for a in archives}
        if missing:
            log.warning("archives not found in plan: %s", ", ".join(sorted(missing)))
    log.info("building %d archive(s) from plan %s (parallel=%d)",
             len(archives), plan.get("source_root"), max(1, parallel))

    if parallel <= 1 or len(archives) <= 1:
        manifests: list[dict] = []
        for arch in archives:
            m = build_archive(arch, source_root, output_dir, zstd_level=zstd_level, force=force)
            if m:
                manifests.append(m)
        return manifests

    # Parallel mode: one worker process per archive (up to `parallel` at once).
    # Path objects survive pickling but we pass strings to stay defensive against
    # spawn-context quirks.
    from multiprocessing import Pool
    args_list = [
        (arch, str(source_root), str(output_dir), zstd_level, force)
        for arch in archives
    ]
    with Pool(parallel) as pool:
        results = pool.starmap(_build_archive_worker, args_list)
    return [m for m in results if m]


def _build_archive_worker(arch, source_root_str, output_dir_str, zstd_level, force):
    """multiprocessing entry point — restores log config in spawn workers."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    return build_archive(
        arch,
        Path(source_root_str),
        Path(output_dir_str),
        zstd_level=zstd_level,
        force=force,
    )


def write_collection_summary(output_dir: Path, manifests: list[dict], source_root: Path) -> Path:
    """Write summary.json — collection-level metadata used by the master index."""
    summary = {
        "name": output_dir.name,
        "compressed_at": _iso_now(),
        "source_root": str(source_root),
        "archive_count": len(manifests),
        "file_count": sum(m.get("file_count", 0) for m in manifests),
        "source_bytes": sum(m.get("total_uncompressed_bytes", 0) for m in manifests),
        "tape_bytes": sum(m.get("archive_size_bytes", 0) for m in manifests),
        "compressed_bytes": sum(m.get("total_compressed_bytes", 0) for m in manifests),
        "archives": [
            {
                "name": m["archive"].removesuffix(".tar"),
                "sha256": m.get("archive_sha256", ""),
                "size_bytes": m.get("archive_size_bytes", 0),
                "file_count": m.get("file_count", 0),
                "source_bytes": m.get("total_uncompressed_bytes", 0),
                "compressed_bytes": m.get("total_compressed_bytes", 0),
            }
            for m in manifests
        ],
    }
    path = output_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path


def verify_archives(archives_dir: Path, manifests_dir: Path) -> tuple[int, int, list[str]]:
    """Re-hash every .tar in archives_dir and compare against its manifest.

    Returns (checked_count, failure_count, failures).
    """
    failures: list[str] = []
    checked = 0
    for manifest_path in sorted(manifests_dir.glob("*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_sha = manifest.get("archive_sha256")
        if not expected_sha:
            continue
        tar_name = manifest["archive"]
        tar_path = archives_dir / tar_name
        if not tar_path.exists():
            failures.append(f"{tar_name}: missing on disk")
            checked += 1
            continue
        actual = _sha256_file(tar_path)
        checked += 1
        if actual != expected_sha:
            failures.append(f"{tar_name}: sha256 mismatch ({actual} != {expected_sha})")
            log.error("BAD %s: sha256 mismatch", tar_name)
        else:
            log.info("OK  %s (%s)", tar_name, _fmt_size(tar_path.stat().st_size))
    return checked, len(failures), failures


def _sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _fmt_size(b: float) -> str:
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"
