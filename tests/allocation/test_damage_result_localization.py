# 验证直伤/轴伤结果 DTO 的可见静态文案使用冻结语言目录，业务数值与状态不变。
from src.domain.localization.catalog import make_catalog
from src.features.allocation.results.damage_result_summary import damage_optimization_presentation
from src.integrations.preferences.locale_files import bundled_text_catalog


def test_damage_result_presentation_uses_catalog_for_static_labels():
    base = bundled_text_catalog()
    # 补丁须保留每个 key 的命名参数，否则目录按参数一致性契约拒绝加载。
    messages = {
        key: f"TEXT {key}" + "".join(f" {{{name}}}" for name in sorted(base.parameters[key]))
        for key in base.messages
        if key.startswith("allocation.damage.")
    }
    catalog = make_catalog({
        "format_version": 1, "catalog_id": "nte-ui", "locale": "en-US",
        "display_name": "Fixture", "messages": messages,
    }, base=base)
    value = {
        "objective": "standard_direct_v1", "status": "completed",
        "baseline_value": 100.0, "candidate_value": 100.0,
        "termination": "local_optimum", "neighborhood_complete": True,
    }
    result = damage_optimization_presentation(value, text_catalog=catalog)
    assert result is not None
    assert "TEXT allocation.damage.label.direct" in result.headline
    assert "TEXT allocation.damage.scope.standard" in result.details
