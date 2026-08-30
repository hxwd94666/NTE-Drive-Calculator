# 养成体力计算

`ProgressionStaminaService` 是角色、弧盘、技能及后续养成页面共用的 Qt/SQLite 无关入口。各页面只负责把
自己的当前/目标状态投影为材料需求，不复制鉴别等级、副本过滤或体力算法。

## 等级输入

- **猎人等级**：当前版本范围为 1–60。达到 10/20/30/40/45/50/55 时，鉴别等级分别为 1–7；低于
  10 级时为 0。
- **生效鉴别等级**：默认等于猎人等级投影。原生鉴别等级达到 3 后，可以显式下调一级，不能自动下调或跨级。
- **养成对象状态**：角色、弧盘和技能各自负责将当前等级、突破阶段、技能等级及目标状态汇总为材料 ID 和
  需求数量；体力 Service 不读取页面控件或重新推断突破阶段。

## 计算输入

一次不可变请求包含：

1. 猎人等级和可选的生效鉴别等级；
2. 每种材料的正式 `item_id`、总需求与已持有数量；
3. 候选副本档位的稳定 ID、显示名、最低猎人/鉴别等级和单次体力；
4. 每个档位一次完成后可以确定获得的正整数材料包及其来源。

只有确定产出进入精确规划。随机概率、范围产出或尚未导入的 `drop_id` 不得用均值冒充确定数量；调用方可以
提供用户确认的单次产出，但必须保留 `source=user_supplied`。

`ProgressionStaminaService` 可注入只读正式档位来源。请求的 `stages` 非空时，它们是本次用户显式输入并优先
于正式来源；只有 `stages=()` 时才读取注入来源，未注入时继续按无可用档位处理，不在 Service 内自行打开数据库。

## 游戏资料库公共面板

角色等级、技能和弧盘详情页把自己的不可变需求 DTO 交给同一个公共养成面板。面板不拥有材料算法，也不读取
其他页面控件；组合根注入 `ProgressionStaminaService`、`StaticCatalogTerminologyService`，并通过一次性
结果回调把带稳定路由身份的 `ProgressionStaminaResult` 投回发起页面。冻结会话和结果都显式携带
`owner_id`（角色 ID 或弧盘 ID）与可选 `skill_id`；组合根直接使用这些字段核对当前详情，不从兼容
`entity_id` 拼接字符串中拆身份。

面板使用卡片而非数据表：顶部编辑猎人等级并显示原生鉴别等级，原生鉴别等级达到 3 后才提供“下调一级”；
每张材料卡显示正式本地化名称、所需量和用户可编辑的当前持有量。材料原始 ID、canonical ID、text table/key
与命中语言只放在默认折叠的“更多信息”。缺少正式名称时统一显示“名称暂未提供”，View 不维护材料名或货币
别名。

角色字典请求必须提供 `kind`、稳定对象身份、`requirements`、`requirement_status` 与
`requirement_gaps`；弧盘使用公开 `ForkProgressionRequest`。Qt 无关适配器会合并同一 canonical item、保留
上游未知数量和 gap code，再把可确定的需求交给公共 Service。上游为 `partial/unavailable` 时，即使已知部分
可以求得体力，也不得把最终状态提升为 `complete`，且完整 `total_stamina` 保持不可用。弧盘未知总需求若有
正式已知下界，只计算该下界并明确标记为部分结果。请求若声称 `complete` 却同时携带 gap，适配器会在进入
公共 Service 前按是否存在可计算需求降为 `partial` 或 `unavailable`，不会接受相互矛盾的完整状态。

公共面板是组合根持有的可复用 modeless dialog；`open_request(..., on_result=...)` 冻结并替换本次请求，
`dispose()` 在应用关闭时先解除页面回调再关闭窗口。结果回调通过 Qt 无关安全边界调用；目标页面已释放或
回投失败时记录不含原始路径的错误事件，并在面板显示可读错误，不让异常逃逸 Qt `clicked` 处理器。页面和
组合根均不得复制体力求解或使用服务定位器。

## 输出与算法

计算先执行 `max(0, 需求 - 已持有)`，再过滤当前等级不可用的副本。可用副本可能在同一次掉落多种材料，
Service 将整个掉落包作为一次动作，用离散最短路求满足全部已知缺口的最低体力，因此不会把同一次掉落按
材料重复计费。

结果分别保留：

- 原生和生效鉴别等级；
- 每种材料的需求、持有和缺口；
- 每个副本档位的次数、单次/合计体力和总产出；
- `known_stamina`：已具备确定产出的部分最低体力；
- `total_stamina`：只有所有材料均可解析时才存在；
- 未解析材料 ID 和稳定 gap code。

状态语义：

- `complete`：所有材料均有确定可用产出，`total_stamina` 是精确最低值；
- `partial`：部分材料可计算，只展示 `known_stamina`，总量保持 unavailable；
- `unavailable`：没有可用确定产出，或精确搜索超过保护上限；
- 无材料缺口属于 `complete`，总量为 0。

## 正式静态档位

schema v30 通过 `clone_activity_difficulty`、`clone_drop_projection` 和
`clone_drop_projection_item` 提供正式只读档位。稳定档位 ID 为 `clone_id:difficulty_ordinal`；
`team_level` 投影最低猎人等级，`difficulty_level` 投影最低鉴别等级，单次体力直接读取 `stamina_cost`。
DAO 只把 `clone_drop_projection_item` 中确定的正整数数量投影为 `FarmingStage`：

- `name_missing` 只表示展示名称尚未齐全，不否定已经闭合的正式物品 ID 和数量；
- `drop_group_missing`、`sequence_branch_divergent` 及其他非名称 gap 会使整个档位退出确定产出集合，即使同一
  投影还保留部分条目，也不会把不完整掉落包冒充确定材料包；
- 缺少正式产出的材料需求继续返回 `partial/unavailable`；
- 运行时不读取仓库外导出目录，也不从中文说明、概率、范围上下限或经验值推算单次产出。

正式库尚未晋升到 v30 时，组合根不得注入 v30 档位来源；显式用户档位仍按原 contract 可用。
