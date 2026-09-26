# 乙方主体规则

乙方合同数据的唯一配置源是报价单 Skill 的 `config/entities.json`。中国签约主体必须配置 `contract.address`、`contract.representative`、`contract.title`、`contract.email`、`contract.dispute_place` 和 `contract.arbitration`。

匹配规则：

- `scripts/extract_quotation.py` 通过报价单内页眉图片与统一配置中的 `header_image.file` 做 SHA-256 匹配，得到唯一主体；不得按文件名、正文公司名或相似名称推断。
- 公司名称读取主体配置的 `company`；地址、代表人、职务、邮箱和争议解决读取同一主体的 `contract` 对象。
- 邮箱写入普通文本，不保留 `mailto:` 包装。
- 页眉图片未命中、命中多个主体或 `contract` 字段缺失时停止生成并提示维护统一配置；不得类推。
