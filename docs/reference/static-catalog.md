# 游戏资料库

“工具 → 游戏资料库”只读打开发行静态库，以一次冻结的 dataset、schema、importer 和构建时间为请求边界。
页面不读取账号库，不允许输入 SQL、表名、字段名或 Gameplay Tag，不启动战报 worker，也不改变任何配置。

当前公共入口包含：

- **覆盖总览**：固定登记 schema v29 的 110 张表，逐表显示行数、领域与 A–E 覆盖状态；空表和发行时省略
  的 `source_row.payload_json` 仍显式登记，不用 0 伪装未知值。
- **角色数据**：角色身份、1–80 级面板、临界等级突破前后、技能等级和正式材料 ID、觉醒、好感度、培养
  路线、推荐弧盘与毕业模板。当前 schema 没有角色升级/突破材料关系时明确显示 unavailable。
- **弧盘数据**：1–80 级经验与面板、突破材料/金币、精炼 1–5 级说明与参数、Buff、GE、触发和角色关系。
- **怪物与玩法**：图鉴、模板画像、争锋、轨外、副本、异象追猎和具有正式怪物池的高危委托。身份只能来自
  正式绑定、ID 或类路径，不从中文名猜测。
- **装备、技能/伤害、Buff/效果、资源/动画、来源追溯**：使用 typed record ID 在窄领域内搜索与跳转；
  Blueprint 引用、Gameplay Tag、语义属性、GA→GE、GA→Montage 和 Montage Notify 在单个资源详情内按
  50 行懒加载；`target_available=false` 的关系逐行显示 unavailable，但不生成可点击目标。正式目标存在但
  当前资料库没有对象级详情（如 CurveTable）时，保留目标证据并单列详情 unavailable，也不生成坏按钮。
- **伤害公式与反事实模型**：区分正式静态、项目规则与派生展示值。DOT 只以正式
  `State.Damage.Dot` 标识，`FinalDamageUp` 单独列示。unknown/unavailable 不落成 0 或倍率 1。

反事实矩阵报告的是现有生产消费者的证据与缺口。独立 C++ sidecar 仅用于与 Python 金标准做差分验证，
不是生产执行入口，也不会因为差分通过而把尚未接入生产链的模型标成已完成。

各领域字段、来源和已知缺口见：

- [角色数据域](static-catalog-character-domain.md)
- [弧盘数据域](static-catalog-fork.md)
- [怪物与玩法域](static-catalog-monster-domain.md)
- [公式与反事实矩阵](static-catalog-formula-model.md)
- [110 表覆盖清单](static-catalog-coverage.md)
