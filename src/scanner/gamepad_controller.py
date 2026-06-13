"""Full inventory scanner driven by the virtual gamepad controller."""

import time
import os
import mss
import mss.tools

from src.scanner.window_capture import capture_foreground_window
from src.utils.logger import logger


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
        os.makedirs(self.output_dir, exist_ok=True)
        self._stopped = False

        self.cols = 7

        logger.info("正在连接虚拟 Xbox 360 手柄...")
        try:
            import vgamepad as vg
            self.gamepad = vg.VX360Gamepad()
        except Exception as exc:
            text = str(exc).upper()
            if "VIGEM" in text or "BUS_NOT_FOUND" in text or "VI_GEM" in text:
                raise ViGEmDriverNotReadyError(_format_vigem_error(exc)) from exc
            raise
        time.sleep(2)
        logger.success("虚拟手柄连接完成")

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

    def capture_panel(self, counter):
        """截图并以前导零序号命名保存"""
        with mss.MSS() as sct:
            screenshot, _ = capture_foreground_window(sct)
            filename = os.path.join(self.output_dir, f"raw_drive_{counter:04d}.png")
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=filename)
            logger.info(f"[{counter:04d}] 捕获成功")

    def push_left_joystick(self, x, y):
        """微操控制摇杆"""
        self.gamepad.left_joystick_float(x_value_float=x, y_value_float=y)
        self.gamepad.update()
        time.sleep(0.04)
        self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        self.gamepad.update()
        time.sleep(0.25)

    def _generate_path(self, total_drives: int) -> list:
        """生成 S 形遍历路径，处理最后一行不满的情况"""
        scan_order = []
        for r in range((total_drives + self.cols - 1) // self.cols):
            cols_in_row = min(self.cols, total_drives - r * self.cols)
            if r % 2 == 0:
                for c in range(cols_in_row): scan_order.append((r, c))
            else:
                for c in range(cols_in_row - 1, -1, -1): scan_order.append((r, c))

        commands = []
        curr_r, curr_c = 0, 0
        for target_r, target_c in scan_order:
            moves = []
            # 优先水平移动，再垂直下落（避免越界）
            while curr_c < target_c:
                moves.append('R')
                curr_c += 1
            while curr_c > target_c:
                moves.append('L')
                curr_c -= 1
            # 垂直下落
            while curr_r < target_r:
                moves.append('D')
                curr_r += 1
            commands.append(moves)
        return commands

    def start_scan(self, total_drives=None):
        logger.warning("\n" + "=" * 50)
        logger.warning("虚拟手柄已就位，将在 3 秒后接管控制，请切回游戏")
        logger.warning("请确保此时已选中第一排第一个驱动。")
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

        logger.info(f"\n====== S 形遍历启动 (总目标: {total_drives} 个) ======")

        # 预先生成所有的微操指令
        self._clear_output_images()
        path_commands = self._generate_path(total_drives)

        for i, moves in enumerate(path_commands, 1):
            if self._stopped:
                break
            for move in moves:
                if move == 'R':
                    self.push_left_joystick(1.0, 0.0)
                elif move == 'L':
                    self.push_left_joystick(-1.0, 0.0)
                elif move == 'D':
                    self.push_left_joystick(0.0, -1.0)

            if self._stopped:
                break
            self.capture_panel(i)

        logger.success("\n" + "=" * 40)
        logger.success(f"扫描完成，共处理 {total_drives} 个驱动。")
        logger.success("=" * 40)
        return total_drives
