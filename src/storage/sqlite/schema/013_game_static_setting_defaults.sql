-- 静态库 v13：应用设置的官方默认值；运行时只读。

CREATE TABLE application_setting_default (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    description_zh TEXT NOT NULL
);

INSERT INTO application_setting_default(setting_key, value_json, description_zh) VALUES
(
    'sync',
    '{"inventory_sync_method":"nte_core","equipment_apply_method":"nte_core","capture_device_id":null,"raw_capture_enabled":false,"inventory_settle_seconds":5.0,"auto_start_inventory_sync":false,"inventory_snapshot_retention_count":20}',
    '背包同步与装配执行默认设置'
),
(
    'hotkeys',
    '{"capture":"F9","finish":"F10","stop":"F12"}',
    '截图与扫描快捷键默认设置'
),
(
    'update',
    '{"never_remind":false,"ignored_version":""}',
    '软件更新提醒默认设置'
),
(
    'ui',
    '{"log_enabled":false,"skip_unsaved_allocation_prompt":false,"skip_automatic_assembly_duplicate_warning":false,"full_scan_dual_thread_processing":true,"full_scan_amd_compatibility":false,"theme":"dark"}',
    '界面与操作提示默认设置'
);
