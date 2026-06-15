# 从截图中解析驱动盘和音擎属性。
"""Helpers for parsing a single screenshot into one equipment item."""

from __future__ import annotations

from src.scanner.config import ScannerConfig
from src.features.inventory_import.equipment_classifier import classify_item
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode
from src.utils.logger import logger


def process_single_image(processor, image_path: str):
    img = imread_unicode(image_path)
    if img is None:
        raise ValueError("图像损坏或无法读取")
    img = crop_window_border_from_image(img)

    height, width = img.shape[:2]
    region_profiles = ScannerConfig.get_region_profiles(target_width=width, target_height=height)
    item_type, profile_name, regions, shape_res, hub_joined_text = classify_item(processor, img, region_profiles)
    logger.debug(
        f"截图坐标方案: {profile_name} | 尺寸: {width}x{height} | "
        f"类型: {item_type} | 形状: {shape_res['shape_id']}({shape_res['confidence']}) | "
        f"身份文本: {hub_joined_text}"
    )

    if item_type == "drive":
        if shape_res["shape_id"] == "Unknown" or shape_res["confidence"] < 0.7:
            raise ValueError(f"形状识别置信度不足: {shape_res['confidence']}")

        sub_box = regions["drive_sub_stats"]
        sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]
        raw_sub_texts = processor.ocr_engine.extract_text(sub_crop)
        return processor.parser.synthesize_drive(shape_res["shape_id"], raw_sub_texts)

    set_name = processor.parser._fuzzy_match_set_name(hub_joined_text)
    main_box = regions["tape_main_stat"]
    sub_box = regions["tape_sub_stats"]

    main_crop = img[main_box[1]:main_box[3], main_box[0]:main_box[2]]
    sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]

    raw_main_texts = processor.ocr_engine.extract_text(main_crop)
    raw_sub_texts = processor.ocr_engine.extract_text(sub_crop)
    return processor.parser.synthesize_tape(set_name, raw_main_texts, raw_sub_texts)
