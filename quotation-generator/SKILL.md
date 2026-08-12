---
name: quotation-generator
version: 1.10.1
description: >
  山海图报价单生成器。新建：用户提供 aiCode → fetch → 生成 .docx。
  修改：用户未提供 aiCode → 基于既有 quotation.json 修改后重建。
  支持 10 签约主体、IDR/RMB/USD/SGD/THB/VND 六种报价币种。
last_updated: "2026-08-12"
---

# 山海图报价单生成器

查询服务信息 → 整理服务内容 → 生成标准格式 `.docx` 报价单。核心目标：签约主体、币种、价格、增值税、含税总计、页眉/银行/签名一致，且版式无错乱乱码。

## 硬规则速查

### 数据规则

| # | 规则 | 要点 |
|---|------|------|
| 1 | 失败即中断 | 查询失败（API 层/服务层/空结果）→ 展示 API message 原文，不继续生成 |
| 2 | 价格为整数总价 | 不接受单价；为空/面议时要求用户补充总价；API 返回"价格面议"需询问用户后填入；BPO/百分比定价服务例外：`price` 填 0，费率说明写 `note`/`notes`（见『修复与异常边界』） |
| 3 | 优惠只扣税前小计 | 增值税 = (小计 − 优惠金额) × 税率；优惠 ≤ 小计 |
| 4 | 服务名不带数量 | `services[].items[].name` 写基础服务名；数量写 `quantity` |
| 5 | 汇率留档但不入报价单 | API 返回的 `rateToCny/rateToUsd` 随 `queried_services.json` 和 `_meta` 留档；客户可见报价单不写汇率说明 |
| 6 | 公共不含项去重 | 多个/全部服务共同适用的费用不含项抽取到 `notes`；各服务 `exclude` 只留服务特有项 |
| 7 | 金额符号 | 人民币 `￥` 无空格；印尼盾 `Rp ` 后跟空格；美元 `$ ` 后跟空格；新加坡元 `S$ ` 后跟空格；泰铢 `฿` 后跟空格；越南盾 `₫ ` 后跟空格 |
| 8 | 付款方式只提醒 | 付款方式留给用户最终处理；脚本只检查明显不合理项并 warning，例如付款比例合计 >100%、付款金额合计 > 合同含税总计 |

### 流程规则

| # | 规则 | 要点 |
|---|------|------|
| 1 | aiCode 必先 fetch | 只接受 `服务名-19位数字编码`；脚本会将完整字符串作为 `aiCodes` 请求参数；不截取末尾 19 位数字编码 |
| 2 | 手改 `.docx` 优先 | 用户提供/说明手改过报价单时，先保留客户可见内容，再重新 build；付款方式由 build 从旧 `.docx` 保留——仅当 `--output` 指向旧 `.docx` 时自动保留，修改场景输出新文件时须显式传 `--preserve-payment-from` 指向旧 `.docx`，否则会回退到 `quotation.json`/实体默认付款方式 |
| 3 | 输出位置 | 最终生成的 `.docx` 放在 `quotation/YYYY-MM/` 下（YYYY-MM 为报价单日期所在年月）；`quotation.json`、`queried_services.json` 等过程文件写入其子目录；不写 skill 根目录；修改已有报价单时输出新文件 |
| 4 | Agent 不改脚本 | 执行报价任务不得修改 build/validate/verify/fetch 等脚本；业务/数据问题按业务处理，只有严重脚本缺陷才提示联系 SKILL 开发者 |
| 5 | 重复 aiCode 不累加 | 用户重复输入同一 aiCode 时，数量和价格不翻倍，也不累加；大概率是用户误重复输入。若用户明确要求"多加一行"，则在报价单中新增同名服务行（用序号或 aiCode 末三位区分），每行各自独立 |

## 签约主体

| 签约主体 | `--entity` | 增值税 | 默认币种 | 可切换币种 |
|----------|------------|--------|----------|-----------|
| PT. SHAN HAI MAP (雅加达) | `jakarta` | 11% | IDR | RMB, USD |
| 北京山海图科技有限公司 | `beijing` | 6% | RMB | USD |
| 北京山海图科技有限公司西安分公司 | `xian` | 6% | RMB | USD |
| 北京山海图科技有限公司深圳分公司 | `shenzhen` | 6% | RMB | USD |
| 北京山海图科技有限公司上海分公司 | `shanghai` | 1% | RMB | USD |
| 上海山海图新企业咨询有限公司 | `shanghai_new` | 1% | RMB | USD |
| SHAN HAI MAP CONSULTANCY PTE.LTD (新加坡) | `singapore` | 0% | SGD | RMB, USD |
| PT DEIN TALENT SOLUTIONS (德音人力) | `deyin` | 11% | IDR | RMB, USD |
| SHAN HAI MAP (THAILAND) CO., LTD. (泰国) | `thailand` | 7% | THB | RMB, USD |
| CÔNG TY TNHH SHANHAIMAP VIỆT NAM (越南) | `vietnam` | 8% | VND | RMB, USD |

完整银行账户、地址、税号等详见 `config/entities.json` 和 `references/entity-bank-info.md`。

⚠️ **SWIFT CODE 注意**：生成美元（USD）报价时，银行信息中的 SWIFT CODE 仅 jakarta、shanghai、shanghai_new、singapore、deyin、thailand、vietnam 七个实体已配置。beijing、xian、shenzhen 的 entities.json 中缺少 SWIFT CODE，用户可能提出疑问。若用户要求补全，需向用户确认具体 SWIFT CODE 后更新 `config/entities.json`。

标题、日期、客户、合同号、付款条件写入 `quote_meta`。

## WeCom 交互节奏

在 WeCom（企业微信）网关上，用户在一次回复中看不到中间进度——所有工具调用完成后才显示最终消息。fetch API 耗时数秒至十几秒，加上 validate/build/verify，全程对用户是黑盒。

**分步交互**，每步独立回复，用户全程可见进展：

1. 🔍 正在查询 N 个服务... → fetch
2. ✅ 查询完成，列出服务名/数量/价格/币种 → 币种转换
3. 📝 正在整理数据、生成报价单... → validate + build + verify
4. ✅ 报价单已生成，附摘要表格

## 场景判断

```bash
USER_WORKDIR="$(pwd)"
# QUOTATION_DIR: 报价单日期所在年月目录，如 quotation/2026-06（兼容 Linux/macOS）
QUOTATION_DIR="$USER_WORKDIR/quotation/$(date -d '<quote_date>' +%Y-%m 2>/dev/null || date -j -f '%Y-%m-%d' '<quote_date>' +%Y-%m)"
mkdir -p "$QUOTATION_DIR"
WORKDIR="$QUOTATION_DIR/quotation-YYYYMMDD-client" && mkdir -p "$WORKDIR"
cd <skill-root>
```

| 场景 | 条件 | 路径 |
|------|------|------|
| A 新建 | 用户提供 aiCode，工作区无 quotation.json | fetch → organize → validate → build → verify |
| B 修改 | 用户未提供 aiCode | 定位 → 保留手改/快照对比 → 修改 → validate → build → verify |
| C 追加 | 用户提供 aiCode + 既有 quotation.json | 定位 → 保留手改/快照对比 → fetch 新服务 → 合并 → validate → build → verify |

（仅限修改/追加场景）找不到既有 quotation.json 时要求用户提供，不从 sample 凭空改；新建场景仍按『数据文件』章节从 sample 复制起步。

## 手改 `.docx` 保留

付款方式/付款条件属于客户可见手改内容。初次生成的默认付款方式只作占位，后续用户手动修改或让 Agent 修改后，重新生成服务、金额、优惠时不得把付款方式还原为 `quotation.json` 或实体默认值。脚本只做合理性提醒，不因付款方式 warning 阻断生成。

`build_quotation.py` 默认行为：
- 如果 `--output` 指向已存在的 `.docx`，自动从该旧文件读取付款方式并用于本次重建。
- 如果输出到新文件但需要沿用某个旧报价单的付款方式，传 `--preserve-payment-from "$WORKDIR/已编辑报价单.docx"`。
- 只有明确要用 `quotation.json` / 实体默认付款方式覆盖旧文件时，才传 `--overwrite-payment-terms`。
- 如果指定了旧 `.docx` 但付款方式提取失败，必须立即中止；不得回退到 `quotation.json` 或实体默认付款方式，也不得覆盖旧文件。
- 仅当旧 `.docx` 付款方式提取失败，或用户明确要求修改付款方式时，才向用户询问并确认付款方式。用户明确告知后，将其写入本次工作目录的 `quote_meta.payment_terms`，并传 `--overwrite-payment-terms` 重新生成；这不属于从旧文档同步数据。
- `validate` / `build` / `verify` 会提醒明显不合理的付款方式：付款比例合计超过 100%，或付款金额合计大于合同含税总计。

```bash
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --preserve-payment-from "$QUOTATION_DIR/已编辑报价单.docx" --output "$QUOTATION_DIR/新版报价单.docx"
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

依赖检查：`fetch_services.py` 依赖 `markdownify` 库生成干净的 Markdown 服务内容。如果未安装，脚本会在 stdout 打印 `⚠️ markdownify not available, using fallback HTML cleaner` 警告，该警告会混入 `queried_services.json` 导致 JSON 损坏。**必须在 fetch 前检查并处理**：

```bash
# 方案 A：安装依赖（推荐）
pip install markdownify
# 如 pip 不可用，使用 uv：
uv pip install markdownify --system

# 方案 B：fetch 后剥离警告行
python3 scripts/fetch_services.py '...' > "$WORKDIR/queried_services_raw.json"
tail -n +2 "$WORKDIR/queried_services_raw.json" > "$WORKDIR/queried_services.json"
```

`convert_currency.py --query-result` 传入损坏的 JSON 时会报 `Cannot read query result file`，根因通常是此警告行未剥离。

失败处理：
- API 顶层 `success: false`：必须将 API 返回字段 `message` 的文字原样转达给用户，不生成报价单。
- 任一服务内部 `success: false` / `查询成功: false`：必须列出失败服务的 aiCode 和错误信息，立即中断，不得仅使用成功项继续生成。API 原始错误字段为 `message`；脚本输出中的 `errors` 只是便于 Agent 展示的归一化包装，不是 API 原生字段。
- API 顶层成功但未返回任何服务：视为查询失败，必须中断并告知用户未查询到服务。
- API 返回成功但服务内容为空或仅含引导语（如"点击对应规格查看业务完整详情"）：内容不足以生成报价单，必须中断并告知用户"API 未返回服务详情，请确认 aiCode 或提供完整服务信息"。不可仅凭价格和名称强行生成。
- 展示给用户只列服务名、数量及单位、价格及币种；不展示原始 JSON、规格、有效期。

## 确定实体与币种

用户明确提到北京/西安/深圳/上海/上海新企业/雅加达/新加坡/德音人力/泰国/越南时使用对应实体。用户未指定时：
- 服务原币种为 IDR 且用户未要求人民币/美元报价，默认倾向 `jakarta`。
- 服务原币种为 THB 且用户未要求人民币/美元报价，默认倾向 `thailand`。
- 服务原币种为 SGD 且用户未指定主体，默认倾向 `singapore`。
- 服务原币种为 VND 且用户未要求人民币/美元报价，默认倾向 `vietnam`。
- 客户要求人民币/美元付款时，不代表必须选择中国主体；`jakarta` 主体也可使用 RMB/USD 报价和收款。
- 需要特定币种报价但未说明签约主体时，优先沿用当前/已选主体；没有当前主体时再向用户确认主体。
- 多币种服务、追加到既有报价单、或主体/币种意图不明确时，必须确认。

币种可由 `_meta.target_currency` 指定（`IDR`/`RMB`/`USD`/`SGD`/`THB`），优先级高于实体配置默认币种；例如 `applicable_entity: jakarta` 且 `target_currency: USD` 表示使用雅加达主体生成美元报价单。

⚠️ **实体币种限制**：报价单币种仅支持**签约主体所在国家的本币**与**人民币/美元**外币（必须在该实体 `allowed_currencies` 内）。用户要求切换支付币种时，必须先查 `config/entities.json` 中该实体的 `allowed_currencies`，不可凭记忆假设。中国主体（beijing/xian/shenzhen/shanghai/shanghai_new）只支持 RMB 和 USD，不支持 IDR；印尼主体（jakarta、deyin）支持 IDR、RMB、USD；新加坡（singapore）支持 SGD、RMB、USD；泰国（thailand）支持 THB、RMB、USD；越南（vietnam）支持 VND、RMB、USD。IDR 报价只能用印尼主体（jakarta / deyin），SGD 报价只能用新加坡主体（singapore），THB 报价只能用泰国主体（thailand），VND 报价只能用越南主体（vietnam）。若币种不被当前实体支持，向用户提供两个选项：(1) 在备注中标注等值金额；(2) 切换到支持该币种的实体。

⚠️ **泰国预扣税**：使用泰国主体（`thailand`）报价时，**必须先询问用户是否需要扣除预扣税（Withholding Tax，税率 3%）**。若用户明确要求扣除，在 `quotation.json` **顶层**设置 `withholding_tax: true`（与 `discount_amount`/`notes` 同级，**不要放进 `_meta`**）；若用户明确说不需要，则不设置（`withholding_tax: false`），报价单中不显示和计算预扣税。注意：`withholding_tax` 缺失或误放 `_meta` 时，build 会静默按不扣税处理，报价单只显示增值税。

换币种必须用脚本，不心算：

```bash
python3 scripts/convert_currency.py --query-result "$WORKDIR/queried_services.json" --to RMB --json-only
python3 scripts/convert_currency.py --query-result "$WORKDIR/queried_services.json" --to USD --json-only
python3 scripts/convert_currency.py --amount 250000000 --from IDR --to RMB --rateToCny 2173.91 --json-only
python3 scripts/convert_currency.py --amount 115000 --from RMB --to IDR --rateToCny 2173.91 --json-only
python3 scripts/convert_currency.py --amount 30000000 --from IDR --to USD --rateToUsd 17875 --json-only
```

`rateToCny` 表示 `1 CNY = N 服务币种`，`rateToUsd` 表示 `1 USD = N 服务币种`。修改已有报价单时优先使用原始 `queried_services.json` 或 `quotation.json` 留档汇率；没有留档汇率，必须让用户提供或确认汇率。

**服务币种**：API 返回的币种可能为 IDR、VND、CNY、MYR、SGD、THB、EGP，`rateToCny` 和 `rateToUsd` 对所有币种均有效。报价单目标币种仅支持**签约主体所在国家的本币**与**人民币/美元**外币，且须在对应实体 `allowed_currencies` 内（见『签约主体』表）：印尼主体（jakarta/deyin）本币 IDR，中国主体本币 RMB，新加坡主体本币 SGD，泰国主体本币 THB，越南主体本币 VND；RMB/USD 为通用外币。通过 `_meta.target_currency` 指定。换算公式通用：`RMB 价格 = 服务价格 ÷ rateToCny`，`USD 价格 = 服务价格 ÷ rateToUsd`，取整后写入 quotation.json。

`convert_currency.py` 仅支持 IDR ↔ RMB ↔ USD 自动换算。其他服务币种（MYR、SGD、THB、EGP）需 Agent 手动按公式换算后再走后续流程。VND（越南盾）同理：用 API 的 `rateToCny`（即 1 CNY = N VND）手动换算 `VND 价格 ÷ rateToCny = RMB 价格`，取整后写入 quotation.json。若目标币种与服务币种相同（如 SGD→singapore 报价、THB→thailand 报价、VND→vietnam 报价），无需换算，直接填入价格。`_meta` 留档源币种和汇率；报价单中不展示汇率说明。

## 价格计算（API → quotation.json）

API 的 `服务价格` 已经是 **总价**（对应服务数量的总价），`convert_currency.py` 换算后直接得到行项总价。不需要再乘数量。

计算链路：

```
API 服务价格 (总价，已含数量)
  → convert_currency.py 按汇率换算（如需换币种）
  → 换算后价格 = quotation.json price（行项总价，整数）
```

示例：API 返回 服务价格=30,000,000 IDR、服务数量=2、rateToCny=2250
```
convert: 30,000,000 ÷ 2,250 = 13,333 RMB  （脚本自动取整）
price:   13,333                            （直接写入 quotation.json）
```

`convert_currency.py` 输出的换算后价格即为行项总价，Agent 直接写入 `quotation.json`，不再乘数量。生成报价单后主动展示完整计算过程（API 总价 → 汇率 → 换算后总价 → 增值税 → 含税总计），用户要求时可追溯到每一步来源。

税金小数位：人民币/美元/泰铢/新币（RMB/USD/THB/SGD）的增值税、预扣税、含税总计**仅当金额有小数值时才显示小数位**（如 `฿ 2,100.07`、`$ 70.35`），整数金额不显示 `.00`（如 `$ 70`、`฿ 2,100`）；印尼盾/越南盾（IDR/VND）恒为整数（如 `Rp 1,100,000`）。由 build/verify 脚本按币种自动处理，Agent 无需手工干预。

## 整理服务内容

从 API Markdown 服务内容提取结构化数据。**5 个以上服务时**，使用 `execute_code` 批量提取，详见 `references/bulk-extraction-pattern.md`。

| API 内容 | 目标字段 | 规则 |
|---------|----------|------|
| 办理时间/工作日 | `services[].items[].days` | 提取数字 + 工作日；多个时间取最长 |
| 基本信息/服务说明/服务概述 | `services[].items[].note` | 100-200 字摘要；不足 100 字完整呈现；**若基本信息中包含金额相关描述（如罚款金额、收费标准、官方费用等），必须将金额信息总结写入备注** |
| 费用包含/服务包含 | `fee_details[].include` | 每条一项，保留原文 |
| 费用不含/不包含 | `fee_details[].exclude` + `notes` | 公共不含项进 `notes`；服务特有项留 `exclude` |
| 办理流程/服务流程 | `process_data[].process` | 每步一条，保留序号 |
| 交付文件/交付材料 | `process_data[].deliverables` | 每项一条；**当交付文件数量 >1 时，每项前自动添加无序编号 `•`** |
| 所需资料/所需材料 | `doc_data[].docs` | 每项一条，保留子层级编号 |

忽略 API 内容里的付款方式、退款/售后、发票相关。用户明确指定报价单付款条件时写入 `quote_meta.payment_terms`。

公共不含项规则：比较各服务的不含项；多个/全部服务共同适用，或语义上对整份报价共同适用的项目，统一写入 `notes` 一次。**notes 已写的不含项，`exclude` 不再重复**——哪怕只有一个服务也是如此。`config/entities.json` 的 `universal_excludes` 只是常见公共项种子示例，不是白名单。服务无特有不含项时 `exclude: []`。

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
| `quote_meta` | 标题、日期、客户、合同号、付款条件；**付款条件必须填非空数组**——留空 `[]` 会触发 validate error，不是静默回退实体默认值；不指定付款条件时从 `config/entities.json` 复制该实体 `payment_terms` 填入 |
| `services` | 分组列表；每项含 `id/name/quantity/days/price/note`；`name` 不带数量 |
| `discount_amount` | 整数；无优惠填 0；优惠 ≤ 小计 |
| `withholding_tax` | 布尔（**顶层**字段，与 `discount_amount`/`notes` 同级，不要放进 `_meta`）；泰国主体扣预扣税时填 `true`，否则不填或填 `false`。缺失或误放 `_meta` 时 build 会静默按不扣税处理 |
| `notes` | 通用备注；公共不含项在这里统一显示 |
| `fee_details` | 每项含 `name/include/exclude/note`；`name` 必须匹配服务名 |
| `process_data` | 每项含 `name/process/deliverables` |
| `doc_data` | 每项含 `name/docs` |
| `doc_notes_text` | 材料备注，字符串列表 |

## 预检 → 生成 → 验证

```bash
python3 scripts/validate_data.py --entity xian --data "$WORKDIR/quotation.json"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/build_quotation.py --entity shenzhen --data "$WORKDIR/quotation.json" --title-line1 "印尼投资" --title-line2 "公司注册服务方案" --quote-date 2026-06-15 --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/verify_quotation.py --entity xian --input "$QUOTATION_DIR/报价单.docx" --data "$WORKDIR/quotation.json"
```

validate error → 必须修复，warning → 判断后处理。`--entity` 必传。`--title-line1/2`、`--quote-date` 优先级：命令行 > quote_meta > 默认值。报价币种优先级：`_meta.target_currency` > entity 默认币种。verify 自动检查页眉/银行/签名、服务名覆盖、金额公式、字体、A4，并在传入 `--data` 时对比 `_meta.applicable_entity` / `_meta.target_currency`。目视补充：标题/客户/日期正确，表格无错位乱码。

## 修复与异常边界

| 问题 | 处理 |
|------|------|
| verify 报页眉公司名与银行公司名不一致但只差一个 `.` | 正常现象（如 `Pte.Ltd` vs `Pte.Ltd.`、`PT. SHAN HAI MAP` vs `PT SHAN HAI MAP`）。verify v1.9.1 起用 `normalize_company_name()`（去点号 + 归一化空白）比较，点号差异不再判为异常；真正不同的公司名仍报错 |
| 服务内容表格「序号」列显示服务编码而非数字 | `build_quotation.py` v1.8.6 起使用自增计数器（1, 2, 3...）生成序号，不再依赖 `item['id']`。旧数据即使 `id` 填了服务编码也不影响显示 |
| 页眉/银行/签名不一致 | 检查 `--entity` 和 `config/entities.json`；属于业务/数据问题，不提示联系 SKILL 开发者 |
| 服务名覆盖缺失 | 补齐 `fee_details/process_data/doc_data` 中缺失的同名条目 |
| 金额或币种异常 | 检查 `discount_amount`、服务整数总价、汇率和实体币种；重新运行预检 |
| 公共不含项重复出现在 `exclude` | 从各服务 `exclude` 移除公共项，在 `notes` 中统一显示 |
| `convert_currency.py` 报 `Cannot read query result file` | `queried_services.json` 被 `markdownify` 警告污染 → 见『查询服务信息』章节的 markdownify 警告处理（剥离首行或安装 markdownify 后重新 fetch） |
| 服务名重复 `Duplicate service name` | 当多个 aiCode 返回相同 `服务名称` 时，用 aiCode 末三位区分（如 `公司注册-114`、`公司注册-818`）；同步更新 `fee_details/process_data/doc_data` 中所有 name 引用 |
| `build` 报 `Invalid quotation data` | 先跑 `validate_data.py` 定位字段并修正 |
| `verify` 失败 | 除付款方式等客户最终处理内容外，优先修改 `quotation.json` 后重新 build；不要手改其他 `.docx` 内容 |
| 用户质疑价格/计算公式 | 展示完整计算链路：API 总价 → 汇率 → 换算后总价 → 增值税 → 含税总计，每步附带来源值 |
| 美元报价缺少 SWIFT CODE | beijing/xian/shenzhen 未配置 SWIFT CODE → 提醒用户并提供参考 `references/entity-bank-info.md`；用户确认后可补入 `config/entities.json` |
| 服务币种不支持（如 MYR） | `convert_currency.py` 不支持 MYR → HKD 等非 IDR/RMB/USD 币种。手动换算：`MYR 价格 ÷ rateToCny = RMB 价格`，取整后写入 quotation.json；马币原价写入各服务 `note` 字段；`_meta` 中注明源币种和汇率公式（报价单中不展示汇率说明） |
| 新加坡元（SGD）报价 | `convert_currency.py` 目前仅支持 IDR/RMB/USD 互转，不支持 SGD。若服务币种为 SGD，直接填入价格无需换算；若需从 IDR/RMB 换算至 SGD，需用户提供汇率后手动换算：`源币种价格 ÷ 汇率 = SGD 价格`，取整后写入 quotation.json。`_meta` 中注明源币种和汇率 |
| `fee_details[].include` 不能为空 | validate 报 `include is required and must be a non-empty list` → API 未列费用包含项的服务，至少填 `"山海图服务费"` |
| `quote_meta.payment_terms` 留空数组 | validate 报 `payment_terms must be a non-empty list when provided`（不会静默回退实体默认值）→ 从 `config/entities.json` 复制该实体 `payment_terms` 填入，或写用户明确指定的付款条件 |
| 用户提供分享链接而非 aiCode | 山海图分享链接（`#/products/productDetailInner?sharingRecordId=xxx`）中的 `sharingRecordId` 不是 aiCode，无法用于 `aiCode/resolve` API（返回"AI Code 无效"）。除 `aiCode/resolve` 外的所有产品接口均需登录认证。**不要反复尝试不同 aiCode 组合**——直接告知用户分享链接不可用，要求提供完整的 `服务名-19位数字编码` 格式 aiCode。若是规格化产品（如 ODI 按投资额/股东数定价），同时向用户确认所选规格和价格 |
| API 返回 "AI Code 无效" | aiCode 中服务名部分的空格必须与数据库完全一致（如 `JSHK 账户维护` 不能写成 `JSHK账户维护`）。若用户坚持 aiCode 正确，检查空格是否遗漏后再重试 |
| 修改付款比例后 docx 仍显示旧比例 | build 默认从旧 `.docx` 保留付款方式。`quotation.json` 改了付款条件但 rebuild 后未生效 → 必须加 `--overwrite-payment-terms` 强制覆盖 |
| 换算后价格与官网差 1 元 | `convert_currency.py` 使用整数截断（floor），不是四舍五入。例如 18,400,000 ÷ 2,250 = 8,177.78，脚本得 8,177，官网四舍五入得 8,178。用户指出差异时，用 `round()` 修正后再写入 quotation.json 并重建 |
| 调整服务顺序后报价单未变化 | 仅修改 `services[].items[]` 的顺序不够，必须同步将 `fee_details`、`process_data`、`doc_data` 按新顺序重排：提取目标 names_order 后用 `sorted(q[key], key=lambda x: names_order.index(x["name"]))` 对三者重排 |
| 追加服务时复用已有查询数据 | 同一批次多个报价单共享部分 aiCode 时，可从已有 `queried_services.json` 提取目标服务，避免重复 fetch；从已转换价格列表中对应取值 |
| BPO/百分比定价服务 | BPO 业务流程外包等按比例收费的服务，`price` 设为 0，费率结构在 `note` 和 `notes` 中说明（如"8%月用工总成本"），押金等附加费同样在备注说明。validate 会报 price 过小 warning，可忽略 |
| 测试费作为独立行项 | 用户要求加测试费时，新增独立服务行项（如"样品测试费"），单独列金额，填写简略 `process/deliverables/docs`（如 `["样品送检"]`），`days` 填 `"-"` |
| 用户用「条」表示百万 | 「条」= juta = 百万印尼盾，如「120条」= Rp120,000,000。报价单按数字填，不保留「条」字；向用户展示时可直接换算展示 |
| `rateToCny=1.0` 且 `服务币种=IDR` | 这是 API 数据标记错误：实际为人民币定价服务，系统误标为 IDR。不要按 IDR 换算，直接当 RMB 价格处理。**必须向用户展示此异常并确认报价币种**——用户可能选 RMB（直接用 API 价格）或 IDR（需用户提供真实汇率重新换算）。`_meta` 中注明此异常 |
| `服务币种=IDR` 但 `rateToCny` 异常低 + 服务编码非 ID 前缀 | `rateToCny` 与 IDR 严重不匹配（如 0.6、4.0 而非 ~2250），且服务编码以 MY/VN/SG/TH/EG 等国家前缀开头 → API 币种标记错误，实际为对应国家币种（如 MY=马币、VN=越南盾）。**向用户展示异常**，结合服务编码前缀与同批次服务汇率一致性判断真实币种，确认后按实际币种换算。`_meta` 中注明此异常及判断依据 |
| API 服务内容 Markdown 格式不一致导致提取失败 | API 返回的服务内容中，章节标题格式极不统一：`****费用包含：****`、`****费用包含********：****`、`费用包含：`（无星号）、`****费用不包含：****`（非"费用不含"）等。正则提取（如 `r'费用包含[：:]*\s*\n'`）频繁失败。**方案：使用 `str.find()` 定位关键标记 + 截取文本的方式更可靠**，忽略中间星号。`execute_code` 中编写 `extract_list(content, start_marker, end_marker)` 函数，基于 `find()` 而非正则 |
| `doc_data[].docs` 为空导致 validate 失败 | 部分服务"所需资料"下直接写"无"——API 未返回资料清单。提取结果为 `[]` 会触发 `docs is required and must be a non-empty list`。**必须补兜底**：`docs = extract_list(...) or ["请咨询山海图获取详细资料清单"]` |

业务/数据问题包括但不限于：数据填写错误、缺字段、金额不一致、实体选择错误、页眉公司名称和签约名称不一致、金额过大疑似选错币种、付款金额与合同金额不一致、付款条件/优惠/税率/签约主体等业务口径不明确。此类问题应修数据或向用户确认，不许提示联系 SKILL 开发者。

只有排除业务/数据问题后，同一份有效数据仍触发脚本异常、校验逻辑明显错误、生成内容与配置矛盾，且严重影响报价正确性或无法交付时，才暂停交付，展示缺陷问题、输入数据/命令/报错摘要，并提示联系 SKILL 开发者。执行报价任务时不得修改 skill 脚本，不得绕过校验。
