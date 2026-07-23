# 测试文字输入鉴定的词条别名、分隔符和数值忽略规则。
import unittest
from types import SimpleNamespace


class ManualIdentificationInputTests(unittest.TestCase):
    @staticmethod
    def _window():
        from src.features.identification import controller
        from src.optimizer.scoring import ScoringEngine

        window = SimpleNamespace(scoring_engine=ScoringEngine("config"))
        window._manual_tokens = lambda text: controller._manual_tokens(window, text)
        window._resolve_stat_name = lambda name, percent=False: controller._resolve_stat_name(window, name, percent)
        return window

    def test_manual_stats_accepts_mixed_delimiters_without_values(self):
        from src.features.identification import controller

        stats = controller._parse_manual_stats(
            self._window(),
            "攻击力% 暴击，爆伤\n通用伤害; 倾陷 环合",
        )
        self.assertEqual(
            {"攻击力%", "暴击率%", "暴击伤害%", "伤害增加%", "倾陷强度", "环合强度"},
            set(stats),
        )

    def test_manual_stats_derives_display_values_from_quality_and_grid_count(self):
        from src.features.identification import controller

        window = self._window()
        drive_stats = controller._parse_manual_stats(
            window, "攻击力% 暴伤", quality="Purple", grid_equivalent=2,
        )
        tape_stats = controller._parse_manual_stats(
            window, "生命值 通伤", quality="Purple", grid_equivalent=10,
        )

        self.assertEqual({"攻击力%": 2.0, "暴击伤害%": 3.2}, drive_stats)
        self.assertEqual({"生命值": 800.0, "伤害增加%": 8.0}, tape_stats)
        self.assertEqual(
            {"攻击力": 50.4, "生命值": 672.0},
            controller._manual_drive_main_stats(3, "Purple"),
        )

    def test_manual_stats_ignores_numbers_and_normalizes_percent_attack(self):
        from src.features.identification import controller

        stats = controller._parse_manual_stats(
            self._window(),
            "攻击力10% 大攻击 攻击力百分比 百分比攻击力",
        )

        self.assertEqual({"攻击力%"}, set(stats))

    def test_manual_stats_normalizes_requested_aliases(self):
        from src.features.identification import controller

        stats = controller._parse_manual_stats(
            self._window(),
            "大生命 防御 攻击 生命值% 防御力% 攻击力% 暴击 爆击 暴伤 爆伤",
        )

        self.assertEqual(
            {"生命值%", "防御力", "攻击力", "防御力%", "攻击力%", "暴击率%", "暴击伤害%"},
            set(stats),
        )


if __name__ == "__main__":
    unittest.main()
