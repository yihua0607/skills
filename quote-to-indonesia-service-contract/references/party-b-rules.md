# 乙方匹配与字段规则

主体配置数据以 [parties.json](parties.json) 为单一事实来源；`quotationEntityBindings` 把报价单主体 key 映射到合同 party，`parties[]` 保存合同字段。本文件解释匹配规则与约束。

## 识别顺序

1. 优先读取报价单页眉中的可见公司名称，并与 `quotationEntityBindings[].headerName` 匹配。
2. 页眉只有徽标或名称不可提取时，使用 `entities.json` 中该报价主体的页眉图片 SHA-256 指纹。
3. 仅在前两步未命中时使用保守的感知图片相似度；必须唯一命中且低于阈值，否则停止并请用户确认。

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

`matchType: normalized` 的匹配按以下确定性算法执行，不得靠语感判断：

1. 输入：报价单页眉公司名 `s1` 与 parties.json 条目 `name`（或 `aliases` 元素）`s2`。
2. 对两者分别做归一化：
   - 去除所有 ASCII 句点 `.`、全角句点 `．`、空白字符（含空格、制表符、不间断空格）。
   - 全部字符转为小写（Unicode case folding）。
3. 归一化结果字符串**严格相等**即视为命中，否则不命中。

合同名称使用 `quotationEntityBindings[].headerName`，它是当前受支持报价主体的可见标准名称；法定主体字段取所绑定的 `parties[]` 条目。

示例（以 `PT. SHAN HAI MAP` 条目为例）：

| 报价单页眉文本 | 归一化结果 | 命中 |
|---|---|---|
| `PT. SHAN HAI MAP` | `ptshanhaimap` | ✅ |
| `PT SHAN HAI MAP` | `ptshanhaimap` | ✅ |
| `P.T. SHAN HAI MAP` | `ptshanhaimap` | ✅ |
| `PT.SHANHAIMAP` | `ptshanhaimap` | ✅ |
| `pt shan hai map` | `ptshanhaimap` | ✅ |
| `PT SHAN HAI MAPS` | `ptshanhamaps` | ❌ |
| `PT SHANMAI MAP` | `ptshanmaimap` | ❌ |

## 代表人、职务和邮箱

所有字段以 `parties.json` → `parties[]` 中对应条目的值为准。写入合同时：

- 邮箱写为普通文本，不保留 `mailto:` 包装。
- 同一 `party` 条目内的 representative / title / email 必须整体命中，不得跨条目混用。
- 未命中任何条目时停止生成，请用户提供代表人、职务和邮箱，不得从相似名称类推。

## 特殊规则

- 中国分公司规则只适用于 `matchType: exact_or_branch` 条目；名称必须以该条目的 `name` 开头且明确含"分公司"。
- 印尼主体名称允许匹配同一法定主体的句点和大小写排版差异。
- `SEA LAW FIRM` / `FIRMA HUKUM SEA` / `Hukum SEA Firma` 属于同一 `alias_group`；当前报价主体标准名称为 `SEA LAW FIRM`。
