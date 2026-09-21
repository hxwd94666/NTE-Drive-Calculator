# 由正式静态晋升入口调用，成套校验并可回滚地替换角色目录。
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from src.integrations.role_catalog_release import read_role_catalog, validate_role_assets


def promote_role_catalog(finalized: dict, target: Path) -> dict:
    candidate = Path(finalized["database_path"]).parent
    release = read_role_catalog(candidate)
    assets = validate_role_assets(release.asset_root, release.dataset_id, release.sha256)
    if target.exists():
        previous = read_role_catalog(target)
        previous_assets = validate_role_assets(previous.asset_root, previous.dataset_id, previous.sha256)
        allowed = {"game_static.sqlite3", "manifest.json", "game_ui/manifest.json"}
        allowed.update("game_ui/" + path for path in previous_assets["files"])
        if any(path.relative_to(target).as_posix() not in allowed for path in target.rglob("*") if path.is_file()):
            raise ValueError("旧角色目录包含非托管文件，保留原目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".role-catalog-", dir=target.parent) as temporary:
        root = Path(temporary)
        stage, rollback = root / "new", target.parent / ".role_catalog.previous"
        if rollback.exists():
            raise ValueError("角色目录存在待恢复备份，请先处理上次晋升结果")
        stage.mkdir()
        members = ["game_static.sqlite3", "manifest.json", "game_ui/manifest.json"]
        members.extend("game_ui/" + relative for relative in assets["files"])
        for relative in members:
            source, destination = (candidate / relative).resolve(), (stage / relative).resolve()
            if candidate.resolve() not in source.parents or stage.resolve() not in destination.parents:
                raise ValueError("角色目录成员越界")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        read_role_catalog(stage)
        had_previous = target.exists()
        if had_previous:
            os.replace(target, rollback)
        try:
            os.replace(stage, target)
            installed = read_role_catalog(target)
        except BaseException:
            if target.exists():
                os.replace(target, stage)
            if had_previous:
                os.replace(rollback, target)
            raise
        if had_previous:
            # 仅删除已读取并核对所有成员归属的上一版目录；恢复失败时保留它。
            if rollback.resolve().parent != target.parent.resolve():
                raise ValueError("角色目录备份路径越界")
            shutil.rmtree(rollback)
    return {"promoted": True, "catalog_scope": installed.scope, "dataset_id": installed.dataset_id,
            "database_sha256": installed.sha256, "database_path": str(installed.database_path),
            "image_count": len(assets["files"]), "battle_dataset_replaced": False}
