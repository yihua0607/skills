# 高复杂度异常边界

以下异常的处理方案从 SKILL.md『修复与异常边界』表拆出，正文表格只保留触发条件 + 一句话要点，完整处理见本文档对应小节。

## 外币 → 外币（API 没有直接汇率）

`rateToCny` / `rateToUsd` 的定义是「外币 ↔ 人民币 / 美元」，因此：

| 场景 | 处理 |
|------|------|
| 外币 → 人民币 | 该外币 `rateToCny`（÷） |
| 外币 → 美元 | 该外币 `rateToUsd`（÷） |
| 人民币 / 美元 → 外币 | 同一汇率反向（×） |
| 人民币 ↔ 美元 | 一条外币的两个汇率做桥，不用问用户 |
| **外币 → 外币**（两侧都不是人民币/美元，如 THB→VND、MYR→SGD） | **API 没有汇率数据**：脚本会拒绝并提示索取。必须**向用户索取**（如「1 THB = N VND」），拿到后 `--cross-rate N`（1 `<from>` = N `<to>`）；若用户给的是反向汇率（1 VND = M THB），取 `1/M` 再传。**不得自己估汇率** |
| 币种不在 8 个币种内（如 HKD） | 只要显式传汇率脚本就放行（`--rateToCny` / `--rateToUsd`）；不传则报 `Unknown ... currency` |

8 个币种（IDR / RMB / USD / SGD / THB / VND / EGP / MYR）的所有「外币 ↔ 人民币/美元」换算都由 `convert_currency.py` 完成，取整 ROUND_HALF_UP，**不允许心算**。

## 分享链接而非 aiCode

山海图分享链接（`#/products/productDetailInner?sharingRecordId=xxx`）中的 `sharingRecordId` 不是 aiCode，无法用于 `aiCode/resolve` API（返回"AI Code 无效"）。除 `aiCode/resolve` 外的所有产品接口均需登录认证。**不要反复尝试不同 aiCode 组合**——直接告知用户分享链接不可用，要求提供完整的 `服务名-19位数字编码` 格式 aiCode。若是规格化产品（如 ODI 按投资额/股东数定价），同时向用户确认所选规格和价格。

## rateToCny 异常低（服务编码非 ID 前缀）

`rateToCny` 与 IDR 严重不匹配（如 0.6、4.0 而非 ~2250），且服务编码以 MY/VN/SG/TH/EG 等国家前缀开头 → API 币种标记错误，实际为对应国家币种（如 MY=马币、VN=越南盾）。**向用户展示异常**，结合服务编码前缀与同批次服务汇率一致性判断真实币种，确认后按实际币种换算。`_meta` 中注明此异常及判断依据。

## Markdown 格式不一致导致提取失败

API 返回的服务内容中，章节标题格式极不统一：`****费用包含：****`、`****费用包含********：****`、`费用包含：`（无星号）、`****费用不包含：****`（非"费用不含"）等。正则提取（如 `r'费用包含[：:]*\s*\n'`）频繁失败。**方案：使用 `str.find()` 定位关键标记 + 截取文本的方式更可靠**，忽略中间星号。编写 `extract_list(content, start_marker, end_marker)` 函数，基于 `find()` 而非正则。

## doc_data[].docs 为空

部分服务"所需资料"下直接写"无"——API 未返回资料清单。提取结果为 `[]` 会触发 `docs is required and must be a non-empty list`。**必须补兜底**：`docs = extract_list(...) or ["请咨询山海图获取详细资料清单"]`。
