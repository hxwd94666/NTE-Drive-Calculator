# nte-core 对应源码

本仓库随附的 `bin/nte-core.exe` 基于 `kongbaiz/nte-dps-toolkit`，对应源码已发布到维护分支：

- 二进制版本：`0.4.4`
- 上游源码：<https://github.com/kongbaiz/nte-dps-toolkit>
- 公开源码分支：<https://github.com/ternary-chen/nte-dps-toolkit/tree/codex/fix-battle-capture-v044>
- 对应源码提交：<https://github.com/ternary-chen/nte-dps-toolkit/commit/24bb078fe38675d1719d24e92465a313baf65b3e>
- 本地增量：战斗读取契约 v5、类型化时停、目标身份/逐击对账、活动期有界读取和无目标死亡结算标记过滤
- 构建工具链：Rust `1.98.0-x86_64-pc-windows-msvc`
- 构建命令：`cargo build --release --bin nte-core --no-default-features --features cli`
- 未压缩 SHA-256：`CCE437B16BDE3EC444E87AF1F2725349471055E2F9A8EE31791CEE501BE4DAA6`
- UPX 5.2.0 压缩后 SHA-256：`92C5931948F7121942F617939CB8AE5B6638027969DCD0753E1AF7B939D6B6C5`
- 许可证：本目录 `LICENSE`（AGPL-3.0）

这是从上述公开提交构建的开发组件；分发时必须同时保留对应完整源码入口与许可证材料。
