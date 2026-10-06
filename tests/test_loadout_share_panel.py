# 验证角色分享图保留指定槽位、冻结评分、未知来源和可取消的只读语义。
from __future__ import annotations

import unittest
from concurrent.futures import CancelledError
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from src.domain.loadout_share import LoadoutSharePanel, ShareEquipment, ShareStat
from src.domain.loadout_share_highlight import ShareHighlightPolicy
from src.domain.official_role import OfficialAttributeSummaryValue
from src.integrations.loadout_share_image import (
    BODY_TOP, HEADER_CONTACT_LINES, SKILLS_PER_ROW, _equipment_sections, _layout,
    _title, render_loadout_share_png, save_share_png,
)
from src.services import loadout_share_service as service


def sample_panel(count: int = 3) -> LoadoutSharePanel:
    return LoadoutSharePanel(
        role_name="示例角色", slot_name="备用槽位", score=246.8, grade="SS",
        score_source="方案冻结评分", cultivation=(ShareStat("角色等级", "80 · 配置"),),
        attributes=(ShareStat("攻击力", "2,160"), ShareStat("暴击率", "68.2%")),
        skills=(ShareStat("普攻", "Lv.10"),), fork_name="示例弧盘",
        fork_description="Lv.80 · 精炼 1", fork_icon=None, portrait=None, avatar=None,
        equipment=tuple(ShareEquipment(
            name=f"装备 {index}", kind="空幕" if index == 0 else "驱动", score=30, grade="A",
            icon=None, main_stats=(ShareStat("攻击力", "10%"),),
            sub_stats=(ShareStat("暴击率", "3.2%", True),),
        ) for index in range(count)), notes=("布局验证用示例数据",),
    )


class LoadoutSharePanelTests(unittest.TestCase):
    def test_core_card_keeps_main_before_substats_in_one_section(self):
        panel = service.build_loadout_share_panel(self.request())
        item = panel.equipment[0]
        self.assertEqual(_equipment_sections(item), (("词条属性", item.main_stats + item.sub_stats),))
        self.assertEqual(item.main_stats[0].value, "37.5%")

    def test_ten_row_capacity_keeps_attributes_skills_and_weapon_separate(self):
        panel = sample_panel()
        for rows in (7, 8, 9, 10, 12):
            for skills in (4, 7, 8):
                current = replace(panel, attributes=(panel.attributes[0],) * rows,
                                  skills=(panel.skills[0],) * skills)
                skill_y, score_y = _layout(current)
                self.assertGreater(score_y, skill_y + ((skills + SKILLS_PER_ROW - 1) // SKILLS_PER_ROW - 1) * 148 + 123)
                self.assertGreater(score_y, BODY_TOP + 160 + (max(10, rows) - 1) * 64 + 60)

    def test_header_contact_text_is_complete_and_four_skills_need_no_extra_row(self):
        texts = []
        with patch("src.integrations.loadout_share_image._text", side_effect=lambda draw, xy, text, *a, **kw: texts.append(text)):
            _title(Image.new("RGBA", (1100, 1300)), sample_panel())
        self.assertEqual(tuple(texts[-2:]), HEADER_CONTACT_LINES)
        self.assertEqual(texts[-2:], ['使用教程：B站飞行库冯', 'Github：NTE-Drive-Calculator'])
        self.assertNotIn('项目地址', texts)
        panel = sample_panel()
        four = replace(panel, skills=panel.skills * 4)
        five = replace(panel, skills=panel.skills * 5)
        self.assertEqual(_layout(four), _layout(panel))
        self.assertGreater(_layout(five)[1], _layout(four)[1])

    def test_role_image_does_not_require_project_qr_asset(self):
        from src.integrations.loadout_share_assets import share_asset
        def without_qr(group, key):
            return None if group == 'branding' else share_asset(group, key)
        with patch('src.integrations.loadout_share_image.share_asset', side_effect=without_qr):
            data = render_loadout_share_png(sample_panel())
        with Image.open(BytesIO(data)) as image:
            image.verify()

    def test_share_hides_charge_without_changing_public_summary_or_scores(self):
        rows = (OfficialAttributeSummaryValue("PanelAtk", "总攻击力", 100, False, ("AtkBase",)),
                OfficialAttributeSummaryValue("ChargeGetEfficiencyBase", "充能效率", .3, True,
                                              ("ChargeGetEfficiencyBase",)))
        raw = {"kind": "core", "uid_slot": 1, "uid_serial": 1}
        request = replace(self.request(), character_id=1004, state=dict(self.request().state, _official_items=[raw]))
        with patch.object(service, "StaticGameDataDao"), \
                patch.object(service, "project_equipment_items_to_max_level", return_value=[]), \
                patch.object(service, "load_official_role_detail", return_value={"profile": {"fork_level": 80}}), \
                patch.object(service, "calculate_official_role_attribute_summaries", return_value={"character": rows}), \
                patch.object(service, "fork_panel_stats", return_value={"AtkBase": 570, "ChargeGetEfficiencyBase": .3}):
            panel = service.build_loadout_share_panel(request)
        self.assertEqual([row.label for row in panel.attributes], ["攻击力"])
        self.assertEqual(len(panel.fork_stats), 1)
        self.assertEqual(panel.fork_stats[0].label, "AtkBase")
        self.assertEqual(rows[1].value, .3)
        self.assertEqual((panel.score, panel.equipment[0].score), (51.75, 51.75))

    def test_loadout_kind_comes_from_mode_not_character_or_display_name(self):
        request = self.request()
        self.assertEqual(service.build_loadout_share_panel(request).loadout_kind, "计算配装")
        game = replace(request, state=dict(request.state, _game_mode=True))
        self.assertEqual(service.build_loadout_share_panel(game).loadout_kind, "游戏配装")
    def test_highlight_threshold_aliases_and_fixed_drive_main(self):
        policy = ShareHighlightPolicy.from_weights(
            {"攻击力%": .39, "暴击率%": .4, "通用伤害增强%": .7}, {"攻击力%": 1})
        self.assertTrue(policy.role(("AtkBase", "AtkUp", "AtkAdd")))
        self.assertFalse(policy.sub_stat("AtkUp"))
        self.assertTrue(policy.sub_stat("CritBase"))
        self.assertTrue(policy.sub_stat("DamageUpGeneralBase"))
        self.assertFalse(policy.main_stat("AtkBase", core=False))
        self.assertFalse(ShareHighlightPolicy.from_weights({"攻击力%": 1}, {}).main_stat("AtkUp", core=True))

    def test_explicit_effective_ids_still_require_positive_sub_weight(self):
        policy = ShareHighlightPolicy.from_weights({"AtkUp": .1, "CritBase": 0}, {}, ("AtkUp", "CritBase"))
        self.assertTrue(policy.role(("CritBase",)))
        self.assertTrue(policy.sub_stat("AtkUp"))
        self.assertFalse(policy.sub_stat("CritBase"))

    def test_total_grades_remain_the_calc_public_rule(self):
        for score, grade in ((180, "B"), (200, "A"), (220, "S"), (240, "SS"), (260, "SSS"), (280, "ACE")):
            panel = service.build_loadout_share_panel(replace(self.request(), score=score))
            self.assertEqual(panel.grade, grade)
            self.assertEqual(panel.equipment[0].score, 51.75)

    def test_unlock_state_uses_original_level_and_preserves_unknown(self):
        request = self.request()
        state = dict(request.state)
        state["equipped_tape"] = dict(state["equipped_tape"], uid="nte-core-1-7")
        raw = {"kind": "core", "uid_slot": 1, "uid_serial": 7, "level": 0,
               "sub_stats": [{"property_id": "CritBase", "value": .032, "percent": True}]}
        state["_official_items"] = [raw]
        self.assertTrue(service.build_loadout_share_panel(replace(request, state=state)).equipment[0].sub_stats[0].locked)
        raw.pop("level")
        self.assertFalse(service.build_loadout_share_panel(replace(request, state=state)).equipment[0].sub_stats[0].locked)

    def request(self):
        return service.LoadoutShareRequest(
            user_database_path=Path("unused-user.sqlite3"), static_database_path=Path("unused-static.sqlite3"),
            asset_root=Path("unused-art"), config_dir=Path("unused-config"), character_id=None,
            role_name="自建示例", slot_name="备用", score=51.75,
            state={"_sqlite_assignment_scores_complete": True, "equipped_tape": {
                "set_name": "示例空幕", "score": 51.75, "grade": "B",
                "main_stats": "攻击力%", "main_value": 37.5,
                "sub_stats": {"暴击率%": 3.2},
            }, "equipped_drives": []},
            weights={"暴击率%": 0.1}, main_weights={}, shape_areas={},
        )

    def test_frozen_scores_are_not_reinterpreted_with_new_weights(self):
        with patch.object(service, "ScoringEngine", side_effect=AssertionError("no recomputation")):
            panel = service.build_loadout_share_panel(self.request())
        self.assertEqual(panel.slot_name, "备用")
        self.assertEqual(panel.score, 51.75)
        self.assertEqual(panel.equipment[0].score, 51.75)
        self.assertEqual(panel.equipment[0].main_stats[0].value, "37.5%")
        self.assertEqual(panel.equipment[0].sub_stats[0].value, "3.2%")
        self.assertEqual(panel.attributes, ())
        self.assertEqual(panel.cultivation[0].value, "—")
        self.assertIsNone(panel.fork_level)
        self.assertIsNone(panel.fork_refinement)
        self.assertIsNone(panel.likeability_level)
        self.assertEqual(panel.fork_stats, ())

    def test_alternate_slot_uses_only_supplied_equipment(self):
        request = self.request()
        second = replace(request, slot_name="第二备用", score=12.5, state={
            "_sqlite_assignment_scores_complete": True,
            "equipped_drives": [{"shape_id": "H_2", "score": 12.5, "sub_stats": {}}],
        })
        panel = service.build_loadout_share_panel(second)
        self.assertEqual(panel.slot_name, "第二备用")
        self.assertEqual([item.name for item in panel.equipment], ["Ⅱ型驱动"])
        self.assertEqual(panel.score, 12.5)

    def test_virtual_equipment_is_explicit_and_zero_scored(self):
        request = self.request()
        virtual = replace(request, score=0, state={"equipped_drives": [{
            "shape_id": "H_2", "virtual": True, "score": 99, "sub_stats": {},
        }]})
        panel = service.build_loadout_share_panel(virtual)
        self.assertTrue(panel.equipment[0].virtual)
        self.assertEqual(panel.equipment[0].score, 0)

    def test_cancelled_or_empty_request_produces_no_panel(self):
        with self.assertRaises(CancelledError):
            service.build_loadout_share_panel(self.request(), cancelled=lambda: True)
        with self.assertRaises(ValueError):
            service.build_loadout_share_panel(replace(self.request(), state={}))

    def test_drive_cards_keep_only_four_substats_and_game_display_names(self):
        request = self.request()
        for shape, name in (("H_2", "Ⅱ型驱动"), ("L_3_BL", "Ⅲ型驱动"), ("Trap_4_V", "Ⅳ型驱动")):
            state = {"equipped_drives": [{"shape_id": shape, "score": 12.5, "grade": "S",
                     "main_stats": "攻击力", "main_value": 63,
                     "sub_stats": {"暴击率%": 3, "暴击伤害%": 6, "攻击力%": 2.5, "防御力": 16}}]}
            card = service.build_loadout_share_panel(replace(request, state=state)).equipment[0]
            self.assertEqual(card.name, name)
            self.assertEqual(card.main_stats, ())
            self.assertEqual(len(card.sub_stats), 4)
            self.assertEqual((card.score, card.grade), (12.5, "S"))

    def test_renderer_exports_variable_count_without_damage_section(self):
        sizes = []
        for count in (1, 3, 9):
            with Image.open(BytesIO(render_loadout_share_png(sample_panel(count)))) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.width, 1100)
                sizes.append(image.height)
                image.verify()
        self.assertGreater(sizes[2], sizes[0])

    def test_atomic_save_preserves_original_on_failed_replace(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "panel.png"
            target.write_bytes(b"original")
            with patch.object(Path, "replace", side_effect=OSError("injected write failure")):
                with self.assertRaises(OSError):
                    save_share_png(target, render_loadout_share_png(sample_panel()))
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_optional_presentation_data_keeps_frozen_scores_and_unknowns(self):
        panel = sample_panel()
        enriched = replace(panel, fork_stats=(ShareStat("基础攻击力", "570"),),
                           fork_level=80, fork_refinement=1)
        data = render_loadout_share_png(enriched)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(enriched.score, panel.score)
        self.assertEqual(enriched.equipment, panel.equipment)
        self.assertIsNone(enriched.likeability_level)


if __name__ == "__main__":
    unittest.main()
