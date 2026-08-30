# 游戏资料库：伤害公式与反事实模型状态

本页记录“工具 → 游戏资料库”中公式详情和反事实支持矩阵的数据边界。它不是第二套伤害规则，公式权威内容
仍在 [`damage-calculation.md`](damage-calculation.md)，生产计算仍由现有战报服务负责。

## 所有权与数据流

```text
data/game_static.sqlite3（发行静态，只读）
  → StaticCatalogFormulaQueries（固定 SQL、schema 校验、聚合证据）
  → StaticCatalogFormulaService（公式与支持状态领域投影）
  → formula_detail / counterfactual_model_matrix（Qt 无关展示数据）
  → 后续集成任务接入游戏资料库页面
```

投影不读取账号库、战报库、当前页面、worker 或 C++ 进程，不保存任何状态，也不向生产反事实服务提供功能
开关。页面关闭、账号切换和旧回调因此不涉及本域生命周期；发行 dataset 变化时重新读取即可。

## 公式证据口径

每条公式都必须分别标记以下来源，不能互相冒充：

- `project_contract`：项目确认的公式、乘区归类、默认值和适用条件；
- `implementation`：当前 Python 符号如何消费这些规则；
- `public_behavior_test`：公共行为边界，而不是“存在测试文件”即宣称全部完成；
- `official_static`：SQLite 正式倍率、属性、标签和结构化效果，只是公式输入；
- `repository_audit`：对当前已提交树中消费者或缺失执行器的审计结论。

第一版覆盖角色面板、技能倍率和 `CoefModify`、直伤总式、增伤、易伤、暴击、防御、抗性、独立
`FinalDamageUp`、DOT 专属最终乘区、倾陷、覆纹、最终取整和最大生命下降结算。正式静态记录只显示表/字段、
dataset 和聚合数量；manifest 已声明省略来源 payload，因此本域不承诺原始 payload 浏览。

## 反事实支持状态

状态针对每一行声明的**具体 scope**，不代表整个角色或整个机制族：

- `complete`：该 scope 的输入、公式消费者、未知传播和公共行为边界均有证据；
- `partial`：已有可量化子集，但仍有明确 `gap_codes` 和未覆盖实体/状态；
- `unavailable`：缺少可靠历史状态或生产消费者，不能生成数值，尤其不能以 `ratio=1` 或零收益代替；
- `not_applicable`：该 scope 不进入伤害反事实，不等于相邻系统已经完整模拟。

矩阵每行保存机制方案、证据、消费者入口、缺口代码、dataset、已覆盖角色/机制和限制。状态只用于展示和
审计；集成层不得据此选择 Python/C++、启用回退或改变生产结果。

## 固定轴与正式标签

所有可计算机制都遵守固定轴：保留原动作、逐击、时间、半场和目标，只替换冻结构筑输入。缺失动作、召唤物
生命周期、目标画像、暴击策略、护盾轴或状态事件必须保持 unknown/unavailable。

DOT 分类由导入 Gameplay Tag `State.Damage.Dot` 拥有；不得以手工 DOT 通道白名单替代。正式
`FinalDamageUp` 仍需解析 Source/Target 标签和运行时条件，其中 DOT 双端标签限定的来源进入 DOT 专属槽位，
不能扩散成通用最终增伤。

## 公共入口与验证边界

公共资料库通过独立 Provider 将公式证据和支持矩阵映射到统一搜索/详情契约；组合根只注入发行静态库路径，
不向 Service 传 UI 字段、SQL、Tag 或执行器选择。域专项测试、真实发行只读查询和公共适配器 smoke 均由集成
门禁执行。`native/counterfactual-core/` 已实现独立 C++20 固定轴无状态直伤切片，以公开合成夹具对当前
Python oracle 做 8 组 Buff、56 次逐击差分；它覆盖普通加法面板、增伤、冻结暴击、目标抗性和正式
`DefIgnore` 的受限分支。生产消费者仍只走 Python，DOT、倾陷、反应、状态机及进程/打包生命周期尚未迁移，
因此矩阵标为 `partial` 且没有 consumer entry，不能据此启用 C++、回退执行器或把未接入能力改标为完成。
