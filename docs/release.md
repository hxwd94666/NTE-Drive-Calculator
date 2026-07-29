# 手工发布说明

项目不使用 `.github/workflows/release.yml`。发布由维护者在本地完成验证和构建后，
使用 `gh` 明确确认并创建；本地工具不会创建标签、推送、创建 Release 或上传 Mirror。

## 1. 准备环境

首选使用 `uv`，并安装运行、构建和开发依赖：

```powershell
uv sync --group build --group dev
```

Inno Setup、ViGEmBus 安装程序和仓库外开发路径可继续使用
`build_installer.py` 已支持的命令行参数、环境变量或本地 JSON 配置。

## 2. 本地发布前检查

先把版本写入唯一版本源 `src/app/version.py`，提交全部预期改动，确保工作区干净，
然后执行：

```powershell
uv run --group build python tools/release/prepare_release.py
```

该命令依次执行完整测试、编译检查、静态数据库检查、随包组件与许可证检查、应用和
安装包构建，并在安装包旁生成 `.sha256` 文件。工坊权重同步默认是发行构建的硬性要求。

开发期间可以使用 `--allow-dirty` 检查未提交改动，或使用 `--skip-tests`、
`--skip-build` 调试脚本；这些参数不应替代正式发布检查。

## 3. 人工发布

本地工具通过后会输出带实际版本和产物路径的命令。维护者核对后手工执行：

```powershell
git tag <version>
git push origin <version>
gh release create <version> <installer> <installer.sha256> --title <version> --notes-file <notes>
```

若同名标签已经存在，必须先判断这是版本号未递增还是尚未发布的本地标签；不要覆盖已经
对外发布的标签。Mirror 的文件分发、下载和更新风险由 Mirror 发布链负责。
