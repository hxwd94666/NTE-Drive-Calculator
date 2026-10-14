# 验证语言目录、原子安装、本机偏好与账号生命周期，全部使用合成临时文件。
from __future__ import annotations

import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.domain.localization.catalog import CatalogError
from src.integrations.preferences import json_store
from src.integrations.preferences.json_store import LocalPreferencesStore
from src.integrations.preferences.locale_files import LocaleFiles, MAX_CATALOG_BYTES
from src.services.preferences.global_theme_settings_service import GlobalThemeSettingsService
from src.services.preferences.language_settings_service import LanguageSettingsService

ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = "core"


@pytest.fixture
def service(tmp_path):
    files = LocaleFiles(ROOT / "assets/locales", tmp_path / "config/locales")
    result = LanguageSettingsService(LocalPreferencesStore(tmp_path / "config/global_ui_preferences.json"), files)
    result.startup()
    return result


def payload(**messages):
    return {"format_version": 1, "catalog_id": "nte-ui", "locale": "en-US",
            "display_name": "English sample", "messages": messages}


def write_patch(tmp_path, value):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_partial_catalog_falls_back_and_named_values_are_literal(service, tmp_path):
    patch = write_patch(tmp_path, payload(**{"settings.language.saved": "Saved {language}"}))
    catalog = service.install(patch)
    assert catalog.render("settings.language.saved", language="{plain}") == "Saved {plain}"
    assert catalog.render("settings.language.save") == "确认语言"
    with pytest.raises(CatalogError, match="parameter_mismatch"):
        catalog.render("settings.language.saved", count=1)
    assert not service.preferences.path.exists()  # 安装不隐式改变选择。


def test_rejected_patch_keeps_valid_file_and_preference(service, tmp_path):
    path = write_patch(tmp_path, payload(**{"settings.language.saved": "Saved {language}"}))
    service.install(path)
    service.select("en-US")
    old_patch = (service.files.patch_dir / "en-US.json").read_bytes()
    old_preferences = service.preferences.path.read_bytes()
    # 共用同一有效原件逐项验证，避免为七个坏字符串重复创建整套服务和文件。
    for key, text, code in [
        ("settings.language.saved", "{language.name}", "invalid_placeholder"),
        ("settings.language.saved", "{language[0]}", "invalid_placeholder"),
        ("settings.language.saved", "{language!r}", "invalid_placeholder"),
        ("settings.language.saved", "{language:{other}}", "invalid_placeholder"),
        ("settings.language.saved", "{}", "invalid_placeholder"),
        ("settings.language.saved", "{count}", "parameter_mismatch"),
        ("unknown.message", "text", "unknown_key"),
    ]:
        path = write_patch(tmp_path, payload(**{key: text}))
        with pytest.raises(CatalogError, match=code):
            service.install(path)
        assert (service.files.patch_dir / "en-US.json").read_bytes() == old_patch, text
        assert service.preferences.path.read_bytes() == old_preferences, text


def test_duplicate_compatibility_size_and_reserved_paths_are_rejected(service, tmp_path):
    path = tmp_path / "candidate.json"
    path.write_text('{"locale":"en-US","locale":"zh-CN"}', encoding="utf-8")
    with pytest.raises(CatalogError, match="duplicate_key"):
        service.install(path)
    wrong = payload(); wrong["format_version"] = 2
    with pytest.raises(CatalogError, match="unsupported_format"):
        service.install(write_patch(tmp_path, wrong))
    path.write_bytes(b" " * (MAX_CATALOG_BYTES + 1))
    with pytest.raises(CatalogError, match="too_large"):
        service.install(path)
    for locale in ("../../outside", "con", "nul"):
        with pytest.raises(CatalogError, match="invalid_locale"):
            service.select(locale)
    assert not service.preferences.path.exists()
    assert not service.files.patch_dir.exists()


def test_theme_locale_and_other_fields_survive_migration_and_interleaved_saves(service, tmp_path):
    service.preferences.update({"locale": "zh-CN", "extra": {"keep": True}})
    theme = GlobalThemeSettingsService(service.preferences.path, store=service.preferences)
    assert theme.load(legacy_theme="light") == "light"
    with ThreadPoolExecutor(max_workers=2) as pool:
        theme_job = pool.submit(theme.save, "dark")
        locale_job = pool.submit(service.select, "zh-CN")
        assert theme_job.result() == "dark"
        assert locale_job.result().locale == "zh-CN"
    assert service.preferences.read() == {"theme": "dark", "locale": "zh-CN", "extra": {"keep": True}}
    # 主题失败回退仍只拥有 theme 字段，不能回填缓存的整个文件。
    theme.save("light")
    assert service.preferences.read()["locale"] == "zh-CN"


def test_replace_failure_preserves_original_preferences_and_patch(service, tmp_path, monkeypatch):
    patch = write_patch(tmp_path, payload(**{"settings.language.saved": "Saved {language}"}))
    service.install(patch)
    service.select("zh-CN")
    original_preferences = service.preferences.path.read_bytes()
    original_patch = (service.files.patch_dir / "en-US.json").read_bytes()
    def fail(*_args):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(json_store.os, "replace", fail)
    with pytest.raises(OSError):
        service.select("en-US")
    with pytest.raises(OSError):
        service.install(patch)
    assert service.preferences.path.read_bytes() == original_preferences
    assert (service.files.patch_dir / "en-US.json").read_bytes() == original_patch
    assert not list(service.files.patch_dir.glob(".*.tmp"))
    assert not list(service.preferences.path.parent.glob(".*.tmp"))


def test_confirm_chinese_account_switch_and_restart_keep_distinct_language_states(service, tmp_path):
    current = service.startup().catalog
    service.install(write_patch(tmp_path, payload(**{"settings.language.saved": "Saved {language}"})))
    service.select("en-US")
    paths = ApplicationPaths.from_roots(root=tmp_path, app_dir=tmp_path, data_root=tmp_path,
                                       bundled_config_dir=tmp_path / "config", asset_dir=tmp_path / "assets",
                                       app_icon_path=tmp_path / "assets/icon.ico")
    def account(name):
        directory = paths.accounts_dir / name
        return AccountContext(name, name, directory, directory / "user_data.sqlite3",
                              directory / "config", directory / "screenshots", directory / "logs")
    context = AppContext(paths, account("first"))
    context.switch_account(account("second"))
    assert paths.locale_patch_dir == service.files.patch_dir
    assert service.startup().catalog is current
    restarted = LanguageSettingsService(service.preferences, service.files)
    assert restarted.startup().catalog.locale == "en-US"
    service.select("zh-CN")
    assert service.startup().catalog is current
    assert (service.files.patch_dir / "en-US.json").is_file()
    assert LanguageSettingsService(service.preferences, service.files).startup().catalog.locale == "zh-CN"


def test_corrupt_selected_catalog_falls_back_without_rewriting_choice(service, tmp_path):
    service.install(write_patch(tmp_path, payload()))
    service.select("en-US")
    original = service.preferences.path.read_bytes()
    (service.files.patch_dir / "en-US.json").write_text("broken", encoding="utf-8")
    next_run = LanguageSettingsService(service.preferences, service.files)
    selection = next_run.startup()
    assert selection.catalog.locale == "zh-CN"
    assert selection.warning_code == "invalid_json"
    assert service.preferences.path.read_bytes() == original


def test_unselected_bad_json_is_skipped_and_corrupt_preferences_have_bounded_fallback(service):
    service.files.patch_dir.mkdir(parents=True, exist_ok=True)
    bad = service.files.patch_dir / "de-DE.json"
    malformed = [b'{"nested":' * 1100 + b'0' + b'}' * 1100,
                 b'{"integer":' + b'9' * 5000 + b'}', b'{"value":NaN}']
    for data in malformed:
        assert len(data) < MAX_CATALOG_BYTES
        bad.write_bytes(data)
        restarted = LanguageSettingsService(service.preferences, service.files)
        assert restarted.startup().catalog.locale == "zh-CN"
        assert [c.locale for c in restarted.choices()] == ["zh-CN"]
        with pytest.raises(CatalogError, match="invalid_json"):
            restarted.install(bad)
        service.preferences.path.parent.mkdir(parents=True, exist_ok=True)
        service.preferences.path.write_bytes(data)
        selected = LanguageSettingsService(service.preferences, service.files).startup()
        assert selected.catalog.locale == "zh-CN"
        assert selected.warning_code == "invalid_preferences"
        assert service.preferences.path.read_bytes() == data


def test_locale_case_collision_is_rejected_without_replacing_valid_resource(service, tmp_path):
    patch = write_patch(tmp_path, payload(**{"settings.language.saved": "Saved {language}"}))
    service.install(patch)
    service.select("en-US")
    original_patch = (service.files.patch_dir / "en-US.json").read_bytes()
    original_preferences = service.preferences.path.read_bytes()
    for locale in ("en-us", "zh-cn", "zh-hans", "qps-Ploc"):
        value = payload(); value["locale"] = locale
        with pytest.raises(CatalogError, match="invalid_locale"):
            service.install(write_patch(tmp_path, value))
        assert (service.files.patch_dir / "en-US.json").read_bytes() == original_patch
        assert service.preferences.path.read_bytes() == original_preferences
    assert LanguageSettingsService(service.preferences, service.files).startup().catalog.locale == "en-US"
