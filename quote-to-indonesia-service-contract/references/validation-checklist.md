# 校验清单

规则细节见 [party-b-rules.md](party-b-rules.md) 和 [contract-field-rules.md](contract-field-rules.md)。主体数据见 [parties.json](parties.json)。本清单只列出检查项与失败判定，不重复规则。

校验按三层 fail-fast 执行：**任一层次失败都必须先修复再继续，禁止跨层跳过。** 中文版本和三语版本的 Gate 2 大部分可由 `scripts/verify_contract.py` 自动执行。

---

## Gate 1 · 前置门（生成开始前）

失败任何一项 → **立即中止生成**，请用户修正输入后再重新开始。不得进入步骤 2。

### parties.json 完整性

- [ ] parties.json 可被解析为合法 JSON
- [ ] 每个 party 条目包含必需字段：name, matchType, country, address, representative, title, email
- [ ] `alias_group` 类型的条目包含非空 `aliases` 数组
- [ ] `address.source` 为 `fixed` 的条目包含非空 `address.ref`
- [ ] `address.ref` 的值命中 `parties.json` 顶层 `fixedAddresses` 中的某个 key

### 报价单可识别性

- [ ] 报价单能按 [party-b-rules.md](party-b-rules.md) 的分层识别顺序唯一命中 `quotationEntityBindings` 中某个主体
- [ ] 命中条目的 `country` 为 `CN` 或 `ID`，能确定模板路由

---

## Gate 2 · 生成中检查（每步完成后立即校验）

失败任何一项 → **在当前步骤内修复后再进入下一步**。中文版本可直接调用 `scripts/verify_contract.py`，失败问题会一次性列出。

### 主体一致性

- [ ] 合同首页乙方名称等于命中的 `quotationEntityBindings[].headerName`
- [ ] 所有主体：合同乙方地址符合 parties.json 对应条目的 `address` 声明（`header` 则与页眉逐字一致；`fixed` 则等于 `fixedAddresses[ref]`）
- [ ] 合同首页、签字页及全文所有乙方名称完全一致
- [ ] 所有主体：representative / title / email 均与 parties.json 对应条目一致，未跨条目混用

### 甲方信息

- [ ] 报价单第一页"公司名称"有值时，合同甲方"客户名称"逐字一致
- [ ] 甲方客户名称未与乙方公司混淆
- [ ] 报价单第一页"联系人"有值时，合同甲方"代表人"逐字一致
- [ ] 电话/手机号只写入"电话号码"，邮箱/微信号只写入"电子邮箱/微信号"
- [ ] 多类型联系方式已拆分到正确字段，原始值未丢失或被改写
- [ ] 报价单未提供的甲方地址、职务等字段未被虚构

### 页眉与文件结构

- [ ] 合同页眉图片像素内容、比例、位置和公司地址与报价单一致
- [ ] 合同页脚与报价单一致（不保留模板版本号），中文版本与三语版规则相同
- [ ] 页眉关系指向有效图片，所有 section 的 header reference 正确
- [ ] 页脚关系指向有效内容，所有 section 的 footer reference 正确
- [ ] 模板正文条款、样式、编号、表格未发生无关变化
- [ ] 七处乙方字段已设为 `sdtContentLocked` 或同等只读保护
- [ ] 文档不存在 `w:documentProtection`；其他输入位置保持可编辑并保留灰色区域提示

### 合同字段

- [ ] 三语版首页不存在"Jasa/ 服务/ Service"整行
- [ ] 三语版页脚与报价单一致，不存在模板版本号或 `Hal/Page` 旧页脚
- [ ] 三语版第 2.2 条三个终止条件默认状态依次为"勾选、未勾选、勾选"，且均可修改
- [ ] 首页"Jasa/ 服务/ Service"整行已删除，两个版本规则相同
- [ ] 合同编号与附后报价单中的合同号逐字一致；源字段为空时两处都为空且无占位提示
- [ ] 合同编号及附后报价单合同号均不含 ASCII 小写字母 `a-z`
- [ ] 中文与三语模板首页合同编号控件的 `w:sdtPr/w:rPr` 和当前内容运行均包含 `w:caps`
- [ ] 上海主体：争议地点和仲裁机构同时命中 parties.json → `disputeDefaults.CN.上海`
- [ ] 印尼主体：适用法律、优先语言、争议机构和地点均命中 parties.json → `disputeDefaults.ID.default`，且三语版本对应值语义一致
- [ ] 法律、优先语言、争议机构和地点均为只能选择的下拉控件，无 `w:comboBox`
- [ ] 三语版保留印尼文、中文和英文文本；首页与签字页无模板示例主体残留
- [ ] 条款 6 的四个百分比位置均有独立灰色可编辑区域提示
- [ ] 签字页甲乙双方日期默认等于生成当天日期，且可修改
- [ ] 甲方名称或代表人为空时，签字区提示保留为 `w:showingPlcHdr` 原生占位符；点击输入会替换提示，提示文字未固化为正文

---

## Gate 3 · 交付前结构检查

失败任何一项 → **不得交付**；修复后从 Gate 2 起重新校验。

### 报价单附件

- [ ] 签字页之后存在完整报价单附件（非链接、文件名或摘要）
- [ ] 附件页数与源报价单一致；正文、表格、图片、页眉页脚和原始分页均完整
- [ ] 若合同编号被修改，附件报价单中的编号已同步；合同正文与附件无不同编号
- [ ] 合同正文页脚的总页数字段为 `NUMPAGES`（或等效的全文档计数），数值等于合同正文页数 + 附件报价单页数；不为 `SECTIONPAGES`

### 默认检查方式

- [ ] 已运行 `scripts/verify_contract.py` 且全部通过
- [ ] 未为常规检查渲染全部页面或逐页读取图片
- [ ] 只有用户明确要求视觉检查，或结构化校验无法判断特定版式风险时，才渲染并检查必要页面
