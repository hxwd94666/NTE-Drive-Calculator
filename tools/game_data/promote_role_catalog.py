# 由正式静态晋升入口调用，成套校验并可回滚地替换角色目录。
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import tempfile

from src.integrations.role_catalog_release import read_role_catalog, validate_role_assets


def promote_role_skill_assets(candidate: Path, target: Path) -> dict:
    """Asset-only maintenance of an unchanged, already validated catalog dataset."""
    candidate, target = candidate.resolve(), target.resolve()
    if candidate == target:
        raise ValueError('技能图片候选与正式目录相同')
    current, updated = read_role_catalog(target), read_role_catalog(candidate)
    if (current.sha256, current.dataset_id, current.scope) != (updated.sha256, updated.dataset_id, updated.scope):
        raise ValueError('技能图片维护不接受数据库或数据集变更，请走静态整包晋升')
    if (candidate/'manifest.json').read_bytes() != (target/'manifest.json').read_bytes():
        raise ValueError('技能图片维护不修改目录身份清单')
    before = validate_role_assets(current.asset_root, current.dataset_id, current.sha256)
    after = validate_role_assets(updated.asset_root, updated.dataset_id, updated.sha256)
    for key in set(before) | set(after):
        if key not in {'skills', 'files', 'total_files', 'total_bytes'} and before.get(key) != after.get(key):
            raise ValueError('技能图片维护改变了其他资源组')
    before_files = {k: v for k, v in before['files'].items() if not k.startswith('skills/')}
    after_files = {k: v for k, v in after['files'].items() if not k.startswith('skills/')}
    if before_files != after_files:
        raise ValueError('技能图片维护改变了其他图片或来源')
    from tools.game_data.build_role_skill_assets import role_skill_asset_requests
    requests = dict(role_skill_asset_requests(updated.database_path))
    if set(requests) != set(after.get('skills', {})):
        raise ValueError('技能图片没有覆盖当前目录的全部正式技能 ID')
    for identity, asset_path in requests.items():
        relative = after['skills'][identity]
        if not relative.startswith('skills/') or after['files'][relative]['source_asset_path'] != asset_path:
            raise ValueError('技能图片与正式资源路径不一致')
    if after['total_files'] != len(after['files']) or after['total_bytes'] != sum((updated.asset_root/k).stat().st_size for k in after['files']):
        raise ValueError('技能图片清单总量不一致')
    result = promote_role_catalog({'database_path': updated.database_path}, target)
    # Reopen the installed mapping too; no runtime fallback to share-only skills exists.
    installed = json.loads((target/'game_ui/manifest.json').read_text(encoding='utf-8'))
    if installed['skills'] != after['skills']:
        raise ValueError('已安装技能图片映射校验失败')
    return result


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
