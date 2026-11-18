# 验证账号队伍的持久化、升级、失败回滚、过期提交及头像选择行为。
import sqlite3

import pytest

from src.domain.teams.models import TeamDraft, TeamRole
from src.services.teams.management import TeamService, TeamSessionExpired
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite import user_data_support


def service(tmp_path, account="one", current=lambda: True):
    path = tmp_path / f"{account}.sqlite3"
    with UserDataDao(path, account_id=account):
        pass
    return TeamService(account_id=account, database_path=path, static_database_path=None,
                       asset_root=None, context_is_current=current,
                       role_loader=lambda: tuple(TeamRole(i, f"角色{i}") for i in range(1, 7)))


def test_team_crud_and_account_isolation(tmp_path):
    first, other = service(tmp_path), service(tmp_path, "two")
    draft = first.save(TeamDraft("常用队", ()))
    assert first.list_teams() == (draft,)
    assert other.list_teams() == ()
    full = first.save(TeamDraft("残虹队", (1, 2, 3, 4)), existing=draft)
    backup = first.save(TeamDraft("备用队", (1, 2, 5, 6)))
    assert full.team_id == draft.team_id and full.revision == 2
    assert full.character_ids == (1, 2, 3, 4)
    assert backup.team_id != full.team_id  # 不同备用队伍可以共享角色。
    with pytest.raises(ValueError, match="已变更"):
        first.save(TeamDraft("过期修改", (1,)), existing=draft)
    first.delete(full)
    assert first.list_teams() == (backup,)


@pytest.mark.parametrize("draft", [TeamDraft(" ", ()), TeamDraft("队", (1, 1)),
                                   TeamDraft("队", (1, 2, 3, 4, 5)), TeamDraft("队", (99,))])
def test_invalid_team_does_not_replace_saved_members(tmp_path, draft):
    store = service(tmp_path)
    saved = store.save(TeamDraft("原队伍", (1, 2)))
    with pytest.raises(ValueError):
        store.save(draft, existing=saved)
    assert store.list_teams() == (saved,)


def test_expired_account_and_wrong_database_cannot_write(tmp_path):
    alive = [True]
    store = service(tmp_path, current=lambda: alive[0])
    saved = store.save(TeamDraft("旧账号", (1,)))
    alive[0] = False
    with pytest.raises(TeamSessionExpired):
        store.save(TeamDraft("不能保存", (2,)), existing=saved)
    alive[0] = True
    store.account_id = "different"
    with pytest.raises(TeamSessionExpired):
        store.delete(saved)


def test_member_write_failure_rolls_back_name_revision_and_members(tmp_path):
    store = service(tmp_path)
    saved = store.save(TeamDraft("原队伍", (1,)))
    with sqlite3.connect(store.database_path) as db:
        db.execute("CREATE TRIGGER fail_member BEFORE INSERT ON team_member "
                   "WHEN NEW.character_id = 2 BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.save(TeamDraft("新名称", (2,)), existing=saved)
    assert store.list_teams() == (saved,)
    assert store.save(TeamDraft("重试成功", (3,)), existing=saved).revision == 2


def test_v45_upgrade_preserves_existing_data(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base
    path = tmp_path / "old.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 45)
        patch.setattr(user_data_base, "USER_MIGRATIONS", {
            k: v for k, v in user_data_support.USER_MIGRATIONS.items() if k <= 45})
        with UserDataDao(path, account_id="old") as dao:
            assert dao.profile()["account_id"] == "old"
    with UserDataDao(path) as dao:
        assert dao.profile()["account_id"] == "old"
        assert dao.list_teams() == ()
        assert dao._db().execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == user_data_support.SCHEMA_VERSION


def test_failed_migration_rolls_back_and_can_retry(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base
    path = tmp_path / "retry.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 45)
        patch.setattr(user_data_base, "USER_MIGRATIONS", {
            k: v for k, v in user_data_support.USER_MIGRATIONS.items() if k <= 45})
        with UserDataDao(path, account_id="retry"):
            pass
    broken = tmp_path / "broken.sql"
    broken.write_text(user_data_support.USER_MIGRATIONS[46].read_text(encoding="utf-8") +
                      "\nINSERT INTO table_that_does_not_exist VALUES (1);", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setitem(user_data_base.USER_MIGRATIONS, 46, broken)
        with pytest.raises(RuntimeError, match="无法升级"):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 45
        assert db.execute("SELECT name FROM sqlite_master WHERE name='team'").fetchone() is None
    with UserDataDao(path) as dao:
        assert dao.list_teams() == ()


def test_editor_selection_limit_search_and_cancel():
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from src.features.teams.editor import TeamEditor
    application()
    roles = tuple(TeamRole(i, name) for i, name in enumerate(
        ("残虹", "安魂曲", "明音澪", "娜娜莉", "零"), 1))
    editor = TeamEditor(roles=roles)
    try:
        editor.show()
        editor.name_edit.setText("双人起步")
        for key in (1, 2, 3, 4):
            editor.role_buttons[key].click()
        assert editor.draft().character_ids == (1, 2, 3, 4)
        assert not editor.role_buttons[5].isEnabled()
        editor.search.setText("canhong")
        assert not editor.role_buttons[1].isHidden()
        assert editor.role_buttons[2].isHidden()
        editor.member_buttons[0].click()
        assert editor.draft().character_ids == (2, 3, 4)
        assert editor.role_buttons[5].isEnabled()
        editor.reject()
        assert editor.result() == 0
    finally:
        dispose(editor)


def test_controller_save_reopen_and_account_reset(tmp_path):
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from src.features.teams.controller import TeamController
    application()
    store = service(tmp_path)
    controller = TeamController(service_provider=lambda: store)
    try:
        controller.page.show()
        controller.create()
        dialog = controller._dialog
        dialog.name_edit.setText("常用队")
        dialog.role_buttons[1].click()
        dialog.save_requested.emit()
        saved, = store.list_teams()
        assert saved.character_ids == (1,)
        controller.edit(saved.team_id)
        assert controller._dialog.draft().name == "常用队"
        controller._dialog.name_edit.setText("未提交的修改")
        controller.reset_account()
        assert controller._dialog is None
        assert store.list_teams() == (saved,)
        assert controller.page._teams == ()
    finally:
        controller.reset_account()
        dispose(controller.page)
        controller.deleteLater()


@pytest.mark.parametrize("theme", ["black"])
def test_team_editor_actions_remain_reachable_on_small_screen(theme):
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtWidgets import QDialogButtonBox
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from src.app.theme import apply_app_theme
    from src.app.window_geometry import fit_dialog_to_available_screen
    from src.features.teams.editor import TeamEditor
    app = application()
    apply_app_theme(app, theme)
    editor = TeamEditor(roles=tuple(TeamRole(i, f"角色{i}") for i in range(1, 25)))
    try:
        fit_dialog_to_available_screen(editor, QSize(800, 620), available_geometry=QRect(0, 0, 640, 600))
        editor.show()
        app.processEvents()
        editor.name_edit.setText("小窗口队伍")
        assert editor.width() <= 592 and editor.height() <= 552
        for button in editor.member_buttons + [editor.buttons.button(QDialogButtonBox.Save)]:
            assert editor.rect().contains(button.mapTo(editor, button.rect().topLeft()))
            assert editor.rect().contains(button.mapTo(editor, button.rect().bottomRight()))
        cards = list(editor.role_buttons.values())
        assert all(not first.geometry().intersects(second.geometry())
                   for index, first in enumerate(cards) for second in cards[index + 1:])
        editor.role_buttons[1].setFocus()
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        QTest.keyClick(editor.role_buttons[1], Qt.Key_Space)
        assert editor.draft().character_ids == (1,)
    finally:
        dispose(editor)
