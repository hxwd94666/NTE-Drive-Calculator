# 自动装配仅发送阶段与角色证据；展示回调失败不改变游戏输入任务。
from src.utils.logger import logger


def report_role_progress(callback, role_name, state, *, verified=False, dispatched=False):
    if callback is None:
        return
    try:
        callback(
            {
                "role_name": role_name,
                "state": state,
                "phase": "verifying" if state == "waiting" else "operating",
                "verified": verified,
                "dispatched": dispatched,
                "scope": "full" if verified else "none",
            }
        )
    except Exception:
        logger.debug("自动装配状态回调失败", exc_info=True)
