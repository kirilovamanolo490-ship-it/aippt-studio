# 对话归档、反馈学习与案例检索

> 2026-10-02 起，新制作 Work 默认采用 [精简存储](WORKFLOW_STORAGE.md)：不保留本地对话副本，不生成重复复盘，闭环使用一份 production receipt。Studio、技能和库维护不创建 Work。下文原始归档和叙事复盘流程仅适用于旧格式 Work。


这条链路由当前对话助手理解需求，Studio 保存证据、执行工具与检索，本机后台进程负责增量补齐和已授权的归档语义复盘。语义复盘使用已经登录的 Codex CLI，会使用当前账户的模型用量；不要求新 API key，不部署本地模型或下载权重。技能本身是入口说明；审计或修改技能不需要启动 PPT 制作流程或套用其作品批准门禁。

## 从哪里开始

macOS 在工作区根目录运行：

```sh
./scripts/new-work.sh -Name '客户任务名称' -Request '本轮完整要求'
./scripts/work-studio.sh 37 doctor --json
./scripts/work-studio.sh 37 doctor --deep --json
./scripts/work-studio.sh 37 context --query '按原模板修改四页，保留前两页' --work-dir work/37 --output work/37/workspace/assistant-context.json --json
```

替换示例中的编号。普通 `doctor` 只是能力清单；`doctor --deep` 才实际执行生成、渲染和 PowerPoint 检查。它会在当前 Work 保存回执；跳过或不可用的环节不能当作已验证。无需在每次无关文档修改时重复运行深度检查。

新 Work 的 `workspace/runtime-environment.json` 记录经过验证、适合当前主机的 managed/core 依赖。切换机器或选择失效时，通过 Work 执行 `runtime-select --work-dir work/37 --json` 刷新。所有自定义脚本必须通过统一入口：

```sh
python3 scripts/with-studio-runtime.py --work-dir work/37 -- node work/37/workspace/build.mjs '<参数>'
python3 scripts/with-studio-runtime.py --work-dir work/37 -- python work/37/workspace/check.py '<参数>'
```

Windows 使用本机可用的 Python 启动同一包装器。脚本放当前 Work 的 `workspace` 或共享脚本目录。入口保持参数边界并使用已验证的本机库，不借历史 Work 的 `node_modules`、虚拟环境或其他平台二进制。`runtime-environment.json` 缺失时先运行 `runtime-select`，不能绕过包装器临时拼依赖路径。

所有文档化的共享归档、索引、语义学习、核心演进和 closure evidence 命令也会在参数解析后检查**当前** Python 是否与物理主机匹配。Apple Silicon 上的 Rosetta x64、历史 Work/runtime 或无法可信识别的 Darwin x64 解释器不能写 Work 证据、共享索引或学习状态；`--help` 始终保留给恢复路径。macOS 的硬件能力探测、AppleScript 和 launchd 控制分别固定使用 `/usr/sbin/sysctl`、`/usr/bin/osascript` 和 `/bin/launchctl`，不从继承 `PATH` 解析同名程序。

新任务自动读取当前 `CODEX_THREAD_ID`，把这次任务与对话明确关联。旧任务不按标题猜测绑定；使用确切的任务 ID：

分配器同时参考现存数字目录、`_cancelled` 中曾用过的编号和 `.work-sequence.json` 高水位，并为新 Work 保存 `work_uuid`。取消或另行归档的编号不再分配给新项目；历史曾复用编号的任务要核对实际内容，不能只按数字合并对话。

```sh
./scripts/work-studio.sh 37 conversation-sync --work-dir work/37 --thread-id '<实际 Codex task UUID>' --json
```

没有 Codex 本地记录时，创建任务仍可成功，`startup-diagnostics.json` 会说明缺失。只要创建时取得了确切的任务 ID，便先保存到 `workspace/pending-conversation-binding.json`；即使 Python 暂不可用也保留这份绑定意图。后续维护会自动重试，绑定成功才移除待办。已知绑定仍失败时，准备结项会报告原因，不能假装已经归档。显式 JSONL 导入及历史候选发现见 `python3 scripts/archive-work-conversation.py --help`；该脚本只委托 Studio 的受保护对话入口，任何会写归档的 `bind`、`archive`、`refresh` 或 `refresh-tree` 都要求 host-native Python。原始会话绑定与工作状态是两套记录。

## 证据和知识的分层

| 数据 | 位置 | 自动化处理 | 使用边界 |
| --- | --- | --- | --- |
| 可获得的原始对话 | Work 的 `workspace/conversations/snapshots/` | 原字节快照、SHA-256、追加关系、归档状态 | 全量原始快照存本地；不是云端全量或附件备份；语义复盘会选取用户正文送到现有 Codex |
| 可读对话、工具失败索引 | `workspace/conversations/derived/` | 从原始记录重新生成 | 原始 JSONL 是事实依据；未知事件仍保留 |
| 明确反馈与修正 | `workspace/learning-events.jsonl` | 带作用范围和验证哈希的追加事件 | 助手负责解释反馈，不能编造根因 |
| 自动证据草稿 | `workspace/retrospective-draft.md` | 汇集来源、反馈、修正、验证 | 不是正式叙事复盘或作品验收 |
| 正式任务复盘 | `任务知识库/entries/` 和 `INDEX.md` | 由当前助手根据证据整理，finish 更新状态 | 未完成案例可导航，但不作为已确认经验 |
| 案例和作品集目录 | `任务知识库/catalog/case-index.json` | 增量提取文本、页码、对象类型、比例、文件哈希 | OOXML 检索不等于看懂版式或视觉认可 |
| 已完成任务的验证经验 | `任务知识库/generated/lessons.json` | 从用户确认完成、`passed` 且证据未变的事件生成 | 证明范围以具体验证为准；不代替语义指导或代码修复 |
| 归档语义复盘及激活指导 | `任务知识库/evolution/runs/`、`state.json`、`active-guidance.json` | Codex 逐条分类，检查原句、哈希及适用范围后激活数据 | 不要求 Work 已完成；自动进入 authoring defaults/context；不改变作品接受状态 |
| 核心修复队列 | `任务知识库/evolution/repair-candidates.json`、`core/` | 原始证据核验、代码诊断、持久化处理状态、隔离验证与应用 | 只有真实回归及独立审查通过的补丁进入共享核心；项目问题与视觉验收另行判断 |
| 后台运行状态 | `任务知识库/runtime/` | 每次状态、变化、错误和独占锁 | 不向客户发送消息，不自动发布成品 |

### Work 1–61 的一次性对话保留例外

在本轮用户明确授权下，对既有 Work 1–61 执行了一次有界清理。46 个 Work（含仅有 pending 绑定的 Work 41、42）写有 `workspace/conversations/retention-policy.json`，字段为 `schema_version: 1`、`mode: metadata_only`。Work-local 对话快照及 derived 对话文件已删除；Work 61 的 `workspace/conversation-purge-receipt.json` 记录 5,001 个文件、30,301,497,631 字节已删除，剩余快照文件为 0。原始 Codex 会话位于 `~/.codex`，删除脚本未触碰；轻量 `manifest.json` 和 bindings 留存为 provenance。

清理后，对 726 个唯一 source locator 做了复核：689 个原路径仍存在，另 37 个已移入 `archived_sessions` 且可匹配。清理时 6 条 active guidance 的 18 个 evidence reference 均通过原始记录字节与哈希核验；后台随后新增 1 条，最终 `load_guidance` 为 7 条、0 错误。此结果只证明这些活动指导引用有效，不代表所有历史对话均已分析或学会。由于 Work-local 原始字节已不存在，完整本地对话分析及 `project_repair` 相关复核受限；缺少证据时必须记录覆盖缺口，不能把 metadata、已有指导或归档状态当作完整复盘。该策略只描述本次获授权的历史清理，不是未来 Work 的自动删除政策。

后台维护虽然每 15 分钟检查一次，但仍在进行的对话不应每一轮都复制一份越来越大的全量 JSONL。维护对活跃、已绑定对话最多按一天一次保存新的完整原字节快照；首次绑定仍立即保存，达到间隔后继续保存，原始对话转为归档状态时立即补齐最后内容。未到间隔而来源已增长时，manifest 只写明 `deferred_active_snapshot`、来源文件元数据、上一份已保存快照和下次采集时间；这不是“新内容已归档”的证明。用户或助手显式调用 `conversation-sync`，以及准备结项和实际归档，仍立即抓取并验证当前原始字节，不受维护节流限制。已有快照与哈希不删改；Work 的逻辑文件体积可用 `work-storage --work-dir work/<N>` 只读查看，APFS 克隆导致的共享物理块不能由逻辑长度推断。

## 如何真正记住一次纠正

助手在反馈发生时写请求 JSON，然后调用 Work 的 `feedback` 命令。用户不需要填表或记住命令。例子：

```json
{
  "kind": "feedback",
  "trigger": "仅修改指定页面时",
  "user_quote": "前两页不要动",
  "problem": "修改扩散到已经认可的页面",
  "scope": "既有 PPT 的局部修订",
  "correction": "限定页码并比较非目标页前后渲染",
  "verification": "passed",
  "evidence": ["workspace/qa/non-target-comparison.json"],
  "reusable": true,
  "lesson": "局部修订应保留非目标页的前后对照证据"
}
```

```sh
./scripts/work-studio.sh 37 feedback --work-dir work/37 --request work/37/workspace/feedback.json --json
```

`kind` 为 `feedback`、`failure`、`decision` 或 `tool_use`；`verification` 为 `passed`、`failed` 或 `unverified`。`passed` 必须有真实存在的 Work 内证据文件。相同事件不会重复写入；纠正旧判断时用 `supersedes` 指向旧事件 ID。完成的任务需先 reopen 才可新增这种 Work 反馈事件。只有本次审阅包含的事件、已完成并获得用户确认的 Work、仍然有效的验证证据，才会成为 `generated/lessons.json` 中的已验证经验。这个门禁不用于阻止下述已授权的归档语义指导。

常规 QA 文件改变后，旧的通过结论会失效，准备结项也会指出具体事件。对操作日志、完成报告这类会被结项流程继续追加的文本，记录事件时会保存当时字节的独立快照及原始位置；后续例行追加不会误伤已经核验的历史证据，快照本身仍须通过哈希校验。

## 归档触发的自动语义学习

用户已经授权：归档后自动回看对话、识别反复问题，并让有依据的指导进入下一次工作。此授权允许更新指导数据，不要求每条指导等到 Work `completed` 后再问一次。作品结束、客户验收、脚本修复仍需各自的实际证据。

后台对已绑定且已有归档快照的用户对话，以及可取得对话证据的 completed Work 做增量处理。它不只搜索“小字”“编号”等关键词：每个选入批次的用户事件都必须获得一次有原因的分类。处理合同为：

1. 从原始记录重建用户正文，去掉系统、工具和内部上下文包装；核验原始字节及正文 SHA-256。完整 JSONL 和工具记录继续保存在本地证据包。
2. 使用已有登录态运行 `codex exec --ephemeral --sandbox read-only`，把选定用户消息、相关历史反馈和当前基础指导交给语义复盘。无需新模型服务或密钥，会消耗现有 Codex 账户用量。`--ephemeral` 不代表离线推理或零数据传输。
3. 对本批每条用户事件区分反馈、项目指令、非反馈和 `needs_context`。跨批证据池提供此前已复核的反馈，用来发现独立 Work 重复出现的同一问题；待上下文事件在所属对话出现新上下文后重新审阅。缺失助手或作品上下文时保留不确定，不猜测用户含义。
4. 仅当原句支持明确的工作区方向，或来自独立 Work 的重复反馈支持共同经验时，生成有 `action`、`applies_when`、`exceptions` 和来源依据的指导。必须逐字引用真实用户原句，接受结果时重新核验原始记录；加载指导时再核验回执及来源。不能把客户本页要求、示例话术或旧风格直接变成全局规则。
5. 通过检查的指导数据自动进入 authoring defaults 和任务上下文。新用户指令优先；替代旧指导需明确记录 `supersedes` 并保留旧回执。涉及脚本缺陷先生成带原始证据的 repair candidate，再由独立核心更新阶段诊断、复现和验证。候选本身不等于修复，也不改 Work 的接受状态。

任务结项后的验证经验与归档语义指导可并存：前者回答“什么修正已由这次工作验证”，后者回答“以后遇到什么情境应怎样判断”。语义指导不以案例数量多为成功标准；要看原句依据、作用范围、下次任务是否实际读取，以及重复问题是否得到有证据的改正。

运行及恢复合同：配置在 `任务知识库/evolution/config.json`，默认 `enabled` 由当前授权配置启用，`max_runs_per_day` 为 4，`batch_events` 为 64，单次 CLI 超时默认 600 秒。每日预算按 UTC 日期计算；这是批次数量上限，不是 token 或费用上限。达到预算后留待后续处理，不把未读事件记为完成。失败批次应隔离并保留待处理信息，避免阻断其他完好对话；无变化的失败不应每 15 分钟重复请求模型。已接受回执的覆盖和指导投影应可重试且不重复写入。损坏规则或来源退出激活集合并报告，基础 authoring defaults 继续可用。

可用 `<Studio Python> -m aippt_studio.evolution --repo-root <workspace>` 单独运行已配置的语义学习；`--force` 用于明确重试失败批次或超过当日批次预算，不能当作每次维护的默认参数。`enabled: false` 停止后续语义调用，不删除本地原始快照或既有证据；普通本地归档和索引维护仍可继续。

实际验证：`codex_internal_context` 续跑/预算注入与浏览器环境块已分离，旧缓存自动迁移。真实 Codex 复盘已处理一批原始用户消息，关于“样稿按终稿真实页面制作，不用梗概代替”的条件化指导通过原文核验并进入新任务上下文和新job策略；依据不够的通用化建议保留未激活，项目修复候选未冒充已修复。最初失败、修正与接受回执都保留。脚本还补齐了macOS系统代理对子进程的继承，且保留用户当前配置的Codex模型。真实证据在 Work37 的 `workspace/qa/platform-evolution/` 和知识库 `evolution/runs/`。这证明本轮链路运行，不能把尚未语义审阅的其它消息说成全部已经理解。

## 归档后直接改善 Studio 核心

`maintenance.run_maintenance` 在语义复盘后调用 `core_evolution.run_core_evolution`。即使语义复盘当天额度已用完，也继续处理已有修复队列；原始归档、指导激活、代码更新和作品接受状态分别记录。

1. 从已接受语义回执读取候选，重新核验原始用户记录及原句。`repair_context` 按归档索引定位邻近助手回复和工具失败，限制文本长度、过滤敏感内容和图像数据，并标明缺失或截断。它们是诊断证据，不能成为新的执行指令。
2. 已登录 Codex 在 `aippt-studio/.maintenance/core-evolution/<run>/candidate/` 的核心副本中读取实际代码，判断是核心缺陷、项目修改、已有能力，还是缺少上下文。一次只修一个可以复现的核心问题；没有证据时如实记录，不编造成功。
3. 候选必须包含生产代码改动和新增回归测试。固定 `core_patch` 验证器先跑原有完整测试，再证明新测试在旧代码中实际失败，最后确认修改后的完整测试通过。测试在 macOS 沙箱运行，禁止网络、AppleEvents、写原始项目或改测试源。环境无法完成检查时保留结果，不降低门槛。
4. 第二次独立、只读的 Codex 审查核对实际差异、接入路径、回归意义和副作用。原有测试、依赖、权限、更新器与验收门禁不允许候选修改。通过后还要重新检查原文证据、代码哈希、文件权限和依赖身份。并发改动会推迟应用。
5. 验证通过的差异直接应用到共享 Studio 文件。逐文件原子替换、事务日志和回滚记录保留在隔离维护目录。`任务知识库/evolution/core/` 保存每个问题的稳定 ID、判断、验证和应用状态；重建候选索引不能重置这些结果。

这些是工作台功能，不是 Work37 专用脚本。普通制作任务继续使用 Work；归档不移动或删除项目。本次核心整改的技术记录在 `.maintenance/2026-09-15-archive-evolution/`，没有把“本次直接整改核心”改成全局取消 Work 的规则。

配置仍为 `任务知识库/evolution/config.json`。`core_updates.enabled` 单独控制代码阶段；`max_runs_per_day` 默认 1 次诊断/修复运行，每次最多 `candidate_batch` 个候选（默认 8），可包含提案和独立审查两次模型调用；`timeout_seconds` 默认 900。额度不是费用上限。这个阶段也使用当前登录账户及其模型选择。无变化不重复调用模型；失败保留现场，明确重试使用 `python -m aippt_studio.core_evolution --repo-root <workspace> --force`。该 standalone 命令会先验证 host-native Python，之后才可能读取队列、调用 Codex、写运行状态或应用补丁；`--help` 不受该 gate 阻断。该命令由维护助手操作，用户不必每次手动执行。

软件测试不能判断一页 PPT 是否适合真实演讲。无法从代码和证据确立的视觉问题会留为待补上下文，实际页面仍走已有 `expression-review`。是否已运行、已修复，要看 `core/runs/<run>/result.json`、隔离区 `validation.json` 和 `applied.json`，不能只根据服务已安装或模块存在得出结论。

## 归档和结束

用户明确说当前任务完成、结项或归档，已经构成该动作的授权；不要求念固定口令。若任务身份清楚，不再问一次。准备审阅时自动保存对话快照、旧审阅历史和证据清单。助手完成独立叙事复盘及知识评估后，执行原有 finish 参数合同并保存用户原话。

Codex 侧栏 Archive 只表示对话被收起。后台通过只读查询本机线程状态检测这件事，并继续补齐已绑定对话的原始记录。它不把作品变成“客户已经验收”。若需要把一条对话关联多个 Work，必须显式声明 shared conversation。历史缺失、截断、迁移后失效的路径都会记录覆盖限制，不能通过写一篇总结伪装补全。

旧任务不会被批量设为完成，也不会移动、压缩、删除。继续修改时，`reopen-work.ps1` 保存原审阅并把独立条目撤回未完成状态，防止继续引用过时结论。

正式复盘在 finish 完成时绑定实际文件哈希。`context`、`knowledge-recall` 及其 new-work 写入的 `knowledge-context.json` 使用同一双重门槛：当前最终项目复盘必须能核验原始用户的明确确认，且将要返回的旧复盘文本必须仍与批准哈希一致。`completed`、`user_approved`、目录状态和条目哈希只说明生命周期或存储字节，不能单独恢复“可复用经验”“失败教训”或历史叙事摘录；证据不足时只返回 Work/条目、哈希、信任状态和导航边界。尚无哈希的旧任务明确标为 `legacy_status_only`，表示沿用历史状态，不能冒充已经重新核验证据。

归档准备会收集 Work 内真实 `upstream-call` 回执、调用请求、声明产物，以及 native scene/prototype 的回执指针。`workspace/qa/tool-execution-summary.json` 是有明确省略数量的简短摘要，`tool-execution-detail.json` 保存完整调用记录；原始文件各自进入最终清单并在 finish 时重新核验。任务自定义目录中的调用也会收集。成功执行、工具结果需要修订、失败、重试、选入样稿、与交付文件字节相同分别记录，不能换算成工具成品采用率。没有注册回执的外部制作脚本仍须按自己的构建与采用记录核实，不能从零回执推断未使用工具。

`officecli validate` 的新回执绑定实际验证前后文件的 SHA-256 和字节数；验证期间文件变化或消失会失败。旧回执缺少这份绑定时明确保留为未记录，不能给历史记录补造文件身份，也不能将它作为同名新版文件已经通过的依据。

## 后台自动维护

当前环境没有可调用的 Codex 自动化管理工具，使用可撤销的 macOS 用户级 launchd 任务。默认每 15 分钟以及登录加载时执行一次；进程采用独占锁，无变化时只更新本地运行状态。

```sh
python3 scripts/knowledge-maintenance.py install
python3 scripts/knowledge-maintenance.py status
python3 scripts/knowledge-maintenance.py uninstall
./scripts/work-studio.sh 37 maintain --json
```

电脑关机时不会执行；离线不影响本地归档与检索，但现有 Codex 的语义复盘需要其服务可用，失败会留下可处理记录。下一次维护继续补齐能找到的新增本地记录。原始全量快照不上传；语义复盘会把选定用户正文和相关历史反馈发给已有 Codex，并使用当前账户用量。服务不下载模型权重，不向他人发送消息、不自动发布作品，不改写上游源码；核心候选代码只在隔离区经过固定测试和独立审查，符合门禁才应用到共享 Studio。错误及恢复写入 `runtime/attention.json`，语义运行回执保存在 `evolution/`；没有变化或可行动事项时保持安静，不逐次打扰用户。这不是桌面推送承诺。

launchd 使用 `--quiet` 运行时，原始归档和本地索引已经完成、但模型阶段受额度、重试窗口、旧任务绑定或连接问题影响而留下 `status: partial`，会把待办继续写入 `attention.json` 和 `last-worker-run.json`，并以退出码 0 结束，以便下一次定时运行继续推进。它表示一次可追溯的维护已落盘，不表示模型已经完成分析或修复；`status` 会显示 `health=needs_attention`。直接在终端运行同一命令仍以非零状态提示 partial，真正的 `error` 也始终失败退出。

首次后台启动可能触发 macOS 的“文稿文件夹”权限提示。仅注册 loaded 不等于已经运行成功：安装器为每次安装生成实例标识，`status` 的 `health=verified` 才表示当前服务已加载、来源匹配、最近一次后台执行成功且未超过时效。`last-worker-run.json` 单独保留后台回执，手动补跑不会覆盖它；历史执行成功也不会掩盖服务停止或回执过期。若系统日志显示 Documents 授权等待，需要用户处理系统提示；不应通过更换进程身份冒领权限，也无需把普通文稿授权升级为全磁盘权限。

跨平台维护命令为 `<Studio Python> -m aippt_studio.maintenance run --repo-root <workspace>`，Windows 可交由系统任务计划程序运行。本次安装器只针对 macOS。

## 新任务如何用上

### Work 语料分类与安全案例召回

`aippt_studio.work_corpus` 把本机 `work/<N>` 做成独立的**分类和证据索引**，而不是把历史客户任务变成可复制的模板库。它记录的只是物理 Work 身份、生命周期、输入类型计数、请求哈希、表达场景/修改方式/视觉系统的置信度、观察到的历史路线、QA 证据状态和验收资格。Work 名称、客户名称、`input/request.md` 正文、素材/源文件名、页面文字、讲稿、图片、路径和旧 job 名都不写入索引或召回结果。

为提高旧 Work 的类别准确性，读取器会在 `input/request.md` 存在且不超过 256 KiB 时只在内存中做一次有界分类，然后立即丢弃正文；索引只留下请求哈希、分类值和 `bounded_work_request_inference` 置信度。没有可读请求才退回 Work 名称推断。这个旧任务推断不能倒填新的模板保留合同、技术路线或设计决定。只有请求快照、brief、intake decision 和 deck plan 都通过同一 Work 的哈希校验时，才会显示 `hash_validated_studio_intent`。

召回的资格按证据分层：

- `verified_case`：当前最终项目复盘以可校验的原始用户确认语句为证据，且当前 Work 绑定 QA 仍逐字节有效；
- `accepted_lesson`：当前最终项目复盘以可校验的原始用户确认语句为证据，且批准复盘的当前哈希仍匹配；
- `completion_metadata_history`：`completed` / `user_approved` 等结项元数据只可人工导航，不能自动进入新任务；
- `not_reusable`：未确认、未完成或证据不足，不能召回。

新请求仍必须走自己的 `PresentationIntent`、输入角色和实际页面审阅。被召回的案例只提供“这是一类什么任务、有哪些证据边界”的提示，不能带入旧客户事实、资产、措辞、已接受对象或视觉方案。输出字节、QA 或批准复盘发生变化时，下次刷新会自动重新判定；过去 Work 中的 `export`、`publish`、`qa` 等脚本名仅形成静态重复流程信号，既不是运行命令、执行回执，也不是允许复制旧脚本的依据。

正常 `context` 创建和 `maintenance` 的本地索引阶段都会通过 `build_case_index` 刷新这个索引，使用原子写入 `任务知识库/catalog/work-corpus-index.json`，不会改写任意来源 Work。可以独立检查或排障：

```sh
<Studio Python> -m aippt_studio.work_corpus build --project-root <workspace>
<Studio Python> -m aippt_studio.work_corpus recall --project-root <workspace> --query '正式竞赛答辩，保留模板后重新组织'
```

`build` 选择正确性优先的完整紧凑元数据重扫，而不是复用可能已过期的 QA 缓存；它适合后台静默维护和新任务初始化。读取失败会使该索引/上下文显示 `partial`，保留已有来源 Work，不会猜测案例、更改归档状态或阻断其他合法维护步骤。

`assistant-context.json` 返回已确认经验、未完成案例、作品集匹配页、工具操作建议和 `user_authoring_defaults`。激活的语义指导直接进入 authoring defaults 和上下文，带原句回执、适用条件及例外，无需先命中某个案例关键词。请求改变时刷新上下文。助手应据此选择少量相关资料，查看实际页面，说明采用哪些元素、哪些不能迁移，再进入制作。工具推荐带当前本地来源、操作合同和运行限制；`upstream-call` 成功回执才能证明实际调用，素材绑定和成品检查另有记录。

案例检索兼容旧复盘的已知章节标题，解析规则更新后自动重建旧目录缓存。匹配依据包括任务主题、语义关系及原文中的反馈/验证历史；历史叙事摘录保持出处与适用边界，不升级为已验证通用经验。文件格式只有属于任务本身时才作为匹配依据，不能因各类 PPT 都导出 PDF、都读取过 Word，就把它们召回为 Word 排版案例。新项目的初始化会读取当前索引；旧项目已有的 `assistant-context.json` 是生成当时的快照，需求或知识更新后应通过正常 `context` 刷新。

常驻判断从听众、页面论点和证据开始。默认去掉没有信息作用的页角 `01/02/03/04`、空编号圆圈、附属装饰线和随意英文标签，保留真实数量、完整单位、明确需要的页码、章节与业务步骤。不能把“不要装饰编号”扩大成禁用所有数字。表达自审同时检查参考的元素关系和几何、图片与论点是否对应、证据实体及单位是否完整、比较依据是否成立，以及用户已改好的对象有没有被覆盖。实际看完整页面和前后页之后才记录 `expression-review`；自动字号、几何和编号提示不能替代这个判断。

这套机制保证记录可追溯、回流有条件、检索会执行。它不能保证模型永远不再出错，也不能用关键词索引替代视觉审阅。反复发生的问题应进一步变成具体检查或核心修复，并有回归验证。

## 按整个项目复盘，再进入下一份作品

`project_learning` 是项目级独立分析阶段，和通用偏好提取、代码缺陷修复分别运行。`prepare-work-close` 保存原始归档并持久化复盘请求；`finish-work` 完成本地证据与索引后唤醒已安装的 macOS 后台进程。后台每次先推进项目复盘，再推进其它学习阶段，各自有锁和每日模型调用预算。模型运行不再持有归档写入锁，结束任务不用等待模型长调用。本地刷新也不能清除尚未重试的模型错误。

准备请求在最终清单和 review 状态写入之后生成，绑定该次清单、审阅时间和闭环证据。finish 及后台只能把同一完成周期的已有请求同步到最新原始快照，保留真实 `requested_at`；重开、重新准备、不同完成周期不能借用旧优先级。旧请求仅在能从当前最终清单认证其闭环证据、嵌入请求和时间顺序时迁移；历史回填不会自动变成刚归档。这个优先级用于后续核心诊断排序，不改变原始结论的验证要求或任何作品验收状态。

正式案例可检索、条件经验已分析、代码修复已应用是三个不同进度。当天模型预算耗尽时，本地归档和案例索引仍能完成，深度项目复盘继续显示 pending，并在后续允许的后台周期推进。不能把 pending 计为已经汲取全部教训，也不能用 `generated/lessons.json` 中为零的已验证结构化事件，推断所有已完成任务案例不可用。

项目复盘实际读取按任务绑定的原始用户消息、可见助手答复、相关操作与验证结果，保留角色、时间、记录位置和原始字节哈希。内部推理、重复事件、传输包装、图像二进制及工具噪音不进入语义正文；无法读取或超限的部分明确记为未覆盖。长正文跨段续读，游标不是“已理解全部”的证明。各段的反馈、尝试与结果还要经过最终模型综合，才标记 `review_complete`。

### Studio 意图链如何进入归档

当 Work 通过 `workspace/aippt.sh` 或 `workspace/aippt.ps1` 初始化 Studio job 后，项目复盘会额外检查该 Work 内的 Studio 规划链，但不会把它混入原始对话：

- `input/request.md` 必须和 job 的 `work_request` 绑定同一个 Work、同一个 SHA256；其只读快照必须位于该 job 的规范哈希路径。原始 JSONL 对话字节不会被重写或替换。
- 只有同一绑定的 `planning/intake-decision.json`，以及其 `presentation_intent` 与 decision 完全一致的 `planning/deck-plan.json`，才会作为意图证据收集。deck plan 只投影 change mode、表达场景、保留合同和页面角色；页面正文、讲稿、素材和客户事实不作为归档学习输入。
- 请求快照、intake decision 和 deck plan 都是 `studio_*` 规划证据：它们可以和原始用户反馈一起说明“需求理解—决策—页面结构”哪里脱节，但不是用户原话、页面验收或通过的 QA。它们不能单独支撑条件经验、`user_accepted` 或 `artifact_verified`，更不能变成全局风格规则或自动修改核心代码的指令。
- 任一文件缺失、路径越界、哈希不符、绑定不属于本 Work，或 decision/plan 意图不一致时，复盘会保留简短 omission 记录并拒绝不可信内容。当前 `input/request.md` 已改变时，旧快照仅作为历史事实保留，不会伪装为当前需求。意图链变化会使该 Work 的项目复盘签名失效，并从新的证据链重新分段，而不是把旧页面计划的回执拼进新需求。

这条链让归档能够识别“做错了哪类任务、模板复排和局部修改是否被误判、选择的正式/竞赛/汇报表达是否真正进入计划”，但仍需原始反馈和实际页面复核来判断问题与结果。它不会因为某个项目的 request 写了偏好，就自动把偏好推广给所有项目。

### QA 与共享对话的证据边界

`workspace/qa`、`workspace/studio-jobs/*/qa` 和 expression-review JSON 会保留为项目历史，但 `status: passed` 本身不是可验证结果。只有 QA 文件带有受限的 `project_learning_provenance`，并且归档时重新验证以下内容，才可标记 `verification_passed=true` 并支持某个具体问题的 `artifact_verified`：同一 Work 和 Studio job、当前 request 快照、brief、intake decision、deck plan、每个输入源及被检查的 `.pptx` 输出的 SHA256。任何孤立文件、旧 job、请求或计划已变、源文件或输出已变、缺少字段或哈希不一致的 QA，都会保留为“历史 QA”，不能因文本里写了 passed 而提升结果级别。`work.json.work_id` 还必须等于物理 `work/<N>` 目录；缺失或复制错配时，Studio 绑定和 QA 证明都失败关闭，原始对话仍保持不变。这个经过筛选的 QA 清单（相对路径、内容哈希、验证状态和范围）进入项目复盘签名；新增或改变 QA 会使已完成复盘失效，并从原始对话和当前证据重新分段，而不是把旧回执继续追加。

正常 Studio 生产链会在满足条件时自动写入这份证明，而不是要求每个任务手工拼 JSON：

- `presentation-quality-*` 仅在检查当前 `build/deck.pptx` 或已推广的交付 PPTX、且审阅所读字节仍与输出哈希相同时附加证明。临时 staging 文件、prototype 和假渲染结果不会成为已验证结果。
- final `expression-review` 保持原有 `ready/needs_revision` 语义，并写入同目录 QA 摘要；只有整页审阅绑定的 `build/deck.pptx` 仍是当前输出时，摘要才为 passed。
- `export` 在交付目录原子替换完成后写 final-delivery QA 摘要。它要求当前表达审阅 ready、页面质量检查通过、Office 验证、证据追溯和 PowerPoint roundtrip 都通过；`--skip-powerpoint` 只产生候选交付，明确不能进入 `artifact_verified`。

归档读取器不会信任这些写入动作本身：每次读取都会再比较 Work、job、request、brief、decision、plan、源文件和输出字节。任何后续变化都会把此前 passed 的记录降回历史记录。

同一个 root thread 的不可变快照可以被多个 Work 引用，以保证原始归档不丢失。但当前存档没有逐条、哈希绑定的 Work 范围证明时，`shared_conversation=true` 的快照只保留为原始档案：项目复盘记录 `shared_thread_scope_unresolved` omission，不把其中任何文本发送进语义复盘、条件经验、召回或 `project_repair` 的核心诊断上下文。后续只有加入可验证的 Work 专属记录范围后，才可以在那个 Work 中重新审阅；不能靠模型猜测或复制片段划分归属。

产物包含：

- **问题与反复原因**：区分观察到的问题和推测的原因，标注原因置信度。
- **尝试过程**：改过什么、为何仍被否决、后来采取了什么措施。
- **结果级别**：`unresolved`、`assistant_reported`、`artifact_verified`、`user_accepted` 分开。助手自述修好不能变成验收；作品验证和用户接受各自必须有相应原始依据。
- **下一次如何表达**：每条经验给出适用情境、例外、具体做法和实际页面上的验证方法。局部客户风格不升格成全局模板，技术过程记录也不变成可执行指令。
- **证据与覆盖**：每个结论引用原始记录的真实连续原句；接收和召回时重新验证来源与回执。遗漏媒体意味着没有视觉验收，归档状态不等于客户认可。
- **确定性引用合同**：新项目复盘不要求模型转写 quote。主机先从每份已审阅来源文本生成含 `source_id`、字符范围、文本 SHA-256 和原文的 `fragment_id`；模型只可从 `citable_fragments` 选择 ID。主机据此恢复持久回执中的精确原文，并重新核对来源文本与不可变原始记录。模型可见的证据正文只来自这些原始来源片段；路径、哈希、QA 结果和 provenance 分类都不是可引用文本。未知、伪造或过期的 ID 会失败关闭，不能回退到手写来源或引用。
- **结果资格与迁移**：`user_accepted` 只能使用原始用户记录中包含完整肯定句及语境的 eligible fragment，随后仍要验证原始字节；`artifact_verified` 继续独立要求当前 Work 绑定的 QA provenance。schema v2 回执保存主机派生的 `{fragment_id, source_id, quote}` 和实际引用锚点；旧 schema v1 回执继续按其历史逐字 quote 合同重验，绝不被改写。对于旧 quote 合同而失败的排队项目，下一次正常复盘只会记录一次协议迁移并在既有有限预算内重新排队；失败 receipt、旧错误与来源 Work 保持不变，其他错误不借此重开。

最终综合还需要逐项处理分段中发现的问题：保留、合并到哪个问题，或为何排除。每个输入问题只能处置一次，不能用空问题列表加“全部完成”的总结越过覆盖检查。用户接受核对原始消息上下文，不能从否定句里截出“没问题”。旧综合缺少这份覆盖合同会重新进入综合阶段，已经验证的原始分段继续复用，旧回执保留。

请求、分段、综合、状态和可读复盘均保存在 `任务知识库/project_reviews/`，原始 JSONL 仍在原 Work。可读综合报告位于 `reports/work-<N>.md`，与完成验收的正式知识条目区分。普通 `context`、新任务初始化和 `create_deck_plan` 会实际调用有效条件经验；`expression-review` 要求针对当前页面记录采用或不适用的原因及观察结果。单纯检索命中不算实际采用。指导、采用记录和工具调试文本不会自动成为页面正文或讲稿。

完整复盘也通过 `project_repair` 进入现有 `core_evolution` 诊断队列。该入口重新核验综合、分段与全部引用，提供问题的完整尝试过程和其他问题概要；它不要求用户先点名每个工具故障，也不会把工具故障伪装成用户投诉。项目过程中的问题只是诊断候选，由现有核心阶段判断是页面修改、核心缺陷、已覆盖还是缺少上下文。只有核心缺陷能继续进入隔离代码修复、旧版失败/新版通过回归、独立审查和共享核心应用。新的综合证据会使相关判断重新排队；已经验证应用的改动保留其真实应用状态。损坏或过期的项目证据单独报错，不阻断其他合法候选。

配置复用 `任务知识库/evolution/config.json` 的 `project_reviews` 字段。使用现有 Codex 登录态和当前模型，不需要新 API 或本地推理模型。普通后台继续按整个持久队列推进；`project-review --work-dir work/<N> --json` 和 API 的 `run_project_learning(..., only_work_ids=["<N>"])` 则是严格单 Work 范围：不发现、初始化或模型审阅其他 pending Work，仍使用当前配置、日预算、退避和同一严格验证链。它不改变 Work 接受状态。连接失败有退避与有限重试，损坏项目不阻塞其他完整项目。后台关闭或预算耗尽时保留待处理状态，不虚报完成。

本机当前项目复盘预算是每日最多 6 次模型调用，单段最多 200,000 个可读字符、256 条记录，单次超时 600 秒；失败调用也计入预算。长项目会跨多个后台周期继续，最终综合同样占用调用预算。通常由 Work 本地原始快照提供逐字证据；上文 Work 1–61 的一次性 `metadata_only` 例外已删除这些 Work 内副本，相关完整项目复盘需如实标记来源受限。发送给现有 Codex 的是分段筛选后的可见对话、操作结果及必要证据，不包含内部推理和媒体二进制。当前整改的显式回归重跑单独记录，不修改日常预算。

新增 PPTX 语义读取直接复用锁定 `ppt-master` 的 OOXML reader，正常导入产生结构化 sidecar，案例索引同步读取图表分类/系列/缓存数值、表格合并和 SmartArt 关系。原文缓存进入检索，因此不是只新增一个未消费的 JSON 字段。公式不自动重算，缓存缺失或数量不完整会标明 partial；上游不可用时可见降级，恢复后缓存自动失效重读。像素差异工具及采用记录的具体格式见 `docs/PRESENTATION_QUALITY.md`。
