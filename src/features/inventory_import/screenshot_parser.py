# 从截图中解析驱动盘和卡带属性。
"""Helpers for parsing a single screenshot into one equipment item."""

from __future__ import annotations

import os
import time

from src.scanner.config import ScannerConfig
from src.features.identification.parser import _detect_tape_identity_from_lines
from src.features.inventory_import.equipment_classifier import classify_item, locate_selected_reward_shape
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode
from src.utils.logger import logger
from src.utils.perf import log_perf


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def process_single_image(processor, image_path: str):
    total_start = time.perf_counter()
    filename = os.path.basename(image_path)

    read_start = time.perf_counter()
    img = imread_unicode(image_path)
    if img is None:
        raise ValueError("图像损坏或无法读取")
    img = crop_window_border_from_image(img)
    read_ms = _elapsed_ms(read_start)

    height, width = img.shape[:2]
    region_profiles = ScannerConfig.get_region_profiles(target_width=width, target_height=height)

    classify_start = time.perf_counter()
    item_type, profile_name, regions, shape_res, hub_joined_text = classify_item(processor, img, region_profiles)
    classify_ms = _elapsed_ms(classify_start)
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
        ocr_start = time.perf_counter()
        raw_sub_texts = processor.ocr_engine.extract_text(sub_crop)
        detail_ocr_ms = _elapsed_ms(ocr_start)
        synth_start = time.perf_counter()
        item = processor.parser.synthesize_drive(shape_res["shape_id"], raw_sub_texts)
        synth_ms = _elapsed_ms(synth_start)
        log_perf(
            logger,
            "parse.image",
            elapsed_ms=_elapsed_ms(total_start),
            filename=filename,
            item_type="drive",
            read_ms=read_ms,
            classify_ms=classify_ms,
            detail_ocr_ms=detail_ocr_ms,
            synth_ms=synth_ms,
            ocr_calls=1 if shape_res.get("identity_skipped") else 2,
            profile=profile_name,
        )
        return _prefer_complete_or_reward_item(processor, img, item, filename)

    set_name = processor.parser._fuzzy_match_set_name(hub_joined_text)
    main_box = regions["tape_main_stat"]
    sub_box = regions["tape_sub_stats"]

    main_crop = img[main_box[1]:main_box[3], main_box[0]:main_box[2]]
    sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]

    main_ocr_start = time.perf_counter()
    raw_main_texts = processor.ocr_engine.extract_text(main_crop)
    main_ocr_ms = _elapsed_ms(main_ocr_start)
    sub_ocr_start = time.perf_counter()
    raw_sub_texts = processor.ocr_engine.extract_text(sub_crop)
    sub_ocr_ms = _elapsed_ms(sub_ocr_start)
    synth_start = time.perf_counter()
    item = processor.parser.synthesize_tape(set_name, raw_main_texts, raw_sub_texts)
    synth_ms = _elapsed_ms(synth_start)
    log_perf(
        logger,
        "parse.image",
        elapsed_ms=_elapsed_ms(total_start),
        filename=filename,
        item_type="tape",
        read_ms=read_ms,
        classify_ms=classify_ms,
        main_ocr_ms=main_ocr_ms,
        sub_ocr_ms=sub_ocr_ms,
        detail_ocr_ms=main_ocr_ms + sub_ocr_ms,
        synth_ms=synth_ms,
        ocr_calls=3,
        profile=profile_name,
    )
    return _prefer_complete_or_reward_item(processor, img, item, filename)


def _prefer_complete_or_reward_item(processor, img, item, filename: str):
    if _has_four_valid_sub_stats(processor, item):
        return item
    try:
        reward_item = process_reward_scene(processor, img, filename=filename)
        if _has_four_valid_sub_stats(processor, reward_item):
            return reward_item
    except Exception as exc:
        logger.debug(f"奖励场景解析未命中: {filename} | {exc}")
    return item


def _has_four_valid_sub_stats(processor, item) -> bool:
    sub_stats = getattr(item, "sub_stats", {}) or {}
    valid_stats = set(getattr(getattr(processor, "parser", None), "GOLD_BASE_VALUES", {}) or {})
    if not valid_stats:
        return len(sub_stats) >= 4
    return sum(1 for stat in sub_stats.keys() if stat in valid_stats) >= 4


def process_reward_scene(processor, img, filename: str = ""):
    lines = processor.ocr_engine.extract_lines(img)
    scene_type = _detect_reward_scene_type(processor, lines)
    if scene_type == "drive":
        return _synthesize_reward_drive(processor, img, lines)
    if scene_type == "tape":
        return _synthesize_reward_tape(processor, lines)
    raise ValueError("未识别到奖励场景")


def _detect_reward_scene_type(processor, lines: list[dict]) -> str | None:
    joined = "".join(str(line.get("text", "") or "") for line in lines)
    compact = joined.replace(" ", "")
    if "倒带获得" in compact or "型驱动" in compact:
        return "drive"
    if "卡带" in compact:
        return "tape"
    set_name, _main_stat = _detect_tape_identity_from_lines(processor, lines)
    if set_name:
        return "tape"
    return None


def _synthesize_reward_drive(processor, img, lines: list[dict]):
    shape = locate_selected_reward_shape(processor.shape_recognizer, img)
    if shape["shape_id"] == "Unknown" or shape["confidence"] < 0.62:
        raise ValueError(f"奖励驱动形状识别失败: {shape}")
    sub_texts = _reward_sub_stat_texts(processor, lines)
    if len(sub_texts) < 4:
        raise ValueError("奖励驱动副词条不足 4 条")
    return processor.parser.synthesize_drive(shape["shape_id"], sub_texts[:4])


def _synthesize_reward_tape(processor, lines: list[dict]):
    set_name, main_stat = _detect_tape_identity_from_lines(processor, lines)
    if not set_name:
        set_name = "未知套装"
    main_texts = [main_stat] if main_stat else [""]
    sub_texts = _reward_sub_stat_texts(processor, lines)
    if len(sub_texts) < 4:
        raise ValueError("奖励卡带副词条不足 4 条")
    item = processor.parser.synthesize_tape(set_name, main_texts, sub_texts[:4])
    if main_stat:
        item.main_stats = main_stat
    return item


def _reward_sub_stat_texts(processor, lines: list[dict]) -> list[str]:
    ordered = sorted(lines, key=lambda item: (item.get("box", (0, 0, 0, 0))[1], item.get("box", (0, 0, 0, 0))[0]))
    label_indexes = [
        index
        for index, line in enumerate(ordered)
        if "副属性" in str(line.get("text", "") or "").replace(" ", "")
    ]
    for label_index in label_indexes:
        texts = []
        for line in ordered[label_index + 1:]:
            text = str(line.get("text", "") or "").strip()
            if not text:
                continue
            if processor.parser._clean_stats([text]):
                texts.append(text)
                if len(texts) >= 4:
                    return texts
    return []
