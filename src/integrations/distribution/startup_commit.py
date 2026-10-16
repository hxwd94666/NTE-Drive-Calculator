# 将发行接受与数据启动确认纳入可恢复提交；失败不留下新的可信接受记录。
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.integrations.accounts.index_file import write_account_index_bytes
from .data_lifecycle import finish_data_upgrade
from .data_roots import write_record
from .lock import DataRootLease, reject_links
from .metadata import DistributionError
from .retired_files import ACCEPTED_MANIFEST, accept_release


def recover_interrupted_acceptance(root: Path, lease: DataRootLease) -> None:
    """仅恢复小型接受记录，不逆向迁移用户库；待恢复的数据仍受原 pending 门禁保护。"""
    lease.require_root(root)
    maintenance = root / "maintenance"
    marker = maintenance / "pending-acceptance.json"
    reject_links(marker)
    if not marker.exists():
        return
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        expected = record["previous_sha256"]
        if record["schema_version"] != 1:
            raise ValueError("version")
        target = maintenance / ACCEPTED_MANIFEST
        reject_links(target)
        if expected is None:
            target.unlink(missing_ok=True)
        else:
            previous = maintenance / "previous-accepted-release.json"
            reject_links(previous)
            raw = previous.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("digest")
            write_account_index_bytes(target, raw)
        if record.get("pending_upgrade") is not None:
            write_record(maintenance / "pending-upgrade.json", record["pending_upgrade"])
        marker.unlink()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DistributionError("上次启动接受记录未提交完整，请恢复维护记录后再启动。") from error


def commit_runtime_acceptance(root: Path, verified, snapshot: Path | None, lease: DataRootLease) -> None:
    lease.require_root(root)
    if verified is None and snapshot is None:
        return
    maintenance = root / "maintenance"
    target = maintenance / ACCEPTED_MANIFEST
    reject_links(target)
    raw = target.read_bytes() if target.exists() else None
    if raw is not None:
        write_account_index_bytes(maintenance / "previous-accepted-release.json", raw)
    pending = maintenance / "pending-upgrade.json"
    reject_links(pending)
    pending_upgrade = json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else None
    write_record(maintenance / "pending-acceptance.json", {
        "schema_version": 1, "previous_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "pending_upgrade": pending_upgrade,
    })
    try:
        accept_release(maintenance, verified)
        finish_data_upgrade(root, snapshot, lease)
        (maintenance / "pending-acceptance.json").unlink()
    except BaseException:
        # 原记录恢复失败时 pending 保留，后续启动也会在清理/迁移之前阻断或恢复。
        recover_interrupted_acceptance(root, lease)
        raise
