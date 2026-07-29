# 按产品功能域声明 Windows 引导式人工步骤及其预期日志事件。
"""Guided validation profiles; no profile sends input to the game."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidationProfile:
    key: str
    title: str
    instruction: str
    expected_events: tuple[str, ...] = ()


PROFILES = (
    ValidationProfile(
        "startup",
        "启动、退出与独立运行日志",
        "以管理员权限启动应用；开启、关闭并再次开启运行日志，然后正常退出。",
        ("app.startup", "app.shutdown"),
    ),
    ValidationProfile(
        "account-switch",
        "账号切换与后台生命周期",
        "在账号 A/B 间切换，并在一个后台任务活跃时验证阻止或安全停止行为。",
        ("account.switch",),
    ),
    ValidationProfile(
        "nte-core-sync",
        "nte-core 背包同步",
        "运行环境诊断并完成一次稳定背包同步；如条件允许再验证一次失败提示。",
        ("inventory_sync.",),
    ),
    ValidationProfile(
        "vision",
        "视觉扫描与单件鉴定",
        "执行一次当前设备分辨率的扫描，并验证取消或人工补录中的一个分支。",
        ("scanning.", "identification."),
    ),
    ValidationProfile(
        "calculation",
        "计算、角色与基础权重",
        "固定稳定快照完成计算、保存和一次替换；再检查角色及基础权重页面。",
        ("allocation.", "role."),
    ),
    ValidationProfile(
        "warehouse",
        "仓库筛选与状态管理",
        "检查仓库筛选；仅在明确测试装备上执行一次锁定或弃置状态写回。",
        ("warehouse.",),
    ),
    ValidationProfile(
        "assembly",
        "极速装配与游戏界面装配",
        "使用测试方案执行适用的装配流程，并验证 F12 停止或失败恢复。该操作不会完整撤销。",
        ("equipment_apply.", "assembly."),
    ),
    ValidationProfile(
        "environment-update",
        "环境、插件与 Mirror 更新",
        "运行环境诊断、Mirror 更新检查；插件部署/还原和安装器启动仅在安全环境执行。",
        ("environment.", "update."),
    ),
)

PROFILE_BY_KEY = {profile.key: profile for profile in PROFILES}

