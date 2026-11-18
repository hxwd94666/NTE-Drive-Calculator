# 验证缺项好感仍保留账号选择，面板明确显示未知，用户关闭后才恢复已知数值。
from types import SimpleNamespace

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox
import pytest

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.characters.official.test_likeability_source_gap import gap


NTE_TEST_TIER = "core"


@pytest.mark.parametrize("theme", ["black"])
def test_unknown_likeability_choice_is_not_silently_cleared(monkeypatch, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    from src.domain.official_role.models import WorldBonusSettings
    from src.features.official_role.editing import growth_fields
    app = application()
    previous = current_theme_name(app)
    apply_app_theme(app, theme)
    monkeypatch.setattr(growth_fields, "_refresh_role_calculations", lambda _editor: None)
    window = SimpleNamespace(_official_role_dirty_ids=set(), _my_role_dirty=False)
    profile = {"character_level": 80, "breakthrough_stage": 6, "likeability_level_10_enabled": True}
    detail = {"character": {"character_id": 1090, "name_zh": "合成角色"}, "profile": profile,
              "world_bonus": WorldBonusSettings(yaodao_attack_add=0, quantao_crit_damage=0),
              "likeability_bonus": gap(), "growth_rows": [
                  {"level": 80, "breakthrough_stage": 6, "hp_base": 1000, "atk_base": 100, "def_base": 100}]}
    editor = {"detail": detail}
    group = growth_fields._build_base_group(window, 1090, detail, editor)
    try:
        group.show()
        checkbox = group.findChild(QCheckBox)
        stats = group.findChildren(QDoubleSpinBox)
        assert checkbox.isChecked() and checkbox.isEnabled()
        assert "缺失" in checkbox.text()
        assert "关闭后不计此项" in checkbox.toolTip()
        assert len(stats) == 5 and all("未知" in field.text() for field in stats)
        assert window._official_role_dirty_ids == set()
        checkbox.setChecked(False)
        assert all("未知" not in field.text() for field in stats)
        assert {1000, 100, 5, 50} <= {field.value() for field in stats}
        assert window._official_role_dirty_ids == {1090}
        checkbox.setChecked(True)
        assert all("未知" in field.text() for field in stats)
        assert profile["likeability_level_10_enabled"] is True
    finally:
        dispose(group)
        apply_app_theme(app, previous)
