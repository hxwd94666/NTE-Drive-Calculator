# 在现有原生会话上传递轮盘租约和状态，不单独创建采集连接或重放动作。
class QuickWheelConnectionChanged(RuntimeError):
    """连接更换后先通知 owner 换代，不向新进程传递旧动作应答。"""


def exchange_quick_wheel(session, payload, context_key):
    if payload['enabled']:
        session._guard('native_load')
    with session._lock:
        if context_key != session._current_context() or session._close_requested.is_set():
            raise LookupError('账号或原生会话已改变')
        client = session._connect() if payload['enabled'] else session._client
        if client is None or not client.is_running:
            if payload['enabled']:
                raise LookupError('等待游戏连接')
            session._quick_wheel_active = False
            return None
        if 'native_quick_wheel_v2' not in (client.hello_result or {}).get('capabilities', ()):
            raise NotImplementedError('当前组件不支持快捷轮盘，请更新配套组件并重启游戏')  # ui-text: internal
        if payload['enabled'] and session._quick_wheel_client is not client:
            session._quick_wheel_client = client
            raise QuickWheelConnectionChanged('轮盘连接已更换')
        if context_key != session._current_context():
            raise LookupError('账号已改变，未派发轮盘请求')
        # 未知启用结果仍保留消费标志，显式停用或连接收尾才释放。
        session._quick_wheel_active = True
        result = client.call('native.wheel.exchange', payload, timeout=1.5)
        if not isinstance(result, dict) or result.get('owner') != payload['owner']:
            raise ValueError('轮盘回复身份不匹配')
        if not payload['enabled'] and result.get('ready') is False:
            session._quick_wheel_active = False
        return result
