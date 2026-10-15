# 上游项目调用与任务参与

Studio 负责需求、任务状态、证据、审批和交付。上游项目通过明确的工具或原有 Adapter 执行，源码树只读。源码、运行依赖、候选产物是三个不同对象；获取最新源码不会自动升级正在使用的 npm 运行包。

## 当前活跃清单（2026-10-02，Work 62）

`github/` 现保留 15 个活跃检出，12 个已更新、3 个远端无变化；来源提交见 `upstreams.lock.json`。PPT Master 为 6.6.0。OfficeCLI 源码已更新，当前主机二进制实测为 1.0.152，本轮未替换该二进制；各 npm / Python 包继续使用经验证的独立锁。

PPTAgent、Presenton、Motion Canvas、Remotion、Remotion Bits、smiley-sans、academic-pptx-skill 和 awesome-ai-ppt 的完整源码已移出活跃目录，并在 Work 62 保留恢复清单。尚未获取的 source-han-sans 条目一并退役。下文保留这些可选接口的历史合同，但 `lifecycle: retired` 项不会出现在 `upstream-tools`，不能执行或被资产注册器当作可用字体。需要恢复时按 lock 中 `retirement.restore_guidance` 操作。

制作采用[模板优先工作流](TEMPLATE_FIRST_WORKFLOW.md)。保留源码、可调用能力、当前任务成功执行与实际视觉采用分别记录。

## 目录、选型和真实执行是不同的事实

`github/<provider>` 存在、`upstreams --check` 通过、策划阶段选中了某份设计资料，均**不**表示该 provider 已为本次 PPT 运行。Studio 将这几层事实分开保存，归档或下一次任务只能引用对应层级的证据：

| 层级 | 记录位置 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| 源码/目录 | `upstreams.lock.json`、`upstreams --check` | 检出来源、提交和干净状态可供评估 | 当前任务调用过该项目，或运行了哪个包 |
| 资料检索/策划选型 | `planning/upstream-context.json` | 哪些参考被读取或推荐；其中 `execution.executed_operations` 初始为空 | 参考的渲染器、编辑器或服务已执行 |
| 工具实际调用 | `workspace/.../receipt.json` | 某次受限操作成功，输入、产物和运行身份已记录 | 画面质量、页面采用、交付或用户接受 |
| Adapter 构建 | `build/native-adapter-result.json`，或 `build/adapter-last-result.json` 加 `build/external-engine-manifest.json` | 本次构建的引擎、bridge/runtime 身份、输入/产物哈希 | 视觉审阅或最终采用 |
| QA、交付、接受 | 各自的 QA、输出清单和 Work 状态 | 相应的检查、交付关联或用户确认 | 其他层级未记录的事实 |

工具目录的 `execution_profile` 始终带 `executed: false`，它只说明**如果未来调用**会使用哪类路径。真正成功后，调用收据才将 `execution.executed` 写为 `true`；`read` / `search` 即使成功，也只标为 `source_resource_read`，从不冒充渲染或编辑引擎的执行。旧 receipt 没有这个字段时会被标记为 `legacy_unclassified`，需要逐份检查，不能倒推为已调用。

下表说明常见运行身份与最低证明。它是对运行契约的分类，不是“所有这些项目已经被当前任务使用过”的清单。

| 运行身份 | 代表 provider | 实际运行内容 | 最低证明 |
| --- | --- | --- | --- |
| `managed_package_runtime` | `dom-to-pptx`、`PptxGenJS`、`pptx-automizer` | Studio 锁定的 npm 包；GitHub checkout 只供源码审查/版本溯源 | 带 package-lock/bridge 身份的 native 或 adapter result |
| `direct_checked_out_source_adapter` | `codex-ppt-skill`、`image-to-editable-ppt-skill` | 锁定 Git checkout 中的指定 CLI；执行前后均要求 checkout 干净且 HEAD 一致 | adapter receipt 和前后源码完整性记录 |
| `direct_checked_out_source_tool` | `ppt-master` 的受限读取、SVG、native-motion 模块 | 仅注册的受限模块，不是整个仓库的所有脚本 | source-semantics provenance 或 Work-local receipt |
| `managed_host_binary` / `managed_isolated_runtime` | `officecli`；`echarts`、`mermaid-cli`、`mediabunny` | 主机受管二进制或 Studio 隔离运行包；checkout 仅作来源记录 | 绑定输入/运行锁/产物哈希的成功 receipt |
| `configured_work_copy_runtime` / `configured_external_service` | `pptagent` / `presenton` | 只有配置齐全后才会启动的隔离运行时或服务 | 成功的 MCP/service receipt；配置或目录本身不算执行 |
| `reference_only` | `awesome-ai-ppt`、`remotion`、`remotion-bits`、`motion-canvas`、许可证冲突的 `academic-pptx-skill` | 资料、发现或视频参考 | 没有 Studio PPTX 执行证明；不能作为自动路由依据 |

模板路线尤其需要区分 bridge 与引擎：目前真正运行的是 Studio 锁定的 `pptx-automizer@0.8.2`；`template_adapter.py` 的 bridge 版本独立记录。成功的模板 receipt 同时保存 runtime package、package-lock、Node runner 和 source-bound template map 的身份，因此不会把桥接脚本版本或 GitHub 目录误报成实际引擎版本。

## 正式流程

1. 用 Work wrapper 创建和导入任务。策划时自动生成 `planning/upstream-context.json`，检索适合当前任务的 ppt-master 风格资源与管理层表达参考。完整内容留在任务内；`deck-plan.json` 保存其来源和哈希。
2. 调度者读设计资料，根据本次材料决定叙事、版式、字体和图形；资源检索本身不等于设计已经应用。现有确定性规划器仍是初稿生成器，不是完整的审美模型。
3. 用 `upstream-tools --json` 查询操作，用 `upstream-call` 执行。每次使用新的 Work 输出目录，保留 `request.json`、`receipt.json`、日志和产物。成功执行、质量检查通过、被页面采用、完成交付分别判断。
4. 图标/图表/流程图通过 `asset-need` 注册用途，再用 `asset-import JOB --need NEED --receipt PATH` 验证并绑定。它记录真实集合许可证、哈希、调用证据并撤销过期 prototype/final 审批。离线素材回接不需要联网许可。
5. 新作原生 PPT 可保持默认 dom-to-pptx，或用 `native-scene` 显式选择 PptxGenJS。模板、重建、视觉替代仍走原 Adapter；不悄悄换路由。
6. 用真实 PowerPoint 预览和 OfficeCLI 检查，再执行既有审阅/交付门禁。调用成功不能替代视觉检查。

所有示例都通过当前 Work 的 wrapper 调用，例如 Mac：

```sh
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 upstream-tools --json
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 upstream-call iconify-icon-sets icon --request work/17/workspace/icon-request.json --output work/17/workspace/calls/icon-001 --json
```

`--request` 是 JSON 文件；`--output` 必须是当前 Work workspace 内尚不存在的目录。失败会保留失败 receipt，不覆盖旧调用。新任务使用自己的 Work 编号。

## 操作契约

| Provider / operation | JSON 请求 | 结果与边界 |
|---|---|---|
| 除 Iconify 外的 provider / `search` | `{"query":"README","limit":20}` | 搜索 Git 跟踪的文件名，不是全文语义检索；最多返回 100 项 |
| iconify-icon-sets / `search` | `{"query":"flower","limit":12}`，可加 `"collection":"lucide"` | 在锁定的本地图标集中搜索**具体图标键**，返回 `集合:图标`、作者、该集合的独立许可证、资源路径与来源提交；不联网、不自动采用或下载，名称检索以英文键为主 |
| 所有活跃 provider / `read` | `{"path":"README.md"}` | 读取不超过 256 KB 的具体文本资源；含来源版本/文件哈希；不执行资源中的指令 |
| iconify-icon-sets / `icon` | `{"name":"lucide:workflow","color":"#187C79"}` | SVG、嵌入 HTML、集合级 license/attribution；暂不解析带变换的 aliases |
| lxgw-wenkai-gb / `font` | `{"path":"fonts/TTF/LXGWWenKaiGB-Regular.ttf"}` | 字体二进制与 OFL；未全局安装，目标 PowerPoint 字体仍需验证 |
| smiley-sans / `font` | 指向真实字体二进制的 `path` | 当前源码只有字形资源；不能把这些资源当作已安装字体 |
| echarts / `chart` | `{"source":"/absolute/task/chart.json"}` | SVG 图形资产；原生可编辑图表应选 PptxGenJS |
| mermaid-cli / `diagram` | `{"source":"/absolute/task/diagram.mmd"}` | SVG，默认 flowchart 使用文本标签，支持素材验证与自包含嵌入 |
| PptxGenJS / `author` | 下述 scene JSON | 原生 PPTX 候选与 leadership 布局检查；警告显示 `review_required` |
| ppt-master / `svg-deck` | `{"project":"/absolute/Work/workspace/svg-project"}` | 项目包含 `svg_output/`；复制到调用目录，运行归属检查、canonical final quality gate、native SVG export；不伪造质量报告、不使用绕过开关 |
| GSAP / `timeline` | `{"ease":"power2.inOut","duration":1.2,"samples":7}` | 调用真实 GSAP 求出时间/进度；不是 PPTX 对象动画导出 |
| markitdown / `convert` | `{"source":"/absolute/document.pptx"}` | 使用当前源码和 Studio Python 依赖做本地转 Markdown，未配置 LLM/外部插件 |
| officecli / `validate` | `{"source":"/absolute/candidate.pptx"}` | 主机 OfficeCLI 结构验证；不会代替逐页视觉检查 |
| pptagent / `mcp-session` | `{"job":"JOB","steps":[{"name":"list_templates"}]}` | 在隔离运行副本中调用有状态 MCP；模型/运行环境未配置时拒绝执行 |
| presenton / `generate` | `{"job":"JOB","content":"明确提供的内容","template":"general"}` | 配置服务后生成候选；遵守 `cloud_allowed`，不隐式上传任务文件；仍需 Studio 审阅 |

ECharts 输入沿用 `support`：`{"width":900,"height":500,"option":{...}}`。SVG 以 data URI 嵌入，避免 Chrome 可见而 PPT 图片丢失。

## PptxGenJS 正式原生制作

scene 使用 13.333333 × 7.5 英寸坐标；支持 text、shape、chart、table、image。每个元素有有限数值的 `x/y/w/h`，不得越界。图像使用自包含 base64；speaker notes 不从 scene 接收，通过 Studio 备注流程处理。

```json
{
  "title": "业务汇报",
  "font_family": "PingFang SC",
  "slides": [{
    "id": "slide-01",
    "background": "FFFFFF",
    "elements": [
      {"type":"text","text":"与 deck plan 一致的标题","options":{"x":0.8,"y":0.6,"w":11.6,"h":0.8,"fontSize":32,"color":"17364D"}},
      {"type":"chart","chart":"bar","data":[{"name":"交付","labels":["Q1","Q2"],"values":[24,38]}],"options":{"x":1,"y":2,"w":10,"h":4,"showLegend":false}}
    ]
  }]
}
```

独立 `author` 可用于短技术样例。正式 `native-scene JOB --request SCENE.json` 要求现有 native 任务处于可制作阶段，覆盖 deck plan 的全部 1–200 页、顺序与 ID 一致，保留每页标题。绑定记录 deck plan 的哈希，素材或策划变化后必须重新绑定，不能继续渲染过期 scene。

绑定后 `run` 的 prototype 和 full build 使用 PptxGenJS；HTML 审阅展示该引擎实际 PowerPoint 预览。布局检查归一化表格 EMU 后调用原版 leadership helper；警告阻止正式构建。Studio 修正 PptxGenJS 4.0.1 已复现的空 notes 结构、2D 图表多余系列轴引用和中文表格 Windows 字符集提示；不改变上游源码。QA 不会自动判断文案的审美优劣或所有文字溢出，因此仍要看图。

输出中报告真实引擎，交付包保留 scene JSON。默认 HTML 引擎行为保留。Office 往返操作的是候选副本，原调用 receipt 对应文件保持不可变。

## 其余项目如何参与

- **dom-to-pptx、pptx-automizer、codex-ppt-skill、image-to-editable-ppt-skill**：原生 HTML、模板、视觉替代、图片重建 Adapter 已存在，沿用其输入语义、运行状态与审批规则。
- **editable-leadership-pptx**：设计资料已接入策划，布局 JS helper 被 PptxGenJS 桥实际调用；不代表该仓库所有脚本都具备运行依赖。
- **awesome-ai-ppt**：用于发现候选工具和检索资料，它本身不是 PPT 渲染器。
- **PPTAgent**：上游实际入口是 `pptagent/mcp_server.py` 的有状态 stdio MCP。完整会话顺序为 `list_templates → set_template → create_slide → write_slide → generate_slide → save_generated_slides`，可重复中间步骤。需要隔离环境与 `PPTAGENT_MODEL/PPTAGENT_API_BASE/PPTAGENT_API_KEY`，Studio 的 `mcp-session` 会复制包和模板到本次调用目录，缓存也落在 Work。还需配置 `AIPPT_PPTAGENT_PYTHON` 为已安装上游依赖的隔离 Python；MCP 客户端使用 `mcp>=1.28,<2`。一个请求包含一组有状态步骤，调用结束后进程退出；可以先查询模板/布局，再用新请求从 `set_template` 开始生成。每个请求只选择一次模板，保存只能出现一次并位于最后；Studio 固定保存到调用目录。接口和未配置保护已实现，但没有真实模型配置，因此尚未跑通过真实 PPTAgent 生成。
- **Presenton**：服务调用适配器已实现并通过模拟服务契约测试；没有真实服务地址/模型，尚未进行真实生成验证。配置 `AIPPT_PRESENTON_URL`（HTTPS 或 loopback HTTP）；如服务需要认证，使用环境变量 `AIPPT_PRESENTON_TOKEN`，不把密钥写进请求 JSON。依据当前源码调用 `/api/v1/ppt/presentation/generate`，下载同源 `/app_data/` 或 `/static/` 导出。不能用未配置的状态宣称可生产。
- **Remotion、remotion-bits、Motion Canvas**：当前可检索组件和代码；完整视频制作仍需独立运行环境、composition 与验证。视频和 PPTX 对象动画是不同产物，不能直接互换。
- **academic-pptx-skill**：许可证文件与 SKILL frontmatter 存在冲突，自动复用保持未启用，可读取资料检查原因。
- **smiley-sans**：当前 clone 是字形源码；要用于 Office 还需官方字体发布文件或隔离构建、字体安装和字体 QA。

这些边界是实际状态，不是“每个仓库必须运行一次”的计数要求。提高美观程度需要按内容选择能力、明确版式，并基于成品预览迭代；增加仓库数本身不能保证质量。

MCP 会话 API 依据 [官方 Python SDK 1.x 客户端文档](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/client.md)；该上游 FastMCP 2.x 路线不应无约束安装 MCP SDK 2.x。

## 原生动画的精细调用（2026-09-07）

新增三项 `officecli` provider 操作。检查与时长修补由 Studio 适配器完成，新增动画及结构验证调用主机 OfficeCLI；不会改动上游仓库。

| operation | 请求字段 | 能力边界 |
|---|---|---|
| `animations` | `source` | 按演示文稿关系中的实际页序列出对象 ID、名称、文本、原生效果 ID、毫秒/秒时长、触发节点和原始开始条件。没有 presetClass 的特殊时序不作为可编辑效果列出。 |
| `animation-add` | `source, source_sha256, slide`，对象选择器，`effect, duration_seconds, delay_seconds, trigger` | 当前支持顶层文本/形状的 fade、wipe 入场效果；对象须无现有动画。trigger 为 onClick、afterPrevious、withPrevious。wipe 可指定 direction=left/right/up/down。其他对象的既有动画逐项回读比较。 |
| `animation-duration` | `source, source_sha256, slide`，对象选择器，`animation_id`，`delta_seconds` 或 `duration_seconds` | 在原时长上增减，或设置绝对时长；二者只选一个。只接受明确、相同正数时长的基本行为；嵌套、错开、隐含或不等时长明确拒绝。 |

对象选择器是 `shape_id`、`shape_name`、`shape_text` 三选一，名称与文本精确匹配；不唯一时拒绝猜测。页码从 1 开始。修改前必须从最新检查结果取得 `source_sha256`，效果 ID 也绑定该文件版本。

例如读取后，将第 1 页对象 ID 3 的动画 ID 3 延长 2 秒：

```json
{
  "source": "/absolute/Work/workspace/calls/add/candidate.pptx",
  "source_sha256": "从当前 animations 结果复制完整哈希",
  "slide": 1,
  "shape_id": "3",
  "animation_id": "3",
  "delta_seconds": 2
}
```

```sh
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 upstream-call officecli animation-duration --request work/17/workspace/animation/extend-request.json --output work/17/workspace/animation/calls/extend-new --json
```

该例是 **0.8 → 2.8 秒**，不是设置为 2 秒，也不是延后 2 秒。输入支持毫秒精度，最终时长必须大于 0 且不超过一小时。重复次数与反向播放等参数不变；总播放耗时可能受它们影响，`duration_seconds` 表示基本效果的时长。

为什么只改时长不调用上游的 indexed Set：当前 OfficeCLI 源码会快照、删除并重建同一对象的动画链。Studio 在这里仅替换选定 `p:cTn dur` 属性的数字字节，保留其他 XML 字节和 ZIP 条目内容，随后独立回读并调用 OfficeCLI 检查。记录真实 writer，不能把本地修补误报为上游 Set 执行。

每次调用保留 input.pptx、before.json、after.json、receipt.json 及成功候选。验证失败不发布 candidate.pptx。输出状态是 `candidate_requires_review`：不替换既有任务主文件、不批准任务。正式交付仍需按原流程采用候选并重审；目前没有新增自动采纳入口。

本机已实测 OfficeCLI 1.0.147：淡入 0.8 秒延长为 2.8 秒，0.3 秒延迟、单击触发保持；Mac PowerPoint 保存后参数仍保留。PDF/PNG 只核验静态版面；复杂路径、音频同步、Morph 成对匹配不属于这三个基础操作的覆盖范围，`playback_verified` 明确为 false。


## Mac 高级原生动画与影片（Work 17 后续）

这里只在编排或验收动画时使用下面的完整链路。AI 制作 PPT 时如需看当前稿的一页或全稿，直接运行 `aippt export-png <file.pptx> --slide N` 或 `aippt export-png <file.pptx>`，用 `--output` 保存到当前 Work 的 QA/workspace 或临时目录，再直接读取生成的图片；不需要 PowerPoint 鼠标操作或中间 PDF。此路径是 OfficeCLI HTML 近似渲染，用来观察而非宣称原生视觉验收。若用户仅要求把已经完成的 PPTX 保存为 PDF/MP4/PNG，分别直接运行 `aippt export-pdf`、`aippt export-mp4`、`aippt export-png`，成功并写出非空成品后交付；不进入 `native-effects`、完整解码、抽帧或媒体检查。视频动效是否保真是另一个明确请求。

`ppt-master native-motion` 的请求为 `{"source":"绝对 PPTX 路径","source_sha256":"当前文件哈希","plan":{...}}`。通过隔离进程调用仓库原版动画/切换库；Studio 负责请求校验、对象解析、自定义右移距离及候选发布。只生成候选，不自动采用，不修改 `github/ppt-master`。

正式任务使用 `native-effects JOB --plan PLAN.json --input build/文件.pptx`。Mac 自动选择 OOXML + Mac PowerPoint 后端：OfficeCLI 检查、实际打开保存、动画/切换语义回读比较；请求预览时还须影片生成及完整解码成功。失败不发布新的 PPTX。

支持的 `effects` 名称是 `fade`、`wipe`、`fly`、`zoom`、`swivel`、`float`、`bounce`、`appear-exit`、`motion-right`、`growShrink`；每项有 `slide`、顶层对象唯一 `shape` 名称、`duration`、`delay`、`trigger`（onClick/withPrevious/afterPrevious）。可用 `repeat`、`autoreverse`；`wipe`、`fly`、`zoom`、`swivel` 可使用其原生 `direction` 选项，右移用 `motion_by_x`，缩放强调用 `scale_percent`。fade/wipe 可加 `exit:true`。这是明确范围的合同，不代表 ppt-master 所有预设均已接入。

`carousels` 与 `morph_links` 沿用 Studio native plan 编译器；完整已验收技术请求见 Work 17 `workspace/mac-complete/native-plan-with-video.json`。已有动画的页须显式 `replace_existing:true` 才能重新编排；当前高级入口不支持向任意复杂既有序列无损追加。只延长两秒应使用上面的 `animation-duration`。

`previews` 支持选页、每页秒数、输出高度、帧率与质量。默认预览副本写入逐页自动换页时间，并在最终 MP4 转码时静音；交付 PPTX 不受预览副本修改影响。`use_timings_and_narrations:true` 只表示保留源稿中的计时并尝试沿用 PowerPoint 原生影片的音轨；当前 AppleScript 没有配置 Mac 导出面板的「使用录制计时和旁白」选项，也未完成有旁白文件的声音同步验收。原生影片使用 PowerPoint 可用的默认设置，FFmpeg 再控制最终 MP4 尺寸、帧率和编码质量。该 MP4 仅作页面序列预览；[微软说明](https://support.microsoft.com/en-au/powerpoint/save-a-presentation-as-a-movie-file-or-mp4-in-powerpoint-for-mac)指出对象动画不会在影片中播放，部分转场效果也会变化。动效验收需要实际 PowerPoint 放映或另建可验证的动画成片路线。

本机媒体运行包在 macOS/Linux 使用 `aippt-studio/scripts/setup-media.sh` 安装（Windows 使用同名 `.ps1`）。输出提供完整解码结果、哈希和最多前 60 秒的每秒样帧；每轮抽帧使用独立目录并在回执记录帧数及逐帧哈希，避免同名影片重新导出时混入旧帧。长影片或短时效果需要补充抽帧/人工查看。`video_exported=true` 只证明影片已产生；`playback_verified` 不自动置 true，逐效果视觉结论另存 QA。


## 当前对话模型调度：批量时长与媒体检查

默认决策模型就是当前对话助手。用户给自然语言要求，助手检查对象与现状、生成 JSON 操作计划、调用工具并核验。无需另接推理模型；本工作区不部署本地模型或下载权重。PPTAgent/Presenton 的独立模型流水线保留为可选，不作为当前制作路线缺失的前置条件。

### officecli animation-batch

请求字段：`source`、`source_sha256`、`edits`（1–100 项）、`dry_run`（默认 false），以及可选的 `expected_plan_sha256`。每项只能包含单项时长编辑已有的选择器/字段：`slide`、三种对象选择器之一、`animation_id`、`delta_seconds` 或 `duration_seconds`。不接受新增、删除、触发器、延迟或顺序修改；重复目标拒绝，以免对同一效果意外累加。

```json
{
  "source": "/absolute/current.pptx",
  "source_sha256": "从 animations 检查结果取得",
  "dry_run": true,
  "edits": [
    {"slide": 1, "shape_id": "2", "animation_id": "5", "delta_seconds": 2},
    {"slide": 1, "shape_id": "3", "animation_id": "9", "duration_seconds": 2}
  ]
}
```

先调用 `upstream-call officecli animation-batch` 查看 `plan.json`，其中列出 before_ms、after_ms、delta_ms、原触发/开始条件和 plan_sha256。执行时设置 dry_run=false，携带刚读取的 expected_plan_sha256，输出到新的调用目录。整个批次先解析校验，随后只修补明确的时长属性，最后一次 OfficeCLI 验证并原子发布候选；失败无部分候选，原文件不变。dry-run 是计划预览，不是 Office 验证。正式采用后仍需实际 Office 往返和动画预览。

### mediabunny inspect

源仓库：Vanilagy/mediabunny；实际执行包是独立锁定的 npm `mediabunny@1.55.7`，位于 `.tools/media-inspect-runtime`。源码 checkout 不安装依赖、不改动。执行脚本为 Studio `node/inspect-media.cjs`；只读取本地媒体，不接受 URL 或直播清单，不需要 WebCodecs 解码器、FFmpeg server 绑定或推理模型。

```json
{"source":"/absolute/preview.mp4","require_video":true,"require_audio":true}
```

可选 `expected_end_seconds` 与 `tolerance_seconds`（默认 0.1 秒）检查容器最大结束时间戳。注意这是最后结束时间，不是“结束减开始”的区间长度；音轨编码预热可能产生负开始时间，报告保留原值，不将其误判为字幕偏移。

返回每轨道类型、编码、开始/结束时间、包数、平均包速率；视频另有宽高，音轨另有采样率和声道数。未知字段、错误布尔值、无穷数、缺少要求的轨道或超出时间容差明确失败。上限 500 MB 本地文件。报告明确 `decoded=false`、`audible_content_verified=false`、`semantic_sync_verified=false`，不能把存在音轨当成能听见声音或逐词同步已完成。

Mac 原生导出中，若已安装此运行包，则在 FFmpeg 完整解码后自动通过注册 provider 执行检查并记录上游 receipt；失败不发布新 MP4。未安装时明确记录未执行，不伪造检查通过。完整重建命令为 `./aippt-studio/scripts/setup-media-inspect.sh`。
