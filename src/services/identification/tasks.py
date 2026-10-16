# 冻结鉴定批处理输入，后台解析与评分不访问页面或当前账号。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.integrations.vision.recognition.resources import RecognitionResources
from src.services.recognition.resources import load_recognition_resources, check_recognition_resources
from src.models.equipment import Drive, Tape
from src.services.identification.equipment_service import EquipmentIdentificationService
from src.services.identification.matching_settings import MatchingSettingsService


@dataclass(frozen=True)
class IdentificationImageJob:
    path: Path
    forced_type: str | None = None
    forced_shape_id: str | None = None
    forced_set_name: str | None = None
    forced_main_stat: str | None = None

    @classmethod
    def from_options(cls, path: Path, options: Mapping[str, Any]) -> IdentificationImageJob:
        return cls(path, options.get("type"), options.get("shape_id"),
                   options.get("set_name"), options.get("main_stat"))


@dataclass(frozen=True)
class IdentificationImageRequest:
    input_dir: Path
    output_file: Path
    config_dir: Path
    jobs: tuple[IdentificationImageJob, ...]
    static_database_path: Path | None = None


@dataclass(frozen=True)
class IdentificationParsedBatch:
    items: tuple[Drive | Tape, ...]
    resources: RecognitionResources | None


@dataclass(frozen=True)
class IdentificationMatchRequest:
    config_dir: Path
    user_database_path: Path
    static_database_path: Path
    items: tuple[Drive | Tape, ...]
    resources: RecognitionResources | None = None

    @classmethod
    def freeze(cls, *, config_dir: Path, user_database_path: Path,
               static_database_path: Path,
               items: Sequence[Drive | Tape],
               resources: RecognitionResources | None = None) -> IdentificationMatchRequest:
        # 卡带匹配会归一化套装名；后台只修改请求私有副本，不碰表单对象。
        return cls(config_dir, user_database_path, static_database_path,
                   tuple(item.model_copy(deep=True) for item in items), resources)


def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise CancelledError("装备鉴定已取消")


def parse_identification_images(
    request: IdentificationImageRequest, *,
    cancel_check: Callable[[], bool] | None = None,
) -> IdentificationParsedBatch:
    _check_cancel(cancel_check)
    if not request.jobs:
        return IdentificationParsedBatch((), None)
    # 仅图片解析才加载 OCR；手工与仓库鉴定不因导入服务初始化推理后端。
    from src.integrations.vision.recognition.batch_processor import BatchProcessor

    resources = load_recognition_resources(request.config_dir, request.static_database_path)
    _check_cancel(cancel_check)
    processor = BatchProcessor(input_dir=str(request.input_dir),
                               output_file=str(request.output_file),
                               config_dir=str(request.config_dir), resources=resources,
                               cancel_check=cancel_check)
    items = []
    for job in request.jobs:
        _check_cancel(cancel_check)
        items.extend(processor.parse_identify_items(
            str(job.path), forced_type=job.forced_type,
            forced_shape_id=job.forced_shape_id, forced_set_name=job.forced_set_name,
            forced_main_stat=job.forced_main_stat,
        ))
    _check_cancel(cancel_check)
    # 图片任务消费处理器实际解码的模板与布局，确认/匹配阶段不丢失图片身份。
    resources = processor.resources
    check_recognition_resources(resources)
    return IdentificationParsedBatch(tuple(items), resources)


def match_identification_items(
    request: IdentificationMatchRequest, *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    _check_cancel(cancel_check)
    if not request.items:
        return []
    # 每批只构造一次图纸/匹配器，不在每件装备上重复读库或初始化评分。
    resources = request.resources or load_recognition_resources(request.config_dir, request.static_database_path)
    check_recognition_resources(resources)
    matcher = EquipmentIdentificationService.from_paths(
        config_dir=request.config_dir, user_database_path=request.user_database_path,
        static_database_path=request.static_database_path, cancel_check=cancel_check, resources=resources,
    )
    results = matcher.identify_items(request.items, checkpoint=lambda: _check_cancel(cancel_check))
    _check_cancel(cancel_check)
    check_recognition_resources(resources)
    MatchingSettingsService(request.user_database_path).require_identity(matcher.context.settings_identity)
    return results
