from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .compress import compress_archives, plan_archives
from .config import Config
from .ingest import ingest_to_tape
from .jetraw import download, set_destination_url
from .mirror import build_mirror
from .rclone import check_mount

log = logging.getLogger("tape_archive.pipeline")

ALL_STEPS = ["precheck", "download", "mirror", "compress", "ingest"]


def _build_archive_resolver(source_dir: Path, cfg: Config):
    plan = plan_archives(
        source_dir,
        cfg.compression.granularity,
        target_size_mb=cfg.compression.target_size_mb,
        max_size_mb=cfg.compression.max_size_mb,
    )
    prefix_to_archive: dict[str, str] = {}
    for name, paths in plan:
        for p in paths:
            key = p.relative_to(source_dir).as_posix()
            prefix_to_archive[key] = name

    def resolver(rel: Path) -> str | None:
        parts = rel.parts
        for i in range(len(parts), 0, -1):
            key = "/".join(parts[:i])
            if key in prefix_to_archive:
                return prefix_to_archive[key]
        return prefix_to_archive.get("", None) or prefix_to_archive.get(".", None)

    return resolver


def _stage_precheck(cfg: Config, *, dry_run: bool) -> None:
    log.info("== precheck ==")
    check_mount(cfg.rclone.mount_path)
    if cfg.source.mode == "jetraw" and cfg.jetraw.set_url:
        set_destination_url(cfg.jetraw.destination, cfg.rclone.mount_path, dry_run=dry_run)
    elif cfg.source.mode == "plain":
        nas_src = cfg.nas_source_path
        if not dry_run and not nas_src.exists():
            raise RuntimeError(f"plain source path does not exist: {nas_src}")
        log.info("plain source: %s (stage_locally=%s)", nas_src, cfg.source.stage_locally)


def _stage_download(cfg: Config, *, force: bool, dry_run: bool) -> None:
    log.info("== download ==")
    if cfg.source.mode == "plain" and not cfg.source.stage_locally:
        log.info("plain mode, no local staging: reading directly from %s", cfg.nas_source_path)
        return

    marker = cfg.download_dir.parent / f"{cfg.source.name}.downloaded"
    if marker.exists() and not force:
        log.info("download already done (marker %s); skipping", marker)
        return

    if cfg.source.mode == "jetraw":
        if not dry_run:
            cfg.download_dir.mkdir(parents=True, exist_ok=True)
        assert cfg.source.jetraw_path is not None
        download(
            cfg.source.jetraw_path,
            cfg.download_dir,
            verify_checksum=cfg.jetraw.verify_checksum,
            verify_checksum_flag=cfg.jetraw.verify_checksum_flag,
            dry_run=dry_run,
        )
    else:  # plain + stage_locally
        src = cfg.nas_source_path
        dst = cfg.download_dir
        log.info("$ cp -r %s %s", src, dst)
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                log.warning("staging dst exists; removing before copy: %s", dst)
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    if not dry_run:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def _stage_mirror(cfg: Config, *, force: bool, dry_run: bool) -> None:
    log.info("== mirror ==")
    manifest_path = cfg.mirror_dir / "manifest.json"
    if manifest_path.exists() and not force:
        log.info("mirror already done (%s); skipping", manifest_path)
        return
    if dry_run:
        log.info("[dry-run] would mirror %s -> %s", cfg.source_data_dir, cfg.mirror_dir)
        return
    resolver = _build_archive_resolver(cfg.source_data_dir, cfg)
    build_mirror(
        cfg.source_data_dir,
        cfg.mirror_dir,
        per_file_checksum=cfg.mirror.per_file_checksum,
        archive_resolver=resolver,
    )


def _stage_compress(cfg: Config, *, dry_run: bool) -> None:
    log.info("== compress ==")
    compress_archives(
        cfg.source_data_dir,
        cfg.archive_dir,
        granularity=cfg.compression.granularity,
        level=cfg.compression.level,
        threads=cfg.compression.threads,
        target_size_mb=cfg.compression.target_size_mb,
        max_size_mb=cfg.compression.max_size_mb,
        mirror_dir=cfg.mirror_dir,
        dry_run=dry_run,
    )


def _stage_ingest(cfg: Config, *, dry_run: bool) -> None:
    log.info("== ingest ==")
    ingest_to_tape(cfg.archive_dir, cfg.mirror_dir, cfg.tape_target_dir, dry_run=dry_run)


def run(cfg: Config, *, steps: list[str], force: bool, dry_run: bool) -> int:
    for s in steps:
        if s not in ALL_STEPS:
            raise ValueError(f"unknown step: {s}. valid: {ALL_STEPS}")
    if "precheck" in steps:
        _stage_precheck(cfg, dry_run=dry_run)
    if "download" in steps:
        _stage_download(cfg, force=force, dry_run=dry_run)
    if "mirror" in steps:
        _stage_mirror(cfg, force=force, dry_run=dry_run)
    if "compress" in steps:
        _stage_compress(cfg, dry_run=dry_run)
    if "ingest" in steps:
        _stage_ingest(cfg, dry_run=dry_run)
    log.info("done")
    return 0
