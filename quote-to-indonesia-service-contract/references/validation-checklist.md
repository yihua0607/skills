# 校验清单

规则见 [party-b-rules.md](party-b-rules.md) 和 [contract-field-rules.md](contract-field-rules.md)，主体数据见 [parties.json](parties.json)。自动检查以 `scripts/verify_contract.py` 为准；本文只保留前置判断和脚本无法替代的人工检查。

## 生成前

任一项失败即停止：

- `parties.json` 是合法 JSON，引用的主体、固定地址和模板路由完整。
- 报价单按规定顺序唯一命中 `quotationEntityBindings`，且主体国家为 `CN` 或 `ID`。
- 多份报价单时，用户已根据页眉主体、合同号和客户名称选定输入文件。
- 已根据本次实际生成日期（Asia/Jakarta 当天）确定 `YYYY-MM` 输出目录；不得使用合同签署日期，且当前工作目录名为 `contract` 时不得重复创建 `contract/contract/`。

## 生成后

必须运行：

```bash
python3 scripts/verify_contract.py <合同.docx> --quotation <报价单.docx>
```

校验器负责检查主体及甲方字段、内容控件、合同编号、争议条款、三语内容、页眉页脚关系、报价单附件和分页结构。任何失败均不得交付；先判断属于输入错误、已知限制还是实现缺陷，普通生成任务不得临时修改脚本或模板。

交付前另行确认成稿位于本次生成日期对应的 `contract/YYYY-MM/`（或当前 `contract` 工作目录下的 `YYYY-MM/`）。修改或重新生成既有合同时也使用本次生成月份；合同内签署日期不影响归档目录。

## 视觉检查例外

默认不渲染。仅在用户要求视觉检查、校验器报告无法结构化判断的版式风险，或模板版式被修改时，渲染并检查受影响页面。

## 维护验证

修改脚本、模板或 `parties.json` 后：

1. 运行 `python3 -m unittest discover -s tests -v`。
2. 分别生成一份中文合同和一份三语合同，并运行结构校验。
3. 如修改了页边距、表格、字体或分页逻辑，再渲染受影响页面。
