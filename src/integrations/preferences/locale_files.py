# 读取发行中文目录，并在本机限定目录校验、安装纯文本语言补丁。
from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath

from src.domain.localization.catalog import BASE_LOCALE, CatalogError, TextCatalog, make_catalog, valid_locale
from src.integrations.preferences.json_store import PreferencesError, atomic_bytes, decode_json

MAX_CATALOG_BYTES = 256 * 1024
MAX_LOCAL_CATALOGS = 64


def read_catalog_file(path: Path) -> object:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_CATALOG_BYTES + 1)
    except OSError as exc:
        raise CatalogError("catalog_unavailable") from exc
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("too_large")
    try:
        return decode_json(data)
    except PreferencesError as exc:
        raise CatalogError(exc.code) from exc


class LocaleFiles:
    def __init__(self, bundled_dir: Path, patch_dir: Path):
        self.bundled_dir = Path(bundled_dir).resolve()
        self.patch_dir = Path(patch_dir).absolute()
        self._allowed_root = self.patch_dir.parent.resolve()
        self._base: TextCatalog | None = None

    @property
    def base(self) -> TextCatalog:
        if self._base is None:
            self._base = make_catalog(read_catalog_file(self.bundled_dir / f"{BASE_LOCALE}.json"))
        return self._base

    def _target(self, locale: str) -> Path:
        if not valid_locale(locale) or locale == BASE_LOCALE or PureWindowsPath(f"{locale}.json").is_reserved():
            raise CatalogError("invalid_locale")
        # 本机补丁目录及目标解析后仍须在配置归属下；符号链接不能扩张写入范围。
        root = self.patch_dir.resolve()
        target = (root / f"{locale}.json").resolve()
        if root.parent != self._allowed_root or not target.is_relative_to(root):
            raise CatalogError("invalid_path")
        return target

    def load(self, locale: str) -> TextCatalog:
        if locale == BASE_LOCALE:
            return self.base
        catalog = make_catalog(read_catalog_file(self._target(locale)), base=self.base)
        if catalog.locale != locale:
            raise CatalogError("locale_mismatch")
        return catalog

    def available(self) -> tuple[TextCatalog, ...]:
        catalogs = [self.base]
        if not self.patch_dir.exists():
            return tuple(catalogs)
        if self.patch_dir.resolve().parent != self._allowed_root:
            raise CatalogError("invalid_path")
        files = sorted(self.patch_dir.glob("*.json"))
        if len(files) > MAX_LOCAL_CATALOGS:
            raise CatalogError("too_many_catalogs")
        for path in files:
            try:
                catalogs.append(self.load(path.stem))
            except CatalogError:
                # 坏包不作为可选语言，不部分加载；当前有效目录不受其影响。
                continue
        return tuple(catalogs)

    def install(self, source: str | Path) -> TextCatalog:
        source = Path(source)
        if source.suffix.casefold() != ".json":
            raise CatalogError("invalid_file_type")
        payload = read_catalog_file(source)
        catalog = make_catalog(payload, base=self.base)
        target = self._target(catalog.locale)
        if not target.exists() and len(tuple(self.patch_dir.glob("*.json"))) >= MAX_LOCAL_CATALOGS:
            raise CatalogError("too_many_catalogs")
        # 验证整包后序列化；不复制脚本、目录或语言包声明的任意路径。
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(data) > MAX_CATALOG_BYTES:
            raise CatalogError("too_large")
        atomic_bytes(target, data)
        return catalog
