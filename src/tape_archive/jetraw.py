from __future__ import annotations

import logging
from pathlib import Path

from .proc import run_cmd

log = logging.getLogger("tape_archive.jetraw")


def set_destination_url(destination: str | None, url: str | Path, *, dry_run: bool = False) -> None:
    """Run `jr destination edit [<destination>] --set-url <url>`."""
    cmd: list[str] = ["jr", "destination", "edit"]
    if destination:
        cmd.append(destination)
    cmd.extend(["--set-url", str(url)])
    run_cmd(cmd, dry_run=dry_run)


def download(
    source: str,
    dest: Path,
    *,
    verify_checksum: bool = True,
    verify_checksum_flag: str = "--verify-checksum-after-download-before-decompression",
    dry_run: bool = False,
) -> None:
    """Run `jr download <source> <dest>` and optionally append the verify-checksum flag."""
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    cmd = ["jr", "download", source, str(dest)]
    if verify_checksum:
        cmd.append(verify_checksum_flag)
    run_cmd(cmd, dry_run=dry_run)
