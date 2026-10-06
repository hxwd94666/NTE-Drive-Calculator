# 在隔离候选中准备无字符裁剪的官方静态字体和像素等价的分享纹理。
"""Prepare release assets; never modify the source tree or fetch fonts at runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageOps


BOLD_SOURCE = (
    "https://raw.githubusercontent.com/iota9star/fonts/"
    "a24a5d6633ac19ec10ea86d8efcf7b3dd4e15c89/misans/MiSans-Bold.ttf"
)
BOLD_SHA256 = "d0c1d327952ed935e86fb78a97a6c182b44f2c2b08777326786b1f8b26d1fe1e"
GRADE_SIZES = (48, 54, 92)
BANNER_SIZE = (1860, 300)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root or not path.is_file():
        raise ValueError(f"资源清单路径无效：{relative}")
    return path


def prepare_candidate(source: Path, candidate: Path, bold: Path) -> dict:
    """Bake original-size render results and preserve source provenance/licensing."""
    source = source.resolve(strict=True)
    candidate = candidate.resolve()
    bold = bold.resolve(strict=True)
    if candidate.exists() or candidate.is_relative_to(source) or source.is_relative_to(candidate):
        raise ValueError("候选须为独立且不存在的目录")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest["fonts"]["panel"] != "fonts/MiSansVF.ttf" or manifest["font_weight"] != 630:
        raise ValueError("来源字体或字重改变，需要重新核对官方静态字体")
    if digest(bold) != BOLD_SHA256:
        raise ValueError("静态字体不属于已核对的完整 MiSans Bold 4.009 文件")
    records = {entry["path"]: entry for entry in manifest["files"]}
    for relative, entry in records.items():
        if digest(checked_path(source, relative)) != entry["sha256"]:
            raise ValueError(f"来源资源与清单哈希不同：{relative}")
    original_bytes = sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
    shutil.copytree(source, candidate)
    changes = []

    def save_image(original: str, relative: str, image: Image.Image, transform: dict) -> None:
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG", optimize=True)
        with Image.open(target) as reopened:
            if reopened.convert("RGBA").tobytes() != image.convert("RGBA").tobytes():
                raise ValueError(f"候选 PNG 的解码像素改变：{relative}")
        old = records[original]
        entry = {
            "path": relative, "sha256": digest(target), "source": old["source"],
            "source_sha256": old["sha256"], "transform": transform,
        }
        if relative in records:
            manifest["files"] = [entry if e["path"] == relative else e for e in manifest["files"]]
        else:
            manifest["files"].append(entry)
        changes.append({"path": relative, "bytes": target.stat().st_size, "sha256": entry["sha256"]})

    for grade, relative in manifest["grades"].items():
        with Image.open(source / relative) as original:
            original = original.convert("RGBA")
            for size in GRADE_SIZES:
                name = relative if size == 92 else f"grades/{size}/{Path(relative).name}"
                rendered = ImageOps.contain(original, (size, size), Image.Resampling.LANCZOS)
                save_image(relative, name, rendered, {"operation": "contain", "size": [size, size],
                                                      "resampling": "LANCZOS", "pillow_version": Image.__version__})
                if size != 92:
                    manifest.setdefault(f"grades_{size}", {})[grade] = name
    title = manifest["level"]["title"]
    with Image.open(source / title) as original:
        rendered = ImageOps.fit(original.convert("RGBA"), BANNER_SIZE, Image.Resampling.LANCZOS,
                                centering=(.5, .3))
        save_image(title, title, rendered, {"operation": "fit", "size": list(BANNER_SIZE),
                                          "centering": [.5, .3], "resampling": "LANCZOS",
                                          "pillow_version": Image.__version__})
    old_font = manifest["fonts"]["panel"]
    new_font = "fonts/MiSans-Bold.ttf"
    shutil.copy2(bold, candidate / new_font)
    (candidate / old_font).unlink()
    manifest["fonts"]["panel"] = new_font
    manifest["font_format"] = "static"
    manifest["files"] = [e for e in manifest["files"] if e["path"] != old_font]
    manifest["files"].append({"path": new_font, "sha256": BOLD_SHA256, "source": BOLD_SOURCE,
                              "copyright": "Xiaomi Inc.", "version": "4.009",
                              "transform": "none; complete unmodified upstream static Bold font",
                              "replaces_source_sha256": records[old_font]["sha256"]})
    (candidate / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    notice = candidate / "NOTICE.md"
    # Attribution lives in the project's unified NOTICE, not a generated duplicate.
    # Keep both upstream and font license documents in the candidate unchanged.
    if notice.is_file():
        if notice.is_symlink() or not notice.resolve().is_relative_to(candidate):
            raise ValueError("候选说明文件路径越界")
        notice.unlink()
    for entry in manifest["files"]:
        if digest(checked_path(candidate, entry["path"])) != entry["sha256"]:
            raise ValueError(f"候选资源校验失败：{entry['path']}")
    candidate_bytes = sum(p.stat().st_size for p in candidate.rglob("*") if p.is_file())
    return {"original_bytes": original_bytes, "candidate_bytes": candidate_bytes,
            "saved_bytes": original_bytes - candidate_bytes, "font_sha256": BOLD_SHA256,
            "baked_images": changes}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成分享素材优化候选，不直接改发行资源")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--bold-font", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = prepare_candidate(args.source, args.candidate, args.bold_font)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
