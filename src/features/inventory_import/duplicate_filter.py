# 过滤截图解析结果中的重复装备。
"""Helpers for adjacent-screenshot duplicate filtering."""

from __future__ import annotations

import os
import re

import cv2
import numpy as np

from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode


def image_fingerprint(image_path: str):
    img = imread_unicode(image_path)
    if img is None:
        return None
    img = crop_window_border_from_image(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    return cv2.resize(gray, (96, 54), interpolation=cv2.INTER_AREA)


def is_same_capture(previous, current) -> bool:
    if previous is None or current is None:
        return False
    diff = cv2.absdiff(previous, current)
    return float(np.mean(diff)) <= 1.0


def filename_sequence_key(filename: str | None):
    if not filename:
        return None
    stem = os.path.splitext(os.path.basename(filename))[0]
    match = re.match(r"^(.*?)(\d+)$", stem)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def is_inventory_probe_filename(filename: str | None) -> bool:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return stem.startswith("raw_drive_probe_")


def is_probe_first_new_pair(previous_filename: str | None, current_filename: str | None) -> bool:
    previous_stem = os.path.splitext(os.path.basename(previous_filename or ""))[0]
    current_stem = os.path.splitext(os.path.basename(current_filename or ""))[0]
    return previous_stem.startswith("raw_drive_probe_") and current_stem == "raw_drive_new_0001"


def are_named_neighbors(previous_filename: str | None, current_filename: str | None) -> bool:
    if is_probe_first_new_pair(previous_filename, current_filename):
        return True
    previous_key = filename_sequence_key(previous_filename)
    current_key = filename_sequence_key(current_filename)
    if not previous_key or not current_key:
        return False
    return previous_key[0] == current_key[0] and current_key[1] == previous_key[1] + 1


def process_image_file(processor, image_path: str, filename: str | None = None):
    item_data = processor._process_single_image(image_path)
    current_name = filename or os.path.basename(image_path)
    current_signature = processor._item_signature(item_data)
    current_fingerprint = image_fingerprint(image_path)
    is_inventory_probe_duplicate = (
        processor._is_inventory_probe_filename(current_name)
        and current_signature in processor._load_existing_inventory_signatures()
    )
    is_adjacent_duplicate = (
        processor._last_parsed_signature == current_signature
        and are_named_neighbors(processor._last_parsed_filename, current_name)
        and is_same_capture(processor._last_parsed_image_fingerprint, current_fingerprint)
    )

    processor._last_parsed_filename = current_name
    processor._last_parsed_signature = current_signature
    processor._last_parsed_image_fingerprint = current_fingerprint
    processor._mark_image_success(image_path)

    if is_inventory_probe_duplicate:
        return item_data, False

    if is_adjacent_duplicate:
        return item_data, False

    processor.inventory.append(item_data)
    return item_data, True
