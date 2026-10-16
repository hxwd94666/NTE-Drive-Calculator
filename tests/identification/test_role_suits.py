# 验证额外套装的账号隔离、合法形状、正式身份与统一评分，不操作真实库存。
from concurrent.futures import CancelledError
from copy import deepcopy

import pytest

from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.services.identification.equipment_service import EquipmentIdentificationService
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import MatchingSettingsService, normalize_matching_settings
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError

NTE_TEST_TIER = "core"


class Catalog:
    roles_db = {"测试角色": {"character_id": 1001, "default_set": "默认套装", "weights": {"暴击率%": 1}}}
    sets_db = {"默认套装": {"suit_id": "A"}, "额外套装": {"suit_id": "B"}, "无解套装": {"suit_id": "C"}}

    def __init__(self):
        self.calls = []

    def _resolve_set_name(self, name):
        return name

    def solve_blueprints(self, roles, *, custom_sets, cancel_check=None):
        role = roles[0]
        name = custom_sets[role]
        self.calls.append((role, name))
        return {role: [] if name == "无解套装" else [{
            "set_pieces": ["H_2" if name == "默认套装" else "V_2"], "extra_pieces": ["H_3"]}]}


def test_extra_suit_matching_is_shared_deduplicated_and_does_not_mutate_role():
    catalog = Catalog()
    before = deepcopy(catalog.roles_db)
    context = build_equipment_match_context(catalog, {"version": 1, "extra_suits": {"1001": ["A", "B", "B"]}})
    assert len(catalog.calls) == 2
    scoring = ScoringEngine("config", roles_db=catalog.roles_db)
    service = EquipmentIdentificationService(catalog, context, scoring)
    tape = Tape(uid="tape", quality="Gold", area=15, suit_id="B", set_name="额外套装",
                main_stats="攻击力%", sub_stats={"暴击率%": 2.8})
    rows = service.identify_item(tape)["rows"]
    assert len(rows) == 1 and rows[0]["match"] == "自选匹配"
    weights = catalog.roles_db["测试角色"]["weights"]
    assert rows[0]["score"] == scoring.calculate_cartridge_score(tape, weights, scoring.max_theoretical_weight(weights))
    drive = Drive(uid="drive", quality="Gold", area=3, shape_id="H_3", set_name="",
                  main_stats={"攻击力": 10, "生命值": 100}, sub_stats={})
    rows = service.identify_item(drive)["rows"]
    assert len(rows) == 1 and len(rows[0]["matched_suits"]) == 2
    drive.shape_id = "H_4"
    assert service.identify_item(drive)["matching_status"] == "unmatched"
    tape.suit_id = "wrong-id"
    assert service.identify_item(tape)["matching_status"] == "unmatched"
    assert catalog.roles_db == before


def test_no_blueprint_and_missing_catalog_are_distinct_and_cancel_is_not_swallowed():
    settings = {"version": 1, "extra_suits": {"1001": ["C", "missing"]}}
    context = build_equipment_match_context(Catalog(), settings)
    assert context.unavailable == (("测试角色", "C", "没有合法图纸"),)
    assert context.errors == (("测试角色", "missing", "套装资料缺失"),)
    assert {match.suit_id for match in context.matches} == {"A"}
    with pytest.raises(CancelledError):
        build_equipment_match_context(Catalog(), settings, cancel_check=lambda: True)


def test_settings_isolated_noop_and_conflict_preserve_current_value(tmp_path):
    services = []
    for account in ["a", "b"]:
        path = tmp_path / f"{account}.sqlite3"
        with UserDataDao(path, account_id=account):
            pass
        services.append(MatchingSettingsService(path))
    a, b = services
    original = a.load()
    draft = {"version": 1, "extra_suits": {"1001": ["B", "B"]}}
    assert a.save(draft, expected=original)
    assert a.load()["extra_suits"] == {"1001": ["B"]}
    assert b.load() == original
    assert not a.save(draft, expected=a.load())
    with pytest.raises(UserDataValidationError):
        a.save(original, expected=original)
    before = a.load()
    checks = 0

    def cancel_after_write():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise CancelledError()

    with pytest.raises(CancelledError):
        a.save(original, expected=before, check_current=cancel_after_write)
    assert a.load() == before


@pytest.mark.parametrize("failure", ["write", "cancel"])
def test_management_pair_commit_rolls_back_both_settings(tmp_path, monkeypatch, failure):
    from src.domain.post_actions import default_post_action_config
    database = tmp_path / "atomic.sqlite3"
    with UserDataDao(database, account_id="fixture") as dao:
        dao.replace_application_setting_copy("ui", {"other_preference": "keep"})
        before = dao.list_application_setting_copies()
    service = MatchingSettingsService(database)
    original = service.load()
    patch_fields = UserDataDao.patch_application_setting_copy
    def fail_after_write(dao, key, fields):
        patch_fields(dao, key, fields)
        if failure == "write":
            raise OSError("fixture write failure")
    monkeypatch.setattr(UserDataDao, "patch_application_setting_copy", fail_after_write)
    checks = 0
    def check():
        nonlocal checks
        checks += 1
        if failure == "cancel" and checks == 3:
            raise CancelledError()
    with pytest.raises((OSError, CancelledError)):
        service.save({"version": 1, "extra_suits": {"1001": ["B"]}}, expected=original,
                     check_current=check, post_action_config=default_post_action_config())
    with UserDataDao(database) as dao:
        assert dao.list_application_setting_copies() == before


def test_management_can_save_rules_without_changing_suits_or_other_preferences(tmp_path):
    from src.domain.post_actions import default_post_action_config
    database = tmp_path / "unchanged.sqlite3"
    with UserDataDao(database, account_id="fixture") as dao:
        dao.replace_application_setting_copy("ui", {"other_preference": "keep"})
    service = MatchingSettingsService(database)
    config = default_post_action_config()
    config["server_region"] = "hmt"
    assert not service.save(service.load(), expected=service.load(), post_action_config=config)
    with UserDataDao(database) as dao:
        preferences = dao.list_application_setting_copies()["ui"]
        assert preferences["other_preference"] == "keep"
        assert preferences["full_scan_post_action_config"] == config


def test_account_database_transfer_retains_matching_settings(tmp_path):
    from src.storage.sqlite.accounts.transfer_database import backup_account_database, prepare_account_database

    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    with UserDataDao(source, account_id="source"):
        pass
    settings = MatchingSettingsService(source)
    draft = {"version": 1, "extra_suits": {"1001": ["B"]}}
    settings.save(draft, expected=settings.load())
    backup_account_database(source, target)
    prepare_account_database(target, account_id="source", account_name="临时测试账号")
    assert MatchingSettingsService(target).load() == draft
    assert settings.load() == draft


def test_explicit_suit_replacement_round_trips_and_excludes_official_without_changing_role(tmp_path):
    from src.services.identification.matching_settings import set_matching_suits, reset_matching_role
    from src.storage.sqlite.accounts.transfer_database import backup_account_database, prepare_account_database
    original = {"version": 1, "extra_suits": {"1001": ["B"]}}
    draft = deepcopy(original)
    set_matching_suits(draft, 1001, "A", ["B"])
    assert draft == {"version": 2, "extra_suits": {}, "suit_overrides": {"1001": ["B"]}}
    catalog = Catalog()
    before = deepcopy(catalog.roles_db)
    assert {m.suit_id for m in build_equipment_match_context(catalog, draft).matches} == {"B"}
    assert catalog.roles_db == before
    path, target = tmp_path / "source.sqlite3", tmp_path / "restored.sqlite3"
    with UserDataDao(path, account_id="fixture"):
        pass
    service = MatchingSettingsService(path)
    service.save(original, expected=service.load())
    service.save(draft, expected=original)
    assert service.load() == draft
    backup_account_database(path, target)
    prepare_account_database(target, account_id="fixture", account_name="测试账号")
    assert MatchingSettingsService(target).load() == draft
    reset_matching_role(draft, 1001)
    assert {m.suit_id for m in build_equipment_match_context(catalog, draft).matches} == {"A"}
    service.save(draft, expected=service.load())
    assert service.load()["suit_overrides"] == {}
    # 恢复后再次保存等价旧格式，不重写配置、不失效现有结果。
    assert not service.save({"version": 1, "extra_suits": {}}, expected=service.load())


def test_invalid_replacement_does_not_fall_back_to_official_and_preserves_missing_identity():
    draft = {"version": 2, "extra_suits": {}, "suit_overrides": {"1001": ["missing"], "999999": ["B"]}}
    context = build_equipment_match_context(Catalog(), draft)
    assert context.matches == ()
    assert context.errors == (("测试角色", "missing", "套装资料缺失"),)
    assert context.missing_character_ids == (999999,)
    with pytest.raises(ValueError):
        normalize_matching_settings({"version": 2, "extra_suits": {}, "suit_overrides": {"1001": []}})


def test_matching_save_changes_only_own_setting_not_weights_or_historical_plans(tmp_path):
    import sqlite3

    path = tmp_path / "isolated.sqlite3"
    with UserDataDao(path, account_id="fixture") as dao:
        dao.seed_character_weight_preferences(1001, properties=[{"property_id": "CritBase", "weight": 0.7}],
            source_dataset_id="fixture", source_kind="account")
        dao.replace_application_setting_copy("calculation_preferences", {"strategy": "role_priority", "roles": [1001]})
        dao.save_loadout_plan(name="历史方案", character_id=1001, assignments=[], score=123.4,
            payload={"assignment_scores": {"fixture": 123.4}, "damage_optimization": {"mode": "direct"}})
    def unrelated_dump():
        with sqlite3.connect(path) as connection:
            # 比较全部账号表及其他设置，只有本功能自己的一行允许改变。
            return tuple(line for line in connection.iterdump()
                         if not line.startswith('INSERT INTO "application_setting_copy" VALUES(\'identification_role_suits\','))
    baseline = unrelated_dump()
    settings = MatchingSettingsService(path)
    settings.save({"version": 1, "extra_suits": {"1001": ["B"]}}, expected=settings.load())
    assert unrelated_dump() == baseline
    settings.save(normalize_matching_settings(None), expected=settings.load())
    assert unrelated_dump() == baseline


@pytest.mark.parametrize("raw", [{"version": 2, "extra_suits": {}},
                                 {"version": 1, "extra_suits": {"角色名": ["B"]}},
                                 {"version": 1, "extra_suits": {"1001": [None]}}])
def test_invalid_settings_do_not_become_empty_defaults(raw):
    with pytest.raises(ValueError):
        normalize_matching_settings(raw)


def test_formal_avatar_alias_merges_extras_without_rewriting_stored_settings(tmp_path):
    from pathlib import Path
    from src.services.identification.matching_identities import project_matching_settings

    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    service = MatchingSettingsService(database)
    stored = {"version": 1, "extra_suits": {"1046": ["B"], "1051": ["C"], "999999": ["A"]}}
    service.save(stored, expected=service.load())
    catalog = Catalog()
    catalog.roles_db = {"当前主角": {"character_id": 1051, "default_set": "默认套装", "weights": {}}}
    catalog.static_database_path = Path("data/game_static.sqlite3")
    projected, missing = project_matching_settings(catalog.roles_db, service.load(), catalog.static_database_path)
    assert projected["extra_suits"] == {"1051": ["B", "C"], "999999": ["A"]}
    assert missing == (999999,)
    context = build_equipment_match_context(catalog, service.load())
    assert {m.suit_id for m in context.matches} == {"A", "B"}
    assert context.missing_character_ids == (999999,)
    assert service.load() == stored  # 读取投影不把旧变体配置或缺失条目静默删掉。


def test_ambiguous_logical_identity_does_not_guess_target(monkeypatch):
    from src.services.identification.matching_identities import project_matching_settings
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

    # 显式制造同逻辑角色多个当前入口；即使名称看似相同也不挑任意一个。
    monkeypatch.setattr(StaticGameDataDao, "get_logical_character_key", lambda _self, _id: "shared")
    settings = {"version": 1, "extra_suits": {"1003": ["B"]}}
    projected, missing = project_matching_settings(
        {"甲": {"character_id": 1001}, "乙": {"character_id": 1002}}, settings, "data/game_static.sqlite3")
    assert projected == settings and missing == (1003,)


def test_missing_old_role_blocks_all_scope_not_selected_valid_role():
    from src.domain.post_actions import default_post_action_config, evaluate_target_state_for_item

    catalog = Catalog()
    context = build_equipment_match_context(catalog, {"version": 1, "extra_suits": {"999999": ["B"]}})
    scoring = ScoringEngine("config", roles_db=catalog.roles_db)
    tape = Tape(uid="fixture", quality="Gold", area=15, set_name="默认套装", suit_id="A",
                main_stats="攻击力%", sub_stats={"暴击率%": 2.8})
    scoring.evaluate_global_inventory([tape])
    config = default_post_action_config()
    config["lock"].update(enabled=True, grade="D", set_names=["默认套装"])
    state, detail = evaluate_target_state_for_item(tape, "normal", config, scoring, score_context=context)
    assert state == "normal" and detail["lock"]["reason"] == "evaluation_incomplete"
    config["lock"]["role_scope"] = "selected"
    state, _detail = evaluate_target_state_for_item(tape, "normal", config, scoring,
        selected_roles={"lock": ["测试角色"]}, score_context=context)
    assert state == "locked"
    result = EquipmentIdentificationService(catalog, context, scoring).identify_item(tape)
    assert result["matching_status"] == "incomplete"
    assert ("999999", "", "角色资料缺失") in result["matching_errors"]


def test_management_uses_same_matches_and_keeps_unmatched_separate_from_failures():
    from src.domain.equipment.matching import EquipmentMatchContext
    from src.domain.post_actions import default_post_action_config, evaluate_target_state_for_item

    catalog = Catalog()
    scoring = ScoringEngine("config", roles_db=catalog.roles_db)
    tape = Tape(uid="tape", quality="Gold", area=15, suit_id="B", set_name="额外套装",
                main_stats="攻击力%", sub_stats={"暴击率%": 2.8})
    scoring.evaluate_global_inventory([tape])
    config = default_post_action_config()
    config["discard"].update(enabled=True, grade="ACE", set_names=["额外套装"])
    default = build_equipment_match_context(catalog)
    state, detail = evaluate_target_state_for_item(tape, "normal", config, scoring, score_context=default)
    assert state == "normal" and detail["discard"]["reason"] == "unmatched"
    assert detail["discard"]["grade"] is None and detail["discard"]["score"] is None
    config["discard"]["discard_unmatched"] = True
    assert evaluate_target_state_for_item(tape, "normal", config, scoring, score_context=default)[0] == "discarded"
    failed = EquipmentMatchContext(errors=(("测试角色", "B", "资料缺失"),))
    state, detail = evaluate_target_state_for_item(tape, "normal", config, scoring, score_context=failed)
    assert state == "normal" and detail["discard"]["reason"] == "evaluation_incomplete"
    context = build_equipment_match_context(catalog, {"version": 1, "extra_suits": {"1001": ["B"]}})
    config["discard"].update(grade="D", discard_unmatched=False)
    config["lock"].update(enabled=True, grade="D", set_names=["额外套装"])
    state, detail = evaluate_target_state_for_item(tape, "normal", config, scoring, score_context=context)
    assert state == "locked" and detail["lock"]["role"] == "测试角色"
    config["lock"].update(role_scope="selected")
    assert evaluate_target_state_for_item(tape, "normal", config, scoring,
        selected_roles={"lock": [], "discard": []}, score_context=context)[0] == "normal"


def test_explicit_rule_can_apply_even_when_matching_evaluation_failed():
    from src.domain.equipment.matching import EquipmentMatchContext
    from src.domain.post_actions import default_post_action_config, evaluate_target_state_for_item

    config = default_post_action_config()
    config["preserve_rules"] = [{"name": "明确弃置", "action": "discard", "item_type": "tape",
        "main_stats": ["攻击力%"], "set_names": ["额外套装"]}]
    tape = Tape(uid="tape", quality="Gold", area=15, set_name="额外套装", main_stats="攻击力%", sub_stats={})
    state, detail = evaluate_target_state_for_item(tape, "normal", config, None,
        score_context=EquipmentMatchContext(errors=(("", "", "资料缺失"),)))
    assert state == "discarded" and detail["preserve"]["reason"] == "rule_discard"


def test_invalid_selected_scope_does_not_block_other_module_or_explicit_rule():
    from unittest.mock import patch
    from src.domain.post_actions import default_post_action_config
    from src.services.inventory.post_action_evaluation import PostActionEvaluator

    catalog = Catalog()
    context = build_equipment_match_context(catalog, {"version": 1, "extra_suits": {"1001": ["B"]}})
    matcher = EquipmentIdentificationService(catalog, context, ScoringEngine("config", roles_db=catalog.roles_db))
    plain = Tape(uid="plain", quality="Gold", area=15, suit_id="A", set_name="默认套装", main_stats="攻击力%", sub_stats={})
    rule = plain.model_copy(update={"uid": "rule", "main_stats": "暴击率%"})
    scored = plain.model_copy(update={"uid": "scored", "suit_id": "B", "set_name": "额外套装"})
    config = default_post_action_config()
    config["discard"].update(enabled=True, grade="ACE", discard_unmatched=True, role_scope="selected",
                             selected_character_ids=[999999], set_names=["默认套装"])
    config["lock"].update(enabled=True, grade="D", role_scope="selected",
                          selected_character_ids=[1001], set_names=["额外套装"])
    config["preserve_rules"] = [{"name": "明确锁定", "action": "lock", "item_type": "tape", "main_stats": ["暴击率%"]}]
    items = [plain, rule, scored]
    with patch("src.services.inventory.post_action_evaluation.EquipmentIdentificationService.from_paths", return_value=matcher):
        result = PostActionEvaluator(post_actions_config=config, static_database_path="data/game_static.sqlite3").evaluate(
            [(i, item, "normal") for i, item in enumerate(items)], items)
    assert [(c["uid"], c["target_state"]) for c in result.state_changes] == [("rule", "locked"), ("scored", "locked")]
    assert result.filter_summary["post_action_incomplete_count"] == 1
    assert result.matching_errors == (("discard", "", "指定角色评分资料缺失，请重新选择角色"),)


def test_bad_scores_are_not_zero_and_evaluation_failure_does_not_block_independent_rules():
    from unittest.mock import patch
    from src.domain.post_actions import default_post_action_config, build_state_changes
    from src.services.inventory.post_action_evaluation import PostActionEvaluator

    catalog = Catalog()
    context = build_equipment_match_context(catalog)
    scoring = ScoringEngine("config", roles_db=catalog.roles_db)
    tape = Tape(uid="tape", quality="Gold", area=15, set_name="默认套装", main_stats="攻击力%", sub_stats={})
    config = default_post_action_config()
    config["discard"].update(enabled=True, grade="ACE", discard_unmatched=True, set_names=["默认套装"])
    for scores in ({}, {"测试角色": float("nan")}, {"测试角色": float("inf")}, {"测试角色": True}):
        tape.role_scores = scores
        summary = {}
        assert build_state_changes([(1, tape, "normal")], config, scoring,
            score_context=context, evaluation_summary=summary) == []
        assert summary == {"post_action_incomplete_count": 1}
    config["preserve_rules"] = [{"name": "明确保留", "action": "lock", "item_type": "tape",
                                "main_stats": ["暴击率%"]}]
    rule_item = tape.model_copy(update={"uid": "rule-item", "main_stats": "暴击率%"})
    with patch("src.services.inventory.post_action_evaluation.EquipmentIdentificationService.from_paths",
               side_effect=ValueError("fixture unavailable")):
        result = PostActionEvaluator(post_actions_config=config).evaluate(
            [(1, tape, "normal"), (2, rule_item, "normal")], [tape, rule_item])
    assert len(result.state_changes) == 1 and result.state_changes[0]["uid"] == "rule-item"
    assert result.state_changes[0]["target_state"] == "locked"
    assert result.filter_summary["post_action_incomplete_count"] == 1
    assert result.matching_errors
