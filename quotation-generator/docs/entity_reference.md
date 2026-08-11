# 签约主体完整参数参考

本文档反映 `config/entities.json` 中的签约主体配置详情。LLM 生成报价单时 **不需要手动填写这些信息**——`build_quotation.py` 按 `--entity` 参数自动加载配置、填充页眉、银行信息、签名主体和付款条件。

仅在需要向用户解释具体银行账户信息时参考本文档。

> 新增/修改签约主体时，只需编辑 `config/entities.json`，无需修改任何 Python 脚本。

---

## 雅加达 — PT. SHAN HAI MAP

| 项目 | 值 |
|------|-----|
| 参数 | `--entity jakarta` |
| 模板 | 雅加达 |
| 增值税 | 11% |
| 币种 | Rp 印尼盾（整数） |
| 付款条件 | 收到发票后 5 工作日内 100% |
| 页眉 | PT. SHAN HAI MAP |
| 银行 | BCA (KCP CENTRAL PARK) |
| 账号 | 5485225789 |
| SWIFT | CENAIDJA |

---

## 北京 — 北京山海图科技有限公司

| 项目 | 值 |
|------|-----|
| 参数 | `--entity beijing` |
| 模板 | 中国公司 |
| 增值税 | 6% |
| 币种 | ￥ 人民币 |
| 页眉公司名 | 北京山海图科技有限公司 |
| 页眉地址 | 北京市海淀区西四环北路158号1幢一层3-65 |
| 账户名称 | 北京山海图科技有限公司 |
| 税号 | 91110108080546395Q |
| 开户银行 | 华夏银行(北京学院路华夏银行支行) |
| 银行账号 | 10242000000049937 |
| 银行信息地址 | 北京市海淀区西四环北路158号1幢一层3-65 |

---

## 西安 — 北京山海图科技有限公司西安分公司

| 项目 | 值 |
|------|-----|
| 参数 | `--entity xian` |
| 模板 | 中国公司 |
| 增值税 | 6% |
| 币种 | ￥ 人民币 |
| 页眉公司名 | 北京山海图科技有限公司西安分公司 |
| 页眉地址 | 西安市高新区科技路林凯国际大厦15层1501-01-03室 |
| 统一社会信用代码 | 91610131MAB11JW331 |
| 开户行 | 中国银行西安高新技术开发区支行 |
| 开户名 | 北京山海图科技有限公司西安分公司 |
| 账号 | 1021 0955 7761 |
| 银行信息地址 | 西安市高新区科技路林凯国际大厦15层1501-01-03室 |

> 页眉地址与银行信息地址现已统一为"西安市…"。

---

## 深圳 — 北京山海图科技有限公司深圳分公司

| 项目 | 值 |
|------|-----|
| 参数 | `--entity shenzhen` |
| 模板 | 中国公司 |
| 增值税 | 6% |
| 币种 | ￥ 人民币 |
| 页眉公司名 | 北京山海图科技有限公司深圳分公司 |
| 页眉地址 | 深圳市南山区南海大道1052号海翔广场717 |
| 统一社会信用代码 | 91440300MA5HXMAEXM |
| 开户行 | 中国银行股份有限公司深圳高新区支行 |
| 开户名 | 北京山海图科技有限公司深圳分公司 |
| 账号 | 7770 7729 1133 |
| 银行信息地址 | 深圳市南山区南海大道1052号海翔广场717 |

---

## 上海 — 北京山海图科技有限公司上海分公司

| 项目 | 值 |
|------|-----|
| 参数 | `--entity shanghai` |
| 模板 | 中国公司 |
| 增值税 | 1% |
| 币种 | ￥ 人民币 |
| 页眉公司名 | 北京山海图科技有限公司上海分公司 |
| 页眉地址 | 上海市闵行区虹桥LM世界中心L3-B栋 305A |
| 账户名称 | 北京山海图科技有限公司上海分公司 |
| Account Name | Beijing Shanhaitu Technology Co., Ltd. Shanghai Branch |
| 税号 | 91310118MAEGFAM57M |
| 账户号码 | 4351 8873 3108 |
| 开户银行 | 中国银行上海市虹桥会展中心支行（Bank of China Shanghai Branch Hongqiao Exhibition And Convention Center Sub-Branch） |
| 行号 | 104290020130 |
| SWIFT CODE | BKCHCNBJ300 |
| 银行信息地址 | 上海市闵行区虹桥LM世界中心L3-B栋 305A |

---

## 上海新企业 — 上海山海图新企业咨询有限公司

| 项目 | 值 |
|------|-----|
| 参数 | `--entity shanghai_new` |
| 模板 | 中国公司 |
| 增值税 | 1% |
| 币种 | ￥ 人民币 |
| 页眉公司名 | 上海山海图新企业咨询有限公司 |
| 页眉地址 | 上海市青浦区虹桥LM世界中心L3-B栋 305A |
| 账户名称 | 上海山海图新企业咨询有限公司 |
| Account Name | Shanghai Shanhaimap New Enterprise Consulting Co., Ltd. |
| 税号 | 91310113MAEW51431Q |
| 账号 | 4520 8977 3373 |
| 开户银行 | 中国银行股份有限公司上海市虹桥会展中心支行（Bank of China Shanghai Branch Hongqiao Exhibition And Convention Center Sub-Branch） |
| 行号 | 104290020130 |
| SWIFT CODE | BKCHCNBJ300 |
| 银行信息地址 | 上海市青浦区虹桥LM世界中心L3-B栋 305A |

---

## 泰国 — SHAN HAI MAP (THAILAND) CO., LTD.

| 项目 | 值 |
|------|-----|
| 参数 | `--entity thailand` |
| 模板 | 泰国公司 |
| 增值税 | 7% |
| 预扣税 | 3% |
| 币种 | ฿ 泰铢（THB） |
| 页眉公司名 | SHAN HAI MAP (THAILAND) CO., LTD. |
| 页眉地址 | Thanapoom Tower, 25th floor Unit A2, 1550 New Petchaburi Rd, |
| | Khwaeng Makkasan, Khet Ratchathewi, Bangkok 10400 |
| Beneficiary Name | SHAN HAI MAP (THAILAND) CO., LTD. |
| Beneficiary Bank | Kasikorn PCL. Thailand, Central Rama 9 Branch (847) |
| Beneficiary Bank Address | 9/9 Central Plaza Tower, 5 Floor, Room 512-513, Rama9 rd Huaykhwang, HuayKwang BKK 10310 |
| SWIFT CODE | KASITHBK |
| Account Number | 1931179981 |

---

## 越南 — CÔNG TY TNHH SHANHAIMAP VIỆT NAM

| 项目 | 值 |
|------|-----|
| 参数 | `--entity vietnam` |
| 中文名 | 山海图越南有限公司 |
| 模板 | 越南公司 |
| 增值税 | 8% |
| 币种 | ₫ 越南盾（VND，整数） |
| 页眉公司名 | 模板内置（脚本不覆盖） |
| 银行 | Vietcombank - Chi nhánh Thăng Long |
| VND 账号 | 104 799 1200 |
| USD/RMB 账号 | 104 799 1540 |
| SWIFT Code | BFTVVNVX |
| 分行地址 | Tòa Nhà Pvoil Phú Thọ, Số 148 Hoàng Quốc Việt, Phường Nghĩa Tân, Quận Cầu Giấy, Thành Phố Hà Nội |

---

## 新增签约主体操作指南

如需新增签约主体，只需编辑 `config/entities.json`，添加新实体条目：

```json
{
  "新实体key": {
    "template": "china 或 jakarta",
    "company": "公司全称",
    "header_lines": ["公司名", "地址", ""],
    "vat_rate": 0.06,
    "currency": "RMB 或 IDR",
    "allowed_currencies": ["RMB", "USD"],
    "payment_terms": ["付款条件文本"],
    "bank_lines": ["银行信息行1", "银行信息行2", ...]
  }
}
```

然后确保 `template` 字段引用的是已有模板类型（`china` 或 `jakarta`）。脚本会自动从 `config/entities.json` 加载所有实体 key，不需要修改 `build_quotation.py` 的 `--entity choices`。

> 无需修改 `build_quotation.py` 的生成逻辑、无需修改 `SKILL.md` 的签约主体表（但建议更新本文档和 SKILL.md 中的表格供 Agent 参考）。

---

## XML 格式参数（脚本内置）

| 参数 | 值 | 说明 |
|------|-----|------|
| 字体 | FangSong（仿宋） | 正文 12pt (sz=24) |
| 标题 | #4472C4 | 19pt/18pt (sz=38/36)，居中，底部蓝色边框 |
| 表头底色 | #BDD6EE | 粗体居中 |
| 列宽 | 555, 2514, 1050, 2000, 3671 | 总和 9790 DXA |
| 单元格边距 | top=60, bottom=60, left=108, right=108 | DXA |
| 行距 | 280 (1.17 倍) | auto lineRule |
| 价格字体 | 10pt (sz=20) | 防止换行 |
| 纸张 | A4 (11906 × 16838 DXA) | 脚本自动保留 sectPr |

> 以上参数全部由 build_quotation.py 在生成时自动应用，LLM 无需手动设置。
