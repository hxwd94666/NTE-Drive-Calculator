# 仅将已核对的干净 onedir 制作为便携 ZIP，不隐式编译、安装驱动或上传平台。
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.version import __version__
from tools import build_cli
from tools.release.managed_paths import plain_directory_root, plain_member_stat
from tools.release.portable_archive import build_portable_archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, help="与 build_exe.py 同一隔离输出根。")
    parser.add_argument("--app-dir", type=Path, help="干净 onedir；默认本次输出根/dist/NTE_Drive_Calc。")
    parser.add_argument("--input-receipt", type=Path, help="build_exe 产生的白名单；默认本次build/NTE_Drive_Calc/onedir-files.json。")
    parser.add_argument("--profile", choices=("manual", "kokodayo"), default="manual")
    parser.add_argument("--update-channel", choices=("manual", "platform"), default="manual",
                        help="平台候选须显式选择 platform；不从平台进程或路径猜测。")
    parser.add_argument("--data-policy", choices=("userdata", "external"),
                        help="平台候选必须明确选择；KoKodayo 只接受 external，程序目录会被重建。")
    parser.add_argument("--data-root-key", default="", help="external策略的跨版本稳定定位键，不接受本机绝对路径。")
    parser.add_argument("--previous-platform-version", help="平台更新时显式提供已发布版本，要求严格递增。")
    args = parser.parse_args(argv)
    try:
        output_root = build_cli.select_output_root(ROOT, args.output_root)
        app_dir = args.app_dir or output_root / "dist/NTE_Drive_Calc"
        receipt = args.input_receipt or output_root / "build/NTE_Drive_Calc/onedir-files.json"
        destination = output_root / "dist/portable"
        try:
            plain_member_stat(ROOT, destination)
        except FileNotFoundError:
            destination.mkdir(parents=True, exist_ok=True)
        plain_directory_root(destination)
        output = destination / f"NTE_Drive_Calc_Portable_{__version__}_{uuid4().hex[:12]}.zip"
        result = build_portable_archive(app_dir=app_dir, receipt_path=receipt, output=output,
            source_root=ROOT, version=__version__, profile=args.profile, previous_version=args.previous_platform_version,
            update_channel=args.update_channel, data_policy=args.data_policy, data_root_key=args.data_root_key)
        # 外部报告不进入 ZIP；包含当前构建来源和精确压缩/展开大小，供维护者发布核对。
        report = output.with_suffix(".zip.json")
        with report.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        with output.with_suffix(".zip.sha256").open("x", encoding="ascii") as stream:
            stream.write(f"{result['sha256']}  {output.name}\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.profile == "kokodayo":
            print("[提示] ZIP层级、入口、三段版本、保守1GB及 external 门禁已核对；0.1.7静态证据显示程序目录先删除再解包，平台更新/子进程退出/外置数据保留仍待实测。")
            if args.previous_platform_version is None:
                print("[提示] 未提供平台旧版本，尚未验证平台侧版本递增条件。")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"[失败] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
