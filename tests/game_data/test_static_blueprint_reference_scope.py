# 验证包内导出元数据去重不丢弃技能、继承与跨包依赖。
import unittest

from tools.game_data.static_database_blueprint_imports import _local_export_metadata_reference


class BlueprintReferenceScopeTests(unittest.TestCase):
    def test_only_local_export_metadata_is_omitted(self):
        for field in ("Outer", "ClassDefaultObject", "Children[2]", "FuncMap.Calculate"):
            self.assertTrue(_local_export_metadata_reference(f"$[1].{field}", "/Game/A", "/game/a"))
            self.assertFalse(_local_export_metadata_reference(f"$[1].{field}", "/Game/A", "/Game/B"))
        for field in ("Super", "Template", "Properties.FollowSkillClass", "Properties.Outer"):
            self.assertFalse(_local_export_metadata_reference(f"$[1].{field}", "/Game/A", "/Game/A"))
