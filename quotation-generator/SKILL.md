---
name: quotation-generator
version: 1.17.1
description: >
  山海图报价单生成器。新建：用户提供 aiCode → fetch → 生成 .docx。
  修改：用户未提供 aiCode → 基于既有 quotation.json 修改后重建。
  支持 13 签约主体、IDR/RMB/USD/SGD/THB/VND/EGP/MYR 八种报价币种。
last_updated: "2026-09-18"
---

# 山海图报价单生成器

查询服务信息 → 整理服务内容 → 生成标准格式 `.docx` 报价单。核心目标：签约主体、币种、价格、增值税、含税总计、页眉/银行/签名一致，且版式无错乱乱码。

## 硬规则速查

### 数据规则

| # | 规则 | 要点 |
|---|------|------|
| 1 | 失败即中断 | 查询失败（API 层/服务层/空结果）→ 展示 API message 原文，不继续生成 |
| 2 | 价格为整数总价 | 不接受单价；为空/面议时要求用户补充总价；API 返回"价格面议"需询问用户后填入；BPO/百分比定价服务例外（`price` 填 0，详见『修复与异常边界』） |
| 3 | 优惠只扣税前小计 | 增值税 = (小计 − 优惠金额) × 税率；优惠 ≤ 小计 |
| 4 | 服务名不带数量 | `services[].items[].name` 写基础服务名；数量写 `quantity` |
| 5 | 汇率留档但不入报价单 | API 返回的 `rateToCny/rateToUsd` 随 `queried_services.json` 和 `_meta` 留档；客户可见报价单不写汇率说明 |
| 6 | 公共不含项去重 | 多个/全部服务共同适用的费用不含项抽取到 `notes`；各服务 `exclude` 只留服务特有项 |
| 7 | 价格不带货币符号 | 所有价格单元格（服务价格、小计、增值税、预扣税、含税总计）一律不加货币符号；币种由价格列头「价格 (币种名)」标注 |
| 8 | 付款方式只提醒 | 付款方式留给用户最终处理；脚本只检查明显不合理项并 warning，例如付款比例合计 >100%、付款金额合计 > 合同含税总计 |

### 流程规则

| # | 规则 | 要点 |
|---|------|------|
| 1 | aiCode 必先 fetch | 只接受完整的 `服务名-19位数字编码`；脚本将该完整字符串原样作为 `aiCodes` 请求参数。只给 19 位纯数字会被脚本直接拒绝（`不支持纯19位数字编码查询`），此时要求用户补上服务名 |
| 2 | 手改 `.docx` 优先 | 用户手改过的报价单，先保留客户可见内容再重新 build；付款方式保留规则详见『手改 .docx 保留』 |
| 3 | 输出位置 | 最终生成的 `.docx` 放在 `quotation/YYYY-MM/` 下（YYYY-MM 为报价单日期所在年月）；`quotation.json`、`queried_services.json` 等过程文件写入其子目录；不写 skill 根目录；修改已有报价单时输出新文件 |
| 4 | Agent 不改脚本 | 执行报价任务不得修改 build/validate/verify/fetch 等脚本；业务/数据问题按业务处理，只有严重脚本缺陷才提示联系 SKILL 开发者。页眉版式同理，一律由 build 按配置生成，不要手改 `header1.xml`。**文字页眉**（8 套模板，共用 `china` 模板的中国主体在内）统一为「文本段落 → 唯一定位段（承载 logo + 蓝色分隔线）」，不要增删页眉末段——该段的高度正是正文避开蓝线的余量，动它会让蓝线压住正文「公司名称：」。logo 与蓝线必须锚在**同一段**：两个锚点的偏移量都从所在段落的顶端起算，同段时两者间距（`蓝线偏移 − logo 偏移 − logo 可见高度`，可见高度按 PNG alpha 量，位图自带的透明留白不算）才是模板常量；分开锚定会让蓝线随中文行高漂移而 logo 不动，字体缺字回退时蓝线就会压住 logo。承载 logo 的 run 必须沿用蓝线 run 的 `rPr`（字号与字体都会参与该段行高计算，即使是只装浮图、无文字的 run），否则末段变高、正文整体下移。**图片页眉**（配了 `header_image` 的 8 家）整段换成横幅，见『页眉的两种形态』。两种情况都已由 `tests/test_smoke.py` 锁定，改动后跑测试即可发现 |
| 5 | 重复 aiCode 不累加 | 用户重复输入同一 aiCode 时，数量和价格不翻倍，也不累加；大概率是用户误重复输入。若用户明确要求"多加一行"，则在报价单中新增同名服务行（用序号或 aiCode 末三位区分），每行各自独立 |

## 签约主体

| 签约主体 | `--entity` | 税率 | 默认币种 | 可切换币种 |
|----------|------------|------|----------|-----------|
| PT. SHAN HAI MAP (雅加达) | `jakarta` | 11% | IDR | RMB, USD |
| PT SHM CONSULTING INDONESIA (山海图咨询印尼) | `sci` | 11% | IDR | RMB, USD |
| 北京山海图科技有限公司 | `beijing` | 6% | RMB | USD |
| 北京山海图科技有限公司西安分公司 | `xian` | 6% | RMB | USD |
| 北京山海图科技有限公司深圳分公司 | `shenzhen` | 6% | RMB | USD |
| 北京山海图科技有限公司上海分公司 | `shanghai` | 1% | RMB | USD |
| 上海山海图新企业咨询有限公司 | `shanghai_new` | 1% | RMB | USD |
| SHAN HAI MAP CONSULTANCY PTE.LTD (新加坡) | `singapore` | 0% | SGD | RMB, USD |
| PT DEIN TALENT SOLUTIONS (德音人力) | `deyin` | 11% | IDR | USD |
| SHAN HAI MAP (THAILAND) CO., LTD. (泰国) | `thailand` | 7% | THB | RMB, USD |
| CÔNG TY TNHH SHANHAIMAP VIỆT NAM (越南) | `vietnam` | 8% | VND | RMB, USD |
| SHAN HAI MAP FOR CONSULTING CO (埃及) | `egypt` | 10% | EGP | RMB, USD |
| SHANHAIMAP SDN. BHD. (马来西亚) | `malaysia` | 8%（SST） | MYR | RMB, USD |

完整银行账户、地址、税号等详见 `config/entities.json` 和 `references/entity-bank-info.md`。

**主体别名识别**（用户说法 → `--entity`）。别名同时登记在 `config/entities.json` 各实体的 `aliases` 字段（脚本不读该字段，`--entity` 仍只接受实体 key；新增主体时记得一起补 `aliases`）：

| 用户可能说法 | `--entity` |
|--------------|------------|
| 雅加达 / 雅加达山海图 / PT. SHAN HAI MAP / SHM信头 / 印尼shanhaimap | `jakarta` |
| **SCI / 山海图咨询印尼 / 山海图咨询 / 印尼咨询 / PT SHM CONSULTING INDONESIA** | `sci` |
| **德音人力 / 德音 / 德音人力资源 / deyin / dein / DEIN / dein信头 / DEIN信头 / PT DEIN TALENT SOLUTIONS** | `deyin` |
| 北京 / 北京山海图 | `beijing` |
| 西安 / 西安分公司 | `xian` |
| 深圳 / 深圳分公司 | `shenzhen` |
| 上海 / 上海分公司 | `shanghai` |
| 上海新企业 / 上海山海图新企业 | `shanghai_new` |
| 新加坡 / SHAN HAI MAP CONSULTANCY | `singapore` |
| 泰国 / 曼谷 | `thailand` |
| 越南 / SHANHAIMAP VIỆT NAM | `vietnam` |
| 埃及 / SHAN HAI MAP FOR CONSULTING | `egypt` |
| 马来西亚 / 大马 / SHANHAIMAP SDN. BHD. | `malaysia` |

- 别名匹配**大小写不敏感**（`DEIN` = `dein` = `deyin`），中英文混写同样按上表识别。
- ⚠️ **出现「德音」二字或 `dein`/`deyin` 一律走 `deyin`**，不要误判成 `jakarta`：两者都是印尼主体、都能用 IDR，但公司名、页眉地址、银行账户完全不同（`deyin` = PT DEIN TALENT SOLUTIONS，BCA 6802 044 409）。
- ⚠️ **印尼有三个主体，别混**：`jakarta`（PT. SHAN HAI MAP）、`sci`（PT SHM CONSULTING INDONESIA，山海图咨询印尼）、`deyin`（PT DEIN TALENT SOLUTIONS，德音人力）。三家都能用 IDR，但公司名、银行账户各不相同；`sci` 与 `jakarta` 甚至连注册地址都一样，只能靠公司名区分。用户只说「山海图印尼」时按 `jakarta`，说「SCI」「咨询印尼」时才走 `sci`。
- 别名不覆盖「多主体/意图不明」的情形：同时提到两个主体、或只说「印尼」而未指明公司名时，仍须向用户确认。

⚠️ **马来西亚税金**：马来西亚主体没有增值税（VAT），而是**销售与服务税（SST，Sales & Service Tax）**。报价单汇总表税金行标签用「销售与服务税 8%」替代「增值税 8%」，税率仍走 `config/entities.json` 的 `vat_rate`（字段不变，仅标签不同，由 `tax_label` 控制）。

新增签约主体只需在 `config/entities.json` 添加新 key 并包含 `_meta.required_entity_fields` 所列字段（`payment_terms` 可选，缺省回退 `_meta.payment_terms_default`）；`--entity` 的 choices 由脚本从 `entities.json` 自动加载，无需改任何脚本。

**A4 打印**：build 会把模板的 `sectPr` 归一为 A4 纵向（11906×16838 DXA）+ 标准页边距（上下 679/1155、左右 1440/1133 DXA，页眉 227、页脚 227，即距纸张边缘 0.4cm），模板页面尺寸不合规时会强制纠正并在日志说明；verify 独立复核尺寸、方向与页边距。正文表格与页眉蓝线按设计略宽于文本栏（左右各溢出约 20pt），实测距纸张边缘仍 ≥6.8mm，在打印机可达范围内（一般 ≥5mm），属正常版式，不要为了"贴边"去改表格宽度。

**页眉的两种形态**：主体分「文字页眉」与「图片页眉」两类，由 `config/entities.json` 该实体是否配了 `header_image` 决定。已配图片页眉的 8 家：`beijing`、`xian`、`shenzhen`、`shanghai`、`shanghai_new`、`jakarta`、`sci`、`deyin`；其余（`singapore`、`thailand`、`vietnam`、`egypt`、`malaysia`）仍是文字页眉，下面两段只适用于它们。

**图片页眉**：整个页眉（logo、公司名、地址、Web）替换成 `assets/页眉-<公司>.png` 一幅横幅，蓝线保留。布局由三件事决定，都在 `_meta.header_image_defaults` 与实体的 `header_image` 里：

- **水平位置**：横幅的**可见部分与正文栏左右对齐**（左缘 = 左边距 25.4mm、宽 = 正文栏宽 164.6mm），由 `A4_MARGINS` 算出；需要偏离时用可选字段 `ink_left_mm` / `ink_width_cm` 覆盖。蓝线仍按原设计比正文栏宽约 20pt，两者不等宽是有意的；
- `ink_top_mm`（6.0，图墨迹顶距纸张上缘）、`line_gap_mm`（3.0，图墨迹底→蓝线）、`body_top_mm`（30.0，正文首行距纸张上缘）；
- 实体级补充字段 `bbox_px`：该图 alpha 边界在 PNG 里的像素坐标 `[x0, y0, x1, y1]`，各图不同，**配新实体时必须实测，不能照抄**。

横幅用**浮动锚定**（`wp:anchor` + `wp:wrapNone`），再按 `bbox_px` 用 `a:srcRect` **裁掉 PNG 自带的透明边距**（PNG 文件本身一个字节都不动）。裁过之后图片框 == 图墨迹，可见内容一个像素不变，但框不会再顶到纸张上缘、也不会越过正文栏——这些透明边距有 5.6~7.0mm 之厚，不裁的话框会贴到纸边（实测雅加达框顶距纸边仅 0.37mm）并压到正文首行前 0.66mm。页眉高度完全由承载段的 `space-after` 决定。`ink_top_mm` 必须 ≥ `MIN_PRINTABLE_INK_TOP_MM`（5.0mm，多数打印机不可打印区约 4.2mm），低于此值 build 会 warning、verify 直接报错。图片页眉的公司名与地址都印在图里、XML 中没有对应文字，也改不动，因此 verify 会**显式跳过**「页眉公司名 vs 银行」与「页眉地址归属」两项检查并打印说明，改为核对横幅是否在位、锚点与裁剪参数是否与配置一致、图片框有没有越出纸张或正文栏、嵌入的是否就是原图。

⚠️ **不要让横幅图片框越出纸张或正文栏**：不要把透明边距留着靠「框顶伸到纸外、那截全透明不打印」来兜——落点依赖针对单个渲染器标定的漂移常量（`HEADER_IMAGE_INK_DRIFT_MM` / `HEADER_IMAGE_LINE_DRIFT_MM`），Word/WPS 与 LibreOffice 的浮动锚点落点不同，换个渲染器就可能真的越界。裁掉透明边距后框有多大就是墨迹有多大，与渲染器无关。`verify` 与 `tests/test_smoke.py` 都会拦下未裁剪或越界的横幅。

文字页眉的公司地址过长、会换行（页眉 logo 挤压文本宽度所致）时，为该实体加可选字段 `header_address_size_pt`（地址字号，单位 pt，如 `8`），地址行按此字号渲染、公司名与 Web 行不变。目前只有 `malaysia` 设了 8 号（原 `jakarta`、`deyin` 的该字段随图片页眉改造一并删除——图片页眉不读它，留着是死配置）。新增实体若地址超过一行同样加上。

文字页眉的公司名称对齐方式：由可选字段 `header_company_align`（`right`/`center`，缺省 `center`）控制。公司名过长时居中会把左端推进左侧 logo（logo 浮动锚定在同一段落带内），这些主体一律右对齐——右对齐把公司名右端钉在右页边距上，从而让出 logo 的空间：`thailand`、`vietnam`、`egypt`、`singapore` 已设 `right`，其余主体居中。新增主体若公司名超过约 25 字符，先渲染确认不压 logo 再决定——实测 26 字符居中时距 logo 仅 0.2mm，已在碰撞边缘。页眉 Web 行下方不留空行、蓝线分隔线位置不变，均由 build 脚本自动处理，无需配置。

⚠️ **SWIFT CODE 注意**：生成美元（USD）报价前，检查 `config/entities.json` 中该实体**所选币种对应**的银行信息（有 `bank_lines_by_currency` 时取对应币种，否则取 `bank_lines`）是否含 SWIFT CODE（现状：仅 beijing 没有，按刘旭 2026-09-13 确认北京不需要 SWIFT CODE，别再提示用户补全；xian/shenzhen/shanghai/shanghai_new 等均已配置，见 `references/entity-bank-info.md`）。若用户要求补全，向用户确认具体 SWIFT CODE 后更新 `config/entities.json`，不要凭记忆假设。

标题、日期、客户、合同号、付款条件写入 `quote_meta`。

## WeCom 交互节奏

在 WeCom（企业微信）网关上，用户在一次回复中看不到中间进度——所有工具调用完成后才显示最终消息。fetch API 耗时数秒至十几秒，加上 validate/build/verify，全程对用户是黑盒。

**分步交互**，每步独立回复，用户全程可见进展：

1. 🔍 正在查询 N 个服务... → fetch
2. ✅ 查询完成，列出服务名/数量/价格/币种 → 币种转换
3. 📝 正在整理数据、生成报价单... → validate + build + verify
4. ✅ 报价单已生成，附摘要表格

## 场景判断

先确定目录与工作区（见流程规则 #3「输出位置」）：

- `QUOTATION_DIR`（报价单目录）= `<用户工作目录>/quotation/<报价单日期的年月>/`，如 `<用户工作目录>/quotation/2026-06`。
- `WORKDIR`（工作子目录）= `QUOTATION_DIR/quotation-<YYYYMMDD>-<客户简称>`，`YYYYMMDD` 为报价单日期，`<客户简称>` 替换为实际客户名（没有客户名时才用 `client`）。
- **两者一律用绝对路径**：`QUOTATION_DIR` 与 `WORKDIR` 都必须先解析成绝对路径，命令里直接写绝对路径。脚本执行时会 `cd` 到 skill 根目录，相对路径会被解析到 skill 目录下，过程文件就写进 skill 根目录了（违反流程规则 #3）。用户工作目录取启动本 skill 时所在的工作目录（`pwd`），过程文件写入 `WORKDIR`。
- 取「年月」注意 Linux/macOS 差异：Linux `date -d '2026-06-15' +%Y-%m`；macOS `date -j -f '%Y-%m-%d' '2026-06-15' +%Y-%m`。

| 场景 | 条件 | 路径 |
|------|------|------|
| A 新建 | 用户提供 aiCode，工作区无 quotation.json | fetch → organize → validate → build → verify |
| B 修改 | 用户未提供 aiCode | 定位 → 保留手改/快照对比 → 修改 → validate → build → verify |
| C 追加 | 用户提供 aiCode + 既有 quotation.json | 定位 → 保留手改/快照对比 → fetch 新服务 → 合并 → validate → build → verify |

（仅限修改/追加场景）先**定位**既有报价单：在用户工作目录下 `find "<用户工作目录>/quotation" -name quotation.json`，连同同目录的 `.docx` 一起列给用户确认是哪一单，确认后再读它所在目录下的 `quotation.json`；不要凭客户名猜测。找不到时要求用户提供路径，不从 sample 凭空改。新建场景仍按『数据文件』章节从 sample 复制起步。

## 手改 `.docx` 保留

付款方式/付款条件属于客户可见手改内容。初次生成的默认付款方式只作占位，后续用户手动修改或让 Agent 修改后，重新生成服务、金额、优惠时不得把付款方式还原为 `quotation.json` 或实体默认值。脚本只做合理性提醒，不因付款方式 warning 阻断生成。

`build_quotation.py` 默认行为：
- 如果 `--output` 指向已存在的 `.docx`，自动从该旧文件读取付款方式并用于本次重建。
- 如果输出到新文件但需要沿用某个旧报价单的付款方式，传 `--preserve-payment-from "$WORKDIR/已编辑报价单.docx"`。
- 只有明确要用 `quotation.json` / 实体默认付款方式覆盖旧文件时，才传 `--overwrite-payment-terms`。
- 每次 build 都会打印一行「付款方式来源：…」（旧报价单 / quotation.json / 实体默认配置）。按流程修改报价单是**输出新文件**，此时不会自动沿用旧单子的付款方式，build 会提示同目录已有的报价单——看到这行就核对一下手改有没有被沿用，需要就补 `--preserve-payment-from`。
- 如果指定了旧 `.docx` 但付款方式提取失败，必须立即中止；不得回退到 `quotation.json` 或实体默认付款方式，也不得覆盖旧文件。
- 仅当旧 `.docx` 付款方式提取失败，或用户明确要求修改付款方式时，才向用户询问并确认付款方式。用户明确告知后，将其写入本次工作目录的 `quote_meta.payment_terms`，并传 `--overwrite-payment-terms` 重新生成；这不属于从旧文档同步数据。
- `validate` / `build` / `verify` 会提醒明显不合理的付款方式：付款比例合计超过 100%，或付款金额合计大于合同含税总计。

```bash
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --preserve-payment-from "$WORKDIR/已编辑报价单.docx" --output "$QUOTATION_DIR/新版报价单.docx"
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

`markdownify` 是可选的：装了服务内容 Markdown 更干净，没装脚本用内置兜底转换器，并在 **stderr** 打一行 `⚠️ markdownify not available, using fallback HTML cleaner`。因为走的是 stderr，`> "$WORKDIR/queried_services.json"` 拿到的 JSON 依然完整，**不需要任何额外处理**。想装就装（`pip install markdownify`，pip 不可用则 `uv pip install markdownify --system`）。

⚠️ 只有自己把 stderr 也并进文件时才会污染 JSON（例如 `... > x.json 2>&1`）。这时按内容过滤警告行，**不要 `tail -n +2` 盲删首行**——正常 JSON 的第一行是 `{`，删掉它反而会把好文件弄坏，症状正好就是下面那条报错：

```bash
python3 scripts/fetch_services.py '...' 2>&1 | grep -v '^⚠️' > "$WORKDIR/queried_services.json"
```

`convert_currency.py --query-result` 报 `Cannot read query result file` 时，先看 `queried_services.json` 首行是不是 `{`：不是 → 上面那种 stderr 混入，重新 fetch 并过滤警告行；是 → 文件没写成或 fetch 本身失败，重新 fetch。

批量换算时若个别服务报 `Missing price or currency` / `Cannot parse price`，脚本退出码为 1，但 stdout 的 JSON 依然完整可用。这是「该服务价格面议或未标价」的正常信号（见数据规则 2），按提示向用户补充总价即可，**不要当成 fetch 失败重跑**。

失败处理：
- API 顶层 `success: false`：必须将 API 返回字段 `message` 的文字原样转达给用户，不生成报价单。
- 任一服务内部 `success: false` / `查询成功: false`：必须列出失败服务的 aiCode 和错误信息，立即中断，不得仅使用成功项继续生成。API 原始错误字段为 `message`；脚本输出中的 `errors` 只是便于 Agent 展示的归一化包装，不是 API 原生字段。
- API 顶层成功但未返回任何服务：视为查询失败，必须中断并告知用户未查询到服务。
- API 返回成功但服务内容为空或仅含引导语（如"点击对应规格查看业务完整详情"）：内容不足以生成报价单，必须中断并告知用户"API 未返回服务详情，请确认 aiCode 或提供完整服务信息"。不可仅凭价格和名称强行生成。
- 展示给用户只列服务名、数量及单位、价格及币种；不展示原始 JSON、规格、有效期。

## 确定实体与币种

### 实体默认

用户明确提到北京/西安/深圳/上海/上海新企业/雅加达/新加坡/德音人力（德音、deyin、dein、DEIN）/泰国/越南/埃及/马来西亚时使用对应实体（完整别名见上方「主体别名识别」表）。用户未指定时：
- 服务原币种为 IDR 且用户未要求人民币/美元报价，默认倾向 `jakarta`。
- 服务原币种为 THB 且用户未要求人民币/美元报价，默认倾向 `thailand`。
- 服务原币种为 SGD 且用户未指定主体，默认倾向 `singapore`。
- 服务原币种为 VND 且用户未要求人民币/美元报价，默认倾向 `vietnam`。
- 服务原币种为 EGP 且用户未要求人民币/美元报价，默认倾向 `egypt`。
- 服务原币种为 MYR 且用户未要求人民币/美元报价，默认倾向 `malaysia`。
- 服务原币种为 RMB/CNY 且未指定主体，默认倾向中国主体（按用户提到的城市选 beijing/xian/shenzhen/shanghai/shanghai_new）。
- 客户要求人民币/美元付款时，不代表必须选择中国主体；`jakarta` 主体也可使用 RMB/USD 报价和收款。
- 需要特定币种报价但未说明签约主体时，优先沿用当前/已选主体；没有当前主体时再向用户确认主体。
- 多币种服务、追加到既有报价单、或主体/币种意图不明确时，必须确认。

### 币种限制

币种可由 `_meta.target_currency` 指定（`IDR`/`RMB`/`USD`/`SGD`/`THB`/`VND`/`EGP`/`MYR`），优先级高于实体配置默认币种；例如 `applicable_entity: jakarta` 且 `target_currency: USD` 表示使用雅加达主体生成美元报价单。

⚠️ **实体币种限制**：报价单币种仅支持**签约主体所在国家的本币**与**人民币/美元**外币，且必须在该实体 `allowed_currencies` 内（见上方「签约主体」表）。用户要求切换支付币种时，必须先查 `config/entities.json` 中该实体 `allowed_currencies`，不可凭记忆假设。本币报价只能用于对应主体（IDR→jakarta/sci/deyin、SGD→singapore、THB→thailand、VND→vietnam、EGP→egypt、MYR→malaysia）。若币种不被当前实体支持，向用户提供两个选项：(1) 用 `convert_currency.py` 换算后，在备注中只写等值金额数字，不得出现汇率/折算/兑换字样（`validate_data.py` 会拦下这些关键词）；(2) 切换到支持该币种的实体。

### 泰国预扣税

**预扣税目前仅泰国主体支持**（唯一配了 `withholding_tax_rate` 的实体）。

计算口径：**预扣税 =（小计 − 优惠）× 3%**，基数是税前小计，**不乘增值税**；**含税总计 = 税前小计 + 增值税 − 预扣税**。该口径由 `calculate_amounts()` 实现，所有数值取脚本输出。

使用泰国主体（`thailand`）报价时，**必须先询问用户是否需要扣除预扣税（Withholding Tax，税率 3%）**。若用户明确要求扣除，在 `quotation.json` **顶层**设置 `withholding_tax: true`（与 `discount_amount`/`notes` 同级，**不要放进 `_meta`**）；若用户明确说不需要，则填 `withholding_tax: false`，报价单中不显示和计算预扣税。泰国主体漏填该字段时 `validate_data.py` 会 warning 提醒（不阻断），build 也会再提醒一次并按不扣税生成。

其他主体**不要**写 `withholding_tax: true`：`validate_data.py` 会直接报错拦下（build 会静默跳过、verify 必然失败）。

### 汇率与换算

换币种必须用脚本，不心算：

```bash
python3 scripts/convert_currency.py --query-result "$WORKDIR/queried_services.json" --to RMB --json-only
python3 scripts/convert_currency.py --query-result "$WORKDIR/queried_services.json" --to USD --json-only
python3 scripts/convert_currency.py --amount 250000000 --from IDR --to RMB --rateToCny 2173.91 --json-only
python3 scripts/convert_currency.py --amount 115000 --from RMB --to IDR --rateToCny 2173.91 --json-only
python3 scripts/convert_currency.py --amount 30000000 --from IDR --to USD --rateToUsd 17875 --json-only
```

⚠️ **本文所有示例里的汇率数字（`2173.91`、`17875`、`5.45`、`1.05`、`2250`、`4.85`、`4.75` 等）只是格式演示，不是可用汇率。**实际汇率一律取自 `queried_services.json` 的 `人民币兑换服务币种汇率`/`美元兑换服务币种汇率`；照抄示例数字会直接算错价格。

`rateToCny` 表示 `1 CNY = N 服务币种`，`rateToUsd` 表示 `1 USD = N 服务币种`。修改已有报价单时优先使用原始 `queried_services.json` 或 `quotation.json` 留档汇率；没有留档汇率，必须让用户提供或确认汇率。

**币种与换算（一律用脚本，8 个币种全覆盖）**：API 返回币种可能为 IDR、CNY、MYR、SGD、THB、EGP、VND；报价单目标币种见『币种限制』。`convert_currency.py` 覆盖全部 8 个币种：

| 场景 | 用哪个汇率 | 命令 |
|------|-----------|------|
| 外币 → 人民币 | 该外币的 `rateToCny`（÷） | `--from THB --to RMB --rateToCny 4.85` |
| 外币 → 美元 | 该外币的 `rateToUsd`（÷） | `--from MYR --to USD --rateToUsd 4.75` |
| 人民币/美元 → 外币 | 同一汇率反向（×） | `--from RMB --to SGD --rateToCny 5.45` |
| 人民币 ↔ 美元 | 一条外币的两个汇率做桥 | `--from RMB --to USD --rateToCny 2250 --rateToUsd 17710` |
| 外币 → 外币 | **API 无此汇率**：先向用户索取 | `--from THB --to VND --cross-rate 1.05`（1 THB = 1.05 VND） |

取整为 **ROUND_HALF_UP**（四舍五入，与官网一致）。批量换算：`--query-result "$WORKDIR/queried_services.json" --to <币种> --json-only`，源币种自动识别；目标币种若整批服务里拿不到该币种汇率（例如全批都是人民币定价、要报泰铢），脚本会报错提示向用户索取，**不得自己估汇率**。目标币种与服务币种相同则无需换算（identity）。`_meta` 留档源币种与用到的汇率。

## 价格计算（API → quotation.json）

API 的 `服务价格` 已经是 **总价**（对应服务数量的总价），`convert_currency.py` 换算后直接得到行项总价。不需要再乘数量。

计算链路：

```
API 服务价格 (总价，已含数量)
  → convert_currency.py 按汇率换算（如需换币种）
  → 换算后价格 = quotation.json price（行项总价，整数）
```

示例：API 返回 服务价格=30,000,000 IDR、服务数量=2（总价已含数量，不乘）、rateToCny=2250
```
convert: 30,000,000 ÷ 2,250 = 13,333 RMB  （脚本自动取整）
price:   13,333                            （直接写入 quotation.json）
```

`convert_currency.py` 输出的换算后价格即为行项总价，Agent 直接写入 `quotation.json`，不再乘数量。生成报价单后主动展示完整计算过程（API 总价 → 汇率 → 换算后总价 → 增值税 → 含税总计），用户要求时可追溯到每一步来源。

税金小数位：人民币/美元/泰铢/新币/埃及镑/马币（RMB/USD/THB/SGD/EGP/MYR）的增值税、预扣税、含税总计**仅当金额有小数值时才显示小数位**（如 `2,100.07`、`70.35`），整数金额不显示 `.00`（如 `70`、`2,100`）；印尼盾/越南盾（IDR/VND）恒为整数（如 `1,100,000`）。由 build/verify 脚本按币种自动处理，Agent 无需手工干预。价格一律不带货币符号（见数据规则 7）。

## 整理服务内容

从 API Markdown 服务内容提取结构化数据。**5 个以上服务时**，用脚本（Python）批量提取，避免逐条手写。

| API 内容 | 目标字段 | 规则 |
|---------|----------|------|
| 服务编码（API `服务编码`） | `services[].items[].code` | **必填，每一列服务都要有**。从 `queried_services.json` 的 `服务编码` 逐条抄入（如 `ID0117`），build 时服务名自动渲染为 `编码-服务名`（如 `ID0117-公司注册`），报价单所有表格的服务名都带这个编码。**禁止自行编造编码**；API 对每个查到的服务都返回 `服务编码`，取不到就重查，不要留空 |
| 办理时间 | `services[].items[].days` | 提取数字 + 单位（**工作日**或**月**）；多个时间取最长；**填单次/每件办理时间，不乘数量**（如 3 个服务各 15 个工作日 → 填 `15个工作日`，不写 `15个工作日x3`） |
| 基本信息/服务说明/服务概述 | `services[].items[].note` | 摘要 100-250 字；不足 100 字完整呈现（脚本仅在 <40 字时提示确认信息是否不足）；**若基本信息中包含金额相关描述（如罚款金额、收费标准、官方费用等），必须将金额信息总结写入备注，且其中的金额一律照抄 API 原文的数字与单位，不得换算、汇总或改写**；**若基本信息中包含带 `*` 的说明项（如 `*备注：`、`*注：`、`*注意：` 等），要将 `*` 标记的内容尽可能完整总结到备注中** |
| 费用包含/服务包含 | `fee_details[].include` | 每条一项，保留原文 |
| 费用不含/不包含 | `fee_details[].exclude` + `notes` | 公共不含项进 `notes`；服务特有项留 `exclude`。`notes` 只放不含项与通用说明，**不放办理时间免责声明**——该表没有办理时间列 |
| 办理流程/服务流程 | `process_data[].process` | 每步一条，保留序号 |
| 交付文件/交付材料 | `process_data[].deliverables` | 每项一条；数量 >1 时**编号写进 JSON**（`1. 2. 3.`，示例均为手工编号），build 只在检测到未编号时才兜底补号，别指望它替你编号 |
| 所需资料/所需材料 | `doc_data[].docs` | 每项一条（**一个元素＝一个段落**，不要把多行内容塞进一条，否则渲染时换行会被压成空格、子项挤在一行里），保留子层级编号；**要用层级缩进就在行首写全角空格**（1 个＝1 级、最多 6 级），build 会把它转成段落左缩进 `w:ind`（每级 360 twips）并在末尾打印「所需资料及信息行缩进：N 行」。行首写半角空格或 NBSP 无效——`quotation_schema` 的 `item.strip()` 会把它们剥掉 |

忽略 API 内容里的付款方式、退款/售后、发票相关。用户明确指定报价单付款条件时写入 `quote_meta.payment_terms`。

公共不含项规则：比较各服务的不含项；多个/全部服务共同适用，或语义上对整份报价共同适用的项目，统一写入 `notes` 一次。**notes 已写的不含项，`exclude` 不再重复**——哪怕只有一个服务也是如此。`config/entities.json` 的 `universal_excludes` 只是常见公共项种子示例，不是白名单。服务无特有不含项时 `exclude: []`。

示例：A/B 都有“文件翻译费用、资料快递费用（国际）”，A 另有“相关资质办理费”，B 另有“政府规费”：
- `A.exclude = ["相关资质办理费"]`
- `B.exclude = ["政府规费"]`
- `notes` 统一写“以上服务报价不包括：文件翻译费用（如需）、资料快递费用（国际）。”

**`notes` 里不写办理时间免责声明**：`notes` 渲染在「服务内容」表正下方，而该表只有 序号/服务内容/数量/价格/备注 五列，没有办理时间列——「上述办理时间不包括收集材料时间、检查资料时间、客户签字盖章反馈时间、客户修改或补充资料的时间和文件邮寄时间。」在这里指不到任何上文（办理时间在更靠后的流程表里）。**报错只说明位置不对，不代表这条要求被废弃**：这句话要表达的信息已由文末标准备注第 1 条完整覆盖——「以上办理时间为收集齐所需资料及信息开始的官方办理时间，法定节假日及办证政府机构休息日不算入办理时间」；客户准备材料、签字盖章反馈、文件邮寄都发生在「收集齐资料」之前，本就不计入办理时间。所以处理方式就是从 `notes` 删除：不要改写成别的措辞，也不必另找位置补写一遍。API 的每个服务「备注」段都带这句样板文，汇总基本信息时极易顺手带进 `notes`；`validate_data.py` 会直接报错拦下。

判定用的是「办理时间 + 近邻的不包括 + 样板文列举的时间项」三者同时命中，所以「办理时间不包括节假日」这类**正常业务说明不会被误判**（build 也不会剔除）。

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
| `services` | 分组列表；每项含 `id/name/code/quantity/days/price/note`——**`code` 必填**（产品编码，展示时自动拼到服务名前，取自 `queried_services.json` 的 `服务编码`，不得编造）；`name` 和 `code` 分开放，`name` 不带数量、不带编码 |
| `discount_amount` | 整数；无优惠填 0；优惠 ≤ 小计 |
| `withholding_tax` | 布尔（**顶层**字段，与 `discount_amount`/`notes` 同级，不要放进 `_meta`）；泰国主体扣预扣税时填 `true`，否则不填或填 `false`。缺失或误放 `_meta` 时 build 会静默按不扣税处理 |
| `notes` | 通用备注；公共不含项在这里统一显示。**不得写办理时间免责声明**（「服务内容」表没有办理时间列，`validate_data.py` 报错拦下） |
| `fee_details` | 每项含 `name/include/exclude/note`；`name` 必须匹配服务名 |
| `process_data` | 每项含 `name/process/deliverables` |
| `doc_data` | 每项含 `name/docs` |

## 预检 → 生成 → 验证

```bash
python3 scripts/validate_data.py --entity xian --data "$WORKDIR/quotation.json"
python3 scripts/build_quotation.py --entity xian --data "$WORKDIR/quotation.json" --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/build_quotation.py --entity shenzhen --data "$WORKDIR/quotation.json" --title-line1 "深圳公司注册" --title-line2 "服务方案" --quote-date 2026-06-15 --output "$QUOTATION_DIR/报价单.docx"
python3 scripts/verify_quotation.py --entity xian --input "$QUOTATION_DIR/报价单.docx" --data "$WORKDIR/quotation.json"
```

validate error → 必须修复，warning → 判断后处理。`--entity` 必传。`--title-line1/2`、`--quote-date` 优先级：命令行 > quote_meta > 默认值。报价币种优先级：`_meta.target_currency` > entity 默认币种。verify 自动检查页眉/银行/签名、服务名覆盖、金额公式、字体、A4 打印安全（页面尺寸 + 方向 + 页边距），并在传入 `--data` 时对比 `_meta.applicable_entity` / `_meta.target_currency`。图片页眉的主体没有可核对的页眉文字，verify 会显式跳过「页眉公司名 vs 银行」与「页眉地址归属」，改核横幅是否在位、锚点与 `srcRect` 裁剪参数是否与配置一致、图片框有没有越过纸张上缘或正文栏左右界（日志里有说明，不是静默不检查）。目视补充：标题/客户/日期正确，表格无错位乱码。

## 修复与异常边界

| 问题 | 处理 |
|------|------|
| verify 报页眉公司名与银行公司名不一致但只差一个 `.` | 正常现象（如 `Pte.Ltd` vs `Pte.Ltd.`、`PT. SHAN HAI MAP` vs `PT SHAN HAI MAP`）。verify 用 `normalize_company_name()`（去点号 + 归一化空白）比较，点号差异不再判为异常；真正不同的公司名仍报错 |
| 服务内容表格「序号」列显示服务编码而非数字 | `build_quotation.py` 使用自增计数器（1, 2, 3...）生成序号，不依赖 `item['id']`；旧数据 `id` 填了服务编码也不影响显示 |
| 页眉/银行/签名不一致 | 检查 `--entity` 和 `config/entities.json`；属于业务/数据问题，不提示联系 SKILL 开发者 |
| 服务名覆盖缺失 | 补齐 `fee_details/process_data/doc_data` 中缺失的同名条目 |
| validate 报 `services[i].items[j].code is required`，或成稿服务内容只有服务名没有编码 | 汇总时漏抄 `服务编码`。回到 `queried_services.json`，按 `服务名称` 找到对应 `服务编码` 填入 `services[].items[].code`（如 `ID0117`），**不要自己编号码**；填完重新预检。verify 会拿数据文件的原始服务名跟成稿逐字比对，成稿服务名 == 原始服务名即报「服务内容缺少服务编码」 |
| 金额或币种异常 | 检查 `discount_amount`、服务整数总价、汇率和实体币种；重新运行预检 |
| 公共不含项重复出现在 `exclude` | 从各服务 `exclude` 移除公共项，在 `notes` 中统一显示 |
| validate 报 `notes[i] 是办理时间免责声明，必须删除` | API 服务「备注」段的样板文被带进了 `notes`。`notes` 渲染在「服务内容」表下，该表没有办理时间列，「上述办理时间」无所指；直接从 `notes` 删除该条（**不要改写成别的措辞，也不必另找位置补写**——文末标准备注第 1 条已覆盖该信息）。build 也会兜底剔除并告警 |
| `convert_currency.py` 报 `Cannot read query result file` | 先看 `queried_services.json` 首行是不是 `{`。不是 → fetch 时把 stderr 并进了文件（`2>&1`），按『查询服务信息』章节重新 fetch 并过滤 `⚠️` 开头的行；`tail -n +2` 会连 JSON 首行一起删掉，别用。首行正常 → 文件没写成或 fetch 本身失败，重新 fetch |
| validate 报 `withholding_tax 仅支持…未配置` | 只有泰国主体支持预扣税，把 `withholding_tax` 改为 `false` 或删除该字段；见『泰国预扣税』 |
| 服务名重复 `Duplicate service name` | 当多个 aiCode 返回相同 `服务名称` 时，用 aiCode 末三位区分（如 `公司注册-114`、`公司注册-818`）；同步更新 `fee_details/process_data/doc_data` 中所有 name 引用 |
| `build` 报 `Invalid quotation data` | 先跑 `validate_data.py` 定位字段并修正 |
| `verify` 失败 | 除付款方式等客户最终处理内容外，优先修改 `quotation.json` 后重新 build；不要手改其他 `.docx` 内容 |
| 用户质疑价格/计算公式 | 展示完整计算链路：API 总价 → 汇率 → 换算后总价 → 增值税 → 含税总计，每步附带来源值 |
| 美元报价缺少 SWIFT CODE | 仅 beijing 无 SWIFT CODE，且系有意不配（2026-09-13 用户确认），客户问起就说明该主体不使用 SWIFT；其他主体若确有缺口 → 向用户确认具体 SWIFT 后补入 `config/entities.json`，参考 `references/entity-bank-info.md` |
| 服务币种不在 8 币种内（如 HKD），或要把外币换成另一种外币 | 8 个币种（IDR/RMB/USD/SGD/THB/VND/EGP/MYR）脚本直接算；其他币种显式传汇率即可（`--rateToCny/--rateToUsd`）；**外币 → 外币（两侧都不是人民币/美元）API 没有汇率数据，必须先向用户索取**，再用 `--cross-rate N`（1 from = N to）；见 `references/edge-cases.md`「外币 → 外币」 |
| 新加坡元（SGD）报价 | 服务币种即 SGD 时直接填价；RMB/USD → SGD 用 SGD 的汇率走脚本（`--from RMB --to SGD --rateToCny 5.45`）；IDR/THB 等 → SGD 属外币→外币，API 无汇率，须向用户索取后用 `--cross-rate N`；`_meta` 注明 |
| `fee_details[].include` 不能为空 | validate 报 `include is required and must be a non-empty list` → API 未列费用包含项的服务，至少填 `"山海图服务费"` |
| `quote_meta.payment_terms` 留空数组 | validate 报 `payment_terms must be a non-empty list when provided`（不会静默回退实体默认值）→ 从 `config/entities.json` 复制该实体 `payment_terms` 填入，或写用户明确指定的付款条件 |
| 用户提供分享链接而非 aiCode | 分享链接的 `sharingRecordId` 不是 aiCode，无法 resolve；见 `references/edge-cases.md`「分享链接而非 aiCode」 |
| API 返回 "AI Code 无效" | aiCode 中服务名部分的空格必须与数据库完全一致（如 `JSHK 账户维护` 不能写成 `JSHK账户维护`）。若用户坚持 aiCode 正确，检查空格是否遗漏后再重试 |
| 修改付款比例后 docx 仍显示旧比例 | build 默认从旧 `.docx` 保留付款方式。`quotation.json` 改了付款条件但 rebuild 后未生效 → 必须加 `--overwrite-payment-terms` 强制覆盖 |
| 换算后价格与官网差 1 元 | `convert_currency.py` 用 **ROUND_HALF_UP（四舍五入）**，不是截断：18,400,000 ÷ 2,250 = **8,178**（1.16.2 之前的文档误写「截断得 8,177」，已导致 5 张历史单各少算 1 元，如 VND 80,000,000 ÷ 3,893.98 成稿写成 20,544、应为 20,545）。遇到差异先与用户确认采用哪个口径，**不要**自行改写已交付价格 |
| 调整服务顺序后报价单未变化 | 仅改 `services[].items[]` 顺序不够，须同步重排 `fee_details`/`process_data`/`doc_data`，见 `references/reorder-services.md` |
| 追加服务时复用已有查询数据 | 同一批次多个报价单共享部分 aiCode 时，可从已有 `queried_services.json` 提取目标服务，避免重复 fetch；从已转换价格列表中对应取值 |
| BPO/百分比定价服务 | BPO 业务流程外包等按比例收费的服务，`price` 设为 0，费率结构在 `note` 和 `notes` 中说明（如"8%月用工总成本"），押金等附加费同样在备注说明。validate 会报 price 过小 warning，可忽略 |
| 测试费作为独立行项 | 用户要求加测试费时，新增独立服务行项（如"样品测试费"），单独列金额，填写简略 `process/deliverables/docs`（如 `["样品送检"]`），`days` 填 `"-"` |
| 用户用「条」表示百万 | 「条」= juta = 百万印尼盾，如「120条」= Rp120,000,000。报价单按数字填，不保留「条」字；向用户展示时可直接换算展示 |
| `rateToCny=1.0` 且 `服务币种=IDR` | 这是 API 数据标记错误：实际为人民币定价服务，系统误标为 IDR。不要按 IDR 换算，直接当 RMB 价格处理。**必须向用户展示此异常并确认报价币种**——用户可能选 RMB（直接用 API 价格）或 IDR（需用户提供真实汇率重新换算）。`_meta` 中注明此异常 |
| `服务币种=IDR` 但 `rateToCny` 异常低 + 服务编码非 ID 前缀 | API 币种标记错误，按服务编码前缀判断真实币种；见 `references/edge-cases.md`「rateToCny 异常低」 |
| API 服务内容 Markdown 格式不一致导致提取失败 | 用 `str.find()` 定位 + 截取，不用正则；见 `references/edge-cases.md`「Markdown 格式不一致」 |
| `doc_data[].docs` 为空导致 validate 失败 | 补兜底 `or ["请咨询山海图获取详细资料清单"]`；见 `references/edge-cases.md`「docs 为空」 |
| 「所需资料及信息」列子项挤在一行 / 看不到层级缩进 | ① 先拆行：一个数组元素只放一行（多行塞一条时，段落内换行会被压成空格）；② 缩进只在**行首写全角空格**（U+3000）才生效，1 个＝1 级；半角空格 / NBSP / U+3164 / U+2800 / U+200B 都无效（`quotation_schema` 的 `item.strip()` 剥掉空白，填充字符不产生位移）。build 会打印「所需资料及信息行缩进：N 行」，没有这行就说明一条都没标层级 |

业务/数据问题包括但不限于：数据填写错误、缺字段、金额不一致、实体选择错误、页眉公司名称和签约名称不一致、金额过大疑似选错币种、付款金额与合同金额不一致、付款条件/优惠/税率/签约主体等业务口径不明确。此类问题应修数据或向用户确认，不许提示联系 SKILL 开发者。

只有排除业务/数据问题后，同一份有效数据仍触发脚本异常、校验逻辑明显错误、生成内容与配置矛盾，且严重影响报价正确性或无法交付时，才暂停交付，展示缺陷问题、输入数据/命令/报错摘要，并提示联系 SKILL 开发者。执行报价任务时不得修改 skill 脚本，不得绕过校验。
