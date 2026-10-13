# 管理工坊公开权重模板的本地更新。
"""Runtime public Workshop weight-template refresh and resolution."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from copy import deepcopy
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.app.constants import APP_VERSION, WORKSHOP_WEIGHT_CONFIGS_API
from src.domain.recommended_weights import parse_workshop_recommendations, workshop_weight_source_ids
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.utils.logger import logger


WORKSHOP_WEIGHT_TEMPLATE_ENV = "NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE"
_TEMPLATE_SCHEMA_VERSION = 1
_REQUEST_TIMEOUT_SECONDS = 8


class WorkshopRefreshToken(threading.Event):
    """取消与发布接纳线性化；只锁状态，不在 GUI 的锁持有期间执行磁盘 IO。"""

    def __init__(self) -> None:
        super().__init__()
        self._publish_gate = threading.Lock()
        self._publish_accepted = False

    def set(self) -> None:
        with self._publish_gate:
            super().set()

    def begin_publish(self) -> bool:
        with self._publish_gate:
            if self.is_set():
                return False
            self._publish_accepted = True
            return True

    @property
    def publish_accepted(self) -> bool:
        with self._publish_gate:
            return self._publish_accepted


@dataclass(frozen=True, slots=True)
class WorkshopWeightRefreshResult:
    updated: bool
    character_count: int
    payload_sha256: str
    character_ids: tuple[int, ...] | None = None


def configured_workshop_weight_template_file() -> Path | None:
    """Return the composition-root configured runtime template path, if any."""

    raw_path = os.environ.get(WORKSHOP_WEIGHT_TEMPLATE_ENV, "").strip()
    return Path(raw_path).expanduser().resolve() if raw_path else None


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_template(template_file: Path | None) -> dict[str, Any] | None:
    if template_file is None or not template_file.is_file():
        return None
    try:
        payload = json.loads(template_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or int(payload.get("schema_version") or 0) != _TEMPLATE_SCHEMA_VERSION
        or not isinstance(payload.get("characters"), dict)
    ):
        return None
    return payload


def effective_workshop_recommended_weights(
    template_file: str | Path | None,
    character_id: int,
    static_recommendation: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """单次解析公开模板；批量消费者直接复用请求级快照，不建立全局缓存。"""
    return load_workshop_weight_snapshot(template_file).resolve(character_id, static_recommendation)


def workshop_weight_template_revision(
    template_file: str | Path | None = None,
) -> str | None:
    """读取公开模板的版本；批量请求从同一快照取得数值和版本。"""
    return load_workshop_weight_snapshot(template_file).revision


@dataclass(frozen=True, slots=True)
class WorkshopWeightSnapshot:
    """一次请求私有持有解析结果；对外投影深拷贝，下一次请求重新读取。"""

    _template: dict[str, Any] | None

    @property
    def revision(self) -> str | None:
        digest = str((self._template or {}).get("payload_sha256") or "").strip()
        return f"workshop-runtime:{digest}" if digest else None

    def resolve(
        self, character_id: int, static_recommendation: Mapping[str, Any] | None,
    ) -> Mapping[str, Any] | None:
        if self._template is not None:
            for source_id in workshop_weight_source_ids(character_id):
                runtime = self._template["characters"].get(str(source_id))
                if isinstance(runtime, Mapping):
                    # 来源 ID 与消费角色 ID 分开；编辑返回值不改变本批其余角色。
                    return {**deepcopy(runtime), "character_id": int(character_id),
                            "weight_source_character_id": source_id}
        return static_recommendation


def load_workshop_weight_snapshot(template_file: str | Path | None = None) -> WorkshopWeightSnapshot:
    """按本次配置只读一次文件，冻结模板内容与 revision 的一致身份。"""
    return WorkshopWeightSnapshot(_read_template(
        Path(template_file).expanduser().resolve()
        if template_file is not None
        else configured_workshop_weight_template_file()
    ))


def start_workshop_weight_template_refresh(
    template_file: str | Path,
    static_database_path: str | Path,
    *,
    cancel_token: WorkshopRefreshToken | None = None,
) -> threading.Thread:
    """Start the single non-blocking startup refresh without touching account state."""

    def refresh() -> None:
        logger.info("工坊权重启动更新已开始")
        try:
            _check_refresh_cancelled(cancel_token)
            with StaticGameDataDao(static_database_path) as static_dao:
                character_rows = tuple(static_dao.list_role_template_characters())
            character_ids = tuple(int(row["character_id"]) for row in character_rows)
            result = WorkshopWeightTemplateService(template_file).refresh(
                known_character_ids=character_ids, cancel_token=cancel_token,
            )
            outcome = "已更新" if result.updated else "已是最新"
            logger.info(
                "工坊权重启动更新完成：公开角色数={}，{}",
                result.character_count,
                outcome,
            )
            if result.character_ids is not None:
                available = set(result.character_ids)
                missing = [
                    str(row.get("name_zh") or row["character_id"])
                    for row in character_rows
                    if not available.intersection(workshop_weight_source_ids(int(row["character_id"])))
                ]
                if missing:
                    logger.info(
                        "工坊权重缺少 {}，本次继续使用静态默认权重",
                        "、".join(missing),
                    )
        except CancelledError:
            return
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            # A failed startup refresh deliberately preserves the last valid
            # template (or the packaged fallback); there is no user action.
            logger.warning(
                "工坊权重启动更新失败（{}），继续使用本地模板或发行基线",
                type(exc).__name__,
            )
            return

    worker = threading.Thread(
        target=refresh,
        name="workshop-weight-template-refresh",
        daemon=True,
    )
    worker.start()
    return worker


def _check_refresh_cancelled(cancel_token: WorkshopRefreshToken | None) -> None:
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("工坊启动更新已取消")


class WorkshopWeightTemplateService:
    """Fetch the public endpoint once and atomically replace the local template."""

    def __init__(
        self,
        template_file: str | Path,
        *,
        endpoint: str = WORKSHOP_WEIGHT_CONFIGS_API,
        timeout_seconds: int = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._template_file = Path(template_file).expanduser().resolve()
        self._endpoint = str(endpoint)
        self._timeout_seconds = int(timeout_seconds)

    def refresh(
        self,
        *,
        known_character_ids: Iterable[int],
        cancel_token: WorkshopRefreshToken | None = None,
    ) -> WorkshopWeightRefreshResult:
        _check_refresh_cancelled(cancel_token)
        # QtNetwork 只在后台真正请求时加载；总截止覆盖 DNS、响应头和持续分块响应。
        from src.integrations.workshop_weight_http import fetch_workshop_payload
        raw_payload = fetch_workshop_payload(
            self._endpoint, timeout_seconds=self._timeout_seconds,
            cancel_check=cancel_token.is_set if cancel_token is not None else lambda: False,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "User-Agent": f"NTE-Drive-Calc/{APP_VERSION}"},
        ).decode("utf-8")
        _check_refresh_cancelled(cancel_token)
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("工坊权重接口返回内容不是有效 JSON") from exc
        if not isinstance(payload, Mapping) or int(payload.get("code") or 0) != 200:
            message = str(payload.get("msg") or "未知错误") if isinstance(payload, Mapping) else "格式异常"
            raise RuntimeError(f"工坊权重接口返回失败：{message}")
        records = payload.get("data")
        if not isinstance(records, list):
            raise RuntimeError("工坊权重接口 data 不是角色数组")

        known_ids = tuple(dict.fromkeys(
            source_id for character_id in known_character_ids
            for source_id in workshop_weight_source_ids(character_id)
        ))
        parsed = parse_workshop_recommendations(records, known_ids)
        characters = {
            str(character_id): self._runtime_recommendation(recommendation)
            for character_id, recommendation in parsed.items()
            if recommendation.get("source_kind") == "workshop_api"
        }
        existing = _read_template(self._template_file)
        # 接口暂缺主角时保留1046上次有效记录，绝不借用旧1051模板。
        previous_main = (existing or {}).get("characters", {}).get("1046")
        if 1046 in known_ids and "1046" not in characters and isinstance(previous_main, Mapping):
            if previous_main.get("properties") and previous_main.get("source_kind") == "workshop_runtime":
                characters["1046"] = deepcopy(dict(previous_main))
        canonical = json.dumps(characters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        updated = existing is None or str(existing.get("payload_sha256") or "") != digest
        if updated:
            self._write_template({
                "schema_version": _TEMPLATE_SCHEMA_VERSION,
                "endpoint": self._endpoint,
                "fetched_at_utc": _now_utc(),
                "payload_sha256": digest,
                "characters": characters,
            }, cancel_token=cancel_token)
        return WorkshopWeightRefreshResult(
            updated=updated,
            character_count=len(characters),
            payload_sha256=digest,
            character_ids=tuple(sorted(int(character_id) for character_id in characters)),
        )

    def _write_template(
        self, payload: Mapping[str, Any], *, cancel_token: WorkshopRefreshToken | None = None,
    ) -> None:
        _check_refresh_cancelled(cancel_token)
        self._template_file.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self._template_file.name}.",
            suffix=".tmp",
            dir=self._template_file.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            # 接纳与取消共用短锁：取消先赢不发布；已接受发布完成原子替换后再物理退出。
            # 不在检查与 replace 之间宣称可以撤回一个已接受的文件提交。
            if cancel_token is not None and not cancel_token.begin_publish():
                raise CancelledError("工坊启动更新在发布接纳前取消")
            os.replace(temporary, self._template_file)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _runtime_recommendation(
        recommendation: Mapping[str, Any],
    ) -> dict[str, Any]:
        properties = list(recommendation.get("properties") or ())
        return {
            **recommendation,
            "source_kind": "workshop_runtime",
            "properties": properties,
            "property_weights": {
                str(row["property_id"]): float(row["weight"])
                for row in properties
                if float(row.get("weight") or 0.0) > 0
            },
            "main_property_weights": {
                str(row["property_id"]): float(row["main_weight"])
                for row in properties
                if float(row.get("main_weight") or 0.0) > 0
            },
        }
