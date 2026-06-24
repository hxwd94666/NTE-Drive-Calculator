# 解析手动输入文本中的装备属性。
"""Helpers for identify-mode OCR parsing and multi-item synthesis."""

from __future__ import annotations

import json
import os
import re

from src.scanner.config import ScannerConfig
from src.features.inventory_import.equipment_classifier import locate_shape_in_image, looks_like_drive_identity, looks_like_tape_identity
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode
from src.utils.logger import logger


def parse_identify_items(
    processor,
    image_path: str,
    max_items: int = 12,
    forced_type: str | None = None,
    forced_shape_id: str | None = None,
    forced_set_name: str | None = None,
    forced_main_stat: str | None = None,
):
    img = imread_unicode(image_path)
    if img is None:
        raise ValueError("图像损坏或无法读取")
    img = crop_window_border_from_image(img)

    items = []
    effective_forced_set_name = forced_set_name
    effective_forced_main_stat = forced_main_stat
    if forced_type is not None:
        try:
            item = process_identify_standard_forced(
                processor,
                img,
                forced_type=forced_type,
                forced_shape_id=forced_shape_id,
                forced_set_name=forced_set_name,
                forced_main_stat=forced_main_stat,
            )
            if is_valid_identify_item(item):
                items.append(item)
                return items
            effective_forced_set_name, effective_forced_main_stat = _carry_tape_identity_defaults(
                item,
                forced_set_name=effective_forced_set_name,
                forced_main_stat=effective_forced_main_stat,
            )
        except Exception as exc:
            logger.debug(f"标准区域强制鉴定解析未命中，切换整图识别: {exc}")
    else:
        try:
            item = processor._process_single_image(image_path)
            if item and item.sub_stats:
                items.append(item)
        except Exception as exc:
            logger.debug(f"标准区域鉴定解析未命中，切换整图识别: {exc}")

    try:
        lines = processor.ocr_engine.extract_lines(img)
        if forced_type is None and items and _looks_like_single_reward_scene(lines, img.shape[:2]):
            return dedupe_identify_items(processor, items)
        if forced_type == "tape":
            effective_forced_set_name, effective_forced_main_stat = _detect_tape_identity_from_lines(
                processor,
                lines,
                forced_set_name=effective_forced_set_name,
                forced_main_stat=effective_forced_main_stat,
            )
        for cluster in cluster_identify_lines(lines, img.shape[:2]):
            item = synthesize_identify_cluster(
                processor,
                img,
                cluster,
                forced_type=forced_type,
                forced_shape_id=forced_shape_id,
                forced_set_name=effective_forced_set_name,
                forced_main_stat=effective_forced_main_stat,
            )
            if is_valid_identify_item(item):
                items.append(item)
                if len(items) >= max_items:
                    break
    except Exception as exc:
        logger.debug(f"整图鉴定解析失败: {exc}")

    return items if forced_type is not None else dedupe_identify_items(processor, items)


def is_valid_identify_item(item) -> bool:
    if item is None or len(getattr(item, "sub_stats", {}) or {}) < 4:
        return False
    bad_keywords = ("附近", "最多", "持续", "每层", "每有", "受到", "造成", "装备者", "角色位于")
    for name in item.sub_stats.keys():
        if any(keyword in str(name) for keyword in bad_keywords):
            return False
    return True


def _is_known_text(value, unknown_keyword: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and unknown_keyword not in text


def _carry_tape_identity_defaults(item, forced_set_name=None, forced_main_stat=None):
    if item is None or getattr(item, "item_type", "") != "tape":
        return forced_set_name, forced_main_stat
    set_name = forced_set_name
    main_stat = forced_main_stat
    if not set_name and _is_known_text(getattr(item, "set_name", ""), "未知"):
        set_name = getattr(item, "set_name", None)
    return set_name, main_stat


def _detect_tape_identity_from_lines(processor, lines: list[dict], forced_set_name=None, forced_main_stat=None):
    set_name = forced_set_name
    main_stat = forced_main_stat
    ordered_lines = sorted(lines, key=lambda item: (item.get("box", (0, 0, 0, 0))[1], item.get("box", (0, 0, 0, 0))[0]))
    if not set_name:
        set_name = _find_set_name_from_lines(processor, ordered_lines)
    if not main_stat:
        main_stat = _find_tape_main_from_lines(processor, ordered_lines)
    return set_name, main_stat


def _find_set_name_from_lines(processor, lines: list[dict]) -> str | None:
    for line in lines:
        text = line.get("text", "")
        direct_match = _match_known_text(text, getattr(processor.parser, "REAL_SETS_WHITE_LIST", []))
        if direct_match:
            return direct_match
    for line in lines:
        resolved = processor.parser._fuzzy_match_set_name(line.get("text", ""))
        if resolved in getattr(processor.parser, "REAL_SETS_WHITE_LIST", []):
            return resolved
    return None


def _find_tape_main_from_lines(processor, lines: list[dict]) -> str | None:
    for index, line in enumerate(lines):
        if "\u4e3b\u5c5e\u6027" not in _chinese_only(line.get("text", "")):
            continue
        for nearby in lines[index + 1:index + 8]:
            matched = _match_tape_main_line(processor, nearby.get("text", ""), allow_fuzzy=True)
            if matched:
                return matched

    for line in lines:
        matched = _match_tape_main_line(processor, line.get("text", ""), allow_fuzzy=False)
        if matched:
            return matched
    return None


def _match_tape_main_line(processor, text: str, allow_fuzzy: bool = False) -> str | None:
    clean_text = _chinese_only(text)
    if "\u63d0\u5347" in clean_text or "\u589e\u52a0" in clean_text:
        return None
    direct_match = _match_known_text(text, getattr(processor.parser, "TAPE_MAIN_STATS_POOL", []))
    if direct_match:
        return direct_match
    if not allow_fuzzy:
        return None
    resolved = processor.parser._fuzzy_match_tape_main(text)
    if resolved in getattr(processor.parser, "TAPE_MAIN_STATS_POOL", []):
        return resolved
    return None


def _match_known_text(text: str, choices: list[str]) -> str | None:
    clean_text = _chinese_only(text)
    if not clean_text:
        return None
    for choice in choices:
        clean_choice = _chinese_only(choice)
        if clean_choice and clean_choice in clean_text:
            return choice
    return None


def _chinese_only(value: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5]", "", str(value or ""))


def _looks_like_single_reward_scene(lines: list[dict], image_shape: tuple[int, int]) -> bool:
    height, width = image_shape
    for line in lines:
        text = str(line.get("text", "") or "").replace(" ", "")
        if "倒带获得" in text:
            return True
        if "卡带" not in text:
            continue
        x1, y1, x2, y2 = line.get("box", (0, 0, 0, 0))
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        if width * 0.35 <= cx <= width * 0.65 and cy <= height * 0.25:
            return True
    return False


def process_identify_standard_forced(
    processor,
    img,
    forced_type: str,
    forced_shape_id: str | None = None,
    forced_set_name: str | None = None,
    forced_main_stat: str | None = None,
):
    height, width = img.shape[:2]
    region_profiles = ScannerConfig.get_region_profiles(target_width=width, target_height=height)
    _, regions = region_profiles[0]
    if forced_type == "drive":
        if not forced_shape_id:
            raise ValueError("未选择驱动形状")
        sub_box = regions["drive_sub_stats"]
        sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]
        raw_sub_texts = processor.ocr_engine.extract_text(sub_crop)
        return processor.parser.synthesize_drive(forced_shape_id, raw_sub_texts)

    if forced_type == "tape":
        main_box = regions["tape_main_stat"]
        sub_box = regions["tape_sub_stats"]
        main_crop = img[main_box[1]:main_box[3], main_box[0]:main_box[2]]
        sub_crop = img[sub_box[1]:sub_box[3], sub_box[0]:sub_box[2]]
        set_name = forced_set_name
        if not set_name:
            set_texts = []
            for box in _tape_set_ocr_boxes(regions, width, height):
                crop = img[box[1]:box[3], box[0]:box[2]]
                set_texts.extend(processor.ocr_engine.extract_text(crop))
                joined = "".join(set_texts)
                set_name = processor.parser._fuzzy_match_set_name(joined)
                if set_name and "未知" not in str(set_name):
                    break
            if not set_name:
                set_name = "未知套装"
        raw_main_texts = processor.ocr_engine.extract_text(main_crop)
        raw_sub_texts = processor.ocr_engine.extract_text(sub_crop)
        if not raw_main_texts:
            raw_main_texts = [""]
        main_texts = [forced_main_stat] if forced_main_stat else raw_main_texts
        item = processor.parser.synthesize_tape(set_name, main_texts, raw_sub_texts)
        if forced_set_name:
            item.set_name = forced_set_name
        if forced_main_stat:
            item.main_stats = forced_main_stat
        return item

    raise ValueError(f"未知鉴定类型: {forced_type}")


def _tape_set_ocr_boxes(regions: dict, image_width: int, image_height: int) -> list[tuple[int, int, int, int]]:
    boxes = []
    explicit = regions.get("tape_set_name")
    if explicit:
        boxes.append(explicit)
    identity = regions.get("identity_check")
    if identity:
        x1, y1, x2, _y2 = identity
        width = max(1, x2 - x1)
        boxes.append((
            max(0, x1 - int(width * 1.25)),
            max(0, y1 - int(image_height * 0.12)),
            min(image_width, x2 + int(width * 0.20)),
            max(0, y1 - int(image_height * 0.05)),
        ))
        boxes.append(identity)
    main_box = regions.get("tape_main_stat")
    if main_box:
        x1, y1, x2, _y2 = main_box
        width = max(1, x2 - x1)
        height = max(1, _y2 - y1)
        pad_x = int(width * 0.25)
        box_height = max(int(height * 2.2), 48)
        top_y2 = max(0, y1 - 6)
        top_y1 = max(0, top_y2 - box_height)
        boxes.append((max(0, x1 - pad_x), top_y1, min(image_width, x2 + pad_x), top_y2))
    deduped = []
    seen = set()
    for box in boxes:
        normalized = tuple(int(v) for v in box)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def cluster_identify_lines(lines: list[dict], image_shape: tuple[int, int]) -> list[list[dict]]:
    height, width = image_shape
    stat_lines = [line for line in lines if is_identify_stat_candidate(line.get("text", ""))]
    if not stat_lines:
        return []

    parent = list(range(len(stat_lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    x_limit = max(150, int(width * 0.11))
    y_limit = max(180, int(height * 0.24))
    for i, a in enumerate(stat_lines):
        ax1, ay1, ax2, ay2 = a["box"]
        acx, acy = (ax1 + ax2) / 2, (ay1 + ay2) / 2
        for j in range(i + 1, len(stat_lines)):
            b = stat_lines[j]
            bx1, by1, bx2, by2 = b["box"]
            bcx, bcy = (bx1 + bx2) / 2, (by1 + by2) / 2
            if abs(acx - bcx) <= x_limit and abs(acy - bcy) <= y_limit:
                union(i, j)

    grouped = {}
    for idx, line in enumerate(stat_lines):
        grouped.setdefault(find(idx), []).append(line)

    clusters = list(grouped.values())
    clusters.sort(key=lambda group: (min(line["box"][1] for line in group), min(line["box"][0] for line in group)))
    return clusters


def box_intersects(box, region) -> bool:
    x1, y1, x2, y2 = box
    rx1, ry1, rx2, ry2 = region
    return not (x2 < rx1 or x1 > rx2 or y2 < ry1 or y1 > ry2)


def synthesize_identify_cluster(
    processor,
    img,
    lines: list[dict],
    forced_type: str | None = None,
    forced_shape_id: str | None = None,
    forced_set_name: str | None = None,
    forced_main_stat: str | None = None,
):
    texts = [line.get("text", "") for line in lines if line.get("text")]
    stat_texts = identify_stat_texts(lines, forced_type=forced_type)
    parse_texts = stat_texts if stat_texts else ([] if forced_type else texts)
    if not processor.parser._clean_stats(parse_texts):
        return None

    if forced_type == "tape":
        set_name = forced_set_name or processor.parser._fuzzy_match_set_name("".join(texts))
        main_texts = [forced_main_stat] if forced_main_stat else ["".join(texts)]
        item = processor.parser.synthesize_tape(set_name, main_texts, parse_texts[-4:])
        if forced_main_stat:
            item.main_stats = forced_main_stat
        return item
    if forced_type == "drive":
        if not forced_shape_id:
            return None
        return processor.parser.synthesize_drive(forced_shape_id, parse_texts[:4])

    joined = "".join(texts)
    set_name = processor.parser._fuzzy_match_set_name(joined)
    main_stat = processor.parser._fuzzy_match_tape_main(joined)
    looks_tape = looks_like_tape_identity(processor.parser, joined) or (
        set_name in processor.parser.REAL_SETS_WHITE_LIST and main_stat in processor.parser.TAPE_MAIN_STATS_POOL
    )
    if looks_tape and not looks_like_drive_identity(joined):
        return processor.parser.synthesize_tape(set_name, [joined], parse_texts[-4:])

    x1 = min(line["box"][0] for line in lines)
    y1 = min(line["box"][1] for line in lines)
    x2 = max(line["box"][2] for line in lines)
    y2 = max(line["box"][3] for line in lines)
    height, width = img.shape[:2]
    search_region = (
        max(0, x1 - int(width * 0.35)),
        max(0, y1 - int(height * 0.45)),
        min(width, x2 + int(width * 0.08)),
        min(height, y2 + int(height * 0.10)),
    )
    shape = locate_shape_in_image(processor.shape_recognizer, img, search_region)
    if shape["shape_id"] == "Unknown":
        shape = locate_shape_in_image(processor.shape_recognizer, img, None)
    if shape["shape_id"] == "Unknown":
        logger.debug(f"鉴定候选未找到形状，跳过: {texts}")
        return None
    return processor.parser.synthesize_drive(shape["shape_id"], parse_texts[:4])


def identify_stat_texts(lines: list[dict], forced_type: str | None = None) -> list[str]:
    bad_keywords = (
        "史诗", "传说", "附近", "最多", "持续", "每层", "每有", "受到", "造成",
        "装备者", "角色位于", "依旧生效", "推荐", "装配一个", "每装配",
    )
    good_keywords = ("增加", "提升", "增强", "%")
    stat_keywords = ("暴击率", "暴击伤害", "伤害", "攻击力", "防御力", "生命值", "环合强度", "倾陷强度")
    texts = []
    for line in sorted(lines, key=lambda item: (item["box"][1], item["box"][0])):
        text = (line.get("text") or "").strip()
        if not any(ch.isdigit() for ch in text):
            continue
        if any(keyword in text for keyword in bad_keywords):
            continue
        if forced_type in ("drive", "tape") and not (
            any(keyword in text for keyword in good_keywords)
            or any(keyword in text for keyword in stat_keywords)
        ):
            continue
        if len(text) > 24:
            continue
        texts.append(text)
    return texts


def is_identify_stat_candidate(text: str) -> bool:
    text = (text or "").strip()
    if not any(ch.isdigit() for ch in text):
        return False
    bad_keywords = (
        "史诗", "传说", "附近", "最多", "持续", "每层", "每有", "受到", "造成",
        "装备者", "角色位于", "依旧生效", "推荐", "装配一个", "每装配", "UID",
    )
    if any(keyword in text for keyword in bad_keywords):
        return False
    stat_keywords = ("暴击率", "暴击伤害", "伤害", "攻击力", "防御力", "生命值", "环合强度", "倾陷强度")
    return any(keyword in text for keyword in ("增加", "提升", "增强", "%")) or any(
        keyword in text for keyword in stat_keywords
    )


def dedupe_identify_items(processor, items: list) -> list:
    deduped = []
    seen = set()
    for item in items:
        signature = item_signature(processor, item)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def item_signature(processor, item_data) -> str:
    data = normalized_signature_data(item_data.model_dump())
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def item_signature_from_dict(item_data: dict) -> str:
    data = normalized_signature_data(item_data)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized_signature_data(item_data: dict) -> dict:
    data = dict(item_data or {})
    for key in ("uid", "role_scores", "max_score", "is_mvp", "pick_order"):
        data.pop(key, None)
    return data


def load_existing_inventory_signatures(processor) -> set[str]:
    if processor._existing_inventory_signatures is not None:
        return processor._existing_inventory_signatures
    signatures = set()
    if not processor.replace_output and processor.output_file and os.path.exists(processor.output_file):
        try:
            with open(processor.output_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        signatures.add(item_signature_from_dict(item))
        except Exception as exc:
            logger.debug(f"读取现有库存签名失败，跳过仓底首图库存去重: {exc}")
    processor._existing_inventory_signatures = signatures
    return signatures
