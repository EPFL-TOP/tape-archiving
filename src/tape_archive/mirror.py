from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("tape_archive.mirror")


def _sha256(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def build_mirror(
    source_dir: Path,
    mirror_dir: Path,
    *,
    per_file_checksum: bool = False,
    archive_resolver: Callable[[Path], str | None] | None = None,
) -> dict:
    """Mirror `source_dir` as empty files under `mirror_dir` and write manifest.json.

    `archive_resolver(rel_path)` maps each relative file path to the name of the
    archive that will contain it, so operators can browse the mirror and know
    which archive to pull from tape.
    """
    mirror_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    for p in sorted(source_dir.rglob("*")):
        rel = p.relative_to(source_dir)
        target = mirror_dir / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.touch()
        st = p.stat()
        entry: dict = {
            "path": rel.as_posix(),
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        }
        if archive_resolver is not None:
            entry["archive"] = archive_resolver(rel)
        if per_file_checksum:
            entry["sha256"] = _sha256(p)
        files.append(entry)
    manifest = {
        "source_dir": str(source_dir),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "file_count": len(files),
        "files": files,
    }
    (mirror_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("mirror built: %d files at %s", len(files), mirror_dir)
    return manifest
