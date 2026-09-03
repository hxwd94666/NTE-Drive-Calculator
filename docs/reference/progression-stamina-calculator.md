# 养成体力规划

`ProgressionStaminaService` 将已知材料缺口和正式副本确定产出组合为最低体力方案。它是 Qt/SQLite 无关 Service：
页面或工具只投影输入和展示结果，不能复制鉴别等级、副本筛选或最短路算法。

工具页“养成计算器”与本 Service 的边界不同：工具页汇总角色、弧盘和技能的正式材料，不把材料猜成活力；
需要最低体力时，由调用方显式传入猎人等级和副本档位后调用本 Service。

## 冻结输入

一次请求包含：

1. 猎人等级（1–60）与可选生效鉴别等级；10/20/30/40/45/50/55 级对应鉴别 1–7，低于 10 级为 0；
2. 每种材料的正式 `item_id`、总需求和已持有数量；
3. 候选副本的稳定 ID、显示名、最低猎人/鉴别等级、单次体力与确定材料包；
4. 每个材料包的来源。

请求中的显式 `stages` 非空时优先于正式来源；为空时才读取注入的只读正式档位来源。Service 不自行打开数据库，
不读取页面控件或仓库外 JSON。随机、范围或缺失掉落不以均值进入精确规划；用户确认的档位可用
`source=user_supplied`，但必须保留来源标记。

## 算法与结果

先计算 `max(0, 需求 - 已持有)`，过滤当前等级不可进入的档位，再将每次副本的完整材料包作为一次动作进行离散
最短路搜索。同一次掉落满足多种材料时只计一次体力。

结果保留原生/生效鉴别等级、每种材料的需求/持有/缺口、档位次数与体力、已知最低体力、完整最低体力和稳定
gap code：

- `complete`：全部缺口有确定产出，`total_stamina` 为精确最低值；无缺口时为 0。
- `partial`：只计算已闭合部分，显示 `known_stamina`，总量保持 unavailable。
- `unavailable`：没有可用确定产出，或搜索触及保护上限。

## 正式静态档位

`clone_activity_difficulty`、`clone_drop_projection` 与 `clone_drop_projection_item` 提供只读档位。
稳定 ID 为 `clone_id:difficulty_ordinal`；`team_level` 投影最低猎人等级，`difficulty_level` 投影最低鉴别等级，
体力读取 `stamina_cost`。

只有确定正整数掉落进入 `FarmingStage`。`name_missing` 不否定已闭合的正式物品 ID 与数量；
`drop_group_missing`、`sequence_branch_divergent` 及其他非名称 gap 会令该档位退出确定集合。缺少正式来源时结果必须
保留 `partial/unavailable`，不得由中文说明、概率、上下限、经验值或外部解包文件推算。

当前发行静态为 v31、代码候选为 v32；调用方只使用其已核验的只读 schema，不自行兼容已退役的 v30 数据库。
