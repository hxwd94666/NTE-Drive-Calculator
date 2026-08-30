# 读写带版本的压缩战报 JSON 二进制容器。

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any, Mapping


BATTLE_REPORT_BUNDLE_EXTENSION = ".ntebr"
BATTLE_REPORT_BUNDLE_MAGIC = b"NTEBR\x1a\r\n"
BATTLE_REPORT_BUNDLE_VERSION = 1
_COMPRESSION_ZLIB = 1
_HEADER = struct.Struct(">8sBBHQQ32s")
_MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_JSON_BYTES = 1024 * 1024 * 1024


class BattleReportBundleError(ValueError):
    """The selected file is not a supported, intact battle-report package."""


def encode_battle_report_bundle(payload: Mapping[str, Any]) -> bytes:
    try:
        raw = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BattleReportBundleError("战报包内容无法序列化") from error
    if len(raw) > _MAX_JSON_BYTES:
        raise BattleReportBundleError("战报包解压后内容过大")
    compressed = zlib.compress(raw, level=9)
    digest = hashlib.sha256(raw).digest()
    return _HEADER.pack(
        BATTLE_REPORT_BUNDLE_MAGIC,
        BATTLE_REPORT_BUNDLE_VERSION,
        _COMPRESSION_ZLIB,
        0,
        len(raw),
        len(compressed),
        digest,
    ) + compressed


def decode_battle_report_bundle(data: bytes) -> dict[str, Any]:
    if len(data) < _HEADER.size:
        raise BattleReportBundleError("文件不是完整的 NTE 战报包")
    magic, version, compression, flags, raw_size, packed_size, digest = (
        _HEADER.unpack(data[: _HEADER.size])
    )
    if magic != BATTLE_REPORT_BUNDLE_MAGIC:
        raise BattleReportBundleError("文件不是 NTE 战报包")
    if version != BATTLE_REPORT_BUNDLE_VERSION:
        raise BattleReportBundleError(f"不支持的战报包容器版本：{version}")
    if compression != _COMPRESSION_ZLIB or flags != 0:
        raise BattleReportBundleError("战报包使用了当前版本不支持的编码")
    if packed_size > _MAX_COMPRESSED_BYTES or raw_size > _MAX_JSON_BYTES:
        raise BattleReportBundleError("战报包内容过大")
    compressed = data[_HEADER.size :]
    if len(compressed) != packed_size:
        raise BattleReportBundleError("战报包压缩长度校验失败")
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(compressed, _MAX_JSON_BYTES + 1)
        if inflater.unconsumed_tail:
            raise BattleReportBundleError("战报包解压后内容过大")
        raw += inflater.flush()
    except zlib.error as error:
        raise BattleReportBundleError("战报包解压失败") from error
    if not inflater.eof:
        raise BattleReportBundleError("战报包压缩数据不完整")
    if inflater.unused_data:
        raise BattleReportBundleError("战报包包含无效的附加压缩数据")
    if len(raw) != raw_size or len(raw) > _MAX_JSON_BYTES:
        raise BattleReportBundleError("战报包解压长度校验失败")
    if hashlib.sha256(raw).digest() != digest:
        raise BattleReportBundleError("战报包 SHA-256 校验失败")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BattleReportBundleError("战报包内部不是有效的 UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise BattleReportBundleError("战报包顶层必须是对象")
    return payload


def read_battle_report_bundle(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        size = source.stat().st_size
        if size > _MAX_COMPRESSED_BYTES + _HEADER.size:
            raise BattleReportBundleError("战报包文件过大")
        return decode_battle_report_bundle(source.read_bytes())
    except OSError as error:
        raise BattleReportBundleError("无法读取战报包") from error


def write_battle_report_bundle_atomic(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    before_replace,
) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = encode_battle_report_bundle(payload)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        before_replace()
        os.replace(temporary_path, target)
        temporary_path = None
        return len(encoded)
    except OSError as error:
        raise BattleReportBundleError("无法原子写入战报包") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
