# 测试维护

本目录按产品功能组织跨层行为验证，不按一次修复的批次或日期命名。根目录只保留包入口和本说明；
测试、夹具和检查工具各有归属，不再通过把 `tests/` 加到模块搜索路径来裸导入文件。

## 归属

| 目录 | 验证范围 |
| --- | --- |
| `accounts/`、`storage/` | 账号隔离、导入导出、事务、数据库迁移与失败回滚 |
| `app/`、`preferences/`、`work_modes/` | 应用启动、退出、页面任务、设置与模式授权 |
| `characters/` | 角色配置和权重；`official/` 为正式角色面板、养成及家具 |
| `allocation/` | 分配输入、合法性、求解、直伤/战报后处理与冻结保存 |
| `equipment/` | 配装与汇总；`assembly/` 为视觉装配，`apply/` 为原生执行及后验确认 |
| `teams/` | 队伍与槽位；`matching/`、`overview/`、`graduation/`、`enhancement/` 分别拥有对应流程 |
| `inventory/`、`warehouse/` | 库存浏览/同步与快照；仓库规则、建议、锁定/弃置草稿及派发 |
| `native_session/`、`capture/`、`components/`、`plugins/` | 共享原生会话、抓包、组件身份部署与插件；`components/analysis/` 独立分析协议，`components/capture/` 采集组件协议 |
| `battle_report/` | 按采集、持久化、分析、构筑、Buff、重放等既有子流程划分 |
| `cultivation/`、`rewind/` | 养成规划与历史、倒带推荐与执行 |
| `scanning/`、`identification/`、`vision/` | 扫描、手动/图片鉴定与视觉识别 |
| `game_data/`、`static_catalog/` | 静态构建/晋升/游戏素材与运行时只读图鉴 |
| `ui/`、`localization/` | 跨功能控件与窗口行为；语言目录统一契约及实际译文行为 |
| `observability/`、`performance/`、`quality/` | 日志与性能观察、代码质量和测试入口自身 |
| `release/`、`updates/` | 构建发行、免安装数据根、更新与回退 |
| `support/`、`fixtures/` | 无业务归属的 Qt 生命周期/主题帮助；跨语言和多流程共用的有限合成证据 |

新增测试优先加入已有领域文件。单一流程过大再拆子包；不建立 `misc`、修复批次包或旧路径兼容壳。
只有夹具与帮助函数的模块不以 `test_` 命名；独有业务夹具留在所属领域，跨领域基础设施才进入 `support/`。
使用 `from tests.<领域>.<模块> import ...`；pytest 夹具若通过导入注册，显式写同名别名，避免被当成未使用导入清理。

## 保留与精简

- 保留公开行为、计算口径、来源/未知语义、冻结身份、账号隔离、迁移、取消、权限、事务回滚和后验确认。
- UI 保留可见文字完整、键盘可操作、长译文、窗口限位、草稿保护和 owner 收尾；不把颜色、字号、装饰对象名、
  精确边距或中心像素固定为长期契约。布局变化若遮挡必要内容或阻断操作，仍属行为缺陷。
- 同一全局契约只维护一份，例如语言目录参数和旧补丁回退；页面测试只保留该页面真实交互的特有证据。
- 布局、可达性和长译文用例只在默认主题 `black` 与最窄有效尺寸下运行；断言本身依赖主题或尺寸差异时才增加参数。
  三主题外观仍属发布前真实 Windows 验收，不在自动化中按主题复制同一断言。
- 删除前说明被其他公共行为覆盖或已无消费者的依据，检查导入、动态 patch、夹具文件、维护脚本和文档引用；
  不因测试慢、需要受控组件或当前失败就删掉。孤立夹具随最后消费者一起清理。
- 不在测试模块重复定义同名用例；后定义会静默覆盖前定义。测试夹具只释放本例创建的资源，不全局关窗。

## 执行

遵循仓库契约：只有用户授权的范围才执行；静态核对、收集、专项执行、core/full 和真实 Windows 验收分别记录。

```powershell
python tools/quality/run_tests.py full --collect-only
python tools/quality/run_tests.py core --pattern test_native_furniture_sync.py --jobs 1
python tools/quality/run_tests.py core
python tools/quality/run_tests.py full
```

`--pattern` 递归匹配文件名，目录迁移不改变原有专项调用；`core/full` 的分层和整模块分片仍由
`tools/quality/run_tests.py` 冻结并选择。不使用历史命令的旧路径来维持第二套测试入口。
新建、升级和回退的模拟证据不替代游戏、安装器或不同主题/DPI 的实际验收。
