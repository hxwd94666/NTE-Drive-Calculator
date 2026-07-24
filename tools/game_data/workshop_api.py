# 读取异环工坊角色权重开放接口的开发期客户端。
"""Developer-only client for the workshop character-weight API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from src.app.constants import APP_VERSION, WORKSHOP_WEIGHT_CONFIGS_API


WORKSHOP_API_TIMEOUT_SECONDS = 8


def fetch_workshop_weight_configs(
    api_key: str,
    *,
    api_url: str = WORKSHOP_WEIGHT_CONFIGS_API,
    timeout: int = WORKSHOP_API_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Fetch the raw workshop records without touching user configuration."""

    normalized_key = str(api_key or "").strip()
    if not normalized_key:
        raise ValueError("请先填写异环工坊 Open API Key。")
    request = urllib.request.Request(
        api_url,
        method="GET",
        headers={
            "X-API-Key": normalized_key,
            "Content-Type": "application/json",
            "User-Agent": f"NTE-Drive-Calc/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("异环工坊 API Key 无效或已过期。") from exc
        raise RuntimeError(f"异环工坊接口请求失败，HTTP {exc.code}。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"异环工坊接口请求异常: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("异环工坊接口返回内容不是有效 JSON。") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("异环工坊接口返回格式异常。")
    if int(payload.get("code", 0) or 0) != 200:
        message = str(payload.get("msg") or "未知错误")
        raise RuntimeError(f"异环工坊接口返回失败: {message}")
    records = payload.get("data")
    if not isinstance(records, list):
        raise RuntimeError("异环工坊接口 data 不是角色数组。")
    return [record for record in records if isinstance(record, dict)]
