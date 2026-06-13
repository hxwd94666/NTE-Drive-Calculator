"""Console-friendly board visualization for solved equipment layouts."""

import collections
from src.utils.logger import logger

class BoardVisualizer:
    # 定义 12 种形状的专属 24位真彩色 (ANSI True Color)
    COLORS = {
        "Trap_4_V": "\033[38;2;255;87;34m",  # 深橙
        "Trap_4_H": "\033[38;2;255;152;0m",  # 亮橙
        "V_4": "\033[38;2;255;235;59m",  # 黄
        "H_4": "\033[38;2;76;175;80m",  # 绿
        "L_3_TL": "\033[38;2;0;188;212m",  # 青
        "L_3_TR": "\033[38;2;33;150;243m",  # 蓝
        "L_3_BL": "\033[38;2;103;58;183m",  # 深紫
        "L_3_BR": "\033[38;2;156;39;176m",  # 品红
        "V_3": "\033[38;2;233;30;99m",  # 粉
        "H_3": "\033[38;2;244;67;54m",  # 红
        "V_2": "\033[38;2;0;150;136m",  # 蓝绿(Teal)
        "H_2": "\033[38;2;139;195;74m",  # 浅绿
    }

    GRAY = "\033[38;2;100;100;100m"  # 暗灰色
    RED_ALERT = "\033[38;2;255;0;0m"  # 红色警报
    RESET = "\033[0m"

    @classmethod
    def _validate_board_integrity(cls, board: list, assigned_drives: list) -> bool:
        """校验矩阵图纸中的形状与实际分配的驱动是否一致"""
        assigned_shapes = set([d.shape_id for d in assigned_drives])

        # 2. 提取图纸矩阵里的形状 ID 集合
        board_shapes = set()
        for row in board:
            for cell in row:
                if isinstance(cell, str) and cell not in ["0", "-1"]:
                    board_shapes.add(cell)

        # 3. 交叉比对
        if assigned_shapes != board_shapes:
            logger.error(f"{cls.RED_ALERT}[严重警告] 图纸渲染终止：矩阵与分配数据不一致{cls.RESET}")
            logger.error(f"  - 图纸画出的形状: {board_shapes}")
            logger.error(f"  - 实际分配的形状: {assigned_shapes}")
            return False

        return True

    @classmethod
    def display_final_plan(cls, role_name: str, plan: dict, default_set: str, grade: str):
        """渲染最终方案的彩色棋盘图"""
        board = plan["blueprint"].get("board", [])
        extra_pieces = plan["blueprint"].get("extra_pieces", [])

        # 将套装驱动和散件驱动合并
        all_assigned_drives = plan.get("assigned_set_drives", []) + plan.get("assigned_extra_drives", [])

        logger.info(f"[{role_name}] 综合评级: [{grade}] | 总得分: {plan['score']} / 350.0 分")
        logger.info(f"  专属套装: {default_set}")
        logger.info(f"  额外散件: {', '.join(extra_pieces)}")

        if not board:
            logger.error(f"{cls.RED_ALERT}  错误：未接收到有效的二维图纸(board)数据{cls.RESET}")
            return

        # 强制启动图纸一致性校验
        if not cls._validate_board_integrity(board, all_assigned_drives):
            return

        logger.info("  镶嵌图纸:")

        # 严格 10 字符宽度网格对齐打印
        for row in board:
            formatted_row = []
            for cell in row:
                if cell == -1:
                    formatted_row.append(f"{cls.GRAY}[   XX   ]{cls.RESET}")
                elif cell == 0:
                    formatted_row.append(f"{cls.GRAY}[        ]{cls.RESET}")
                else:
                    color = cls.COLORS.get(cell, "\033[38;2;255;255;255m")
                    name = str(cell)[:8].center(8)
                    formatted_row.append(f"{color}[{name}]{cls.RESET}")

            logger.opt(raw=True).info("      " + "".join(formatted_row) + "\n")
        logger.opt(raw=True).info("-" * 65 + "\n")
