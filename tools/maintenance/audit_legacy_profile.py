# 只读核对单个旧 onedir 与安装器已提取程序成员，生成精确旧文件基线。
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.integrations.distribution.release_files import bootstrap_path, checked_path, release_path


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root: Path) -> dict:
    rows = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            relative = path.relative_to(root).as_posix()
            checked_path(root, relative)
            if path.is_dir():
                if directory == root and path.name != "_internal":
                    raise ValueError(f"unexpected legacy root directory: {relative}")
                pending.append(path)
            elif path.is_file():
                release_path(relative)
                key = relative.casefold()
                if key in rows:
                    raise ValueError(f"duplicate Windows identity: {relative}")
                rows[key] = {"path": relative, "size": path.stat().st_size,
                             "sha256": sha(path), "bootstrap": bootstrap_path(relative)}
            else:
                raise ValueError(f"not a regular file: {relative}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("onedir", "extracted", "installer", "iss", "toc", "output", "evidence"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    left, right = inventory(args.onedir), inventory(args.extracted)
    if left != right:
        raise ValueError(f"legacy installer/onedir differ: {sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))}")
    sources = {name: sha(getattr(args, name)) for name in ("installer", "iss", "toc")}
    value = {"schema_version": 1, "product": "nte-drive-calc",
             "profile_id": "2.3.1-post-refactor-candidate", "sources_sha256": sources,
             "files": sorted(left.values(), key=lambda row: row["path"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.evidence.write_text(json.dumps({"matched_files": len(left), "profile_sha256": sha(args.output),
        "sources": {name: str(getattr(args, name).absolute()) for name in ("onedir", "extracted", "installer", "iss", "toc")},
        "source_hashes": sources, "scope": "one exact candidate; installer-generated uninstaller and mutable root files excluded"}, indent=2), encoding="utf-8")
    print(f"MATCH {len(left)} files; profile SHA256 {sha(args.output)}")


if __name__ == "__main__":
    main()
