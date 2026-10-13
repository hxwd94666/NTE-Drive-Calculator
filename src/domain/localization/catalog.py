# 校验纯文本语言目录及命名参数，不读取文件或依赖 Qt。
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from string import Formatter
from types import MappingProxyType

BASE_LOCALE = "zh-CN"
CATALOG_ID = "nte-ui"
FORMAT_VERSION = 1
_LOCALE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class CatalogError(ValueError):
    """错误码只描述文本契约，不携带用户原文或文件内容。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def valid_locale(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 40 or not _LOCALE.fullmatch(value):
        return False
    parts = value.split("-")
    # 一个身份只允许一种拼写，避免 Windows 将 en-US/en-us 写入同一文件。
    # 普通脚本四字母首字母大写、两字母地区大写；伪语言 qps 的测试标签全小写。
    canonical = [parts[0]]
    for part in parts[1:]:
        if parts[0] == "qps":
            canonical.append(part.lower())
        elif len(part) == 4 and part.isalpha():
            canonical.append(part.title())
        elif len(part) == 2 and part.isalpha():
            canonical.append(part.upper())
        else:
            canonical.append(part.lower())
    return value == "-".join(canonical)


def parameter_names(text: str) -> frozenset[str]:
    names = set()
    try:
        for _literal, field, spec, conversion in Formatter().parse(text):
            if field is None:
                continue
            # 仅允许 {name}，不访问对象属性、下标或执行格式转换。
            if not _NAME.fullmatch(field) or spec or conversion:
                raise CatalogError("invalid_placeholder")
            names.add(field)
    except ValueError as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError("invalid_placeholder") from exc
    return frozenset(names)


@dataclass(frozen=True)
class TextCatalog:
    locale: str
    display_name: str
    messages: Mapping[str, str]
    parameters: Mapping[str, frozenset[str]]

    def render(self, key: str, **values: str | int | float | bool) -> str:
        if key not in self.messages:
            raise CatalogError("unknown_key")
        if set(values) != self.parameters[key]:
            raise CatalogError("parameter_mismatch")
        if any(type(v) not in (str, int, float, bool) or
               (type(v) is float and not math.isfinite(v)) for v in values.values()):
            raise CatalogError("invalid_parameter")
        return self.messages[key].format_map(values)


def make_catalog(payload: object, *, base: TextCatalog | None = None) -> TextCatalog:
    """完整校验才生成不可变目录；补丁缺项整体使用唯一中文基础目录。"""
    fields = {"format_version", "catalog_id", "locale", "display_name", "messages"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise CatalogError("invalid_schema")
    if type(payload["format_version"]) is not int or payload["format_version"] != FORMAT_VERSION:
        raise CatalogError("unsupported_format")
    if payload["catalog_id"] != CATALOG_ID:
        raise CatalogError("incompatible_catalog")
    locale = payload["locale"]
    name = payload["display_name"]
    if not valid_locale(locale) or not isinstance(name, str) or not name.strip() or len(name) > 80 or "\n" in name or "\r" in name:
        raise CatalogError("invalid_locale")
    source = payload["messages"]
    if not isinstance(source, dict) or len(source) > 2000:
        raise CatalogError("invalid_messages")
    messages = dict(base.messages) if base else {}
    parameters = dict(base.parameters) if base else {}
    for key, text in source.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(text, str) or len(text) > 4096:
            raise CatalogError("invalid_message")
        names = parameter_names(text)
        if base is not None:
            if key not in base.messages:
                raise CatalogError("unknown_key")
            if names != base.parameters[key]:
                raise CatalogError("parameter_mismatch")
        messages[key], parameters[key] = text, names
    if base is None and (locale != BASE_LOCALE or not messages):
        raise CatalogError("invalid_base")
    if base is not None and locale == BASE_LOCALE:
        raise CatalogError("reserved_locale")
    return TextCatalog(locale, name, MappingProxyType(messages), MappingProxyType(parameters))
