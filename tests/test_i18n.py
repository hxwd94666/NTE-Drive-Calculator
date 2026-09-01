# 覆盖界面语言目录、游戏术语显示名与语言偏好持久化。
"""Interface localisation: catalogue lookup, glossary display names, persistence."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from src import i18n
from src.i18n.catalog import GLOSSARY_SECTIONS, load_catalog, load_glossary
from src.services.global_language_settings_service import GlobalLanguageSettingsService
from src.services.global_theme_settings_service import GlobalThemeSettingsService


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"
SOURCE = ROOT / "src"


class LanguageRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)

    def test_source_language_returns_the_source_string(self) -> None:
        i18n.set_language("zh_CN")
        self.assertEqual("工作台", i18n.tr("工作台"))
        self.assertTrue(i18n.is_source_language())

    def test_english_translates_known_copy(self) -> None:
        i18n.set_language("en")
        self.assertEqual("Dashboard", i18n.tr("工作台"))
        self.assertEqual("Save", i18n.tr("保存"))
        self.assertFalse(i18n.is_source_language())

    def test_unknown_string_falls_back_to_the_source(self) -> None:
        i18n.set_language("en")
        self.assertEqual("尚未翻译的文案", i18n.tr("尚未翻译的文案"))

    def test_unknown_language_falls_back_to_the_default(self) -> None:
        self.assertEqual(i18n.DEFAULT_LANGUAGE, i18n.set_language("klingon"))
        self.assertEqual(i18n.DEFAULT_LANGUAGE, i18n.set_language(None))

    def test_placeholders_are_formatted_and_failures_do_not_raise(self) -> None:
        i18n.set_language("zh_CN")
        self.assertEqual("共 3 项", i18n.tr("共 {count} 项", count=3))
        # A translation missing the placeholder must not raise into the UI.
        self.assertEqual("共 {count} 项", i18n.tr("共 {count} 项", other=1))

    def test_empty_text_is_returned_unchanged(self) -> None:
        i18n.set_language("en")
        self.assertEqual("", i18n.tr(""))

    def test_tr_falls_back_to_the_glossary_for_game_terms(self) -> None:
        # Some UI copy is also a game term. tr() must not show Chinese just
        # because the string lives in the glossary rather than en.json.
        i18n.set_language("en")
        self.assertEqual("Cartridge", i18n.tr("卡带"))
        self.assertEqual("Sub Stat", i18n.tr("副词条"))

    def test_every_tr_key_resolves(self) -> None:
        catalog = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
        glossary = json.loads((LOCALES / "glossary.en.json").read_text(encoding="utf-8"))
        known = set(catalog) | {
            key for section, entries in glossary.items() if section != "_meta"
            for key in entries
        }
        unresolved = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"):
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                value = node.args[0].value
                if isinstance(value, str) and value not in known:
                    unresolved.append(f"{path.name}:{node.lineno} {value[:40]}")
        self.assertEqual([], unresolved)


class GlossaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)

    def test_game_terms_display_in_english_but_keys_stay_chinese(self) -> None:
        i18n.set_language("en")
        self.assertEqual("ATK", i18n.display_term("攻击力"))
        self.assertEqual("Cycle Intensity", i18n.display_term("环合强度"))
        self.assertEqual("Break Intensity", i18n.display_term("倾陷强度"))
        self.assertEqual("Anima", i18n.display_term("灵"))

    def test_source_language_leaves_game_terms_alone(self) -> None:
        i18n.set_language("zh_CN")
        self.assertEqual("攻击力", i18n.display_term("攻击力"))

    def test_unknown_term_passes_through(self) -> None:
        i18n.set_language("en")
        self.assertEqual("某个未知词条", i18n.display_term("某个未知词条"))

    def test_display_terms_maps_a_sequence(self) -> None:
        i18n.set_language("en")
        self.assertEqual(["ATK", "DEF"], i18n.display_terms(["攻击力", "防御力"]))

    def test_glossary_file_covers_every_declared_section(self) -> None:
        payload = json.loads((LOCALES / "glossary.en.json").read_text(encoding="utf-8"))
        for section in GLOSSARY_SECTIONS:
            with self.subTest(section=section):
                self.assertIsInstance(payload.get(section), dict)
                self.assertTrue(payload[section])

    def test_glossary_values_are_non_empty_strings(self) -> None:
        payload = json.loads((LOCALES / "glossary.en.json").read_text(encoding="utf-8"))
        for section in GLOSSARY_SECTIONS:
            for key, value in payload[section].items():
                with self.subTest(section=section, key=key):
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())


class CatalogFileTests(unittest.TestCase):
    def test_every_language_has_a_loadable_catalogue(self) -> None:
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                self.assertIsInstance(load_catalog(language), dict)
                self.assertIsInstance(load_glossary(language), dict)

    def test_english_catalogue_has_no_empty_translations(self) -> None:
        payload = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
        for key, value in payload.items():
            if key.startswith("_"):
                continue
            with self.subTest(key=key):
                self.assertIsInstance(value, str)

    def test_every_language_is_labelled(self) -> None:
        for language in i18n.LANGUAGES:
            self.assertIn(language, i18n.LANGUAGE_LABELS)


class LanguagePersistenceTests(unittest.TestCase):
    def test_language_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "global_ui_preferences.json"
            service = GlobalLanguageSettingsService(path)

            self.assertEqual(i18n.DEFAULT_LANGUAGE, service.load())
            self.assertEqual("en", service.save("en"))
            self.assertEqual("en", GlobalLanguageSettingsService(path).load())

    def test_unknown_stored_language_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "global_ui_preferences.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"language": "klingon"}', encoding="utf-8")
            self.assertEqual(
                i18n.DEFAULT_LANGUAGE, GlobalLanguageSettingsService(path).load()
            )

    def test_theme_and_language_share_the_file_without_clobbering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "global_ui_preferences.json"
            theme = GlobalThemeSettingsService(path)
            language = GlobalLanguageSettingsService(path)

            theme.save("light")
            language.save("en")
            self.assertEqual("light", theme.load())

            theme.save("dark")
            self.assertEqual("en", language.load())

            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"theme": "dark", "language": "en"}, stored)

    def test_broken_file_does_not_lose_the_language_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "global_ui_preferences.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(
                i18n.DEFAULT_LANGUAGE, GlobalLanguageSettingsService(path).load()
            )


class DisplayWiringTests(unittest.TestCase):
    """Game terms reach widgets through display_term, never by rewriting keys."""

    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)

    def test_percent_bearing_keys_lose_the_sign_when_translated(self) -> None:
        # Callers must derive a "%" suffix from the Chinese key, never from the
        # translated label: the English build spells these "… Bonus".
        payload = json.loads((LOCALES / "glossary.en.json").read_text(encoding="utf-8"))
        percent_keys = [k for k in payload["stats"] if "%" in k]
        self.assertTrue(percent_keys)
        without_sign = [k for k in percent_keys if "%" not in payload["stats"][k]]
        self.assertTrue(
            without_sign,
            "expected at least one percent key whose English drops the sign",
        )
        self.assertEqual("ATK Bonus", payload["stats"]["攻击力%"])

    def test_bonus_stat_label_translates_and_still_compacts_chinese(self) -> None:
        from src.features.allocation.results_bonus_view import _display_bonus_stat_label

        i18n.set_language("en")
        self.assertEqual("Cosmos DMG Bonus", _display_bonus_stat_label("光属性异能伤害增强"))
        self.assertEqual("ATK", _display_bonus_stat_label("攻击力"))

        i18n.set_language("zh_CN")
        self.assertEqual("光属性伤害", _display_bonus_stat_label("光属性异能伤害增强"))
        self.assertEqual("攻击力", _display_bonus_stat_label("攻击力"))

    def test_unknown_stat_survives_both_languages(self) -> None:
        from src.features.allocation.results_bonus_view import _display_bonus_stat_label

        for language in ("zh_CN", "en"):
            i18n.set_language(language)
            self.assertEqual("自定义词条", _display_bonus_stat_label("自定义词条"))


class GameTextTests(unittest.TestCase):
    """Long-form game text resolved through the string-table keys."""

    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)

    def test_fork_description_resolves_in_english(self) -> None:
        i18n.set_language("en")
        text = i18n.display_text("/Game/Text/ST_Fork.ST_Fork", "fork_BitGame_des", "zh")
        self.assertNotEqual("zh", text)
        self.assertNotIn("一", text)

    def test_source_language_keeps_the_chinese_fallback(self) -> None:
        i18n.set_language("zh_CN")
        self.assertEqual(
            "中文",
            i18n.display_text("/Game/Text/ST_Fork.ST_Fork", "fork_BitGame_des", "中文"),
        )

    def test_unknown_key_returns_the_fallback(self) -> None:
        i18n.set_language("en")
        self.assertEqual("fb", i18n.display_text("/Game/Text/ST_Fork.ST_Fork", "nope", "fb"))
        self.assertEqual("fb", i18n.display_text("/Game/Text/ST_Fork.ST_Fork", "", "fb"))

    def test_lookup_ignores_key_casing(self) -> None:
        i18n.set_language("en")
        lower = i18n.display_text("ST_Fork", "fork_BitGame_des", "")
        upper = i18n.display_text("ST_Fork", "FORK_BITGAME_DES", "")
        self.assertTrue(lower)
        self.assertEqual(lower, upper)

    def test_catalogue_file_is_well_formed(self) -> None:
        payload = json.loads((LOCALES / "gametext.en.json").read_text(encoding="utf-8"))
        entries = payload["entries"]
        self.assertGreater(len(entries), 400)
        for key, value in entries.items():
            self.assertEqual(key, key.lower(), "keys must be stored lower-cased")
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip())


class StatListKeyTests(unittest.TestCase):
    """Translated stat rows must still round-trip their Chinese key."""

    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)

    def test_selected_stats_stay_chinese_in_english(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

        from src.features.scanning.preserve_rule_editor import _item_stat

        QApplication.instance() or QApplication([])
        i18n.set_language("en")
        options = ["生命值%", "环合强度"]
        widget = QListWidget()
        for stat in options:
            item = QListWidgetItem(i18n.display_term(stat))
            item.setData(Qt.UserRole, stat)
            widget.addItem(item)

        self.assertEqual(
            ["HP Bonus", "Cycle Intensity"],
            [widget.item(i).text() for i in range(widget.count())],
        )
        self.assertEqual(
            options, [_item_stat(widget.item(i)) for i in range(widget.count())]
        )

    def test_combo_selection_saves_the_chinese_key(self) -> None:
        """Set and Arc combos show English but must store the Chinese key."""
        from src.features.allocation.role_selector_preferences import (
            resolve_optional_priority_choice,
            resolve_priority_choice,
        )

        i18n.set_language("en")
        sets = ["「真红：双生蝶」"]
        forks = ["噬心诡刃"]
        self.assertEqual("Crimson: Twin Butterflies", i18n.display_term(sets[0]))
        self.assertEqual(
            sets[0],
            resolve_priority_choice(sets, i18n.display_term(sets[0]), sets[0]),
        )
        self.assertEqual(
            forks[0],
            resolve_optional_priority_choice(forks, i18n.display_term(forks[0]), forks[0]),
        )

    def test_singular_variant_applies_at_one(self) -> None:
        """English needs a singular form where Chinese does not."""
        i18n.set_language("en")
        key = "{count} 个驱动"
        self.assertEqual("1 Module", i18n.tr(key, count=1))
        self.assertEqual("3 Modules", i18n.tr(key, count=3))
        self.assertEqual("0 Modules", i18n.tr(key, count=0))

    def test_only_the_named_field_selects_the_singular(self) -> None:
        """Another integer in the same string must not trigger inflection."""
        i18n.set_language("en")
        key = "已收到新快照 #{snapshot}，但仍有 {count} 件状态尚未确认"
        self.assertIn("3 states are", i18n.tr(key, snapshot=1, count=3))
        self.assertIn("1 state is", i18n.tr(key, snapshot=7, count=1))

    def test_source_language_ignores_singular_variants(self) -> None:
        i18n.set_language("zh_CN")
        self.assertEqual("1 个驱动", i18n.tr("{count} 个驱动", count=1))

    def test_every_singular_variant_has_a_source_and_field(self) -> None:
        """A stale variant would silently never fire."""
        payload = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
        sources = {k for k in payload if "::one::" not in k}
        for key, value in payload.items():
            if "::one::" not in key:
                continue
            source, _, field = key.partition("::one::")
            self.assertIn(source, sources, f"orphan singular variant: {key}")
            self.assertTrue(field, key)
            self.assertIn("{" + field, source, f"{field} is not a placeholder in {source}")
            self.assertTrue(str(value).strip(), key)

    def test_localized_payload_wins_over_the_glossary(self) -> None:
        """nte-core ships en/ja/zh_cn names, so the game data answers first."""
        names = {"en": "Shadow Creed", "ja": "「影の信条」", "zh_cn": "「影之信条」"}
        i18n.set_language("en")
        self.assertEqual("Shadow Creed", i18n.display_localized(names))
        i18n.set_language("zh_CN")
        self.assertEqual("「影之信条」", i18n.display_localized(names))

    def test_vision_rows_still_fall_back_to_the_glossary(self) -> None:
        """Vision-sourced rows only carry Chinese, so they need the glossary."""
        i18n.set_language("en")
        self.assertEqual(
            "Shadow Creed",
            i18n.display_localized({"zh-CN": "「影之信条」"}, "「影之信条」"),
        )
        self.assertEqual("", i18n.display_localized({}, ""))

    def test_warehouse_filter_keys_stay_chinese_in_english(self) -> None:
        """The display name changes; the value behind it stays a lookup key."""
        from src.features.inventory.warehouse import _localized

        i18n.set_language("en")
        names = {"en": "Shadow Creed", "zh_cn": "「影之信条」"}
        self.assertEqual("「影之信条」", _localized(names, "fallback"))

    def test_skill_names_are_not_translated(self) -> None:
        """Skill names stay as nte-core reports them.

        A local mapping would need a fresh locres export for every character the
        game adds, so the battle report shows the upstream name verbatim.
        """
        payload = json.loads((LOCALES / "gametext.en.json").read_text(encoding="utf-8"))
        self.assertNotIn("skills", payload)
        self.assertFalse(hasattr(i18n, "display_skill"))
        page = (SOURCE / "features" / "battle_report" / "page.py").read_text(encoding="utf-8")
        self.assertIn("str(skill.name)", page)

    def test_language_is_activated_before_any_ui_or_feature_import(self) -> None:
        source = (ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="src/ui/app.py")

        activation_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "set_language"
        ]
        self.assertTrue(activation_lines, "src/ui/app.py must call set_language()")

        deferred_imports = [
            node.lineno
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(("src.features", "src.ui"))
        ]
        self.assertTrue(deferred_imports, "expected feature/ui imports in app.py")

        self.assertLess(
            min(activation_lines),
            min(deferred_imports),
            "set_language() must run before importing modules that build UI copy",
        )


class NavigationLabelTests(unittest.TestCase):
    def test_navigation_labels_follow_the_active_language(self) -> None:
        # Nav labels are resolved at import time, so this asserts the catalogue
        # entries exist rather than re-importing the module.
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)
        i18n.set_language("en")
        for source, expected in (
            ("工作台", "Dashboard"),
            ("⚡  计算", "⚡  Calculate"),
            ("🔧  设置", "🔧  Settings"),
            ("角色图纸", "Character Blueprints"),
        ):
            with self.subTest(source=source):
                self.assertEqual(expected, i18n.tr(source))


if __name__ == "__main__":
    unittest.main()
