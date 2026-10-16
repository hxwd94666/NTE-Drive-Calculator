# 验证手工与文本剪贴板沿同一冻结资料解析、派发，变化或取消不覆盖已有结果。
from dataclasses import replace
import json
import sqlite3
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QTextEdit

from src.domain.stat_catalog import StatCatalog
from src.features.identification import controller, task_controller
from src.features.identification.dependencies import IdentificationDependencies
from src.features.identification.manual_parsing import IdentificationManualParsingMixin
from src.features.identification.task_controller import IdentificationTaskController
from src.optimizer.scoring import ScoringEngine
from src.services.identification import tasks
from src.services.identification.matching_settings import matching_settings_identity, normalize_matching_settings
from src.services.recognition.resources import RecognitionResourceError
from tests.vision.test_recognition_resources import selected as selected

NTE_TEST_TIER = "core"


def test_manual_names_values_and_quality_use_explicit_catalog():
    parser = IdentificationManualParsingMixin()
    catalog = StatCatalog.from_data({"gold_base_values": {
        "攻击力%": 1.25, "暴击率%": 1.4, "暴击伤害%": 2.0, "伤害增加%": 1.0,
        "生命值": 100.0, "生命值%": 2.0, "防御力": 3.0, "攻击力": 4.0,
        "防御力%": 2.5, "倾陷强度": 5.0, "环合强度": 6.0,
    }, "stat_alias_mapping": {"夹具别名": "环合强度"}})
    # 同时保护分隔符、数字忽略、两类别名；无需四套页面评分器夹具。
    text = "攻击力10% 大攻击,攻击力百分比 百分比攻击力 暴击，爆击 爆伤\n暴伤; 通用伤害 倾陷 环合 夹具别名"
    assert parser._parse_manual_stats(text, stat_catalog=catalog) == {
        name: catalog.gold_base_values[name] for name in (
            "攻击力%", "暴击率%", "暴击伤害%", "伤害增加%", "倾陷强度", "环合强度")}
    assert set(parser._parse_manual_stats(
        "大生命 防御 攻击 生命值% 防御力% 攻击力%", stat_catalog=catalog,
    )) == {"生命值%", "防御力", "攻击力", "防御力%", "攻击力%"}
    assert parser._parse_manual_stats("攻击力% 暴伤", quality="Purple", grid_equivalent=2,
                                     stat_catalog=catalog) == {"攻击力%": 2.0, "暴击伤害%": 3.2}
    assert parser._parse_manual_stats("生命值 通伤", quality="Purple", grid_equivalent=10,
                                     stat_catalog=catalog) == {"生命值": 800.0, "伤害增加%": 8.0}
    assert parser._manual_drive_main_stats(3, "Purple") == {"攻击力": 50.4, "生命值": 672.0}


@pytest.fixture
def manual_host(selected, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    config, database = selected
    deps = IdentificationDependencies("fixture", 1, tmp_path, tmp_path / "shots", config,
                                     tmp_path / "user.sqlite3", config, database)
    current = [deps]
    owner = IdentificationTaskController(lambda: current[0])
    host = SimpleNamespace(_identify_tasks=owner, _identify_task=None, _identify_dependencies=None,
        _dialog_parent=None, _shape_areas={"H_2": 2}, all_set_names=["夹具套装"],
        scoring_engine=ScoringEngine(config, roles_db={}), _set_identify_busy=Mock(),
        is_running=owner.is_running, ident_manual_text=QTextEdit(), ident_path_edit=QLineEdit(),
        ident_tape_rb=SimpleNamespace(isChecked=lambda: False),
        ident_drive_rb=SimpleNamespace(setChecked=lambda _value: None),
        _identify_result_pages=[{"fixture": "previous result"}])
    for name, value in (("shape", "H_2"), ("quality", "Gold"), ("set", "夹具套装"), ("main", "攻击力%")):
        combo = QComboBox(); combo.addItem(value, value)
        setattr(host, f"ident_{name}_combo", combo)
    for name in ("_identify_start", "_identify_from_manual", "_identify_from_clipboard",
                 "_start_identify_items", "_apply_identify_manual_fields", "_manual_tokens",
                 "_manual_value", "_resolve_stat_name", "_parse_manual_stats",
                 "_combo_data_or_resolved_text", "_identify_quality", "_on_identify_type_changed",
                 "_get_tape_main_stats_pool", "_set_combo_data", "_identify_paths_from_text"):
        setattr(host, name, MethodType(getattr(controller.IdentificationController, name), host))
    host._manual_drive_main_stats = controller.IdentificationController._manual_drive_main_stats
    submissions, errors = [], []
    monkeypatch.setattr(controller, "_current_identification_dependencies", lambda _owner: current[0])
    monkeypatch.setattr(controller, "_begin_identification_operation", Mock())
    monkeypatch.setattr(controller, "_identification_event", Mock())
    monkeypatch.setattr(controller.QMessageBox, "critical", lambda *_args: errors.append("invalid resources"))
    monkeypatch.setattr(owner._lane, "submit", submissions.append)
    host.ident_manual_text.setPlainText("攻击力%")
    try:
        yield host, current, submissions, errors
    finally:
        owner.close()
        for value in vars(host).values():
            if isinstance(value, (QComboBox, QLineEdit, QTextEdit)):
                value.deleteLater()
        app.processEvents()


def change_base(config):
    path = config / "stats.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["gold_base_values"]["攻击力%"] = 7.0
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(("source", "grid"), [("manual", 2), ("clipboard", 10)])
@pytest.mark.parametrize("change", ["before_parse", "stats_after_parse", "dataset_after_parse"])
def test_manual_and_clipboard_share_parse_and_match_identity(manual_host, monkeypatch, source, grid, change):
    host, current, submissions, errors = manual_host
    deps = current[0]
    if source == "clipboard":
        # 仅读合成剪贴板，不触及真实剪贴板；卡带分支也消费同一目录。
        host.ident_tape_rb.isChecked = lambda: True
        clipboard = SimpleNamespace(mimeData=lambda: None, text=lambda: "攻击力%")
        monkeypatch.setattr(controller, "QApplication", SimpleNamespace(clipboard=lambda: clipboard))
        host._identify_from_clipboard()
    if change == "before_parse":
        change_base(deps.config_dir)
    host._identify_start()
    assert not errors and len(submissions) == 1
    read = submissions[0].read
    request = read.args[0]
    resources = request.resources
    assert resources is host._identify_tasks.resources_for(host._identify_task)
    assert resources is not None and resources.image_profile is None
    base = 7.0 if change == "before_parse" else 3.0
    assert request.items[0].sub_stats == {"攻击力%": grid * base}
    assert resources.stat_catalog().gold_base_values["攻击力%"] == base
    # 刻意保留页面旧评分器，证明实际数值不再借用它。
    assert host.scoring_engine.gold_base_values["攻击力%"] == 3.0
    settings = normalize_matching_settings(None)
    factory = Mock(return_value=SimpleNamespace(identify_items=lambda items, **_kwargs: [{"item": item, "rows": []} for item in items],
        context=SimpleNamespace(settings_identity=matching_settings_identity(settings))))
    # 本例隔离解析资料，不创建账号库；替身仍遵守匹配器的冻结配置身份契约。
    monkeypatch.setattr(tasks.MatchingSettingsService, "load", lambda _self: settings)
    monkeypatch.setattr(tasks.EquipmentIdentificationService, "from_paths", factory)
    if change == "before_parse":
        assert read()[0]["item"].sub_stats == {"攻击力%": grid * base}
        assert factory.call_args.kwargs["resources"] is resources
    else:
        if change == "stats_after_parse":
            change_base(deps.config_dir)
        else:
            with sqlite3.connect(deps.static_database_path) as database:
                database.execute("UPDATE dataset SET dataset_id='changed-dataset'")
            database.close()
        with pytest.raises(RecognitionResourceError):
            read()
        factory.assert_not_called()
    assert host.ident_manual_text.toPlainText() == "攻击力%"
    assert host._identify_result_pages == [{"fixture": "previous result"}]
    assert not deps.user_database_path.exists()


@pytest.mark.parametrize("failure", ["invalid_stats", "generation", "cancel"])
def test_manual_freeze_failure_preserves_input_and_results(manual_host, monkeypatch, failure):
    host, current, submissions, errors = manual_host
    deps = current[0]
    if failure == "invalid_stats":
        (deps.config_dir / "stats.json").write_text("{}", encoding="utf-8")
    else:
        load = task_controller.load_recognition_resources
        def changed(*args):
            resources = load(*args)
            if failure == "generation":
                current[0] = replace(deps, generation=2)
            else:
                host._identify_tasks.cancel()
            return resources
        monkeypatch.setattr(task_controller, "load_recognition_resources", changed)
    host._identify_start()
    assert not submissions
    assert bool(errors) == (failure == "invalid_stats")
    assert host._identify_tasks.resources_for(host._identify_task) is None
    assert host.ident_manual_text.toPlainText() == "攻击力%"
    assert host._identify_result_pages == [{"fixture": "previous result"}]
    assert not deps.user_database_path.exists()
