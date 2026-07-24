# nte-core CLI 协议

[English](CLI_PROTOCOL.md) | 简体中文

`nte-core` 是仅输出英文机器协议值的无界面本地 Sidecar。协议字段和值保持稳定；调用方必须根据 JSON-RPC 数字错误码和 `error.data.domain_code` 分支，不能依赖用于人工阅读的 `message` 或 `detail` 文案。

正式发行包为 `nte-core-windows-x64.zip`，包含 `nte-core.exe`、`CLI_PROTOCOL.md`、`CLI_PROTOCOL_ZH.md`、`examples/`、`THIRD_PARTY_LICENSES.md` 与 `licenses/`。Sidecar 供同一台 Windows 计算机上的第三方工具调用，不监听或开放 TCP/HTTP 端口。CLI 只内嵌抓包解析所需的核心 JSON，不包含 GUI 图片、字体、应用图标或 GUI 依赖栈。发行和集成仍受仓库 AGPL／商业双授权约束。

## 命令行

```text
nte-core serve --stdio [--data-dir <path>] [--log-level <level>]
nte-core version --json
nte-core devices --json
```

未知参数或缺失参数值会向 stderr 写入一行英文错误，并以退出码 2 结束。`devices --json` 成功时向 stdout 写入 `{"devices":[...]}` 并退出 0；Npcap 错误写入 stderr 并退出 1。`version --json` 和成功的 `devices --json` 都只向 stdout 写入一行 JSON。

`--data-dir` 指定 Core PCAP 文件根目录，默认是 `logs`。显式目录会直接使用，并在原始抓包启动时创建。

### 一次性命令示例

```powershell
.\nte-core.exe version --json | ConvertFrom-Json
.\nte-core.exe devices --json | ConvertFrom-Json
```

## 传输协议

`serve --stdio` 使用 UTF-8 JSON-RPC 2.0 over NDJSON：

- stdin 每行承载一个请求对象；
- stdout 每行承载一个响应或事件对象，不得出现其他文本；
- stderr 专用于英文诊断日志；
- 每条 stdout 消息都会立即 flush；
- 空输入行会被忽略；
- 不支持 JSON-RPC batch 数组和客户端 notification；
- 请求 ID 只能是字符串、整数或 null；
- 单行最大 1 MiB，不包含换行符；
- 超长行会收到 Invalid Request 响应，随后服务关闭；
- stdin EOF 或 stdout broken pipe 会让服务以退出码 0 关闭。

响应和 `event.*` notification 共用 stdout，二者可能交错出现。调用方必须按 `id` 匹配响应，并按无 `id` 消息的 `method` 路由事件；不能假定下一行就是最近一次请求的响应。

### PowerShell stdio 示例

下面会发送一段完整、有限的 NDJSON 会话。最后一行发送后 PowerShell 会关闭 stdin，而 `core.shutdown` 会先请求 Core 正常退出。

```powershell
$requests = @(
  '{"jsonrpc":"2.0","id":"1","method":"core.hello","params":{"client_name":"PowerShell example","client_version":"1.0.0","protocol_min":1,"protocol_max":1}}'
  '{"jsonrpc":"2.0","id":"2","method":"capture.detect","params":{}}'
  '{"jsonrpc":"2.0","id":"3","method":"core.status","params":{}}'
  '{"jsonrpc":"2.0","id":"4","method":"core.shutdown","params":{}}'
)
$requests | .\nte-core.exe serve --stdio
```

### Python stdio 示例

[`examples/nte_core_client.py`](examples/nte_core_client.py) 是一个只使用 Python 标准库的客户端，包含独占 stdout reader、响应 ID 匹配、事件路由、超时和正常关闭逻辑。需要 Python 3.10 或更高版本，不依赖第三方包。

```powershell
# 握手、状态、环境探测、关闭；不会开始抓包
python .\examples\nte_core_client.py .\nte-core.exe

# 可选：进行 15 秒战斗抓包，但不创建 PCAP 文件
python .\examples\nte_core_client.py .\nte-core.exe `
  --live-seconds 15 --profile combat --raw-capture disabled
```

实时示例会打印 `event.*`，然后查询 `inventory.get_latest` 和 `battle.get_summary`。如果短时间内没有观察到对应数据，背包查询可能正常返回 `INVENTORY_NOT_READY`，战斗摘要也可能为 null。

## 错误

支持以下标准 JSON-RPC 错误：

| Code | Message |
| ---: | --- |
| -32700 | Parse error |
| -32600 | Invalid Request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |

Core 领域错误使用 code `-32000`、message `Core error`，并提供稳定的 `error.data.domain_code`。协议版本 1 可能返回：

- `PROTOCOL_VERSION_MISMATCH`
- `HANDSHAKE_REQUIRED`
- `NPCAP_NOT_FOUND`
- `GAME_PROCESS_NOT_FOUND`
- `CAPTURE_DEVICE_NOT_FOUND`
- `SYSTEM_PROBE_FAILED`
- `CAPTURE_ALREADY_RUNNING`
- `CAPTURE_NOT_RUNNING`
- `INVENTORY_NOT_READY`
- `EQUIPMENT_PLUGIN_UNAVAILABLE`
- `EQUIPMENT_PLUGIN_BUSY`
- `EQUIPMENT_REQUEST_REJECTED`

底层操作系统、Npcap、端点、payload 和文件系统技术细节不会复制到 stdout。

## 握手

除 `core.hello` 和 `core.shutdown` 外，其他方法都要求先成功握手。重复发送有效握手是幂等操作。

```json
{"jsonrpc":"2.0","id":"hello-1","method":"core.hello","params":{"client_name":"NTE Drive Calc","client_version":"1.3.0","protocol_min":1,"protocol_max":1}}
```

结果包含 `core_version`、协商后的 `protocol_version`、`data_version`、`capabilities` 和 `raw_capture_default`。当前 Core 构建支持本机装备插件桥接时，`capabilities` 包含 `equipment`。只有本文列出的方法可调用。

## 方法索引

| Method | Params | 需要握手 | Result |
| --- | --- | --- | --- |
| `core.hello` | 握手对象 | 否 | 版本、能力、原始抓包默认值 |
| `core.status` | `{}` 或省略 | 是 | 当前抓包、背包和战斗状态 |
| `core.shutdown` | `{}` 或省略 | 否 | `shutting_down` |
| `capture.detect` | `{}` 或省略 | 是 | 游戏/网卡探测快照 |
| `capture.start` | 抓包选项 | 是 | 进程内 `operation_id` |
| `capture.stop` | `{}` 或省略 | 是 | 已停止的 `operation_id` |
| `inventory.get_latest` | `{}` 或省略 | 是 | 最新完整背包快照 |
| `equipment.equip_module` | 角色、装备、行、列 | 是 | 插件派发状态 |
| `equipment.equip_core` | 角色、装备 | 是 | 插件派发状态 |
| `equipment.unequip_module` | 角色、装备 | 是 | 插件派发状态 |
| `equipment.unequip_core` | 角色、装备 | 是 | 插件派发状态 |
| `equipment.unequip_all` | 角色 | 是 | 插件派发状态 |
| `equipment.equip_one_key` | 角色、位置列表、核心 | 是 | 插件派发状态 |
| `equipment.move_module_to_character` | 角色、装备、行、列 | 是 | 插件派发状态 |
| `equipment.move_core_to_character` | 角色、装备 | 是 | 插件派发状态 |
| `equipment.set_item_discarded` | 装备、弃置状态 | 是 | 插件派发状态 |
| `equipment.set_item_locked` | 装备、锁定状态 | 是 | 插件派发状态 |
| `battle.get_summary` | `subtract_time_stop` | 是 | 战斗摘要或 null |
| `battle.reset` | `{}` 或省略 | 是 | `reset:true` |

## Core 方法

### `core.hello`

协商协议版本 1。客户端名称或版本为空、字段缺失、版本范围倒置时返回 Invalid params；范围不包含版本 1 时返回 `PROTOCOL_VERSION_MISMATCH`。

### `core.status`

返回握手状态、抓包是否运行、当前 profile、Core 状态、最新背包 generation、是否存在战斗数据和可公开的原始抓包路径。空闲时 `core_state` 为 `idle`，抓包时为 `capturing`。

### `core.shutdown`

握手前后均可调用。返回 `{"shutting_down":true}`，flush 响应后以退出码 0 结束。stdin EOF 会执行相同清理，但不返回响应。

### `capture.detect`

枚举 Npcap 网卡并返回：

```json
{
  "game_process_detected": false,
  "recommended_device": null,
  "local_ip_detected": false,
  "devices": [
    {"name":"...","description":"...","ipv4":["192.168.x.x"]}
  ]
}
```

游戏未运行、没有可用活动 TCP 连接或没有匹配 Npcap 网卡时，以正常的 `false`/`null` 状态报告。Npcap 失败和初始进程枚举失败会返回领域错误。当前 platform 边界无法区分“没有游戏连接”和“TCP 表探测失败”，二者都按尽力而为的 `local_ip_detected:false` 处理。不会返回远端服务器地址或端口。

## 抓包与背包方法

### `capture.start`

```json
{
  "jsonrpc":"2.0",
  "id":"start-1",
  "method":"capture.start",
  "params":{
    "profile":"inventory",
    "device":{"mode":"auto"},
    "include_incoming":true,
    "server_damage_calibration":true,
    "raw_capture":"enabled"
  }
}
```

`profile` 为 `inventory` 或 `combat`。`device` 为 `{"mode":"auto"}`，或 `{"mode":"name","name":"Npcap device name"}`。`raw_capture` 可省略，默认 `enabled`；`disabled` 不创建或写入 PCAP 文件。自动选网卡要求已探测到游戏连接。手动模式下，命名网卡缺失是硬错误，游戏连接不可用则只软降级本机 IP 推断。

响应包含进程内有效的 `operation_id`，不会等待首个封包或背包快照。可靠的 `event.capture.status` 会报告 `starting`、`running` 和 `stopped`，并携带单调递增的 `sequence`、operation ID 和 profile。已有抓包运行时再次开始会返回 `CAPTURE_ALREADY_RUNNING`。

只有调用方显式发送 `"raw_capture":"enabled"` 时，原始 PCAP 路径才会出现在 `core.status`。省略该字段仍保持默认启用，但不会广播路径。

### `capture.stop`

停止并 join 抓包/解析线程，flush PCAP writer，排空已经产生的引擎事件，然后返回被停止的 operation ID。`event.capture.status` 会报告 `stopped`。没有活动抓包时调用会返回 `CAPTURE_NOT_RUNNING`。停止前已进入队列的有效背包快照会在清理完成前保留并发出。

### `inventory.get_latest`

返回最新完整背包快照，不会自行开始抓包，也不会写业务背包文件。首次完整快照出现前返回 `INVENTORY_NOT_READY`。

背包结果和 `event.inventory.snapshot` 都包含 `generation`、`observed_at_unix_ms`、`complete`、`item_count` 和 `items`；事件还包含全局 `sequence`。内部物品 ID 映射为：

```json
{
  "uid":{"slot":1,"serial":2},
  "locked":true,
  "discarded":false,
  "equipped":true,
  "equipped_character_uid":{"slot":3,"serial":4},
  "equipped_character_id":1020,
  "equipped_placement":{"row":2,"column":3}
}
```

内部拼写错误 `solt` 不会暴露。已知装备定义包括 kind、quality、geometry、grid、suit ID、物品/套装多语言名称、等级上限和多语言属性元数据。未知物品或属性定义会保留稳定 ID 和数值，可选元数据保持 null，不会伪造。
`equipped_character_id` 是 `res/data/characters/characters.json` 中作为键使用的稳定角色 ID；物品未装备或尚未解析出装备者时为 null。`equipped_character_uid` 仍表示当前账号内的角色物品实例 UID。
`equipped_placement` 表示已装备驱动块从 1 开始的锚点行列；卡带、未装备驱动块或尚未解析出位置时为 null。

## 装备方法

装备方法通过本机命名管道 `\\.\pipe\nte-equipment-plugin-v3` 调用
`nte-equipment-plugin` ABI v4 / IPC v3。Core 不负责注入或加载插件；与当前
客户端匹配的插件必须已经由 `HTGame.exe` 加载。角色与装备 UID 都使用背包
快照返回的 `{"slot":1,"serial":2}` 结构；两个分量都必须非零，且都不能为
`4294967295`（`u32::MAX`）。驱动块行列从 1 开始，且必须同时位于 `1..5`。
`equip_one_key` 的每个位置项形如
`{"equipment":{"slot":3,"serial":4},"row":1,"column":2}`，数组必须包含
1..64 项；`discarded` 与 `locked` 必须是 JSON 布尔值。

各方法直接对应插件操作：

| Method | 作用 |
| --- | --- |
| `equipment.equip_module` | 把未装备驱动块装配到指定行列 |
| `equipment.equip_core` | 装配未装备卡带 |
| `equipment.unequip_module` | 从角色卸载驱动块 |
| `equipment.unequip_core` | 从角色卸载卡带 |
| `equipment.unequip_all` | 卸载指定角色的全部装备 |
| `equipment.equip_one_key` | 一次装配 1..64 个驱动块位置和一个卡带 |
| `equipment.move_module_to_character` | 把已装备驱动块转移到另一角色的指定位置 |
| `equipment.move_core_to_character` | 把已装备卡带转移到另一角色 |
| `equipment.set_item_discarded` | 设置或清除装备弃置标记 |
| `equipment.set_item_locked` | 锁定或解锁装备 |

示例：

```json
{
  "jsonrpc":"2.0",
  "id":"move-1",
  "method":"equipment.move_module_to_character",
  "params":{
    "character":{"slot":1,"serial":2},
    "equipment":{"slot":3,"serial":4},
    "row":2,
    "column":5
  }
}
```

成功结果为 `{"status":"rpc_dispatched"}`；备用值
`{"status":"dry_run_ok"}` 只用于试运行插件宿主。派发成功仅代表 RPC 已提交，
调用方必须等待之后抓包产生的 `event.inventory.snapshot` 确认游戏/服务器状态。
管道缺失或超时返回 `EQUIPMENT_PLUGIN_UNAVAILABLE`；超过一个执行中请求和一个排队请求时
返回 `EQUIPMENT_PLUGIN_BUSY`；插件校验返回派发/试运行
以外状态时返回 `EQUIPMENT_REQUEST_REJECTED`。

## 抓包与背包事件

- `event.capture.status`：可靠的抓包生命周期状态；
- `event.inventory.snapshot`：可靠的完整、已补充元数据的背包快照；
- `event.core.warning`：英文抓包降级信息；
- `event.core.error`：英文抓包失败信息。

stdout 永远不会输出 `PacketDebug`、payload preview、payload hex、decoded text、网络端点或 PCAP 内容。

## 战斗方法

### `battle.get_summary`

```json
{
  "jsonrpc":"2.0",
  "id":"battle-1",
  "method":"battle.get_summary",
  "params":{"subtract_time_stop":true}
}
```

`subtract_time_stop` 必填，选择与 GUI 相同的计时口径。尚无战斗或深渊数据时 result 为 null；否则包含总时长、伤害、DPS、承伤、命中数、角色行、技能行、深渊上下半和脱敏解析质量计数。稳定的 `dps_time_mode` 值为 `subtract_time_stop` 与 `wall_clock`；quality source 值为 `live`、`pcapng_replay`、`json_replay` 和 `unknown`。

外部战斗 DTO 由内部 `CombatSessionSummary` 显式逐字段映射，不直接暴露内部 Rust 序列化结构。所有数值都来自已验证的战斗状态，并保证是有限 JSON 数字。

### `battle.reset`

只重置战斗命中、聚合、深渊状态、时停状态、封包和解析质量计数。不会停止活动抓包、删除 PCAP、清除最近背包快照或修改背包 generation。返回 `{"reset":true}`。

## 战斗事件

`event.battle.summary` 使用 `subtract_time_stop=true`，DTO 与 `battle.get_summary` 相同，另含全局 `sequence`。普通更新最多每 250 ms 发布一次（4 Hz），并使用单一 latest-value 槽；stdout writer 尚未消费的旧摘要会被新摘要替换，因此 Npcap 采集不会等待该通道。抓包停止和进程关闭会清除待发送摘要，并通过可靠输出队列发送一次最终摘要，然后再完成 stopped/shutdown 流程。

事件不包含逐击、PacketDebug、payload、网络端点或 PCAP 内容。

## 典型实时集成顺序

1. 使用 stdin/stdout pipe 启动 `nte-core serve --stdio`；
2. 发送请求前先启动常驻 stdout reader；
3. 调用 `core.hello`，检查 `protocol_version` 和 `capabilities`；
4. 调用 `capture.detect`，选择自动模式或一个返回的网卡名称；
5. 使用 `inventory` 或 `combat` profile 调用 `capture.start`；
6. 持续消费抓包状态、背包、warning/error 和战斗事件；
7. 需要时点快照时调用 `inventory.get_latest` 或 `battle.get_summary`；
8. 调用 `capture.stop`，等待响应和最终可靠事件；
9. 调用 `core.shutdown`，关闭管道并等待进程退出。

手动网卡模式把自动 selector 替换为：

```json
{"mode":"name","name":"capture.detect 返回的 Npcap 网卡名称"}
```

只需要业务 DTO、对隐私敏感的集成应显式发送 `"raw_capture":"disabled"`。这会关闭 PCAP 文件创建，但不会改变战斗或背包解析。
