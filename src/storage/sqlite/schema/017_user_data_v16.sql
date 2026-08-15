-- 用户数据库 v16：默认配装槽位使用角色显示名，而非历史“主力”占位名。
UPDATE role_loadout_slot
SET slot_name = (
    SELECT json_extract(plan.payload_json, '$.source_role_name')
    FROM loadout_plan AS plan
    WHERE plan.plan_id = role_loadout_slot.current_plan_id
)
WHERE slot_key = 'primary'
  AND slot_name = '主力'
  AND current_plan_id IS NOT NULL
  AND typeof((
      SELECT json_extract(plan.payload_json, '$.source_role_name')
      FROM loadout_plan AS plan
      WHERE plan.plan_id = role_loadout_slot.current_plan_id
  )) = 'text'
  AND trim((
      SELECT json_extract(plan.payload_json, '$.source_role_name')
      FROM loadout_plan AS plan
      WHERE plan.plan_id = role_loadout_slot.current_plan_id
  )) <> '';
