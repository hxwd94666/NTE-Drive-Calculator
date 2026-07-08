# 管理扫描截图命名、筛选和归档准备。
"""Screenshot file lifecycle helpers for scan/parse workflows.

This module owns the file-level rules around screenshot selection, incremental
probe comparison, failed screenshot quarantine, and incremental screenshot
renaming. Keeping this outside the main window makes the scan lifecycle easier
to review and rollback independently from UI changes.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src.utils.logger import logger
from src.utils.image_io import imread_unicode
from src.features.scanning.naming import (
    FAILED_SCREENSHOT_DIRNAME,
    FULL_SCAN_TEMP_DIRNAME,
    IMAGE_EXTS,
    PROBE_PREFIX,
    full_scan_filename,
    is_full_scan_filename,
    is_incremental_filename,
    raw_drive_index_from_name,
)


@dataclass
class IncrementalParsePreparation:
    """Result from preparing an incremental parse run."""

    skip_names: set[str] = field(default_factory=set)
    delete_after_parse: list[str] = field(default_factory=list)
    probe_duplicate_count: int = 0
    baseline_missing: bool = False


@dataclass
class ScreenshotUsage:
    """Current managed screenshot/temp-file usage for one account."""

    files: list[Path] = field(default_factory=list)
    count: int = 0
    size_mb: float = 0.0


@dataclass
class ScreenshotCleanupPlan:
    """Files that are safe to remove from one account according to lifecycle rules."""

    delete_files: list[Path] = field(default_factory=list)
    preserved_files: list[Path] = field(default_factory=list)
    scan_delete_count: int = 0
    temp_delete_count: int = 0
    baseline_missing: bool = False

    @property
    def total_count(self) -> int:
        return len(self.delete_files)

    def confirmation_text(self) -> str:
        prompt = (
            "由于增量逻辑设定，需要保留一张 raw_drive_0001.png，请勿删除。\n\n"
            f"将删除 {self.scan_delete_count} 个其它扫描截图、"
            f"{self.temp_delete_count} 个临时鉴定粘贴截图，不可恢复。"
        )
        if self.baseline_missing:
            prompt += "\n\n注意：丢失用于对比的截图，请重新全量扫描，或不要使用全自动增量扫描。"
        return prompt


@dataclass
class ScreenshotCleanupResult:
    """Result after executing a screenshot cleanup plan."""

    deleted: int = 0
    failed_files: list[Path] = field(default_factory=list)


def iter_image_files(path: Path) -> list[Path]:
    """Return all image files below a directory, including subdirectories."""

    if not path.exists():
        return []
    return [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXTS]


def iter_root_image_files(path: Path) -> list[Path]:
    """Return only image files directly under a directory."""

    if not path.exists():
        return []
    return [f for f in path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]


def raw_drive_index(path: Path) -> int | None:
    """Extract the numeric index from names like raw_drive_0001.png."""

    return raw_drive_index_from_name(path)


def is_allowed_filename(filename: str, parse_scope: str, skip_names: Iterable[str] | None = None) -> bool:
    """Check whether a filename belongs to the selected parse scope."""

    if filename in set(skip_names or []):
        return False
    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in IMAGE_EXTS:
        return False
    if parse_scope == "incremental":
        return is_incremental_filename(filename, parse_scope)
    if parse_scope == "full":
        return is_full_scan_filename(filename)
    if parse_scope in ("incremental_auto", "incremental_semi"):
        return is_incremental_filename(filename, parse_scope)
    return True


def is_scope_image(path: Path, parse_scope: str, skip_names: Iterable[str] | None = None) -> bool:
    """Check whether a root screenshot path belongs to the selected parse scope."""

    if path.name in set(skip_names or []):
        return False
    if path.suffix.lower() not in IMAGE_EXTS or not path.is_file():
        return False
    return is_allowed_filename(path.name, parse_scope, skip_names=None)


def equipment_compare_signature(item) -> tuple:
    """Build a stable OCR-data signature for probe/baseline comparison."""

    item_type = getattr(item, "item_type", "")
    quality = getattr(item, "quality", "")
    sub_stats = tuple((str(k), float(v)) for k, v in (getattr(item, "sub_stats", {}) or {}).items())
    if item_type == "drive":
        main_stats = tuple((str(k), float(v)) for k, v in (getattr(item, "main_stats", {}) or {}).items())
        return ("drive", quality, getattr(item, "shape_id", ""), main_stats, sub_stats)
    if item_type == "tape":
        return ("tape", quality, getattr(item, "set_name", ""), str(getattr(item, "main_stats", "")), sub_stats)
    return (item_type, quality, sub_stats)


def _iter_account_temp_images(account_root: Path) -> list[Path]:
    """Return app-generated temporary images in the account root."""

    from src.features.identification.temp_files import iter_identify_clipboard_files

    return iter_identify_clipboard_files(Path(account_root))


def managed_screenshot_usage(screenshot_dir: Path, account_root: Path) -> ScreenshotUsage:
    """Return files and disk usage managed by the screenshot lifecycle policy."""

    files = iter_image_files(Path(screenshot_dir)) + _iter_account_temp_images(Path(account_root))
    size_mb = sum(f.stat().st_size for f in files if f.exists()) / (1024 * 1024) if files else 0.0
    return ScreenshotUsage(files=files, count=len(files), size_mb=size_mb)


def build_screenshot_cleanup_plan(screenshot_dir: Path, account_root: Path) -> ScreenshotCleanupPlan:
    """Build a cleanup plan while preserving files required by incremental scanning."""

    screenshot_dir = Path(screenshot_dir)
    scan_files = iter_image_files(screenshot_dir)
    temp_files = _iter_account_temp_images(Path(account_root))
    baseline = screenshot_dir / "raw_drive_0001.png"
    baseline_resolved = baseline.resolve()
    delete_scan_files = []
    preserved_files = []
    for path in scan_files:
        if path.resolve() == baseline_resolved:
            preserved_files.append(path)
        else:
            delete_scan_files.append(path)
    return ScreenshotCleanupPlan(
        delete_files=delete_scan_files + temp_files,
        preserved_files=preserved_files,
        scan_delete_count=len(delete_scan_files),
        temp_delete_count=len(temp_files),
        baseline_missing=bool(scan_files) and not baseline.exists(),
    )


def execute_screenshot_cleanup(plan: ScreenshotCleanupPlan) -> ScreenshotCleanupResult:
    """Delete files from a cleanup plan and log any failures."""

    result = ScreenshotCleanupResult()
    for path in plan.delete_files:
        try:
            path.unlink()
            result.deleted += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.failed_files.append(path)
            logger.warning(f"删除截图文件失败: {path} ({exc})")
    return result


class ScanFileLifecycle:
    """Coordinate screenshot file operations after OCR parsing finishes."""

    def __init__(
        self,
        screenshot_dir: Path,
        output_file: Path,
        config_dir: Path,
        batch_processor_cls=None,
    ):
        self.screenshot_dir = Path(screenshot_dir)
        self.output_file = Path(output_file)
        self.config_dir = Path(config_dir)
        if batch_processor_cls is None:
            from src.scanner.batch_processor import BatchProcessor

            batch_processor_cls = BatchProcessor
        self.batch_processor_cls = batch_processor_cls

    def same_equipment_by_ocr(self, left: Path, right: Path) -> bool:
        """Compare two screenshots by parsed equipment data, not image pixels."""

        try:
            processor = self.batch_processor_cls(
                input_dir=str(self.screenshot_dir),
                output_file=str(self.output_file),
                config_dir=str(self.config_dir),
                replace_output=False,
            )
            left_item = processor._process_single_image(str(left))
            right_item = processor._process_single_image(str(right))
            left_sig = equipment_compare_signature(left_item)
            right_sig = equipment_compare_signature(right_item)
            same = left_sig == right_sig
            logger.info(
                f"增量首图 OCR 数据比对: {'一致' if same else '不一致'} | {Path(left).name} vs {Path(right).name}"
            )
            if not same:
                logger.debug(f"probe={left_sig} baseline={right_sig}")
            return same
        except Exception as exc:
            logger.warning(f"增量首图 OCR 数据比对失败，将保留 probe 进入正常解析: {exc}")
            return False

    def prepare_incremental_parse(self, parse_scope: str) -> IncrementalParsePreparation:
        """Prepare duplicate-probe skipping for incremental parse scopes."""

        result = IncrementalParsePreparation()
        if parse_scope not in ("incremental", "incremental_auto"):
            return result
        probes = sorted(
            [p for p in iter_root_image_files(self.screenshot_dir) if p.stem.startswith(PROBE_PREFIX)]
        )
        if not probes:
            return result

        baseline = self.screenshot_dir / "raw_drive_0001.png"
        if not baseline.exists():
            result.baseline_missing = True
            return result
        if imread_unicode(baseline) is None:
            result.baseline_missing = True
            logger.warning(f"增量扫描基准图不可读取，请重新全量扫描: {baseline}")
            return result

        first_probe = probes[0]
        if self.same_equipment_by_ocr(first_probe, baseline):
            result.skip_names.add(first_probe.name)
            result.delete_after_parse.append(str(first_probe))
            result.probe_duplicate_count = 1
            logger.info(f"增量首图与 raw_drive_0001 一致，解析完成后删除 {first_probe.name}")
        return result

    def matching_scope_files(self, parse_scope: str, skip_names: Iterable[str] | None = None) -> list[Path]:
        """List root screenshots that should be parsed for the selected scope."""

        return sorted(
            [p for p in iter_root_image_files(self.screenshot_dir) if is_scope_image(p, parse_scope, skip_names)],
            key=lambda p: p.name,
        )

    def unique_path(self, directory: Path, name: str) -> Path:
        """Return a non-existing target path inside a directory."""

        directory.mkdir(parents=True, exist_ok=True)
        candidate = directory / name
        base = candidate.with_suffix("")
        ext = candidate.suffix
        suffix = 1
        while candidate.exists():
            candidate = Path(f"{base}_{suffix}{ext}")
            suffix += 1
        return candidate

    def move_to_failed(self, paths: Iterable[str | Path]) -> int:
        """Move failed screenshots to the failed quarantine directory."""

        failed_dir = self.screenshot_dir / FAILED_SCREENSHOT_DIRNAME
        moved = 0
        for src in paths:
            src_path = Path(src)
            if not src_path.exists():
                continue
            dst = self.unique_path(failed_dir, src_path.name)
            try:
                shutil.move(str(src_path), str(dst))
                moved += 1
            except Exception as exc:
                logger.error(f"移动失败截图失败: {src_path.name} | {exc}")
        return moved

    def delete_paths(self, paths: Iterable[str | Path]) -> int:
        """Delete duplicate screenshots after parsing has completed."""

        deleted = 0
        for src in paths:
            src_path = Path(src)
            if not src_path.exists():
                continue
            try:
                src_path.unlink()
                deleted += 1
            except Exception as exc:
                logger.error(f"删除重复截图失败: {src_path.name} | {exc}")
        return deleted

    def next_full_scan_index(self) -> int:
        """Return the next available raw_drive sequence index."""

        indexes = [
            idx for idx in (raw_drive_index(p) for p in iter_root_image_files(self.screenshot_dir)) if idx is not None
        ]
        return (max(indexes) if indexes else 0) + 1

    def rename_incremental_successes(self, paths: Iterable[str | Path]) -> int:
        """Append successfully parsed incremental screenshots to the full sequence."""

        next_index = self.next_full_scan_index()
        renamed = 0
        for src in paths:
            src_path = Path(src)
            if not src_path.exists():
                continue
            target = self.screenshot_dir / full_scan_filename(next_index, src_path.suffix.lower() or ".png")
            while target.exists():
                next_index += 1
                target = self.screenshot_dir / full_scan_filename(next_index, src_path.suffix.lower() or ".png")
            try:
                src_path.rename(target)
                renamed += 1
                next_index += 1
            except Exception as exc:
                logger.error(f"增量截图重命名失败: {src_path.name} | {exc}")
        return renamed

    def move_first_full_scan_to_tail(self) -> bool:
        """Move raw_drive_0001 to the current tail before replacing it with a new probe."""

        first_candidates = [p for p in iter_root_image_files(self.screenshot_dir) if p.stem == "raw_drive_0001"]
        if not first_candidates:
            return False
        first_path = first_candidates[0]
        next_index = self.next_full_scan_index()
        target = self.screenshot_dir / full_scan_filename(next_index, first_path.suffix.lower() or ".png")
        while target.exists():
            next_index += 1
            target = self.screenshot_dir / full_scan_filename(next_index, first_path.suffix.lower() or ".png")
        first_path.rename(target)
        logger.info(f"全自动增量首图插队：旧 raw_drive_0001 已移动到 {target.name}")
        return True

    def postprocess_vision_files(
        self,
        stats: dict,
        delete_after_parse: Iterable[str | Path] | None = None,
        probe_duplicate_count: int = 0,
    ) -> dict:
        """Apply all screenshot file mutations after a parse run succeeds."""

        scope = stats.get("parse_scope") or "all"
        failed_paths = list(stats.get("failed_paths") or [])
        duplicate_paths = list(stats.get("duplicate_paths") or [])
        added_paths = list(stats.get("added_paths") or [])
        moved_failed = self.move_to_failed(failed_paths)
        deleted_duplicates = self.delete_paths(list(duplicate_paths) + list(delete_after_parse or []))

        renamed = 0
        if scope in ("incremental", "incremental_auto", "incremental_semi"):
            probe_success = []
            if scope == "incremental_auto":
                probe_success = [
                    p for p in added_paths if Path(p).stem.startswith(PROBE_PREFIX) and Path(p).exists()
                ]
            if probe_success:
                probe_path = Path(probe_success[0])
                self.move_first_full_scan_to_tail()
                target = self.screenshot_dir / full_scan_filename(1, probe_path.suffix.lower() or ".png")
                probe_path.rename(target)
                renamed += 1
                added_paths = [p for p in added_paths if Path(p) != probe_path]
            renamed += self.rename_incremental_successes(added_paths)

        return {
            "moved_failed": moved_failed,
            "deleted_duplicates": deleted_duplicates,
            "renamed": renamed,
            "probe_duplicates": probe_duplicate_count,
        }
