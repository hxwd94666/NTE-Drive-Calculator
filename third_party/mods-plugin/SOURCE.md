# nte-mods-plugin 对应源码

本仓库随附的 `bin/dwmapi.dll` 和 `workspace/` 来自
`kongbaiz/nte-dps-toolkit`：

- 组件版本：`0.3.8`
- 源码：<https://github.com/kongbaiz/nte-dps-toolkit>
- 发布标签：[`v0.3.8-build-98-efe9473`](https://github.com/kongbaiz/nte-dps-toolkit/releases/tag/v0.3.8-build-98-efe9473)
- 发布源码提交：[`efe94737b5b7bd5ed796b97ca6161e6e53b34d77`](https://github.com/kongbaiz/nte-dps-toolkit/commit/efe94737b5b7bd5ed796b97ca6161e6e53b34d77)
- 本地构建源码提交：[`9b88da5ae5182e0a18ddc7ce69ba6594df61e3a2`](https://github.com/kongbaiz/nte-dps-toolkit/commit/9b88da5ae5182e0a18ddc7ce69ba6594df61e3a2)，插件相关目录与发布提交一致
- 本地补丁：`src/offset_resolver.cpp` 增加正式服映像 `0x1066A000` 的已验证偏移 profile；
  偏移来自本机运行映像的唯一签名与结构匹配结果
- 构建工程：`native/nte-mods-plugin/nte-mods-plugin.sln`，`Release|x64`
- 许可证：本目录的 `LICENSE`（AGPL-3.0）

二进制和来源压缩包的 SHA-256 见 `COMPONENT.md`。
