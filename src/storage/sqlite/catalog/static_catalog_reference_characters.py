# 查询独立参考角色表，禁止通过此入口返回可计算角色或修改静态库。
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class ReferenceCharacterQueries(StaticGameDataDao):
    def entries(self):
        if not self._one("SELECT name FROM sqlite_master WHERE type='table' AND name='reference_character'"):
            return []
        return self._rows('SELECT * FROM reference_character ORDER BY character_id')
