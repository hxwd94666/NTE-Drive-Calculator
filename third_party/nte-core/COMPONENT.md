# nte-core 组件记录

- 上游项目：`kongbaiz/nte-dps-toolkit`
- 当前二进制版本：`0.4.3`（`nte-core version --json`）
- 源码基线分支：`master`
- 源码基线标签：`v0.4.3-build-146-bff9f56`
- 源码基线提交：`bff9f569ae19fc659b61a3a810407c01be5fc71a`
- 构建工具链：`1.98.0-x86_64-pc-windows-msvc`
- 构建命令：`cargo build --release --bin nte-core --no-default-features --features cli`
- 构建包：`nte-core-windows-x64-0.4.3-bff9f56.zip`
- 构建包 SHA-256：`BF622EA5F9B5A090B9A51CBADD26B948E9AF28E3E4B415556708BBAB56EFB7D6`
- 当前二进制 SHA-256：`8F7DE146B74187988443B19EA7E140E6948FD4490D7BDEEED96A0B8C0D287AC9`
- 上游许可证：AGPL-3.0；本项目根目录的 AGPL-3.0 仅适用于项目自有代码，不能自动改变此组件的许可证。

`bin/nte-core.exe` 从上述远端分支提交在 Windows x64 本地构建，不是已发布的 GitHub Release 资产。
发行副本使用仓库 CI 固定的 UPX 5.2.0 `--best --lzma` 压缩并通过 `upx -t` 完整性检查。该二进制会随
本仓库提交并用于 CI 构建。对应源码获取地址、许可证、构建来源和第三方声明必须与二进制一同保留；更新
二进制时必须同步更新本文件中的版本、源码提交和 SHA-256。

当前 Core 的 CLI `protocol_version` 仍为 1；`battle.get_summary`、`battle.get_record` / `battle.get_axis`
响应契约提供与 `total_damage` 分离的权威 `max_hp_reduction` 聚合值及逐击值。随包中英文协议文件与该
源码基线保持一致。

本目录的协议文件和许可证清单为集成记录，不替代上游完整源码或上游许可证。
