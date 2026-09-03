# 管理工坊公开权重模板的本地更新。
"""Runtime public Workshop weight-template refresh and resolution."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.app.constants import APP_VERSION, WORKSHOP_WEIGHT_CONFIGS_API
from src.domain.recommended_weights import parse_workshop_recommendations
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.utils.logger import logger


WORKSHOP_WEIGHT_TEMPLATE_ENV = "NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE"
_TEMPLATE_SCHEMA_VERSION = 1
_REQUEST_TIMEOUT_SECONDS = 8


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
    """Resolve one current public template row, falling back to release static data."""

    template = _read_template(
        Path(template_file).expanduser().resolve()
        if template_file is not None
        else configured_workshop_weight_template_file()
    )
    runtime = (
        template.get("characters", {}).get(str(int(character_id)))
        if template is not None
        else None
    )
    return runtime if isinstance(runtime, Mapping) else static_recommendation


def workshop_weight_template_revision(
    template_file: str | Path | None = None,
) -> str | None:
    """Return the immutable runtime-template revision for frozen calculations."""

    template = _read_template(
        Path(template_file).expanduser().resolve()
        if template_file is not None
        else configured_workshop_weight_template_file()
    )
    if template is None:
        return None
    digest = str(template.get("payload_sha256") or "").strip()
    return f"workshop-runtime:{digest}" if digest else None


def start_workshop_weight_template_refresh(
    template_file: str | Path,
    static_database_path: str | Path,
) -> threading.Thread:
    """Start the single non-blocking startup refresh without touching account state."""

    def refresh() -> None:
        logger.info("工坊权重启动更新已开始")
        try:
            with StaticGameDataDao(static_database_path) as static_dao:
                character_rows = tuple(static_dao.list_role_template_characters())
            character_ids = tuple(int(row["character_id"]) for row in character_rows)
            result = WorkshopWeightTemplateService(template_file).refresh(
                known_character_ids=character_ids,
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
                    if int(row["character_id"]) not in available
                ]
                if missing:
                    logger.info(
                        "工坊权重缺少 {}，本次继续使用静态默认权重",
                        "、".join(missing),
                    )
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
    ) -> WorkshopWeightRefreshResult:
        request = urllib.request.Request(
            self._endpoint,
            method="GET",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": f"NTE-Drive-Calc/{APP_VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw_payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"工坊权重接口请求失败，HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"工坊权重接口连接失败：{exc}") from exc
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

        known_ids = tuple(dict.fromkeys(int(character_id) for character_id in known_character_ids))
        parsed = parse_workshop_recommendations(records, known_ids)
        characters = {
            str(character_id): self._runtime_recommendation(recommendation)
            for character_id, recommendation in parsed.items()
            if recommendation.get("source_kind") == "workshop_api"
        }
        canonical = json.dumps(characters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = _read_template(self._template_file)
        updated = existing is None or str(existing.get("payload_sha256") or "") != digest
        if updated:
            self._write_template({
                "schema_version": _TEMPLATE_SCHEMA_VERSION,
                "endpoint": self._endpoint,
                "fetched_at_utc": _now_utc(),
                "payload_sha256": digest,
                "characters": characters,
            })
        return WorkshopWeightRefreshResult(
            updated=updated,
            character_count=len(characters),
            payload_sha256=digest,
            character_ids=tuple(sorted(int(character_id) for character_id in characters)),
        )

    def _write_template(self, payload: Mapping[str, Any]) -> None:
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
