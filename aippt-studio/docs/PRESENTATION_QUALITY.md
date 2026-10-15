# 整页表达与助手自审

Work 37 用户最新纠正覆盖了先前的字号解释：用户要求的是正式演讲或汇报中的整页表达，不能把它收窄成固定字号。一个页面可能所有字都很大，却仍然主次失衡、意义不明、措辞随意；一个较小的必要标注也可能在该页面中清楚、合适。对字号的工程修复保留，但数值通过不能充当设计判断。

## 制作的底层逻辑

先明确听众、场合和这页要传达的一件事，再选择支撑它的事实、图像、数据和组织形式。标题提出的判断必须有相应依据；正文解释关键关系，图像承担明确含义。看前后页，确认前提是否已交代、这页增加了什么、下一页如何接续。不能用“注意和前页关联”这类提醒代替真正修好页面之间的关系。

文案要像汇报者正在对听众说话：准确、具体、符合场合，保留必要的范围、时间、比较基线与不确定性。正式不等于堆套话；简洁不等于删掉事实；大气不等于只剩巨字与空白。“本项目”不是禁词，有事实的项目说明可以保留；真实工艺步骤、实施阶段也可以是必要内容。需要排除的是助手对用户的回复、对制作者的排版建议、QA提醒，以及没有具体信息的填充话。

从完整页面判断文字比例、宽度、分组、视线顺序和图文关系。巨大数字旁边挤一条狭窄说明，可能把最关键的解释降成装饰；同样，大数字也可能是这页合理的核心证据。必须结合内容和实际图像判断，不能把两种情况用一个比例阈值自动裁决。

新Work的 `assistant-context.json.user_authoring_defaults` 和新job的 `planning/authoring-policy.json` 已载入上述原则，不依赖历史案例命中。默认策略为 `presentation-expression-v3`。对当前来源和已认可参考的理解仍由当前助手负责。

## 规划、成稿与审阅意见分开

可见页面读取计划中面向听众的标题与正文。`purpose`、修订备注、排版建议只供制作使用。明确标注为制作元数据的输入完整保存到 `planning/editorial-inputs.json`，保留原文与来源，交给助手检查其中是否夹带有效业务事实；不能因为一行带“客户要求”就把事实静默删掉。

备注区只能是讲者可使用的讲述稿。“先给听众一个路线图”是给制作者的指导，应改成真实讲述或移出讲稿。未标注的混合文案不能仅靠字符串分类，需要在实际候选审阅中辨认。

默认HTML已取消“出现一个数值就自动抽成巨大数字”的路径，数值保留在完整表达中。页面是否采用指标重点构图应由其语义决定。默认渲染仍需要具体尺寸才能执行，这些实现值不构成用户的通用设计规定。

## 当前助手必须看实际页面

样稿和最终批准前，Studio要求当前助手完成整页表达审阅。这是助手自检，不增加用户确认步骤，也不能把检查工作交回给用户。

```sh
./scripts/work-studio.sh 37 expression-review <job-id> --gate prototype --json
./scripts/work-studio.sh 37 expression-review <job-id> --gate final --json
```

命令先核对原生页面渲染回执；没有当前PPTX的有效图像时，会复制当前候选交给原生PowerPoint生成页面，保留原稿。同一文件的有效渲染可复用，旧HTML预览不能冒充当前PPTX页面。随后生成 `reviews/<gate>-expression-packet.json`，汇集实际PPTX文字与备注、逐页图像、规划目的、来源、制作输入和相邻页上下文。样稿只覆盖所选样页，最终覆盖全稿。自动文字信号只指出可能的制作语言，不能直接删除；编辑培训中的反例引用可能正是合法正文。

助手打开页面图像，结合来源读整页，判断主旨、正式文案、视觉主次与跨页叙事。发现问题先修稿，再渲染重看。用 `--request` 保存当前判断，例如以下结构；其中的观察必须来自实际审阅，不能机械填“通过”：

```json
{
  "packet_sha256": "从当前审阅包读取",
  "reviewer": "current_assistant",
  "narrative_assessment": "结合实际相邻页说明叙事是否连贯、是否有证据跳跃或无效重复。",
  "slides": [{
    "slide_id": "slide-01",
    "preview_sha256": "实际查看的页面图像校验值",
    "audience_takeaway": "听众应该从这一页理解的具体意思。",
    "page_assessment": "结合这页实际文字与布局，说明表达、主次、图文关系及需修订之处。",
    "decision": "revise",
    "signal_dispositions": []
  }]
}
```

```sh
./scripts/work-studio.sh 37 expression-review <job-id> --gate final --request work/37/workspace/expression-assessment.json --json
```

有自动信号时逐一记录其 `signal_id`、`decision`（`legitimate` 或 `revise`）和具体 `reason`。待修订页不能标成ready。缺页、重复页、旧稿、旧预览、来源上下文变化会使凭据不成立。批准后候选变化导致的本阶段哈希阻塞，可在正式提交新审阅时恢复；其他来源、资产或交付不一致不会被解除。批准及导出复核当前凭据，技术检查通过不再替代整页审阅。

这个接口保存助手实际作出的判断，程序不能证明助手真的看过图片，也没有独立的审美模型。执行助手必须诚实查看，不能自动填满ready；用户验收仍是另一事实。原生Office另存和最终交付页面也必须实际复查，尤其是转换后的换行、图片与动画。

## 整套模板候选确认

新建稿使用模板库整套模板前，先运行 `library-reuse` 并把当前候选及来源页实际预览呈给用户。可用 `library-template-review <job> --path <模板库相对路径> --json` 取得所选候选的源 SHA-256、可见页数与预览提交契约；该命令只返回元数据，不代表图片已展示或已被检查。实际预览须保存在当前 Work 的 `workspace/` 内，并逐页记录页码、文件 SHA-256 和具体视觉观察。

用户选定后，通过 `library-template-confirm <job> --request <JSON>` 写入所选目录路径、来源 SHA-256、至少两个不同来源页预览（单页模板至少一个）、非空人类确认原文，以及适配范围：保留的构图/主题、将替换的文字与图片、允许的版式微调。每张预览须带当前 Work 内 `preview-slide` 生成的 `.preview.json` 路径及 SHA；Studio 核验 receipt 的 `rendered_advisory` 状态、模板 source SHA、页码、输出 PNG SHA 和文件当前字节。该渲染仍是近似效果，不能称为 Office 原生审阅。若本轮原始 Work 请求明确写出了所选模板的名称或路径，原请求可作为确认原文，避免重复追问；仍须提交有效预览与适配范围。此 CLI 是受信任的确认记录接口：成功只表示输入的证据被记录，不独立证明引用来自真实用户或助手确实看过预览；操作者必须引用本轮人类选择，不能把模型自行挑选、空白授权或命令成功说成用户确认。

`library-template <job> --path <模板库相对路径>` 在任何导入/路线变更前校验确认收据仍对应当前 Work 请求、候选路径、模板 SHA-256 和未变化的 Work-local 预览。通过其他 route/input-role 操作直接指定模板库文件也执行相同检查；旧 catalog 哈希从完整 `DESIGN-STRUCTURE` 索引解析并核对原 PPTX 字节。绑定后会记录采用标记和绑定前计划哈希供审计，同源路线调整不再因绑定引发的 brief/plan 更新而误判过期，但仍复核当前模板、请求、确认范围及源页预览。更换候选、模板源或原始 Work 请求后须重新呈示并记录。最终逐页来源映射、真实 PPTX 输出核验及 prototype/final 审批继续使用既有流程；模板选择确认不等于样稿或终稿接受。用户记录具体不适配来源页后选择新构图仍走现有 `library-reuse-decision` 门。

```sh
./scripts/work-studio.sh 62 library-template-review <job-id> --path '竞赛与答辩/模板.pptx' --json
./scripts/work-studio.sh <N> library-template-confirm <job-id> --request work/<N>/workspace/template-confirmation.json --json
./scripts/work-studio.sh 62 library-template <job-id> --path '竞赛与答辩/模板.pptx' --json
```

## 数值和几何检查的作用

HTML导出前检查真实文字范围、容器截断、页面边界、图像加载和相交。PPTX检查读取最终字号、继承、自动缩放、分组、表格、可见图表标签和遮挡。明确裁切、越界和前景遮挡继续阻断；不确定的相交要求复查。

默认字号与相对尺度提示是 `advisory`，不因低于固定值单独判页面不合格；所有报告明确未验证视觉或表达质量。新建构图的浏览器整稿预检在连续至少三页实际布局骨架相同时也给出 `repeated_layout_skeleton` advisory，提醒复核跨页表达是否真的需要重复，来源/参考派生不套这个新稿提示。只有项目真的给出明确字号约束时，才通过 `quality-policy` 配置 `typography_mode: explicit` 和对应的 `minimum_font_sizes`，未指定的角色不被补上固定底线。

```sh
./scripts/work-studio.sh 37 quality-check work/37/workspace/candidate.pptx --output work/37/workspace/qa/quality.json --json
./scripts/work-studio.sh 37 quality-policy <job-id> --request work/37/workspace/project-constraints.json --json
```

`quality-check --policy` 支持审计配置及正式job策略。`quality-policy` 支持 `typography_mode`、`minimum_font_sizes`、`object_roles`、`exceptions`；只提交具体字号约束时视为explicit。正常叠放可按具体对象及证据说明，不能用全局跳过替代判断。更新保留旧策略和brief批准，撤回受影响视觉批准；已交付/失败终态不改写，需独立修订任务。

`quality-check --form teaching|government_briefing|business_report|competition_defense|...` 会在同一次本地检查中附上 `form_review`：用当前 PPTX 的可编辑文字定位可能混入的制作说明、孤立的角落序号等，并给出相应场景的复核问题。它不删文案、不把合法的步骤/问答编号判错、不设统一字号，也不能从 OOXML 判断图像意义或整页美感。场景应来自当前任务的真实用途，而不是由模板颜色推断。

文案信号匹配明确的制作口吻，如“我会帮你优化PPT”“以下是为您准备的汇报PPT”“这一页可以放项目背景”，并要求逐项 context-required 处置。匹配完全位于明确引号内的内容不触发助手口吻信号，保护培训引用；真实项目名称、技术流程、对受众的有效操作指令仍可正常保留。窄栏检测 `TEXT_DENSITY_CONTEXT` 组合可编辑文字长度、段落/换行数、OOXML文字框宽度与实际可解析字级对比，返回这些测量成分供当前页面复核。它是 advisory 定位器，不判断含义、实际换行、对比度或字号是否达标，不会自动否决；引用图像或母版中的文字仍在扫描范围之外。

结项准备使用 `output-preflight` 对 `output/` 中**当前文件**重新计算 SHA-256、页数和全部页面的本地结构质量。它把 `workspace/qa` 与 `workspace/final-build` 内的终稿类旧清单/报告标为 `current`、`stale` 或 `unbound`，旧文件的报告不能代替新文件的检查。若当前 Work 的各份 Studio brief 对表达场景一致，预检自动采用该场景；存在冲突时记录冲突并回到通用复核，不猜测当前终稿归属，明确的 `--form` 可覆盖。`scripts/prepare-work-close.ps1` 已对每个输出 PPTX 自动运行这一门槛；只要当前文件仍有未关闭的 blocker/review 项，就停在 review 之前，并把诊断保存到 `workspace/qa/output-preflight/`。结项入口同时把未关闭的客观信号记入本 Work 的不可变 quality observation 与 learning event，供后续归档复盘使用；它不自动推断原因、宣称已修复或推广通用经验。这能拦截手工替换终稿而绕过 Studio job gate 的情况。

刻意的图上文字或有意义的装饰需要按当前文件、具体对象留下复核依据。可在 `workspace/qa/output-quality-policies/<当前PPTX的sha256>.json` 写入 `source_sha256` 与 `quality_policy` 两个字段，后者使用 `quality-check` 的对象级例外格式；文件哈希变化后旧例外不再有效。排除真实遮挡前，查看当前整页和邻页，确认例外是有意且可读的。`output-preflight` 只给本地结构结论；最终仍需当前文件的实际页面表达审阅与原生 PowerPoint 往返/导出验证。

## 精确微调与学习

`pptx-inspect` 与 `pptx-patch` 按源文件SHA256和页/对象ID修改位置、尺寸、字号、层级、对齐、等距分布；先dry-run，再应用到新目录。保留未选对象、其它ZIP部件及原有混合文字样式。微调结束后重新看整页，精确改变对象并不自动说明表达改善。

新稿制作的分场景字体、页边距、间隔、图文比例和页面语法见[可执行场景排版基线](STYLE_PROFILES.md)。这套参数只在新建且创建新视觉系统时作为默认值进入 HTML 和原生制作链；客户已有页面、给定参考及明确风格要求仍需优先保留和验证。

日常局部修改使用[本地快路径](QUICK_REVISIONS.md)：所选页简报、`pptx-text`/`pptx-add-text`/`pptx-picture`/`pptx-patch` 对象修改、`revision-check` 局部结构复核，以及受影响页的实际视觉判断。图片可先用 `image-check` 与 `image-prepare` 在本机作确定性诊断和副本处理；AI 要直接看当前稿的一页或全稿时用 `export-png` 与可选的 `--slide N`，读取本地生成的图片，不通过鼠标另存或先导 PDF。需要源文件哈希回执的单页 QA（例如模板确认）仍用 `preview-slide`，可选 Office Kit 或 OfficeCLI HTML 后端。它们都是近似渲染；`passed_scoped` 只说明回执绑定的页面没有新增或恶化的本地结构问题，不代替正式终稿的表达审阅与原生 Office 验收。

## 从项目经验到当前页面

完整项目复盘产生的条件经验进入 `context.project_learning` 和 `planning/project-lessons.json`。正式表达审阅会根据当前标题、页面内容和 brief 重新获取有原始来源支持的相关经验，放在 packet 的 `project_lessons` 中。历史客户事实和复盘用语只供决策，不会写入页面或讲稿。

当 packet 有经验时，请在原有页面审阅之外提交 `lesson_dispositions`，每条恰好一次：

```json
{
  "lesson_id": "从当前packet复制实际ID",
  "decision": "applied",
  "reason": "本页图像和曲边底形共同组成证据区，适用这条构图经验。",
  "slide_ids": ["slide-03"],
  "verification": "在本轮实际整页图中检查了四个边角和主体，图片轮廓与底形一致，原始证据内容完整。"
}
```

`applied` 与 `revise` 必须指向本轮确实审阅的页面。`not_applicable` 应写清当前要求、参考或内容为何不同，不强行套历史处理。仍为 `revise` 的页不能写 ready；来源回执、页面或上下文改变后，旧审阅失效。这是助手的使用与自查记录，不是新增用户审批。

## 微调后的可见变化

`visual-diff --request REQUEST.json --output NEW_QA_DIRECTORY --json` 实际调用固定版本的 Pixelmatch，比对同尺寸 PNG 并生成差异图。两侧均须给 `path`、`sha256` 和一致的 `renderer_signature`（`renderer`、`version`、`platform`、`fonts_sha256`、`settings_sha256`）。缺失或不一致时明确返回 `not_comparable`，不自动缩放或把跨平台字体差异当成修改。

可用 `allowed_regions: [{"x": 100, "y": 120, "width": 300, "height": 200}]` 标明本次允许改变的像素区域；该区域外有差异会返回 `unexpected_changes`。没有提供区域时报告整页是否改变。输出包含差异数量、边界和原文件哈希，供检查未改区域、图片边缘和修改是否真的可见。抗锯齿容差减少渲染噪声；像素一致与美感、语义、用户认可分别判断。

表达审阅发现的问题与技术失败一样保留到Work证据中；修复原因与复验另行记录。应学习的是“怎样根据受众与证据判断页面”，以及可复现程序原因的修正。不得把某一次版式、统一字号或测试数字变成所有客户的通用答案。原始对话、失败稿和后续纠正都保留，本次用户对先前字号误解的纠正也要覆盖旧经验的适用结论。


## 跨任务反复问题如何进入实际审阅

默认不生成无意义的角落01/02/03/04、空编号圆圈和随附装饰线。实际页中的独立角落数字会进入 `DECORATIVE_ORDINAL` 待判读项；助手必须根据完整页面说明它是否有真实导航、数量、章节或流程意义，没有作用就先从作品移除。不能自动删除所有数字，不能把图片/母版内未被检测的数字当作已检查。

模板的副标题不再自动取内部 `purpose`；只有明确的观众文案才进入副标题。纯图片输入不自造“核心发现1/2”的结论。历史专项脚本的示例标签不能拿来充当通用新稿脚手架。

整页审阅同时覆盖图与论点是否对应、重复与裁切、关键证照/人物的有效可读区域、截图能否支持标题、指标与单位及比较基线、精简后的信息覆盖、参考中的元素关系以及用户已经调好的对象。语义判断仍由当前助手完成；自动信号、文件哈希和程序通过不能证明审美认可。

有原始用户记录和适用范围依据的归档学习指导，会自动加入新job的authoring-policy及新Work上下文。它不覆盖本次用户要求，也不替代实际页面自审。具体方法见 `LEARNING_WORKFLOW.md`。

图片的裁切、表面和边框现在使用 [共享图片几何合同](IMAGE_GEOMETRY.md)，接入原生、HTML 和模板构建。审阅包中的 `IMAGE_FRAME_CONTOUR_MISMATCH` 给出具体图片及底形对象，必须结合实际边缘判读并记录处置。圆角/椭圆图片的空角不能再被几何审计当作覆盖整个矩形的实心遮挡。
