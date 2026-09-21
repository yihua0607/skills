# 服务查询与错误排查

仅在 fetch、aiCode、接口结果或 `queried_services.json` 异常时读取。

## aiCode

- 只接受完整的 `服务名-19位数字编码`。
- 纯 19 位编码要求用户补服务名。
- 服务名中的空格必须与数据库一致，例如 `JSHK 账户维护` 不能写成 `JSHK账户维护`。
- 山海图分享链接（`#/products/productDetailInner?sharingRecordId=xxx`）里的 `sharingRecordId` 不是 aiCode，不能用于 resolve API（返回「AI Code 无效」），除 resolve 外的产品接口都需要登录。**不要反复尝试不同的 aiCode 组合**，直接告知用户分享链接不可用，要求提供完整 aiCode；若是规格化产品（如 ODI 按投资额/股东数定价），同时确认所选规格和价格。

## 批量查询

多个 aiCode 可分参数或用英文逗号传入。脚本会按首次出现顺序去重，每批最多 20 个，并在合并后核对缺失、额外和重复结果。

完整 stdout 保存为 `queried_services.json`。不得把 stderr 合并进去。

## 失败边界

以下情况立即中断：

- API 顶层 `success: false`：原样展示 `message`。
- 任一服务内部失败：列出 aiCode 和原始错误，不得只用成功项继续。
- 顶层成功但没有服务。
- 服务详情为空，或仅返回“点击对应规格查看业务完整详情”等引导语。

价格面议或缺价不是 fetch 失败；向用户补充整数总价后继续。

## stderr 污染 JSON

`markdownify` 缺失时，警告写到 stderr，正常 `>` 重定向不会污染 JSON。只有使用 `2>&1` 才会混入。

`convert_currency.py` 报 `Cannot read query result file` 时：

1. 首行不是 `{`：重新 fetch，只把 stdout 写入文件；如确需合流，按内容过滤 `^⚠️`。
2. 首行是 `{`：文件未写完整或 fetch 失败，重新查询。

不要用 `tail -n +2` 盲删首行，正常 JSON 第一行就是 `{`。

## 其他返回异常

- `Missing price or currency` / `Cannot parse price`：保留查询结果，向用户索取价格。
- Markdown 章节格式不一致：按 [edge-cases.md](edge-cases.md) 的“Markdown 章节格式不一致导致提取失败”处理。
- API 币种与服务编码明显矛盾：按 [currency-conversion.md](currency-conversion.md) 的“常见异常”处理，不得自行猜测。
