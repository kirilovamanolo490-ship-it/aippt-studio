# 参考稿拆解与原生重建示例

本例只保留 Work 57/58 可迁移的方法：先盘点参考页，再由人工编写新内容计划，最后用空白页面创建基础原生对象。它不会复制原页文案、形状 XML 或整页截图。

```bash
python inspect_reference.py /path/to/reference.pptx \
  --output /tmp/reference-inventory.json --media-dir /tmp/reference-media
python build_native.py /path/to/authored-plan.json /tmp/new-deck.pptx \
  --media-dir /tmp/reference-media
```

默认清单不写源文案，只记录画布、对象类型和位置尺寸、文字长度/段落数/显式字号、启发式标题或正文提示、图片 SHA-256 和裁切参数。只有显式传 `--include-text` 才会记录原文。独立图片以内容哈希命名，能作为人工制作计划的输入；哈希不说明图片含义、授权或适用性。

`build_native.py` 从空白演示文稿创建 `text`、`rect`、`picture` 三种对象。文字支持多段与逐 run 字体、字号、粗斜体、颜色；图片默认等比居中裁切，显式裁切与目标比例不符时拒绝拉伸；输出保存后重开并检查页数。计划需明确写入当前任务的新文案、几何、颜色和素材哈希。缺失素材或未支持对象类型会报错。

组、表格、图表、SmartArt、连接关系、动画、母版继承与复杂渐变没有自动迁移。要保留这些效果，须针对实际页面另行重建和审阅。来源版式的结构统计只帮助定位候选；不代表视觉验收、客户内容适用、字体可用或全库风格自动复刻。
