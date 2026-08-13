# 验证角色配装缓存与账号快照的集成边界。
from __future__ import annotations

from src.features.official_role import role_equipment


def test_role_replacement_invalidates_loadout_cache_before_refresh(
    monkeypatch,
) -> None:
    events = []

    class Window:
        official_role_tabs = None

        def refresh_saved_equipment_after_mutation(self):
            events.extend(("invalidate", "loadout_refresh"))

        def _refresh_my_role(self, *, restore_scroll_value):
            events.append(("role_refresh", restore_scroll_value))

    def fake_replacement(_window, _detail, _target, *, on_saved):
        on_saved()
        return True

    monkeypatch.setattr(
        role_equipment,
        "show_official_role_replacement",
        fake_replacement,
    )

    role_equipment._show_replacement_optimizer(Window(), {}, {})

    assert events == [
        "invalidate",
        "loadout_refresh",
        ("role_refresh", None),
    ]
