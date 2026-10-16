# 显式命令提供外部数据根选择、旧根迁入和恢复；执行完退出，不隐式启动账号。
from __future__ import annotations

from pathlib import Path

from src.integrations.application_runtime_paths import resolve_application_roots, select_runtime_data_root
from .data_lifecycle import migrate_existing_data, restore_data_recovery, resume_data_import
from .data_roots import select_external_data_root
from .lock import DataRootLease
from .metadata import DistributionError, load_distribution
from .release_files import verify_release


def add_maintenance_arguments(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--portable-data-root", type=Path, help="显式选择平台声明保留的外部数据绝对路径，完成后退出")
    group.add_argument("--migrate-data-from", type=Path, help="将旧数据根迁入空便携数据根，保留来源，完成后退出")
    group.add_argument("--restore-data-recovery", help="显式恢复指定数据恢复点，先保全新版数据，完成后退出")
    group.add_argument("--resume-data-import", action="store_true", help="恢复已完整暂存但发布中断的迁入，完成后退出")
    parser.add_argument("--confirm-source-closed", action="store_true", help="确认旧程序及相关任务已退出；旧版不遵守新数据锁")
    parser.add_argument("--confirm-replace-current-data", action="store_true", help="确认恢复旧数据；当前数据将先另存恢复点")


def run_maintenance(args) -> bool:
    if not any((args.portable_data_root, args.migrate_data_from, args.restore_data_recovery, args.resume_data_import)):
        if args.confirm_source_closed or args.confirm_replace_current_data:
            raise DistributionError("确认参数必须与对应数据维护命令一起使用。")
        return False
    _package, app_dir = resolve_application_roots()
    distribution = load_distribution(app_dir)
    verify_release(app_dir, distribution)
    if args.portable_data_root:
        chosen = args.portable_data_root.expanduser().absolute()
        if chosen == app_dir.resolve() or app_dir.resolve() in chosen.resolve().parents:
            raise DistributionError("平台外部数据根须位于程序目录之外，避免清空程序目录时一起删除。")
        path = select_external_data_root(distribution, args.portable_data_root)
        print(f"外部数据位置已明确保存：{path}；下次启动使用该目录。")
        return True
    root = select_runtime_data_root(app_dir, distribution=distribution)
    with DataRootLease(root) as lease:
        if args.migrate_data_from:
            if not distribution.portable:
                raise DistributionError("旧根迁入只适用于明确的便携发行。")
            migrate_existing_data(args.migrate_data_from, root, lease, confirm_source_closed=args.confirm_source_closed)
            print(f"迁入已完成：{root}；原数据根保留。")
            print("旧游戏/组件路径记录保留；首次使用前请到环境检测重新确认，旧 Loader 执行物未迁入，未修改注册表或部署。")
        elif args.resume_data_import:
            resume_data_import(root, lease)
            print(f"迁入发布已恢复：{root}。")
        else:
            kept = restore_data_recovery(root, args.restore_data_recovery, distribution, lease,
                                         confirm_replace=args.confirm_replace_current_data)
            print(f"数据已恢复；新版数据保存在 {kept}。请使用与恢复数据匹配的程序版本，再启动。")
    return True
