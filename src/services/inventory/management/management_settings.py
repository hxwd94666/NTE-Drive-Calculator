# 以账号和管理作用域保存配置与匹配草稿；同窗口统一 CAS 与事务，不跨入口互写。
from copy import deepcopy
from pathlib import Path

from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.inventory.post_actions import merge_post_action_config, validate_post_action_config
from src.domain.equipment.warehouse.intelligent_rules import policy_from_rules
from src.services.identification.matching_settings import normalize_matching_settings
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError


class ManagementSettingsService:
    def __init__(self, database, *, scope):
        self.database = Path(database)
        self.scope = MatchingScope(scope)
        if self.scope.management_key is None:
            raise ValueError("鉴定作用域不拥有管理配置")

    def normalize(self, config):
        return (
            normalize_warehouse_config(config)
            if self.scope == MatchingScope.WAREHOUSE
            else merge_post_action_config(config)
        )

    def load(self):
        with UserDataDao(self.database) as dao, dao.read_consistent_state():
            copies = dao.list_application_setting_copies()
            config = copies.get("ui", {}).get(self.scope.management_key)
            matching = copies.get(self.scope.setting_key)
        return self.normalize(config), normalize_matching_settings(matching)

    def save(self, config, matching, *, expected_config, expected_matching, check_current=None):
        value, expected = self.normalize(config), self.normalize(expected_config)
        matching = normalize_matching_settings(matching)
        expected_matching = normalize_matching_settings(expected_matching)
        error = validate_post_action_config(value)
        if error:
            raise ValueError(error)
        if self.scope == MatchingScope.WAREHOUSE:
            policy_from_rules(value["preserve_rules"])
        elif any(rule.get("smart_kind") for rule in value["preserve_rules"]):
            raise ValueError("扫描管理不执行动态智能策略")
        with UserDataDao(self.database) as dao, dao.write_transaction():
            if check_current is not None:
                check_current()
            copies = dao.list_application_setting_copies()
            current = self.normalize(copies.get("ui", {}).get(self.scope.management_key))
            if current != expected:
                raise UserDataValidationError("管理配置已变化，请重新打开后修改")
            raw_matching = copies.get(self.scope.setting_key, normalize_matching_settings(None))
            if normalize_matching_settings(raw_matching) != expected_matching:
                raise UserDataValidationError("套装匹配已变化，请重新打开后修改")
            dao.compare_and_replace_application_setting_copy(
                self.scope.setting_key,
                matching,
                expected=raw_matching,
                missing_value=normalize_matching_settings(None),
                check=check_current,
            )
            dao.patch_application_setting_copy("ui", {self.scope.management_key: deepcopy(value)})
            if check_current is not None:
                check_current()
        return value
