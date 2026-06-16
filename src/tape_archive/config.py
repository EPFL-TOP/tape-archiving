from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

SourceMode = Literal["jetraw", "plain"]
Granularity = Literal["subfolder", "whole", "auto"]


@dataclass
class SourceCfg:
    name: str
    mode: SourceMode = "jetraw"
    # jetraw mode: path as known to jetraw (e.g. /home/2024_12_Ece)
    jetraw_path: str | None = None
    # plain mode: absolute path, or path relative to rclone.mount_path
    nas_path: str | None = None
    # plain mode only: copy from rclone mount to <staging>/data/<name>/ before
    # mirror+compress, instead of streaming from the mount. Slower start (one
    # full copy) but isolates compression from network instability.
    stage_locally: bool = False

    def __post_init__(self):
        if self.mode == "jetraw" and not self.jetraw_path:
            raise ValueError("source.jetraw_path is required when mode=jetraw")
        if self.mode == "plain" and not self.nas_path:
            raise ValueError("source.nas_path is required when mode=plain")
        if self.stage_locally and self.mode != "plain":
            raise ValueError("source.stage_locally only applies in plain mode")


@dataclass
class RcloneCfg:
    mount_path: Path
    remote: str | None = None


@dataclass
class JetrawCfg:
    destination: str | None = None
    set_url: bool = True
    verify_checksum: bool = True
    verify_checksum_flag: str = "--verify-checksum-after-download-before-decompression"


@dataclass
class StagingCfg:
    root: Path


@dataclass
class CompressionCfg:
    level: int = 9
    threads: int = 0
    granularity: Granularity = "subfolder"
    # For granularity=auto: target archive size; the planner aims for archives
    # at or below max_size_mb, bundling small adjacent subtrees up to target.
    target_size_mb: int = 1024   # 1 GB
    max_size_mb: int = 4096      # 4 GB


@dataclass
class MirrorCfg:
    per_file_checksum: bool = False


@dataclass
class TapeCfg:
    mount_path: Path


@dataclass
class Config:
    source: SourceCfg
    rclone: RcloneCfg
    staging: StagingCfg
    tape: TapeCfg
    jetraw: JetrawCfg = field(default_factory=JetrawCfg)
    compression: CompressionCfg = field(default_factory=CompressionCfg)
    mirror: MirrorCfg = field(default_factory=MirrorCfg)

    @property
    def download_dir(self) -> Path:
        return self.staging.root / "data" / self.source.name

    @property
    def nas_source_path(self) -> Path:
        """Original path on the rclone-mounted NAS (plain mode only)."""
        assert self.source.mode == "plain" and self.source.nas_path is not None
        p = Path(self.source.nas_path)
        return p if p.is_absolute() else (self.rclone.mount_path / p)

    @property
    def source_data_dir(self) -> Path:
        """The directory mirror/compress read from."""
        if self.source.mode == "jetraw":
            return self.download_dir
        if self.source.stage_locally:
            return self.download_dir
        return self.nas_source_path

    @property
    def mirror_dir(self) -> Path:
        return self.staging.root / "mirror" / self.source.name

    @property
    def archive_dir(self) -> Path:
        return self.staging.root / "archives" / self.source.name

    @property
    def tape_target_dir(self) -> Path:
        return self.tape.mount_path / self.source.name


def load_config(path: str | Path) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Config(
        source=SourceCfg(**raw["source"]),
        rclone=RcloneCfg(
            mount_path=Path(raw["rclone"]["mount_path"]),
            remote=raw["rclone"].get("remote"),
        ),
        jetraw=JetrawCfg(**raw.get("jetraw", {})),
        staging=StagingCfg(root=Path(raw["staging"]["root"])),
        tape=TapeCfg(mount_path=Path(raw["tape"]["mount_path"])),
        compression=CompressionCfg(**raw.get("compression", {})),
        mirror=MirrorCfg(**raw.get("mirror", {})),
    )
