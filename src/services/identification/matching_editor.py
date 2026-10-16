# 读取匹配编辑器的账号草稿和静态选项，图纸校验只准备当前角色。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path

from src.domain.suit_identity import suit_id_for_target
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import MatchingSettingsService, effective_matching_suits
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.services.identification.matching_identities import project_matching_settings
from src.services.recognition.resources import load_recognition_resources, check_recognition_resources
from src.solver.orchestrator import NTEPipelineOrchestrator


@dataclass(frozen=True)
class MatchingEditorRequest:
    account_id: str
    generation: int
    database: Path
    config: Path
    static: Path
    asset_root: Path | None = None


def _source(request, cancel):
    if cancel.is_set():
        raise CancelledError()
    resources = load_recognition_resources(request.config, request.static)
    source = NTEPipelineOrchestrator(config_dir=request.config, user_database_path=request.database,
        static_database_path=resources.static_database_path, expected_dataset=resources.dataset,
        stat_catalog=resources.stat_catalog())
    check_recognition_resources(resources)
    if cancel.is_set():
        raise CancelledError()
    return source, resources


def load_matching_editor(request, cancel):
    source, resources = _source(request, cancel)
    settings = MatchingSettingsService(request.database).load()
    roles = tuple((data["character_id"], name, suit_id_for_target(data.get("default_set"), source.sets_db))
                  for name, data in source.roles_db.items() if type(data.get("character_id")) is int)
    suits = tuple(sorted((str(data["suit_id"]), name) for name, data in source.sets_db.items() if data.get("suit_id")))
    # 已失效的角色仍展示可清理入口，不因资料更新自动丢掉账号偏好。
    projected, missing_ids = project_matching_settings(source.roles_db, settings, request.static)
    missing = tuple((identity, "", None) for identity in missing_ids)
    # IO 留在后台：按正式角色和卡带物品 ID 查公共素材，不按显示名称猜图片。
    artwork = {"roles": {}, "suits": {}}
    if request.asset_root is not None:
        assets = GameUiAssetCatalog(request.asset_root)
        artwork["roles"] = {identity: assets.character_icon(identity) for identity, _, _ in roles}
        with StaticGameDataDao(resources.static_database_path) as dao:
            # 同套装按最高品质选代表图，避免目录排序把蓝色低阶卡带当作封面。
            quality_order = {"ORANGE": 3, "PURPLE": 2, "BLUE": 1}
            items = sorted(dao.list_equipment_items("core"),
                           key=lambda row: (not row["is_guide_item"], quality_order.get(row["quality"], 0)),
                           reverse=True)
            for item in items:
                if cancel.is_set():
                    raise CancelledError()
                suit = str(item["suit_id"])
                path = assets.equipment_icon(item["item_id"])
                if path is not None and suit not in artwork["suits"]:
                    artwork["suits"][suit] = path
    check_recognition_resources(resources)
    return roles + missing, suits, settings, projected, artwork


def preview_matching_role(request, settings, character_id, cancel):
    source, resources = _source(request, cancel)
    names = tuple(name for name, data in source.roles_db.items() if data.get("character_id") == character_id)
    if not names:
        return {suit or "": "missing_role" for suit in effective_matching_suits(settings, character_id, None)}
    context = build_equipment_match_context(source, settings, cancel_check=cancel.is_set, role_names=names)
    check_recognition_resources(resources)
    states = {match.suit_id: "matched" for match in context.matches}
    states.update({suit: "unavailable" for _role, suit, _reason in context.unavailable})
    states.update({suit: "incomplete" for _role, suit, _reason in context.errors})
    return states


def save_matching_editor(request, draft, expected, cancel, *, post_action_config=None):
    def check():
        if cancel.is_set():
            raise CancelledError("账号或编辑器已关闭，本次未提交")
    check()
    return MatchingSettingsService(request.database).save(
        draft, expected=expected, check_current=check, post_action_config=post_action_config)
