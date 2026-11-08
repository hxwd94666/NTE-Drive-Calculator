# 在临时真实账号库上验证分阶段集合排除、状态增量、纯手动快路径及冻结失效。
from concurrent.futures import CancelledError
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.domain.equipment.intelligent_rules import attach_origin, RULE_SCHEME_VERSION
from src.domain.equipment.management_settings import MatchingScope
from src.domain.equipment.warehouse_decisions import DecisionStatus, decision_row
from src.services.inventory.management_settings import ManagementSettingsService
from src.services.inventory import warehouse_predecessors as predecessors
from src.services.inventory import warehouse_recommendation_preview as previews
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_user_data_inventory_dao import item, snapshot, stat


def case(tmp_path, monkeypatch):
    path = tmp_path / "account.sqlite3"
    rows = [item(serial, serial, "core") for serial in range(11, 16)]
    properties = ["AtkUp", "HPMaxUp", "DefUp", "CritBase", "CritDamageBase"]
    for row, property_id in zip(rows, properties):
        row.update(locked=False, discarded=row["uid"]["serial"] == 14, main_stats=[stat(property_id, 0.1, True)])
    with UserDataDao(path, account_id="test") as dao:
        snapshot_id = dao.import_inventory_snapshot(snapshot(1, rows))
    static = SimpleNamespace(list_suits=lambda: [{"suit_id": "Suit1", "name_zh": "套装"}])
    monkeypatch.setattr(predecessors, "StaticGameDataDao", lambda _: nullcontext(static))
    rules = [
        {
            "name": "保留",
            "enabled": True,
            "rule_id": "keep",
            "item_type": "tape",
            "action": "keep",
            "main_stats": ["攻击力%"],
        },
        {
            "name": "弃置",
            "enabled": True,
            "rule_id": "discard",
            "item_type": "tape",
            "action": "discard",
            "main_stats": ["生命值%"],
        },
        {
            "name": "冲突保留",
            "enabled": True,
            "rule_id": "conflict-keep",
            "item_type": "tape",
            "action": "keep",
            "main_stats": ["防御力%"],
        },
        {
            "name": "冲突锁定",
            "enabled": True,
            "rule_id": "conflict-lock",
            "item_type": "tape",
            "action": "lock",
            "main_stats": ["防御力%"],
        },
    ]
    service = ManagementSettingsService(path, scope=MatchingScope.WAREHOUSE)
    config, matching = service.load()
    config["preserve_rules"] = rules
    config["lock"].update(enabled=True, grade="SSS")
    config["discard"].update(enabled=True, grade="B")
    evaluated = []

    def prepare(config, inventory, **kwargs):
        assert kwargs["scope"] == MatchingScope.WAREHOUSE
        for equipment in inventory:
            equipment.role_scores = {"A": 100 if equipment.uid.endswith(("-11", "-12", "-13")) else 50}
        scorer = SimpleNamespace(roles_db={"A": {}}, get_grade_tag=lambda score, _: "SSS" if score >= 80 else "A")
        context = SimpleNamespace(
            missing_character_ids=(), errors_for_roles=lambda _: (), for_item=lambda _: [SimpleNamespace(role="A")]
        )
        return scorer, context, {"lock": ["A"], "discard": ["A"]}, frozenset()

    scoring_decision = predecessors.scoring_decision

    def score(equipment, *args, **kwargs):
        evaluated.append(equipment.uid)
        return scoring_decision(equipment, *args, **kwargs)

    monkeypatch.setattr(predecessors, "prepare_management_scoring", prepare)
    monkeypatch.setattr(predecessors, "scoring_decision", score)
    return path, snapshot_id, service, config, matching, evaluated


def test_manual_keep_discard_and_conflict_never_enter_scoring_even_with_high_lock_scores(tmp_path, monkeypatch):
    path, snapshot_id, _, config, _, evaluated = case(tmp_path, monkeypatch)
    batch = predecessors.evaluate_predecessors(path, "static", "config", config)
    assert batch.snapshot_id == snapshot_id
    assert evaluated == ["nte-core-14-14", "nte-core-15-15"]
    assert batch.decisions["nte-core-11-11"].target == "normal"
    assert batch.decisions["nte-core-12-12"].target == "discarded"
    assert batch.decisions["nte-core-13-13"].status == DecisionStatus.CONFLICT
    restored = batch.decisions["nte-core-14-14"]
    assert restored.status == DecisionStatus.DECIDED and restored.target == "normal"
    assert batch.decisions["nte-core-15-15"].status == DecisionStatus.UNDECIDED
    assert not decision_row(batch.decisions["nte-core-15-15"], batch.raw_by_uid["nte-core-15-15"])["predecessor_fixed"]


def test_explicit_user_target_and_runtime_state_are_frozen_without_mutating_inventory(tmp_path, monkeypatch):
    path, snapshot_id, _, config, _, evaluated = case(tmp_path, monkeypatch)
    with UserDataDao(path) as dao:
        dao.apply_inventory_runtime_state_delta(
            snapshot_id,
            [{"uid": {"slot": 15, "serial": 15}, "locked": False, "discarded": True, "equipped": False}],
            force=True,
        )
        original = deepcopy(dao.export_inventory_snapshot(snapshot_id))
    batch = predecessors.evaluate_predecessors(
        path, "static", "config", config, user_targets={"nte-core-14-14": "locked"}
    )
    assert evaluated == ["nte-core-15-15"]
    assert batch.decisions["nte-core-14-14"].stage == "user"
    assert batch.decisions["nte-core-14-14"].target == "locked"
    assert batch.decisions["nte-core-15-15"].target == "normal"
    with UserDataDao(path) as dao:
        assert dao.export_inventory_snapshot(snapshot_id) == original


def preview_case(tmp_path, monkeypatch, *, smart=False):
    path, snapshot_id, service, config, matching, evaluated = case(tmp_path, monkeypatch)
    if smart:
        config["preserve_rules"].append(
            attach_origin(
                {
                    "name": "智能条件",
                    "enabled": True,
                    "item_type": "tape",
                    "action": "lock",
                    "main_stats": ["暴击伤害%"],
                },
                "combo:test",
            )
        )
        config["recommendation"] = {"rule_scheme_version": RULE_SCHEME_VERSION}
    base, _ = service.load()
    config = service.save(config, matching, expected_config=base, expected_matching=matching)
    data, assets, config_dir = tmp_path / "data", tmp_path / "images", tmp_path / "config"
    for directory in (data, assets, config_dir):
        directory.mkdir()
    static = data / "static.sqlite3"
    for file in (static, data / "manifest.json", assets / "manifest.json", config_dir / "stats.json"):
        file.write_text("{}", encoding="utf8")
    args = dict(
        database=path,
        static_database=static,
        asset_root=assets,
        config_dir=config_dir,
        config=config,
        matching=matching,
        check_context=lambda: None,
        cancel_check=lambda: False,
    )
    return args, snapshot_id, evaluated


def test_plain_preview_uses_no_intelligent_solver_and_retains_readonly_conflict_evidence(tmp_path, monkeypatch):
    args, snapshot_id, evaluated = preview_case(tmp_path, monkeypatch)
    monkeypatch.setattr(previews, "run_warehouse_trial", lambda **_: pytest.fail("无智能规则不得求解"))
    result, guard = previews.build_preview(**args)
    assert result["snapshot_id"] == snapshot_id and result["loss_check"]["scopes"] == []
    assert previews.preview_targets(result) == {"nte-core-12-12": "discarded", "nte-core-14-14": "normal"}
    assert result["conflicts"][0]["uid"] == "nte-core-13-13"
    assert {rule["rule_id"] for rule in result["conflicts"][0]["rules"]} == {"conflict-keep", "conflict-lock"}
    assert evaluated == ["nte-core-14-14", "nte-core-15-15"]
    guard.require_current()
    with UserDataDao(args["database"]) as dao:
        dao.apply_inventory_runtime_state_delta(
            snapshot_id,
            [{"uid": {"slot": 15, "serial": 15}, "locked": True, "discarded": False, "equipped": False}],
            force=True,
        )
    with pytest.raises(ValueError, match="装备状态已变化"):
        guard.require_current()


def test_predecessor_decisions_are_passed_to_smart_without_manual_rules_or_lost_image_root(tmp_path, monkeypatch):
    args, snapshot_id, _ = preview_case(tmp_path, monkeypatch, smart=True)
    calls = []

    def trial(**kwargs):
        calls.append(kwargs)
        assert kwargs["static_database"] != args["static_database"]
        assert kwargs["asset_root"] == args["asset_root"]
        assert all(rule.get("intelligent") for rule in kwargs["preserve_rules"])
        decisions = kwargs["predecessors"]
        assert decisions["nte-core-14-14"].status == DecisionStatus.DECIDED
        assert decisions["nte-core-15-15"].status == DecisionStatus.UNDECIDED
        assert kwargs["prepared_scorer"] is not None
        with UserDataDao(kwargs["account_snapshot"]) as dao:
            raw = {
                f"nte-core-{row['uid_slot']}-{row['uid_serial']}": row
                for row in dao.list_inventory_items_with_runtime_state(snapshot_id)
            }
        return {
            "snapshot_id": snapshot_id,
            "rows": [decision_row(decision, raw[uid]) for uid, decision in decisions.items()],
            "violations": [],
        }

    monkeypatch.setattr(previews, "run_warehouse_trial", trial)
    _, guard = previews.build_preview(**args)
    assert len(calls) == 1
    assert args["asset_root"] / "manifest.json" in guard.files
    guard.require_current()


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "overridden"])
def test_smart_partial_or_overriding_output_is_rejected_before_projection(tmp_path, monkeypatch, invalid):
    args, snapshot_id, _ = preview_case(tmp_path, monkeypatch, smart=True)

    def trial(**kwargs):
        with UserDataDao(kwargs["account_snapshot"]) as dao:
            raw = {
                f"nte-core-{row['uid_slot']}-{row['uid_serial']}": row
                for row in dao.list_inventory_items_with_runtime_state(snapshot_id)
            }
        rows = [decision_row(decision, raw[uid]) for uid, decision in kwargs["predecessors"].items()]
        if invalid == "missing":
            rows.pop()
        elif invalid == "duplicate":
            rows[-1] = deepcopy(rows[0])
        else:
            rows[0]["fixed_state_target"] = "discarded"
        return {"snapshot_id": snapshot_id, "rows": rows, "violations": []}

    monkeypatch.setattr(previews, "run_warehouse_trial", trial)
    with pytest.raises(ValueError, match="不替换已有草稿"):
        previews.build_preview(**args)


def test_cancel_never_returns_partial_predecessor_batch(tmp_path, monkeypatch):
    path, _, _, config, _, _ = case(tmp_path, monkeypatch)
    checks = []

    def cancel():
        checks.append(True)
        return len(checks) == 3

    with pytest.raises(CancelledError):
        predecessors.evaluate_predecessors(path, "static", "config", config, cancel_check=cancel)


def test_smart_priority_does_not_visit_fixed_predecessors():
    from src.domain.equipment.warehouse_decisions import WarehouseDecision
    from src.domain.equipment.warehouse_rule_order import apply_ordered_rules

    state = {"locked": False, "discarded": True, "equipped": False}
    fixed = WarehouseDecision("fixed", DecisionStatus.DECIDED, "scoring", "normal")
    rows = [decision_row(fixed, state), {"uid": "open", "decision": "keep", "reasons": []}]
    rules = [{"enabled": True, "action": "discard"}, {"enabled": True, "action": "lock"}]
    result = {"rows": rows}
    # 故意不提供固定件的匹配结果；仲裁必须先排除，而非先决定再覆盖回来。
    apply_ordered_rules(
        result, [SimpleNamespace(uid="fixed"), SimpleNamespace(uid="open")], rules, matched_by_uid={"open": rules}
    )
    assert "winning_rule" not in rows[0] and rows[0]["fixed_state_target"] == "normal"
    assert rows[1]["winning_rule"]["position"] == 1 and rows[1]["decision"] == "discard"
    apply_ordered_rules(
        result,
        [SimpleNamespace(uid="fixed"), SimpleNamespace(uid="open")],
        list(reversed(rules)),
        matched_by_uid={"open": rules},
    )
    assert rows[1]["decision"] == "lock"
