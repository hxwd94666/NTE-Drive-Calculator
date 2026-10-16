# 保存计算页数据读取区的全局展开偏好，不混入账号、角色或扫描执行参数。
from src.integrations.preferences.json_store import LocalPreferencesStore


class DataReadingSettings:
    KEY = "data_reading_options_expanded"

    def __init__(self, store: LocalPreferencesStore):
        self.store = store

    def load(self) -> bool:
        # 老配置和非布尔值均按默认收起展示；读取本身不写配置。
        return (self.store.read() or {}).get(self.KEY) is True

    def save(self, expanded: bool) -> None:
        if type(expanded) is not bool:
            raise ValueError("展开状态必须为布尔值")
        # 复用组合根的合并写入 owner，避免覆盖主题、语言及其它本机偏好。
        self.store.update({self.KEY: expanded})
