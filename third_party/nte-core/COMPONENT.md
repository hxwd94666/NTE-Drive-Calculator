# nte-core 组件记录

- 上游项目：`kongbaiz/nte-dps-toolkit`
- 当前二进制版本：`0.4.2`（`nte-core version --json`）
- 源码基线分支：`codex/dedupe-embedded-resources-upx-lzma`
- 源码基线提交：`508bebafeea8958f5efc9451c9832d55c9a38242`
- 开发构建 ZIP SHA-256：`4663A48F42FCEC317384D646912C7622D63865EAF92759E350B49D1CA5E8C1BC`
- 当前本机二进制 SHA-256：`B4D7AA3B068672ACE2D610E8280071CFC9BE7C1EBF58849765C0D428E9A37C6C`
- 上游许可证：AGPL-3.0；本项目根目录的 AGPL-3.0 仅适用于项目自有代码，不能自动改变此组件的许可证。

`bin/nte-core.exe` 来自用户提供的上游 0.4.2 开发构建包 `nte-dps-tool dev.zip`，不是已发布的
GitHub Release 资产；本次同步的上游源码基线为上述远端分支提交。该二进制会随本仓库提交并用于 CI
构建。对应源码获取地址、许可证、开发构建来源和第三方声明必须与二进制一同保留；更新二进制时必须同步
更新本文件中的版本、源码提交和 SHA-256。

当前 Core 的 CLI `protocol_version` 仍为 1；`battle.get_record` / `battle.get_axis` 响应契约为 v3，
新增权威逐击 `overkill_damage`。随包中英文协议文件与该源码基线保持一致。

本目录的协议文件和许可证清单为集成记录，不替代上游完整源码或上游许可证。
