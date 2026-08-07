# 多服务批量提取 quotation.json 模式

当报价单包含 5 个以上服务时，手写 quotation.json 极为繁琐且易错，应使用 `execute_code` 批量提取结构化数据。

## 触发条件

- 服务数量 ≥ 5 个
- 或服务数据复杂（流程步骤多、文档列表长）

## 核心函数模式

```python
import json, re
from collections import Counter

def clean(line):
    """去除 markdown 标记和转义符，合并空白"""
    line = re.sub(r'\*+', '', line)
    line = line.replace('\\', '')
    return re.sub(r'\s+', ' ', line).strip()

# ★ 推荐：find() 法提取列表 —— API Markdown 格式极不一致，正则频繁失败
def extract_list(content, start_marker, end_marker=None):
    """从 content 中提取 start_marker 和 end_marker 之间的列表项"""
    idx = content.find(start_marker)
    if idx == -1:
        return []
    rest = content[idx + len(start_marker):]
    if end_marker:
        ei = rest.find(end_marker)
        if ei != -1:
            rest = rest[:ei]
    return [clean(l) for l in rest.split('\n') if clean(l) and len(clean(l)) > 1]

def extract_days(content):
    """提取办理时间天数"""
    m = re.search(r'办理时间[：:]*\*{0,4}\s*(\d+)', content)
    return m.group(1) if m else ""

def extract_note(content):
    """从基本信息提取摘要（前3句，限200字）"""
    bi, tp = content.find('基本信息'), content.find('办理时间及流程')
    if not (bi >= 0 and tp > bi):
        return ""
    bi_text = content[bi+4:tp].strip()
    sents = [clean(re.sub(r'^[\d]+[\.\、\s)]*', '', l).strip())
             for l in bi_text.split('\n') if len(clean(l)) > 5]
    note = ' '.join(sents[:3])
    return note[:200] if len(note) > 200 else note

def extract_process(content):
    """直接从内容中匹配第X步/第一步模式"""
    return [clean(l) for l in content.split('\n')
            if re.match(r'(?:第[一二三四五六七八九十\d]+步|第一步)', l.strip())]
```

## 处理流程

1. **保存 queried_services.json** — fetch 后 stdout 重定向到文件
2. **按 extract_list 提取各段**：
   ```python
   delivs = extract_list(content, '交付文件', '费用说明')
   docs   = extract_list(content, '所需资料', '交付文件')
   # 兜底：docs 为空时补占位（部分服务写"无"）
   docs   = docs or ["请咨询山海图获取详细资料清单"]
   
   fs, ps = content.find('费用说明'), content.find('付款方式')
   fee_text = content[fs:ps] if fs >= 0 and ps > fs else (content[fs:] if fs >= 0 else "")
   fee_inc = extract_list(fee_text, '费用包含', '费用不含')
   fee_exc = extract_list(fee_text, '费用不含', '付款')
   ```
3. **公共不含项去重** — `Counter(all_excludes)` 找到出现 >1 次的项，统一放入 notes
4. **构建 quotation.json** — 组装完整 JSON 结构
5. **validate → build → verify** — 用脚本校验生成

## 处理重复服务名

```python
name_counts = Counter(s["服务名称"] for s in services)
for s in services:
    display_name = s["服务名称"]
    if name_counts[display_name] > 1:
        ai_suffix = s.get("查询aiCode", "").split("-")[-1][-3:]
        display_name = f"{display_name}-{ai_suffix}"
    # 同步更新 fee_details/process_data/doc_data 中的 name
```

## 处理服务顺序调整 / 替换 / 删除

修改 quotation.json 后重新生成即可，不需要重新 fetch（除非新增服务）。调整顺序时直接重排 items 数组，并同步重排 fee_details/process_data/doc_data。

**⚠️ 从缓存 queried_services.json 筛选时，必须验证输出顺序**：当从一个大批次 queried_services.json 中按服务名筛选子集时，筛选结果的顺序可能与用户期望不同。生成 quotation.json 后**务必打印 items 列表核对顺序**（`print([i['name'] for i in items])`），确认与用户指定顺序一致后再 build。

## 已知坑点

1. **API Markdown 费用格式极不一致** — `****费用包含：****`、`****费用包含********：****`、`费用包含：`（无星号）、`****费用不包含：****`（非"费用不含"）等。**不要用正则提取费用段，用 `str.find()` 定位关键标记 + 截取文本。** `extract_list(content, '费用包含', '费用不含')` 比正则 `r'费用包含[：:]*\s*\n'` 可靠得多。

2. **办理流程提取** — 不要找 "办理流程" 子标题再切分，直接从全文中匹配 `第X步` 模式。

3. **费用包含提取不到时** — fallback `["山海图服务费"]`。

4. **docs 为空** — 部分服务"所需资料"写"无"，提取结果 `[]` 会触发 validate 失败。**必须兜底**：`docs = extract_list(...) or ["请咨询山海图获取详细资料清单"]`

5. **verify "费用明细有多余服务" 警告** — 当 fee_details 的 note 字段包含长文本时，verify 可能误判为多余服务名。此警告无害，不影响报价单正确性。

6. **CNY 定价服务混入 IDR 查询结果** — 注意 `rateToCny=1.0` 且 `服务币种=IDR` 的服务实际是 CNY 定价，`convert_currency.py` 会原样返回。直接用原价，不除汇率。

7. **API 内容带字面反斜杠残留** — fetch 返回的部分服务内容字段含字面 `\n`（反斜杠+n 文本，不是真实换行）和多余 `\`，直接按行 split 会被污染。提取前先做两步清理：把字面 `\n` 替换为真实换行，再删除多余反斜杠（clean() 已做后者）。`extract_list` 的 `len > 1` 过滤会保留「服务费」这类 3 字短项，不要用 `len > 3`。

8. **`notes` 必须是对象列表** — `notes` 每项为 `{"text": ..., "indent": N}` 结构，不能是纯字符串数组；`payment_terms` 也不能是空列表（entity 有默认值，需从 `config/entities.json` 取或省略字段）。

9. **非交付项泄漏进 deliverables/docs** — 当 API 内容缺结束标记时，`extract_list` 会把后续「付款方式」「费用说明」等段落捞进交付文件/所需资料数组。提取后逐项过滤：`delivs = [d for d in delivs if not re.match(r'(付款方式|费用说明|服务价格是|人民币付款|印尼盾)', d)]`，再核对首尾项是否属于本段。
