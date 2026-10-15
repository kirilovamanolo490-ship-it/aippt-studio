# 架构概览

AI PPT Studio 是一个本地 PPT 制作与审阅工作台。Python CLI 负责任务流程、内容和结构检查、素材/模板流程及文件操作；Node.js 程序承担部分 HTML 到 PPTX 的渲染、预览和视觉处理。PowerPoint、OfficeCLI、浏览器及其他上游工具属于宿主或可选外部能力，不随公开源码仓库提供。

## 仓库布局

公开仓库保留 `aippt-studio/` 子目录和仓库根级 `scripts/` 布局：

| 路径 | 职责 |
| --- | --- |
| `aippt-studio/src/aippt_studio/` | Python 包、CLI、工作流、PPTX 处理、规划、审阅、素材与模板接口。 |
| `aippt-studio/node/` | Node 渲染、场景、预览、媒体检查和视觉差异程序。 |
| `aippt-studio/tests/` | Python 自动化测试。 |
| `aippt-studio/scripts/` | 产品专用安装、测试、适配器和 PowerPoint 辅助脚本。 |
| `aippt-studio/integrations/` | 可移植 PowerPoint 技能及其说明、合同和 schema。 |
| `aippt-studio/docs/` | 架构、安装、能力和第三方组件说明。 |
| 仓库根 `scripts/` | 跨平台启动、运行时装载及 Studio 脚本执行辅助程序。 |

Python 包配置位于 `aippt-studio/pyproject.toml`，命令入口为 `aippt`，对应 `aippt_studio.cli:app`。Node 渲染依赖和入口位于 `aippt-studio/package.json` 与 `aippt-studio/node/`。扩展工具有独立的 `aippt-studio/extensions/package.json`。

## 文件与运行时边界

代码仓库不包含用户的 Work、客户输入、生成结果或私有模板库。任务目录由调用方选择；程序可通过 `AIPPT_JOBS_DIR` 指定 Studio job 存储位置，未设置时当前配置会使用仓库根目录旁的 `jobs/`。上游目录可由 `AIPPT_UPSTREAM_DIR` 指定，默认位置为仓库根目录旁的 `github/`。`.tools/` 是本机工具的可选放置位置，不是随源码分发的二进制目录。

模板和布局检索命令当前按仓库根目录旁的 `模板库/` 查找资产。公开仓库不附带该私有库，因此这些命令需要用户另行提供有使用权的素材；源码克隆成功不表示模板库已就绪。`upstreams.lock.json` 是上游来源和版本记录，不是上游代码、二进制或服务已经安装的证明。

Python、Node、浏览器、PowerPoint、OfficeCLI、扩展渲染器及媒体处理工具是不同运行时或外部能力。`doctor` 可报告当前宿主能力；具体命令仍需检查其所依赖的工具是否可用。Windows/macOS 的原生 PowerPoint 操作使用各自平台脚本，不能从静态预览推断 PowerPoint 放映或原生显示已通过验证。
