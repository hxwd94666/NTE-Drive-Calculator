# 定义跨账号的两个显示插件及其不可变选项。
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PluginSettings:
    cooldown: bool = False
    enemy_bars: bool = False
    hp: bool = True
    unbalance: bool = True

    def payload(self) -> dict[str, bool]:
        # 原生协议仍携带就绪位，但它随技能显示总开关派生，不再是独立偏好。
        return {**asdict(self), "ready_cue": self.cooldown}

    @property
    def enabled(self) -> bool:
        """技能卡独立启用；敌人卡须同时启用且至少选择一种状态栏。"""
        return self.cooldown or (self.enemy_bars and (self.hp or self.unbalance))
