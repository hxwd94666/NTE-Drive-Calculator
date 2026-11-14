# 将本轮冻结手动冲突转为只读展示证据，图片按同一资料目录解析，不重读当前背包。
from copy import deepcopy

from src.domain.equipment.warehouse.warehouse_decisions import DecisionStatus
from src.integrations.game_assets.catalog import GameUiAssetCatalog


def freeze_conflict_details(batch, asset_root):
    assets, conflicts = GameUiAssetCatalog(asset_root), []
    for item in batch.items:
        decision = batch.decisions[item.uid]
        if decision.status != DecisionStatus.CONFLICT:
            continue
        raw = batch.raw_by_uid[item.uid]
        icon = assets.inventory_item_icon(raw["kind"], item.item_id)
        names = raw.get("names") or {}
        conflicts.append(
            {
                "uid": item.uid,
                "item": {
                    key: deepcopy(raw.get(key))
                    for key in ("kind", "item_id", "quality", "main_stats", "sub_stats", "locked", "discarded")
                },
                "name": str(names.get("zh_cn") or item.item_id or item.uid),
                "quality": item.quality,
                "icon_path": str(icon) if icon else None,
                "main": item.main_stats if isinstance(item.main_stats, str) else "、".join(item.main_stats),
                "sub_stats": deepcopy(item.sub_stats),
                "rules": deepcopy(list(decision.matches)),
            }
        )
    return conflicts
