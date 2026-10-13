# 冻结性能请求的账号代次与原连接；未知派发保留占用，关闭不重连也不提升权限。
from src.integrations.nte_core_protocol import NteCoreRpcError


def _check_performance_request(session, context_key):
    session._guard("native_load")
    if (context_key != session._current_context() or session._closing
            or session._close_requested.is_set() or session._permanently_closed.is_set()):
        raise LookupError("性能请求所属账号或会话已改变")


def configure_performance(session, payload, context_key):
    enabled = payload["enabled"]
    if enabled:
        session._guard("native_load")
    # 等锁期间可能撤权或换代；旧请求不借新账号连接完成。
    with session._lock:
        if enabled:
            _check_performance_request(session, context_key)
            client = session._connect()
        else:
            client = session._client
        if client is None or not client.is_running:
            if enabled:
                raise LookupError("等待原生组件")
            return {"enabled": False, "overlay": False, "installed": False, "rejected": False}
        if not enabled:
            owner = session._performance_owner
            if (owner is None or owner[0] is not client or owner[1] != context_key
                    or session._connected_context != context_key):
                raise LookupError("性能清理所属连接已改变，未向新连接发送旧清理")
        if "native_performance_v1" not in (client.hello_result or {}).get("capabilities", ()):
            raise NotImplementedError("当前组件不支持性能悬浮窗，需要配套更新")
        if enabled:
            # connect/start 也可能等待；紧邻 RPC 再复核当前权限、代次和同一连接。
            _check_performance_request(session, context_key)
            if session._client is not client or session._connected_context != context_key:
                raise LookupError("性能连接已改变，未派发旧请求")
            session._performance_owner = (client, context_key)
        result = client.call("native.performance.configure", payload, timeout=2.0)
        if (not isinstance(result, dict)
                or any(type(result.get(k)) is not bool for k in ("enabled", "overlay", "installed", "rendered", "rejected"))
                or result["enabled"] != enabled or result["overlay"] != payload["overlay"]
                or result.get("cost_coverage") != "native_dispatch_and_hud_v1"
                or result.get("cost_complete") is not False
                or (result.get("cost_us") is not None and
                    (type(result["cost_us"]) is not int or result["cost_us"] < 0))):
            raise ValueError("性能组件返回的状态未确认")
        # timeout、传输故障及畸形回复不清 owner；只确认完整停用回复才释放。
        session._performance_active = enabled
        if not enabled:
            session._performance_owner = None
        return result


def read_performance_status(self):
    """只读现存连接的计数，不启动采集或建立新连接。"""
    self._guard("native_sync")
    with self._lock:
        client, context = self._client, self._current_context()
        if (client is None or self._failed or self._close_requested.is_set()
                or not self._context_matches() or not client.is_running):
            raise LookupError("native_session_unavailable")
    status = self._status_queries.read(client, timeout=2.0)
    self._guard("native_sync")
    if (client is not self._client or context != self._current_context()
            or self._close_requested.is_set()):
        raise LookupError("native_session_changed")
    return client, status


def control_performance_trace(session, payload, context_key):
    if payload['action'] != 'stop':
        session._guard('native_load')
    with session._lock:
        if session._current_context() != context_key or session._close_requested.is_set():
            raise LookupError('账号或会话已改变，未向新会话发送采样指令')
        client = session._connect() if payload['action'] == 'start' else session._client
        if client is None or not client.is_running:
            raise LookupError('原生会话已断开')
        if 'native_performance_trace_v1' not in (client.hello_result or {}).get('capabilities', ()):
            raise NotImplementedError('当前组件不支持细分性能采样，需要更新配套 Core、宿主和插件')
        if session._current_context() != context_key:
            raise LookupError('账号已改变')
        if payload['action'] == 'start':
            session._performance_trace_active = True  # 未知开始结果保留原租约，不重发 start。
            session._performance_trace_owner = (client, payload['trace_id'])
        elif session._performance_trace_owner != (client, payload['trace_id']):
            raise LookupError('采样所属连接已改变，未向新连接发送旧采样指令')
        try:
            result = client.call('native.performance.trace', payload, timeout=2.0)
        except NteCoreRpcError as error:
            if payload['action'] == 'start' and error.message in {
                'performance_plugin_unavailable', 'performance_busy', 'performance_start_failed',
                'invalid_trace_id', 'invalid_action', 'invalid_services',
            }:
                session._performance_trace_active = False
            raise
        if isinstance(result, dict) and result.get('trace_id') == payload['trace_id'] and result.get('active') is False:
            session._performance_trace_active = False
        return result
