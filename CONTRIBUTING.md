# 参与开发

请先阅读 README、系统架构与相关模块的测试。问题报告应包含运行平台、Python/Node 版本、执行命令、预期结果和实际结果；用最小可公开夹具复现，避免上传客户材料或凭据。

修改核心时保持非目标行为，保留现有测试，运行与变更相关的检查。涉及页面显示时附实际页面证据；涉及动画时附对应播放验证，静态截图不能替代放映结果。

核心代码位于 `aippt-studio/src/aippt_studio/`，Node 执行脚本位于 `aippt-studio/node/`，跨平台启动和 Work 脚本位于根 `scripts/`。对共享实现的修改记录到 `aippt-studio/docs/CORE_CHANGELOG.md`。素材、模板和上游源码的许可不因提交到本仓库而改变。
