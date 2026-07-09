# 导出解析后的库存数据文件。
"""Helpers for writing parsed inventory JSON output."""

from __future__ import annotations

import json
import os
import time

from src.utils.logger import logger


def make_unique_uid(uid: str, existing_uids: set) -> str:
    if uid not in existing_uids:
        return uid
    base = uid
    suffix = 2
    while f"{base}_{suffix}" in existing_uids:
        suffix += 1
    return f"{base}_{suffix}"


def export_inventory(processor) -> None:
    """Merge parsed items and persist them to the configured inventory file."""
    existing_inventory = []
    existing_uids = set()

    if processor.replace_output:
        for item in processor.inventory:
            data = item.model_dump()
            uid = data.get("uid") or f"item_{int(time.time() * 1000)}"
            data["uid"] = make_unique_uid(uid, existing_uids)
            existing_inventory.append(data)
            existing_uids.add(data["uid"])
        with open(processor.output_file, "w", encoding="utf-8") as f:
            json.dump(existing_inventory, f, ensure_ascii=False, indent=4)
        logger.success(f"仓库覆盖更新完成。本次写入 {len(existing_inventory)} 个装备。")
        return

    if os.path.exists(processor.output_file):
        try:
            with open(processor.output_file, "r", encoding="utf-8") as f:
                existing_inventory = json.load(f)
            existing_uids = {
                item.get("uid")
                for item in existing_inventory
                if isinstance(item, dict) and item.get("uid")
            }
        except Exception:
            existing_inventory = []
            existing_uids = set()

    new_count = 0
    for item in processor.inventory:
        data = item.model_dump()
        uid = data.get("uid") or f"item_{int(time.time() * 1000)}"
        data["uid"] = make_unique_uid(uid, existing_uids)
        existing_inventory.append(data)
        existing_uids.add(data["uid"])
        new_count += 1

    with open(processor.output_file, "w", encoding="utf-8") as f:
        json.dump(existing_inventory, f, ensure_ascii=False, indent=4)

    logger.success(f"仓库增量更新完成。新入库 {new_count} 个，总库存 {len(existing_inventory)} 个。")
