# 持久选择平台外部数据根；缺失或冲突时停止，不创建替代账号。
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from uuid import uuid4

from src.integrations.accounts.index_file import write_account_index_bytes
from .lock import DataRootLease, reject_links
from .metadata import Distribution, DistributionError


def write_record(path: Path, value: dict) -> None:
    reject_links(path)
    write_account_index_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _locator(distribution: Distribution, selection_home: Path | None) -> Path:
    if distribution.data_policy != "external" or not distribution.data_root_key:
        raise DistributionError("当前发行方式未声明平台外部数据根。")
    home = selection_home or (Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None)
    if home is None or not home.is_absolute():
        raise DistributionError("外部数据根选择记录的位置未明确，请恢复本机 LocalAppData。")
    return home / "NTE Drive Calc" / "portable-roots" / f"{distribution.data_root_key}.json"


def _read(path: Path) -> dict:
    reject_links(path)
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("record too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("record shape")
        return value
    except (OSError, ValueError) as error:
        raise DistributionError(f"数据根记录缺失或损坏：{path}；请显式恢复数据位置选择。") from error


def select_external_data_root(distribution: Distribution, chosen_path: Path,
                              *, selection_home: Path | None = None) -> Path:
    """仅用户明确命令调用；已有记录冲突时拒绝改绑，避免切到空目录。"""
    locator = _locator(distribution, selection_home)
    root = Path(chosen_path).expanduser()
    if not root.is_absolute():
        raise DistributionError("请明确提供外部数据目录的绝对路径。")
    root = root.absolute()
    reject_links(root)
    # 同一 locator 的首次选择须串行；两个不同候选根的锁不能防止互相改绑。
    with DataRootLease(locator.parent):
        if locator.exists():
            existing = resolve_external_data_root(distribution, selection_home=selection_home)
            if existing != root:
                raise DistributionError(f"已有外部数据根为 {existing}；先恢复原选择，不自动改绑。")
            return existing
        history = locator.with_suffix(".selected.json")
        if history.exists():
            previous = _read(history)
            if previous.get("path") != str(root):
                raise DistributionError("原选择记录丢失；请恢复原外部数据位置，未创建替代账号。")
        with DataRootLease(root):
            marker = root / "maintenance" / "data-root.json"
            if marker.exists():
                identity = _read(marker)
                if (identity.get("data_root_key") != distribution.data_root_key or identity.get("schema_version") != 1
                        or not re.fullmatch(r"[0-9a-f]{32}", str(identity.get("root_id", "")))):
                    raise DistributionError("外部数据目录属于另一份选择记录。")
            else:
                if any(child.name != ".ndc-data.lock" for child in root.iterdir()):
                    raise DistributionError("外部数据目录已有内容；请选择空目录后显式迁入旧数据。")
                identity = {"schema_version": 1, "data_root_key": distribution.data_root_key,
                            "root_id": uuid4().hex}
                write_record(marker, identity)
            record = {**identity, "path": str(root)}
            if history.exists() and _read(history) != record:
                raise DistributionError("原外部数据根身份不匹配，请恢复原数据。")
            write_record(history, record)
            write_record(locator, record)
        return root


def resolve_external_data_root(distribution: Distribution, *, selection_home: Path | None = None) -> Path:
    try:
        record = _read(_locator(distribution, selection_home))
    except DistributionError as error:
        raise DistributionError(f'{error} 首次使用请运行 --portable-data-root "<程序目录外的数据绝对路径>"；已有数据请先恢复原选择记录。') from error
    root = Path(str(record.get("path", "")))
    if not root.is_absolute() or record.get("data_root_key") != distribution.data_root_key:
        raise DistributionError("外部数据根身份冲突，请恢复原选择记录。")
    marker = _read(root / "maintenance" / "data-root.json")
    if (marker.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{32}", str(marker.get("root_id", "")))
            or any(marker.get(key) != record.get(key) for key in ("root_id", "data_root_key", "schema_version"))):
        raise DistributionError("外部数据根记录与目录身份不一致，未初始化替代账号。")
    return root


def establish_portable_identity(root: Path, distribution: Distribution, lease: DataRootLease) -> None:
    """首次准备即留下明确便携身份，清单意外丢失后也不转建安装版账号。"""
    lease.require_root(root)
    if not distribution.portable or distribution.data_policy == "external":
        return
    marker = root / "maintenance" / "data-root.json"
    if marker.exists():
        identity = _read(marker)
        if (identity.get("schema_version") != 1 or identity.get("data_root_key") != ""
                or not re.fullmatch(r"[0-9a-f]{32}", str(identity.get("root_id", "")))):
            raise DistributionError("便携 userdata 维护身份冲突。")
        return
    write_record(marker, {"schema_version": 1, "data_root_key": "", "root_id": uuid4().hex})


def external_selection_is_first(distribution: Distribution) -> bool:
    """保留独立选择痕迹，定位记录遗失不作为首次启动。"""
    locator = _locator(distribution, None)
    reject_links(locator)
    reject_links(locator.with_suffix(".selected.json"))
    if locator.exists():
        resolve_external_data_root(distribution)
        return False
    if locator.with_suffix(".selected.json").exists():
        raise DistributionError("外部数据位置记录遗失；请恢复原记录或用维护命令选择原数据目录。")
    return True
