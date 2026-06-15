# 命令行入口，串联解析、分配和保存流程。
"""CLI entry point and packaged-runtime bootstrap for NTE Drive Calc."""

import os
import sys
import json
import traceback
from pathlib import Path

# 打包环境下优先注册 OpenVINO DLL 路径，必须在 import openvino 之前
if getattr(sys, 'frozen', False) and sys.platform == 'win32':
    _meipass = Path(sys._MEIPASS)
    _ov_libs = _meipass / "openvino" / "libs"
    if _ov_libs.is_dir():
        os.add_dll_directory(str(_ov_libs))
        os.environ["OPENVINO_LIB_PATHS"] = str(_ov_libs)

ROOT_DIR = Path(__file__).parent.resolve()
sys.path.append(str(ROOT_DIR))

from src.scanner.batch_processor import BatchProcessor
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.optimizer.state_manager import StateManager
from src.utils.logger import logger

CONFIG_DIR = ROOT_DIR / "config"
OUTPUT_FILE = CONFIG_DIR / "real_inventory.json"


class NTEApplication:

    def __init__(self, config_dir=str(CONFIG_DIR)):
        self.config_dir = config_dir
        if not Path(self.config_dir).exists():
            logger.error("config 目录缺失")

    def execute_drone_scan(self, output_dir: str = "scanned_images", mode: str = "auto"):
        from src.scanner.drone_scanner import DroneScanner
        logger.info("启动增量扫描...")
        scanner = DroneScanner(output_dir=output_dir)

        if mode == "auto":
            scanner.start_scan()
        else:
            scanner.start_semi_auto_scan()

        logger.success("增量扫描完成")

    def execute_physical_scan(self, total_count: int, output_dir: str = "scanned_images"):
        from src.scanner.gamepad_controller import GamepadScanner
        logger.info(f"启动手柄扫描，目标数量: {total_count}")
        scanner = GamepadScanner(output_dir=output_dir)
        scanner.start_scan(total_drives=total_count)
        logger.success("手柄扫描完成")

    def execute_vision_processing(self, input_dir: str = "scanned_images", output_file: str = str(OUTPUT_FILE)):
        logger.info("开始视觉解析...")
        processor = BatchProcessor(input_dir=input_dir, output_file=output_file)
        processor.process_all()
        logger.success("视觉解析完成")
        return processor

    def execute_allocation(self, inventory_file: str, priority_list: list, custom_sets: dict, mode: str):
        if not os.path.exists(inventory_file):
            logger.error(f"文件不存在: {inventory_file}")
            return None, None

        with open(inventory_file, "r", encoding="utf-8") as f:
            inventory = json.load(f)

        logger.info("=" * 60)
        logger.info(f"开始分配 | 策略: {mode} | 角色: {priority_list}")
        logger.info("=" * 60)

        orchestrator = NTEPipelineOrchestrator(config_dir=self.config_dir)
        state_mgr = StateManager(config_dir=self.config_dir)

        locked_uids = set()
        backend_mode = mode

        if mode == "update_mode":
            locked_uids = state_mgr.get_locked_uids()
            backend_mode = "role_priority"

        final_plan = orchestrator.run_full_allocation(
            inventory=inventory,
            priority_list=priority_list,
            custom_sets=custom_sets,
            mode=backend_mode,
            locked_uids=locked_uids
        )
        return final_plan, state_mgr


def run_cli():
    app = NTEApplication()
    vision_processor = None

    print("\n[第一步] 扫描模式")
    print("1. 手柄全量扫描 -> 解析 -> 分配（首次使用）")
    print("2. 增量扫描 -> 解析 -> 分配（日常推荐）")
    print("3. 离线解析（读取 scanned_images/）-> 分配")
    print("4. 跳过扫描，直接读取库存分配")
    scan_choice = input("> 请选择 (1/2/3/4) [默认 4]: ").strip() or "4"

    try:
        if scan_choice == "1":
            total_input = input("\n请输入全量扫描总数 (如 422): ").strip()
            total = int(total_input) if total_input else 0
            if total <= 0:
                logger.error("数量必须大于 0！系统退出。")
                sys.exit(0)
            app.execute_physical_scan(total_count=total)
            vision_processor = app.execute_vision_processing()

        elif scan_choice == "2":
            print("\n[无人机模式选择]")
            print("1. 全自动巡航（自动翻页到底）")
            print("2. 半自动模式（手动点选，F9抓取，F10结算）[推荐日常补漏]")
            drone_mode = input("> 请选择模式 (1/2) [默认 2]: ").strip() or "2"

            logger.info("请确保游戏画面在背包首页！")

            if drone_mode == "1":
                app.execute_drone_scan(mode="auto")
            else:
                app.execute_drone_scan(mode="semi")

            vision_processor = app.execute_vision_processing()

        elif scan_choice == "3":
            vision_processor = app.execute_vision_processing()

    except ValueError:
        logger.error("输入无效，请输入一个纯数字！系统退出。")
        sys.exit(0)
    except Exception as e:
        logger.error(f"视觉提取阶段发生崩溃: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n[第二步] 配置角色优先级")
    print("输入角色名称，用逗号分隔 (例如: 薄荷,主角)")
    roles_input = input("> 请输入目标角色: ").strip()
    if not roles_input:
        logger.error("角色不能为空！系统退出。")
        sys.exit(0)

    priority_list = [r.strip() for r in roles_input.split(",")]

    custom_sets = {}
    print("\n[可选] 角色套装自定义覆盖")
    for role in priority_list:
        set_input = input(f"> {role} 使用非常规套装？(回车跳过，或输入套装名如 '失落光芒'): ").strip()
        if set_input:
            custom_sets[role] = set_input
            logger.info(f"已将 {role} 的目标套装设为: {set_input}")

    print("\n[第三步] 选择分配策略")
    print("1. 角色优先 (Role Priority): 主C优先拿极品")
    print("2. 驱动优先 (Drive Priority): 极品贪心反选")
    print("3. 全局最优 (Global Optimal): 全队总分极限")
    print("4. 增量更新 (Update Mode): 锁定已穿戴，仅用闲置装备配装")
    mode_choice = input("> 请选择策略 (1/2/3/4) [默认 1]: ").strip() or "1"

    mode_map = {"1": "role_priority", "2": "drive_priority", "3": "global_optimal", "4": "update_mode"}
    selected_mode = mode_map.get(mode_choice, "role_priority")

    try:
        final_plan, state_mgr = app.execute_allocation(
            inventory_file=str(OUTPUT_FILE),
            priority_list=priority_list,
            custom_sets=custom_sets,
            mode=selected_mode
        )

        if final_plan and state_mgr:
            save_choice = input("\n> 是否将本次方案的装备锁定入库？(y/n) [默认 n]: ").strip().lower()
            if save_choice == 'y':
                state_mgr.save_allocation(final_plan)
                if vision_processor:
                    vision_processor.archive_processed_images()

    except Exception as e:
        logger.error(f"统筹阶段发生崩溃: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NTE 卡带驱动全自动统筹系统")
    parser.add_argument("--cli", action="store_true", help="命令行交互模式")
    parser.add_argument("--gui", action="store_true", help="桌面 GUI 模式（默认）")
    args = parser.parse_args()

    if args.cli:
        run_cli()
    else:
        from src.ui.app import run_gui
        run_gui()
