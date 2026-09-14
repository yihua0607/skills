#!/usr/bin/env python3
"""Shared utilities for quotation data validation and building.

Provides money parsing, VAT calculation, currency formatting, and entity
config loading used by validate_data.py, build_quotation.py, and
verify_quotation.py to ensure consistent behaviour across the pipeline.
"""
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP


# 币种元数据（唯一权威来源）：code -> (中文名, 符号, 是否目标币种, 是否整数币种)。
# 目标币种 = 报价单可直接用于签约/收款的币种（签约主体本币 + RMB/USD）。
CURRENCIES = {
    'RMB': ('人民币', '￥', True, False),
    'IDR': ('印尼盾', 'Rp', True, True),
    'USD': ('美元',   '$',  True, False),
    'SGD': ('新币',   'S$', True, False),
    'THB': ('泰铢',   '฿',  True, False),
    'VND': ('越南盾', '₫',  True, True),
    'EGP': ('埃及镑', 'E£', True, False),
    'MYR': ('马币',   'RM', True, False),
}

# 源币种别名：统一归一化到已知 code（如 API 返回 CNY 时视作 RMB）。
CURRENCY_ALIASES = {
    'CNY': 'RMB',
}

CURRENCY_NAMES = {code: info[0] for code, info in CURRENCIES.items()}
CURRENCY_NAME_TO_CODE = {info[0]: code for code, info in CURRENCIES.items()}
INTEGER_ONLY_CURRENCIES = tuple(code for code, info in CURRENCIES.items() if info[3])
TARGET_CURRENCIES = tuple(code for code, info in CURRENCIES.items() if info[2])


def currency_has_decimals(currency):
    """币种的金额是否保留 2 位小数。仅 IDR/VND 省略小数，其余一律保留。"""
    return currency not in INTEGER_ONLY_CURRENCIES


def currency_symbol(code):
    """返回币种符号（如 '￥'/'Rp'/'S$'）；未知 code 返回空串。"""
    info = CURRENCIES.get(code)
    return info[1] if info else ''


def normalize_currency_code(code):
    """按别名归一化源币种 code（如 CNY -> RMB）；未知 code 原样返回。"""
    if code is None:
        return None
    return CURRENCY_ALIASES.get(code, code)


def is_target_currency(code):
    """code 是否为报价单支持的签约/收款币种（本币 + RMB/USD）。"""
    return code in TARGET_CURRENCIES

# Path to entity configuration, resolved relative to this module's location.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_CONFIG_PATH = os.path.join(_SKILL_DIR, 'config', 'entities.json')

REQUIRED_ENTITY_FIELDS = (
    'template', 'company', 'header_lines', 'vat_rate', 'currency',
    'allowed_currencies', 'bank_lines',
)

# Canonical A4 page geometry, in DXA (twips; 20 DXA = 1pt). Every entity template
# carries exactly this setup, and build normalizes to it so a drifted or non-A4
# template can never produce a quotation that won't print on A4. build enforces it;
# verify re-checks it independently.
A4_PAGE_W = 11906
A4_PAGE_H = 16838
A4_MARGINS = {
    'top': 679,      # 33.95pt
    'right': 1133,   # 56.65pt
    'bottom': 1155,  # 57.75pt
    'left': 1440,    # 72pt
    'header': 567,   # 28.35pt from the paper edge
    'footer': 340,   # 17pt from the paper edge
    'gutter': 0,
}
# Width of the text column the body flows into: 11906 - 1440 - 1133 = 9333 DXA.
A4_TEXT_WIDTH = A4_PAGE_W - A4_MARGINS['left'] - A4_MARGINS['right']

# 「服务内容」表只有 序号/服务内容/数量/价格/备注 五列，没有办理时间列；紧跟在它下面的
# *备注： 区块因此不能出现「上述办理时间不包括收集材料时间……」这类免责声明——办理时间
# 在更靠后的流程表里，「上述」在这里指不到任何上文。
# 这句话本身仍然是需要的，只是位置不对：不要整条删掉，也不要改写成别的措辞（见 SKILL.md）。
# API 的服务「备注」段普遍带这句样板文，Agent 汇总基本信息时容易顺手带进 notes：
# validate 报错拦下、build 兜底剔除并告警、verify 复核成稿，三处共用这一判定。
#
# 判定要认得出样板文，又不能误杀恰好提到时间的正常备注——误杀会让合法信息被 validate
# 拦下、被 build 删掉。所以除了「办理时间 + 近邻的不包括」这个壳子，还要求紧跟其后出现
# 样板文列举的时间项之一：只有壳子不算数。「办理时间不包括节假日」「办理时间不包括周末及
# 法定节假日」都是正常业务说明，必须放行。
PROCESS_TIME_DISCLAIMER_ITEMS = (
    '收集材料时间', '收集资料时间', '检查资料时间', '检查材料时间',
    '签字盖章', '反馈时间', '修改或补充', '补充资料', '邮寄时间', '快递时间',
)
PROCESS_TIME_DISCLAIMER_RE = re.compile(
    r'办理时间[^。；;\n]{0,10}不(?:包括|包含)[^。；;\n]*(?:'
    + '|'.join(PROCESS_TIME_DISCLAIMER_ITEMS)
    + r')'
)


def is_process_time_disclaimer(text):
    """text 是否为「办理时间不包括……」类免责声明（notes 中不允许出现）。"""
    return bool(PROCESS_TIME_DISCLAIMER_RE.search(text or ''))


def load_entity_config():
    """Load and validate entity configuration from config/entities.json.

    Returns a tuple of (entities, universal_excludes):
      - entities: dict of entity_key → config, with _meta keys removed.
      - universal_excludes: list of common exclude item seeds (may be empty).

    Exits with code 1 if the config file is missing or any entity is
    missing a required field.
    """
    if not os.path.exists(ENTITY_CONFIG_PATH):
        print(f"❌ Entity config not found: {ENTITY_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(ENTITY_CONFIG_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    # Validate every entity has all required fields (skip _meta annotation keys).
    errors = []
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue
        for field in REQUIRED_ENTITY_FIELDS:
            if field not in cfg:
                errors.append(f"Entity '{key}' missing required field: {field}")
    if errors:
        print(f"❌ Invalid entity config:\n- " + '\n- '.join(errors), file=sys.stderr)
        sys.exit(1)

    # Return only entity keys (strip _meta / annotation entries).
    entities = {k: v for k, v in raw.items() if not k.startswith('_')}

    meta = raw.get('_meta', {})
    universal_excludes = []
    if isinstance(meta, dict):
        universal_excludes = meta.get('universal_excludes', [])

    # 付款条件：仅 jakarta/deyin 显式声明「收到发票后 100%」，其余主体
    # 共用 _meta.payment_terms_default，未显式声明时在此注入。
    default_terms = meta.get('payment_terms_default') if isinstance(meta, dict) else None
    if default_terms:
        for cfg in entities.values():
            cfg.setdefault('payment_terms', default_terms)

    return entities, universal_excludes


def parse_money_int(value, path):
    """Parse integer amount from a JSON field value."""
    if isinstance(value, bool):
        raise ValueError(f'{path} must be an integer amount, not boolean')
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.replace(',', '').strip()
        if raw.isdigit():
            return int(raw)
    raise ValueError(f'{path} must be an integer amount or comma-formatted integer string')


def _to_decimal(value):
    """Convert int/float/str to Decimal."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def calculate_amounts(subtotal, discount, vat_rate, currency, withholding_tax_rate=None):
    """Calculate discounted subtotal, VAT, and grand total.

    Args:
        subtotal: integer or Decimal, sum of service prices.
        discount: integer or Decimal, discount amount (0 if none).
        vat_rate: Decimal or float, e.g. 0.06 for 6%.
        currency: 税金保留 2 位小数的币种（除 IDR/VND 外全部：RMB/USD/SGD/THB/EGP/MYR）；
            仅 IDR/VND 取整数。
        withholding_tax_rate: Decimal or float, e.g. 0.03 for 3% WHT. None if no WHT.

    Returns:
        dict with:
          - subtotal (int)
          - discount (int)
          - discounted (int)
          - vat (Decimal for 2-decimal currencies, int for IDR/VND)
          - total (Decimal for 2-decimal currencies, int for IDR/VND)
          - vat_rate (Decimal)
          - withholding_tax (Decimal/int or None)
          - withholding_tax_rate (Decimal or None)
    """
    subtotal_d = _to_decimal(subtotal)
    discount_d = _to_decimal(discount)
    vat_rate_d = _to_decimal(vat_rate)
    has_decimals = currency_has_decimals(currency)

    discounted_d = subtotal_d - discount_d

    vat_raw = discounted_d * vat_rate_d
    if has_decimals:
        vat_d = vat_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        vat_d = vat_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    # Withholding tax
    wht_d = None
    wht_rate_d = None
    if withholding_tax_rate is not None:
        wht_rate_d = _to_decimal(withholding_tax_rate)
        wht_raw = discounted_d * wht_rate_d
        if has_decimals:
            wht_d = wht_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            wht_d = wht_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    total_d = discounted_d + vat_d - (wht_d or Decimal('0'))

    result = {
        'subtotal': int(subtotal_d),
        'discount': int(discount_d),
        'discounted': int(discounted_d),
        'vat': vat_d if has_decimals else int(vat_d),
        'total': total_d if has_decimals else int(total_d),
        'vat_rate': vat_rate_d,
        'withholding_tax': None,
        'withholding_tax_rate': None,
    }
    if wht_d is not None:
        result['withholding_tax'] = wht_d if has_decimals else int(wht_d)
        result['withholding_tax_rate'] = wht_rate_d
    return result


def format_price_display(price_str, currency):
    """Return price string without currency symbol.

    报价单价格一律不带货币符号（￥/Rp/$/S$/฿/₫）：币种由价格列头「价格 (币种名)」
    标注，不在每个金额上重复附加符号。currency 参数保留以兼容调用方签名。
    """
    return price_str


def format_price_int(val, currency):
    """Format integer amounts (subtotal, discount, discounted) — 不带货币符号。"""
    val_d = _to_decimal(val)
    formatted = f'{int(val_d):,}'
    return format_price_display(formatted, currency)


def _format_amount_smart(val_d, currency):
    """2 位小数币种的金额：仅当有小数值时显示小数位，整数金额省略 .00。"""
    val_d = _to_decimal(val_d).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if val_d == val_d.to_integral_value():
        return format_price_display(f'{int(val_d):,}', currency)
    return format_price_display(f'{val_d:,.2f}', currency)


def format_price_vat(val, currency):
    """Format VAT — RMB/USD/THB/SGD 仅在有小数时显示小数位，IDR/VND 恒为整数。"""
    val_d = _to_decimal(val)
    if currency_has_decimals(currency):
        return _format_amount_smart(val_d, currency)
    return format_price_display(f'{int(val_d):,}', currency)


def format_price_total(val, currency):
    """Format grand total — RMB/USD/THB/SGD 仅在有小数时显示小数位，IDR/VND 恒为整数。"""
    val_d = _to_decimal(val)
    if currency_has_decimals(currency):
        return _format_amount_smart(val_d, currency)
    return format_price_display(f'{int(val_d):,}', currency)


def vat_percent_label(vat_rate):
    """Return human-readable VAT percentage like '6%' or '1%'."""
    return f"{float(vat_rate) * 100:g}%"


def price_magnitude_warnings(prices, currency):
    """Return warnings when prices look like the wrong magnitude for the currency.

    Shared by validate_data.py (preflight) and build_quotation.py (build time)
    to catch RMB/IDR/USD mix-ups. Only positive prices are considered — a 0
    price marks BPO/percentage pricing and must not trip the "too small" checks.
    """
    warnings = []
    positive = [p for p in prices if p > 0]
    if not positive:
        return warnings
    if currency == 'IDR' and any(p < 1_000_000 for p in positive):
        warnings.append('Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update from a previous RMB quote?')
    if currency == 'VND' and any(p < 1_000_000 for p in positive):
        warnings.append('Some prices appear too small for VND (min: ₫ 1,000,000). Did you forget to update from a previous RMB/USD quote?')
    if currency == 'RMB' and any(p >= 1_000_000 for p in positive):
        warnings.append('Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?')
    if currency == 'USD' and any(p < 50 for p in positive):
        warnings.append('Some prices appear too small for USD (min: $50). Did you forget to convert from IDR?')
    if currency == 'USD' and any(p >= 500_000 for p in positive):
        warnings.append('Some prices appear too large for USD (>= 500,000). Did you forget to convert from IDR?')
    return warnings
