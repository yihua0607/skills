---
name: quotation-generator
description: >
  查询 ShanhaiMap 产品 aiCode 接口、整理服务内容，并基于山海图公司报价单模板生成符合格式的 .docx 报价单。
  触发场景：
  - 场景A（新建报价单）：用户提供"服务名-19位编码"(aiCode)，必须先 fetch 查询服务详情/价格/币种/汇率/数量，再走全链路生成报价单。
  - 场景B（修改报价单）：用户未提供 aiCode（如仅更换币种、更换签约主体、增加优惠等），跳过 fetch，直接修改现有 quotation.json 后重新 build。
  支持 6 个签约主体（雅加达/北京/西安/深圳/上海/上海新企业），2 套在用模板，3 种增值税率，2 种币种。
version: "1.4"
last_updated: "2026-07-02"
---

# 山海图报价单生成器

查询服务信息 → 整理服务内容 → 生成标准格式 .docx 报价单。

> **核心要求：核对签约主体、价格、增值税、含税总计，确认页眉与银行信息一致、格式整齐无乱码。**

## 🚨 关键约束速查

以下约束是报价单生成中最容易违反的规则，务必在每一步对照检查：

| # | 约束 | 说明 |
|---|------|------|
| 1 | **不可编造** | 查询失败的服务项不可继续生成，不可编造内容；部分失败须向用户说明并确认跳过 |
| 2 | **总价整数** | 价格只填整数总价，不接受单价；为空/面议须提醒用户补充 |
| 3 | **优惠≤小计** | 优惠只扣减税前小计；增值税 = (小计 − 优惠金额) × 税率 |
| 4 | **name不加×N** | services[].name 写基础服务名，数量写 quantity 字段 |
| 5 | **汇率不入报价单** | 币种换算用脚本完成，不要把汇率换算说明写入客户可见报价单 |
| 6 | **通用不含项入notes** | 快递费/翻译费/差旅费等通用项写 notes 一次，不重复写入 exclude |
| 7 | **价格币种符号** | 人民币 ￥（无空格），印尼盾 Rp（后跟空格） |
| 8 | **币种换算用脚本** | 不要心算汇率，使用 convert_currency.py 脚本完成换算 |
| 9 | **aiCode 必先 fetch** | 用户提供 aiCode → 必须先 fetch 查询；无 aiCode → 跳过 fetch，直接改 quotation.json |

---

## 模板与签约主体

| # | 签约主体 | 公司全称 | --entity | 模板 | 增值税 | 币种 |
|---|----------|----------|----------|------|--------|------|
| 1 | 雅加达 | PT. SHAN HAI MAP | jakarta | 雅加达 | 11% | Rp |
| 2 | 北京 | 北京山海图科技有限公司 | beijing | 中国 | 6% | ￥ |
| 3 | 西安 | 北京山海图科技有限公司西安分公司 | xian | 中国 | 6% | ￥ |
| 4 | 深圳 | 北京山海图科技有限公司深圳分公司 | shenzhen | 中国 | 6% | ￥ |
| 5 | 上海 | 北京山海图科技有限公司上海分公司 | shanghai | 中国 | 1% | ￥ |
| 6 | 上海新企业 | 上海山海图新企业咨询有限公司 | shanghai_new | 中国 | 1% | ￥ |

> 页眉公司名/地址、银行账户信息、签名主体、付款条件均由脚本按 --entity 自动从 config/entities.json 加载。完整参数见 docs/entity_reference.md。

> **通用费用不含项**：config/entities.json 中 `universal_excludes` 定义了以下通用项，应统一写入 notes 通用备注，不要重复写入 fee_details.exclude：
> - 文件翻译费用（如需）
> - 资料快递费用（国际）
> - 差旅费/考察费（如有）

---

## 工作流程

> **⚠️ 流程铁律：用户提供 aiCode → 必须先 fetch；用户未提供 aiCode → 跳过 fetch，直接操作 quotation.json。**

### 步骤 0：判断场景

根据用户输入判断走哪条路径：

| 场景 | 触发条件 | 执行路径 | 示例 |
|------|----------|----------|------|
| **A：新建报价单** | 用户提供了"服务名-19位编码"(aiCode) | **fetch → convert → entity → organize → validate → build → verify**（全链路） | `"帮我查 儿童玩具-SNI认证-2072304344749555713 并生成报价单"` |
| **B：修改报价单** | 用户未提供 aiCode | **直接修改 quotation.json → validate → build → verify**（跳过步骤 1–4） | `"把报价单换成雅加达签约主体"` / `"增加优惠5000"` / `"换成人民币报价"` |

⚠️ **判断要点**：只要用户消息中包含 19 位编码（即 aiCode），就属于场景 A，必须先 fetch。即使同时提到"修改报价单"等词，只要有 aiCode 就走全链路。

---

### 步骤 1：查询服务信息（仅场景 A）

⚠️ **场景 B 跳过此步骤**——用户未提供 aiCode 时，直接修改已有 quotation.json，不需要 fetch。

```bash
python3 scripts/fetch_services.py '交通影响分析-2072516766550704130' '儿童玩具-SNI认证-2072304344749555713'
```

脚本输出规范化字段（服务名称、价格、币种、数量、汇率、清理后的服务内容）。**服务内容已转为 Markdown 格式**，保留了原文的层级、加粗、列表、表格等语义结构，便于 AI 提取。完整结果保存为 JSON 供后续使用。

⚠️ 查询失败时脚本输出结构化错误 JSON（含 `success: false` 和 `services: []`），须向用户转达，**不要继续生成报价单**。对查询失败的服务项，**不可编造内容**。

⚠️ **部分成功处理**：如果多个 aiCode 中部分查询成功、部分失败，脚本输出中 `partial_failure: true` 标记此情况。只使用成功的服务项继续生成，失败的项须向用户明确说明并确认是否跳过。

展示给用户只列：服务名、数量及单位、价格及币种。不展示原始 JSON、不展示 规格/specSnapshot 和 有效期/effectiveEndTime。

### 步骤 2：校验价格与币种（仅场景 A）

- 报价单只接受**总价**，不接受单价。价格为空/面议时，提醒用户补充总价。
- **币种转换使用脚本，不要心算**：

```bash
# IDR → RMB（使用 rateToCny）
python3 scripts/convert_currency.py --amount 250000000 --from IDR --to RMB --rateToCny 0.00046

# RMB → IDR（使用 rateToCny，反算）
python3 scripts/convert_currency.py --amount 115000 --from RMB --to IDR --rateToCny 0.00046

# 批量转换：从查询结果JSON中提取所有服务的汇率自动换算
python3 scripts/convert_currency.py --query-result queried_services.json --to RMB
```

- 转人民币用 `rateToCny`，转美元用 `rateToUsd`（均来自 API 返回）
- 不要把汇率换算说明写入客户可见报价单

### 步骤 3：确定签约主体（仅场景 A）

用户未指定时，列出上方签约主体表让其选择。签约主体决定模板、币种、税率和银行账户。

### 步骤 4：整理服务信息（仅场景 A）

从接口返回的 Markdown 格式**服务内容**中提取结构化字段。

#### 字段提取映射表

| API 返回字段 | 目标数据字段 | 提取规则 | 示例 |
|-------------|-------------|---------|------|
| 含"办理时间"/"工作日" | services[].days | 提取数字+"工作日"；多个时间取最长 | `"22"` |
| "基本信息"/"服务说明"/"服务概述" | services[].note | 100–200字摘要；不足100字完整呈现 | `"根据BKPM规定…未激活将无法合规运营。"` |
| "费用包含"/"服务包含" | fee_details[].include | 每条一项，保留原文 | `["服务费", "资料快递费（本地）"]` |
| "费用不含"/"不包含" | fee_details[].exclude | **仅保留服务特有不含项**；通用项统一入 notes | `["相关资质办理费"]` |
| "办理流程"/"服务流程" | process_data[].process | 每步一条，保留原文序号 | `["第一步：收集资料", "第二步：整理资料（5个工作日）"]` |
| "交付文件"/"交付材料" | process_data[].deliverables | 每项一条 | `["1. 已激活的标准证书/营业执照"]` |
| "所需资料"/"所需材料" | doc_data[].docs | 每项一条，保留子层级编号 | `["1. 公司章程", "2. 司法部批文"]` |

⚠️ **忽略**：付款方式、退款/售后、发票相关。

⚠️ **通用费用不含项精简**：以下通用项统一写入 notes 通用备注一次，fee_details[].exclude 只保留**服务特有不含项**。无特有不含项时填 `[]`。

通用不含项清单（来自 config/entities.json `universal_excludes`）：
- 文件翻译费用（如需）
- 资料快递费用（国际）
- 差旅费/考察费（如有）

> **示例**：API 返回"费用不含：文件翻译费用、资料快递费用（国际）、差旅费/考察费、相关资质办理费"
> → exclude 只写 `["相关资质办理费"]`（服务特有）
> → notes 写 `"以上服务报价不包括：文件翻译费用（如需）、资料快递费用（国际）、差旅费/考察费（如有）。"`（通用）

> 无需询问：报价日期（默认当天，可通过 --quote-date 修改）、客户信息（可留空）、优惠金额（无则填 0）。

### 步骤 5：准备数据并预检

复制 examples/sample_quotation.json（注意 _meta 说明适用实体和币种），按本次服务编辑。

**编辑完成后先运行预检脚本**，确认数据无误再生成：

```bash
python3 scripts/validate_data.py --entity xian --data quotation.json
```

预检脚本检查：必填字段完整性、quantity ≥ 1、name 不含 ×N、价格为整数、优惠 ≤ 小计、名称唯一性、费用/流程/材料覆盖所有服务、通用不含项不在 exclude 中重复、价格量级守卫（RMB/IDR 混用检测）、预估金额汇总。

✅ 预检通过后再运行生成脚本。**不要修改 scripts/build_quotation.py 的生成逻辑。**

```bash
# 所有实体统一使用 --entity 参数
python3 scripts/build_quotation.py --entity jakarta --data quotation.json --output 报价单.docx
python3 scripts/build_quotation.py --entity shenzhen --data quotation.json --output 报价单.docx
python3 scripts/build_quotation.py --entity xian --data quotation.json --output 报价单.docx
python3 scripts/build_quotation.py --entity shanghai_new --data quotation.json --output 报价单.docx
# 自定义标题
python3 scripts/build_quotation.py --entity shenzhen --data quotation.json \
  --title-line1 "印尼投资" --title-line2 "公司注册服务方案" --output 报价单.docx
# 自定义报价日期
python3 scripts/build_quotation.py --entity shenzhen --data quotation.json \
  --quote-date 2026-06-15 --output 报价单.docx
```

| 参数 | 说明 |
|------|------|
| --entity | **必传**：jakarta/beijing/xian/shenzhen/shanghai/shanghai_new |
| --vat-rate | 手动覆盖增值税率（上海实体通过 --entity 自动设 1%，无需传） |
| --title-line1/2 | 标题行（默认"印尼投资"/"综合服务方案"） |
| --quote-date | 报价日期（默认当天，格式 YYYY-MM-DD） |
| --data | 报价数据文件（.json） |
| --output | 输出路径 |

> **重要变更**：--template 参数已移除。模板由 --entity 自动从配置中选择（jakarta 用雅加达模板，其余用中国模板）。

⚠️ **关键约束**（参见顶部 🚨 速查）：
- services[].name 写基础服务名，不加 ×N；数量写 quantity，脚本自动显示
- 优惠只扣减**税前小计**：增值税 = (小计 − 优惠金额) × 税率
- 价格只填整数总价；人民币增值税/含税总计保留 2 位小数；印尼盾全部整数
- 所有金额数字前带货币符号：人民币 ￥（无空格），印尼盾 Rp（后跟空格）

### 数据字段清单

| 字段 | 模块 | 必填 | 说明 |
|------|------|:----:|------|
| services | 服务内容表 | ✅ | 分组列表；每项含 id/name/quantity/days/price/note；name 不加 ×N |
| discount_amount | 费用汇总 | 视 | 整数；无优惠填 0；优惠 ≤ 小计 |
| notes | 通用备注 | 视 | [{"text":"...", "indent":360}]；通用不含项写在这里 |
| fee_details | 费用包含/不含 | ✅ | 每项含 name/include(必填)/exclude(可空[])/note；exclude 只写服务特有不含项 |
| process_data | 流程及交付 | ✅ | 每项含 name/process/deliverables |
| doc_data | 所需材料 | ✅ | 每项含 name/docs |
| doc_notes_text | 材料备注 | 视 | 字符串列表 |

脚本自动校验：必填字段、quantity ≥ 1、name 不含 ×N、价格为整数、优惠 ≤ 小计、名称唯一、费用/流程/材料覆盖所有服务。

### 步骤 6：交付前校对

报价单涉及金额和签约信息，一处错误影响客户信任。**必须完成以下校对后再交付。**

#### 6.1 运行校验脚本

```bash
python3 scripts/verify_quotation.py --input 报价单.docx --data quotation.json
```

脚本自动检查：
- **页眉公司名与银行信息一致性**
- **页眉地址与银行信息一致性**
- **签名公司名与页眉一致性**（动态匹配 entities.json 所有公司名）
- **服务名覆盖完整性**：流程表和材料表是否覆盖所有服务内容表中的服务名
- 金额内部计算一致性（小计、优惠、增值税、含税总计）
- 字体（全文仿宋 FangSong）
- A4 页面尺寸

#### 6.2 打开 .docx 目视确认

- 页眉公司名和地址与银行信息中的公司名和地址**一致**
- 节标题样式一致、表头浅蓝底色、标题蓝色居中
- 表格边框完整、列宽合理、无错位
- 多行内容每项独立成行
- 无乱码/方块/问号

#### 6.3 金额核对

对比脚本 stdout 输出的费用行与 .docx 中实际显示的数字：
- 各服务总价正确、币种正确
- 小计 = 各服务总价之和
- 增值税 = (小计 − 优惠金额) × 税率
- 含税总计 = (小计 − 优惠金额) + 增值税
- 千分位正确

#### 6.4 最终确认

- 报价日期正确（默认当天，允许自定义）
- 签约主体正确
- 如从 aiCode 查询生成，已保存规范化查询结果

#### 6.5 修复指引

校对发现问题时的处理方法：

| 问题 | 修复方式 |
|------|---------|
| 页眉/银行信息不一致 | 检查 --entity 参数是否正确；确认 entities.json 配置 |
| 服务名覆盖缺失 | 在 quotation.json 中补充 fee_details/process_data/doc_data 中缺失的 name 条目 |
| 金额不一致 | 检查 discount_amount、各服务 price 是否为整数总价；重新运行 validate_data.py |
| 通用不含项出现在 exclude | 从 exclude 移除通用项，在 notes 中补充 |
| verify 失败 | **修改 quotation.json 后重新运行 build_quotation.py**，不要手动修改 .docx |
| 价格币种错误 | 使用 convert_currency.py 重新换算；检查签约主体与币种是否匹配 |
| build 报错"Invalid quotation data" | 先运行 validate_data.py 定位具体错误字段，修正后重试 |

> ⚠️ **不要手动修改生成的 .docx 文件**——所有修正应通过修改 quotation.json 后重新运行 build_quotation.py 完成，否则后续 verify 可能无法正确交叉校验。
