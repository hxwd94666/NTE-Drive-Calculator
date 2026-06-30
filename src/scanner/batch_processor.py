# 批量处理截图并生成库存数据。
"""Offline screenshot parser that turns scanned images into inventory items."""

import os
import time
import shutil

from src.scanner.shape_recognizer import ShapeRecognizer
from src.scanner.ocr_engine import OCREngine
from src.scanner.parser import DriveDataParser
from src.features.inventory_import.duplicate_filter import (
    are_named_neighbors,
    filename_sequence_key,
    image_fingerprint,
    is_inventory_probe_filename,
    is_probe_first_new_pair,
    is_same_capture,
    process_image_file as process_image_file_helper,
)
from src.features.inventory_import.equipment_classifier import (
    classify_item as classify_item_helper,
    locate_shape_in_image,
    looks_like_drive_identity,
    looks_like_tape_identity,
)
from src.features.identification.parser import (
    box_intersects,
    cluster_identify_lines,
    dedupe_identify_items,
    identify_stat_texts,
    is_identify_stat_candidate,
    is_valid_identify_item,
    item_signature,
    item_signature_from_dict,
    load_existing_inventory_signatures,
    normalized_signature_data,
    parse_identify_items as parse_identify_items_helper,
    process_identify_standard_forced,
    synthesize_identify_cluster,
)
from src.features.inventory_import.exporter import export_inventory, make_unique_uid
from src.features.inventory_import.screenshot_parser import process_single_image

# 引入基座定义的全局日志与异常类
from src.utils.logger import logger
from src.utils.exceptions import InventoryEmptyError


class BatchProcessor:
    """全自动离线批处理管线，支持增量归档"""
    DRIVE_TYPE_CONFIDENCE = 0.86

    def __init__(self, input_dir: str = "scanned_images", output_file: str = "config/real_inventory.json", config_dir: str = "config", replace_output: bool = False):
        self.input_dir = input_dir
        self.output_file = output_file
        self.replace_output = replace_output

        # 归档文件夹（已解析的图片移至此处）
        self.archive_dir = os.path.join(self.input_dir, "archive")
        os.makedirs(self.archive_dir, exist_ok=True)

        logger.info("=" * 60)
        logger.info("离线批处理管线启动")
        logger.info("=" * 60)

        self.shape_recognizer = ShapeRecognizer(template_dir=os.path.join(config_dir, "templates"))
        self.ocr_engine = OCREngine()
        self.parser = DriveDataParser(config_dir=config_dir)
        self.inventory = []
        self.successful_image_paths = []
        self._last_parsed_filename = None
        self._last_parsed_signature = None
        self._last_parsed_image_fingerprint = None
        self._existing_inventory_signatures = None

    def process_all(self, filter_adjacent_duplicates: bool = False):
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
            return

        logger.info(f"\n发现 {total_files} 张未处理截图，开始解析...\n")
        start_time = time.time()
        success_count = 0
        duplicate_count = 0

        for idx, filename in enumerate(image_files, 1):
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

            except Exception as e:
                logger.error(f"[{idx:04d}/{total_files:04d}] 解析失败: {filename} | 错误: {str(e)}\n")

        if success_count > 0:
            self._export_to_json()

        cost_time = time.time() - start_time
        avg_time = cost_time / total_files if total_files else 0
        logger.success("=" * 60)
        if filter_adjacent_duplicates:
            logger.success(f"解析完成。本次入库 {success_count} 个装备，过滤疑似连拍重复 {duplicate_count} 个。")
        else:
            logger.success(f"解析完成。本次入库 {success_count} 个装备。")
        logger.success(f"总耗时: {cost_time:.2f} 秒 (平均 {avg_time:.2f} 秒/张)")
        logger.success("=" * 60)

    def archive_processed_images(self, image_paths=None) -> int:
        """Move successfully parsed screenshots into archive after allocation is saved."""
        paths = list(image_paths if image_paths is not None else self.successful_image_paths)
        archived_count = 0
        for file_path in paths:
            if not os.path.exists(file_path):
                continue
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
        self.successful_image_paths.append(image_path)

    def _image_fingerprint(self, image_path: str):
        return image_fingerprint(image_path)

    def _is_same_capture(self, previous, current) -> bool:
        return is_same_capture(previous, current)

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
        return parse_identify_items_helper(
            self,
            image_path,
            max_items=max_items,
            forced_type=forced_type,
            forced_shape_id=forced_shape_id,
            forced_set_name=forced_set_name,
            forced_main_stat=forced_main_stat,
        )

    def _is_valid_identify_item(self, item) -> bool:
        return is_valid_identify_item(item)

    def _process_identify_standard_forced(
        self,
        img,
        forced_type: str,
        forced_shape_id: str | None = None,
        forced_set_name: str | None = None,
        forced_main_stat: str | None = None,
    ):
        return process_identify_standard_forced(
            self,
            img,
            forced_type=forced_type,
            forced_shape_id=forced_shape_id,
            forced_set_name=forced_set_name,
            forced_main_stat=forced_main_stat,
        )

    def _item_signature(self, item_data) -> str:
        return item_signature(self, item_data)

    def _item_signature_from_dict(self, item_data: dict) -> str:
        return item_signature_from_dict(item_data)

    def _normalized_signature_data(self, item_data: dict) -> dict:
        return normalized_signature_data(item_data)

    def _load_existing_inventory_signatures(self) -> set[str]:
        return load_existing_inventory_signatures(self)

    def _is_inventory_probe_filename(self, filename: str | None) -> bool:
        return is_inventory_probe_filename(filename)

    def _cluster_identify_lines(self, lines: list[dict], image_shape: tuple[int, int]) -> list[list[dict]]:
        return cluster_identify_lines(lines, image_shape)

    def _box_intersects(self, box, region) -> bool:
        return box_intersects(box, region)

    def _synthesize_identify_cluster(
        self,
        img,
        lines: list[dict],
        forced_type: str | None = None,
        forced_shape_id: str | None = None,
        forced_set_name: str | None = None,
        forced_main_stat: str | None = None,
    ):
        return synthesize_identify_cluster(
            self,
            img,
            lines,
            forced_type=forced_type,
            forced_shape_id=forced_shape_id,
            forced_set_name=forced_set_name,
            forced_main_stat=forced_main_stat,
        )

    def _identify_stat_texts(self, lines: list[dict], forced_type: str | None = None) -> list[str]:
        return identify_stat_texts(lines, forced_type=forced_type)

    def _is_identify_stat_candidate(self, text: str) -> bool:
        return is_identify_stat_candidate(text)

    def _locate_shape_in_image(self, img, region=None) -> dict:
        return locate_shape_in_image(self.shape_recognizer, img, region)

    def _dedupe_identify_items(self, items: list) -> list:
        return dedupe_identify_items(self, items)

    def _filename_sequence_key(self, filename: str | None):
        return filename_sequence_key(filename)

    def _are_named_neighbors(self, previous_filename: str | None, current_filename: str | None) -> bool:
        return are_named_neighbors(previous_filename, current_filename)

    def _is_probe_first_new_pair(self, previous_filename: str | None, current_filename: str | None) -> bool:
        return is_probe_first_new_pair(previous_filename, current_filename)

    def _process_single_image(self, image_path: str):
        return process_single_image(self, image_path)

    def _classify_item(self, img, region_profiles):
        return classify_item_helper(self, img, region_profiles)

    def _looks_like_drive_identity(self, text: str) -> bool:
        return looks_like_drive_identity(text)

    def _looks_like_tape_identity(self, text: str) -> bool:
        return looks_like_tape_identity(self.parser, text)

    def _export_to_json(self):
        return export_inventory(self)

    def _make_unique_uid(self, uid: str, existing_uids: set) -> str:
        return make_unique_uid(uid, existing_uids)
