# GitHub 上游与 Studio 无侵入接入

更新时间：2026-10-02；历史技术执行证据：Work 17；当前运行身份和每次任务的证明规则见 [UPSTREAM_TOOLS.md](UPSTREAM_TOOLS.md)。

## Work 62 当前状态

下面的完整仓库表是 Work 17 的历史清单。当前活跃源码有 15 个检出；已核对全部登记项目远端 HEAD，更新了其中 12 个活跃检出，3 个已是当前版本。最新提交以 `upstreams.lock.json` 和 `adapter-acceptance/evidence/active-upstream-updates.json` 为准。

未用于当前 PPT 制作的 8 个仓库已从活跃 `github/` 移入 Work 62 的完整恢复区；未获取的 source-han-sans 条目也已退役。源码与调用合同没有混同：退休项不再广告工具或可用字体，OfficeCLI、PptxGenJS、模板、重建、图表和图标能力仍保留。源码更新没有改动主 npm / Python / 扩展运行包锁。当前模板桥和两项源码适配器已通过新回归重新核验调用记录。

PPT Master 当前源码为 6.6.0（`44c10ed0bc3a`）；OfficeCLI 源码为 `7d8f777a34ce`，实际主机二进制当前实测为 1.0.152，本轮未替换该二进制。用户制作流程见 [TEMPLATE_FIRST_WORKFLOW.md](TEMPLATE_FIRST_WORKFLOW.md)。

## 实际边界

`github/` 保留上游的完整干净检出；不向其中写入 Studio 适配器、安装 node_modules、缓存、运行状态或生成的客户文件。新旧适配层全部由 `aippt-studio/` 拥有，任务结果归当前 `work/<N>/workspace/`。上游技能与应用不能接管 Studio 的审批、QA 和交付状态。

本次从 GitHub 确认了原 18 个目录，并新增 4 个相关项目。22 个仓库均使用默认分支、depth=1、单分支、无标签、非 sparse checkout；每个仓库只保留 1 个提交，完整检出 53,667 个受 Git 管理的文件。子模块递归处理，验证未遗留 Git LFS 指针。

源码默认分支 HEAD 与实际运行包是两条独立记录：`upstreams.lock.json` 记录源码提交；主 npm / Python 依赖仍由 `package-lock.json` / `uv.lock` 管理，新增 Mermaid 和 ECharts 运行环境由 `extensions/package-lock.json` 管理。下载最新版源码不会冒充正在运行最新版包。

任何表中的“已接入”“可调用”均只表示 Studio 有受限的调用合同或已通过对应 adapter acceptance，**不是**某个未来 Work 已实际使用该上游。具体任务必须检查其 Work-local `receipt.json`、native/adapter result 与 external-engine manifest；成功运行、QA 通过、页面采用、交付和用户接受仍是独立状态。资料检索和 `planning/upstream-context.json` 也只记录参考，不是执行记录。

## 仓库目录

| 目录 | GitHub 仓库 | 提交 | 当前用途 / 接入程度 |
| --- | --- | --- | --- |
| GSAP | [greensock/GSAP](https://github.com/greensock/GSAP) | `13e2b7905464` | 浏览器动效候选，非 PowerPoint 原生动画接口 |
| LxgwWenkaiGB | [lxgw/LxgwWenkaiGB](https://github.com/lxgw/LxgwWenkaiGB) | `7e280c0880f6` | 本地字体源与字体文件；按任务选择使用 |
| OfficeCLI | [iOfficeAI/OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) | `b94f3906fd52` | 受管主机二进制负责 QA；GitHub 源码供版本/接口评估，具体调用须有输入绑定 receipt |
| PPTAgent | [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) | `2419d30b134a` | 独立应用候选，需配置模型与隔离服务 |
| PptxGenJS | [gitbrent/PptxGenJS](https://github.com/gitbrent/PptxGenJS) | `3c9ec1b687c1` | 已有 npm 运行库；补齐源码与版本登记 |
| Presenton | [presenton/presenton](https://github.com/presenton/presenton) | `66c7ff25f3ba` | 独立应用候选，需配置模型与隔离服务 |
| academic-pptx-skill | [Gabberflast/academic-pptx-skill](https://github.com/Gabberflast/academic-pptx-skill) | `9f2b703ffe8d` | 许可证声明冲突，保留评估并排除自动调用 |
| awesome-ai-ppt | [ningzimu/awesome-ai-ppt](https://github.com/ningzimu/awesome-ai-ppt) | `39b36dfd52bb` | 项目发现资料库 |
| codex-ppt-skill | [ningzimu/codex-ppt-skill](https://github.com/ningzimu/codex-ppt-skill) | `f2ed80372f65` | 直接源码 visual adapter 已验收；仅 adapter receipt 与前后 Git 完整性可证明实际任务执行 |
| dom-to-pptx | [atharva9167j/dom-to-pptx](https://github.com/atharva9167j/dom-to-pptx) | `c9f7519db743` | native 路线使用独立锁定的 Studio npm 运行包；GitHub checkout 是源码溯源，不是 runtime |
| echarts | [apache/echarts](https://github.com/apache/echarts) | `018ed3998984` | 新增可运行的 support chart，输出 SVG |
| editable-leadership-pptx | [CamelKing1997/editable-leadership-pptx](https://github.com/CamelKing1997/editable-leadership-pptx) | `392d4043f63c` | 中文汇报策略参考，未自动套用内容 |
| icon-sets | [iconify/icon-sets](https://github.com/iconify/icon-sets) | `ad784891fdb4` | 本地图标资产目录；逐集合保留许可证 |
| image-to-editable-ppt-skill | [ningzimu/image-to-editable-ppt-skill](https://github.com/ningzimu/image-to-editable-ppt-skill) | `fb869763127f` | 直接源码 reconstruction adapter 已验收；具体任务仍须 adapter receipt 与前后 Git 完整性 |
| markitdown | [microsoft/markitdown](https://github.com/microsoft/markitdown) | `b6e8bbdce628` | 已有 Python 包负责资料导入；补齐源码登记 |
| mermaid-cli | [mermaid-js/mermaid-cli](https://github.com/mermaid-js/mermaid-cli) | `48615db4f646` | 新增可运行的 support diagram，输出 SVG |
| motion-canvas | [motion-canvas/motion-canvas](https://github.com/motion-canvas/motion-canvas) | `7b91435c301d` | 视频 / 解释动画候选，尚未接入制作路线 |
| ppt-master | [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) | `c45b7427e707` | 受限的 SVG/内容提取/native-motion 模块可调用；一个模块 receipt 不代表整个仓库都被执行 |
| pptx-automizer | [singerla/pptx-automizer](https://github.com/singerla/pptx-automizer) | `62b6790d480f` | template 路线使用 Studio 锁定的 `0.8.2` npm runtime；checkout 只作源码溯源，运行身份在 adapter receipt 中记录 |
| remotion | [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | `5a93463ea0df` | React 视频引擎候选，保留其自定义许可边界 |
| remotion-bits | [av/remotion-bits](https://github.com/av/remotion-bits) | `6c71169aa061` | Remotion 组件候选，依赖独立视频运行环境 |
| smiley-sans | [atelier-anchor/smiley-sans](https://github.com/atelier-anchor/smiley-sans) | `cbf2ce4a2793` | 本地字体设计源；不等同于已安装字体 |

## 查询与验证

在项目根目录执行，使用当前 Work 的 wrapper：

```sh
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 upstreams --json
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 upstreams --id codex-ppt-skill --check --json
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 upstreams --check --json
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 doctor --json
```

`upstreams --check` 核对 HEAD、origin、工作区干净状态和锁文件。缺失已获取的仓库、错误来源、源码修改、版本不一致均返回非零退出码。旧锁中的 `source-han-sans` 仍是一个未获取的 pending 字体参考，未把它算入本次 22 个仓库，也不把它当作可用资产。

`codex-ppt-skill` 和 `image-to-editable-ppt-skill` 属于直接执行源码的适配器：调用前还检查 Git HEAD 与干净状态；换提交或改源码后会降级，必须重新验收。源码执行关闭 Python 字节码写入。运行产物仍必须经过 Studio 共同门禁，图片组装永远标记 image_based。

## 新增：图表与流程图

新增运行库位于 `.tools/extension-runtime`，与主制作链及上游源码隔离：ECharts 6.1.0、Mermaid CLI 11.17.0。可按锁文件重装：

```sh
./aippt-studio/scripts/setup-extensions.sh
```

安装使用 `npm ci --ignore-scripts`，Mermaid 使用本机 Chrome，不下载另一个浏览器。

```sh
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 support diagram work/17/workspace/smoke/flow.mmd --output work/17/workspace/smoke/flow.svg --json
./scripts/pwsh.sh -NoProfile -File work/17/workspace/aippt.ps1 support chart work/17/workspace/smoke/chart.json --output work/17/workspace/smoke/chart.svg --json
```

图表输入格式：

```json
{"width":1280,"height":720,"option":{"xAxis":{"type":"category","data":["甲","乙"]},"yAxis":{"type":"value"},"series":[{"type":"bar","data":[10,20]}]}}
```

只接受数据 JSON，不执行函数或 JavaScript 字符串。输出必须在当前 Work workspace 内；使用暂存文件，失败时保留之前的有效图。每个 SVG 同时生成 `.provenance.json`，记录输入、输出、运行包版本及锁文件哈希。进入客户稿时仍要登记资产用途与来源。SVG 是图形资产；导入方式决定它在 PPT 内是图形还是可逐对象编辑的内容，不能一概宣称是原生可编辑图表。

## 新版重建接口

新版 editppt 的准备标识是 `deck_manifest.json`；已准备任务再次调用不会重置页面状态。新任务显式选择 `builtin-imagegen` 后端，避免借用上游默认的用户级 OAuth / API 选择逻辑；prepare 不调用云模型。

最终文件由 deck_manifest 的 output 指定，Studio 检查文件位于该 Work run 内、run_state / run_summary 为 complete、validation.passed 为 true、三个记录的页数一致后导入。旧任务的 final.pptx + final-manifest.json 交接形式保留。仍需真实逐页重建、上游 finalize 与 Studio 公共 QA，prepare 成功不表示重建完成。

## 尚未自动接入的项目

Presenton / PPTAgent 是独立应用，需要模型配置和服务隔离；Remotion / Motion Canvas / GSAP 属于视频或网页动效能力，不能直接替代 PowerPoint 原生动画。当前提供完整源码、版本、入口与接入定位，未自动启动服务、下载模型或发起付费调用。

Academic PPTX 的 LICENSE 为 MIT，但 SKILL.md 仍宣称 Proprietary 并指向 LICENSE.txt，因此保留冲突记录，暂不自动复用。GSAP 与 Remotion 保留各自自定义许可；Iconify 图标必须读取所用集合的独立许可。许可证元数据在本地锁文件中逐项记录。

## 更新方式

本次删除旧目录的操作已获用户明确授权；删除前清单与 SHA-256 在 Work 17 的 qa/deleted-*-manifest.json，源码重克隆日志为 qa/clone-*.log。未来更新时先确认仓库身份和网络可达、检查本地改动，再更新完整浅克隆；不盲目删除其他目录，不自动启用上游仓库里的脚本或配置。

本机 Git 不自动读取系统代理，首次直连失败。本轮命令使用当前系统已启用的本地代理完成克隆，未修改全局 Git 设置。该地址只在 Work 17 的任务执行脚本中使用；换机器应读取本机配置，不能把该端口当作产品固定依赖。

## SVG 进入 PPT 的实测补充

相对 SVG 文件路径在浏览器预览可见，但当前 HTML-to-PPTX 运行包会漏掉图形。support 现在同时输出 `.embed.html`，其中的图像使用自包含 data URI；将该 img 元素放进 Studio 页面后，PowerPoint 导出中流程图与图表均可见。原始 SVG 继续保留。当前 renderer 将这两项图形嵌入为图片，不能标为可逐数据点编辑的 PowerPoint 原生图表。两页测试稿已通过前后两次 OfficeCLI、PowerPoint 保存/重开/PDF 导出以及最终逐页视觉核对。


## 2026-09-07 后续：进入制作流程

统一工具调用、素材回接、PptxGenJS 正式原生制作、SVG 转换、设计资源上下文与真实可用性边界见 [UPSTREAM_TOOLS.md](UPSTREAM_TOOLS.md)。这份新增说明覆盖上文中仍标为 catalog-only 的已升级能力；未配置的模型/视频项目仍不能视为已运行。
