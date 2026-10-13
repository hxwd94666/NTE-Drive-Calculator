# 批量处理截图并生成库存数据。
"""Offline screenshot parser that turns scanned images into inventory items."""

from concurrent.futures import CancelledError
from dataclasses import replace

import os
import time
import shutil
from pathlib import Path

from src.integrations.vision.recognition.backend_runtime import CancelCheck, check_cancel
from src.integrations.vision.recognition.config import ScannerConfig
from src.integrations.vision.recognition.language_resources import OcrRuntimeResources
from src.integrations.vision.recognition.model_resources import OcrModelResourceError
from src.integrations.vision.recognition.image_resources import RecognitionImageProfile, CaptureTemplateFile
from src.integrations.vision.recognition.resources import RecognitionResources
from src.integrations.vision.recognition.shape_recognizer import ShapeRecognizer
from src.integrations.vision.recognition.ocr_engine import OCREngine
from src.integrations.vision.recognition.parser import DriveDataParser
from src.integrations.vision.recognition.duplicate_filter import (
    is_inventory_probe_filename,
    process_image_file as process_image_file_helper,
)
from src.integrations.vision.recognition.identification_parser import (
    item_signature,
    load_existing_inventory_signatures,
    parse_identify_items as parse_identify_items_helper,
)
from src.integrations.vision.recognition.screenshot_parser import process_single_image

# 引入基座定义的全局日志与异常类
from src.utils.logger import logger
from src.utils.exceptions import InventoryEmptyError

class BatchProcessor:
    """全自动离线批处理管线，支持增量归档"""
    DRIVE_TYPE_CONFIDENCE = 0.86

    def __init__(
        self,
        input_dir: str = "scanned_images",
        output_file: str | None = None,
        config_dir: str | Path | None = None,
        replace_output: bool = False,
        ocr_backend_preference: str | None = None,
        *, resources: RecognitionResources, cancel_check: CancelCheck = None,
        capture_template: CaptureTemplateFile | None = None,
    ):
        self.cancel_check = cancel_check
        check_cancel(cancel_check)
        self.input_dir = input_dir
        self.output_file = output_file
        self.replace_output = replace_output
        self.resources = resources
        self.layout = ScannerConfig.freeze()
        resolved_config_dir = resources.config_dir
        if config_dir is not None and Path(config_dir).resolve() != resolved_config_dir:
            raise ValueError("识别配置路径与本批冻结资料不一致。")

        # 归档文件夹（已解析的图片移至此处）
        self.archive_dir = os.path.join(self.input_dir, "archive")

        logger.info("=" * 60)
        logger.info("离线批处理管线启动")
        logger.info("=" * 60)

        self.shape_recognizer = ShapeRecognizer(
            template_dir=os.path.join(resolved_config_dir, "templates"),
            valid_shape_ids=resources.shape_ids
        )
        # 全量/增量、离线/手柄扫描最终均通过本处理器解析截图；在这里统一
        # 校验模板，避免模板目录意外丢失时让大量驱动逐张识别失败。
        self.shape_recognizer.require_complete_templates()
        profile = RecognitionImageProfile(self.layout, self.shape_recognizer.template_files, capture_template=capture_template)
        profile.verify()
        # 一个批次共享一套 OCR、词库和模板；界面语言不隐式改变图片输入。
        check_cancel(cancel_check)
        self.ocr_engine = OCREngine(backend_preference=ocr_backend_preference, cancel_check=cancel_check)
        if not isinstance(self.ocr_engine.runtime_resources, OcrRuntimeResources):
            raise OcrModelResourceError("OCR 引擎缺少本批完整资源身份。")
        profile = replace(profile, ocr_resources=self.ocr_engine.runtime_resources)
        profile.verify()
        check_cancel(cancel_check)
        self.resources = replace(resources, image_profile=profile)
        self.parser = DriveDataParser(config_dir=resolved_config_dir,
            stat_catalog=resources.stat_catalog(), suit_names=[name for _id, name in resources.suits])
        check_cancel(cancel_check)
        self.inventory = []
        self.successful_image_paths = []
        self._last_parsed_filename = None
        self._last_parsed_signature = None
        self._last_parsed_image_fingerprint = None
        self._existing_inventory_signatures = None

    def process_all(self, filter_adjacent_duplicates: bool = False):
        check_cancel(self.cancel_check)
        if not os.path.exists(self.input_dir):
            raise InventoryEmptyError(f"找不到截图文件夹 {self.input_dir}，请先执行扫描！")

        image_files = [
            f for f in os.listdir(self.input_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
            and os.path.isfile(os.path.join(self.input_dir, f))
        ]
        image_files.sort()
        total_files = len(image_files)

        if total_files == 0:
            logger.info("收件箱为空，没有新的截图需要处理。")
            return {"total_count": 0, "success_count": 0, "failed_count": 0, "duplicate_count": 0, "parse_scope": "full"}

        logger.info(f"\n发现 {total_files} 张未处理截图，开始解析...\n")
        start_time = time.time()
        success_count = 0
        duplicate_count = 0

        for idx, filename in enumerate(image_files, 1):
            check_cancel(self.cancel_check)
            file_path = os.path.join(self.input_dir, filename)
            try:
                t1 = time.time()
                item_obj, added = self.process_image_file(
                    file_path,
                    filename,
                    filter_adjacent_duplicates=filter_adjacent_duplicates,
                )
                cost = time.time() - t1

                logger.info(f"[{idx:04d}/{total_files:04d}] 解析: {cost:.2f}s | {filename}")
                if not added:
                    duplicate_count += 1
                    logger.info("      > 增量重复截图已过滤\n")
                    continue

                if item_obj.item_type == "drive":
                    logger.info(f"      > [驱动] | 形状: {item_obj.shape_id.ljust(8)} | 品质: {item_obj.quality}")
                else:
                    logger.info(
                        f"      > [卡带] | 套装: {getattr(item_obj, 'set_name', '未知').ljust(8)} | 品质: {item_obj.quality}"
                    )

                logger.info(f"      > 主词条: {item_obj.main_stats}")
                logger.info(f"      > 副词条: {item_obj.sub_stats}\n")
                success_count += 1

            except (CancelledError, OcrModelResourceError):
                raise
            except Exception as e:
                logger.error(f"[{idx:04d}/{total_files:04d}] 解析失败: {filename} | 错误: {str(e)}\n")

        check_cancel(self.cancel_check)
        cost_time = time.time() - start_time
        avg_time = cost_time / total_files if total_files else 0
        logger.success("=" * 60)
        if filter_adjacent_duplicates:
            logger.success(f"解析完成。本次入库 {success_count} 个装备，过滤疑似连拍重复 {duplicate_count} 个。")
        else:
            logger.success(f"解析完成。本次入库 {success_count} 个装备。")
        logger.success(f"总耗时: {cost_time:.2f} 秒 (平均 {avg_time:.2f} 秒/张)")
        logger.success("=" * 60)
        # 捕获单图错误后的非空集合不等于完整库存；调用方必须消费这些统计。
        return {"total_count": total_files, "success_count": success_count,
                "failed_count": total_files - success_count - duplicate_count,
                "duplicate_count": duplicate_count, "parse_scope": "full"}

    def archive_processed_images(self, image_paths=None) -> int:
        """Move successfully parsed screenshots into archive after allocation is saved."""
        paths = list(image_paths if image_paths is not None else self.successful_image_paths)
        archived_count = 0
        for file_path in paths:
            if not os.path.exists(file_path):
                continue
            # 解析阶段不改输入目录结构，保存后的显式归档才创建目录。
            os.makedirs(self.archive_dir, exist_ok=True)
            filename = os.path.basename(file_path)
            archive_path = os.path.join(self.archive_dir, filename)
            base, ext = os.path.splitext(archive_path)
            suffix = 1
            while os.path.exists(archive_path):
                archive_path = f"{base}_{suffix}{ext}"
                suffix += 1
            shutil.move(file_path, archive_path)
            archived_count += 1
        if archived_count:
            logger.success(f"已归档 {archived_count} 张已保存配装的截图。")
        return archived_count

    def _mark_image_success(self, image_path: str) -> None:
        # 被去重的截图也算处理成功，保存完成后才允许归档。
        self.successful_image_paths.append(image_path)

    def process_image_file(
        self,
        image_path: str,
        filename: str | None = None,
        *,
        filter_adjacent_duplicates: bool = True,
    ):
        return process_image_file_helper(
            self,
            image_path,
            filename,
            filter_adjacent_duplicates=filter_adjacent_duplicates,
        )

    def parse_identify_items(
        self,
        image_path: str,
        max_items: int = 12,
        forced_type: str | None = None,
        forced_shape_id: str | None = None,
        forced_set_name: str | None = None,
        forced_main_stat: str | None = None,
    ):
        check_cancel(self.cancel_check)
        result = parse_identify_items_helper(
            self,
            image_path,
            max_items=max_items,
            forced_type=forced_type,
            forced_shape_id=forced_shape_id,
            forced_set_name=forced_set_name,
            forced_main_stat=forced_main_stat,
        )
        check_cancel(self.cancel_check)
        return result

    def _item_signature(self, item_data) -> str:
        return item_signature(self, item_data)

    def _load_existing_inventory_signatures(self) -> set[str]:
        return load_existing_inventory_signatures(self)

    def _is_inventory_probe_filename(self, filename: str | None) -> bool:
        return is_inventory_probe_filename(filename)

    def _process_single_image(self, image_path: str):
        # 保留解析入口的窄适配，供鉴定、增量首图及去重调用，避免模块反向导入。
        check_cancel(self.cancel_check)
        result = process_single_image(self, image_path)
        check_cancel(self.cancel_check)
        return result

