# 真实可编辑模板的选择与改造

更新时间：2026-10-08。制作任务使用精简 Work；Studio 维护直接修改共享项目。

本文是模板改造的详细命令和限制参考。三条作者入口（已有稿局部改、按真实模板改造、新建）及 route/intent 区别见 [authoring entrypoints](AUTHORING_ENTRYPOINTS.md) 和 [workflow](playbook/workflow.md)。只有真实选定并绑定可编辑源文件/页面、且实际继承其结构或对象关系时，才走模板改造；页面灵感或主题相似不等于套用模板。Job-bound hybrid binding 目前只覆盖已索引策展集合：创赛与职规；不得声称适用于整个模板库。

## 接到需求后怎样做

1. 明确听众、演示场合、页数和材料中的事实。每页先写一个需要让听众理解的主张，再选证据和图片。
2. 从 `模板库/CATALOG.jsonl` 和当前深索引按场合、页面用途、主题和构图检索。竞赛答辩优先比较竞赛作品；不能因为色彩相近就选普通服务方案。
3. 看 2–5 份相关候选的实际页面，向用户展示最适合的候选及适配说明。至少看所选模板两个不同来源页；单页模板看一页。包含封面和一页较难的内容页，不能只凭封面选整套。
4. 用户选定后记录本轮选择；用户已明确指定的模板可直接沿用该指令。随后绑定模板，建立来源页与当前内容的映射，开始制作。
5. 沿用所选模板的主题、母版、字体层级、图文关系与图片几何。把旧事实、人物、项目 Logo、照片和数据换成本项目材料；根据新内容调整宽度、行数和留白。生成图适合概念视觉，不能替代实景、证照或真实数据证据。
6. 每页完成后看实际图像，检查正式文案、主次、文字密度、图片意义、圆角与边框；再看相邻页的论证和视觉节奏。需要修订的内容先改稿。整稿保留原有表达审阅和最终文件核验。

### 两类新增素材库的类型匹配

- 创赛候选先区分创新创业/产品路演、研究调查、乡村/红旅/非遗实践和通用模板；再按本任务页主张选真实来源页。不同类型的页序不能拼成固定目录。参见 创赛 STYLE-LEARNINGS（本地资源，未随公开仓库提供）。
- 职规候选先区分成长发展、就业能力论证、竞赛展示、人物访谈和通用/课程材料。growth/employment 自动分类覆盖有限，未标赛道时看用户材料和实际页面，不要强行推断。参见 职规 STYLE-LEARNINGS（本地资源，未随公开仓库提供）。
- 上述 STYLE-CATALOG/STYLE-PAGES 是页序及结构检索线索；抽样视觉报告只覆盖点名 PPTX 页。旧式 PPT 的 LibreOffice 转换、整页截图和不可编辑作品不作为可绑定的可编辑模板。候选确认前按当前源 SHA 重验，inspect 源文件并查看真实封面和实质内容页。用 source-preserving copy 保持母版/版式/主题及对象关系，在明确映射内替换示例文字、图片和事实，替换后检查整页与邻页。
- 近似静态预览、OOXML 结构、原生 PowerPoint 打开和动画放映属于不同验证状态。视觉样本的 OfficeKit PNG 不能证明字体回退、复杂母版、动画或原生显示；当字体/复杂母版可靠性不明、用户要求原生检查或请求涉及播放行为时，对最终绑定源/成品做相应原生核验。其余检查按变更影响与风险确定，不默认要求对每次静态修订执行通用 PowerPoint roundtrip。

## Studio 执行接口

所有命令通过当前 Work 的 `workspace/aippt.sh` 或 `aippt.ps1` 调用。

- `library-reuse <job> --json`：按当前逐页主张找整套模板、具体来源页和嵌入素材。
- `library-style-search --query '<赛事/赛道/页型/构图需求>' --collection creative|career --json`：在创赛和职规赛的全套 OOXML 索引中按类型、页面角色、已测量的配色/图像密度筛选，模板候选与案例参考分开返回；结果不是视觉确认。
- `library-style-search --layout-examples --query '<页面用途与内容>' --json`：查询小型页级参考集。`record_kind=slot_experiment` 记录对象替换和容量试验，`design_reference` 记录实际源页的题材适配、图文关系、图片分工及优缺点；后者不伪造可编辑槽位或已改造成品。已填写案例保持 `reference_only`；被用户否定的设计基线不再进入直接推荐。先读 `design_review` 并看当前页图，技术试验成功不代表排版优秀；[作者入口](AUTHORING_ENTRYPOINTS.md#judge-content-layouts-before-selecting-a-source)说明如何判断正文布局。
- `library-style-inspect --path '<模板库相对PPTX>' --slide N --output '<Work workspace JSON>'`：重验原件与逐份结构的哈希后，读取该页对象、几何、文本、图片关系和版式/母版链。须配合真实源页预览判断版面与图片意义。
- `library-style-image-inspect --path '<模板库相对PPTX>' --slide N --output '<Work workspace JSON>'`：列出该页、关联版式和母版的普通图片及形状图片填充，给出对象 ID、名称、几何位置、关系与媒体 SHA。换图时以实际可见的对象和图像用途定位；例如整页背景与嵌在矩形中的头像不是同一图片。`library-hybrid-build` 的 `image_replacements` 要有 `purpose` 和 `visual_role`，头像对象若覆盖了大半页会被拒绝。
- `library-style-copy --request <JSON> --output '<Work workspace candidate.pptx>'`：仅对已分类为可复用模板的现代 PPTX，校验至少两页的具体视觉观察与 `preview-slide` 回执，然后按连续页段复制源包结构并白名单替换对象；产物始终是待清理旧内容与待复核的可编辑候选，不能直接宣称完成新稿。对旧式 PPT、PPSX、PPTM、案例或指南只作参考分析。
- `library-hybrid-plan --request <JSON> --output '<Work workspace plan.json>'`：同时找整套主模板与最多两页的替代版式。输入包括当前项目的场合、赛道、职业、所需页数范围与逐页主张，或已选主模板的路径和 SHA。结果列出来源页、页型/内容/配色/字体/画布差距及页数匹配状态，不自动认定搜索第一名可用。若仍选择页数不符的来源，构建请求须写明删并页计划；当前候选仍保留来源全套页数。
- `library-style-page-search --request <JSON> --limit 5`：在主模板某页需要换版时，根据页面主张、角色、证据和构图找 2–5 个其他来源页，并单列 `reference_candidates`。主模板所在集合优先；跨集合和已填写案例均只作版式参考，全部人物、事实、数字和图片须重新映射。检索中的职业或赛道未明示时标记不确定，不用泛化词把不相干行业页推到前面。
- `library-hybrid-build --request <JSON> --output '<Work workspace candidate.pptx>'`：以一份已核对的 PPTX **全套原稿**为主包，默认保留全部页。可指定一或两处 `target_slide` 对应 `donor_source_path`/`donor_slide`，只迁入供体页静态依赖闭包，再按输出页顺序与 shape ID、原文字或媒体 SHA 替换文图；`geometry_edits` 使用现有 `pptx-patch` 的显式几何编辑合同。命令写出每页来源图、候选 SHA、未清理占位/未映射内容及需要重看的相邻页。供体页的母版、版式、主题、图片关系会独立迁入，主包其他页对象及依赖保持原字节。共享图片用 copy-on-write 只换指定图片，原媒体仍被其他页使用时回执会标记待清理。
- `library-hybrid-bind <job-id> --request <json> --json`：把通过校验的混合模板映射绑定到当前 Work/job；从已有 Work wrapper 调用，随后执行 `run <job-id>`。不创建新 Work。只接受 `template` route、且已导入唯一 `InputRole.TEMPLATE` 源和当前 `deck-plan` 的任务；允许 `brief_review`（保留 brief 审批状态）、`full_build`、`final_review` 和从 `blocked` 恢复到 `full_build`，拒绝 `delivered` 或非模板任务。绑定记录写入 `planning/hybrid-template.json`，并清除 prototype/final 审批；之后仍走标准 preview、候选提升、表达审阅和最终 gate。未绑定时继续使用原 Automizer 路线。

`library-hybrid-bind` 的 `request` 复用 `library-hybrid-build` 合同；主源必须是全套 PPTX，页数必须与当前 plan 相同。逐页必须为每个文字和图片对象提供替换，或逐对象说明保留原因。额外的 `retained_objects` 条目形如 `{slide: 1, shape_id: "2", kind: "text"|"image", content_sha256: "...", reason: "具体说明至少8字符"}`；当前计划的标题和 points 必须能够从构建候选中读回。图片可用 `image_path`（兼容 `path`）；图片与 preview 所需依赖会复制到 `assets/hybrid-inputs`，避免临时路径失效。媒体 SHA 采用 JSON canonical list(media sha) 的哈希；不要求人工计算，出现阻断时按 `build/hybrid-template-receipt.json` 中的对象 SHA 与 blockers 补齐后重新 bind。该 receipt 也区分映射缺项与实际阻断。

绑定后由 TemplateAdapter 内部 source-preserving engine 构建。绑定会明确省略供体本页的旧备注，再由内部 helper 根据当前计划的 `speaker_notes` 写入讲稿；speaker notes 只保留面向听众的讲稿，不保留旧事实或路径。独立 `library-hybrid-build` 默认仍拒绝供体备注，调用方须显式设置 `omit_donor_notes: true` 和具体的 `donor_notes_disposition` 才能省略。源稿保持不变，处置写入回执。普通 WPS `tags` 元数据可随源页保真迁入，仅接受无关系依赖的标准 `p:tagLst/p:tag` 数据；其他不支持的内容仍阻断。候选仍需经过标准预览、promotion、表达审阅和最终 gate。结构验证只能证明结构/映射合同，不代表页面视觉质量或原生 PowerPoint 通过；按实际变更和风险进行视觉及原生检查。
- 局部换版默认只处理静态页。`staticize_donor_timing` 仅在明确制作静态稿、供体本页动画可安全剥离时由调用方显式启用，回执记录删去的节点；`allow_minor_size_delta` 仅限每轴 0.5 mm、宽高比差 0.001 的微小画布差，输出使用主包尺寸且不缩放对象。跨模板 Morph、复杂触发、媒体控制、布局/母版动画及不安全依赖会拒绝。可按真实整页比较提供 `theme_accent_overrides`/`direct_rgb_overrides`，只修改导入供体层的颜色；字体、图片意义和文字行宽仍要检查。
- `library-template-review <job> --path <目录相对路径> --json`：准备所选候选的来源、页数与确认记录要求。这是元数据，不会自动生成或展示页面图像。
- `preview-slide <来源PPTX> --slide <页号> --output <Work内PNG>`：生成绑定来源 SHA、页号及 PNG 哈希的预览回执。Office Kit / OfficeCLI 是近似渲染器；字体、母版或复杂图形呈现不可靠时须看原生 PowerPoint。
- `library-template-confirm <job> --request <JSON>`：保存本轮用户选择、源页预览与观察、构图主题保留、文字图片替换及允许微调的范围。每张预览必须附现有 `preview-slide` 的 `.preview.json` 回执；程序校验来源页号、源文件与图片哈希。
- `library-template <job> --path <目录相对路径> --json`：核验当前确认后导入真实模板并切换到模板路线。缺少确认或使用旧来源、旧请求、旧预览时不会开始绑定。
- `library-media`：按来源页与对象提取独立素材，核对图片含义、权利状态和本页用途。

整套套用时先看封面与至少一个实质内容页，再建立逐页内容和图片对应；只有需要改版的页另看供体整页和相邻页。复用输出的 `content_audit`、`unmapped_changed_page_content` 是待审提示：例如原稿专业、姓名、能力数字、证书或图片即使没有“请输入”也可能是旧事实。必须以当前材料决定保留、替换或删去，再看候选的整页与前后页；根据页面和风险，使用近似预览、原生 PowerPoint 或播放检查并分别记录。动态请求继续走原有动画路线，不能把跨源静态换版标为动效复刻。

从已填写参赛案例借版式时，构建请求将供体页标为 `layout_only_reference`，并对该页**每个非空文本对象和图片对象**给出替换映射，或给出逐对象保留理由；关联版式/母版如含文字或图片，还须列出具体视觉观察与权利处理。图表、表格、SmartArt 等含旧数据的页需另行重建，当前跨源静态换版会拒绝直接复制。真实护理案例第 10 页的环形行动方案在内部试验中用三处文字替换与一处结构性序号保留完成映射，再把橙红箭头改为主模板蓝灰色；该页及相邻页的近似图和内容缺口记录在工作流 QA（本地资源，未随公开仓库提供）。

## 动态模板：先拆解再实际复制

用户明确要动态演示时，`library-reuse` 会把专门的动画候选和普通内容/场合候选一起返回。可用 `library-motion-search --query '页型 + 动态机制' --limit 10 --json` 缩小范围。候选来自 `模板库/镶金边高端动态ppt/分析索引/MOTION-INDEX.jsonl`：126 份 PPTX、1,303 页按当前来源 SHA 记录页面及相连版式/母版的对象、完整时间树、触发条件、转场、媒体与相邻 Morph 对象匹配候选。SS57/SS62 这类相册的路径动画在 `ppt/slideLayouts`，只查 `ppt/slides` 会漏掉核心效果。`library-motion-inspect --path '<目录相对PPTX>' --slide N --output '<Work workspace JSON>'` 在返回完整源页结构前重新核验当前 PPTX 和分析 JSON 的哈希。

动态页的检索命中只证明源页含对应 OOXML。先看真实整页与相邻页，读同目录教程中的照片数量、位置、循环和软件依赖；看清当前需求与来源作品的场合是否相容。`图表与视觉素材`里的动画是可考虑的页级机制，不自动成为竞赛、教学或企业整稿模板。只复制必要的相邻页和对象，旧示例文字、图片、事实、Logo、水印不得进入新稿。

选择保留源动画时，向 `library-motion-copy --request <JSON> --output '<Work workspace candidate.pptx>'` 提供 `source_path`、当前 `source_sha256`、连续的 1 起始源页数组，以及可选的 `replacements`（源页号、shape ID、原文字 SHA-256、新文字）和 `image_replacements`（源页号、shape ID、原媒体 SHA-256、同格式本地图片）。图片在关联版式上时，记录额外指定 `layer:"layout"` 和精确 `layout_part`；只有该版式未被未选页使用、共享媒体的其它引用都已显式映射时才允许替换。这条路线从原 PPTX 复制完整包，再只改所指对象和可见页列表；它逐页回读页面以及关联版式/母版的时间树、转场与动画目标 ID，能证明结构保留。Morph 要连同前一页和本次所需的连续状态一起复制。目标对象无法唯一绑定、媒体被不安全共享或源包异常时停止，而不是输出一份动画已丢失的稿。

复制后的 PPTX 仍需按当前内容与页面图像审阅，要求动态成品时还要在 PowerPoint 中实际放映和复查节奏、触发及媒体。`pptx-automizer` 常规模板路线会对未经审阅的源动画设阻断；不能仅打开 `allow_animations` 就推断它已保真复制。OOXML、近似 PNG、供应商演示视频和 PowerPoint 原生放映要分别记录证据等级。

模板确认记录由可信操作者提交。程序校验字节身份和当前上下文；助手仍须实际展示候选、引用本轮人类选择并诚实检查页面。选择模板、首稿满意和终稿接受是三个阶段。

## 避免做成文档

页面标题和核心证据共同表达一个明确意思。正文用于必要解释；详细过程可写成演讲者讲稿，或在需要时安排证据页。保持正式名称、单位、比较基准和真实事实。不要把助手回复、排版建议、制作提醒写入页面或备注。

长文段、窄文字区和悬殊字级组合会产生 `TEXT_DENSITY_CONTEXT` 提示。提示进入逐页表达审阅，助手需判断、改稿或说明保留理由。它不会自动删正文、压缩事实或强制所有页面同一字号。

## 引擎分工

- 模板库提供可借鉴的设计语言与真实素材。已绑定混合模板映射由 Studio 的 source-preserving 引擎执行；其他常规模板任务继续由锁定的 `pptx-automizer` 运行包执行。
- PPT Master 执行当前接入的源码阅读、原生 SVG 转换及动画模块。独立试用和一次工具成功都不代表自动获得全库的美感。
- OfficeCLI 承担结构检查及受限操作。它的源码检出版本和主机二进制版本分别记录，近似预览不代替原生视觉检查。
- 图表、图标和文字作者选择取决于本页内容。增加工具数量不会代替页面设计和成品迭代。

## 需要保留在制作代码中的内容

制作源应明确每页的主张、来源模板页、对象对应、可替换文字和图片、需要保留的比例与轮廓。用户反馈直接修改这份源或配置；构建成品后复查实际整页与相邻页，修订再构建。不能用对话记录代替可运行的生成代码。

独立 PPT Master 的三页试用被用户指出质量不足，不作为竞赛风格基线。优先使用经实际整页比较的模板与来源素材，由 Studio 组织各工具完成具体对象的制作与核验。参考索引和结构计数只能缩小检索范围。
