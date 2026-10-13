# 通过核对过的采集 Core 控制常驻宿主，超时只报结果未知，不重发变更。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time

from src.integrations.native_plugin_bundle import HOT_PLUGIN_LAYOUTS, inspect_native_plugin_bundle


class NativeHostError(RuntimeError):
    pass


class NativeHostOutcomeUnknown(NativeHostError):
    pass


class NativeHostClient:
    def __init__(self, application_root: Path, game_pid: int):
        bundle = inspect_native_plugin_bundle(application_root)
        if not bundle.ready or bundle.layout not in HOT_PLUGIN_LAYOUTS:
            raise NativeHostError('当前组件包尚未提供受限插件宿主。')
        if type(game_pid) is not int or game_pid <= 0:
            raise NativeHostError('缺少本次游戏进程身份。')
        self.executable = application_root / bundle.roles['core']
        self.digest = bundle.files[bundle.roles['core']]
        self.pid = game_pid
        self.session = None

    def call(self, action: str, plugin: str = ''):
        with self.executable.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != self.digest:
                raise NativeHostError('采集 Core 在本次操作期间发生变化。')
        command = [str(self.executable), 'native-host', str(self.pid), action]
        if plugin or self.session is not None:
            command.append(plugin)
        if self.session is not None:
            command.append(str(self.session))
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                    timeout=9, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            raise NativeHostOutcomeUnknown('宿主请求超时，结果未知；停止后续操作，不自动重发。') from error
        if result.returncode:
            # 传输失败也可能发生在变更已派发之后；保留结果未知，不据此重发。
            raise NativeHostOutcomeUnknown('宿主控制未完成：' + result.stderr.strip()[:500])
        try:
            envelope = json.loads(result.stdout)
            created = envelope['hostCreated']
            if type(created) is not int or created <= 0 or (self.session is not None and self.session != created):
                raise ValueError('host generation mismatch')
            self.session = created
            return envelope['result']
        except (ValueError, KeyError, TypeError) as error:
            raise NativeHostOutcomeUnknown('宿主返回格式不正确，不能确认操作结果。') from error

    def change(self, action: str, plugin: str, *, timeout: float = 15):
        accepted = self.call(action, plugin)
        operation_id = accepted.get('operationId')
        if type(operation_id) is not int or operation_id <= 0:
            raise NativeHostError('宿主未接受插件操作。')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.call('operation')
            if status.get('operationId') != operation_id:
                raise NativeHostOutcomeUnknown('插件操作标识已改变，不能将其他操作当成本次成功。')
            if status.get('state') == 'completed':
                return status
            if status.get('state') == 'failed':
                raise NativeHostError('插件操作失败：' + str(status.get('error', '')))
            if status.get('state') not in {'queued', 'pending'}:
                raise NativeHostOutcomeUnknown('插件操作状态未知。')
            time.sleep(.1)
        raise NativeHostOutcomeUnknown('插件仍在排空，未确认卸载；停止后续操作。')
