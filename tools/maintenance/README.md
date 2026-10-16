# 精确旧发行候选来源

用户操作顺序、参数、数据迁入与恢复限制以 [发行集成说明](../../docs/integrations.md) 为唯一说明；此处只记录候选来源与维护工具边界。`Invoke-LegacyTakeover.ps1` 运行于待更新应用加载链之外，默认 Plan、显式 Apply/Rollback，不是下载器或安装器。`audit_legacy_profile.py` 仅供维护者只读复核，不是终端用户依赖。

## 唯一已核对候选

`legacy_profiles/2.3.1-post-refactor-candidate.json` 覆盖一个实际旧 2.3.1 候选的 1370 个程序文件：旧 onedir 与对应安装器只读提取的 `{app}` 程序成员，路径、大小、SHA-256 完全一致。它不是所有 2.3.1 或历史发行的通用清单。profile SHA-256：

```text
8e0785118d2475888fdbc25caaf00edc135e8e2a91726de90a72cf5663c497cf
```

profile 的 `sources_sha256` 固定安装器、Inno 脚本和 COLLECT 来源；本机来源位置只在维护者 build 证据中保存。其他旧包须先审核其精确成员及来源，不通过改版本号复用此 profile。

对应安装器的 `[Files]` 另有根 `config/stats.json` 和 `drivers/ViGEmBus_Setup.exe`；它们不在程序清理域，保留且不执行。账号、配置、共享用户库、外部数据根和注册表均不修改。旧 `_internal` 中发行默认静态/模板仍是程序资源；其退役字节被用户修改时保留并阻止，不当作用户数据自动迁移或删除。

## 安装器生成文件的证据边界

[Inno 官方 `{uninstallexe}` 文档](https://jrsoftware.org/ishelp/topic_consts.htm) 描述 `unins000.exe` 示例；本候选真实 Inno 脚本使用默认卸载配置并建立 `{uninstallexe}` 快捷方式。使用[官方维护的 innounp](https://www.rathlev-home.de/tools/download/innounp.htm) 对实际安装器以 `-v -m` 列内部文件，未取得安装后生成的卸载字节。因此这些字节不进入 profile，也不用于删除。

仅根目录确切 `unins000.exe` 作为不由 NDC 自动发现/加载/调用的普通文件保留：先拒绝链接和目录；不执行、不删除、不背书其内容或原卸载登记是否仍有效。`unins000.dat` 等普通根文件同样保留。其他根 EXE（包括可被实际发现的 `nte-core.exe`）、其他卸载序号以及 `_internal/unins000.exe` 仍受未知加载文件阻断，未泛化支持。组件发现链使用正式确切名称，此例外不授予任意文件组件身份。

PowerShell 脚本采用 UTF-8 BOM 以兼容 Windows PowerShell 5.1；profile、发行标识与清单则严格按应用协议使用无 BOM 的 UTF-8 JSON。摘要仅核对一致性，分发渠道信任仍须独立成立。
