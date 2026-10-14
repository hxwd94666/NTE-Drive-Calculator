# 校验账号包成员并在账号目录暂存；导出只发布完整的白名单归档。
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

TRANSFER_FORMAT_VERSION = 1
USER_DATABASE_FILENAME = "user_data.sqlite3"
CONFIG_FILENAMES = frozenset({
    "hotkeys.json", "update_config.json", "ui_preferences.json", "guide_seen.json",
    "scan_post_actions.json",
})
IMAGE_FILENAMES = frozenset(f"raw_drive_0001{ext}" for ext in (".png", ".jpg", ".jpeg", ".bmp"))
MAX_MEMBERS = 256
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_DATABASE_BYTES = 2 * 1024**3
MAX_RESOURCE_BYTES = 32 * 1024**2
MAX_JSON_BYTES = 64 * 1024


class AccountImportRecoveryError(RuntimeError):
    """发布补偿受阻时保留整个暂存目录，原件不参与自动清理。"""


def account_directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        status = path.stat()
    except FileNotFoundError:
        return None
    return status.st_dev, status.st_ino


def portable_member(relative: str) -> bool:
    return (
        relative == USER_DATABASE_FILENAME
        or relative in {f"config/{name}" for name in CONFIG_FILENAMES}
        or relative in {f"scanned_images/{name}" for name in IMAGE_FILENAMES}
    )


def _member_name(member: zipfile.ZipInfo) -> str:
    name = member.filename.replace("\\", "/").removesuffix("/")
    parts = name.split("/")
    # 不接受 Windows 路径别名、ADS 或目录穿越；先校验全部成员，再读取任何 payload。
    if (not name or any(not part or part in {".", ".."} or part[-1:] in {".", " "}
                        or any(char in part for char in ':<>"|?*\x00') for part in parts)
            or PurePosixPath(name).is_absolute()):
        raise ValueError("unsafe zip path")
    mode = member.external_attr >> 16
    if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or member.flag_bits & 1:
        raise ValueError("账号包包含链接、特殊文件或加密成员")
    return name


@contextmanager
def stage_account_archive(source: Path, accounts_dir: Path) -> Iterator[tuple[dict, Path]]:
    """未知旧成员仅验证后忽略，不把本机配置、日志或凭据文件安装进账号。"""
    with zipfile.ZipFile(source, "r") as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        seen: set[str] = set()
        total = 0
        if len(archive.infolist()) > MAX_MEMBERS:
            raise ValueError("账号包成员数量超限")
        for member in archive.infolist():
            name = _member_name(member)
            key = name.casefold()
            if key in seen:
                raise ValueError("账号包包含重复或大小写冲突成员")
            seen.add(key)
            total += member.file_size
            limit = (MAX_DATABASE_BYTES if name == f"account/{USER_DATABASE_FILENAME}"
                     else MAX_JSON_BYTES if name.endswith(".json") else MAX_RESOURCE_BYTES)
            if member.file_size > limit or total > MAX_TOTAL_BYTES:
                raise ValueError("账号包解压大小超限")
            if not member.is_dir():
                members[name] = member
        try:
            manifest = json.loads(archive.read(members["manifest.json"]).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("账号导入文件缺少有效的 manifest.json") from error
        if not isinstance(manifest, dict) or manifest.get("format") != "nte-account-export":
            raise ValueError("账号导入文件格式不受支持")
        if type(manifest.get("version")) is not int or manifest["version"] != TRANSFER_FORMAT_VERSION:
            raise ValueError("账号导入文件版本不受支持")
        account = manifest.get("account")
        if not isinstance(account, dict):
            raise ValueError("账号导入文件缺少账号信息")
        if f"account/{USER_DATABASE_FILENAME}" not in members:
            raise ValueError("账号导入文件缺少用户数据")
        accounts_dir.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".account-import-", dir=accounts_dir))
        preserve = False
        try:
            staged = Path(temporary) / "account"
            staged.mkdir()
            for name, member in members.items():
                relative = name.removeprefix("account/")
                if not name.startswith("account/") or not portable_member(relative):
                    continue
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, target.open("wb") as output:
                    shutil.copyfileobj(source_file, output, length=1024 * 1024)
            yield account, staged
        except AccountImportRecoveryError:
            preserve = True
            raise
        finally:
            if not preserve:
                # 成功发布后清理受阻不反转业务结果；遗漏目录也不进入导出白名单。
                shutil.rmtree(temporary, ignore_errors=True)


def export_account_archive(target: Path, account: Mapping, files: Mapping[str, Path]) -> Path:
    """失败只移除本次临时 ZIP，已有导出包保留；数据库已由 DAO 生成一致副本。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({
                "format": "nte-account-export", "version": TRANSFER_FORMAT_VERSION,
                "account": dict(account),
            }, ensure_ascii=False))
            archive.writestr("accounts.json", json.dumps({
                "active_account_id": account["id"], "accounts": [dict(account)],
            }, ensure_ascii=False))
            total = 0
            for relative, source in sorted(files.items()):
                if not portable_member(relative):
                    raise ValueError("账号导出成员不在白名单")
                size = source.stat().st_size
                total += size
                limit = MAX_DATABASE_BYTES if relative == USER_DATABASE_FILENAME else MAX_RESOURCE_BYTES
                if size > limit or total > MAX_TOTAL_BYTES:
                    raise ValueError("账号导出大小超限")
                archive.write(source, f"account/{relative}")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install_account_directory(staged: Path, target: Path, publish) -> None:
    """原件移到同盘恢复点；索引发布失败恢复整目录，不逐文件覆盖活动账号。"""
    backup = staged.parent / "previous-account"
    moved = target.exists()
    installed = False
    if moved:
        target.replace(backup)
    try:
        staged.replace(target)
        installed = True
        publish()
    except BaseException:
        try:
            if installed:
                target.replace(staged)
            if moved:
                backup.replace(target)
        except OSError as recovery_error:
            raise AccountImportRecoveryError(
                f"账号恢复待完成，请保留当前目录和恢复原件：{backup.parent}"
            ) from recovery_error
        raise
