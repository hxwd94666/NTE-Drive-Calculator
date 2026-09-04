# nte-core 组件记录

- 上游项目：`kongbaiz/nte-dps-toolkit`
- 当前二进制版本：`0.4.4`（`nte-core version --json`）
- CLI 协议版本 / 数据版本：`1` / `1`
- 战斗读取契约版本：`5`
- 公开源码分支：`ternary-chen/nte-dps-toolkit` 的 `codex/fix-battle-capture-v044`
- 源码提交：`24bb078fe38675d1719d24e92465a313baf65b3e`
- 源码状态：该提交已发布到上述公开分支，分发记录以提交 ID 和二进制哈希共同定位
- 构建工具链：`1.98.0-x86_64-pc-windows-msvc`
- 构建命令：`cargo build --release --bin nte-core --no-default-features --features cli`
- 未压缩二进制 SHA-256：`CCE437B16BDE3EC444E87AF1F2725349471055E2F9A8EE31791CEE501BE4DAA6`
- 当前随附二进制 SHA-256：`92C5931948F7121942F617939CB8AE5B6638027969DCD0753E1AF7B939D6B6C5`
- 压缩工具：UPX 5.2.0，`--best --lzma`；已通过 `upx -t` 完整性检查
- 上游许可证：AGPL-3.0；本项目根目录许可证不能改变此组件的许可证

本次本地增量包含四条行为链：战斗读取契约 v5 的 `pause_type_mask`；目标句柄规范化、HP 回执与正式
wire target 的逐击去重；活动抓包期间四个战报读取 RPC 的有界 EngineEvent 追赶；无 `target_id` 的敌方
死亡结算标记过滤。暂停事件解释已统一为
一个内部 segmenter，历史裁剪与运行态不再各自维护 Started/MaskChanged/Ended 状态机。

目标补全只会在 settlement 的目标句柄与 CurrentHP 同时匹配该候选逐击时立即释放；同包内其他目标的
settlement 不再提前释放该逐击。没有匹配 settlement 时，候选会在现有 0.5 秒窗口内等待正式 target 记录。

轻量增量验证覆盖：相关逐击去重与混合 settlement、3-bit 身份规范化、暂停 mask 运行态与历史裁剪、live RPC
事件上限、无目标死亡结算标记、CLI `cargo check`、契约版本一致性和 native lifecycle 源码门禁。随附二进制自报
`0.4.4 / protocol 1 / data 1`。它是从公开源码提交生成的本机开发构建，不是 GitHub Release 资产；
分发时仍需随安装包保留对应源码入口及许可证材料。
