# 局部修改的本地快路径

这条路径用于已有可编辑 PPTX 的明确小范围修改，例如改一处文案、移动一组对象、替换一张图片或调整一页层级。它不把一次微调重新当成全稿制作，也不触发 PDF 导出；只有用户在当前任务明确要求 PDF，才另行按需导出。首稿、跨页重构、母版/主题/字体等共享资源变化、动画/视频仍按相应制作流程执行。小范围修订不默认触发全页截图或完整终稿门禁。

## 先决定检查范围

| 本次变化 | 本地执行 | 助手复核 | 正式交付前 |
| --- | --- | --- | --- |
| 单个文字 run 或对象几何 | 源哈希绑定的对象修改；只检查目标页与非目标包部件 | 查看候选的目标整页；跨页叙事变化时看邻页 | revision-publish 更新原文件路径；按改动影响决定是否需要原生 Office 复核 |
| 图片文件预处理 | 本机读取像素、EXIF、透明度、边缘、尺寸；需要时生成等比缩小的副本 | 判断图片是否支持页面主张、裁切/边框和背景形状是否匹配 | 检查交付文件的实际页面 |
| 页级增删、改母版/主题、全稿统一设计 | 按影响范围重建并检查所有依赖页面 | 检查全稿主线与页面关系 | 全部正式门禁 |
| 动画、视频、复杂图表或 Office 兼容性 | 专门的原生运行与播放/编辑性验证 | 看实际播放/交互结果 | 原生往返和最终导出 |

自动信号只是定位问题。`passed_scoped` 表示本次明确目标页的本地结构检查未发现新增或恶化的问题；它**不表示**整稿、原生 PowerPoint 视觉、语义或客户验收通过。已有问题仍在回执中单列，不能因为它们不是本次新增而被忽略。

## 一次完成局部修订

已有可编辑首稿且改动不超过四页时，优先用 `fast-revise`。请求 JSON 与 `pptx-patch` 共用同一来源 SHA、页号和对象 ID 合同；一个调用会把显式对象操作应用到**现有 PPTX**，核对未改包部件，只检查变更页，并默认只渲染这些页的本地近似预览。它不运行规划/生成器，也不创建另一个 Studio Job。输入文件保持不变，候选、原始补丁回执、局部 QA 与预览集中在一个新的 Work 内修订目录。候选只供内部审查；自动结构 QA 和近似预览都不等于视觉验收。助手看过候选受影响整页并完成所需局部表达判断后，才用 `revision-publish` 将候选发布回来源的原路径，文件名不变。多轮微调时，从同一路径的当前稿继续修改，重新检查 SHA 和对象 ID。

```json
{
  "source": "work/<N>/workspace/current.pptx",
  "source_sha256": "<pptx-inspect 返回的 SHA-256>",
  "edits": [{"action": "replace_text", "slide": 4, "shape_id": "12", "expected_text": "旧文字", "replacement_text": "新文字"}]
}
```

```sh
work/<N>/workspace/aippt.sh fast-revise --request work/<N>/workspace/revisions/change-request.json --output work/<N>/workspace/revisions/change-01 --json
```

默认预览后端是本地 Office Kit；已知旧稿母版元素在它里面不完整时可用 `--preview officecli`。明确只需先做结构试改时可用 `--preview none`，回执会显示 `needs_visual_review`。本地预览故障会留在 `fast-revision.json`，不会伪装成已通过视觉复核；局部结构新增/恶化问题返回 `needs_structural_review`。全局字体/主题、母版、跨页重排或五页及以上变动不走快修路线。交付前要看改动后的真实整页；是否重新做原生 PowerPoint 保存重开，按本次改动对字形、裁切、母版、动效和兼容性的实际影响判断，不因普通微调自动重复完整往返。

## 直接编辑文字，不写一次性脚本

以下命令都在当前 Work 的 `workspace/aippt.sh` 内运行；Windows 则使用同一 Work 的 `workspace/aippt.ps1`。具体路径与页号按当前任务填写，输出目录必须是新的 Work 内路径。

```sh
work/<N>/workspace/aippt.sh pptx-inspect work/<N>/workspace/current.pptx --slide 4 --brief --output work/<N>/workspace/qa/slide-4-inspect.json --json
work/<N>/workspace/aippt.sh pptx-text work/<N>/workspace/current.pptx --source-sha256 <inspect中的hash> --slide 4 --shape-id <对象ID> --expected-text '旧文字' --replacement-text '新文字' --output work/<N>/workspace/revisions/text-01 --json
work/<N>/workspace/aippt.sh revision-check --receipt work/<N>/workspace/revisions/text-01/receipt.json --output work/<N>/workspace/qa/text-01-check.json --json
```

`pptx-inspect --brief` 只把所选页的对象 ID、类型、名称和短文字打印给助手；`--output` 保存含几何与文字运行的完整报告供需要时查看。`pptx-text` 只替换唯一匹配的普通文字 run，保留该 run 原有格式；相同文字多次出现时给出 `--paragraph` 和 `--run` 精确定位。它不重写整段混合格式文字，也不编辑 PowerPoint field。复杂修改使用源哈希绑定的 `pptx-patch`，不要用猜测的 XML 替换。

仅需把一个明确对象左移、右移、上移、下移或调整边缘时，使用 `pptx-nudge`。正的 `dx/dy` 表示右/下；`dwidth/dheight` 只调整右/下边缘，左上角保持不变。命令根据当前 PPTX 对象坐标计算目标值，输出新候选和原有补丁回执，不重跑生成代码，也不覆盖源文件：

```sh
work/<N>/workspace/aippt.sh pptx-nudge work/<N>/workspace/current.pptx --source-sha256 <inspect中的hash> --slide 4 --shape-id <对象ID> --dx-mm -2 --dy-mm 1 --output work/<N>/workspace/revisions/nudge-01 --json
work/<N>/workspace/aippt.sh revision-check --receipt work/<N>/workspace/revisions/nudge-01/receipt.json --output work/<N>/workspace/qa/nudge-01-check.json --json
```

这是一条精确局部修订通道，不代替在 PowerPoint 里直接选中对象后拖动或用方向键微调。若使用原生 PowerPoint 人工/助手界面操作，先在 Work 的 workspace 留一个副本，保存后仍检查改动页和当前文件哈希；不要把鼠标动作或局部结构检查当作整页效果已经通过。

macOS 上另有可选的 `host-nudge`，用已安装的 PowerPoint Apple Events 对一个**名称唯一**的对象做原生移动/缩放。它同样核对源 SHA、页和对象 ID，只打开一个新建的 Work 内尝试副本；保存后按当前 PPTX 对象身份及几何回读，通过才发布 `host-candidate-*.pptx` 和 `host-nudge-receipt.json`。未通过的尝试仍留在隐藏的 `.host-attempt-*.pptx`，并写 `host-nudge-failure.json`，不能当作候选。它不处理多对象组合、文字意义或图片裁切。当前 Mac PowerPoint 对新的暂存文件可能弹出“授予文件访问权限”，因此该入口不是无条件无人值守；若权限未给或宿主故障，可回到上面的 `pptx-nudge` 路线。

```sh
work/<N>/workspace/aippt.sh host-nudge work/<N>/workspace/current.pptx --source-sha256 <inspect中的hash> --slide 4 --shape-id <对象ID> --dx-pt -5 --output work/<N>/workspace/revisions/host-01 --json
```

本机 Work37 的一个既有无客户内容测试文件，已在 PowerPoint 取得该测试副本的文件访问权限后完成真实 AppleScript 保存与 OOXML 几何回读：目标文本框横向移动了 1 pt，原文件 SHA 未变。首次运行的权限弹窗、同名候选文稿冲突也被实际发现，现以唯一候选名称和失败回执处理。这个局部宿主实验只证明所测对象操作，不证明所有 Mac 系统、任意旧稿、视觉质量或完整交付；新任务仍应检查目标整页。
在 Codex 桌面可用且 PowerPoint 已显示目标副本时，助手可以直接读取当前窗口的辅助功能状态与页面画面，或做很小的原生界面调整；这不要求先把每张截图另存到 Work。该入口属于当前桌面会话，独立运行的 Studio CLI 不能自行调用它。画面仍必须被实际读取：PPTX 对象树无法推断真实中文字形、换行、图片裁切或最终可见遮挡。原生界面调整后要保存并重新核对文件 SHA 和实际受影响页面。
快入口拒绝使对象新增或加重越界的移动/缩放；设计上确有出血对象时用显式 `pptx-patch` 并按实际页面解释，不能由一个随手微调跳过。

明确需要补一条新内容时，助手先确定这句话对听众的作用及合适位置，再使用本地新增文本框；几何和可选字体由当前页面判断，不取一个全项目固定模板值：

```sh
work/<N>/workspace/aippt.sh pptx-add-text work/<N>/workspace/current.pptx --source-sha256 <inspect中的hash> --slide 4 --text '面向听众的新增结论' --unit mm --x 30 --y 105 --width 80 --height 14 --output work/<N>/workspace/revisions/add-text-01 --json
work/<N>/workspace/aippt.sh revision-check --receipt work/<N>/workspace/revisions/add-text-01/receipt.json --output work/<N>/workspace/qa/add-text-01-check.json --json
```

可根据已选页面明确传 `--font-size-pt` 和 `--font-name`，不传时保留主题默认。当前快入口只新增一行普通可编辑文字；多段落、混合样式、复杂图表、图片和跨页内容仍使用相应的制作路线。新文本框会得到唯一对象 ID；局部 QA 仍要求查看实际页面的换行、层级和遮挡。

`revision-check` 会验证补丁回执、原稿和候选 SHA-256，逐个核对实际改变的 PPTX 包部件，并只比较目标页的局部质量信号。出现新问题或恶化问题时返回 `needs_review`；`passed_scoped` 只说明局部结构检查未发现新增或加重问题，不是视觉验收。版式、文字内容和层级变化仍要查看实际候选整页，因为结构数据不能证明换行、视觉关系或正式措辞正确。不要为了把一个 `needs_review` 改成绿色而忽略真实问题。

## 发布到当前文件名

候选审阅后需要更新现有 `workspace/current.pptx` 或 `output/<原交付文件名>.pptx` 时，可用 `revision-publish`。它要求修订目录同时含有 `receipt.json`、`fast-revision.json`、`revision-qa.json` 和 `candidate.pptx`，三个证据必须绑定相同的来源/候选哈希，局部 QA 必须为 `passed_scoped`。来源只允许位于当前 Work 的 `workspace/` 或 `output/`；`input/` 来源和 Work 外路径会被拒绝。

发布前先以原稿 SHA 在 `workspace/history/revisions/<SHA>.pptx` 留一份并核验的副本，再以同目录临时文件和原子替换更新原来源路径。因此用户原来的输出文件名和路径不变，修订目录里的候选及 QA 也原样保留。相同修订重复发布会识别已发布状态并幂等返回；若源文件或候选在修订后发生变化则拒绝覆盖。输出的 `publish-receipt.json` 只证明候选已发布且局部 QA 对应，不证明视觉/审美通过；仍需查看受影响整页。

```sh
work/<N>/workspace/aippt.sh revision-publish \
  --work-dir work/<N> \
  --revision work/<N>/workspace/revisions/change-01 \
  --json
```

先对候选的受影响页面完成实际视觉审查，再调用发布命令。该命令核对结构 QA 与文件身份，但不读取、推断或代替视觉审查；回执会明确写入 `native_visual_verified=false` 和仍需检查目标页。它会拒绝修订、来源、候选或备份路径中的符号链接跳转。不要将候选目录移出当前 Work，也不要在原稿哈希变化后强行重试；先对当前文件重新 inspect 并从最新版本建立修订。

替换一张现有的、未共用的静态图片，可用同样的来源哈希和对象 ID：

```sh
work/<N>/workspace/aippt.sh pptx-picture work/<N>/workspace/current.pptx --source-sha256 <inspect中的hash> --slide 4 --shape-id <图片对象ID> --replacement-image work/<N>/workspace/assets/photo-prepared.png --output work/<N>/workspace/revisions/picture-01 --json
work/<N>/workspace/aippt.sh revision-check --receipt work/<N>/workspace/revisions/picture-01/receipt.json --output work/<N>/workspace/qa/picture-01-check.json --json
```

`pptx-picture` 保留图片对象的几何、裁切和效果，只替换它独占的媒体部件；共用媒体、外链、动画图片及会丢失透明度的格式转换会被拒绝。新图片会编码成原媒体格式，不能把“换图”误认为已经核对了页面的图片含义、可见裁切或图框轮廓。

需要视觉反馈时只预览改动页：

```sh
work/<N>/workspace/aippt.sh preview-slide work/<N>/workspace/revisions/text-01/candidate.pptx --slide 4 --output work/<N>/workspace/qa/text-01-slide-4.png --json
work/<N>/workspace/aippt.sh preview-slide work/<N>/workspace/revisions/text-01/candidate.pptx --slide 4 --backend officecli --output work/<N>/workspace/qa/text-01-slide-4-officecli.png --json
```

`preview-slide` 默认使用 Studio 锁定的本地 Office Kit 依赖；`--backend officecli` 则调用 Studio 受管的 OfficeCLI HTML 截图，在部分已有 PPTX 上能保留 Office Kit 预览遗漏的母版元素。两种后端都只写指定页的一张 PNG 和源文件哈希回执，不产生其它页截图。OfficeCLI 默认依据原稿页面比例确定截图高度，必要时可明确传 `--width`、`--height`；`--font-file` 仅供 Office Kit 使用。两个渲染器都是近似预览，中文字形和换行、品牌字体、复杂图表、视频和动画可能与 PowerPoint 不同。预览用于快速定位疑问，关键视觉结果和最终版仍看原生 PowerPoint。依赖缺失时命令明确报错；Office Kit 可在 Studio 核心目录执行 `npm ci` 安装锁定版本，不借用历史 Work 的 `node_modules`。

`preview-slide` 仍可生成带哈希回执的 Work 内部单页 QA 预览。AI 在制作或局部修改中要直接看当前 PPTX 的一页或全稿时，使用更短的 `export-png <current.pptx> --slide N` 或 `export-png <current.pptx>`，并用 `--output` 将内部图片放到 Work QA/workspace 或临时目录；随后直接读取图片，不用鼠标逐张另存，也不先生成 PDF。用户明确要求另存成稿图片时，同一个命令可导出一页或全稿并返回文件。命令只负责图片生成，不替代对页面的实际视觉判断；它使用本地 OfficeCLI HTML 近似渲染，不是 PowerPoint 原生验收。

## 图片在本机处理

```sh
work/<N>/workspace/aippt.sh image-check work/<N>/input/reference/photo.jpg --width 6 --height 3.5 --unit in --output work/<N>/workspace/qa/photo-check.json --json
work/<N>/workspace/aippt.sh image-prepare work/<N>/input/reference/photo.jpg --max-edge 2400 --output work/<N>/workspace/assets/photo-prepared.jpg --json
```

`image-check` 在本机检查可解码性、方向、透明度、近白/透明边缘、实际内容区域及按预计展示尺寸计算的像素密度；数值是诊断依据，不是通用美学阈值。`image-prepare` 新建副本，归一化 EXIF 方向并按比例缩小过大图片，不放大、不裁切、不抠图、不锐化，也不改源文件；多帧图片与不能安全保留透明度的转换会被拒绝。是否需要裁切、生成新图、真实来源或调整页面布局，仍由助手结合图像含义和整页决定。

## 复核节奏

一次快改先确认受影响页和目标对象，再作一次精确操作、跑一次局部检查。审查候选时至少看受影响整页；影响叙事时加看邻页。不要每轮把全部页图发给模型。发布会保留用户原来的 PPTX 路径与文件名。只有本次改动实际影响全稿或原生表现时，才追加全稿表达审阅、原生 PowerPoint 往返或播放验证。

本地预览渲染器如果不是 PowerPoint，只能用来快速发现候选问题。中文字体替换、实际换行、动画、视频和复杂图表仍以目标宿主的原生结果为准。
