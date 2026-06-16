import logging
import subprocess
from typing import Sequence

log = logging.getLogger("tape_archive.proc")


def run_cmd(cmd: Sequence[str], *, dry_run: bool = False, check: bool = True, **kwargs):
    printable = " ".join(str(c) for c in cmd)
    log.info("$ %s", printable)
    if dry_run:
        return subprocess.CompletedProcess(list(cmd), 0, b"", b"")
    return subprocess.run(list(cmd), check=check, **kwargs)
