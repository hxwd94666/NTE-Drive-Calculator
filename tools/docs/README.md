# 功能与实现地图生成

维护入口是 `docs/architecture-map.md`；目标设计是 `docs/roadmap/sync-lifecycle.md`；项目声明直接读取根目录 `RESPONSIBLE_USE.md`。HTML 只作阅读快照，不作为新的协议事实源。

正文面向人类解释按钮动作、配置生效时点、影响范围、限制与完成条件。每节“证据：”段落自动折叠，供维护者按需查看。默认白色，不跟随系统主题；顶部“阅读设置”可切换白色／黑色，并尝试在浏览器本地保存。该设置不修改 Calc 配置。

## 多级目录与内容节点

主菜单仍由地图中的二级标题组织；独立深层入口使用带稳定身份的三级标题。例如：

```markdown
### 逐击日志 <!-- page: battle-hits; type: 弹窗 -->

这里写本级进入路径、操作和限制。

### 逐击详情与公式 <!-- page: battle-hit; type: 弹窗; parent: battle-hits -->

这里写更深一级的说明。
```

未指定 parent 时属于所在主菜单；指定 parent 后可以继续嵌套，不限制为两级或三级。每个节点必须保留自身说明，不把父目录做成空文件夹。页面类型区分真正的子页面、弹窗、抽屉、详情区和条件入口，不能让文档目录暗示 Calc 新增了菜单。

点击名称只阅读本级，箭头独立展开／收起；面包屑和本级子目录可直接导航。搜索自动展开匹配项及祖先，清空后恢复原展开状态。稳定页面 ID 用于深链接，改展示标题不应改 ID。浏览器前进／后退保留阅读路径。

生成器检查重复 ID、缺失父级和循环依赖，并与 `src/ui/main_window/navigation.py` 的所有正式导航项核对覆盖和父级。新增真实页面时同步维护 `NAV_PANELS` 和说明；未列入正式导航的弹窗、动态详情等仍需人工核对，不能宣称仅检查导航元数据就已穷举全部控件。

在 Calc 仓库运行：

```powershell
python tools/docs/build_architecture_map.py --audit output/architecture-review-20260927/upstream-review.md --output output/architecture-map/index.html
```

需要工作台、计算、配装和设置的图文说明时，先用项目 Python 环境生成真实 Qt 控件的离线示意图，再显式包含图集：

```powershell
.venv/Scripts/python.exe tools/docs/render_ui_reference.py
python tools/docs/build_architecture_map.py --audit output/architecture-review-20260927/upstream-review.md --ui-references output/architecture-map/ui/manifest.json --output output/architecture-map/index.html
```

渲染脚本仅实例化页面控件，使用演示状态及空回调，不构造应用组合根、不读取账号数据库、不运行采集或游戏操作，也不执行项目测试。图片为白色主题；当前局部图不是完整应用截图。按钮坐标从真实控件位置取得，不手填猜测位置；图片记录版本、日期、基底提交与所加载项目源码哈希。生成器拒绝源码变化后的旧图或没有对应文字说明的按钮标记。图片内嵌 HTML，离线无需额外服务器；生成图集仅保留在 output。

审计文件由当前任务提供；更换核对批次时更新参数。生成器不会 fetch、修改 Git 引用、编译、执行项目测试或部署。运行前另行核对远端，不能以生成时间冒充远端检查时间。

默认读取相邻 `nte-dps-toolkit` 和 `UETools-NTE` 工作区；本机位置改变时需同步跨仓导航链接和 root 参数。两个 Core 在同一私库中，但在 HTML 中分别展示。

Markdown 支持标题、段落、项目列表、表格、代码块、行内代码和链接。所有本地链接都必须存在且位于批准的三个工作区内，缺失时生成失败，不用虚假链接替代。链接生成时计算文件哈希；点击后展示位置与身份，不把私有源码正文嵌入页面。模板为 `architecture_map_template.html`。

生成 HTML 和 `.evidence.json` 含本机源码路径及未提交状态，只放被忽略的 output 目录。本地可直接打开 HTML；对外提供前应按分享范围生成不含本机路径和内部证据的版本，不能直接上传整个 output。

VMP 清单从 UETools 当前配置和已经拉取的 origin/main 生成；版本基线与产物声明分别展示。生成成功不代表保护回执或游戏内性能已经验证。三个项目原有测试、编译和发布流程不由本工具触发。
