# 页眉、模板、打印与页脚

仅在新增/替换页眉图片、调整版式、修改模板或排查 Word/WPS 渲染问题时读取。

## 全局布局

build 会把页面归一为 A4 纵向（11906×16838 DXA）及标准页边距：上下 679/1155、左右 1440/1133 DXA，页眉/页脚 227 DXA。正文表格和蓝线略宽于文本栏，但距纸张边缘仍须满足打印安全范围。

所有主体必须配置：

```json
"header_image": {
  "file": "页眉-公司.png",
  "bbox_px": [x0, y0, x1, y1]
}
```

`bbox_px` 必须是该 PNG 实测 alpha 边界，换图后重新测量，不能照抄其他主体。

全局参数位于 `_meta.header_image_defaults`：

- `ink_top_mm`：裁剪后图片框顶距纸张上缘；当前 6.0mm。
- `line_gap_mm`：裁剪后图片框底到蓝线；当前 1.0mm。
- `body_top_mm`：正文首行距纸张上缘；当前 30.0mm。
- `crop_padding_px`：在 alpha 边界四周保留的原图白边；当前 20px。

因为配置了 `crop_padding_px`，裁剪后的图片框包含白边，**不等于**实际图墨边界。图片可见内容会在框内留出约 1mm 呼吸空间。

## OOXML 实现约束

- 横幅使用浮动锚定 `wp:anchor` + `wp:wrapNone`。
- `a:srcRect` 按 `bbox_px` 加 `crop_padding_px` 裁剪；PNG 原文件不得改动。
- 横幅和蓝线必须锚在同一页眉段，两个 run 使用相同 `rPr`。
- 模板页眉只能保留蓝色分隔线骨架，不得残留文字、独立 logo、旧图片关系或 `media/image1.png`。
- 图片框不得越出纸张或正文栏，也不得压到正文首行。
- `ink_top_mm` 不得低于 `MIN_PRINTABLE_INK_TOP_MM`。

锚点落点包含针对渲染器标定的漂移常量：`HEADER_IMAGE_INK_DRIFT_MM` 与 `HEADER_IMAGE_LINE_DRIFT_MM`。不要用透明框伸出纸张的方式调整视觉位置；Word、WPS 与 LibreOffice 对浮动锚点的解释可能不同。

## 页脚页码

`PAGE` 与 `NUMPAGES` 必须位于正常页脚段落，不得放进文本框。文本框属于独立 story，Word/WPS 可能把总页数显示为当前页码，出现 `1 / 1`、`2 / 2`。

不要把总页数写成静态文本。`verify_quotation.py` 会检查页码字段位置。

## 检查

修改页眉、模板或版式后至少运行：

```bash
python3 -m unittest \
  tests.test_smoke.TestQuotationSmoke.test_all_current_entities_use_image_headers \
  tests.test_smoke.TestQuotationSmoke.test_all_entity_templates_only_keep_the_blue_line_skeleton \
  tests.test_smoke.TestQuotationSmoke.test_image_header_replaces_text_header_with_anchored_banner
```

然后生成一份报价单并运行 `verify_quotation.py`，最后目视检查页眉图、公司名、地址、蓝线、正文和页码。
