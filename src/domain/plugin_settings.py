# 定义跨账号的两个显示插件及其不可变选项。
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PluginSettings:
    cooldown: bool = False
    enemy_bars: bool = False
    ready_cue: bool = True
    hp: bool = True
    unbalance: bool = True

    def payload(self) -> dict[str, bool]:
        return asdict(self)

    @property
    def enabled(self) -> bool:
        """技能卡独立启用；敌人卡须同时启用且至少选择一种状态栏。"""
        return self.cooldown or (self.enemy_bars and (self.hp or self.unbalance))
