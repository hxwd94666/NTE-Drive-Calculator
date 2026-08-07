# 角色、基础权重与图纸

## 模块定位

管理官方角色数据、账号养成配置、弧盘、面板、直伤边际、账号基础权重和角色图纸。

## 当前能力

- 从发行静态库读取角色、成长、技能、弧盘和推荐值；
- 在账号库保存等级、突破、觉醒、技能选择和弧盘指针；
- 角色页面板与直伤边际计算；
- 账号基础权重读取、编辑和恢复推荐；
- 额外形状共享覆盖；
- 角色图纸生成和账号切换后重新加载。

## 数据边界

- 官方事实和推荐值属于发行静态库；
- 养成指针和基础权重属于账号库；
- 额外形状差异属于本机共享库；
- 角色页动态最终权重只读，不写数据库；
- 角色页“保存”不覆盖账号基础权重。

## 当前限制

- 当前综合收益主要是直伤边际；
- nte-core 尚未提供稳定的角色等级、觉醒、武器和精炼快照；
- 战报聚合画像、DOT 和具体环合加权边际属于 2.1.0 后续工作。

## 验证

主要覆盖 official-role、character-weight、shape-bonus 和 blueprint 测试。

## 主要实现

`src/features/official_role/`、`src/features/configuration/`、`src/features/blueprints/`、
`src/services/official_role_*`、`src/services/character_weight_service.py`。
