# nte-mods-plugin 游戏版本适配与管道故障排查

本文交付给维护 nte-core 抓包、`dwmapi.dll` 装配插件和极速装配链路的开发者。它记录当前正式服兼容
问题的技术结论，并给出后续游戏更新时可复用的定位、修复、验证和回滚流程。

## 1. 结论摘要

本次故障不是 nte-core 抓包失败，也不是 `dwmapi.dll` 未加载。游戏更新改变了 `HTGame.exe` 的 PE
映像身份，而插件尚未把新映像登记为已知 profile。插件能够定位 `AppendName` 和 `GWorld`，但在
Viewport Tick vtable 的有限候选窗口内不能得到唯一候选；未知映像又不能采用已验证的首选索引，因而
Viewport Tick Hook 没有安装。脚本的 `on_viewport_tick` 不执行，IPC 传输层没有进入轮询，最终表现为：

```text
Local\nte-mods-plugin-v1-present        存在
\\.\pipe\nte-mods-plugin-v7           不存在
```

将当前正式服映像加入 `KNOWN_OFFSET_PROFILES` 后，插件可以采用已验证的 Viewport Tick 索引 `100`，
Hook 随即安装，脚本进入运行循环，`nte-mods-plugin-v7` 管道创建成功。实机只读探针结果从 `missing`
变为 `available`。

## 2. 抓包与装配插件的边界

两条链路应分开判断：

```text
游戏网络流量 → nte-core/Npcap → 背包或战斗事件

游戏加载 dwmapi.dll → nte-mods-plugin → Viewport Tick Hook
                    → NTE Script → nte-mods-plugin-v7 → 装备 RPC
```

- nte-core 登录抓取在同一正式服版本上能够生成完整快照，说明抓包链路本身可以工作。
- `Local\nte-mods-plugin-v1-present` 只证明 DLL 已加载并发布 presence event。
- `nte-mods-plugin-v7` 存在只证明游戏内插件已经建立 IPC，不等于某次装配 RPC 已成功。
- 极速装配仍需继续验证请求、响应、稳定快照递增和最终装备状态。

因此，“presence 存在但 pipe missing”应优先检查脚本加载、偏移解析、Viewport 链和 Tick Hook，而不是
反复更换 nte-core 或重新抓包。

## 3. 本次正式服映像身份

运行时导出并扫描得到：

| 字段 | 当前正式服 |
| --- | --- |
| `SizeOfImage` | `0x1066A000` |
| PE `CheckSum` | `0x0FDCF5DD` |
| `AppendName` RVA | `0x016491C0` |
| `GWorld` RVA | `0x0F08CDB0` |
| Viewport Tick vtable index | `100` |
| ProcessEvent vtable index | `0x4C` |

扫描结果具有交叉验证：

- `AppendName` 的完整签名和结构匹配都只得到 `0x016491C0`；
- `GWorld` 的完整序列从 `0x038D766D` 解析到 `0x0F08CDB0`；
- `GWorld` 的放宽尾部结构从 `0x038D767C` 解析到同一个目标 `0x0F08CDB0`；
- `GWorld` 目标位于可写数据区，`AppendName` 位于可执行区。

上一个已支持正式映像为 `SizeOfImage=0x1064D000`。新映像变为 `0x1066A000` 后，不再命中旧的已知
profile。当前实现以 `SizeOfImage` 作为 profile 选择键；PE CheckSum 用于诊断记录，不阻止同映像大小的
小更新使用该 profile。

## 4. 为什么签名命中仍没有管道

`offsets::Initialize()` 的解析顺序是：

1. 尝试签名解析 `AppendName` 和 `GWorld`；
2. 签名未完成时再尝试已知 profile；
3. 保存映像大小、CheckSum 和两个地址。

本次运行映像中的两个关键签名仍能唯一命中，所以函数地址本身不是最终阻塞点。后续安装 Viewport Hook
时还要调用 `ResolveViewportTickIndex()`：

1. 以 profile 中的首选索引为中心，仅扫描前后四个 vtable 项；
2. 已知映像且首选索引可执行时，直接采用已验证的首选索引；
3. 未知映像要求候选结构匹配结果唯一；
4. 候选为零或多个时返回失败，不安装 Hook。

新正式服在该窗口内存在语义相似项，未知映像路径不能唯一裁决。因此即使 `AppendName/GWorld` 已解析，
仍会停在 Viewport Tick 选择阶段。IPC 管道由 Tick 执行路径中的脚本和传输轮询带起，所以 Hook 失败最终
表现为 pipe missing。

## 5. 源码修复

在上游 `native/nte-mods-plugin/src/offset_resolver.cpp` 的 `KNOWN_OFFSET_PROFILES` 中加入：

```cpp
{ 0x1066A000, 0x016491C0, 0x0F08CDB0, 100, 0x4C },
```

完整上下文应类似：

```cpp
constexpr KnownOffsetProfile KNOWN_OFFSET_PROFILES[]{
    { 0x1000C000, 0x0161C020, 0x0EAAADB0, 100, 0x4C },
    { 0x1064D000, 0x0164A940, 0x0F071DB0, 100, 0x4C },
    { 0x1066A000, 0x016491C0, 0x0F08CDB0, 100, 0x4C },
    { 0x1000E000, 0x0161BAE0, 0x0EAAADB0, 100, 0x4C },
};
```

同时给 `tests/offset_profile_tests.cpp` 增加公开行为断言：

```cpp
const bool current_live_known =
    nte::mods::offsets::IsKnownImageProfile(0x1066A000, 0x0FDCF5DD);
return current_live_known ? 0 : 1;
```

本次没有修改签名、IPC 协议、脚本格式、装备 RPC 或 nte-core 协议。修改的行为只有：当前正式服映像被
识别为已验证 profile，从而允许继续采用 Viewport Tick/ProcessEvent 的既有索引。

## 6. 诊断过程与避免误判

### 6.1 第一层：确认 DLL 是否加载

检查以下事实：

- 游戏目录 `dwmapi.dll` 哈希是否等于待测构建；
- 游戏进程是否存在 `Local\nte-mods-plugin-v1-present`；
- `Software\NTE DPS Tool\Mods Plugin` 的 `Workspace` 是否指向完整工作区；
- `nte-mods.enabled` 和两个 `.nte` 文件是否存在且版本匹配。

presence 不存在时，应先排查代理导出、DLL 搜索顺序、进程宿主判断和插件启动线程。presence 存在时，
继续检查脚本与 Hook，不要再把现象归类为“DLL 没加载”。

### 6.2 第二层：区分脚本、偏移和 Hook

当前脚本需要 `viewport.tick`、`game.session`、`equipment`/`combat-clock` 与 `ipc` 能力。任一脚本编译失败
都会保留旧程序；首次加载没有旧程序时，`HasViewportTickPrograms()` 仍为假。

诊断构建应记录以下阶段，而不是只记录最终 pipe：

```text
runtime watcher started
workspace read/compile result
offset resolution result and source
resolved image identity and addresses
viewport chain resolved
viewport tick candidate count/index
hook installed
IPC pipe created/listening
```

### 6.3 第三层：运行映像导出

磁盘上的 `HTGame.exe` 可能经过打包或保护，直接扫描磁盘布局不一定包含运行后的代码。需要导出时，把
诊断动作挂在插件自身的启动线程，而不是挂在 `offsets::Initialize()` 或脚本成功加载之后，否则恰好在
前置阶段失败时不会产生诊断文件。

推荐步骤：

1. `GetModuleHandleW(nullptr)` 取得游戏主模块；
2. 解析 DOS/NT Header 和 Section Table；
3. 写入 `SizeOfHeaders`；
4. 把带 `IMAGE_SCN_MEM_EXECUTE` 的 section 按 `VirtualAddress` 写入输出文件；
5. 将文件长度设为 `SizeOfImage`；
6. 扫描器按 RVA 读取该内存布局文件，不再使用磁盘文件的 `PointerToRawData`。

诊断 DLL 只用于生成本机验证证据。完成后应恢复正式 DLL，不把硬编码诊断输出路径或运行映像带入发行包。

## 7. 构建与验证

### 7.1 原生构建

```powershell
msbuild native/nte-mods-plugin/nte-mods-plugin.sln `
  /m /p:Configuration=Release /p:Platform=x64
```

要求：0 警告、0 错误，并运行更新后的 `offset_profile_tests`。当前 profile 断言的预期输出为：

```text
OFFSET_PROFILE_CURRENT_LIVE_TEST image_size=0x1066A000 checksum=0x0FDCF5DD known=true
```

### 7.2 实机验证

1. 游戏完全退出后备份原 `dwmapi.dll`；
2. 部署新 DLL，并核对游戏目录与构建产物 SHA-256 相同；
3. 从官方启动器启动游戏并进入角色可操作场景；
4. 使用项目只读探针检查管道：

   ```powershell
   python -c "from src.services.dwmapi_diagnostics import probe_equipment_pipe; print(probe_equipment_pipe())"
   ```

5. 预期 `state` 为 `available` 或 `busy`，不再是 `missing`；
6. 再通过 nte-core 发起只读查询或受控测试装配，验证请求/响应协议；
7. 等待新的稳定库存快照，确认装备状态确实变化，不能只以 RPC 接受作为成功依据。

本次正式 DLL 的已验证 SHA-256 为：

```text
BFC54DB88A0F9738AA31A4447EB5ADD6EFEFFFBF28E466C75A5D1B84211CB59E
```

仓库专项测试结果：`dwmapi` 诊断、插件部署/还原、装配执行器和输入后端共 52 项通过。

## 8. 后续版本更新检查表

每次游戏更新按以下顺序处理：

1. 记录 `SizeOfImage`、PE CheckSum、游戏版本和插件哈希；
2. 分别确认 nte-core 抓包、presence event、工作区、pipe，定位故障层；
3. 先运行签名解析，记录候选数量，不直接沿用旧偏移；
4. 对 `AppendName` 使用完整签名与结构匹配交叉验证；
5. 对 `GWorld` 使用完整序列、放宽尾部结构、目标区段属性交叉验证；
6. 验证 Viewport 链和 vtable 首选索引，不因函数偏移命中就假定 Hook 一定成功；
7. 新 profile 必须有原生断言、实机 pipe 证据和至少一次协议/状态确认；
8. 保存原 DLL、修复 DLL、源码 diff、验证记录和可运行回滚；
9. 更新 `third_party/mods-plugin/COMPONENT.md` 与 `SOURCE.md` 的来源、补丁和哈希；
10. 删除或隔离诊断 DLL 与运行映像，发行包只保留正式组件。

## 9. 回滚要求

回滚必须在游戏退出后执行：

1. 用部署前备份覆盖仓库发行 DLL 和游戏目录 DLL；
2. 重新计算两处 SHA-256，必须都等于备份哈希；
3. 保持 Mod 工作区注册值和脚本版本与回滚 DLL 匹配；
4. 再次启动游戏时按对应旧游戏版本验证 presence 和 pipe；
5. 如果游戏版本仍是 `0x1066A000`，回滚到缺少该 profile 的 DLL 会重新出现 pipe missing，这是预期的
   基线行为，不应误判为回滚文件损坏。

## 10. 交付时应附带的信息

向抓包和插件维护者交付时至少附带：

- 本文；
- 修复前后 DLL 的 SHA-256；
- `offset_resolver.cpp` 的最小 diff；
- 运行映像扫描的候选数量与 RVA 摘要；
- 原生 profile 测试的命令、输出和退出状态；
- 实机 presence/pipe 状态；
- nte-core 抓包是否独立成功；
- 回滚步骤及其哈希验证结果。

不要附带账号 UID、完整背包、PCAP、Token、本机绝对路径或未经脱敏的运行日志。
