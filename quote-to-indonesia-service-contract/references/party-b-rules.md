# 乙方匹配与字段规则

主体配置数据以 [parties.json](parties.json) 为单一事实来源；`quotationEntityBindings` 把报价单主体 key 映射到合同 party，`parties[]` 保存合同字段。本文件解释匹配规则与约束。

## 识别顺序

1. 先计算 DOCX 内图片的 SHA-256，与 `entities.json` 配置的页眉图片精确匹配；唯一命中即确定主体，不读取或渲染页眉。
2. 哈希未命中时，从页眉 XML 提取可见公司名称，与 `quotationEntityBindings[].headerName` 匹配；此步骤也不使用视觉能力。
3. 前两步未命中时，使用保守的感知图片相似度；必须唯一命中且低于阈值。
4. 脚本仍无法判断时，只渲染或截取页眉并用视觉能力读取公司名称，不读取整页；视觉结果仍须匹配受支持主体，否则停止并请用户确认。

不得根据文件名、币种、服务地点、收款方或客户名称推断报价主体。

## 地址来源

每个 party 条目的 `address` 字段声明其地址来源：

- `{"source": "header"}`：地址逐字取自报价单页眉，不得从其他位置补全。
- `{"source": "fixed", "ref": "<KEY>"}`：`ref` 必须指向 `parties.json` 顶层 `fixedAddresses` 中的某个 key，地址始终使用该 key 对应的固定字符串，不得从报价单页眉读取、拼接、规范化或覆盖。

## 匹配类型（`matchType`）

| 类型 | 含义 |
|---|---|
| `exact` | 报价单页眉公司名必须与本条目 `name` 逐字一致 |
| `exact_or_branch` | `name` 本身精确匹配，或以 `name` 开头且明确含"分公司"的分支也匹配 |
| `normalized` | 允许同一法定主体的句点和大小写排版差异；写入合同的名称必须原样保留报价单页眉文本 |
| `alias_group` | `name` 与 `aliases` 数组中的所有名称视为同一规则组 |

## normalized 归一化算法

分别去除报价单页眉公司名与 `parties.json` 名称（或别名）中的 ASCII/全角句点和全部空白，再执行 Unicode case folding；结果严格相等才命中。合同名称使用所命中 `quotationEntityBindings[].headerName`，其他乙方字段取其绑定的 `parties[]` 条目。

## 代表人、职务和邮箱

所有字段以 `parties.json` → `parties[]` 中对应条目的值为准。写入合同时：

- 邮箱写为普通文本，不保留 `mailto:` 包装。
- 同一 `party` 条目内的 representative / title / email 必须整体命中，不得跨条目混用。
- 未命中任何条目时停止生成，请用户提供代表人、职务和邮箱，不得从相似名称类推。
