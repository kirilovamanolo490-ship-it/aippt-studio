---
name: gaojie-layouts
description: Use the persistent Gaojie page and component library when selecting or adapting PPT layouts, organizing content relationships, or learning from a concrete layout reuse. Covers 31 category guides, native source objects and observed page examples in the AI PPT Studio workspace.
---

# 高阶文化排版学习与使用

把当前内容组织成适合听众的页面，再从高阶文化选择能表达这种关系的实页或组件。使用现有 aippt-studio 的作者路径、当前Work wrapper与可编辑PPTX规则；本技能提供选版、对象映射和持久学习方法。

## 先确定页面功能

先由 aippt-studio 确定整稿的具体性质、听众要理解或决定什么、主张与叙事顺序。整稿组织方式优先比较同类汇报的目录和正文页；31类是页面表达与组件资源，不能代替整稿性质判断。医疗个人述职与医疗成本核算专项汇报并不相同。跨题材借版时必须保留真实信息关系：阶段演进、包含、分配、因果和汇聚不能仅因“都有层级”互换。

从页面主张、证据、信息单元数量及关系开始。两项可能是对比或先后，三项可能是并列或中心分支；四项并不自动是四象限。按[31类详细使用规则](references/categories.md)读取相关分类即可，不必加载全部。

全量机器结构和实际观察分别保存在 `模板库/分析索引/高阶文化/`。首次结构覆盖5,693份完整PPTX与7,244条去重页面记录，观察180个源页，覆盖29个有PPTX的类别；最新覆盖以 `LEARNING-COVERAGE.json` 为准。素材图片和发布会没有本次PPTX，3890损坏原件不可用。目录名只是路由线索，具体页功能以内容、原生对象和实际图像判断。

## 选择与调用

在当前Work wrapper中使用：

```bash
workspace/aippt.sh library-layout-search --category '三段内容' --query '当前页面主张、三项证据及其关系' --json
workspace/aippt.sh library-style-search --collection gaojie --query '时间轴图 阶段计划' --json
```

比较返回的具体源页、组结构、测量数据、视觉发现与限制。优先内容关系合适的页；实景图、人物、数据等要支持当前主张。颜色和文件夹标签不能替代选择判断。

把选版判断写进现有 deck plan 的 `planning_strategy.page_design`：当前页任务、内容关系、相关分类、具体源页、借用/拒绝的元素关系与不用参考的理由。还要说明布局怎样适应当前文字长度、数字口径、主次比例及前后页。只写“借用双栏/饼图/层级”不能证明借用了该参考的设计；没有合适供体时正常自建，解释实际关系即可。字段及 native 对象对应记录见[两层参考流程](../../../aippt-studio/docs/AUTHORING_ENTRYPOINTS.md#two-level-reference-workflow)。

用 `library-style-inspect --path <库内相对路径> --slide N --output <当前Work的检查.json> --json` 读完整对象；需要图片映射时用 `library-style-image-inspect` 区分图片、形状位图填充、母版与背景。直接读取来源PNG或通过 `export-png` 渲染具体页，然后记录明确观察。

标题、文本、数据基座、装饰、实用素材等常是组件板：一张源页有多个替代方案。选择一个顶层组或一组相关对象，保留组内文字/形状/图片关系；不要把整张组件板当成目标正文页。读取真实对象ID，不能从缩略图编造ID。

`library-layout-build --request <映射.json> --output <当前Work内的候选.pptx> --json` 支持单页或选定组件组，完整替换所保留的文字，包括操作说明与样例文案。它保留源对象样式，支持多run正文；组件选取须提供顶层ID，输出留在当前Work，来源不可变。

组件内长标题/副标题需要更宽空间时，可显式提供 `component_text_widths: [{shape_id: '22', width_fraction: 0.70}]`；只支持所选顶层组的直接文字子对象、无旋转/翻转的确定坐标。该适配器扩大文本框与组界并保持组内比例，避免把整个组缩放导致字形一起变化。普通局部patch拒绝嵌套对象时不要取消其限制；选择这个明确支持的组适配器，或在新建页面路线重建所需结构。宽度来自当前画布与留白，例值不是固定设计规则。

在整稿局部换版时，用 `library-style-page-search` 的 `target_page.layout_collection=gaojie` 或项目供体集合 `gaojie`，给出具体分类、主张和角色。选定已观察供体后沿用 `library-hybrid-build`/`library-hybrid-bind`，协调整稿字体、配色、画布、母版及前后页。组件板应先抽取所需组件，不直接充当整页供体。

原生图表、表格和SmartArt的内部数据需要按当前材料重建，不能只换标题/可见标签就当作完成；仅文字的版式调用器会拒绝直接保留这些旧数据。数据组件同时检查单位、时间、比较基准和图形比例。

## 当前页面的表达与容量

映射当前标题、主张、解释和证据，移除“请输入”“字号建议”“高端PPT”等来源操作/宣传文案。图片、人物、品牌和数字按当前材料替换或记录具体保留理由。来源及QA位置放外部记录，备注只写讲稿。

选择闭合标题条或异形容器前确认当前文字长度；正文变长时换更宽的结构、扩大合理的文字区、精简次要解释或分配到邻页。来源字号和字符统计是诊断线索，不是固定规则或安全容量保证。

查看改造后的完整页面与邻页，检查层级、对比、断行、比例、图文对应及对象重叠。近似PNG不确认原生字体/动效，也不代表目录内所有页面已审。

## 持久学习

每次值得复用或暴露问题的真实应用，保留具体输入条件、借用的关系、实际发现、修正方法和限制。用 `library-layout-learn --request <使用记录.json> --json` 写入源SHA/页号、成稿SHA和源/成稿预览回执绑定的应用记录；它区分约束内可用、需修正和不适合这次映射。

学习针对具体关系和条件：例如短标题在开放式结构内可读，长正文在窄卡中需要换版；不要把一次通过扩大为任何内容都可用。保留失败/否定判断，重复应用不重复记账。实际使用记录会附到相应来源页的检索结果。

新增来源时先登记实际文件身份，再运行共享 `build-gaojie-layout-learning.py --index` 增量解析；同SHA缓存可复用。新页图必须真正读取后才新增视觉发现。完整流程与覆盖记录见 `模板库/分析索引/高阶文化/README.md`。本技能持久化项目知识，不推定用户接受或模型权重训练。
