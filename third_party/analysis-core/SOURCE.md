# 独立战报分析组件

本组件由独立的 `analysis-core/Cargo.toml` 构建，产物为 `nte-analysis-core.exe`，许可为 AGPL-3.0-or-later。
它不链接、启动或替换采集组件 `nte-core.exe`，不接入游戏或抓包。Rust 依赖由独立 Cargo manifest 与 lock
管理，包含 serde、serde_json、rusqlite（内置 SQLite）、sha2 及默认启用的 mimalloc。
依赖原始许可随 `THIRD_PARTY_NOTICES.txt` 分发，组件清单记录分配器与通知文件摘要。

正式 `battle_page_v1` 接口接收冻结的数据库选择器和用户输入，只读加载账号战报与正式静态数据，完成
轴与来源解析、状态、逐击重放、目标拟合、反事实和候选编排。Python 管理输入、进程、解码及展示；
Rust 返回的版本化目标派生快照由 Python 在复核账号和静态配置后通过既有 DAO 保存，原始事实不改写。
低层公式、Buff 投影与批量算法协议继续提供独立调用和差分验证能力，详细契约见独立 crate README。

部署命令为 `python tools/counterfactual/package_rust_core.py --source <analysis-core目录>`。
该命令核对程序身份，记录独立源码摘要、编译版本、许可与二进制 SHA-256，并生成只含程序、组件清单、
来源说明和许可通知的组件归档。清单记录源码文件摘要，不打入私有源码或调试符号。
`--destination` 可先输出到隔离验证目录。核验通过的 `bin/nte-analysis-core.exe` 与 `component.json`
成对提交到本仓库，源码使用者拉取代码时同步取得配套组件；更新或回滚时必须同时替换程序和清单。
组件产物允许按用户授权交付到 Calc 或公开发布位置，范围限于程序、清单、来源说明和许可通知。
私有 Rust/C++/Mod 源码、源码归档、调试符号、账号数据、凭据和敏感日志不得随包外发；公开组件目录采用
允许清单跟踪文件，其他本机构建产物不进入 Git。Git 忽略规则不能代替对已跟踪文件和归档内容的检查。

本说明描述源码与分发边界，不证明本机组件已经更新。启用前须核对组件能力与哈希，并完成冻结副本差分、
进程取消、打包输入、升级回滚及真实 Windows 页面验证；不以局部计算核计时替代实机验收。
