# 根据规则纯值生成可读条件摘要，不拥有扫描完成报告。
def _rule_summary_values(values: list[str], limit: int = 2) -> str:
    values = [str(value) for value in values if str(value)]
    if len(values) <= limit:
        return "、".join(values)
    return "、".join(values[:limit]) + f" 等 {len(values)} 项"


def _preserve_rule_summary(rule: dict) -> str:
    if rule.get("smart_kind"):
        from src.domain.equipment.intelligent_rules import dynamic_summary

        return dynamic_summary(rule)
    parts = []
    if rule.get("item_type") == "tape" and rule.get("main_stats"):
        parts.append(f"主：{_rule_summary_values(rule['main_stats'])}")
    if rule.get("sub_stats"):
        raw_mode = rule.get("sub_match", "all")
        if raw_mode == "all":
            mode = "任意四个"
        else:
            try:
                mode = {1: "任意一个", 2: "任意两个", 3: "任意三个", 4: "任意四个"}.get(int(raw_mode), "任意一个")
            except (TypeError, ValueError):
                mode = "任意一个"
        parts.append(f"副：{_rule_summary_values(rule['sub_stats'])}（{mode}）")
    if rule.get("required_sub_stats"):
        parts.append(f"必含：{_rule_summary_values(rule['required_sub_stats'])}")
    return "｜".join(parts) or "未设置词条条件"
