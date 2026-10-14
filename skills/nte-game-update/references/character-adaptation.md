# 角色与功能适配矩阵

对新增、删除、变更角色与共用机制，按正式 ID 记录「来源 → 当前消费者 → 本次接入 → 证据 → 缺口」。
每个受影响功能选一个当前状态：`待核对`、`数据已就绪`、`接入待改`、`候选已验证`、`组件待支持`、`不适用`；
配明原因和下一入口，状态不代表正式晋升或实机通过。角色未实装、来源未知与模型未覆盖分别表达，不合并为“已适配”。

以下路径以仓库为根；先读 `docs/architecture.md`，重构迁移后通过正式 import 定位，别按历史包名另造兼容层。

| 功能 | 核对与接入决定 | 当前正式入口 |
| --- | --- | --- |
| 身份/角色档案 | NPC、未实装、特殊形态与逻辑角色；正式 character_id/Actor/实例关联。变身不重复建普通档案；删除目录行不删除账号历史 | `tools/game_data/catalog_characters.py`、`tools/game_data/character_overrides.json`；`src/services/official_role/{page,profiles,native_profiles}.py` |
| 面板/养成 | 等级/突破、额外形状、好感、家具、具体觉醒、技能基础等级、弧盘养成；无条件与条件效果分开。技能觉醒加级不写入基础等级 | `src/domain/official_role/advancement.py`；`src/services/official_role/{detail_inputs,attributes,awakening,fork_templates}.py`；`src/services/progression/{character_requirements,fork_requirements,planner_service}.py` |
| 技能/新机制 | 正式 SkillID、GA/GE、倍率数组、属性乘区、持续/叠层与归属；描述不是机制证据。已有模型通用导入，新机制扩现有领域/适配器并列覆盖 | `tools/game_data/static_database_{character,combat,blueprint,buff}_imports.py`；`src/services/damage_calculation_service.py`；`src/services/battle_report/characters/` 与 `src/services/battle_report/replay/`；`docs/reference/counterfactual/` |
| 弧盘/装备 | 精炼各档、永久属性、条件效果、套装和形状，统一 property_id；先闭合常驻证据再称面板完整。图鉴 unavailable 保留缺口 | `tools/game_data/static_database_equipment_imports.py`、`tools/game_data/static_database_fork_progression_imports.py`；`src/domain/equipment/official_stats.py`、`src/domain/official_role/advancement.py` |
| 评分/配装 | 推荐权重与账号覆盖、基础分与替换直伤边际区分；缺正式模型仍用基础权重。合法图纸、UID/slot_id、锁、取消、冻结保存与历史评分保持 | `src/optimizer/scoring.py::ScoringEngine`；`src/services/official_role/{base_equipment_scoring,marginal_scoring,graduation}.py`；`src/services/equipment/blueprints.py`、`src/services/allocation/` |
| 鉴定/扫描资料 | 名称/词条/套装/类型别名、形状模板、场景布局；解析后关联正式 ID。新资料不自动支持新截图语言，识别输入与配置身份冻结 | `src/services/recognition/resources.py`；`src/integrations/vision/recognition/ocr/language_resources.py`；`src/integrations/vision/recognition/{shape_recognizer,parser}.py`；`src/domain/stat_catalog.py`、`config/stats.json` |
| 图鉴/材料工具 | 成本、材料链、经验/货币及副本产出。`role_page` 与 `reference` 覆盖有别，养成草稿不回写角色/背包，不伪造活力证据 | `src/services/static_catalog/`；`src/services/progression/`、`src/features/toolbox/cultivation/`；子进程归 `src/integrations/progression/solver_process.py` |
| 配装分享/技能图片 | 面板/逐件冻结分、正式槽位、角色/弧盘/全部可分享主动 SkillID 图片；显示别名不作素材身份。缺图或映射冲突在候选解决 | `src/services/equipment/loadout_share_service.py`；`src/integrations/game_assets/catalog.py::GameUiAssetCatalog`；`tools/game_data/build_role_skill_assets.py`；`src/services/official_role/skill_names.py` |
| 战报/分析组件 | 首击/结算冻结输入、技能归属、被动、Buff/环境证据与固定轴重放；未知不变零，新模型不改实测事实。分析目录存在不等于角色支持 | `src/services/battle_report/inputs/`、`src/services/battle_report/analysis/native_page.py`；`src/integrations/{analysis_core_release,nte_analysis_core}.py`；`docs/reference/battle-report-package.md` |
| 原生同步/装配 | 正式角色实例与装备映射、永久字段覆盖、能力/协议与新后验状态；角色导入不新增 RPC 能力。未返回字段保留旧值、未知结果不重发 | `src/services/official_role/native_profiles.py`、`src/services/inventory/sync/`；`src/services/equipment/{apply,bulk_postcheck}.py`；协议及组件归 `docs/integrations.md` |

## 怎样决定改动范围

- 现有来源结构/公共模型覆盖：只更新候选数据/素材，验证其真实消费者；不按中文角色名增加分支。
- 新关系/格式：在相应 importer、DAO 和公共投影补最小原因；不在 Controller 填 SQL/公式或跨 Feature 私有调用。
- 新机制：分清无条件面板、基础评分、替换边际与固定轴反事实，沿同一公式入口接入；正式数值、GE/曲线或可观测证据不足的项保留未知。
- 组件缺口：在所属集成仓库处理并消费带版本、许可、清单/哈希的包；Calc 不复制私有实现，不绕过正式分析接口。
  文件/进程、握手、声明能力与业务就绪分别核对，当前 engine/协议/页面能力从清单与 client 读取。
- UI 只加必要配置与缺口提示，遵循现有三主题/窗口规则，不借更新重做视觉体系。

## 最小验证与未覆盖边界

按本次实际受影响项选择证据，不把表中每行变成固定全量测试：同一冻结 dataset 下核对基础/临界突破、关键技能/觉醒、
弧盘精炼与条件分离，一套合法图纸/评分/槽位保存，相关识别资料及养成/分享素材；旧角色与旧存档沿适用公共行为回归。
数值差异写来源、公式项与预期；账号隔离、静态字节变化使旧预览失效、取消/事务等边界被触及时追加对应反例。
缺输入、未实装、仅图鉴和组件缺支持须有真实降级/未知结果，不阻断无关的已证实功能。
OCR/扫描、原生同步/装配、分析组件和真实 Windows/游戏表现按授权与环境单独验证；未运行保留未验收。

维护 skill 时只核对本轮迁移路径、真实 CLI 参数与必要合成流程。公式、schema 和协议细节留正式事实源，
重复操作确有收益才补脚本；仓库/个人副本同步状态按 `SKILL.md` 单独交付。
