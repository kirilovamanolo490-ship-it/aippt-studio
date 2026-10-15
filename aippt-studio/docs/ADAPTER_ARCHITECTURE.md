# AI PPT Studio Adapter 架构与调用规范

更新时间：2026-09-01

2026-09-07 补充：上游已刷新为完整浅克隆，新增版本与干净状态检查，以及独立的 Mermaid / ECharts 资产生成入口。源码 HEAD 与 npm/Python 运行版本分别登记；新版 rebuild 状态与 finalized 交接已兼容。详细入口、当前版本、用途和接入边界以 [上游接入说明](UPSTREAM_INTEGRATION.md) 为准。

AI PPT Studio 是任务状态、审批、QA、交付和知识闭环的唯一所有者。`github/` 中的项目通过版本化进程 Adapter 提供专项能力，不使用软链接，也不把上游应用作为新的任务主系统。

## 调用链

```text
work/<N>/workspace/aippt.ps1
  -> input-role map
  -> route decision
  -> adapter registry
  -> native | template | rebuild | visual
  -> common manifest and QA
  -> final review
  -> Work closure
```

每个 Adapter 接收 `build/adapter-request.json`，只在 `build/adapter-staging/` 生成候选。Studio 验证路径、SHA-256、编辑性、页数和预览后才原子提升为 `build/deck.pptx`、`build/previews/`、`build/adapter-source/` 和 `external-engine-manifest.json`。失败不会覆盖已有有效候选。

## 四条生产路线

| 路线 | 引擎 | 自动能力 | 明确边界 |
| --- | --- | --- | --- |
| `native` | dom-to-pptx 2.0.3 | HTML/CSS 到可编辑 PPTX、预览、OfficeCLI、motion | 默认路线；完整原生构建仍受公共 QA 保护 |
| `template` | pptx-automizer 0.8.2 | 克隆模板页、继承 master/layout/theme、替换 `{{title}}`、`{{subtitle}}`、`{{body}}` | 未审阅动画默认阻断；复杂对象使用显式 template map 或手工上游 fallback |
| `rebuild` | image-to-editable-ppt/editppt | 准备和接续截图、PDF、image-only PPT 的重建运行；自动接收 finalized PPTX 与 manifest | 页面重建需要图像/OCR 能力时进入明确的 pending 状态；cloud-disabled 不启动远程提供者 |
| `visual` | codex-ppt | 把逐页批准的全页图像组装为 PPTX | 始终标记 `image_based`，不能写入 editable-master 输出键 |

## 输入角色与路由

Studio 支持六种输入角色：`content`、`template`、`edit_target`、`visual_reference`、`flattened_input`、`asset`。

```powershell
# 查看或重新计算路线
work\<N>\workspace\aippt.ps1 route <job-id> --json

# 指定模板来源并强制模板路线
work\<N>\workspace\aippt.ps1 route <job-id> `
  --role <source-id>=template `
  --set template `
  --reason "用户要求继承该模板" `
  --json

# 显式接受 image-based alternate
work\<N>\workspace\aippt.ps1 route <job-id> `
  --role <source-id>=visual_reference `
  --accept-image-based `
  --json
```

用户覆盖始终优先。路线变化会撤销受影响的 prototype/final 批准并清除旧路线候选，避免陈旧产物继续交付。

## 能力成熟度

`doctor --json` 不再把目录存在等同于可用。Adapter 的成熟度依次为：

```text
unavailable
installed_only
executable
version_reviewed
acceptance_tested
automatic_ready
```

只有 `automatic_ready` 可以自动执行。成熟度由上游锁定提交、Studio Adapter 命令和对应 `adapter-acceptance/*.json` 共同决定。

```powershell
work\<N>\workspace\aippt.ps1 doctor --json
work\<N>\workspace\aippt.ps1 assets --json
work\<N>\workspace\aippt.ps1 strategy <job-id> --json
```

## 许可证与密钥

- 资产注册表从 `upstreams.lock.json` 读取来源、许可证和本地状态。
- `NOASSERTION`、未知许可证和 pending snapshot 不允许自动进入构建。
- API key、token 和 bearer secret 不进入请求、命令回执、结果、manifest、Work 报告或 PowerPoint 备注。
- 外部命令结果在读取后立即递归脱敏并重写，再进入合同校验。
- 当前锁定的 pptx-automizer 0.8.2 存在上游传递依赖告警；Template Adapter 在调用前拒绝路径穿越、ZIP 符号链接和超过 2 GiB 的展开内容，并受 300 秒子进程超时保护。升级到 0.9.x 必须先更新上游锁并重跑模板验收。

## 验收与回退

每个上游 Adapter 至少需要成功 fixture 和失败 fixture。自动执行失败时，Studio 写入 `adapter-last-result.json` 和原有 `<route>-handoff.json`，保留 `manual_upstream_adapter` 作为显式 fallback；不会静默换路线。

公共门禁仍包括：来源证据、备注清洁、输出哈希、母版/版式审计、OfficeCLI、逐页预览、PowerPoint 打开/保存/重开/导出，以及 Work 用户确认与知识处理。
