"""Restore one tape archive: extract the tar, decompress each .zst entry,
verify each file's sha256 against the manifest in a single read pass.

Closes the loop on the per-file integrity story: every byte that was hashed
into the manifest at compress time gets re-hashed here and compared.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tarfile
import time
from pathlib import Path

import zstandard

log = logging.getLogger("tape_archive.restore")

CHUNK = 1 << 20  # 1 MiB


def restore(
    tar_path: Path,
    dest_dir: Path,
    *,
    verify: bool = True,
    decompress: bool = True,
    keep_compressed: bool = False,
    skip_existing: bool = False,
    manifest_path: Path | None = None,
    parallel: int = 1,
) -> dict:
    """Restore one archive.

    ``tar_path``  - the .tar pulled from tape
    ``dest_dir``  - where to lay out the files. Tar entries land relative to
                    this dir (`<dest>/<original-rel-path>.zst`), and the
                    decompressed files alongside (`<dest>/<original-rel-path>`).
    ``verify``    - re-hash each decompressed file and compare to manifest.
                    SHA-256 of the *original* bytes; turn off only if you're
                    in a hurry and intend to verify separately later.
    ``decompress`` - if False, just extract the tar; .zst files stay as-is.
                    Cheap "layout" mode for inspecting what's in the archive.
    ``keep_compressed`` - leave the .zst alongside the decompressed file.
                    Default is to remove the .zst after successful verify.
    ``skip_existing`` - if the decompressed file already exists with matching
                    sha256, leave it alone (resume-friendly).
    ``manifest_path`` - external manifest to use. Defaults to the bundled
                    ``_MANIFEST.json`` extracted from the tar.
    ``parallel``  - decompress N files concurrently.
    """
    tar_path = Path(tar_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    log.info("extracting %s -> %s", tar_path, dest_dir)
    t0 = time.time()
    with tarfile.open(tar_path, "r") as t:
        t.extractall(dest_dir)
    log.info("extracted in %.1fs", time.time() - t0)

    if not decompress:
        log.info("decompress=False; .zst files left in place")
        return {"restored": [], "skipped": [], "failed": [], "tar_extracted": True}

    if manifest_path is None:
        manifest_path = dest_dir / "_MANIFEST.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    log.info("decompressing %d file(s) (verify=%s, parallel=%d)", len(files), verify, parallel)

    if parallel <= 1 or len(files) <= 1:
        results = [_restore_one(f, dest_dir, verify=verify,
                                keep_compressed=keep_compressed,
                                skip_existing=skip_existing) for f in files]
    else:
        from multiprocessing import Pool
        args_list = [(f, str(dest_dir), verify, keep_compressed, skip_existing) for f in files]
        with Pool(parallel) as pool:
            results = pool.map(_restore_worker, args_list)

    restored = [r for r in results if r["ok"] and not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    failed = [r for r in results if not r["ok"]]

    elapsed = time.time() - t0
    log.info("done in %.1fs: restored=%d skipped=%d failed=%d",
             elapsed, len(restored), len(skipped), len(failed))
    for f in failed:
        log.error("  FAIL %s: %s", f["path"], f["reason"])
    return {"restored": restored, "skipped": skipped, "failed": failed, "tar_extracted": True}


def _restore_worker(args):
    file_entry, dest_dir_str, verify, keep_compressed, skip_existing = args
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    return _restore_one(
        file_entry, Path(dest_dir_str),
        verify=verify, keep_compressed=keep_compressed, skip_existing=skip_existing,
    )


def _restore_one(file_entry, dest_dir, *, verify=True, keep_compressed=False, skip_existing=False):
    rel = file_entry["path"]
    expected_sha = file_entry.get("sha256", "")
    expected_size = int(file_entry.get("size_bytes", 0))

    zst_path = dest_dir / (rel + ".zst")
    out_path = dest_dir / rel

    # Resume: if the decompressed file is already there and verifies, skip.
    if skip_existing and out_path.exists():
        if not verify:
            return {"path": rel, "ok": True, "skipped": True, "reason": "exists (no verify)"}
        if _sha256_file(out_path) == expected_sha:
            return {"path": rel, "ok": True, "skipped": True, "reason": "exists and matches"}

    if not zst_path.exists():
        return {"path": rel, "ok": False, "reason": ".zst missing after tar extract"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    bytes_out = 0
    try:
        with zst_path.open("rb") as fin, out_path.open("wb") as fout:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(fin) as reader:
                while True:
                    chunk = reader.read(CHUNK)
                    if not chunk:
                        break
                    sha.update(chunk)
                    fout.write(chunk)
                    bytes_out += len(chunk)
    except (OSError, zstandard.ZstdError) as e:
        return {"path": rel, "ok": False, "reason": f"decompress failed: {e}"}

    if expected_size and bytes_out != expected_size:
        return {"path": rel, "ok": False,
                "reason": f"size mismatch ({bytes_out} != {expected_size})"}

    if verify and expected_sha:
        actual = sha.hexdigest()
        if actual != expected_sha:
            return {"path": rel, "ok": False,
                    "reason": f"sha256 mismatch ({actual} != {expected_sha})"}

    if not keep_compressed:
        try:
            zst_path.unlink()
        except OSError:
            pass

    return {"path": rel, "ok": True, "size": bytes_out}


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()
