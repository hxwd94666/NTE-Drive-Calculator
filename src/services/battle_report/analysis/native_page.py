# 冻结页面选择与用户输入并调用数据库直读核心，只还原展示结果。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
from pathlib import Path

from src.integrations.battle_analysis.page_wire import decode_page, decode_derived_snapshot
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.services.battle_report.analysis.progress import report_battle_analysis_progress
from src.services.battle_report.analysis.progress import BattlePageOverallProgress
from src.services.battle_report.analysis.page_cache import (
    BattleNativePageCache, file_identity, page_cache_key,
)
from src.observability.stage_timings import timed, timed_call, timed_context


_PROGRESS_MESSAGES = {
    'load': '正在读取战报…',
    'target': '正在识别目标与拟合环境…',
    'analyze': '正在重建逐击与战斗状态…',
    'buff_remove': '正在逐项计算 Buff 移除收益',
    'core_baseline': '正在建立空幕对照基线…',
    'core_candidates': '正在比较空幕主属性候选',
    'fork': '正在计算弧盘收益…',
    'panel': '正在计算角色面板…',
    'details': '正在整理逐击详情…',
    'serialize': '正在传回计算结果…',
}


def _user_input(value):
    if is_dataclass(value):
        return _user_input(asdict(value))
    if isinstance(value, dict):
        return {str(key): _user_input(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_user_input(item) for item in value]
    return value


class BattleNativePageService:
    def __init__(self, *, client, dependencies, semantics_path: Path, context_is_current):
        self._client = client
        self._dependencies = dependencies
        self._semantics_path = Path(semantics_path).resolve()
        self._context_is_current = context_is_current
        self._page_cache = BattleNativePageCache()

    def _database_read_lease(self):
        from src.storage.sqlite.user_data_dao import UserDataDao
        database = Path(self._dependencies.user_database_path)
        return UserDataDao.external_read_lease(database) if database.is_file() else nullcontext()

    def _axis_resource_session(self, checkpoint):
        # 一次固定轴评价的前验、求值、后验共用活pin与worker，事务和每次事实摘要仍独立。
        session = getattr(type(self._client), 'verified_session', None)
        if session is None or self._dependencies.static_database_path is None:
            return nullcontext()
        return session(self._client, extra_paths=(Path(self._dependencies.static_database_path).resolve(),
                       self._semantics_path), checkpoint=checkpoint)

    def load_editor_facts(self, battle_record_id):
        from src.integrations.battle_analysis.editor_facts import load_editor_facts
        with self._database_read_lease():
            return load_editor_facts(self._client, self._dependencies, self._context_is_current, battle_record_id)

    def freeze_axis_evaluation(self, request, *, anchor_mode, selected_character_id,
                               baseline_equipment_by_character, candidates, checkpoint=None):
        from src.services.battle_report.analysis.axis_evaluation import freeze_axis_evaluation
        with self._database_read_lease(), self._axis_resource_session(checkpoint):
            return freeze_axis_evaluation(self._client, self._dependencies, self._semantics_path,
                self._context_is_current, request, anchor_mode=anchor_mode, selected_character_id=selected_character_id,
                baseline_equipment_by_character=baseline_equipment_by_character, candidates=candidates, checkpoint=checkpoint)

    def evaluate_axis(self, frozen, *, checkpoint=None):
        from src.services.battle_report.analysis.axis_evaluation import evaluate_frozen_axis
        with self._database_read_lease(), self._axis_resource_session(checkpoint):
            return evaluate_frozen_axis(self._client, self._dependencies, self._semantics_path,
                                       self._context_is_current, frozen, checkpoint=checkpoint)

    @timed('battle_page.total')
    def load(self, request, *, progress_callback=None, timings=None):
        if self._client is None or not self._client.supports_battle_page:
            raise NativeAnalysisError('战报页面需要支持数据库直读的分析组件，请部署配套版本后重试')
        if (request.detail_level == 'composition'
                and not getattr(self._client, 'supports_topple_composition', False)):
            raise NativeAnalysisError('当前分析组件不支持单独计算倾陷归属，请更新配套分析组件')
        dependencies = self._dependencies
        if dependencies.static_database_path is None:
            raise ValueError('原生战报分析缺少静态数据路径')
        if (request.static_database_path is not None
                and Path(request.static_database_path).resolve()
                != Path(dependencies.static_database_path).resolve()):
            raise ValueError('战报请求静态数据上下文已失效')
        # 独立只读进程必须由本次页面任务保有 WAL 连接寿命，不依赖其他窗口碰巧开着库。
        with timed_context(timings, 'battle_page.database_lease', self._database_read_lease()):
            paths = (Path(dependencies.static_database_path).resolve(), self._semantics_path)
            # 组件、静态和机制文件只在本次完整页面请求中拒写固定；账号代次仍每次检查。
            session = getattr(type(self._client), "verified_session", None)
            with timed_context(timings, 'battle_page.resource_lease', session(self._client, extra_paths=paths) if session else nullcontext()) as resources:
                return self._load_frozen(request, progress_callback=progress_callback, timings=timings, resources=resources)

    def _load_frozen(self, request, *, progress_callback, timings, resources=None):
        dependencies = self._dependencies
        cache_enabled = getattr(self._client, 'supports_battle_page_identity', False) is True
        frozen_files = (Path(dependencies.static_database_path).resolve(), self._semantics_path)
        if cache_enabled:
            frozen_files += (Path(self._client.executable).resolve(),)
        def identities():
            return timed_call(timings, 'battle_page.resource_check',
                              lambda: resources.identities(frozen_files) if resources is not None else tuple(file_identity(path) for path in frozen_files))
        try:
            frozen_identity = identities()
        except FileNotFoundError:
            raise NativeAnalysisError('战报分析发行资源缺失，请重新安装完整配套版本后重试') from None
        overall = BattlePageOverallProgress(request)

        def publish(event):
            if progress_callback is not None:
                progress_callback(overall.project(event))

        def checkpoint():
            if not self._context_is_current(dependencies):
                raise CancelledError
            if identities() != frozen_identity:
                raise CancelledError

        checkpoint()
        report_battle_analysis_progress(
            publish, phase='native_page', message='正在读取战报并计算页面…',
        )

        def native_progress(event):
            checkpoint()
            message = _PROGRESS_MESSAGES[event['phase']]
            if request.detail_level == 'composition':
                message = {
                    'target': '正在读取倾陷目标证据…',
                    'analyze': '正在计算当前时段的倾陷归属…',
                    'details': '正在整理倾陷归属结果…',
                }.get(event['phase'], message)
            report_battle_analysis_progress(
                publish, phase=event['phase'],
                message=message,
                completed=event['completed'], total=event['total'],
            )

        # 显式列出计算输入；比较基线和缓存分析仅供展示，不能随请求字段扩张进入原生计算。
        payload = {
            'account_id': dependencies.account_id, 'generation': dependencies.generation,
            'user_database_path': str(Path(dependencies.user_database_path).resolve()),
            'static_database_path': str(Path(dependencies.static_database_path).resolve()),
            'semantics_path': str(self._semantics_path),
            **{key: _user_input(getattr(request, key)) for key in (
                'battle_record_id', 'start_us', 'end_us', 'detail_scope', 'detail_level',
                'marginal_candidate', 'marginal_benefit_candidate',
                'selected_character_id', 'marginal_drive_units')},
        }
        if request.detail_level != 'marginal':
            # 仅边际收益和面板消费角色选择，其他页面的角色筛选只是展示状态。
            payload['selected_character_id'] = None
        raw = None
        cache_key = None
        overview_key = None
        self._page_cache.select_record(request.battle_record_id)
        if cache_enabled:
            input_digest = timed_call(timings, 'battle_page.identity_before',
                self._client.load_battle_page_identity, payload, checkpoint=checkpoint)
            checkpoint()
            cache_key = page_cache_key(
                payload, input_digest=input_digest, files=frozen_identity,
                engine_version=self._client.engine_version, dataset_version=self._client.dataset_version,
            )
            if request.detail_level in {'overview', 'composition'}:
                def detail_key(level):
                    return page_cache_key(
                        {**payload, 'detail_level': level}, input_digest=input_digest,
                        files=frozen_identity, engine_version=self._client.engine_version,
                        dataset_version=self._client.dataset_version,
                    )
                overview_key = detail_key('overview')
                # 倾陷结果包含同范围概览，可复用为概览；反向和逐击结果均不复用。
                raw = timed_call(timings, 'battle_page.cache_decode', self._page_cache.get, detail_key('composition'))
            if raw is None:
                raw = timed_call(timings, 'battle_page.cache_decode', self._page_cache.get, cache_key)
            checkpoint()
        cache_hit = raw is not None
        if timings is not None:
            timings.note('battle_page.cache', cache_hit_count=int(cache_hit), cache_miss_count=int(not cache_hit))
        if cache_hit:
            report_battle_analysis_progress(publish, phase='cache', message='正在读取已完成结果…')
        else:
            raw = timed_call(timings, 'battle_page.component', self._client.load_battle_page,
                payload, checkpoint=checkpoint,
                progress_callback=native_progress if progress_callback is not None else None,
            )
            checkpoint()
            if cache_enabled:
                after_digest = timed_call(timings, 'battle_page.identity_after',
                    self._client.load_battle_page_identity, payload, checkpoint=checkpoint)
                checkpoint()
                if after_digest != input_digest:
                    raise CancelledError
        checkpoint()
        report_battle_analysis_progress(
            publish, phase='decode', message='正在整理页面结果…',
        )
        result = timed_call(timings, 'battle_page.project', decode_page, raw)
        checkpoint()
        # 解码并核对派生身份后才保存；命中缓存不重复写，缓存交付前仍复核账号和文件。
        snapshot = timed_call(timings, 'battle_page.derived_decode', decode_derived_snapshot, raw.get('derived_snapshot'),
            battle_record_id=request.battle_record_id, dataset_version=self._client.dataset_version)
        if snapshot is not None and not cache_hit:
            timed_call(timings, 'battle_page.derived_save', persist_native_snapshot,
                       snapshot, dependencies=dependencies, checkpoint=checkpoint)
        checkpoint()
        if cache_key is not None and not cache_hit:
            if request.detail_level == 'composition' and overview_key is not None:
                self._page_cache.discard(overview_key)
            timed_call(timings, 'battle_page.cache_encode', self._page_cache.put, cache_key, raw)
        checkpoint()
        report_battle_analysis_progress(publish, phase='complete', message='战报计算完成')
        checkpoint()
        return result


def persist_native_snapshot(snapshot, *, dependencies, checkpoint):
    # 数据库与日志依赖仅在确有派生快照时载入，归包不增加首次页面加载开销。
    from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled
    from src.observability import OperationContext
    from src.observability.operation import log_event, safe_exception
    from src.storage.sqlite.user_data_dao import UserDataDao

    checkpoint()
    try:
        with UserDataDao(dependencies.user_database_path, account_id=dependencies.account_id,
                         account_name=dependencies.account_id) as dao:
            checkpoint()
            old = dao.load_battle_inferred_target_snapshot(snapshot['battle_record_id'])
            nullable_labels = {'environment_kind', 'environment_ref', 'environment_name', 'source_kind', 'confidence'}
            if old is not None and all(
                old.get(key) == (value.strip() or None if key in nullable_labels else value)
                for key, value in snapshot.items()
            ):
                return
            # 计算期间新增的用户确认拥有目标；查询后检查不是额外的事务原子保证。
            if dao.load_battle_target_condition(snapshot['battle_record_id']) is not None:
                return
            checkpoint()
            dao.save_battle_inferred_target_snapshot(**snapshot)
    # 取消必须上抛；普通派生保存故障仅告警，不改原始事实或令页面加载失败。
    except (CancelledError, NativeAnalysisCancelled):
        raise
    except Exception as error:
        log_event('WARNING', 'battle_report.inferred_target_snapshot_save_failed',
                  '保存战报自动目标推断快照失败',
                  OperationContext.create('battle_report', account_id=dependencies.account_id,
                                          context_generation=dependencies.generation),
                  phase='failed', battle_record_id=snapshot['battle_record_id'], error=safe_exception(error))
