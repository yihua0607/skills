# 服务顺序重排

报价单服务顺序由 `services[].items[]` 决定，但 `fee_details`、`process_data`、`doc_data` 是并列数组，按 `name` 匹配服务，不会自动跟随 `services` 重排。调整 `services[].items[]` 顺序后，必须同步重排这三个数组，否则报价单顺序不变。

```python
names_order = [item["name"] for group in services for item in group["items"]]
for key in ("fee_details", "process_data", "doc_data"):
    q[key] = sorted(q[key], key=lambda x: names_order.index(x["name"]))
```

重排后写回 `quotation.json` 并重新 build。
