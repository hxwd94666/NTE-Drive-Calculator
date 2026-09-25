# 为安装包复用内容相同的图鉴图片，同时保留清单内的独立图片路径。
"""Prepare an Inno staging tree with duplicate role-catalog images omitted."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class InstallerAssetCopy:
    source_relative: Path
    destination_relative: Path
    sha256: str
    size_bytes: int


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_installer_asset_stage(
    source_internal: Path,
    stage_internal: Path,
    workspace_root: Path,
) -> list[InstallerAssetCopy]:
    """Copy a validated app bundle, omitting only byte-identical catalog images.

    Inno references the retained catalog image a second time at the omitted
    destination. Its installer owns both installed paths, so upgrades and
    uninstall continue to use the normal [Files] lifecycle.
    """
    workspace = workspace_root.resolve(strict=True)
    source = source_internal.resolve(strict=True)
    expected = workspace / "build" / "installer-stage" / "_internal"
    stage = stage_internal.resolve() if stage_internal.exists() else stage_internal.absolute()
    if (
        stage != expected
        or stage_internal.parent.resolve() != expected.parent
        or not source.is_relative_to(workspace)
    ):
        raise ValueError("安装包暂存目录必须位于指定工作区 build/installer-stage/_internal")
    if stage.exists():
        if stage.is_symlink() or not stage.resolve().is_relative_to(workspace / "build"):
            raise ValueError("安装包暂存目录路径不安全")
        shutil.rmtree(stage)
    stage.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, stage)

    catalog_root = stage / "data" / "role_catalog" / "game_ui"
    images_by_digest: dict[str, Path] = {}

    copies: list[InstallerAssetCopy] = []
    for image in sorted(catalog_root.rglob("*")):
        if not image.is_file() or image.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        digest = _digest(image)
        original = images_by_digest.get(digest)
        if original is None:
            images_by_digest[digest] = image
            continue
        copies.append(
            InstallerAssetCopy(
                source_relative=original.relative_to(stage),
                destination_relative=image.relative_to(stage),
                sha256=digest,
                size_bytes=image.stat().st_size,
            )
        )
        image.unlink()

    validate_installer_asset_stage(source, stage, copies)
    return copies


def validate_installer_asset_stage(
    source_internal: Path,
    stage_internal: Path,
    copies: list[InstallerAssetCopy],
) -> None:
    """Prove every source file remains installable at its original path."""
    source = source_internal.resolve(strict=True)
    stage = stage_internal.resolve(strict=True)
    mapped = {copy.destination_relative: copy for copy in copies}
    if len(mapped) != len(copies):
        raise RuntimeError("安装资源去重目标路径重复")
    for original in source.rglob("*"):
        if not original.is_file():
            continue
        relative = original.relative_to(source)
        staged = stage / relative
        mapping = mapped.get(relative)
        if mapping is None:
            if not staged.is_file():
                raise RuntimeError(f"安装暂存缺失发行文件：{relative}")
            if staged.stat().st_size != original.stat().st_size or _digest(staged) != _digest(original):
                raise RuntimeError(f"安装暂存文件哈希不匹配：{relative}")
            continue
        donor = stage / mapping.source_relative
        if staged.exists() or not donor.is_file():
            raise RuntimeError(f"安装资源去重路径不完整：{relative}")
        if original.stat().st_size != mapping.size_bytes or _digest(original) != mapping.sha256:
            raise RuntimeError(f"安装资源原图哈希变化：{relative}")
        if _digest(donor) != mapping.sha256:
            raise RuntimeError(f"安装资源复用图哈希不匹配：{mapping.source_relative}")


def inno_asset_copy_lines(stage_internal: Path, copies: list[InstallerAssetCopy]) -> str:
    """Use one source payload for two Inno-managed installed file entries."""
    lines = []
    for copy in copies:
        source = stage_internal / copy.source_relative
        dest = copy.destination_relative
        if any('"' in str(value) or ";" in str(value) for value in (source, dest)):
            raise ValueError("安装资源路径包含 Inno 脚本分隔符")
        parent = str(dest.parent).replace("/", "\\")
        lines.append(
            f'Source: "{source}"; DestDir: "{{app}}\\_internal\\{parent}"; '
            f'DestName: "{dest.name}"; Flags: ignoreversion'
        )
    return "\n".join(lines)
