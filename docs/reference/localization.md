# 界面本地化

*[English](../en/reference/localization.md) · 简体中文*

界面语言由 `src/i18n` 提供，语言偏好与主题一起保存在 `config/global_ui_preferences.json`。
当前支持 `zh_CN`（源语言）与 `en`。

## 两类文本，两套机制

界面里的中文分成两类，必须区别处理：

| 类别 | 例子 | 机制 | 原因 |
| --- | --- | --- | --- |
| 界面文案 | `保存`、`工作台`、`检查更新` | `tr("...")` | 纯展示文本，可整句替换 |
| 游戏术语 | `攻击力%`、`环合强度`、`「失落光芒」` | `display_term("...")` | 同时是 OCR 匹配值和静态库查询键 |

游戏术语的中文**不允许**就地翻译。它们会与游戏客户端的 OCR 结果比对，并作为
`data/game_static.sqlite3` 的查询键；改写会同时破坏扫描、解析、评分与配装。
`display_term` 只替换展示名，键本身保持不变。

## 文案目录

- `locales/en.json`：界面文案，**以中文源串作为键**。
- `locales/glossary.en.json`：游戏术语显示名，按 `stats`/`elements`/… 分节。

以源串作键意味着缺失的翻译会回落到原中文，而不是回落到键名，因此目录可以逐步补全，
未覆盖的界面仍然可用。源语言 `zh_CN` 不需要目录。

`glossary.en.json` 的英文取自游戏自带字符串表，通过 `name_text_key` 与 `attribute_id`
与静态库关联，`_meta.provenance` 记录每一档来源；修改时只改英文值，不要动中文键。

## 生效时机

语言与主题一样在**下次启动**生效：两者都会改变每个控件的构建结果，不做实时重绘。
`src/ui/app.py` 在导入任何界面模块之前调用 `set_language()`，模块级的 `tr()`
才能取到正确目录；新增模块级文案时必须保持这一顺序。

该调用发生在**导入期**，因此仅仅导入 `src.ui.app` 就会激活本机偏好文件里的语言。
测试断言写的是源语言字符串，所以 `tools/quality/run_tests.py` 会设置
`NTE_UI_LANGUAGE=zh_CN`；`app.py` 优先读取该环境变量，测试结果不再受本机偏好影响。

## nte-core 自带的多语言名称

nte-core 的库存条目同时带 `names`/`suit_names`（`en`/`ja`/`zh_cn`）与稳定
`property_id`，因此装备名、套装名和词条名**不需要**词表：`display_localized(names, 中文兜底)`
按当前语言直接取游戏数据自己的名称，取不到才回落到 `display_term`。

`vision` 来源的行只有中文，回落路径因此必须保留。注意 `_localized()` 返回的仍是中文，
它是 `item_type_id` 等筛选键的来源，不要改成按语言取值。

## 渲染期才替换显示名

`display_term` 只在**即将写入控件**时调用，权重查询、评分、排序、别名归一化一律继续使用中文键。
现有接入点：`_equip_card` 的套装名/主词条/副词条、`AttributeSummaryPanel` 的属性行、
`_display_bonus_stat_label`、角色名与套装名。

一个容易踩的坑：中文键自带百分号（`攻击力%`），英文显示名不带（`ATK Bonus`）。
因此**百分号后缀必须从中文键推导**，不能判断显示名里有没有 `%`：

```python
main_key = str(main_stat)
main_text = display_term(main_key)
percent_suffix = "%" if "%" in main_key else ""   # 取自键，不是显示名
```

`_format_panel_value` 用 `bonus_uses_percent(stat)` 判断，本身就作用于键，不受影响。

## 游戏长文本

套装效果、觉醒效果和弧盘技能说明属于游戏原文，不进 `en.json`。静态库为它们保存了
字符串表键（`description_text_key` 等），运行时用 `display_text(text_table, text_key, 中文兜底)`
查 `locales/gametext.<lang>.json`。

该文件由 `tools/game_data/build_game_text_locale.py` 从 locres 导出生成；只提交生成结果，
locres 导出本身不入库。`fork_star_level` 没有键列，按 `upgradestar_pack_X` → `buff_X_effect`
的命名约定推导；键统一小写存取，避免静态库与字符串表的大小写差异漏查。

英文原文保留同样的 `{n}` 占位符，因此精炼数值替换在两种语言下都成立。

战报中的**技能名不做翻译**，直接显示 nte-core 上报的原文。nte-core 自带的技能名表本身
就是中英混杂的（部分英文、部分中文、部分是 `GA_Shinku_Melee` 这样的原始 id）；本地再维护
一份映射意味着每次游戏更新角色都要重新导出 locres，成本不划算。技能**分类**（`E技能`、
`普攻` 等）是固定的小集合，仍走 `tr()`。


## 单复数

中文不区分单复数，英文区分。需要时在目录里补一条**兄弟键**，键名指明由哪个字段决定：

```json
"{count} 个驱动":              "{count} Modules",
"{count} 个驱动::one::count":  "{count} Module"
```

`tr()` 只在该字段等于 1 时改用兄弟键。字段名必须写明：同一句里常有第二个整数
（快照号、任务号），按“任意整数等于 1”判断会误判。

目录仍是扁平的字符串字典，`load_catalog` 过滤掉兄弟键，`load_plurals` 单独在
加载期拆出来，因此 `tr()` 仍是一次字典查找。这只覆盖英文的 one/other，
不是完整的 CLDR 复数规则。

## 新增文案

1. 在代码中用 `tr("中文源串")` 包裹，f-string 改为 `tr("...{name}...", name=value)`；
2. 把该中文源串作为键补进 `locales/en.json`；
3. 游戏术语改用 `display_term`，不要进入 `en.json`。

`locales/` 属于发行只读资源，由 `src.integrations.bundled_resources.bundled_locales_dir`
定位，并在 `build_exe.py` 中随包分发。

## 相关测试

`tests/test_i18n.py` 固定回落行为、术语映射、目录完整性，以及主题与语言共用一个
偏好文件时互不覆盖。
