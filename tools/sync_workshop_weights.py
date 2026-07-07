# 打包前从异环工坊接口同步角色权重。
"""Sync workshop role weights before packaging.

This developer-only tool reads WORKSHOP_API_KEY from .env or the process
environment, then updates config/roles.json. The API key must never be bundled
into the app or committed to the repository.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.settings.workshop_weights import sync_workshop_weights


ENV_KEY_NAMES = ("WORKSHOP_API_KEY", "YIHUAN_WORKSHOP_API_KEY", "NTE_WORKSHOP_API_KEY")


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _read_api_key(env_file: Path) -> str:
    env_values = _load_dotenv(env_file)
    for key in ENV_KEY_NAMES:
        value = os.environ.get(key) or env_values.get(key)
        if value:
            return value.strip()
    return ""


def resolve_api_key(
    env_file: Path,
    *,
    prompt_when_missing: bool = False,
    allow_normal_fallback: bool = False,
) -> tuple[str, str]:
    api_key = _read_api_key(env_file)
    if api_key:
        return api_key, ".env"
    if prompt_when_missing:
        if allow_normal_fallback:
            print("\n未在 .env 或环境变量中找到 WORKSHOP_API_KEY。")
            print("1. 手动输入")
            print("2. 进入普通模式（跳过权重同步）")
            choice = input("请输入 1 或 2，直接回车默认为 2: ").strip()
            if choice != "1":
                return "", "normal"
        api_key = getpass.getpass("请输入 WORKSHOP_API_KEY（输入内容不会显示）: ").strip()
        if api_key:
            return api_key, "manual"
    return "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync workshop weights into config/roles.json.")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="Path to .env file.")
    parser.add_argument("--config-dir", default=str(ROOT / "config"), help="Config directory containing roles.json.")
    parser.add_argument("--optional", action="store_true", help="Skip quietly when API key is absent.")
    parser.add_argument("--prompt-key", action="store_true", help="Prompt for API key when .env is missing.")
    parser.add_argument("--fallback-normal", action="store_true", help="Offer normal mode when API key is missing.")
    args = parser.parse_args()

    api_key, source = resolve_api_key(
        Path(args.env_file),
        prompt_when_missing=args.prompt_key,
        allow_normal_fallback=args.fallback_normal,
    )
    if not api_key:
        if source == "normal":
            print("[SKIP] 已进入普通模式：不更新异环工坊权重。")
            return 0
        message = (
            "WORKSHOP_API_KEY is missing. Add it to .env before release packaging, "
            "choose manual input, or pass --optional for local builds."
        )
        if args.optional:
            print(f"[WARN] {message}")
            return 0
        print(f"[FAIL] {message}")
        return 2

    try:
        summary = sync_workshop_weights(Path(args.config_dir), api_key)
    except Exception as exc:
        print(f"[FAIL] Workshop weight sync failed: {exc}")
        return 1

    print(
        f"[OK] Workshop weights synced via {source}: "
        f"api_roles={summary.get('api_role_count', 0)}, "
        f"updated={summary.get('updated_count', 0)}, "
        f"unchanged={summary.get('unchanged_count', 0)}, "
        f"skipped={summary.get('skipped_count', 0)}"
    )
    skipped = summary.get("skipped_roles") or []
    if skipped:
        print("[WARN] Skipped unknown local roles: " + ", ".join(map(str, skipped)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
