# 验证裸套装名与带外框的展示套装名会归一到同一官方名称。
import unittest

from src.utils.set_name import normalize_set_display_name


class SetNameNormalizationTests(unittest.TestCase):
    def test_accepts_plain_and_wrapped_official_names(self):
        self.assertEqual("失落光芒", normalize_set_display_name("失落光芒"))
        self.assertEqual("失落光芒", normalize_set_display_name("「失落光芒」"))
        self.assertEqual("失落光芒", normalize_set_display_name("【失落光芒】"))


if __name__ == "__main__":
    unittest.main()
