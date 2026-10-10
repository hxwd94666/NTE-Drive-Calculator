# 验证角色技能图片按正式技能身份构建、共享读取和来源缺失时停止晋升。
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from tools.game_data.build_role_skill_assets import add_role_skill_assets
from tools.game_data.promote_role_catalog import promote_role_skill_assets


class RoleSkillAssetsTests(unittest.TestCase):
    def fixture(self, root: Path):
        source, output = root/'source', root/'candidate/game_ui'
        source.mkdir()
        output.mkdir(parents=True)
        database = root/'candidate/game_static.sqlite3'
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript('''
                CREATE TABLE character_skill(skill_id TEXT, ability_type TEXT);
                CREATE TABLE gameplay_ability_catalog(ability_id TEXT, icon_path TEXT);
                INSERT INTO character_skill VALUES ('GA_A', 'Proactive'), ('GA_B', 'Proactive');
                INSERT INTO gameplay_ability_catalog VALUES
                    ('GA_A', '/Game/Skill/Shared.Shared'), ('GA_B', '/Game/Skill/Shared.Shared');
            ''')
        (source/'Skill').mkdir()
        Image.new('RGBA', (256, 256), 'white').save(source/'Skill/Shared.png')
        (output/'manifest.json').write_text(json.dumps({'files': {}, 'characters': {}, 'fork_items': {}}), encoding='utf-8')
        return source, database, output

    def test_formal_aliases_share_one_file_and_missing_id_has_no_fallback(self):
        with TemporaryDirectory() as temporary:
            source, database, output = self.fixture(Path(temporary))
            result = add_role_skill_assets(source, database, output)
            self.assertEqual(len(result['skills']), 2)
            self.assertEqual(len(result['files']), 1)
            catalog = GameUiAssetCatalog(output)
            self.assertEqual(catalog.skill_icon('GA_A'), catalog.skill_icon('GA_B'))
            self.assertIsNone(catalog.skill_icon('GA_UNKNOWN'))
            self.assertEqual(catalog.skill_icon('GA_A').read_bytes(), (source/'Skill/Shared.png').read_bytes())

    def test_missing_source_preserves_previous_manifest(self):
        with TemporaryDirectory() as temporary:
            source, database, output = self.fixture(Path(temporary))
            (source/'Skill/Shared.png').unlink()
            before = (output/'manifest.json').read_bytes()
            with self.assertRaises(FileNotFoundError):
                add_role_skill_assets(source, database, output)
            self.assertEqual((output/'manifest.json').read_bytes(), before)

    def test_source_path_cannot_leave_content_root(self):
        with TemporaryDirectory() as temporary:
            source, database, output = self.fixture(Path(temporary))
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE gameplay_ability_catalog SET icon_path='/Game/../outside.outside'")
            with self.assertRaises(ValueError):
                add_role_skill_assets(source, database, output)

    def test_version_refresh_prunes_only_previously_managed_skill_file(self):
        with TemporaryDirectory() as temporary:
            source, database, output = self.fixture(Path(temporary))
            add_role_skill_assets(source, database, output)
            Image.new('RGBA', (256, 256), 'black').save(source/'Skill/New.png')
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE gameplay_ability_catalog SET icon_path='/Game/Skill/New.New'")
            result = add_role_skill_assets(source, database, output)
            self.assertFalse((output/'skills/Shared.png').exists())
            self.assertEqual(set(result['files']), {'skills/New.png'})
            self.assertEqual(GameUiAssetCatalog(output).skill_icon('GA_A'), output/'skills/New.png')

    def test_image_only_promotion_rejects_changed_database_identity(self):
        current = SimpleNamespace(sha256='OLD', dataset_id='dataset', scope='reference')
        updated = SimpleNamespace(sha256='NEW', dataset_id='dataset', scope='reference')
        with patch('tools.game_data.promote_role_catalog.read_role_catalog', side_effect=[current, updated]), \
                patch('tools.game_data.promote_role_catalog.promote_role_catalog') as install:
            with self.assertRaises(ValueError):
                promote_role_skill_assets(Path('candidate'), Path('release'))
            install.assert_not_called()


if __name__ == '__main__':
    unittest.main()
