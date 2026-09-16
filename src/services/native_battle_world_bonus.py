# 从本场家具观测冻结加成，保留魔女与世界原始证据，不改写账号配置。
from copy import deepcopy


def native_world_bonus(snapshot, fallback):
    # DT_SpecialFurniture: SF_0011 -> yaodao_0..9; SF_0012 -> quantao_0..9.
    # Level 1..10 corresponds to attack +2..20 and critical damage +0.004..0.04.
    rules = {"SF_0011": ("yaodao_attack_add", 2.0), "SF_0012": ("quantao_crit_damage", 0.004)}
    rows = snapshot.get("domains", {}).get("environment", {}).get("records") or []
    values = dict(fallback)
    evidence = {"rule_version": "special_furniture_v1", "fields": {},
                "environment_records": deepcopy(rows), "witch_effect_state": "unverified"}
    for furniture_id, (field, multiplier) in rules.items():
        matches = [row for row in rows if row.get("kind") == "furniture_raw" and row.get("FurnitureID") == furniture_id]
        source = "account_setting_fallback"
        if len(matches) == 1:
            row = matches[0]
            level, active = row.get("Level"), row.get("bIsActivated")
            if type(active) is bool and type(level) is int and 0 <= level <= 10:
                values[field] = round(level * multiplier, 6) if active else 0.0
                source = "native_furniture_level"
        evidence["fields"][field] = {"source": source, "value": values[field], "furniture_id": furniture_id}
    return values, evidence
