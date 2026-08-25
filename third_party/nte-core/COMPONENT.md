# nte-core 组件记录

- 上游项目：`kongbaiz/nte-dps-toolkit`
- 当前二进制版本：`0.4.2`（`nte-core version --json`）
- 源码基线分支：`codex/dedupe-embedded-resources-upx-lzma`
- 源码基线提交：`c1165ff2ddaf6fe33936347a5156dd7e796180f8`
- 构建工具链：`1.98.0-x86_64-pc-windows-msvc`
- 构建命令：`cargo build --release --bin nte-core --no-default-features --features cli`
- 构建包：`nte-core-windows-x64-0.4.2-c1165ff.zip`
- 构建包 SHA-256：`DF0E950EDC4650635917C712EF3630A598AF901DEB73D725013B4A15090F520B`
- 当前二进制 SHA-256：`8EEF816AEE49675649424E752277F11C544F49567B19AE750E019E98C21B2CF3`
- 上游许可证：AGPL-3.0；本项目根目录的 AGPL-3.0 仅适用于项目自有代码，不能自动改变此组件的许可证。

`bin/nte-core.exe` 从上述远端分支提交在 Windows x64 本地构建，不是已发布的 GitHub Release 资产。
发行副本使用仓库 CI 固定的 UPX 5.2.0 `--best --lzma` 压缩并通过 `upx -t` 完整性检查。该二进制会随
本仓库提交并用于 CI 构建。对应源码获取地址、许可证、构建来源和第三方声明必须与二进制一同保留；更新
二进制时必须同步更新本文件中的版本、源码提交和 SHA-256。

当前 Core 的 CLI `protocol_version` 仍为 1；`battle.get_record` / `battle.get_axis` 响应契约为 v4，
在 v3 权威逐击 `overkill_damage` 之外新增结构化 `max_hp_reduction`。随包中英文协议文件与该源码基线保持一致。

本目录的协议文件和许可证清单为集成记录，不替代上游完整源码或上游许可证。
