---
name: quotation-generator
description: >
  根据 aiCode 新建报价单，或基于既有 quotation.json 修改并重建报价单。
  支持 config/entities.json 中配置的签约主体和报价币种。
---

# 山海图报价单生成器

查询服务信息 → 整理结构化数据 → 生成并验证 `.docx`。签约主体、币种、税金、总计、页眉、银行与签名必须一致。

## 底线规则

以下四条横跨整个流程，没有单独的章节可依附；其余约束在各章节就地说明。

- **客户可见单不出现汇率**：汇率只写入查询文件和 `_meta`，不得出现折算或兑换说明。
- **不改生成脚本**：执行报价任务不得修改 build/validate/verify/fetch；业务问题改数据或向用户确认。
- **付款方式只提醒**：脚本只 warning 明显异常，不替用户决定付款条件。
- **重复 aiCode 不累加**：相同 aiCode 默认去重，用户明确要求多一行时才新增独立行项。

## 签约主体

`config/entities.json` 是主体、别名、模板、税率、币种、银行和页眉配置的唯一数据源。修改配置后运行 `python3 scripts/generate_docs.py`，再运行同一命令加 `--check`。

[docs/entities-summary.md](docs/entities-summary.md)（主体）和 [docs/entity-bank-info.md](docs/entity-bank-info.md)（银行）是自动生成的**人工阅读版**——执行任务时不要读，一律以 `entities.json`、解析器输出和脚本校验为准。

用户说法一律交给解析器，不凭记忆映射：

```bash
python3 scripts/resolve_entity.py '请用德音人力报价'
```

- `matched`：使用返回的 `entity`；`ambiguous` / `no_match`：向用户确认。
- 禁止按国家或城市自行猜测主体；`ambiguous` 必须向用户确认。
- 「印尼/印度尼西亚」和「上海公司」是高风险歧义称呼；候选主体以解析器和自动生成摘要为准。

## 页眉、模板与银行

- 所有主体只使用 `entities.json` 配置的图片页眉，不允许文字页眉回退。
- 银行账户必须按报价币种选择；SWIFT 以 `swift_policy` 为准。
- 更换页眉、修改模板或排查版式时，读取 [references/header-layout.md](references/header-layout.md)，并运行其中列出的测试。

## 场景与工作目录

- `QUOTATION_DIR` = `<用户工作目录>/quotation/<报价日期 YYYY-MM>/`
- `WORKDIR` = `QUOTATION_DIR/quotation-<YYYYMMDD>-<客户简称>/`
- 命令一律传绝对路径；过程文件放 `WORKDIR`；不得写进 skill 根目录。

| 场景 | 条件 | 路径 |
|------|------|------|
| 新建 | 用户提供 aiCode，工作区无 `quotation.json` | fetch → organize → validate → build → verify |
| 修改 | 用户未提供 aiCode | 定位旧单 → 保留手改 → 修改数据 → validate → build → verify |
| 追加 | 用户提供 aiCode 且已有 `quotation.json` | 定位旧单 → 保留手改 → fetch 新服务 → 合并 → validate → build → verify |

修改/追加时，在用户工作目录下查找 `quotation.json` 和同目录 `.docx`，列出候选让用户确认。找不到就要求路径，不从 sample 猜测。旧单保留细节见 [references/edit-existing-quotation.md](references/edit-existing-quotation.md)。

## 查询服务（新建/追加）

```bash
python3 scripts/fetch_services.py '一般纳税人资格办理-2072286656513949697' > "$WORKDIR/queried_services.json"
```

stdout 必须原样存为 `queried_services.json`（不得混入 stderr），它是服务内容、原币种和汇率的权威来源。aiCode 必须是完整的 `服务名-19位数字编码`，只给 19 位编码时先向用户补服务名；多个 aiCode 可分参数或用英文逗号传入。

**失败即中断**：API 顶层失败、任一服务失败、空结果或详情不足时，展示原始错误并停止，不得只用成功项继续。价格面议不是 fetch 失败，向用户补总价后继续。aiCode 校验、分享链接、stderr 污染等排查见 [references/fetch-errors.md](references/fetch-errors.md)。

## 主体、币种与税金

用户未指定主体时，按服务原币种筛选 `currency` 相同的主体：只有一个候选时使用该主体；多个候选、多个服务币种、追加旧单或意图不明时先确认。IDR 未指定主体时默认 `jakarta`。指定主体后，目标币种必须在其 `allowed_currencies` 中；人民币或美元付款不代表必须使用中国主体。

税金以所选主体配置为准：`vat_rate` 和 `tax_label` 控制加税项；存在 `withholding_tax_rate` 时按该税率扣减；`withholding_tax_required: true` 表示固定扣减，否则必须先询问用户。启用扣减时顶层写 `withholding_tax: true`。

- 扣减额 = `(小计 − 优惠) × 税率`；总计 = `小计 − 优惠 + VAT/SST − 扣减额`。

## 汇率与价格

换币种必须使用 `scripts/convert_currency.py`，禁止心算；汇率只能来自 `queried_services.json`、既有留档或用户明确提供。外币 → 外币没有直接汇率时向用户索取，**不得估算**。命令、汇率方向、取整和异常处理见 [references/currency-conversion.md](references/currency-conversion.md)。

换算结果直接写 `services[].price`。生成后向用户展示：API 总价 → 使用汇率 → 换算后总价 → 税金/扣减 → 含税总计。

金额格式：IDR/VND 取整数；RMB/USD/SGD/THB/EGP/MYR 仅在有小数时显示两位小数；所有金额不带币种符号。

## 整理服务数据

| API 内容 | 字段 | 规则 |
|----------|------|------|
| 服务编码 | `code` | 必填，从 API 原样抄入，禁止编造 |
| 服务名 | `name` | 基础服务名不带数量，数量和单位分别写 `quantity` / `unit` |
| 办理时间 | `days` | 单次办理时间，不乘数量 |
| 服务价格 | `price` | API 价格已含数量，不得再乘；面议时向用户索取总价；BPO/百分比定价可填 0 并在 `note` 和顶层 `notes` 写明费率（价格过小的 warning 可忽略，不得把百分比伪造成固定金额） |
| 基本信息 | `note` | 摘要 100–250 字，保留必要金额和限制 |
| 费用包含 | `fees.include` | 每条一项，不能为空 |
| 费用不含 | `fees.exclude` / `notes` | 特有项留服务内，共同项移到顶层 |
| 流程 | `process` | 每步一条 |
| 交付文件 | `deliverables` | 每项一条，多项编号 |
| 所需材料 | `documents` | 一个元素一个段落；全角空格表示层级 |

忽略 API 中的付款、退款/售后和发票条款。`notes` 不得包含「上述办理时间不包括收集材料……」样板免责声明；直接删除，不要改写。详细原因见 [references/edge-cases.md](references/edge-cases.md)。

## 数据文件

新建时复制 `examples/sample_quotation.json`，简单场景可参考 `examples/minimal_quotation.json`。

- `_meta`：至少包含 `schema_version: 2`；记录主体、币种、查询文件和汇率。只要服务有 `ai_code`，就必须配置 `query_result_file`。
- `quote_meta`：标题、日期、客户、合同号和非空付款条件。
- `services`：唯一服务数据源；`line_id` 唯一，`quantity` 必须显式填写。
- `discount_amount`：整数，无优惠填 0，不得超过小计。
- `withholding_tax`：顶层布尔字段；泰国按确认结果填写，STC 必须为 `true`。
- `notes`：通用备注和公共费用不含项。

Schema v2 会检查字段白名单、必填项和分类连续性；字段拼错必须修复。

## 预检、生成与验证

```bash
python3 scripts/validate_data.py --entity xian --data "$WORKDIR/quotation.json"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/verify_quotation.py --entity xian --input "$QUOTATION_DIR/报价单.docx" --data "$WORKDIR/quotation.json"
```

- 必须依次执行 validate → build → verify，不得跳过；validate error 必须修复，warning 根据业务判断。
- `--entity` 必传；目标币种优先级为 `_meta.target_currency` > 主体默认币种。
- **签名栏「报价人」**：默认取「当前使用者」——在**企业微信会话**里生成时填该使用者的通讯录姓名，**不在企业微信环境**（命令行/CI 等）则留空；取不到姓名也留空（绝不把 userid 印到客户可见文档上）。build 会打印一行 `报价人：…（来源）`。
  **用户可改**：用户说「报价人写 XXX」时，把姓名写进 `quote_meta.quoter_name`（**持久生效，后续改单/换主体重出仍是该姓名**）；一次性指定可用 `--quoter <姓名>`（优先级最高），环境变量 `HERMES_QUOTER_NAME` 次之。`quote_meta.quoter_name` 显式写空串 = 该单强制不写报价人。取值优先级：`--quoter` > `quote_meta.quoter_name` > `HERMES_QUOTER_NAME` > 企微会话姓名 > 空。
- verify 必须检查页眉、蓝线、银行、签名、服务覆盖、金额公式、字体、A4 和页脚。
- 目视复核图片内公司名/地址、标题、客户、日期、表格和分页。

企业微信网关执行时读取 [references/wecom-workflow.md](references/wecom-workflow.md)。业务或数据问题应修正 `quotation.json` / `entities.json` 或向用户确认，不提示联系开发者。只有有效数据仍稳定触发脚本异常、配置与成稿矛盾且无法交付时，才报告脚本缺陷。不得绕过校验。
