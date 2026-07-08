# 检查软件更新并显示版本更新弹窗。
"""GitHub release update config, version checks, and update dialog helpers."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QTextEdit, QVBoxLayout

from src.app.theme import themed_style
from src.utils.logger import logger

UPDATE_FAILURE_NETDISK_MESSAGE = "GitHub请求失败，可前往网盘链接查看版本更新情况"
UPDATE_FALLBACK_MESSAGE = "GitHub API 请求失败，已通过 Release 页面获取版本号。"
UPDATE_CHECK_TIMEOUT_SECONDS = 2


class ReleaseNotesHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        lines = [line.strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


def load_update_config(user_config_dir: Path) -> dict:
    path = user_config_dir / "update_config.json"
    default = {"never_remind": False, "ignored_version": ""}
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                default.update(
                    {
                        "never_remind": bool(data.get("never_remind", False)),
                        "ignored_version": str(data.get("ignored_version", "") or ""),
                    }
                )
    except Exception as exc:
        logger.warning(f"读取更新提醒配置失败，使用默认提醒设置: {path} | {exc}")
    return default


def save_update_config(user_config_dir: Path, update_config: dict) -> None:
    path = user_config_dir / "update_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(update_config, f, ensure_ascii=False, indent=2)


def is_newer_version(remote, current) -> bool:
    def nums(v):
        parts = [int(x) for x in re.findall(r"\d+", str(v))]
        return (parts + [0, 0, 0])[:3]

    return nums(remote) > nums(current)


def fetch_update_info(
    latest_release_api: str,
    releases_url: str,
    app_version: str,
    timeout: int = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict:
    feed_result = fetch_update_info_from_atom_feed(releases_url, app_version, timeout=timeout)
    if feed_result.get("has_release"):
        return feed_result

    page_result = fetch_update_info_from_latest_page(releases_url, app_version, timeout=timeout)
    if page_result.get("has_release"):
        return page_result

    request = urllib.request.Request(
        latest_release_api,
        headers={"User-Agent": f"NTE-Drive-Calc/{app_version}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"has_release": False, "newer": False, "url": releases_url, "message": "未找到 GitHub Release。"}
        if exc.code == 403:
            fallback = fetch_update_info_from_latest_page(releases_url, app_version, timeout=timeout)
            if fallback.get("has_release"):
                fallback["api_error"] = str(exc)
                return fallback
            fallback["error"] = str(exc)
            return fallback
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "has_release": False,
            "newer": False,
            "url": releases_url,
            "message": UPDATE_FAILURE_NETDISK_MESSAGE,
            "error": str(exc),
        }

    latest = str(data.get("tag_name") or data.get("name") or "").strip()
    url = data.get("html_url") or releases_url
    assets = data.get("assets") or []
    setup_asset = next((item for item in assets if str(item.get("name", "")).lower().endswith(".exe")), None)
    if setup_asset and setup_asset.get("browser_download_url"):
        url = setup_asset["browser_download_url"]
    return {
        "has_release": True,
        "latest": latest,
        "newer": is_newer_version(latest, app_version),
        "url": url,
        "release_url": data.get("html_url") or releases_url,
        "message": data.get("body") or "",
        "name": data.get("name") or latest,
    }


def fetch_update_info_from_atom_feed(
    releases_url: str,
    app_version: str,
    timeout: int = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict:
    feed_url = releases_url.rstrip("/") + ".atom"
    request = urllib.request.Request(feed_url, headers={"User-Agent": f"NTE-Drive-Calc/{app_version}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            feed_text = response.read().decode("utf-8")
        root = ET.fromstring(feed_text)
    except (ET.ParseError, UnicodeDecodeError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "has_release": False,
            "newer": False,
            "url": releases_url,
            "message": UPDATE_FAILURE_NETDISK_MESSAGE,
            "error": str(exc),
        }

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return {
            "has_release": False,
            "newer": False,
            "url": releases_url,
            "message": UPDATE_FAILURE_NETDISK_MESSAGE,
            "error": "GitHub Release feed 中没有发布条目。",
        }

    release_url = ""
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("rel") == "alternate":
            release_url = link.attrib.get("href", "")
            break
    latest = _release_tag_from_url(release_url)
    if not latest:
        return {
            "has_release": False,
            "newer": False,
            "url": releases_url,
            "message": UPDATE_FAILURE_NETDISK_MESSAGE,
            "error": f"无法从 Release feed 解析版本号: {release_url}",
        }

    title = entry.findtext("atom:title", default=latest, namespaces=ns)
    notes_html = entry.findtext("atom:content", default="", namespaces=ns)
    notes = _html_to_plain_text(notes_html) or "此版本没有填写更新说明。"
    return {
        "has_release": True,
        "latest": latest,
        "newer": is_newer_version(latest, app_version),
        "url": release_url,
        "release_url": release_url,
        "message": notes,
        "name": title or latest,
    }


def fetch_update_info_from_latest_page(
    releases_url: str,
    app_version: str,
    timeout: int = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict:
    latest_url = releases_url.rstrip("/") + "/latest"
    request = urllib.request.Request(latest_url, headers={"User-Agent": f"NTE-Drive-Calc/{app_version}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "has_release": False,
            "newer": False,
            "url": releases_url,
            "message": UPDATE_FAILURE_NETDISK_MESSAGE,
            "error": str(exc),
        }

    latest = _release_tag_from_url(final_url)
    if not latest:
        return {
            "has_release": False,
            "newer": False,
            "url": releases_url,
            "message": UPDATE_FAILURE_NETDISK_MESSAGE,
            "error": f"无法从 Release 页面解析版本号: {final_url}",
        }
    return {
        "has_release": True,
        "latest": latest,
        "newer": is_newer_version(latest, app_version),
        "url": final_url,
        "release_url": final_url,
        "message": UPDATE_FALLBACK_MESSAGE,
        "name": latest,
    }


def _release_tag_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    marker = "/releases/tag/"
    if marker not in path:
        return ""
    return unquote(path.rsplit(marker, 1)[-1]).strip()


def _html_to_plain_text(html_text: str) -> str:
    parser = ReleaseNotesHTMLParser()
    parser.feed(html_text or "")
    return parser.text()


def update_dialog_link_url(info: dict) -> str:
    return str(info.get("url") or info.get("release_url") or "")


def should_show_startup_update(update_config: dict, info: dict) -> bool:
    latest = str(info.get("latest") or "")
    if update_config.get("never_remind"):
        return False
    if latest and update_config.get("ignored_version") == latest:
        return False
    return True


def show_update_dialog(parent, style_sheet: str, info: dict, app_version: str) -> dict:
    latest = str(info.get("latest") or "未知")
    dialog = QDialog(parent)
    dialog.setWindowTitle("发现更新")
    dialog.setMinimumSize(560, 420)
    dialog.setStyleSheet(style_sheet)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QLabel(f"发现新版本 {latest}")
    title.setStyleSheet("font-size:18px;font-weight:700;color:#58a6ff")
    layout.addWidget(title)

    subtitle = QLabel(f"当前版本: {app_version}")
    subtitle.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(subtitle)

    notes = QTextEdit()
    notes.setReadOnly(True)
    notes.setMinimumHeight(220)
    release_text = (info.get("message") or "").strip() or "此版本没有填写更新说明。"
    notes.setPlainText(release_text)
    layout.addWidget(notes, 1)

    url = update_dialog_link_url(info)
    link = QLabel(f"下载页面: {url}")
    link.setText(f'下载页面: <a href="{url}">{url}</a>' if url else "下载页面: 未提供")
    link.setTextFormat(Qt.RichText)
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextBrowserInteraction)
    link.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    layout.addWidget(link)

    never_cb = QCheckBox("永不提醒")
    ignore_cb = QCheckBox("当前版本不再提醒")
    layout.addWidget(never_cb)
    layout.addWidget(ignore_cb)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()

    result = {"changed": False}
    if never_cb.isChecked():
        result["never_remind"] = True
        result["changed"] = True
    if ignore_cb.isChecked():
        result["ignored_version"] = latest
        result["changed"] = True
    return result
