# 图片的表面、裁切和边框

共享实现是 `node/image-geometry.cjs`。原生构建、HTML 导出和模板图片操作调用同一实现，图片的 fit、crop、mask 和 border 描述同一个表面。原始媒体保持嵌入，最终 PPTX 使用原生图片的裁切与几何属性。它不自动把任意两个相近对象认作一组。

## 制作入口

- `node/author-native.cjs` 的图片元素使用 `geometry`，位置和尺寸仍在 `options.x/y/w/h`。不要继续使用 PptxGenJS 的 `rounding` 代替圆角；该选项实际是椭圆语义。
- `SlideSpec.asset_geometry` 以本页准确的 `asset_ref` 为键。HTML 渲染写入对应图片的 `data-image-geometry`；自定义 HTML 使用同名 JSON 属性。导出前生成一致的图片表面，导出后恢复原始媒体和原生属性。
- `node/template_adapter.mjs` 的页映射支持 `image_geometry` 操作列表，每项需要准确 `shape_id`、嵌入原图的 `source_sha256` 和 `geometry`，可选 `box`。未指定的图片保持原状。它是模板构建操作；不能把它写成任意 `pptx-patch` 都支持的未实现字段。

用于完整截图、证件和其它需要保持全部内容的图片：

```json
{"fit":"contain","mask":"rect"}
```

用于已经根据本页用途判断可裁切的照片：

```json
{
  "fit":"cover",
  "mask":"roundRect",
  "radius":0.12,
  "position":{"x":0.5,"y":0.5},
  "allow_crop":true,
  "crop_reason":"本页使用照片主体，已检查裁切保留全部关键人物与动作",
  "border":{"color":"FFFFFF","width_pt":1}
}
```

`radius` 是短边比例，示例不是全局设计规范；`position` 是剩余空间或裁切的定位比例。显式 `crop` 包含 `left/top/right/bottom` 四个原图比例。`mask` 可为 `rect`、`roundRect`、`ellipse`；`fit` 支持等比 `contain` 和 `cover`，不提供静默拉伸。遮去源图的操作必须由作者填写用途与裁切理由，含 `cover`、非矩形蒙版或显式裁切；这是制作数据要求，**不是再次询问用户许可**。未经判断的证据图使用完整保留方式。

非矩形蒙版与 `contain` 组合时，裁切后的源图长宽比必须与图片框一致。比例不同会留下内层矩形图像的直角，因此执行层明确拒绝这种组合。作者可根据照片用途改为 `cover`，或使用 `rect` 完整展示证据；同长宽比的非矩形 `contain` 仍可用。

## 审阅与限制

构建的 `image-geometry.json` 保存源媒体哈希及最终几何。原生与 HTML 入口会提示未绑定的曲面外框。OOXML 检查把同范围的矩形图片和曲面底形加入 `IMAGE_FRAME_CONTOUR_MISMATCH`；正式 `expression-review` 要求助手查看真实页面并记录处置。提示只定位可能的问题，不能自动删除外框或替用户批准视觉效果。

原生或模板图片已有的旋转、翻转及有限负坐标予以保留；遮罩和边框随图片的原生变换保持一致。模板里的分组、带变换的 HTML 图片或不支持的图像格式需要明确支持的转换后才能调用对应操作；失败时保留原文件，不能悄悄替换为有损截图。PNG/JPEG/SVG 是当前共享合同支持的格式，其他格式保留原有处理或先进行明确转换。是否变得协调、主体是否完整、蒙版是否符合本页表达，仍要查看实际页面。结构测试通过不等于原生 PowerPoint 已验收。
