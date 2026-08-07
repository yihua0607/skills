# 非配置签约主体：jakarta 模板生成 + python-docx 后编辑

## 适用场景
用户上传既有 docx 报价单（签约主体不在 6 个配置实体，如 PT. SHM TAX CONSULTING），
要求换币种/改内容但保留原签约主体和银行信息（"别的不用变"）。

## 流程
1. 用最接近的配置实体 build：印尼主体 → `--entity jakarta`（雅加达模板）。
   增值税率跟随主体而非币种：jakarta 换 RMB 报价仍按 11%。
2. build 完成后用 python-docx 替换主体信息（银行户名/账号、签名表格公司名等）。
   必须用 skill venv python：`/opt/data/skills/quotation-generator/.venv/bin/python`
   （系统 python3 无 python-docx，execute_code 沙箱也无）。
3. verify_quotation 会报"银行公司名与配置不一致"——属预期，以 docx 后编辑结果为准。

## 读取上传的 docx（还原服务清单与价格，含表格）
```python
from docx import Document
doc = Document(PATH)
for p in doc.paragraphs:      # 标题/备注/银行信息/付款条件
    if p.text.strip(): print(p.text)
for t in doc.tables:          # 服务价格表/流程表/材料表/签名表
    for row in t.rows:
        print(" | ".join(c.text.strip() for c in row.cells))
```

## 替换公司名/账号（正文段落 + 表格 + 页眉）
```python
from docx import Document
doc = Document(OUTPUT)
OLD, NEW = "PT. SHAN HAI MAP", "PT. SHM TAX CONSULTING"
BANK_OLD, BANK_NEW = "5485225789", "5485483133"
for section in doc.sections:                     # 页眉
    for p in section.header.paragraphs:
        for r in p.runs:
            if OLD in r.text: r.text = r.text.replace(OLD, NEW)
for p in doc.paragraphs:                         # 正文（银行户名/账号等）
    for r in p.runs:
        if OLD in r.text: r.text = r.text.replace(OLD, NEW)
        if BANK_OLD in r.text: r.text = r.text.replace(BANK_OLD, BANK_NEW)
for t in doc.tables:                             # 表格（签名栏等）
    for row in t.rows:
        for c in row.cells:
            for p in c.paragraphs:
                for r in p.runs:
                    if OLD in r.text: r.text = r.text.replace(OLD, NEW)
doc.save(OUTPUT)
```
注意：替换前先打印全文确认旧值（同段文字可能拆成多个 run，替换需逐 run 判断）；
页眉/正文/表格三处都要扫，雅加达模板页眉通常为空（verify 显示页眉公司名 None）。

## 实例
阳洋集团审计-RMB.docx：财务报表审核服务 ×4（用户指定单价 100M IDR）、
签约主体 PT. SHM TAX CONSULTING（BCA 账号 5485483133）、jakarta 11% 增值税、
价格按用户给定值 1,200,000,000 IDR → 533,333 RMB（÷2250 round）。
