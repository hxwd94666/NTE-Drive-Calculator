# 定义角色稀疏同步的实际保存数量与未采用字段说明。
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NativeRoleSyncResult:
    saved_count: int
    warnings: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        summary = (localized_text(None, 'auto.domain.characters.native_role_sync.message.f4a602463', '已同步 {saved_count} 个角色的已确认字段。', saved_count=f'{self.saved_count}') if self.saved_count
                   else '本次未写入新的角色状态。')
        if self.warnings:
            return summary + '\n以下字段未同步，保留原配置：\n' + '\n'.join(self.warnings)
        return summary + '其余养成配置保持原值。'
from src.domain.localization.catalog import localized_text
