# 精简的制作工作流

更新：2026-10-02，按用户本轮要求执行。

## 什么时候创建 Work

新做一份 PPT 时创建 Work，后续修改沿用。修改 Studio、技能、工作流或整理模板库时直接修改共享项目，不创建维护 Work。

新建 minimal Work 在运行环境探测前保存 `workspace/authoring-context.json`，指向当前共享技能和作者入口，并记录初始需求身份；`work.json` 记 `authoring_workflow_version: 1`。这份紧凑入口独立于聊天，不复制原始对话、整个模板索引或其他Work的选择。当前事实、受众、叙事和页设计在本Work的计划中重新决定，需求更新以后以当前请求及绑定为准。版本化新Work在制作前用 `design-plan` 记录助手规划；局部修改与忠实重建沿用简短路径。存量Work不因本次维护被自动迁移或失效。

## 制作包保留什么

```text
work/<N>/
  input/       提供的正文、数据、图片、参考模板
  workspace/   制作脚本、配置、Studio 状态和必要检查收据
  output/      成品 PPTX
```

新 Work 默认使用 `storage_profile=minimal`。不自动留存原始聊天、对话对照、重复复盘和审阅历史副本，不把报告混入成品目录。制作中的页面自审与按需原生检查分别记录。用户可以随时打断或调整制作；当用户认可当前正在看的图片并要求直接拿到 PPT 时，在下一个安全的文件边界停止排队中的无关生成、检查和归档，定位生成该图的准确可编辑 PPTX 并交付。若图片只对应一页，就说明只查看了该页；如果不能确认图片对应哪个 PPTX，不得猜测成稿。需要把现有文件放进 Work `output` 时，用 `pptx-save-as` 复制；若要更新已有同名成品，显式加 `--replace`，不制造多份可见版本。

代码和配置必须能说明读取哪些素材、如何生成 PPT、输出到哪里。修正写回持续使用的制作源，避免下次运行恢复旧错误。临时渲染和候选只用于检查；已被必要收据绑定的文件不能随意删除。

### 存储引用审计与保留预览

维护侧可以调用 `aippt_studio.storage_audit.plan_work_storage_retention(work_dir)` 生成逐文件 dry-run 清单，供 CLI 包装层接入。它返回 `kind=work_storage_retention_plan`、`status=dry_run_only`、每个文件的相对路径、逻辑字节、可用时的 SHA-256 与 `hash_status`、分类、状态、理由和 `referenced_by` 收据路径；SHA-256 计算受默认 64 MiB 总预算和 16 MiB 单文件上限约束，大文件或预算耗尽时会明确标记未计算/未验证，避免为审计读完整个 Work。收据记录的摘要单列在 `receipt_sha256` 中。函数没有删除或改写文件的实现。状态 `protected` 表示输入来源、输出、可执行代码或收据引用文件；`review_candidate` 只标记疑似可再生缓存供人工复核；`manual_review` 表示未知用途或出现解析/路径安全问题；没有任何自动删除状态。

计划读取 receipt、manifest、revision 和 external-engine JSON 中常见的 `path`、`relative_path`、`raw_path`、`file_path` 字段（仅当同一对象含有效 SHA-256 时作为文件引用），并保护这些引用。对预算内且需要校验的引用会比较当前文件摘要；摘要不符或因预算未能验证会列入 `unsafe_reasons`，但不会自动取消被引用文件的保护。收据解析失败、引用越出 Work/缺失目标、符号链接或文件元数据错误都会进入人工复核，并使疑似缓存候选降级为 `manual_review`。Work 内绝对路径会归一为相对路径；外部路径仍需人工复核。逻辑字节是文件长度，不等于 APFS 实际占用；mtime 不参与保留判断，旧备份不会因此自动变成可删项。

普通完整制作进入收尾准备时，一份 `workspace/production-receipt.json` 记录源文件、制作代码、成品和必要 QA 的哈希。准备阶段进入 `review`；只有用户明确同意完成，且收据与文件仍一致，才能结束。用户中途要求交付已经生成且与所看图片相对应的 PPTX，是交付当前文件，不自动表示整稿原生 PowerPoint 检查通过、用户接受未查看页面，或同意 `finish-work`/归档；这些状态继续分开处理。用户要求马上拿文件时，不得让与当前交付无关的收尾、额外测试、测试套件或归档步骤挡住交付。旧 Work 沿用兼容流程，不批量改变其接受状态。

## 直接操作 PPTX 文件

日常查看和文件准备优先使用 Studio 的本地命令，避免为简单操作启动并点击 PowerPoint：`pptx-inspect <source.pptx>` 读取结构和文字，`pptx-new <output.pptx>` 新建空白可编辑稿，`pptx-save-as <source.pptx> --output <copy.pptx>` 复制/另存到目标路径。`pptx-open <file.pptx>` 用于确实要在 Microsoft PowerPoint 中打开给用户看或执行原生检查的情况。制作中看页面通过 `export-png` 直接生成本地 PNG 预览，无须 PowerPoint 打开/保存/重开往返；近似预览与原生检查分别记录。

## 工具如何配合

先明确场合、听众、页面主张与证据，再找模板。竞赛和汇报按真实模板的内容页与相邻页判断；用户选定后，保留模板的字体层级、图文比例、图片轮廓和阅读顺序，再替换内容与素材。模板来源页和当前页之间要有实际对象映射。

PPT Master、OfficeCLI、图表工具与图片工具分别承担执行任务；Studio 负责模板选择、来源绑定、逐页内容计划、正式语言、实际画面检查和整稿一致性。单个工具生成成功不能证明画面达到参考质量。

核心维护脚本可以通过主机 Python 调用：

```sh
python3 scripts/with-studio-runtime.py --maintenance -- python <共享脚本> <参数>
```

该入口使用当前验证的运行环境，探测文件在临时目录中，不分配 Work。生产任务仍使用 Work wrapper。

## 54–62 的处理原则

模板库、源代码与有效共享能力继续使用。仅保留必要的小型校验收据和参数化的制作方法；删除指定的 9 个 Work、重复参考稿、缓存和暂存停用仓库。历史试稿没有自动成为已接受模板；结构索引也没有升级为全库视觉学习。
