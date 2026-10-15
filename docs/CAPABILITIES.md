# 能力范围

AI PPT Studio 提供一组本地 CLI 工作流。能力是否可用取决于输入文件、宿主平台和外部工具；命令存在不代表外部服务已配置、功能已在当前机器运行，或 PPT 已获得用户验收。

## 源码中可见的命令能力

- **PPTX 文件操作**：`pptx-new` 创建空白 16:9 PPTX；`pptx-inspect` 输出页面、对象 ID 和几何信息；`pptx-save-as` 复制已保存文件；文本、图片、对象修订及 revision 流程提供更细的编辑路径。
- **内容规划和审阅**：`presentation-intent`、`design-plan`、`quality-check`、`expression-review`、`output-preflight` 等命令分别处理演示目标、设计计划、质量规则、页面表达审阅和交付预检。
- **制作工作流**：`new`、`ingest`、`ingest-text`、`inspect-source`、`run`、`review`、`approve`、`revise`、`export` 等命令串联 Studio job 的输入、生成、审阅与导出流程。此流程使用 job 存储目录，公开源码包不自带用户 Work。
- **模板与布局**：提供模板检索、审阅、确认、布局搜索、适配计划和构建命令。它们需要外部模板资产；公开仓库不含私有 `模板库/`。
- **素材与上游工具**：素材需求、获取/导入和上游调用命令提供来源记录与适配入口。它们不意味着素材、上游源码、服务账户或凭据随仓库提供。
- **诊断与辅助**：`doctor`、`runtime-select`、`upstreams` 和 `upstream-tools` 用于查看运行时或上游工具信息。诊断报告反映当次机器状态，不是所有平台都具备同一能力的承诺。

完整、当前的参数以安装版本的帮助输出为准：

```sh
aippt --help
aippt <命令> --help
```

## 外部能力和边界

Node 渲染依赖 Node.js 和锁定的 npm 包。HTML/OfficeCLI 预览、扩展图表渲染、视频处理、OfficeCLI 检查与修复、PowerPoint 原生保存/重开等路径分别要求其对应的本机工具。PowerPoint 与 OfficeCLI 不包含在本源码发布包中；公开仓库不会自动下载或安装它们。

`pptx-inspect` 的结构报告和程序生成的页面预览可用于检查文件，但本身不能证明 PowerPoint 原生显示、动画播放、视频保真或跨平台兼容性。静态图像也不能证明交互或动画效果。若任务需要原生应用行为，应在相应宿主应用和目标平台中独立验证。

`upstreams.lock.json` 记录外部来源、版本和接入信息。它不是依赖已下载、许可已覆盖所有使用场景或二进制可直接运行的证明。模板、字体、图像、图标及其他外部内容须由使用者分别确认权利和适用条件。
