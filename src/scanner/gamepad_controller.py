# 使用虚拟手柄执行自动背包扫描。
"""Full inventory scanner driven by the virtual gamepad controller."""

import gc
import os
import shutil
import time

import mss
import mss.tools

from src.scanner.window_capture import capture_foreground_window
from src.utils.logger import logger
from src.utils.perf import log_perf


MOVE_HOLD_SECONDS = 0.10
MOVE_SETTLE_SECONDS = 0.25
ROW_DOWN_HOLD_SECONDS = 0.15
ROW_DOWN_SETTLE_SECONDS = 0.30
DETAIL_REFRESH_MOVE_SETTLE_SECONDS = 0.30
DETAIL_REFRESH_ENTER_SETTLE_SECONDS = 3.00
DETAIL_REFRESH_RETURN_SETTLE_SECONDS = 2.00
EQUIPMENT_SWITCH_SETTLE_SECONDS = 0.10
LOCK_DISCARD_CONFIRM_SECONDS = 0.60
ACTION_MENU_SETTLE_SECONDS = 0.30
ACTION_APPLY_SETTLE_SECONDS = 0.30
MENU_AFTER_EQUIPMENT_MOVE_SECONDS = 0.15
ACTION_OPTION_MOVE_SETTLE_SECONDS = 0.15


def _save_png(screenshot, filename):
    mss.tools.to_png(screenshot.rgb, screenshot.size, output=filename)


class ViGEmDriverNotReadyError(RuntimeError):
    """Raised when the virtual gamepad driver is missing or not running."""


def _format_vigem_error(exc: Exception) -> str:
    raw = str(exc) or exc.__class__.__name__
    return (
        "ViGEmBus 虚拟手柄驱动未就绪，无法启动全量扫描。\n\n"
        "请按下面顺序处理：\n"
        "1. 先重启电脑，再重新打开本程序。\n"
        "2. 如果仍然报错，打开开始菜单里的 NTE Drive Calc -> Install ViGEmBus Driver 重新安装/修复驱动。\n"
        "3. 修复后再次重启电脑。\n\n"
        f"原始错误: {raw}"
    )


class GamepadScanner:
    MAX_INVENTORY_COUNT = 2000

    def __init__(self, output_dir="scanned_images"):
        self.output_dir = output_dir
        self.capture_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._stopped = False
        self.cols = 7
        self._closed = False

        logger.info("正在连接虚拟 Xbox 360 手柄...")
        try:
            import vgamepad as vg

            self.gamepad = vg.VX360Gamepad()
            self._buttons = vg.XUSB_BUTTON
        except Exception as exc:
            text = str(exc).upper()
            if "VIGEM" in text or "BUS_NOT_FOUND" in text or "VI_GEM" in text:
                raise ViGEmDriverNotReadyError(_format_vigem_error(exc)) from exc
            raise
        time.sleep(2)
        logger.success("虚拟手柄连接完成")

    def close(self):
        if self._closed:
            return
        self._closed = True
        gamepad = getattr(self, "gamepad", None)
        if gamepad is None:
            return
        try:
            try:
                gamepad.reset()
                gamepad.update()
            except Exception as exc:
                logger.debug(f"虚拟手柄归零失败，继续释放: {exc}")
        finally:
            self.gamepad = None
            self._buttons = None
            del gamepad
            gc.collect()
            logger.info("虚拟 Xbox 360 手柄已释放")

    def emergency_stop(self):
        logger.error("\n" + "!" * 50)
        logger.error("接收到 F12 指令，已紧急停止")
        logger.error("!" * 50)
        self._stopped = True

    def _clear_output_images(self):
        image_exts = {".png", ".jpg", ".jpeg", ".bmp"}
        removed = 0
        for name in os.listdir(self.output_dir):
            path = os.path.join(self.output_dir, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in image_exts:
                os.remove(path)
                removed += 1
        if removed:
            logger.info(f"全量扫描前已清理旧截图 {removed} 张。")

    def _prepare_temp_output(self):
        self.capture_dir = os.path.join(self.output_dir, "temp")
        if os.path.exists(self.capture_dir):
            shutil.rmtree(self.capture_dir, ignore_errors=True)
        os.makedirs(self.capture_dir, exist_ok=True)

    def _commit_temp_output(self):
        self._clear_output_images()
        moved = 0
        for name in sorted(os.listdir(self.capture_dir)):
            src = os.path.join(self.capture_dir, name)
            dst = os.path.join(self.output_dir, name)
            if os.path.isfile(src):
                shutil.move(src, dst)
                moved += 1
        shutil.rmtree(self.capture_dir, ignore_errors=True)
        self.capture_dir = self.output_dir
        logger.success(f"全量扫描截图已写入根目录，共 {moved} 张。")

    def capture_panel(self, sct, counter):
        total_start = time.perf_counter()
        capture_start = time.perf_counter()
        screenshot, _ = capture_foreground_window(sct)
        capture_ms = (time.perf_counter() - capture_start) * 1000.0
        filename = os.path.join(self.capture_dir, f"raw_drive_{counter:04d}.png")
        write_start = time.perf_counter()
        _save_png(screenshot, filename)
        write_ms = (time.perf_counter() - write_start) * 1000.0
        elapsed_ms = (time.perf_counter() - total_start) * 1000.0
        log_perf(
            logger,
            "scan.capture",
            elapsed_ms=elapsed_ms,
            index=counter,
            capture_ms=capture_ms,
            write_ms=write_ms,
        )
        logger.info(f"[{counter:04d}] 捕获成功")
        return filename

    def push_left_joystick(self, x, y, hold_seconds=MOVE_HOLD_SECONDS, settle_seconds=MOVE_SETTLE_SECONDS):
        self.gamepad.left_joystick_float(x_value_float=x, y_value_float=y)
        self.gamepad.update()
        time.sleep(hold_seconds)
        self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        self.gamepad.update()
        time.sleep(settle_seconds)

    def _press_button(self, button, hold_seconds=0.08, settle_seconds=0.12):
        self.gamepad.press_button(button=button)
        self.gamepad.update()
        time.sleep(hold_seconds)
        self.gamepad.release_button(button=button)
        self.gamepad.update()
        time.sleep(settle_seconds)

    def _press_a(self):
        self._press_button(self._buttons.XUSB_GAMEPAD_A)

    def _press_b(self):
        self._press_button(self._buttons.XUSB_GAMEPAD_B)

    def _press_y(self):
        self._press_button(self._buttons.XUSB_GAMEPAD_Y)

    def _press_menu(self):
        self._press_button(self._buttons.XUSB_GAMEPAD_START)

    def _press_lb(self):
        logger.info("发送手柄键: LB")
        self._press_button(self._buttons.XUSB_GAMEPAD_LEFT_SHOULDER)

    def _press_rb(self):
        logger.info("发送手柄键: RB")
        self._press_button(self._buttons.XUSB_GAMEPAD_RIGHT_SHOULDER)

    def _press_dpad_left(self):
        logger.info("发送手柄键: 十字左")
        self._press_button(self._buttons.XUSB_GAMEPAD_DPAD_LEFT)

    def _press_dpad_right(self):
        logger.info("发送手柄键: 十字右")
        self._press_button(self._buttons.XUSB_GAMEPAD_DPAD_RIGHT)

    def _toggle_action_menu(self):
        self._press_menu()
        time.sleep(ACTION_MENU_SETTLE_SECONDS)

    def _confirm_action(self):
        self._press_a()
        time.sleep(ACTION_APPLY_SETTLE_SECONDS)

    def _apply_moves(self, moves):
        for move in moves:
            if move == "R":
                self.push_left_joystick(1.0, 0.0)
            elif move == "L":
                self.push_left_joystick(-1.0, 0.0)
            elif move == "U":
                self.push_left_joystick(
                    0.0,
                    1.0,
                    hold_seconds=ROW_DOWN_HOLD_SECONDS,
                    settle_seconds=ROW_DOWN_SETTLE_SECONDS,
                )
            elif move == "D":
                self.push_left_joystick(
                    0.0,
                    -1.0,
                    hold_seconds=ROW_DOWN_HOLD_SECONDS,
                    settle_seconds=ROW_DOWN_SETTLE_SECONDS,
                )

    def _move_between_equipment(self, moves):
        self._apply_moves(moves)
        if moves:
            time.sleep(EQUIPMENT_SWITCH_SETTLE_SECONDS)

    def _settle_before_action_menu(self, moved: bool):
        if moved:
            time.sleep(MENU_AFTER_EQUIPMENT_MOVE_SECONDS)

    def _scan_positions(self, total_drives: int) -> list[tuple[int, int]]:
        scan_order = []
        for row in range((total_drives + self.cols - 1) // self.cols):
            cols_in_row = min(self.cols, total_drives - row * self.cols)
            if row % 2 == 0:
                for col in range(cols_in_row):
                    scan_order.append((row, col))
            else:
                for col in range(cols_in_row - 1, -1, -1):
                    scan_order.append((row, col))
        return scan_order

    def _generate_path(self, total_drives: int) -> list:
        scan_order = self._scan_positions(total_drives)

        commands = []
        curr_row, curr_col = 0, 0
        for target_row, target_col in scan_order:
            moves = []
            while curr_col < target_col:
                moves.append("R")
                curr_col += 1
            while curr_col > target_col:
                moves.append("L")
                curr_col -= 1
            while curr_row < target_row:
                moves.append("D")
                curr_row += 1
            commands.append(moves)
        return commands

    def _refresh_to_first_item(self):
        logger.info("发送详情页回跳定位: D -> Y -> B")
        self._apply_moves(["D"])
        time.sleep(DETAIL_REFRESH_MOVE_SETTLE_SECONDS)
        self._press_y()
        time.sleep(DETAIL_REFRESH_ENTER_SETTLE_SECONDS)
        self._press_b()
        time.sleep(DETAIL_REFRESH_RETURN_SETTLE_SECONDS)

    def _sync_selected_equipment_state(self, current_state: str, target_state: str) -> bool:
        current_state = current_state if current_state in {"normal", "locked", "discarded"} else "normal"
        target_state = target_state if target_state in {"normal", "locked", "discarded"} else "normal"
        if current_state == target_state:
            return False
        self._toggle_action_menu()
        if target_state == "discarded":
            self._press_a()
            if current_state == "locked":
                time.sleep(LOCK_DISCARD_CONFIRM_SECONDS)
                self._press_a()
                time.sleep(LOCK_DISCARD_CONFIRM_SECONDS)
            else:
                time.sleep(ACTION_APPLY_SETTLE_SECONDS)
        elif target_state == "locked":
            self._apply_moves(["R"])
            time.sleep(ACTION_OPTION_MOVE_SETTLE_SECONDS)
            self._confirm_action()
        elif current_state == "discarded":
            self._confirm_action()
        elif current_state == "locked":
            self._apply_moves(["R"])
            time.sleep(ACTION_OPTION_MOVE_SETTLE_SECONDS)
            self._confirm_action()
        self._toggle_action_menu()
        return True

    def _sync_selected_equipment_state_hmt(self, current_state: str, target_state: str) -> bool:
        current_state = current_state if current_state in {"normal", "locked", "discarded"} else "normal"
        target_state = target_state if target_state in {"normal", "locked", "discarded"} else "normal"
        if current_state == target_state:
            return False
        if target_state == "discarded":
            self._press_dpad_left()
            if current_state == "locked" and target_state == "discarded":
                time.sleep(LOCK_DISCARD_CONFIRM_SECONDS)
                self._press_a()
                time.sleep(LOCK_DISCARD_CONFIRM_SECONDS)
            else:
                time.sleep(ACTION_APPLY_SETTLE_SECONDS)
            return True
        if target_state == "locked":
            self._press_dpad_right()
            time.sleep(ACTION_APPLY_SETTLE_SECONDS)
            return True
        if current_state == "discarded":
            self._press_dpad_left()
            time.sleep(ACTION_APPLY_SETTLE_SECONDS)
            return True
        if current_state == "locked":
            self._press_dpad_right()
            time.sleep(ACTION_APPLY_SETTLE_SECONDS)
            return True
        return False

    def sync_equipment_states(self, total_drives: int, state_changes: list[dict], action_mode: str = "default") -> int:
        changes = sorted(
            (
                change
                for change in state_changes
                if 1 <= int(change.get("index", 0) or 0) <= int(total_drives)
                and change.get("current_state") != change.get("target_state")
            ),
            key=lambda change: int(change["index"]),
        )
        if not changes:
            return 0

        self._refresh_to_first_item()
        positions = self._scan_positions(int(total_drives))
        curr_row, curr_col = 0, 0
        applied = 0

        def moves_to(index: int) -> list[str]:
            nonlocal curr_row, curr_col
            target_row, target_col = positions[index - 1]
            moves = []
            while curr_row > target_row:
                moves.append("U")
                curr_row -= 1
            while curr_row < target_row:
                moves.append("D")
                curr_row += 1
            while curr_col < target_col:
                moves.append("R")
                curr_col += 1
            while curr_col > target_col:
                moves.append("L")
                curr_col -= 1
            return moves

        logger.warning(f"准备同步 {len(changes)} 个装备状态，请保持游戏背包界面不动。")
        for change in changes:
            if self._stopped:
                logger.warning(f"状态同步已停止，本次已处理 {applied}/{len(changes)} 个目标。")
                break
            index = int(change["index"])
            moves = moves_to(index)
            self._move_between_equipment(moves)
            self._settle_before_action_menu(bool(moves))
            logger.info(
                f"准备同步状态: raw_drive_{index:04d} "
                f"{change.get('current_state')} -> {change.get('target_state')}"
            )
            if action_mode == "hmt":
                changed = self._sync_selected_equipment_state_hmt(change.get("current_state"), change.get("target_state"))
            else:
                changed = self._sync_selected_equipment_state(change.get("current_state"), change.get("target_state"))
            if changed:
                applied += 1
                logger.info(
                    f"已同步状态: raw_drive_{index:04d} "
                    f"{change.get('current_state')} -> {change.get('target_state')}"
                )
        return applied

    def start_scan(self, total_drives=None, on_capture=None, commit_on_complete=True):
        scan_start = time.perf_counter()
        logger.warning("\n" + "=" * 50)
        logger.warning("虚拟手柄已就位，将在 3 秒后接管控制，请切回游戏")
        logger.warning("请确保此时已选中第一排第一个驱动/卡带")
        logger.warning("=" * 50)
        time.sleep(3)

        if total_drives is None:
            raise ValueError("全量扫描需要先填写库存数量。")
        total_drives = int(total_drives)
        if not 0 < total_drives <= self.MAX_INVENTORY_COUNT:
            raise ValueError(f"库存数量必须在 1-{self.MAX_INVENTORY_COUNT} 之间。")

        logger.info("\n====== 发送撞墙唤醒信号 ======")
        self.push_left_joystick(-1.0, 0.0)
        time.sleep(0.5)

        logger.info(f"\n====== S 形遍历启动（总目标 {total_drives} 个）======")
        self._prepare_temp_output()
        path_commands = self._generate_path(total_drives)
        captured_count = 0
        move_ms_total = 0.0

        with mss.MSS() as sct:
            for index, moves in enumerate(path_commands, 1):
                if self._stopped:
                    break
                move_start = time.perf_counter()
                self._apply_moves(moves)
                move_ms_total += (time.perf_counter() - move_start) * 1000.0
                if self._stopped:
                    break
                captured_path = self.capture_panel(sct, index)
                captured_count += 1
                if on_capture is not None:
                    on_capture(captured_path, index, total_drives)

        elapsed_ms = (time.perf_counter() - scan_start) * 1000.0
        if self._stopped or captured_count != total_drives:
            log_perf(
                logger,
                "scan.full",
                elapsed_ms=elapsed_ms,
                total=total_drives,
                captured=captured_count,
                move_ms=move_ms_total,
                status="aborted",
            )
            logger.warning("全量扫描未完整结束，临时截图未替换当前根目录。")
            return 0

        if commit_on_complete:
            self._commit_temp_output()

        log_perf(
            logger,
            "scan.full",
            elapsed_ms=elapsed_ms,
            total=total_drives,
            captured=captured_count,
            move_ms=move_ms_total,
            avg_item_ms=(elapsed_ms / captured_count) if captured_count else 0.0,
            status="ok",
        )
        logger.success("\n" + "=" * 40)
        logger.success(f"扫描完成，共处理 {total_drives} 个装备。")
        logger.success("=" * 40)
        return captured_count
