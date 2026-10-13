# 用真实临时静态库和账号快照保护替换来源、历史身份、早失败及重试。
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from src.services.official_role.inventory_contexts import load_role_replacement_detail
from src.services.official_role.replacement import (
    replacement_candidates_for_frozen_context,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
)
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.integrations.static_catalog_release import StaticCatalogReleaseError
from src.storage.sqlite.user_data_dao import UserDataDao

NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[2]
CURVE = "CritBase_Core_ITEM_QUALITY_ORANGE"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_release_manifest(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        dataset_id, importer, _built = database.execute("SELECT dataset_id, importer_version, built_at_utc FROM dataset").fetchone()
        schema = database.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
        scope = database.execute("SELECT scope FROM dataset_scope").fetchone()[0]
    path.with_name("manifest.json").write_text(json.dumps({
        "catalog_scope": scope, "database": {"filename": path.name, "dataset_id": dataset_id,
        "schema_version": schema, "sha256": digest(path), "size_bytes": path.stat().st_size,
        "source_payloads_omitted": True}, "build_tool": {"importer_version": importer},
    }), encoding="utf-8")


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp("official-role-source")
    original = ROOT / "data/game_static.sqlite3"
    original_hash = digest(original)
    paths = {key: directory / key / "game_static.sqlite3" for key in ("A", "B")}
    deadline = time.monotonic() + 60

    def bounded(*_args):
        if time.monotonic() > deadline:
            raise TimeoutError("static fixture backup budget exceeded")

    for key, path in paths.items():
        path.parent.mkdir()
        with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True, timeout=3)) as source:
            with closing(sqlite3.connect(path, timeout=3)) as target:
                source.backup(target, pages=1024, progress=bounded)
                target.execute("PRAGMA foreign_keys=ON")
                target.execute("BEGIN IMMEDIATE")
                target.execute("PRAGMA defer_foreign_keys=ON")
                dataset_id = "official-role-source-" + key
                target.execute("UPDATE dataset SET dataset_id=?", (dataset_id,))
                target.execute("UPDATE dataset_scope SET dataset_id=?,scope=?",
                               (dataset_id, "game" if key == "A" else "reference"))
                maximum = target.execute(
                    "SELECT value FROM equipment_base_attribute_point WHERE curve_id=? ORDER BY level DESC LIMIT 1",
                    (CURVE,),
                ).fetchone()[0]
                target.execute("UPDATE equipment_base_attribute_point SET value=value*? WHERE curve_id=?",
                               ((0.1 if key == "A" else 0.3) / maximum, CURVE))
                target.commit()
                assert target.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                assert target.execute("PRAGMA foreign_key_check").fetchall() == []
        write_release_manifest(path)
    with StaticGameDataDao(paths["B"], reuse_connection=False) as dao:
        schema = dao.schema_version
        template = dao.get_equipment_item("Attack_orange")
        start = dao.evaluate_equipment_base_attribute_curve(CURVE, 1)
    assert template and start is not None

    def item(serial, crit_damage=0.048):
        return {"uid": {"serial": serial, "slot": 17}, "kind": "core", "item_id": template["item_id"],
                "suit_id": template["suit_id"], "quality": template["quality"], "level": 1,
                "max_level": template["max_level"], "locked": False, "equipped": False,
                "main_stats": [{"property_id": "CritBase", "value": start, "percent": True}],
                "sub_stats": [{"property_id": "CritDamageBase", "value": crit_damage, "percent": True}]}

    account = directory / "user.sqlite3"
    with UserDataDao(account, account_id="official-role-source-fixture") as user:
        historical = user.import_inventory_snapshot(
            {"complete": True, "item_count": 2, "items": [item(601), item(602, 0.096)]}, source="nte_core",
        )
        current = user.import_inventory_snapshot(
            {"complete": True, "item_count": 1, "items": [item(901)]}, source="nte_core",
        )
        primary = user.create_loadout_slot(1003, "primary fixture", slot_key="primary")
        secondary = user.create_loadout_slot(1003, "historical fixture", slot_key="secondary")
        for slot, serial, snapshot in [(primary, 901, current), (secondary, 601, historical)]:
            user.save_plan_to_slot(slot, name="fixture", assignments=[{"uid_serial": serial, "uid_slot": 17,
                                                                     "kind": "core"}],
                                   source_snapshot_id=snapshot, score=10,
                                   payload={"assignment_scores": {f"nte-core-17-{serial}": 10}})
    assets = directory / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text(json.dumps({}), encoding="utf-8")
    yield {"paths": paths, "schema": schema, "account": account, "assets": assets,
           "context_key": f"saved:{secondary}", "historical": historical, "current": current,
           "secondary": secondary}
    StaticGameDataDao.close_shared_connections()
    assert digest(original) == original_hash


def load_detail(sources, *, candidates):
    return load_official_role_detail(
        sources["account"], 1003, static_database_path=sources["paths"]["B"],
        static_schema_version=sources["schema"], asset_root=sources["assets"],
        include_replacement_candidates=candidates,
    )


def explicit(detail):
    return {"static_database_path": detail["static_database_path"],
            "static_schema_version": detail["static_schema_version"], "static_release": detail["static_release"]}


def trace_connections(monkeypatch):
    original = sqlite3.connect
    opened = []

    def traced(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.extend(Path(row[2]).resolve() for row in connection.execute("PRAGMA database_list") if row[1] == "main")
        return connection

    # 只包裹真实连接以记录实际 SQLite 路径，不替代 DAO、投影或曲线。
    monkeypatch.setattr(sqlite3, "connect", traced)
    return opened


@pytest.mark.parametrize("environment", ["A", "missing", "absent"])
def test_eager_and_lazy_replacements_use_explicit_B_not_default(sources, monkeypatch, environment):
    if environment == "absent":
        monkeypatch.delenv("NTE_GAME_STATIC_DB", raising=False)
    else:
        path = sources["paths"]["A"] if environment == "A" else sources["paths"]["A"].with_name("missing.sqlite3")
        monkeypatch.setenv("NTE_GAME_STATIC_DB", str(path))
    eager = load_detail(sources, candidates=True)
    light = load_detail(sources, candidates=False)
    light_before = deepcopy(light)
    opened = trace_connections(monkeypatch)
    lazy = load_role_replacement_detail(sources["account"], light["static_database_path"], sources["assets"],
                                       light, sources["context_key"],
                                       static_schema_version=light["static_schema_version"], static_release=light["static_release"])
    assert light == light_before
    outcomes = []
    for detail in (eager, lazy):
        before = deepcopy(detail)
        context = detail["equipment_contexts"][sources["context_key"]]
        assert context["plan"]["source_snapshot_id"] == sources["historical"]
        assert context["slot_id"] == sources["secondary"]
        assert [row["uid_serial"] for row in context["replacement_items"]] == [601, 602]
        target = context["items"][0]
        ranked = replacement_candidates_for_frozen_context(detail, sources["context_key"], target, **explicit(detail))
        assert ranked == replacement_candidates_for_official_role(detail, sources["context_key"], target, **explicit(detail))
        assert len(ranked) == 1 and ranked[0]["item"]["uid_serial"] == 602
        assert next(row["value"] for row in ranked[0]["item"]["main_stats"] if row["property_id"] == "CritBase") == pytest.approx(0.3)
        assert detail == before
        assert not any(key in json.dumps(detail["profile"]) for key in explicit(detail))
        assert not any(key in json.dumps(context) for key in explicit(detail))
        outcomes.append(ranked)
    assert outcomes[0] == outcomes[1]
    assert sources["paths"]["B"] in opened
    assert set(opened) <= {sources["paths"]["B"], sources["account"]}
    with UserDataDao(sources["account"]) as user:
        assert user.current_inventory_snapshot_id() == sources["current"]




@pytest.mark.parametrize("field,value", [("static_database_path", None), ("static_database_path", ""),
                                         ("static_database_path", "relative.sqlite3"), ("static_database_path", True),
                                         ("static_schema_version", None), ("static_schema_version", 0),
                                         ("static_schema_version", True), ("static_schema_version", "39")])
def test_invalid_explicit_source_fails_before_guard_or_dao(sources, monkeypatch, field, value):
    detail = load_detail(sources, candidates=False)
    options = explicit(detail)
    options[field] = value
    opened = trace_connections(monkeypatch)
    for function in (replacement_candidates_for_frozen_context, replacement_candidates_for_official_role):
        with pytest.raises(ValueError):
            function(detail, "unknown", {}, **options)
    with pytest.raises(ValueError):
        load_role_replacement_detail(sources["account"], options["static_database_path"], sources["assets"],
                                    detail, "unknown", static_schema_version=options["static_schema_version"], static_release=detail["static_release"])
    assert opened == []


@pytest.mark.parametrize("change", ["missing_path", "missing_schema", "different_path", "different_schema"])
def test_detail_metadata_mismatch_fails_before_guard_and_keeps_input(sources, monkeypatch, change):
    detail = load_detail(sources, candidates=False)
    options = explicit(detail)
    if change.startswith("missing_"):
        detail.pop("static_database_path" if change == "missing_path" else "static_schema_version")
    elif change == "different_path":
        detail["static_database_path"] = str(sources["paths"]["A"])
    else:
        detail["static_schema_version"] += 1
    before = deepcopy(detail)
    opened = trace_connections(monkeypatch)
    for function in (replacement_candidates_for_frozen_context, replacement_candidates_for_official_role):
        with pytest.raises(ValueError):
            function(detail, "unknown", {}, **options)
    assert detail == before and opened == []


def test_agreed_wrong_schema_is_rejected_by_real_dao_and_retry_succeeds(sources):
    detail = load_detail(sources, candidates=True)
    target = detail["equipment_contexts"][sources["context_key"]]["items"][0]
    incorrect = deepcopy(detail)
    incorrect["static_schema_version"] += 1
    incorrect["static_release"] = replace(incorrect["static_release"], schema_version=incorrect["static_schema_version"])
    with pytest.raises(StaticCatalogReleaseError):
        replacement_candidates_for_frozen_context(incorrect, sources["context_key"], target, **explicit(incorrect))
    with pytest.raises(StaticCatalogReleaseError):
        load_role_replacement_detail(sources["account"], incorrect["static_database_path"], sources["assets"],
                                    incorrect, sources["context_key"],
                                    static_schema_version=incorrect["static_schema_version"], static_release=incorrect["static_release"])
    assert replacement_candidates_for_frozen_context(detail, sources["context_key"], target, **explicit(detail))




def test_save_transports_no_source_metadata_and_preserves_historical_plan(sources, tmp_path):
    account = tmp_path / "user.sqlite3"
    with sqlite3.connect(sources["account"].as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(account) as copy:
            original.backup(copy)
    detail = load_official_role_detail(account, 1003, static_database_path=sources["paths"]["B"],
                                       static_schema_version=sources["schema"], asset_root=sources["assets"])
    context = detail["equipment_contexts"][sources["context_key"]]
    old = deepcopy(context["plan"])
    target = context["items"][0]
    row = replacement_candidates_for_official_role(detail, sources["context_key"], target, **explicit(detail))[0]
    plan_id = save_official_role_replacement(account, detail, target, row["item"], context_key=sources["context_key"],
                                            replacement_score=31, current_assignment_scores={"nte-core-17-601": 25})
    with UserDataDao(account) as user:
        original = user.get_loadout_plan(old["plan_id"])
        replacement = user.get_loadout_plan(plan_id)
        assert original["payload"] == old["payload"] and original["score"] == old["score"]
        assert replacement["slot_id"] == old["slot_id"] == sources["secondary"]
        assert replacement["source_snapshot_id"] == sources["historical"]
        assert replacement["payload"]["assignment_scores"] == {"nte-core-17-602": 31}
        assert not any(key in json.dumps(replacement) for key in explicit(detail))


def test_battle_prepare_reset_and_frozen_candidate_keep_only_detail_source_carrier(sources):
    from src.services.battle_report.marginal.candidates import BattleMarginalCandidateService

    detail = load_detail(sources, candidates=True)
    detail["original_profile"] = deepcopy(detail["profile"])
    detail["equipment_contexts"]["battle"] = deepcopy(detail["equipment_contexts"][sources["context_key"]])
    editor = {"is_active": False, "details": [detail]}
    original = deepcopy(editor)
    prepared = BattleMarginalCandidateService.prepare_editor_data(editor, equipment_editable=True)
    candidate_detail = prepared["details"][0]
    assert explicit(candidate_detail) == explicit(detail)
    target = candidate_detail["equipment_contexts"]["candidate"]["items"][0]
    rows = replacement_candidates_for_frozen_context(candidate_detail, "candidate", target, **explicit(candidate_detail))
    assert rows and rows[0]["item"]["uid_serial"] == 602
    BattleMarginalCandidateService.replace_equipment(candidate_detail["equipment_contexts"]["candidate"],
                                                     target, rows[0]["item"])
    restored = BattleMarginalCandidateService.prepare_editor_data(editor, equipment_editable=True)
    assert restored["details"][0]["equipment_contexts"]["candidate"]["items"][0]["uid_serial"] == 601
    assert explicit(restored["details"][0]) == explicit(detail) and editor == original
    frozen = BattleMarginalCandidateService.freeze(77, [candidate_detail["profile"]], equipment_editable=True)
    assert not any(key in json.dumps(frozen.profiles) for key in explicit(detail))


def qt_account_context(sources, tmp_path, key):
    from src.app.context import AccountContext, AppContext, ApplicationPaths
    paths = ApplicationPaths.from_roots(root=ROOT, app_dir=tmp_path, data_root=tmp_path,
        bundled_config_dir=ROOT / "config", asset_dir=sources["assets"].with_name("app-assets-not-game-ui"),
        app_icon_path=tmp_path / "unused.ico", static_database_path=sources["paths"][key])
    account = AccountContext("official-role-source-fixture", "fixture", tmp_path, sources["account"],
                             tmp_path / "config", tmp_path / "shots", tmp_path / "logs")
    return AppContext(paths, account)


def test_plan_optimizer_passes_single_selected_static_source_not_app_assets(sources, monkeypatch, tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QWidget
    import src.features.inventory.equipment.plan_optimizer as optimizer
    import src.features.official_role.replacement_controller as popup

    monkeypatch.setenv("NTE_GAME_STATIC_DB", str(sources["paths"]["A"]))
    with UserDataDao(sources["account"]) as user:
        plan = next(row["plan"] for row in user.list_current_loadout_slot_plans()
                    if row["slot"]["slot_id"] == sources["secondary"])
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.app_context = qt_account_context(sources, tmp_path, "B")
    window.show()  # 正式异步 owner 只向仍可见且同账号的 Qt parent 交付。
    app.processEvents()
    observed = []
    monkeypatch.setattr(popup, "show_official_role_replacement",
                        lambda owner, detail, target, **options: observed.append((detail, target, options)))
    try:
        assert optimizer._open_official_saved_plan_optimizer(window, "fixture", "tape", "nte-core-17-601",
                                                             plan_id=plan["plan_id"])
        detail, target, options = observed[0]
        assert explicit(detail) == {"static_database_path": str(sources["paths"]["B"]),
                                    "static_schema_version": sources["schema"], "static_release": detail["static_release"]}
        assert {key: options[key] for key in explicit(detail)} == explicit(detail)
        assert target["uid_serial"] == 601 and options["context_key"] == sources["context_key"]
    finally:
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_frozen_view_keeps_main_icons_unknown_states_and_default_false(sources, tmp_path, monkeypatch):
    from src.features.inventory.warehouse.cards import warehouse_item_view
    from src.services.equipment.tape_main_value import full_level_tape_main_value
    from src.services.allocation.inventory import legacy_stat_name

    projected = {"kind": "core", "quality": "orange", "item_id": "Attack_orange", "suit_id": "Suit8",
                 "uid_serial": 601, "uid_slot": 17, "main_stats": [{"property_id": "CritBase", "value": 0.1,
                                                                     "percent": True}],
                 "sub_stats": [], "level": 20, "max_level": 20, "locked": True, "discarded": True,
                 "equipped": True, "equipped_character_id": 1003, "equipped_character_name": "fixture",
                 "item_icon_path": tmp_path / "frozen-item.png", "equipped_character_icon_path": tmp_path / "frozen-owner.png"}
    before = deepcopy(projected)
    default_main = full_level_tape_main_value(legacy_stat_name("CritBase"), "orange")
    assert default_main != 10
    implicit = warehouse_item_view(projected, static_database_path=sources["paths"]["B"])
    false = warehouse_item_view(projected, static_database_path=sources["paths"]["B"], frozen_projection=False)
    assert implicit == false and "frozen_projection" not in false
    assert false["main_stats"][0]["value"] == f"+{default_main:g}%"
    opened = trace_connections(monkeypatch)
    native = warehouse_item_view(projected, frozen_projection=True)
    assert native["main_stats"][0]["value"] == "+10%"
    assert native["item_icon_path"] == projected["item_icon_path"]
    assert native["equipped_character_icon_path"] == projected["equipped_character_icon_path"]
    assert native["locked"] and native["discarded"] and native["equipped"] and native["state_known"]
    unknown = {**projected, "main_stats": None, "item_icon_path": None, "equipped_character_icon_path": None,
               "level_known": False, "virtual": True}
    visual = warehouse_item_view(unknown, source="vision", frozen_projection=True)
    assert visual["main_stats"] == [] and visual["item_icon_path"] is None and visual["equipped_character_icon_path"] is None
    assert not visual["state_known"] and not visual["level_known"]
    assert not visual["locked"] and not visual["discarded"] and not visual["equipped"]
    assert visual["virtual"] and visual["uid"] == native["uid"] == "nte-core-17-601"
    assert projected == before and opened == []


@pytest.mark.parametrize("environment", ["A", "absent"])
def test_actual_prepared_dialog_paints_frozen_A_with_cold_caches_and_no_source_reopen(sources, monkeypatch, environment, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    from src.features.inventory.warehouse.result_card import WarehouseResultCard
    from src.features.official_role.replacement_controller import show_official_role_replacement
    from src.optimizer.scoring import ScoringEngine
    from src.services.inventory.warehouse_visual_catalog import representative_core_item_id, representative_module_item_id
    from src.services.equipment.tape_main_value import max_level_tape_main_values

    app = QApplication.instance() or QApplication([])
    detail = load_official_role_detail(sources["account"], 1003, static_database_path=sources["paths"]["A"],
                                       static_schema_version=sources["schema"], asset_root=sources["assets"])
    target = detail["equipment_contexts"][sources["context_key"]]["items"][0]
    rows = replacement_candidates_for_official_role(detail, sources["context_key"], target, **explicit(detail))
    window = QWidget()
    window.app_context = qt_account_context(sources, tmp_path, "A")
    window.show()
    app.processEvents()
    window.scoring_engine = ScoringEngine(config_dir=ROOT / "config", roles_db={})
    if environment == "A":
        monkeypatch.setenv("NTE_GAME_STATIC_DB", str(sources["paths"]["A"]))
    else:
        monkeypatch.delenv("NTE_GAME_STATIC_DB", raising=False)
    representative_core_item_id.cache_clear()
    representative_module_item_id.cache_clear()
    max_level_tape_main_values.cache_clear()
    StaticGameDataDao.close_shared_connections()
    opened = trace_connections(monkeypatch)
    observed = []

    def render_then_cancel():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QDialog) and dialog.parentWidget() is window:
            for card in dialog.findChildren(WarehouseResultCard):
                observed.append({"view": deepcopy(card.item_view), "rendered": not card.grab().isNull()})
            dialog.reject()
        else:
            # 只关闭本 fixture owner 的弹窗；完整失败由外层断言传播。
            for owned in window.findChildren(QDialog):
                owned.reject()

    QTimer.singleShot(0, render_then_cancel)
    try:
        assert not show_official_role_replacement(window, detail, target, prepared_candidates=rows,
                                                  context_key=sources["context_key"], **explicit(detail))
        assert len(observed) >= 2 and all(row["rendered"] for row in observed)
        assert all(row["view"]["frozen_projection"] and row["view"]["main_stats"][0]["value"] == "+10%"
                   and row["view"]["item_icon_path"] is None for row in observed)
        assert opened == []
        assert all("frozen_projection" not in row["item"] for row in rows)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("kind", ["core", "module"])
@pytest.mark.parametrize("custom", [False, True])
def test_actual_frozen_card_paint_keeps_missing_icons_and_owner_identity(sources, monkeypatch, kind, custom):
    from PySide6.QtWidgets import QApplication
    from src.features.inventory.warehouse import cards
    from src.features.inventory.warehouse.result_card import WarehouseResultCard
    from src.services.inventory.warehouse_visual_catalog import representative_core_item_id, representative_module_item_id

    app = QApplication.instance() or QApplication([])
    representative_core_item_id.cache_clear()
    representative_module_item_id.cache_clear()
    cards._legacy_character_avatar.cache_clear()
    cards._role_avatar_index.cache_clear()
    StaticGameDataDao.close_shared_connections()
    monkeypatch.setenv("NTE_GAME_STATIC_DB", str(sources["paths"]["A"]))
    opened = trace_connections(monkeypatch)
    item = {"kind": kind, "quality": "orange", "uid_serial": 602, "uid_slot": 17, "main_stats": None,
            "geometry": "Hen3", "suit_id": "Suit8", "sub_stats": [], "item_icon_path": None,
            "equipped": True, "equipped_character_id": 1003, "equipped_character_name": "fixture missing portrait",
            "equipped_character_icon_path": None, "equipped_character_is_custom": custom}
    view = cards.warehouse_item_view(item, frozen_projection=True)
    card = WarehouseResultCard(view, score=None, grade=None, direct_damage_score=None)
    try:
        card.show()
        app.processEvents()
        assert not card.grab().isNull() and opened == []
        assert card.item_view["item_icon_path"] is None and card.item_view["equipped_character_icon_path"] is None
        assert card.item_view["equipped_character_is_custom"] is custom
        assert cards._legacy_character_avatar.cache_info().misses == 0
        assert representative_core_item_id.cache_info().misses == representative_module_item_id.cache_info().misses == 0
    finally:
        card.close()
        card.deleteLater()
        app.processEvents()
