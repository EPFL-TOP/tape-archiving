"""SCITAS-side shipping: NAS → /work → /tape, one archive at a time.

Required external tools: `rclone` (for NAS pull) and `rsync` (for /work → tape).

State is implicit: a tar already present on /tape with matching sha256 is
considered shipped and is skipped on subsequent runs. Resumable, idempotent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("tape_archive.ship")

GiB = 1 << 30


def ship(
    nas: str,
    work: Path,
    tape: Path,
    *,
    only: list[str] | None = None,
    batch_budget_gb: float | None = None,
    rclone_transfers: int = 4,
    rclone_checkers: int = 8,
    dry_run: bool = False,
) -> dict:
    """Ship a collection from NAS to tape via /work staging.

    ``nas``: rclone-style path to a single collection's root, e.g.
        ``nas_rcp:upoates/common/lab-archives-catalog/Ece-thesis-movies``.
        The collection's ``summary.json``, ``archives/<name>.tar`` and
        ``manifests/<name>.json`` are all read from under this path.
    ``work``: local staging dir (e.g. on /work). Each archive lives here only
        between rclone copy and rsync-to-tape.
    ``tape``: destination on the tape mount (e.g. ``/archive/upoates/lab-archives/<COLLECTION>``).
    ``only``: optional list of archive names (without ``.tar``) to ship.
    ``batch_budget_gb``: refuse to start an archive that would push the total
        bytes currently in ``work/archives`` past this limit. Safety knob for
        the 20 TB /work quota; not a scheduler.
    """
    work_archives = work / "archives"
    work_manifests = work / "manifests"
    if not dry_run:
        work_archives.mkdir(parents=True, exist_ok=True)
        work_manifests.mkdir(parents=True, exist_ok=True)
        tape.mkdir(parents=True, exist_ok=True)

    log.info("fetching summary.json from %s", nas)
    summary_data = _rclone_cat(f"{nas}/summary.json")
    summary = json.loads(summary_data)
    archives = summary.get("archives", [])
    if only:
        wanted = set(only)
        archives = [a for a in archives if a["name"] in wanted]
        missing = wanted - {a["name"] for a in archives}
        if missing:
            log.warning("archive(s) not in summary: %s", ", ".join(sorted(missing)))
    if not archives:
        log.warning("no archives to ship")
        return {"shipped": [], "skipped": [], "failed": []}

    total = len(archives)
    results: dict = {"shipped": [], "skipped": [], "failed": []}
    started = time.time()

    for i, arch in enumerate(archives, 1):
        name = arch["name"]
        expected_sha = arch.get("sha256") or ""
        expected_size = int(arch.get("size_bytes") or 0)
        tar_name = f"{name}.tar"
        tape_tar = tape / tar_name
        work_tar = work_archives / tar_name
        log.info("=== [%d/%d] %s (%s) ===", i, total, name, _human(expected_size))

        # Skip if already on tape with the right sha256.
        if tape_tar.exists():
            if expected_sha:
                actual = "(dry-run skipped)" if dry_run else _sha256_file(tape_tar)
                if dry_run or actual == expected_sha:
                    log.info("  already on tape with matching sha256; skipping")
                    results["skipped"].append(name)
                    continue
                log.warning("  on tape but sha256 mismatch (%s != %s); re-shipping",
                            actual, expected_sha)
            else:
                log.warning("  on tape and no expected sha256 in summary; skipping")
                results["skipped"].append(name)
                continue

        # Budget check (counts bytes currently sitting in work/archives).
        if batch_budget_gb is not None and not dry_run:
            existing = sum(p.stat().st_size for p in work_archives.glob("*.tar"))
            if existing + expected_size > int(batch_budget_gb * GiB):
                log.error("  would push /work past --batch-budget-gb=%.0f; stopping",
                          batch_budget_gb)
                results["failed"].append({"name": name, "reason": "budget exceeded"})
                break

        if dry_run:
            log.info("  [dry-run] rclone-copy → verify → rsync → verify → rm")
            continue

        # Pull tar + manifest from NAS.
        try:
            t0 = time.time()
            _rclone_copy(f"{nas}/archives/{tar_name}", work_archives,
                         transfers=rclone_transfers, checkers=rclone_checkers)
            _rclone_copy(f"{nas}/manifests/{name}.json", work_manifests,
                         transfers=rclone_transfers, checkers=rclone_checkers)
            log.info("  pulled from NAS in %.1fs", time.time() - t0)
        except subprocess.CalledProcessError as e:
            log.error("  rclone copy failed (rc=%d)", e.returncode)
            results["failed"].append({"name": name, "reason": "rclone failed"})
            continue

        if not work_tar.exists():
            log.error("  tar missing after rclone: %s", work_tar)
            results["failed"].append({"name": name, "reason": "tar missing after rclone"})
            continue

        # Verify on /work against the archive's manifest (authoritative).
        try:
            work_sha = _sha256_file(work_tar)
        except OSError as e:
            log.error("  sha256 read failed on work: %s", e)
            results["failed"].append({"name": name, "reason": "sha256 read failed (work)"})
            continue
        if expected_sha and work_sha != expected_sha:
            log.error("  sha256 mismatch on /work (%s != %s); leaving tar in /work for inspection",
                      work_sha, expected_sha)
            results["failed"].append({"name": name, "reason": "verify failed (work)"})
            continue
        log.info("  verified on /work")

        # rsync to tape.
        try:
            t0 = time.time()
            subprocess.run(
                ["rsync", "-a", str(work_tar), str(tape_tar)],
                check=True,
            )
            log.info("  rsynced to tape in %.1fs", time.time() - t0)
        except subprocess.CalledProcessError as e:
            log.error("  rsync to tape failed (rc=%d)", e.returncode)
            results["failed"].append({"name": name, "reason": "rsync failed"})
            continue

        # Verify on tape.
        try:
            tape_sha = _sha256_file(tape_tar)
        except OSError as e:
            log.error("  sha256 read failed on tape: %s", e)
            results["failed"].append({"name": name, "reason": "sha256 read failed (tape)"})
            continue
        if expected_sha and tape_sha != expected_sha:
            log.error("  sha256 mismatch on tape (%s != %s); leaving tar on /work, NOT deleting tape copy",
                      tape_sha, expected_sha)
            results["failed"].append({"name": name, "reason": "verify failed (tape)"})
            continue
        log.info("  verified on tape")

        # Only now is it safe to remove from /work.
        try:
            work_tar.unlink()
        except OSError as e:
            log.warning("  could not remove tar from /work: %s", e)
        log.info("  freed %s on /work", _human(expected_size))
        results["shipped"].append(name)

    elapsed = time.time() - started
    log.info("done in %.1fs: shipped=%d skipped=%d failed=%d",
             elapsed, len(results["shipped"]), len(results["skipped"]), len(results["failed"]))
    for f in results["failed"]:
        log.error("  FAIL %s: %s", f["name"], f["reason"])

    # Write shipped.json back to the catalog (the --nas location) when every
    # archive listed in summary.json is now on tape with the right sha256.
    total_in_summary = len(archives)
    fully_shipped = (
        not results["failed"]
        and (len(results["shipped"]) + len(results["skipped"])) == total_in_summary
    )
    if fully_shipped:
        shipped_doc = {
            "shipped_at": datetime.now(tz=timezone.utc).isoformat(),
            "tape_root": str(tape),
            "host": socket.gethostname(),
            "archive_count": total_in_summary,
        }
        try:
            _push_json_to_catalog(nas, "shipped.json", shipped_doc)
            log.info("wrote shipped.json -> %s/shipped.json", nas)
        except subprocess.CalledProcessError as e:
            log.warning("could not push shipped.json to %s (rc=%d); "
                        "run `tape-archive mark-shipped` manually", nas, e.returncode)
    return results


def _push_json_to_catalog(nas: str, filename: str, payload: dict) -> None:
    """rclone-copy a small JSON file into the catalog root."""
    fd, tmp_path = tempfile.mkstemp(prefix="tape-archive-", suffix=".json")
    os.close(fd)
    try:
        Path(tmp_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        cmd = ["rclone", "copyto", tmp_path, f"{nas}/{filename}"]
        log.debug("$ %s", " ".join(cmd))
        subprocess.run(cmd, check=True)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------- rclone shims ----------

def _rclone_copy(src: str, dst: Path, *, transfers: int = 4, checkers: int = 8) -> None:
    cmd = [
        "rclone", "copy", src, str(dst),
        "--transfers", str(transfers),
        "--checkers", str(checkers),
    ]
    log.debug("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _rclone_cat(src: str) -> bytes:
    cmd = ["rclone", "cat", src]
    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, check=True, capture_output=True)
    return result.stdout


def _sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _human(b: float) -> str:
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"
