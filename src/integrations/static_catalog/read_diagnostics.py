# 记录静态发行读取的失败阶段和底层错误码，不记录业务正文或改变异常传播。
from pathlib import Path
from traceback import extract_tb

from src.observability import OperationContext, log_event
from src.observability.redaction import safe_exception


def report_static_read_failure(path, stage, error):
    """只在失败时输出一次有界诊断；日志故障不得覆盖原错误或触发业务重试。"""
    try:
        chain, seen = [], set()
        current = error
        while current is not None and id(current) not in seen and len(chain) < 6:
            seen.add(id(current))
            entry = safe_exception(current)
            for name in ('code', 'winerror', 'errno', 'sqlite_errorcode', 'sqlite_errorname'):
                value = getattr(current, name, None)
                if isinstance(value, (str, int)):
                    entry[name] = value
            frames = extract_tb(current.__traceback__)
            if frames:
                frame = frames[-1]
                entry['origin'] = f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
            chain.append(entry)
            current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        log_event('ERROR', 'static_catalog.read_failed', '静态发行读取失败',
                  OperationContext.create('static_catalog'), phase='failed', stage=stage,
                  database_file=Path(path).name, error_chain=chain)
    except Exception:
        # 文件日志不可写或已退场时仍让原始读取失败沿原调用链返回。
        pass
