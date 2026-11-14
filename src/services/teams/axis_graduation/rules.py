# 同一主静态读取单元把Calc既有评分目录编译为理论规则，来源未变前不重新读取另一份数值表。
from src.domain.teams.graduation.axis_graduation_rules import compile_theory_equipment_rules
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit


def freeze_theory_equipment_rules(*, scoring_rules, quality_coefficients, static_database_path,
                                  checkpoint=lambda: None):
    checkpoint()
    with static_catalog_read_unit(static_database_path) as unit:
        if unit.release.scope != "game":
            raise ValueError("理论词条规则必须读取完整主静态，不使用独立图鉴")
        result = compile_theory_equipment_rules(
            scoring_rules=scoring_rules, quality_coefficients=quality_coefficients,
            attributes=unit.dao.list_equipment_attributes(), equipment_items=unit.dao.list_equipment_items(),
            dataset_id=unit.release.dataset_id, static_schema=unit.release.schema_version,
            static_sha256=unit.release.sha256.lower(), checkpoint=checkpoint,
        )
        checkpoint()
    checkpoint()
    return result
