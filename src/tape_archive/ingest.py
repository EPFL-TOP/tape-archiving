import logging
import shutil
from pathlib import Path

log = logging.getLogger("tape_archive.ingest")


def _copy_tree(src: Path, dst: Path, *, dry_run: bool) -> None:
    log.info("copy %s -> %s", src, dst)
    if dry_run:
        return
    if dst.exists():
        log.warning("destination already exists, skipping: %s", dst)
        return
    shutil.copytree(src, dst)


def ingest_to_tape(archive_dir: Path, mirror_dir: Path, tape_target: Path, *, dry_run: bool = False) -> None:
    """Deposit archives + mirror tree onto the tape mount under `tape_target`."""
    if not dry_run:
        tape_target.mkdir(parents=True, exist_ok=True)
    _copy_tree(archive_dir, tape_target / "archives", dry_run=dry_run)
    _copy_tree(mirror_dir, tape_target / "mirror", dry_run=dry_run)
    log.info("ingest complete -> %s", tape_target)
