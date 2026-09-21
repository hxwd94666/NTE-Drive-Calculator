# 冻结页面选择与用户输入并调用数据库直读核心，只还原展示结果。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import asdict, is_dataclass
from pathlib import Path

from src.integrations.native_battle_page_wire import decode_page, decode_derived_snapshot
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_analysis_progress import report_battle_analysis_progress
from src.services.battle_page_overall_progress import BattlePageOverallProgress
from src.services.battle_native_page_cache import (
    BattleNativePageCache, file_identity, page_cache_key,
)


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

    def load_editor_facts(self, battle_record_id):
        from src.integrations.native_battle_editor_facts import load_editor_facts
        return load_editor_facts(self._client, self._dependencies, self._context_is_current, battle_record_id)

    def load(self, request, *, progress_callback=None):
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
        cache_enabled = getattr(self._client, 'supports_battle_page_identity', False) is True
        frozen_files = (Path(dependencies.static_database_path).resolve(), self._semantics_path)
        if cache_enabled:
            frozen_files += (Path(self._client.executable).resolve(),)
        def identities():
            return tuple(file_identity(path) for path in frozen_files)
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

        # Deliberately list input fields. Cached analyses are rendering state and
        # must never enter the request, even when the request dataclass grows.
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
            # Native consumes role selection only when building marginal benefits/panels.
            payload['selected_character_id'] = None
        raw = None
        cache_key = None
        overview_key = None
        self._page_cache.select_record(request.battle_record_id)
        if cache_enabled:
            input_digest = self._client.load_battle_page_identity(payload, checkpoint=checkpoint)
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
                # A completed composition includes the same overview plus topple
                # attribution. It can satisfy overview, never the reverse.
                raw = self._page_cache.get(detail_key('composition'))
            if raw is None:
                raw = self._page_cache.get(cache_key)
            checkpoint()
        cache_hit = raw is not None
        if cache_hit:
            report_battle_analysis_progress(publish, phase='cache', message='正在读取已完成结果…')
        else:
            raw = self._client.load_battle_page(
                payload, checkpoint=checkpoint,
                progress_callback=native_progress if progress_callback is not None else None,
            )
            checkpoint()
            if cache_enabled:
                after_digest = self._client.load_battle_page_identity(payload, checkpoint=checkpoint)
                checkpoint()
                if after_digest != input_digest:
                    raise CancelledError
        checkpoint()
        report_battle_analysis_progress(
            publish, phase='decode', message='正在整理页面结果…',
        )
        result = decode_page(raw)
        checkpoint()
        snapshot = decode_derived_snapshot(raw.get('derived_snapshot'),
            battle_record_id=request.battle_record_id, dataset_version=self._client.dataset_version)
        if snapshot is not None and not cache_hit:
            from src.services.battle_native_snapshot_persistence import persist_native_snapshot
            persist_native_snapshot(snapshot, dependencies=dependencies, checkpoint=checkpoint)
        checkpoint()
        if cache_key is not None and not cache_hit:
            if request.detail_level == 'composition' and overview_key is not None:
                self._page_cache.discard(overview_key)
            self._page_cache.put(cache_key, raw)
        checkpoint()
        report_battle_analysis_progress(publish, phase='complete', message='战报计算完成')
        checkpoint()
        return result
