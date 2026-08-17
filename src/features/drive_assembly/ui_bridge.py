# 提供配装页面调用的自动装配计划与执行公共入口。
"""Public UI bridge for drive-assembly planning and execution."""

from src.features.drive_assembly.assembly_session import (
    AssemblyRunRecorder,
    build_all_role_assembly_plan,
    build_single_role_assembly_plan,
    close_assembly_backend,
    enable_assembly_randomization,
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
    is_role_detail_startup_recognition,
    role_recognition_candidates,
    summarize_assembly_plan,
    verify_blueprint_against_screenshot,
)
from src.features.drive_assembly.tape_plan import tape_install_sequence

__all__ = [
    "AssemblyRunRecorder",
    "build_all_role_assembly_plan",
    "build_single_role_assembly_plan",
    "close_assembly_backend",
    "enable_assembly_randomization",
    "execute_all_roles_from_current_game_page",
    "execute_selected_role_from_current_game_page",
    "is_role_detail_startup_recognition",
    "role_recognition_candidates",
    "summarize_assembly_plan",
    "tape_install_sequence",
    "verify_blueprint_against_screenshot",
]
