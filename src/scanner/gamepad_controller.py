# 使用虚拟手柄执行自动背包扫描。
"""Full inventory scanner driven by the virtual gamepad controller."""

import gc
import os
import shutil
import time
from dataclasses import dataclass
from typing import Literal

import mss
import mss.tools
import numpy as np

from src.scanner.window_capture import capture_foreground_window
from src.utils.logger import logger
from src.utils.perf import log_perf


Roi = tuple[float, float, float, float]
VisionMetric = Literal["white", "gray", "pink"]
VisionOperator = Literal["gt", "lt"]


@dataclass(frozen=True)
class GamepadVisionRule:
    name: str
    roi: Roi
    metric: VisionMetric
    operator: VisionOperator
    threshold: float
    pixel_threshold: int = 185


ZERO_LEVEL_DETAIL_PAGE_RULES: tuple[GamepadVisionRule, ...] = (
    GamepadVisionRule("强化按钮白色占比", (0.69, 0.88, 0.98, 0.985), "white", "gt", 0.16, 185),
    GamepadVisionRule("快捷添加槽灰色占比", (0.68, 0.70, 0.99, 0.88), "gray", "gt", 0.22),
    GamepadVisionRule("快捷添加槽非高亮", (0.68, 0.70, 0.99, 0.88), "white", "lt", 0.25, 185),
    GamepadVisionRule("详情右栏非背包白底", (0.68, 0.40, 0.97, 0.90), "white", "lt", 0.20, 185),
)

COMMON_DETAIL_PAGE_RULES: tuple[GamepadVisionRule, ...] = (
    GamepadVisionRule("详情属性区灰色占比", (0.68, 0.20, 0.98, 0.55), "gray", "gt", 0.25),
    GamepadVisionRule("详情右栏非背包白底", (0.68, 0.40, 0.97, 0.90), "white", "lt", 0.20, 185),
)


@dataclass(frozen=True)
class GamepadVisionProfile:
    region: str = "cn"
    detail_page_rules: tuple[GamepadVisionRule, ...] = ZERO_LEVEL_DETAIL_PAGE_RULES
    detail_page_rule_groups: tuple[tuple[GamepadVisionRule, ...], ...] = (
        ZERO_LEVEL_DETAIL_PAGE_RULES,
        COMMON_DETAIL_PAGE_RULES,
    )
    inventory_first_item_rules: tuple[GamepadVisionRule, ...] = (
        GamepadVisionRule("第一格粉色选框", (0.04, 0.135, 0.155, 0.34), "pink", "gt", 0.008),
        GamepadVisionRule("右侧属性白底", (0.68, 0.40, 0.97, 0.90), "white", "gt", 0.12, 165),
        GamepadVisionRule("顶部导航灰色占比", (0.15, 0.025, 0.58, 0.105), "gray", "gt", 0.08),
    )

    @classmethod
    def for_region(cls, region: str = "cn") -> "GamepadVisionProfile":
        if region == "hmt":
            return cls(region="hmt")
        return cls(region="cn")


@dataclass(frozen=True)
class GamepadActionProfile:
    region: str = "cn"
    context: str = "scan"
    operation_style: str = "menu"
    move_hold_seconds: float = 0.10
    move_settle_seconds: float = 0.25
    row_move_hold_seconds: float = 0.15
    row_move_settle_seconds: float = 0.30
    equipment_switch_settle_seconds: float = 0.20
    detail_refresh_move_settle_seconds: float = 0.30
    page_state_poll_seconds: float = 0.20
    page_state_stable_frames: int = 2
    page_state_detected_settle_seconds: float = 0.50
    detail_page_wait_timeout_seconds: float = 8.00
    inventory_page_wait_timeout_seconds: float = 8.00
    action_menu_settle_seconds: float = 0.30
    action_apply_settle_seconds: float = 0.30
    popup_confirm_seconds: float = 0.60
    menu_after_equipment_move_seconds: float = 0.20
    action_option_move_settle_seconds: float = 0.15
    button_hold_seconds: float = 0.08
    button_settle_seconds: float = 0.12
    virtual_gamepad_connect_settle_seconds: float = 2.00
    takeover_countdown_seconds: float = 3.00
    wall_wake_settle_seconds: float = 0.50
    vision: GamepadVisionProfile = GamepadVisionProfile.for_region("cn")

    @classmethod
    def scan(cls) -> "GamepadActionProfile":
        return cls(context="scan")

    @classmethod
    def state_management(cls, region: str = "cn") -> "GamepadActionProfile":
        if region == "hmt":
            return cls(region="hmt", context="state_management", operation_style="dpad", vision=GamepadVisionProfile.for_region("hmt"))
        return cls(region="cn", context="state_management", operation_style="menu", vision=GamepadVisionProfile.for_region("cn"))


DEFAULT_SCAN_PROFILE = GamepadActionProfile.scan()
DEFAULT_STATE_PROFILE = GamepadActionProfile.state_management("cn")
HMT_STATE_PROFILE = GamepadActionProfile.state_management("hmt")

MOVE_HOLD_SECONDS = DEFAULT_SCAN_PROFILE.move_hold_seconds
MOVE_SETTLE_SECONDS = DEFAULT_SCAN_PROFILE.move_settle_seconds
ROW_DOWN_HOLD_SECONDS = DEFAULT_SCAN_PROFILE.row_move_hold_seconds
ROW_DOWN_SETTLE_SECONDS = DEFAULT_SCAN_PROFILE.row_move_settle_seconds
DETAIL_REFRESH_MOVE_SETTLE_SECONDS = DEFAULT_STATE_PROFILE.detail_refresh_move_settle_seconds
DETAIL_PAGE_WAIT_TIMEOUT_SECONDS = DEFAULT_STATE_PROFILE.detail_page_wait_timeout_seconds
INVENTORY_PAGE_WAIT_TIMEOUT_SECONDS = DEFAULT_STATE_PROFILE.inventory_page_wait_timeout_seconds
PAGE_STATE_POLL_SECONDS = DEFAULT_STATE_PROFILE.page_state_poll_seconds
PAGE_STATE_STABLE_FRAMES = DEFAULT_STATE_PROFILE.page_state_stable_frames
PAGE_STATE_DETECTED_SETTLE_SECONDS = DEFAULT_STATE_PROFILE.page_state_detected_settle_seconds
EQUIPMENT_SWITCH_SETTLE_SECONDS = DEFAULT_STATE_PROFILE.equipment_switch_settle_seconds
LOCK_DISCARD_CONFIRM_SECONDS = DEFAULT_STATE_PROFILE.popup_confirm_seconds
ACTION_MENU_SETTLE_SECONDS = DEFAULT_STATE_PROFILE.action_menu_settle_seconds
ACTION_APPLY_SETTLE_SECONDS = DEFAULT_STATE_PROFILE.action_apply_settle_seconds
MENU_AFTER_EQUIPMENT_MOVE_SECONDS = DEFAULT_STATE_PROFILE.menu_after_equipment_move_seconds
ACTION_OPTION_MOVE_SETTLE_SECONDS = DEFAULT_STATE_PROFILE.action_option_move_settle_seconds


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
        self.action_profile = DEFAULT_SCAN_PROFILE

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
        time.sleep(self.action_profile.virtual_gamepad_connect_settle_seconds)
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

    def _profile(self) -> GamepadActionProfile:
        return getattr(self, "action_profile", DEFAULT_SCAN_PROFILE)

    def push_left_joystick(self, x, y, hold_seconds=None, settle_seconds=None):
        profile = self._profile()
        if hold_seconds is None:
            hold_seconds = profile.move_hold_seconds
        if settle_seconds is None:
            settle_seconds = profile.move_settle_seconds
        self.gamepad.left_joystick_float(x_value_float=x, y_value_float=y)
        self.gamepad.update()
        time.sleep(hold_seconds)
        self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        self.gamepad.update()
        time.sleep(settle_seconds)

    def _press_button(self, button, hold_seconds=None, settle_seconds=None):
        profile = self._profile()
        if hold_seconds is None:
            hold_seconds = profile.button_hold_seconds
        if settle_seconds is None:
            settle_seconds = profile.button_settle_seconds
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
        time.sleep(self._profile().action_menu_settle_seconds)

    def _confirm_action(self):
        self._press_a()
        time.sleep(self._profile().action_apply_settle_seconds)

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
                    hold_seconds=self._profile().row_move_hold_seconds,
                    settle_seconds=self._profile().row_move_settle_seconds,
                )
            elif move == "D":
                self.push_left_joystick(
                    0.0,
                    -1.0,
                    hold_seconds=self._profile().row_move_hold_seconds,
                    settle_seconds=self._profile().row_move_settle_seconds,
                )

    def _move_between_equipment(self, moves):
        self._apply_moves(moves)
        if moves:
            time.sleep(self._profile().equipment_switch_settle_seconds)

    def _settle_before_action_menu(self, moved: bool):
        if moved:
            time.sleep(self._profile().menu_after_equipment_move_seconds)

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

    def _screenshot_to_bgr(self, screenshot) -> np.ndarray | None:
        try:
            image = np.array(screenshot)
        except Exception:
            return None
        if image.ndim != 3 or image.shape[2] < 3:
            return None
        return image[:, :, :3]

    def _relative_roi(self, image: np.ndarray, x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
        height, width = image.shape[:2]
        left = max(0, min(width, int(round(width * x1))))
        top = max(0, min(height, int(round(height * y1))))
        right = max(left + 1, min(width, int(round(width * x2))))
        bottom = max(top + 1, min(height, int(round(height * y2))))
        return image[top:bottom, left:right]

    def _white_fraction(self, roi: np.ndarray, threshold: int = 185) -> float:
        if roi.size == 0:
            return 0.0
        return float(((roi[:, :, 0] > threshold) & (roi[:, :, 1] > threshold) & (roi[:, :, 2] > threshold)).mean())

    def _gray_fraction(self, roi: np.ndarray) -> float:
        if roi.size == 0:
            return 0.0
        b = roi[:, :, 0].astype(np.int16)
        g = roi[:, :, 1].astype(np.int16)
        r = roi[:, :, 2].astype(np.int16)
        brightness = (b + g + r) / 3.0
        channel_spread = np.maximum.reduce([b, g, r]) - np.minimum.reduce([b, g, r])
        return float(((brightness > 35) & (brightness < 145) & (channel_spread < 38)).mean())

    def _pink_fraction(self, roi: np.ndarray) -> float:
        if roi.size == 0:
            return 0.0
        b = roi[:, :, 0].astype(np.int16)
        g = roi[:, :, 1].astype(np.int16)
        r = roi[:, :, 2].astype(np.int16)
        return float(((r > 150) & (b > 105) & (g < 125) & ((r - g) > 55) & ((b - g) > 25)).mean())

    def _vision_metric_value(self, image: np.ndarray, rule: GamepadVisionRule) -> float:
        roi = self._relative_roi(image, *rule.roi)
        if rule.metric == "white":
            return self._white_fraction(roi, threshold=rule.pixel_threshold)
        if rule.metric == "gray":
            return self._gray_fraction(roi)
        if rule.metric == "pink":
            return self._pink_fraction(roi)
        return 0.0

    def _evaluate_vision_rules(self, image: np.ndarray, rules: tuple[GamepadVisionRule, ...]) -> tuple[bool, str]:
        values = []
        for rule in rules:
            value = self._vision_metric_value(image, rule)
            passed = value > rule.threshold if rule.operator == "gt" else value < rule.threshold
            values.append(f"{rule.name}={value:.3f}{rule.operator}{rule.threshold:.3f}:{'ok' if passed else 'fail'}")
            if not passed:
                return False, "; ".join(values)
        return True, "; ".join(values)

    def _looks_like_detail_page(self, image: np.ndarray) -> tuple[bool, str]:
        reasons = []
        for index, rules in enumerate(self._profile().vision.detail_page_rule_groups, start=1):
            matched, reason = self._evaluate_vision_rules(image, rules)
            group_reason = f"详情规则组{index}: {reason}"
            if matched:
                return True, group_reason
            reasons.append(group_reason)
        return False, " | ".join(reasons)

    def _looks_like_inventory_first_item(self, image: np.ndarray) -> tuple[bool, str]:
        return self._evaluate_vision_rules(image, self._profile().vision.inventory_first_item_rules)

    def _wait_for_page_state(self, predicate, timeout_seconds: float, label: str) -> bool:
        profile = self._profile()
        deadline = time.monotonic() + timeout_seconds
        stable = 0
        last_reason = "未获取到有效截图"
        with mss.MSS() as sct:
            while time.monotonic() < deadline and not self._stopped:
                screenshot, _ = capture_foreground_window(sct)
                image = self._screenshot_to_bgr(screenshot)
                matched = False
                if image is not None:
                    matched, last_reason = predicate(image)
                if matched:
                    stable += 1
                    if stable >= profile.page_state_stable_frames:
                        logger.info(f"页面识别成功: {label} | {last_reason}")
                        return True
                else:
                    stable = 0
                time.sleep(profile.page_state_poll_seconds)
        logger.warning(f"页面识别超时: {label} | {last_reason}")
        return False

    def _wait_for_detail_page(self) -> bool:
        return self._wait_for_page_state(
            self._looks_like_detail_page,
            self._profile().detail_page_wait_timeout_seconds,
            "强化详情页",
        )

    def _wait_for_inventory_first_item(self) -> bool:
        return self._wait_for_page_state(
            self._looks_like_inventory_first_item,
            self._profile().inventory_page_wait_timeout_seconds,
            "背包第一页第一格",
        )

    def _refresh_to_first_item(self):
        logger.info("发送详情页回跳定位: D -> Y -> 等待详情页 -> B -> 等待背包第一页第一格")
        self._apply_moves(["D"])
        time.sleep(self._profile().detail_refresh_move_settle_seconds)
        self._press_y()
        if not self._wait_for_detail_page():
            raise RuntimeError("按 Y 后未检测到强化详情页，已停止扫描后管理以避免误操作。")
        time.sleep(self._profile().page_state_detected_settle_seconds)
        self._press_b()
        if not self._wait_for_inventory_first_item():
            raise RuntimeError("按 B 返回后未检测到背包第一页第一格，已停止扫描后管理以避免误操作。")
        time.sleep(self._profile().page_state_detected_settle_seconds)

    def _sync_selected_equipment_state(self, current_state: str, target_state: str) -> bool:
        return self._sync_selected_equipment_state_with_profile(
            current_state,
            target_state,
            DEFAULT_STATE_PROFILE,
        )

    def _sync_selected_equipment_state_with_profile(
        self,
        current_state: str,
        target_state: str,
        profile: GamepadActionProfile,
    ) -> bool:
        current_state = current_state if current_state in {"normal", "locked", "discarded"} else "normal"
        target_state = target_state if target_state in {"normal", "locked", "discarded"} else "normal"
        if current_state == target_state:
            return False
        previous_profile = getattr(self, "action_profile", DEFAULT_SCAN_PROFILE)
        self.action_profile = profile
        try:
            if profile.operation_style == "dpad":
                return self._sync_selected_equipment_state_dpad(current_state, target_state, profile)
            return self._sync_selected_equipment_state_menu(current_state, target_state, profile)
        finally:
            self.action_profile = previous_profile

    def _sync_selected_equipment_state_menu(
        self,
        current_state: str,
        target_state: str,
        profile: GamepadActionProfile,
    ) -> bool:
        self._toggle_action_menu()
        if target_state == "discarded":
            self._press_a()
            if current_state == "locked":
                time.sleep(profile.popup_confirm_seconds)
                self._press_a()
                time.sleep(profile.popup_confirm_seconds)
            else:
                time.sleep(profile.action_apply_settle_seconds)
        elif target_state == "locked":
            self._apply_moves(["R"])
            time.sleep(profile.action_option_move_settle_seconds)
            self._confirm_action()
        elif current_state == "discarded":
            self._confirm_action()
        elif current_state == "locked":
            self._apply_moves(["R"])
            time.sleep(profile.action_option_move_settle_seconds)
            self._confirm_action()
        self._toggle_action_menu()
        return True

    def _sync_selected_equipment_state_hmt(self, current_state: str, target_state: str) -> bool:
        return self._sync_selected_equipment_state_with_profile(
            current_state,
            target_state,
            HMT_STATE_PROFILE,
        )

    def _sync_selected_equipment_state_dpad(
        self,
        current_state: str,
        target_state: str,
        profile: GamepadActionProfile,
    ) -> bool:
        current_state = current_state if current_state in {"normal", "locked", "discarded"} else "normal"
        target_state = target_state if target_state in {"normal", "locked", "discarded"} else "normal"
        if current_state == target_state:
            return False
        if target_state == "discarded":
            self._press_dpad_left()
            if current_state == "locked" and target_state == "discarded":
                time.sleep(profile.popup_confirm_seconds)
                self._press_a()
                time.sleep(profile.popup_confirm_seconds)
            else:
                time.sleep(profile.action_apply_settle_seconds)
            return True
        if target_state == "locked":
            self._press_dpad_right()
            time.sleep(profile.action_apply_settle_seconds)
            return True
        if current_state == "discarded":
            self._press_dpad_left()
            time.sleep(profile.action_apply_settle_seconds)
            return True
        if current_state == "locked":
            self._press_dpad_right()
            time.sleep(profile.action_apply_settle_seconds)
            return True
        return False

    def sync_equipment_states(self, total_drives: int, state_changes: list[dict], action_mode: str = "default") -> int:
        profile = GamepadActionProfile.state_management("hmt" if action_mode == "hmt" else "cn")
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

        previous_profile = getattr(self, "action_profile", DEFAULT_SCAN_PROFILE)
        self.action_profile = profile
        try:
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
                changed = self._sync_selected_equipment_state_with_profile(
                    change.get("current_state"),
                    change.get("target_state"),
                    profile,
                )
                if changed:
                    applied += 1
                    logger.info(
                        f"已同步状态: raw_drive_{index:04d} "
                        f"{change.get('current_state')} -> {change.get('target_state')}"
                    )
            return applied
        finally:
            self.action_profile = previous_profile

    def start_scan(self, total_drives=None, on_capture=None, commit_on_complete=True):
        scan_start = time.perf_counter()
        profile = self._profile()
        logger.warning("\n" + "=" * 50)
        logger.warning(f"虚拟手柄已就位，将在 {profile.takeover_countdown_seconds:g} 秒后接管控制，请切回游戏")
        logger.warning("请确保此时已选中第一排第一个驱动/卡带")
        logger.warning("=" * 50)
        time.sleep(profile.takeover_countdown_seconds)

        if total_drives is None:
            raise ValueError("全量扫描需要先填写库存数量。")
        total_drives = int(total_drives)
        if not 0 < total_drives <= self.MAX_INVENTORY_COUNT:
            raise ValueError(f"库存数量必须在 1-{self.MAX_INVENTORY_COUNT} 之间。")

        logger.info("\n====== 发送撞墙唤醒信号 ======")
        self.push_left_joystick(-1.0, 0.0)
        time.sleep(profile.wall_wake_settle_seconds)

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
