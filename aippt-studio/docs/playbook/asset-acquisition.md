# Work 内联网素材获取与来源记录

联网素材是任务资产，不是全局素材库。每次任务先检查本地输入、模板库和已授权资产；只有存在明确视觉缺口时才联网检索。下载结果、来源和许可证必须留在当前 Work 的 Studio job 中。

## 标准流程

1. 完成 deck plan 后逐页检查图片、图标、Logo、地图、纹理和插画缺口。
2. 用 `asset-need` 为每个真实缺口登记页面、类型、检索词、用途及是否阻塞。
3. 优先使用本地已授权资产。确需联网时，可由 Studio 的 Commons 搜索接口直接返回候选；其他网站来源也可由当前可用的网页检索能力查找，不把外部服务密钥或 SDK 状态写入 Studio job。
4. 优先选择原始发布者、官方素材库或许可证页面清晰的来源。记录素材直链和独立的来源/授权页面。
5. 对 Wikimedia Commons，可先用 `asset-search-commons` 从登记过的素材需求中搜索每文件候选。图标、Logo、插画查询 `drawing`，照片、地图、纹理查询 `bitmap`；结果再按实际图片 MIME 过滤。候选包括文件说明页、HTTPS 素材直链、许可证元数据和署名，并作为小型内容哈希绑定的检索票据存入当前 job。逐件核对文件说明页，尤其是多许可证、缺作者和公共领域声明。未知许可证返回 `NOASSERTION`，不可自动获取。
6. 明确选择候选并核对说明页后，用 `asset-pick-commons` 指定检索票据和候选序号；无需反复复制下载 URL、来源 URL、许可证和署名。该命令拒绝票据被改写、需求的页、素材类型、用途或检索词改变、未知许可及缺作者的 CC-BY 候选；变更需求后必须重新搜索。其他已核准的来源仍用 `asset-fetch`。Studio 在联网前检查 cloud permission 和许可证，在下载时阻止私网/重定向风险，随后校验文件、哈希和 SVG 主动内容。搜索不会自行选择、下载或插入候选。
7. `asset-status` 的 `pending_required` 必须为 0，才能批准 prototype 或 final。
8. 若设计改变而不再需要素材，使用 `asset-skip` 并写明原因，不删除需求记录。

## CLI

```bash
./scripts/work-studio.sh <N> asset-need <job-id> \
  --slide slide-03 --kind icon --query "企业增长 线性图标" \
  --purpose "强化增长结论" --json

./scripts/work-studio.sh <N> asset-search-commons <job-id> \
  --need <need-id> --limit 8 --json

./scripts/work-studio.sh <N> asset-pick-commons <job-id> \
  --need <need-id> --search-id <search_id-from-search> --candidate 1 \
  --license-review-note "已核对文件说明页中的许可和作者" \
  --selection-note "素材与当前页的主张和构图相符" --json

# 非 Commons 来源，或文件页需补齐作者信息时：
./scripts/work-studio.sh <N> asset-fetch <job-id> \
  --need <need-id> \
  --download-url "https://.../icon.svg" \
  --source-url "https://.../license-or-asset-page" \
  --license "CC-BY-4.0" --attribution "Author or collection" \
  --selection-note "与页面线条和含义一致" --json

./scripts/work-studio.sh <N> asset-status <job-id> --json
```

Windows 上使用同一 Work 的 `workspace/aippt.ps1` 和等价参数。

## Work 内位置

```text
work/<N>/workspace/studio-jobs/<job-id>/
├─ planning/asset-plan.json
├─ planning/asset-searches/commons-<sha256>.json
└─ assets/
   ├─ asset-ledger.json
   └─ acquired/<asset-id>.<ext>
```

Commons search uses the official MediaWiki Action API (`generator=search` and `prop=imageinfo`) and returns candidate metadata only. The API's `extmetadata` is per-file metadata, but it can be incomplete or ambiguous for multi-licensed files. The search ticket binds the exact result used by `asset-pick-commons`; it is not proof of current license terms or visual suitability. The operator must inspect the file description page before recording the review note and selecting an image.

最终交付包保留 `asset-plan.json`、`asset-ledger.json` 和 `assets/acquired/`，HTML 中使用相对路径，不保留本地 `file://` URI。

## 强约束

- `cloud_allowed = false` 时，不进行网页检索或下载。
- 未知、冲突、`NOASSERTION`、无许可素材不得自动下载和使用。
- 不把搜索结果页截图当作素材，不从聚合预览图盗链，不下载带水印的候选图冒充正式素材。
- 不把本地路径写入 PowerPoint 备注；路径只存在于 asset ledger、evidence/QA 和构建记录。
- 不因“需要装饰”而强制给每页配图。只有能增强语义、证据或构图的素材才登记为需求。
