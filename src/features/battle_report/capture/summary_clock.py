# 为实时战报页面和悬浮窗标明采集摘要实际使用的 DPS 计时方式。
def summary_clock_label(mode: str) -> str:
    return {
        "subtract_time_stop": "有效时间",
        "wall_clock": "真实时间",
    }.get(mode, "计时未知")
