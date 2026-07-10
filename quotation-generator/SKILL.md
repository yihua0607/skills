---
name: quotation-generator
version: 1.8.0
description: >
  山海图公司报价单生成器。新建：用户提供 aiCode → fetch → 生成 .docx。
  修改：用户未提供 aiCode → 基于既有 quotation.json 修改后重建。
  支持 6 签约主体、IDR/RMB/USD 三种报价币种。
last_updated: "2026-07-10"
---

# 山海图报价单生成器

查询服务信息 → 整理服务内容 → 生成标准格式 `.docx` 报价单。核心目标：签约主体、币种、价格、增值税、含税总计、页眉/银行/签名一致，且版式无错乱乱码。

## 硬规则速查

### 数据规则

| # | 规则 | 要点 |
|---|------|------|
| 1 | 失败即中断 | 查询失败（API 层/服务层/空结果）→ 展示 API message 原文，不继续生成 |
| 2 | 价格为整数总价 | 不接受单价；为空/面议时要求用户补充总价 |
| 3 | 优惠只扣税前小计 | 增值税 = (小计 − 优惠金额) × 税率；优惠 ≤ 小计 |
| 4 | 服务名不带数量 | `services[].items[].name` 写基础服务名；数量写 `quantity` |
| 5 | 汇率留档但不入报价单 | API 返回的 `rateToCny/rateToUsd` 随 `queried_services.json` 和 `_meta` 留档；客户可见报价单不写汇率说明 |
| 6 | 公共不含项去重 | 多个/全部服务共同适用的费用不含项抽取到 `notes`；各服务 `exclude` 只留服务特有项 |
| 7 | 金额符号 | 人民币 `￥` 无空格；印尼盾 `Rp ` 后跟空格；美元 `$ ` 后跟空格 |
| 8 | 付款方式只提醒 | 付款方式留给用户最终处理；脚本只检查明显不合理项并 warning，例如付款比例合计 >100%、付款金额合计 > 合同含税总计 |

### 流程规则

| # | 规则 | 要点 |
|---|------|------|
| 1 | aiCode 必先 fetch | 只接受 `服务名-19位数字编码`；脚本会将完整字符串作为 `aiCodes` 请求参数；不截取末尾 19 位数字编码 |
| 2 | 手改 `.docx` 优先 | 用户提供/说明手改过报价单时，先保留客户可见内容，再重新 build；付款方式由 build 自动从旧 `.docx` 保留，不必写回 `quotation.json` |
| 3 | 输出位置 | 最终生成的 `.docx` 必须放在用户当前工作目录；`quotation.json`、`queried_services.json` 等过程文件写入用户工作目录下的独立留档目录；不写 skill 根目录；修改已有报价单时输出新文件 |
| 4 | Agent 不改脚本 | 执行报价任务不得修改 build/validate/verify/fetch 等脚本；业务/数据问题按业务处理，只有严重脚本缺陷才提示联系 SKILL 开发者 |

## 签约主体

| 签约主体 | `--entity` | 增值税 | 默认币种 | 可切换币种 |
|----------|------------|--------|----------|-----------|
| PT. SHAN HAI MAP (雅加达) | `jakarta` | 11% | IDR | RMB, USD |
| 北京山海图科技有限公司 | `beijing` | 6% | RMB | USD |
| 北京山海图科技有限公司西安分公司 | `xian` | 6% | RMB | USD |
| 北京山海图科技有限公司深圳分公司 | `shenzhen` | 6% | RMB | USD |
| 北京山海图科技有限公司上海分公司 | `shanghai` | 1% | RMB | USD |
| 上海山海图新企业咨询有限公司 | `shanghai_new` | 1% | RMB | USD |

完整银行账户、地址、税号等详见 `docs/entity_reference.md`。实体配置源文件见 `config/entities.json`。标题、日期、客户、合同号、付款条件写入 `quote_meta`。

## 场景判断

```bash
USER_WORKDIR="$(pwd)"
WORKDIR="$USER_WORKDIR/quotation-YYYYMMDD-client" && mkdir -p "$WORKDIR"
cd <skill-root>
```

| 场景 | 条件 | 路径 |
|------|------|------|
| A 新建 | 用户提供 aiCode，工作区无 quotation.json | fetch → organize → validate → build → verify |
| B 修改 | 用户未提供 aiCode | 定位 → 保留手改/快照对比 → 修改 → validate → build → verify |
| C 追加 | 用户提供 aiCode + 既有 quotation.json | 定位 → 保留手改/快照对比 → fetch 新服务 → 合并 → validate → build → verify |

找不到既有 quotation.json 时要求用户提供，不从 sample 凭空改。

## 手改 `.docx` 保留

付款方式/付款条件属于客户可见手改内容。初次生成的默认付款方式只作占位，后续用户手动修改或让 Agent 修改后，重新生成服务、金额、优惠时不得把付款方式还原为 `quotation.json` 或实体默认值。脚本只做合理性提醒，不因付款方式 warning 阻断生成。

`build_quotation.py` 默认行为：
- 如果 `--output` 指向已存在的 `.docx`，自动从该旧文件读取付款方式并用于本次重建。
- 如果输出到新文件但需要沿用某个旧报价单的付款方式，传 `--preserve-payment-from "$WORKDIR/已编辑报价单.docx"`。
- 只有明确要用 `quotation.json` / 实体默认付款方式覆盖旧文件时，才传 `--overwrite-payment-terms`。
- 如果指定了旧 `.docx` 但付款方式提取失败，必须立即中止；不得回退到 `quotation.json` 或实体默认付款方式，也不得覆盖旧文件。
- Agent 必须向用户询问并确认付款方式。用户明确告知后，将其写入本次工作目录的 `quote_meta.payment_terms`，并传 `--overwrite-payment-terms` 重新生成；这不属于从旧文档同步数据。
- `validate` / `build` / `verify` 会提醒明显不合理的付款方式：付款比例合计超过 100%，或付款金额合计大于合同含税总计。

```bash
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$USER_WORKDIR/报价单.docx"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --preserve-payment-from "$WORKDIR/已编辑报价单.docx" --output "$USER_WORKDIR/新版报价单.docx"
```

`sync_payment_terms.py` 仅作为兼容工具保留；一般不需要使用。其他任何可能包含手动修改的 `.docx`，仍走快照流程：

```bash
python3 scripts/extract_docx_snapshot.py --input "$WORKDIR/已编辑报价单.docx" --output "$WORKDIR/docx_snapshot.json"
```

Checklist：
1. 生成 `docx_snapshot.json`。
2. 与 `quotation.json` 对比标题、日期、客户、合同号、付款条件、服务、金额、费用包含/不含、流程、交付、材料、备注。
3. 明确可映射的差异写回结构化数据：报价单元信息写 `quote_meta`，业务内容写对应数据段。
4. 无法判断业务含义时，用自然语言向用户确认。
5. 确认前不得重新 build 覆盖；build 时输出新 `.docx`。

## 查询服务信息（场景 A/C）

```bash
python3 scripts/fetch_services.py '一般纳税人资格办理-2072286656513949697' '企业年度税务申报-2072286952426291202' '税务电子证书-2072287051462197250' > "$WORKDIR/queried_services.json"
```

脚本输出服务名称、价格、币种、数量、汇率、Markdown 服务内容。完整结果必须保存为 `queried_services.json`；`人民币兑换服务币种汇率`/`美元兑换服务币种汇率` 是后续换币种/换主体/追加服务的权威汇率来源。生成 `quotation.json` 时，在 `_meta` 或等价字段记录查询文件、源币种、目标币种和使用汇率。

失败处理：
- API 顶层 `success: false`：必须将 API 返回字段 `message` 的文字原样转达给用户，不生成报价单。
- 任一服务内部 `success: false` / `查询成功: false`：必须列出失败服务的 aiCode 和错误信息，立即中断，不得仅使用成功项继续生成。API 原始错误字段为 `message`；脚本输出中的 `errors` 只是便于 Agent 展示的归一化包装，不是 API 原生字段。
- API 顶层成功但未返回任何服务：视为查询失败，必须中断并告知用户未查询到服务。
- 展示给用户只列服务名、数量及单位、价格及币种；不展示原始 JSON、规格、有效期。

## 确定实体与币种

用户明确提到北京/西安/深圳/上海/上海新企业/雅加达时使用对应实体。用户未指定时：
- 服务原币种为 IDR 且用户未要求人民币/美元报价，默认倾向 `jakarta`。
- 客户要求人民币/美元付款时，不代表必须选择中国主体；`jakarta` 主体也可使用 RMB/USD 报价和收款。
- 需要特定币种报价但未说明签约主体时，优先沿用当前/已选主体；没有当前主体时再向用户确认主体。
- 多币种服务、追加到既有报价单、或主体/币种意图不明确时，必须确认。

币种可由 `_meta.target_currency` 指定（`IDR`/`RMB`/`USD`），优先级高于实体配置默认币种；例如 `applicable_entity: jakarta` 且 `target_currency: USD` 表示使用雅加达主体生成美元报价单。

换币种必须用脚本，不心算：

```bash
python3 scripts/convert_currency.py --query-result "$WORKDIR/queried_services.json" --to RMB --json-only
python3 scripts/convert_currency.py --query-result "$WORKDIR/queried_services.json" --to USD --json-only
python3 scripts/convert_currency.py --amount 250000000 --from IDR --to RMB --rateToCny 2173.91 --json-only
python3 scripts/convert_currency.py --amount 115000 --from RMB --to IDR --rateToCny 2173.91 --json-only
python3 scripts/convert_currency.py --amount 30000000 --from IDR --to USD --rateToUsd 17875 --json-only
```

`rateToCny` 表示 `1 CNY = N 服务币种`，`rateToUsd` 表示 `1 USD = N 服务币种`。修改已有报价单时优先使用原始 `queried_services.json` 或 `quotation.json` 留档汇率；没有留档汇率，必须让用户提供或确认汇率。

## 整理服务内容

从 API Markdown 服务内容提取结构化数据：

| API 内容 | 目标字段 | 规则 |
|---------|----------|------|
| 办理时间/工作日 | `services[].items[].days` | 提取数字 + 工作日；多个时间取最长 |
| 基本信息/服务说明/服务概述 | `services[].items[].note` | 100-200 字摘要；不足 100 字完整呈现 |
| 费用包含/服务包含 | `fee_details[].include` | 每条一项，保留原文 |
| 费用不含/不包含 | `fee_details[].exclude` + `notes` | 公共不含项进 `notes`；服务特有项留 `exclude` |
| 办理流程/服务流程 | `process_data[].process` | 每步一条，保留序号 |
| 交付文件/交付材料 | `process_data[].deliverables` | 每项一条 |
| 所需资料/所需材料 | `doc_data[].docs` | 每项一条，保留子层级编号 |

忽略 API 内容里的付款方式、退款/售后、发票相关。用户明确指定报价单付款条件时写入 `quote_meta.payment_terms`。

公共不含项规则：比较各服务的不含项；多个/全部服务共同适用，或语义上对整份报价共同适用的项目，统一写入 `notes` 一次。`config/entities.json` 的 `universal_excludes` 只是常见公共项种子示例，不是白名单。服务无特有不含项时 `exclude: []`。

示例：A/B 都有“文件翻译费用、资料快递费用（国际）”，A 另有“相关资质办理费”，B 另有“政府规费”：
- `A.exclude = ["相关资质办理费"]`
- `B.exclude = ["政府规费"]`
- `notes` 统一写“以上服务报价不包括：文件翻译费用（如需）、资料快递费用（国际）。”

## 数据文件

在 `WORKDIR` 复制并编辑示例：

```bash
cp examples/sample_quotation.json "$WORKDIR/quotation.json"
```

最小可用结构见 `examples/minimal_quotation.json`。字段要点：

| 字段 | 说明 |
|------|------|
| `_meta` | 内部留档：实体、源币种、目标币种、查询文件、使用汇率等 |
| `quote_meta` | 标题、日期、客户、合同号、付款条件；付款条件不填则用 entity 默认值 |
| `services` | 分组列表；每项含 `id/name/quantity/days/price/note`；`name` 不带数量 |
| `discount_amount` | 整数；无优惠填 0；优惠 ≤ 小计 |
| `notes` | 通用备注；公共不含项在这里统一显示 |
| `fee_details` | 每项含 `name/include/exclude/note`；`name` 必须匹配服务名 |
| `process_data` | 每项含 `name/process/deliverables` |
| `doc_data` | 每项含 `name/docs` |
| `doc_notes_text` | 材料备注，字符串列表 |

## 预检 → 生成 → 验证

```bash
python3 scripts/validate_data.py --entity xian --data "$WORKDIR/quotation.json"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$USER_WORKDIR/报价单.docx"
python3 scripts/build_quotation.py --entity shenzhen --data "$WORKDIR/quotation.json" --title-line1 "印尼投资" --title-line2 "公司注册服务方案" --quote-date 2026-06-15 --output "$USER_WORKDIR/报价单.docx"
python3 scripts/verify_quotation.py --entity xian --input "$USER_WORKDIR/报价单.docx" --data "$WORKDIR/quotation.json"
```

validate error → 必须修复，warning → 判断后处理。`--entity` 必传。`--title-line1/2`、`--quote-date` 优先级：命令行 > quote_meta > 默认值。报价币种优先级：`_meta.target_currency` > entity 默认币种。verify 自动检查页眉/银行/签名、服务名覆盖、金额公式、字体、A4，并在传入 `--data` 时对比 `_meta.applicable_entity` / `_meta.target_currency`。目视补充：标题/客户/日期正确，表格无错位乱码。

## 修复与异常边界

| 问题 | 处理 |
|------|------|
| 页眉/银行/签名不一致 | 检查 `--entity` 和 `config/entities.json`；属于业务/数据问题，不提示联系 SKILL 开发者 |
| 服务名覆盖缺失 | 补齐 `fee_details/process_data/doc_data` 中缺失的同名条目 |
| 金额或币种异常 | 检查 `discount_amount`、服务整数总价、汇率和实体币种；重新运行预检 |
| 公共不含项重复出现在 `exclude` | 从各服务 `exclude` 移除公共项，在 `notes` 中统一显示 |
| `build` 报 `Invalid quotation data` | 先跑 `validate_data.py` 定位字段并修正 |
| `verify` 失败 | 除付款方式等客户最终处理内容外，优先修改 `quotation.json` 后重新 build；不要手改其他 `.docx` 内容 |

业务/数据问题包括但不限于：数据填写错误、缺字段、金额不一致、实体选择错误、页眉公司名称和签约名称不一致、金额过大疑似选错币种、付款金额与合同金额不一致、付款条件/优惠/税率/签约主体等业务口径不明确。此类问题应修数据或向用户确认，不许提示联系 SKILL 开发者。

只有排除业务/数据问题后，同一份有效数据仍触发脚本异常、校验逻辑明显错误、生成内容与配置矛盾，且严重影响报价正确性或无法交付时，才暂停交付，展示缺陷问题、输入数据/命令/报错摘要，并提示联系 SKILL 开发者。执行报价任务时不得修改 skill 脚本，不得绕过校验。
