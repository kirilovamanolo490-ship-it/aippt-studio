# Work 任务闭环与知识沉淀

> 2026-10-02 起，新制作 Work 默认采用 [精简存储](../WORKFLOW_STORAGE.md)：不保留本地对话副本，不生成重复复盘，闭环使用一份 production receipt。Studio、技能和库维护不创建 Work。下文原始归档和叙事复盘流程仅适用于旧格式 Work。


任务归档包括原始对话留档、工作证据和知识回流。生成文件不等于作品验收；只有用户明确同意任务结束并完成知识评估后，Work 才进入 `completed`。用户已另行授权归档触发的自动语义学习，有原句、范围和哈希依据的指导数据可在作品完成前激活；它不改变 Work 接受状态，也不证明脚本已经修复。新增自动流程与数据合同见 [对话归档与学习](../LEARNING_WORKFLOW.md)。修改技能自身时直接审计和修改源码，不启动 PPT 制作流程或套用其作品批准门禁。

## 状态顺序

```text
intake / in_progress
        ↓
构建、审阅、QA、交付准备完成
        ↓
prepare-work-close.ps1
        ↓
review（等待用户最终确认）
        ↓
用户明确同意结束
        ↓
独立任务复盘写入任务知识库
        ↓
评估本次已验证经验，写入知识结论
        ↓
finish-work.ps1
        ↓
completed
```

不得在用户确认前运行 `finish-work.ps1`，不得代替用户填写批准。

## 用户确认指令

以下是示例表达，不是必须记忆的固定口令：

```text
准备结项 Work <N>
确认 Work <N> 彻底完成并结项
继续修改 Work <N>：<原因>
```

- **准备结项**：运行 `prepare-work-close.ps1`，生成报告、输出哈希和知识候选并进入 `review`，但不结束任务。
- **确认结项**：只在 Work 已处于 `review` 且用户看过当前交付后有效。代理记录用户原话、复核交付物和证据未变化、在根级 `任务知识库/` 写入完整任务复盘、完成通用知识提升或明确 `no-new-knowledge`，再运行 `finish-work.ps1`。
- **继续修改**：运行 `reopen-work.ps1` 返回 `in_progress`；旧报告保留但标记失效，知识候选必须重新评估。

用户明确要求当前已审阅 Work 完成或归档就是授权，记录原话后执行，不重复确认。简短回答应根据此前问题和当前语境理解，不能只靠关键词拒绝。只有无法确认任务身份或是否要求结束时才澄清。多个 Work 同时存在也不自动需要提问；先利用当前对话的明确绑定判断。

明确要求结束当前已审阅 Work 的“归档”可授权完成；点击 Codex 侧栏 Archive 则只是对话存储状态，不能据此完成作品。两者都不移动、不压缩、不删除 Work 目录。侧栏归档可以触发已有授权范围内的语义学习。

## 准备结束

PPT 任务在交付物、逐页视觉检查、结构 QA 和桌面 PowerPoint 往返完成后运行；技能或核心修改应使用其实际代码、测试和审阅证据，不伪造 PPT 制作步骤：

```sh
./scripts/pwsh.sh -NoProfile -File scripts/prepare-work-close.ps1 -WorkId <N> -Summary "本次完成内容"
```

它会生成：

- `workspace/completion-report.md`：需求、输入、流程、审阅、QA、交付和结束门禁。
- `output/工作完成报告.md`：用户可直接查看的工作报告。
- `workspace/qa/final-output-manifest.json`：最终输出大小与 SHA-256。
- `workspace/qa/closure-evidence.json`：已绑定对话的原始快照与反馈事件、当前上下文的哈希。已绑定归档失败时先修复；未绑定的历史 Work 明确显示 unbound，不冒充完整保存。
- `workspace/retrospective-draft.md`：自动汇集证据供当前助手写独立叙事复盘；不是已完成复盘。
- `workspace/review-history/`：重新准备审阅或 reopen 前的旧报告、清单和知识候选。
- Adapter 证据：任务内 `adapter-request.json`、`adapter-last-result.json`、`native-adapter-result.json` 和 `external-engine-manifest.json` 会进入完成报告与最终清单的 Adapter evidence 段。
- `workspace/knowledge-candidate.md`：等待判断是否提升为 Studio 知识。
- `work.json.status = review`，`user_approved = false`。
- `work.json.required_confirmation_phrase` 为旧字段兼容保留，报告只把它当作示例，不要求用户使用固定用词。

## 知识评估

完整任务复盘不再放在 Work 内。每个完成任务必须建立：

```text
任务知识库/entries/<年份>/work-<N>-<slug>.md
```

并更新 `任务知识库/INDEX.md`。条目记录任务目标、输入语义、用户反馈与修改、失败与恢复、最终验证、可复用经验和证据位置。Work 中的 `knowledge-candidate.md` 记录本次结项知识判断。归档语义学习的回执和激活数据保存在 `任务知识库/evolution/`，不需要重复伪造一条 `passed` 事件，也不能拿它替代正式任务复盘。

条目在 review 阶段使用 `- 状态：review`；finish 同步改为 completed。新上下文将已确认经验、未完成案例、作品集参考分开；未完成案例只作导航。新建的 closure_schema_version=2 Work 还会检查正式复盘的关键章节，不能只写一行“均已记录”。自动验证章节存在不等于已理解其内容，当前助手仍要负责语义审阅。

根据完成报告、worklog、用户反馈、版本差异、QA 和失败证据填写 `knowledge-candidate.md`。区分：

1. 可跨任务复用的新决策。
2. 新发现的失败模式、工具边界或兼容性问题。
3. 只属于本客户、本素材或本页面的事实，不得提升为通用规则。
4. 没有新知识时，明确记录“无需提升”及原因，不制造经验。

当前助手在有明确改动任务、证据和检查时，人工提升知识的位置：

- 通用生产决策：`ppt-production-knowledge-base.md`。
- 会改变具体执行的规则：对应专项 reference。
- 证据来源：`evidence-index.md`，指向 `work/<N>/workspace/completion-report.md`。
- 核心代码或架构变化：`aippt-studio/docs/CORE_CHANGELOG.md`。

只提升有文件、报告、渲染、差异或用户反馈直接支持的知识；不要写入客户隐私、任务路径或未经证实的偏好。

后台语义学习不直接改这些 Markdown 或执行生成代码。它把符合原句和范围约束的指导写入数据，供 authoring defaults 与任务上下文自动读取；当前用户要求优先。脚本问题只进入待复现、待代码验证的 repair candidates。旧有 completed + user_approved + passed + 有效证据链继续决定 `generated/lessons.json` 的已验证经验资格，不能用这条旧门禁阻止已授权的归档指导学习。

## 正式结束

用户明确同意结束且知识处理完成后运行：

```sh
./scripts/pwsh.sh -NoProfile -File scripts/finish-work.ps1 \
  -WorkId <N> \
  -UserApproved \
  -ApprovalNote "用户的明确完成或归档原话" \
  -KnowledgeStatus promoted \
  -KnowledgeFiles "aippt-studio/docs/CORE_CHANGELOG.md"
```

若没有新的通用知识，使用 `-KnowledgeStatus no-new-knowledge`，且不传 `KnowledgeFiles`。脚本会拒绝未评估的知识候选、缺失闭环证据、没有用户确认，或缺少独立任务知识条目的结束请求。

`finish-work.ps1` 复核输出文件集合、输入、Adapter、asset、已捕获对话快照和反馈事件哈希。新的后台对话快照可以追加，已经审阅的快照不得变化。完成后自动刷新本机对话和知识索引；若后台刷新失败，记录可重试错误，已保存的用户确认不丢失。

点击 Codex 侧栏归档时，本机后台任务补齐明确绑定对话的原始证据，并按已有授权启动增量语义学习；它不把 Work 自动标为客户已验收。完整原始快照留在 Work 本地，选定用户消息和相关历史反馈通过已经登录的 Codex CLI 做语义复盘，使用当前账户用量；无需新 API key 或本地模型。CLI 使用 `--ephemeral --sandbox read-only`，这不代表离线运行。

后台默认每 15 分钟及登录时触发，语义复盘默认每日最多 4 批、每批最多 64 条新用户消息，可在 `任务知识库/evolution/config.json` 配置。原句、正文及记录哈希和接受回执须可核验；跨批反馈池支持发现重复问题，`needs_context` 等到新增上下文后再审。失败和损坏数据应隔离并报告，保留可恢复记录及基础指导，不把失败记成已学会，也不在无变化时反复打扰用户。完成后的已验证经验生成仍独立运行。

真实语义学习已完成原始用户消息复盘与新任务默认指导的消费验证；系统续跑注入过滤、旧缓存迁移和子进程系统代理已修复。不要把一次运行、后台注册或候选生成扩大为全量历史都已理解。当前覆盖和后续实跑证据以 [对话归档与学习](../LEARNING_WORKFLOW.md) 和本地运行回执为准。

若用户要求继续修改：

review 和 completed 均可 reopen。保存旧审阅并撤回任务复盘的完成状态，避免过时结论继续被当作已确认经验。

```sh
./scripts/pwsh.sh -NoProfile -File scripts/reopen-work.ps1 \
  -WorkId <N> \
  -Reason "用户要求继续修改的准确摘要"
```
