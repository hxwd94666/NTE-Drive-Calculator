# 在调用方的同一账号事务中分离管理配置，不覆盖已有新域或重写鉴定设置。
import json

from src.domain.equipment.eligibility.management_settings import MatchingScope, split_legacy_management_config


def migrate_management_copies(connection, now):
    copies = {
        key: json.loads(value)
        for key, value in connection.execute("SELECT setting_key, value_json FROM application_setting_copy")
    }

    def insert_missing(key, value):
        connection.execute(
            "INSERT OR IGNORE INTO application_setting_copy(setting_key,value_json,updated_at_utc) VALUES (?,?,?)",
            (key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), now),
        )

    ui = copies.get("ui", {})
    if "warehouse_management_config" not in ui and "full_scan_post_action_config" in ui:
        scan, warehouse = split_legacy_management_config(ui["full_scan_post_action_config"])
        ui = {**ui, "full_scan_post_action_config": scan, "warehouse_management_config": warehouse}
        connection.execute(
            "UPDATE application_setting_copy SET value_json=?,updated_at_utc=? WHERE setting_key='ui'",
            (json.dumps(ui, ensure_ascii=False, separators=(",", ":")), now),
        )
    matching = copies.get(MatchingScope.IDENTIFICATION.setting_key)
    if matching is not None:
        for scope in (MatchingScope.SCAN, MatchingScope.WAREHOUSE):
            insert_missing(scope.setting_key, matching)
