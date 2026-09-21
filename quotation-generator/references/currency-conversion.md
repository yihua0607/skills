# 币种与汇率换算

仅在切换币种、追加不同币种服务或排查换算差异时读取。

## 核心规则

- 一律使用 `scripts/convert_currency.py`，禁止心算。
- 汇率来自 `queried_services.json`、原报价留档或用户明确提供。
- 汇率必须为有限正数；拒绝 0、负数、NaN 和 Infinity。
- 所有结果按 `ROUND_HALF_UP` 取整。
- 换算依据写入 `_meta`，不得写进客户可见备注。

`rateToCny` 表示 `1 CNY = N 服务币种`；`rateToUsd` 表示 `1 USD = N 服务币种`。

## 常用命令

```bash
# 整批换算
python3 scripts/convert_currency.py \
  --query-result "$WORKDIR/queried_services.json" \
  --to RMB --json-only

# 单笔换算
python3 scripts/convert_currency.py \
  --amount 250000000 --from IDR --to RMB \
  --rateToCny <查询结果中的汇率> --json-only
```

## 换算方向

| 场景 | 处理 |
|------|------|
| 外币 → RMB | 除以该外币的 `rateToCny` |
| 外币 → USD | 除以该外币的 `rateToUsd` |
| RMB/USD → 外币 | 使用同一汇率反向相乘 |
| RMB ↔ USD | 使用一条外币的 `rateToCny` 与 `rateToUsd` 做桥接 |
| 外币 → 外币 | API 没有直接汇率，向用户索取 `1 from = N to`，传 `--cross-rate N`；用户给的是反向汇率（1 to = M from）时取 `1/M` |
| 源币种 = 目标币种 | identity，不换算 |

`rateToCny` / `rateToUsd` 的定义是「外币 ↔ 人民币 / 美元」，所以两侧都不是人民币或美元时（如 THB→VND、MYR→SGD）API 没有汇率数据，脚本会拒绝并提示索取——**必须问用户，不得自己估**。

8 个币种之外的其他币种（如 HKD）只有在显式传 `--rateToCny` / `--rateToUsd` 时才放行；不传则报 `Unknown ... currency`。

## 价格写入

API `服务价格` 已是包含数量的行项总价。`convert_currency.py` 的结果直接写入 `services[].price`，不得再乘 `quantity`。

## 常见异常

- 换算后与官网差 1：脚本按 `ROUND_HALF_UP` 取整（18,400,000 ÷ 2,250 = **8,178**），不是截断。先与用户确认采用哪个口径，**不要自行改写已交付价格**。
- `rateToCny=1.0` 且标记为 IDR：可能实际是 RMB 定价，展示异常并让用户选择币种。
- IDR 的 `rateToCny` 异常低（如 0.6、4.0 而非 ~2250），且服务编码是 MY/VN/SG/TH/EG 等前缀：可能币种标记错误。**向用户展示异常**，结合服务编码前缀与同批服务汇率的币种一致性判断真实币种，确认后按实际币种换算，并在 `_meta` 注明该异常及判断依据。
- 新加坡元报价：SGD 服务可直接报价；IDR/THB 等转 SGD 属外币→外币，需要 `cross-rate`。
