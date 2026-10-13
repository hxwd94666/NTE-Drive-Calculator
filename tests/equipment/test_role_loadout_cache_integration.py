# 验证角色配装缓存与账号快照的集成边界。
from __future__ import annotations

from src.features.official_role import role_equipment


def test_role_replacement_invalidates_loadout_cache_before_refresh(
    monkeypatch,
) -> None:
    events = []
    # 此处只替身共享控制器；冻结字段必须原样跨适配边界传递。
    release = object()
    detail = {
        "static_database_path": "fixture-static.sqlite3",
        "static_schema_version": 1,
        "static_release": release,
    }

    class Window:
        official_role_tabs = None

        def refresh_saved_equipment_after_mutation(self):
            events.extend(("invalidate", "loadout_refresh"))

    def fake_replacement(
        _window, prepared_detail, _target, *, on_saved,
        static_database_path, static_schema_version, static_release,
    ):
        assert prepared_detail is detail
        assert static_database_path == detail["static_database_path"]
        assert static_schema_version == detail["static_schema_version"]
        assert static_release is release
        on_saved()
        return True

    monkeypatch.setattr(
        role_equipment,
        "show_official_role_replacement",
        fake_replacement,
    )
    monkeypatch.setattr(
        role_equipment,
        "_refresh_role_page_after_replacement",
        lambda _window, *, restore_scroll_value: events.append(
            ("role_refresh", restore_scroll_value)
        ),
    )

    role_equipment._show_replacement_optimizer(Window(), detail, {})

    assert events == [
        "invalidate",
        "loadout_refresh",
        ("role_refresh", None),
    ]
