# 显式持有一次执行任务的惰性 OCR 实例，嵌套步骤只借用同一 owner。
from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager
from typing import Any

from .model_resources import OcrModelResourceError


class OcrTaskStateError(RuntimeError):
    """已关闭、已失效或初始化失败的执行识别任务。"""


class OcrTaskSession:
    """仅由同步任务线程使用；不缓存到全局，也不关闭调用方注入的引擎。"""

    def __init__(self, *, should_stop: Callable[[], bool] | None = None,
                 engine: Any | None = None) -> None:
        self._should_stop = should_stop
        self._engine = engine
        self.closed = False
        self._failed = False

    def check_current(self, *, should_stop: Callable[[], bool] | None = None) -> None:
        if self.closed or self._failed:
            raise OcrTaskStateError('执行 OCR 任务已结束或失效')
        try:
            # 同一停止函数只读取一次；子步骤另有门禁时两者都需成立。
            if self._should_stop is not None and self._should_stop():
                raise CancelledError('执行 OCR 任务已取消')
            if should_stop is not None and should_stop is not self._should_stop and should_stop():
                raise CancelledError('执行 OCR 任务已取消')
            resources = getattr(self._engine, 'runtime_resources', None)
            if resources is not None:
                resources.verify_decoder(self._engine.ocr)
        except (CancelledError, OcrModelResourceError):
            self._failed = True
            raise

    def _engine_for_read(self) -> Any:
        self.check_current()
        if self._engine is None:
            try:
                from .ocr_engine import OCREngine
                self._engine = OCREngine(cancel_check=self._should_stop)
                self.check_current()
            except (CancelledError, OcrModelResourceError):
                self._failed = True
                raise
            except Exception as exc:
                # 初始化失败不能被目标未匹配吞掉，也不能在同一任务重新建引擎。
                self._failed = True
                raise OcrTaskStateError('执行 OCR 引擎初始化失败') from exc
        return self._engine

    def _extract(self, image: Any, *, include_boxes: bool) -> list:
        engine = self._engine_for_read()
        try:
            result = engine.extract_lines(image) if include_boxes else engine.extract_text(image)
            # 原生推理不抢占；物理返回后再次取消/解码校验，禁止回退点击。
            self.check_current()
            return result
        except (CancelledError, OcrModelResourceError):
            self._failed = True
            raise
        except Exception:
            self.check_current()  # 借入引擎抛普通错误时仍优先核对本次取消。
            raise

    def extract_text(self, image: Any) -> list[str]:
        return self._extract(image, include_boxes=False)

    def extract_lines(self, image: Any) -> list[dict[str, Any]]:
        return self._extract(image, include_boxes=True)

    def invalidate(self) -> None:
        """捕获阶段的致命错误也使本次 owner 失效，不重试下一输入。"""
        self._failed = True

    def close(self) -> None:
        # 不调用未声明的原生销毁 API；离开同步作用域后释放自有引用。
        # 借入引擎仍归调用方，清理引用不代表 DLL 推理可强制中断。
        self.closed = True
        self._engine = None
        self._should_stop = None


@contextmanager
def use_ocr_session(*, should_stop: Callable[[], bool] | None = None,
                    session: OcrTaskSession | None = None,
                    engine: Any | None = None) -> Iterator[OcrTaskSession]:
    if session is not None:
        session.check_current()
        yield session
        return  # 子步骤不关闭父任务，父层的 finally 独占清理。
    owned = OcrTaskSession(should_stop=should_stop, engine=engine)
    try:
        yield owned
    finally:
        owned.close()
