# 验证真实库存单位下的卡带评分、边际评分及SQLite换回/重开一致，不写真实账号。
from contextlib import closing
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest

from src.domain.equipment.loadouts.loadout_plan_scores import assignment_score_key
from src.features.inventory.equipment.display.plan_display_state import _sqlite_plan_display_state
from src.models.equipment import Tape
from src.optimizer.scoring import ScoringEngine
from src.services.official_role.equipment import marginal_scoring
from src.services.official_role.equipment.base_equipment_scoring import score_official_role_equipment
from src.services.official_role.page import load_official_role_detail
from src.services.official_role.equipment.replacement import save_official_role_replacement
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.characters.official.test_replacement_static_source import sources as sources


@pytest.fixture(scope="module")
def engine():
    return ScoringEngine(roles_db={})


@pytest.mark.parametrize("quality,coefficient", [("orange", 1.0), ("purple", 0.8), ("blue", 0.6)])
@pytest.mark.parametrize("fraction", [1.0, 0.5])
@pytest.mark.parametrize("main_weight", [1.0, 0.4])
def test_base_and_marginal_card_scores_use_quality_full_level_not_inventory_ratio(
        engine, monkeypatch, quality, coefficient, fraction, main_weight):
    detail = {"attributes": {"CritBase": {"filter_name_zh": "暴击率%"},
                             "CritDamageBase": {"filter_name_zh": "暴击伤害%"}},
              "property_weights": {"CritBase": 1.0, "CritDamageBase": 1.0},
              "main_property_weights": {"CritDamageBase": main_weight}}
    reference = engine.stat_catalog.tape_main_values["暴击伤害%"]
    item = {"kind": "core", "quality": quality,
            "main_stats": [{"property_id": "CritDamageBase", "value": reference * coefficient * fraction / 100,
                            "percent": True}],
            "sub_stats": [{"property_id": "CritBase", "value": 0.1, "percent": True},
                          {"property_id": "CritDamageBase", "value": 0.2, "percent": True}]}
    before = deepcopy(item)
    card = Tape(uid="synthetic-a", area=15, set_name="synthetic", main_stats="暴击伤害%",
                quality={"orange": "Gold", "purple": "Purple", "blue": "Blue"}[quality],
                sub_stats={"暴击率%": 10, "暴击伤害%": 20})
    # 鉴定使用的同一真实批量公共核；不mock公式或组件响应。
    engine.prepare_scores([card], profiles=[{"key": "identification", "weights": {"暴击率%": 1, "暴击伤害%": 1},
                                            "main_weights": {"暴击伤害%": main_weight}}], assign_scores=True)
    expected = card.role_scores["identification"]
    assert expected > 0
    assert score_official_role_equipment(engine, detail=detail, item=item, shape_areas={}) == expected
    # 仅注入同一真实评分器，避免隐式读取另一个配置；不替换任何数值结果。
    monkeypatch.setattr(marginal_scoring, "_replacement_scoring_engine", lambda _path: engine)
    kwargs = dict(property_weights=detail["property_weights"], main_property_weights=detail["main_property_weights"])
    marginal_scoring.prefetch_official_role_hidden_equipment_scores(detail, [item], **kwargs)
    assert marginal_scoring.calculate_official_role_hidden_equipment_score(detail, item, **kwargs) == expected
    assert item == before  # 伤害模型的实际等级、主副词条和百分比事实不因评分而改写。


def test_real_sqlite_a_b_a_restores_score_after_reopen_and_keeps_history(engine, sources, tmp_path):
    account = tmp_path / "isolated-user.sqlite3"
    with closing(sqlite3.connect(sources["account"].as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(account)) as target:
            source.backup(target)
    with UserDataDao(account) as dao:
        current_snapshot = dao.current_inventory_snapshot_id()
        original_items = dao.list_inventory_items(sources["historical"])
        by_serial = {item["uid_serial"]: item for item in original_items}
        slot = dao.create_loadout_slot(1003, "score roundtrip", slot_key="score-roundtrip")
        card = Tape(uid="synthetic-a", area=15, quality="Gold", set_name="synthetic",
                    main_stats="暴击率%", sub_stats={"暴击伤害%": 1})
        weights = {"暴击率%": 1.0, "暴击伤害%": 1.0}
        expected = engine.calculate_cartridge_score(card, weights, engine.max_theoretical_weight(weights),
                                                    {"暴击率%": 1.0})
        original_id = dao.save_plan_to_slot(slot, name="roundtrip", source_snapshot_id=sources["historical"],
            assignments=[{"kind": "core", "uid_slot": 17, "uid_serial": 601}], score=expected,
            payload={"assignment_scores": {"nte-core-17-601": expected}})
        history = dao.get_loadout_plan(original_id)
    key = f"saved:{slot}"
    for serial in (602, 601):
        detail = load_official_role_detail(account, 1003, static_database_path=sources["paths"]["B"],
            static_schema_version=sources["schema"], asset_root=sources["assets"], include_replacement_candidates=False)
        detail["property_weights"] = {"CritBase": 1.0, "CritDamageBase": 1.0}
        detail["main_property_weights"] = {"CritBase": 1.0}
        context = detail["equipment_contexts"][key]
        target = context["items"][0]
        current = {assignment_score_key(item): score_official_role_equipment(
            engine, detail=detail, item=item, shape_areas={}) for item in context["items"]}
        candidate_score = score_official_role_equipment(engine, detail=detail, item=by_serial[serial], shape_areas={})
        plan_id = save_official_role_replacement(account, detail, target, by_serial[serial], context_key=key,
            replacement_score=candidate_score, current_assignment_scores=current,
            expected_account_id="official-role-source-fixture")
        # 关闭并重开DAO，不能靠弹窗内存中的分数掩盖持久化问题。
        with UserDataDao(account) as dao, StaticGameDataDao(sources["paths"]["B"]) as static:
            plan = dao.get_loadout_plan(plan_id)
            display = _sqlite_plan_display_state(plan, dao, static)
            assert plan["score"] == expected
            assert plan["payload"]["assignment_scores"] == {f"nte-core-17-{serial}": expected}
            assert display["equipped_tape"]["score"] == display["total_score"] == expected
            assert display["_sqlite_assignment_scores_complete"]
            assert plan["source_snapshot_id"] == sources["historical"]
            assert dao.list_inventory_items(sources["historical"]) == original_items
            assert dao.current_inventory_snapshot_id() == current_snapshot
            original = dao.get_loadout_plan(original_id)
            assert original["payload"] == history["payload"] and original["score"] == history["score"]


@pytest.mark.parametrize("quality,coefficient", [("orange", 1.0), ("purple", 0.8), ("blue", 0.6)])
def test_flat_main_stat_also_ignores_reinforcement_level_for_score(engine, monkeypatch, quality, coefficient):
    detail = {"attributes": {"CritBase": {"filter_name_zh": "暴击率%"},
                             "CritDamageBase": {"filter_name_zh": "暴击伤害%"},
                             "MagBase": {"filter_name_zh": "环合强度"}},
              "property_weights": {"CritBase": 1.0, "CritDamageBase": 1.0},
              "main_property_weights": {"MagBase": 1.0}}
    monkeypatch.setattr(marginal_scoring, "_replacement_scoring_engine", lambda _path: engine)
    card = Tape(uid="synthetic-flat", area=15, set_name="synthetic", main_stats="环合强度",
                quality={"orange": "Gold", "purple": "Purple", "blue": "Blue"}[quality],
                sub_stats={"暴击率%": 10})
    expected = engine.calculate_cartridge_score(card, {"暴击率%": 1, "暴击伤害%": 1}, 2.0, {"环合强度": 1})
    for fraction in (1.0, 0.5):
        item = {"kind": "core", "quality": quality,
                "main_stats": [{"property_id": "MagBase", "value": 180 * coefficient * fraction, "percent": False}],
                "sub_stats": [{"property_id": "CritBase", "value": 0.1, "percent": True}]}
        before = deepcopy(item)
        assert score_official_role_equipment(engine, detail=detail, item=item, shape_areas={}) == expected
        assert marginal_scoring.calculate_official_role_hidden_equipment_score(detail, item,
            property_weights=detail["property_weights"], main_property_weights=detail["main_property_weights"]) == expected
        assert item == before


def test_replacement_without_ready_scorer_does_not_offer_zero_score_save(monkeypatch):
    from src.features.official_role import replacement_controller

    messages = []
    monkeypatch.setattr(replacement_controller.QMessageBox, "information", lambda *args: messages.append(args[2]))
    result = replacement_controller._show_prepared_replacement(
        SimpleNamespace(scoring_engine=None), {}, {}, [{}], context_key="saved", on_saved=None, owner=None)
    # 提示统一经 plain_tooltip 包成 <qt> 纯文本。
    assert result is False and messages == ["<qt>评分资料尚未就绪，请等待资料加载完成后重试。</qt>"]
