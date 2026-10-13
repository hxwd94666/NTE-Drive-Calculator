# 测量真实基础权重页的进程冷/热响应、合成提交与物理收尾，不连接游戏或真实账号。
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import traceback
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


def summarize(values):
    """近邻秩分位数；小样本 P99 接近最大值，不冒称稳定尾部估计。"""
    values = sorted(values)
    if not values:
        raise ValueError('没有成功样本')
    return {'samples': len(values), 'median_ms': round(statistics.median(values), 3),
            'p95_ms': round(values[math.ceil(len(values) * .95) - 1], 3),
            'p99_ms': round(values[math.ceil(len(values) * .99) - 1], 3),
            'max_ms': round(max(values), 3)}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def isolate_source(source):
    # 只插入 sys.path 会被旧 editable finder 兜底；移除本项目兜底后审计全部实际 src 来源。
    def editable(value):
        return getattr(value, '__module__', '').startswith('__editable___nte_drive_calculator_')
    sys.meta_path[:] = [value for value in sys.meta_path if not editable(value)]
    sys.path_hooks[:] = [value for value in sys.path_hooks if not editable(value)]
    sys.path[:] = [str(source), str(source / 'tests'), *[value for value in sys.path
                   if not str(value).startswith('__editable__.nte_drive_calculator-')]]
    sys.path_importer_cache.clear()


def import_audit(source):
    loaded, foreign = {}, []
    for name, module in sorted(sys.modules.items()):
        if name != 'src' and not name.startswith('src.'):
            continue
        entries = list(getattr(module, '__path__', ()))
        filename = getattr(module, '__file__', None)
        if filename:
            entries.append(filename)
        loaded[name] = [str(Path(path).resolve()) for path in entries]
        foreign.extend((name, path) for path in loaded[name] if not Path(path).is_relative_to(source))
    return {'modules': loaded, 'foreign_project_modules': foreign}


def resources():
    """读取本进程原生工作集和句柄；不是 Python 分配器内存，也不是泄漏判定。"""
    if sys.platform != 'win32':
        return {'working_set_bytes': None, 'peak_working_set_bytes': None, 'handles': None, 'native_threads': None}
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                       'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                       'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    memory = Counters()
    memory.cb = ctypes.sizeof(memory)
    handle = kernel.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    count = wintypes.DWORD()
    if not kernel.GetProcessHandleCount(handle, ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    class ThreadEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('thread_id', wintypes.DWORD), ('owner_pid', wintypes.DWORD),
                    ('base_priority', wintypes.LONG), ('delta_priority', wintypes.LONG),
                    ('flags', wintypes.DWORD)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    threads = 0
    entry = ThreadEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        valid = kernel.Thread32First(snapshot, ctypes.byref(entry))
        if not valid:
            raise ctypes.WinError(ctypes.get_last_error())
        while valid:
            threads += entry.owner_pid == os.getpid()
            valid = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return {'native_threads': threads, 'working_set_bytes': int(memory.WorkingSetSize),
            'peak_working_set_bytes': int(memory.PeakWorkingSetSize), 'handles': count.value}


def restrict_cpu(enabled):
    if not enabled:
        return None
    if sys.platform != 'win32':
        raise RuntimeError('单 CPU 代理目前仅支持 Windows')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    process, system = ctypes.c_size_t(), ctypes.c_size_t()
    handle = kernel.GetCurrentProcess()
    if not kernel.GetProcessAffinityMask(handle, ctypes.byref(process), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    chosen = process.value & -process.value
    if not chosen or not kernel.SetProcessAffinityMask(handle, chosen):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'before_mask': process.value, 'selected_mask': chosen, 'logical_cpus': 1}


def fixture_inputs(fixture):
    marker = json.loads((fixture / 'ui-benchmark-fixture.json').read_text(encoding='utf-8'))
    if marker.get('isolated') is not True or marker.get('synthetic') is not True:
        raise ValueError('需要独立新建的合成 fixture，不读取真实账号副本')
    files = ['user.sqlite3', 'shared.sqlite3', 'game_static.sqlite3', 'manifest.json', 'config/stats.json',
             'config/workshop_weight_template.json', 'ui-benchmark-fixture.json']
    paths = {name: fixture / name for name in files}
    for path in paths.values():
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(fixture):
            raise ValueError('fixture 成员必须是目录内真实文件')
    return {name: sha256(path) for name, path in paths.items()}


def worker(args):
    source, fixture = args.source_root.resolve(), args.fixture.resolve()
    isolate_source(source)
    affinity = restrict_cpu(args.one_cpu)
    inputs = fixture_inputs(fixture)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['NTE_GAME_STATIC_DB'] = str(fixture / 'game_static.sqlite3')
    os.environ['NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE'] = str(fixture / 'config/workshop_weight_template.json')
    started = time.perf_counter()
    from PySide6.QtCore import QEvent, QEventLoop, QThread, QTimer
    from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QMessageBox, QPushButton, QWidget
    from shiboken6 import isValid
    from src.app.page_tasks import PageTaskLane, PageCommitLane
    from src.domain.recommended_weights import base_weight_character_id
    from src.features.configuration.controller import BasicWeightController
    from src.features.configuration.page_owner import BasicWeightPage
    from src.services.characters.basic_weight_service import BasicWeightService
    from src.services.characters import workshop_weight_template_service as workshop
    from src.storage.sqlite.user_data_dao import UserDataDao
    from src.utils.logger import logger
    import_ms = (time.perf_counter() - started) * 1000
    logger.remove()
    app = QApplication.instance() or QApplication([])
    failures, changes, heartbeat = [], [], []
    originals = {name: getattr(QMessageBox, name) for name in ('information', 'question', 'warning')}
    QMessageBox.information = lambda *_args: None
    QMessageBox.question = lambda *_args: QMessageBox.Yes
    QMessageBox.warning = lambda *_args: failures.append(str(_args[-1]))
    counters = {'service_reads': 0, 'template_reads': 0}
    original_load, original_template = BasicWeightService.load_form_data, workshop._read_template
    def counted_load(service):
        counters['service_reads'] += 1
        return original_load(service)
    def counted_template(path):
        counters['template_reads'] += 1
        return original_template(path)
    BasicWeightService.load_form_data, workshop._read_template = counted_load, counted_template
    timer = QTimer()
    last_tick = time.perf_counter()
    def tick():
        nonlocal last_tick
        now = time.perf_counter()
        heartbeat.append((now, max(0, (now - last_tick) * 1000 - 10)))
        last_tick = now
    timer.timeout.connect(tick)
    timer.start(10)

    def wait_for(predicate, timeout=60, check_failures=True):
        loop, poll = QEventLoop(), QTimer()
        error = []
        deadline = time.perf_counter() + timeout
        def check():
            try:
                if check_failures and failures:
                    raise RuntimeError(failures[-1])
                if predicate():
                    loop.quit()
                elif time.perf_counter() >= deadline:
                    raise TimeoutError('页面动作或物理任务未达终态')
            except BaseException as exc:
                error.append(exc)
                loop.quit()
        poll.timeout.connect(check)
        poll.start(2)
        loop.exec()
        poll.stop()
        if error:
            raise error[0]

    wait_for(lambda: len(heartbeat) >= 2)
    shell, page = None, None
    output = {'status': 'running', 'mode': args.worker_mode, 'import_ms': import_ms,
              'affinity': affinity, 'samples': {}, 'baseline_resources': resources()}
    def idle():
        owner = page.task_owner
        return owner is not None and all(not lane.is_running() for lane in [*owner.findChildren(PageTaskLane), *owner.findChildren(PageCommitLane)])
    def ready():
        if '失败' in page.config_load_status.text():
            raise RuntimeError(page.config_load_status.text())
        return idle() and page._config_form_data is not None and page.config_form_area.isEnabled()
    def measure(kind, action, complete):
        output['phase'] = kind
        # 事件循环先启动再调动作；收到动作完成后的下一心跳才结束采样，不漏末段阻塞。
        prepared_tick = len(heartbeat)
        wait_for(lambda: len(heartbeat) > prepared_tick)  # 准备/DAO/观测的停顿归全程记录，不串入下一动作。
        fired, error, timing = [], [], {}
        first_heartbeat = len(heartbeat)
        before_counters = dict(counters)
        began = time.perf_counter()
        def run_action():
            start = time.perf_counter()
            try:
                action()
            except BaseException as exc:
                error.append(exc)
            timing['callback_ms'] = (time.perf_counter() - start) * 1000
            fired.append(True)
        QTimer.singleShot(0, run_action)
        wait_for(lambda: bool(fired) and (bool(error) or complete()))
        if error:
            raise error[0]
        timing['ready_ms'] = (time.perf_counter() - began) * 1000
        completed_tick = len(heartbeat)
        wait_for(lambda: len(heartbeat) > completed_tick)
        values = [delay for _at, delay in heartbeat[first_heartbeat:]]
        timing.update(max_event_loop_delay_ms=max(values, default=0),
                      heartbeats=len(values), stalls_over_200ms=sum(value > 200 for value in values),
                      widgets=len(app.allWidgets()), owners=len(page.findChildren(BasicWeightController)),
                      running_owned_threads=sum(thread.isRunning() for thread in page.findChildren(QThread)),
                      resources=resources(), counters_delta={key: counters[key] - before_counters[key] for key in counters}, **counters)
        output['samples'].setdefault(kind, []).append(timing)
        return timing

    temporary = tempfile.TemporaryDirectory(prefix='weight-measure-', dir=fixture)
    try:
        database = Path(temporary.name) / 'user.sqlite3'
        with closing(sqlite3.connect((fixture / 'user.sqlite3').as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(database)) as clone:
                original.backup(clone)
        context = SimpleNamespace(generation=1,
            account=SimpleNamespace(active_account_id='weight-benchmark', user_database_path=database),
            paths=SimpleNamespace(config_dir=fixture / 'config',
                equipment_allocation_database_path=fixture / 'game_static.sqlite3',
                shared_database_path=fixture / 'shared.sqlite3'))
        def open_page():
            nonlocal shell, page
            shell = QWidget()
            build_start = time.perf_counter()
            page = BasicWeightPage(context, lambda _key: None, lambda: changes.append(1), shell)
            output['widget_construct_ms'] = (time.perf_counter() - build_start) * 1000
            shell.resize(1200, 800)
            page.resize(1200, 800)
            shell.show()
            page.refresh()
        measure('first_enter', open_page, ready)
        assert '1051' in page._config_form_data and '1003' in page._config_form_data
        output['initial_model_digest'] = hashlib.sha256(json.dumps(page._config_form_data, sort_keys=True, default=str).encode()).hexdigest()
        def select_role():
            tabs = page.config_roles_tabs
            tabs.setCurrentIndex(next(index for index in range(tabs.count())
                if tabs.widget(index).property('role_key') == '1051'))
        initial_weight = page._config_form_data['1051']['weights']['CritBase']
        with UserDataDao(database) as dao:
            other = dao.get_character_weight_preferences(base_weight_character_id(1003))
        loops = args.iterations if args.worker_mode == 'hot' else 1
        output['correctness'] = []
        for index in range(loops):
            output['iteration'] = index + 1
            if args.worker_mode == 'hot':
                measure('unchanged_refresh', page.refresh, ready)
            tabs = page.config_roles_tabs
            tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.widget(i).property('role_key') == '1003'))
            wait_for(lambda: ready() and page._config_active_role == '1003')
            target_index = next(i for i in range(tabs.count()) if tabs.widget(i).property('role_key') == '1051')
            already_loaded = tabs.widget(target_index).findChild(QDoubleSpinBox, 'weight-weights-CritBase') is not None
            measure('select_role_loaded' if already_loaded else 'select_role_lazy', select_role,
                    lambda: ready() and page._config_active_role == '1051')
            spin = page.config_roles_tabs.currentWidget().findChild(QDoubleSpinBox, 'weight-weights-CritBase')
            assert spin is not None
            value = round(2.0 + (index + 1) / 100, 3)  # 与真实页面的三位小数持久化规则保持一致。
            spin.setValue(value)
            spin.editingFinished.emit()
            assert page.is_dirty()
            before_change = len(changes)
            save = next(button for button in page.findChildren(QPushButton) if button.text() == '保存')
            measure('save', save.click, lambda: idle() and not page.is_dirty() and len(changes) == before_change + 1)
            with UserDataDao(database) as dao:
                saved = dao.get_character_weight_preferences(base_weight_character_id(1051))
                output['last_observation'] = {'phase': 'validate_save', 'expected': value,
                                              'actual': saved['property_weights']['CritBase']}
                assert saved['property_weights']['CritBase'] == value, output['last_observation']
                assert dao.get_character_weight_preferences(base_weight_character_id(1003)) == other
            before_change = len(changes)
            reset = next(button for button in page.findChildren(QPushButton) if button.text() == '重置当前')
            measure('reset_with_refresh', reset.click, lambda: ready() and not page.is_dirty()
                    and len(changes) == before_change + 1 and page._config_active_role == '1051')
            with UserDataDao(database) as dao:
                restored = dao.get_character_weight_preferences(base_weight_character_id(1051))
                assert restored['source_kind'] == 'default'
                assert restored['property_weights']['CritBase'] == initial_weight
                assert dao.get_character_weight_preferences(base_weight_character_id(1003)) == other
            assert page._config_form_data['1051']['weights']['CritBase'] == initial_weight
            output['correctness'].append({'saved': value, 'reset_default': initial_weight,
                                          'other_role_unchanged': True})
        if args.worker_mode == 'hot':
            before_reads = counters['service_reads']
            measure('burst_refresh_30', lambda: [page.refresh() for _ in range(30)], ready)
            output['burst_service_reads'] = counters['service_reads'] - before_reads
            for _index in range(30):
                old = page.task_owner
                context.generation += 1
                measure('generation_refresh', page.refresh, ready)
                wait_for(lambda: not isValid(old))
                assert len(page.findChildren(BasicWeightController)) == 1
                assert page._config_form_data['1051']['weights']['CritBase'] == initial_weight
        output['role_count'] = len(page._config_form_data)
        output['model_digest'] = hashlib.sha256(json.dumps(page._config_form_data, sort_keys=True, default=str).encode()).hexdigest()
        output['before_close_resources'] = resources()
        owner = page.task_owner
        wait_for(idle)
        page.close_tasks()
        wait_for(idle)
        shell.close()
        shell.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        wait_for(lambda: not isValid(page) and not isValid(owner) and not isValid(shell))
        assert not app.allWidgets()
        output['cleanup'] = {'page_owner_host_destroyed': True, 'widgets_remaining': 0,
                             'owned_threads_remaining': 0, 'after_close_resources': resources()}
        output.update(status='success', notifications=len(changes), counters=counters)
    except BaseException as error:
        output.update(status='failed', error=repr(error), traceback=traceback.format_exc())
        # 不在异常路径销毁活跃 QThread。等物理终态再清理；失败照实保留，不能从统计剔除。
        try:
            if page is not None and isValid(page):
                wait_for(idle, check_failures=False)
                page.close_tasks()
                wait_for(idle, check_failures=False)
                shell.deleteLater()
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                wait_for(lambda: not isValid(page), check_failures=False)
                output['failure_cleanup'] = 'physical tasks settled and page destroyed'
            elif shell is not None and isValid(shell):
                shell.deleteLater()
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                wait_for(lambda: not isValid(shell), check_failures=False)
        except BaseException as cleanup_error:
            output['failure_cleanup'] = repr(cleanup_error)
    finally:
        # 业务错误不阻止物理收尾；仍活跃时保留合成目录，不删后台正在访问的文件。
        if page is None or not isValid(page):
            assert Path(temporary.name).resolve().is_relative_to(fixture)
            try:
                temporary.cleanup()
            except BaseException as cleanup_error:
                output.update(status='failed', cleanup_failed=repr(cleanup_error))
        else:
            temporary._finalizer.detach()
            output['retained_temporary_path'] = temporary.name
        timer.stop()
        for name, value in originals.items():
            setattr(QMessageBox, name, value)
        BasicWeightService.load_form_data, workshop._read_template = original_load, original_template
    output['heartbeat_samples'] = [{'at_monotonic_seconds': at, 'extra_delay_ms': delay} for at, delay in heartbeat]
    output['inputs_hashes_unchanged'] = inputs == fixture_inputs(fixture)
    output['imports'] = import_audit(source)
    if output['imports']['foreign_project_modules'] or not output['inputs_hashes_unchanged']:
        output['status'] = 'failed'
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': output['status'], 'mode': args.worker_mode, 'counters': counters,
                      'error': output.get('error'), 'output': str(args.output)}))
    return 0 if output['status'] == 'success' else 1


def run_child(command, cwd, output, timeout=180):
    """有界等待本次子进程；超时按创建身份收尾，不只杀 venv 启动器。"""
    stdout_path, stderr_path = output.with_suffix('.stdout.txt'), output.with_suffix('.stderr.txt')
    cleanup, identity = None, None
    with stdout_path.open('xb') as stdout, stderr_path.open('xb') as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr)
        if sys.platform == 'win32':
            query = f"Get-CimInstance Win32_Process -Filter 'ProcessId={process.pid}' | Select-Object ProcessId,@{{Name='CreationUtcTicks';Expression={{$_.CreationDate.ToUniversalTime().Ticks}}}} | ConvertTo-Json -Compress"
            try:
                queried = subprocess.run(['powershell', '-NoProfile', '-Command', query], capture_output=True,
                                         text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                if queried.returncode == 0 and queried.stdout.strip():
                    identity = json.loads(queried.stdout)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                # 观测失败不丢失父等待与输出；若随后超时，明确标为子树状态未知。
                identity = None
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = 124
            if identity is not None:
                script = r"""
$ErrorActionPreference='Stop'
$all=@(Get-CimInstance Win32_Process)
$root=@($all | Where-Object {$_.ProcessId -eq __PID__ -and $_.CreationDate.ToUniversalTime().Ticks -eq __TICKS__})
$owned=@();$stopped=@();$remaining=@()
if ($root.Count -eq 1) {
    $owned=@($root[0]);$front=@($root[0].ProcessId)
    while ($front.Count -gt 0) {
        $next=@($all | Where-Object {$_.ParentProcessId -in $front -and $_.ProcessId -notin $owned.ProcessId -and $_.CreationDate -ge $root[0].CreationDate})
        $owned+=$next;$front=@($next | ForEach-Object {$_.ProcessId})
    }
    [array]::Reverse($owned)
    foreach ($row in $owned) {
        $now=Get-CimInstance Win32_Process -Filter "ProcessId=$($row.ProcessId)"
        if ($null -ne $now -and $now.CreationDate -eq $row.CreationDate) {
            Stop-Process -Id $row.ProcessId -Force;$stopped+=$row.ProcessId
        }
    }
    foreach ($row in $owned) {
        $now=Get-CimInstance Win32_Process -Filter "ProcessId=$($row.ProcessId)"
        if ($null -ne $now -and $now.CreationDate -eq $row.CreationDate) {$remaining+=$row.ProcessId}
    }
}
@{root_matched=$root.Count;stopped=$stopped;owned=$owned | Select-Object ProcessId,ParentProcessId;remaining=$remaining} | ConvertTo-Json -Depth 4 -Compress
""".replace('__PID__', str(identity['ProcessId'])).replace('__TICKS__', str(identity['CreationUtcTicks']))
                try:
                    stopped = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                    cleanup = {'exit_status': stopped.returncode, 'stdout': stopped.stdout, 'stderr': stopped.stderr}
                    process.wait(timeout=10)
                except BaseException as error:
                    cleanup = {'cleanup_failed': True, 'error': repr(error), 'prior': cleanup}
            else:
                cleanup = {'cleanup_failed': True, 'reason': 'process identity not observed; tree state unknown'}
            if process.poll() is None:
                process.terminate()  # kernel handle 确切对应本次根，不按可能复用的 PID 扩大停止范围。
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    cleanup = {'cleanup_failed': True, 'prior': cleanup}
    return {'command': command, 'cwd': str(cwd), 'exit_status': code, 'process_identity': identity,
            'cleanup': cleanup, 'stdout': stdout_path.read_text(encoding='utf-8'),
            'stderr': stderr_path.read_text(encoding='utf-8')}


def main():
    parser = argparse.ArgumentParser(description='真实基础权重页合成输入的进程冷/热与资源代理基准。')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--iterations', type=int, default=30)
    parser.add_argument('--cold-iterations', type=int, default=30)
    parser.add_argument('--one-cpu', action='store_true')
    parser.add_argument('--worker-mode', choices=('cold', 'hot'))
    args = parser.parse_args()
    if args.iterations < 1 or args.cold_iterations < 1 or args.output.exists():
        parser.error('样本数须为正，输出须为新路径，保留已有测量')
    args.source_root, args.fixture = args.source_root.resolve(), args.fixture.resolve()
    inputs = fixture_inputs(args.fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.worker_mode:
        return worker(args)
    hashes = {path.relative_to(args.source_root).as_posix(): sha256(path)
              for area in ('src', 'tools', 'tests') for path in sorted((args.source_root / area).rglob('*.py'))}
    output = {'schema': 'calc.weight-responsiveness/2', 'status': 'running',
              'scope': 'process-cold and hot real BasicWeightPage/Controller/DAO; offscreen proxy, no game/full-app/low-end-machine claim',
              'cold_definition': 'fresh process/owner/clone, OS disk cache not cleared',
              'percentile_definition': 'nearest-rank; n=30 P99 near max',
              'read_counter_definition': 'wrappers execute originals; _read_template calls, not proven physical disk reads',
              'memory_definition': 'native current working set and lifetime peak including imports; not per-action peak or leak proof',
              'heartbeat_definition': 'real QEventLoop, interval10ms; operation starts after heartbeat; completion plus next tick',
              'fixture_hashes': inputs, 'source_hashes': hashes,
              'environment': {'python': sys.version, 'platform': sys.platform}, 'workers': []}
    folder = args.output.parent / (args.output.stem + '-workers')
    folder.mkdir(exist_ok=False)
    for mode, count in [('cold', args.cold_iterations), ('hot', 1)]:
        for index in range(count):
            target = folder / f'{mode}-{index + 1}.json'
            command = [sys.executable, str(Path(__file__).resolve()), '--source-root', str(args.source_root),
                       '--fixture', str(args.fixture), '--output', str(target), '--iterations', str(args.iterations),
                       '--worker-mode', mode, *(['--one-cpu'] if args.one_cpu else [])]
            start = time.perf_counter()
            record = run_child(command, args.source_root, target)
            record['elapsed_seconds'] = time.perf_counter() - start
            try:
                record['result'] = json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
            except (OSError, ValueError) as error:
                record.update(result=None, invalid_worker_result=repr(error))
            if record['result'] is None or record['result'].get('status') != 'success':
                record['exit_status'] = record['exit_status'] or 1
            output['workers'].append(record)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
            print(mode, index + 1, record['exit_status'], flush=True)
            if record['exit_status'] != 0:
                output.update(status='failed', failed_sample_retained=True)
                args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
                return 1
    output['summary'] = {}
    for mode in ('cold', 'hot'):
        rows = [row['result'] for row in output['workers'] if row['result']['mode'] == mode]
        actions = {key for row in rows for key in row['samples']}
        output['summary'][mode] = {key: {field: summarize([sample[field] for row in rows
                    for sample in row['samples'].get(key, [])])
                    for field in ('callback_ms', 'ready_ms', 'max_event_loop_delay_ms')}
                    for key in sorted(actions)}
    output['source_hashes_unchanged'] = all(sha256(args.source_root / rel) == digest for rel, digest in hashes.items())
    output['fixture_hashes_unchanged'] = inputs == fixture_inputs(args.fixture)
    output['all_initial_models_equal'] = len({row['result']['initial_model_digest'] for row in output['workers']}) == 1
    output['all_models_equal'] = len({row['result']['model_digest'] for row in output['workers']}) == 1
    output['status'] = 'success' if output['source_hashes_unchanged'] and output['fixture_hashes_unchanged'] and output['all_models_equal'] and output['all_initial_models_equal'] else 'failed'
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': output['status'], 'cold_samples': args.cold_iterations,
                      'hot_samples': args.iterations, 'output': str(args.output)}))
    return 0 if output['status'] == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
