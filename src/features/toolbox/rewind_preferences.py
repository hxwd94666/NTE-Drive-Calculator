# 校验倒带推荐的已保存自定义百分比。
def preference_custom_percent(value: object) -> float | None:
    """Read a persisted optional custom rewind threshold without trusting old data."""

    if isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if 1.0 <= percent <= 100.0 else None
