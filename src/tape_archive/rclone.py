import logging
from pathlib import Path

log = logging.getLogger("tape_archive.rclone")


def check_mount(mount_path: Path) -> None:
    if not mount_path.exists():
        raise RuntimeError(f"rclone mount path does not exist: {mount_path}")
    if not mount_path.is_dir():
        raise RuntimeError(f"rclone mount path is not a directory: {mount_path}")
    try:
        entries = list(mount_path.iterdir())
    except PermissionError as e:
        raise RuntimeError(f"cannot read rclone mount {mount_path}: {e}") from e
    if not entries:
        log.warning("rclone mount %s appears empty; is rclone running?", mount_path)
    else:
        log.info("rclone mount OK: %s (%d top-level entries)", mount_path, len(entries))
