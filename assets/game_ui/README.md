# 轻量游戏界面资源

本目录只包含新界面实际使用或近期会使用的小尺寸图片，不是完整的官方文件目录。每个生成文件的来源、尺寸和 SHA-256 都记录在 `manifest.json`。

## 官方 `Content` 相对路径

构建时将 `../Content` 作为 `Content` 根目录；下面路径都相对于该根目录：

| 资源类别 | 输出目录 | `Content` 中的来源路径 | 映射键 |
| --- | --- | --- | --- |
| 角色头像 | `characters/` | `UI/UI_Icon/AvatarImage/256/<asset>.png` | 官方 `character_id` |
| 属性图标 | `attributes/` | `UI/UI_Icon/Attribute/<asset>.png` | 稳定属性键 |
| 空幕（core） | `equipment/core/` | `UI/UI_Icon/kongmu/256/<asset>.png` | 官方 core `item_id` |
| 驱动（drive/module） | `equipment/module/` | `UI/UI/Equip/fangkuai/<asset>.png` | 官方 module `item_id` |
| 弧盘（arc/fork） | `forks/` | `UI/UI_Icon/Fork/<asset>.png` | 官方 `fork_id` |
| 怪物头像 | `monsters/<static_table>/` | `UI/UI/MonsterManual/MonsterHeadIcon/200x200/<asset>.png` | `static_table:monster_id` |

驱动、弧盘的官方图片路径来自随程序发布的静态数据库：`equipment_item.icon_path` 和 `fork_item.icon_path`。怪物图标路径来自官方怪物表：

- `DataTable/Monster/DT_MonsterStaticData_BigWorld.json`
- `DataTable/Monster/DT_MonsterStaticData_BigWorld_Gameplay.json`
- `DataTable/Monster/DT_MonsterStaticData_BigWorld_Quest.json`
- `DataTable/Monster/DT_MonsterStaticData_Clone.json`
- `DataTable/Monster/DT_MonsterStaticData_Abyss.json`

仅导入这些表中具有官方 `icon.AssetPathName` 的怪物；缺少图标路径的怪物不会猜测文件名。

## 生成方式

可以直接传入公开的相对路径：

```powershell
python tools/game_assets/build_ui_assets.py `
  --content-root ../Content `
  --static-database data/game_static.sqlite3
```

开发者本机的绝对路径统一保存在仓库外的环境配置中；加载配置后也可以显式传给脚本：

```powershell
python tools/game_assets/build_ui_assets.py `
  --config $env:NTE_LOCAL_CONFIG
```

配置使用普通 JSON，不依赖 PowerShell 脚本执行策略。命令行参数优先于环境变量，
环境变量优先于配置文件。`--manifest` 和 `--output` 同样支持外部路径。省略参数时，脚本依次读取
`NTE_OFFICIAL_CONTENT_ROOT`、`NTE_GAME_STATIC_DB`、
`NTE_UI_ASSET_MANIFEST` 和 `NTE_UI_ASSET_OUTPUT`。

构建器会压缩 PNG：角色头像最长边为 256 像素，属性图标为 96 像素，其余图标为 128 像素。不要复制完整官方文件目录、临时导出文件或未列入清单的素材到本目录。
