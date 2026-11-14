# 校验图鉴缺图审查只处理精确登记的角色图片，不放宽技能资源门禁。
import tempfile
import unittest
from pathlib import Path

from tools.game_data.build_reference_catalog_assets import apply_unavailable_character_images


class ReferenceUnavailableImagesTest(unittest.TestCase):
    def test_review_preserves_character_identity_without_substituting_another_image(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            path = "/Game/UI/Missing.Missing"
            recipe = {"characters": [
                {"character_id": "1074", "source_asset_path": path},
                {"character_id": "1057", "source_asset_path": "/Game/UI/A.A"},
            ]}
            review = [{"group": "characters", "character_id": "1074",
                       "source_asset_path": path, "reason": "同版资源包未包含正式引用图片"}]
            apply_unavailable_character_images(recipe, source, review)
            self.assertEqual(["1057"], [r["character_id"] for r in recipe["characters"]])

    def test_review_cannot_hide_skills_existing_images_or_changed_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "UI").mkdir()
            (source / "UI/A.png").write_bytes(b"present")
            for group, asset in [("skills", "/Game/UI/A.A"),
                                 ("characters", "/Game/UI/A.A"),
                                 ("characters", "/Game/UI/Other.Other")]:
                with self.subTest(group=group, asset=asset):
                    recipe = {"characters": [{"character_id": "1074",
                                              "source_asset_path": "/Game/UI/A.A"}]}
                    review = [{"group": group, "character_id": "1074",
                               "source_asset_path": asset, "reason": "审查"}]
                    with self.assertRaises(ValueError):
                        apply_unavailable_character_images(recipe, source, review)
