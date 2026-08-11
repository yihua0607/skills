#!/usr/bin/env python3
"""Shared utilities for quotation data validation and building.

Provides money parsing, VAT calculation, currency formatting, and entity
config loading used by validate_data.py, build_quotation.py, and
verify_quotation.py to ensure consistent behaviour across the pipeline.
"""
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP


CURRENCY_SYMBOLS = {
    'RMB': '￥',
    'IDR': 'Rp',
    'USD': '$',
    'SGD': 'S$',
    'THB': '฿',
    'VND': '₫',
}

CURRENCY_NAMES = {
    'RMB': '人民币',
    'IDR': '印尼盾',
    'USD': '美元',
    'SGD': '新币',
    'VND': '越南盾',
    'MYR': '马币',
    'THB': '泰铢',
    'EGP': '埃及镑',
}

# 无小数的币种（整数报价）：印尼盾、越南盾。
# 其余币种（RMB/USD/THB/SGD 等）税金与总计保留 2 位小数。
INTEGER_ONLY_CURRENCIES = ('IDR', 'VND')


def currency_has_decimals(currency):
    """币种的金额是否保留 2 位小数。仅 IDR/VND 省略小数，其余一律保留。"""
    return currency not in INTEGER_ONLY_CURRENCIES

# Path to entity configuration, resolved relative to this module's location.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_CONFIG_PATH = os.path.join(_SKILL_DIR, 'config', 'entities.json')

REQUIRED_ENTITY_FIELDS = (
    'template', 'company', 'header_lines', 'vat_rate', 'currency',
    'allowed_currencies', 'payment_terms', 'bank_lines',
)


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


def _currency_symbol(currency):
    """Return currency symbol for display."""
    return CURRENCY_SYMBOLS.get(currency, currency)


def calculate_amounts(subtotal, discount, vat_rate, currency, withholding_tax_rate=None):
    """Calculate discounted subtotal, VAT, and grand total.

    Args:
        subtotal: integer or Decimal, sum of service prices.
        discount: integer or Decimal, discount amount (0 if none).
        vat_rate: Decimal or float, e.g. 0.06 for 6%.
        currency: 支持 2 位小数的币种（RMB/USD/THB/SGD）税金保留小数；仅 IDR/VND 取整数。
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
    """Add currency symbol to an already-formatted price string.

    price_str: comma-formatted integer string, e.g. '115,000'.
    currency: 'RMB' (no space after symbol), 'IDR' or 'USD' (space after symbol).
    """
    symbol = _currency_symbol(currency)
    if currency == 'RMB':
        return f'{symbol}{price_str}'
    return f'{symbol} {price_str}'


def format_price_int(val, currency):
    """Format integer amounts (subtotal, discount, discounted) with symbol."""
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
