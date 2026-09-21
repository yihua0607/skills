# 修改已有报价单

仅在修改或追加既有报价单、需要保留客户手改内容时读取。

## 定位旧单

在用户工作目录的 `quotation/` 下查找 `quotation.json` 及同目录 `.docx`，列出候选让用户确认。不得按客户名猜测，也不得从 sample 凭空重建。

## 付款条件保留

付款方式属于客户可见手改内容：

- `--output` 指向已存在的 `.docx` 时，build 自动读取旧文件的付款条件。
- 输出新文件但要沿用旧单时，传 `--preserve-payment-from`。
- 只有用户明确要求用数据覆盖旧付款条件时，才传 `--overwrite-payment-terms`。
- build 会打印付款方式来源；每次都要核对。
- 指定旧文件后提取付款条件失败，必须中止；不得回退默认配置或覆盖旧文件。
- 仅当提取失败或用户明确要求修改付款方式时，才询问付款条件并写入 `quote_meta.payment_terms`。

```bash
python3 scripts/build_quotation.py \
  --entity xian \
  --data "$WORKDIR/quotation.json" \
  --preserve-payment-from "$WORKDIR/已编辑报价单.docx" \
  --output "$QUOTATION_DIR/新版报价单.docx"
```

`sync_payment_terms.py` 仅为兼容工具，一般不使用。

## 其他手改内容

先提取快照：

```bash
python3 scripts/extract_docx_snapshot.py \
  --input "$WORKDIR/已编辑报价单.docx" \
  --output "$WORKDIR/docx_snapshot.json"
```

核对标题、日期、客户、合同号、付款条件、服务、金额、费用包含/不含、流程、交付、材料和备注。可明确映射的差异写回 `quotation.json`；无法判断业务含义时向用户确认。确认前不得覆盖旧文件，重建必须输出新文件。

付款比例合计超过 100%，或付款金额超过合同含税总计时，validate/build/verify 只 warning；由用户决定业务口径。
