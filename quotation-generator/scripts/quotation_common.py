#!/usr/bin/env python3
"""Shared utilities for quotation data validation and building.

Provides money parsing, VAT calculation, and currency formatting used by
both validate_data.py and build_quotation.py to ensure consistent rounding
and formatting across the quotation pipeline.
"""
from decimal import Decimal, ROUND_HALF_UP


CURRENCY_SYMBOLS = {
    'RMB': '￥',
    'IDR': 'Rp',
    'USD': '$',
}


def parse_money_int(value, path):
    """Parse integer amount — same logic as build_quotation.py and validate_data.py."""
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


def calculate_amounts(subtotal, discount, vat_rate, currency):
    """Calculate discounted subtotal, VAT, and grand total.

    Args:
        subtotal: integer or Decimal, sum of service prices.
        discount: integer or Decimal, discount amount (0 if none).
        vat_rate: Decimal or float, e.g. 0.06 for 6%.
        currency: 'RMB' or 'IDR' (default IDR-like behavior for others).

    Returns:
        dict with:
          - subtotal (int)
          - discount (int)
          - discounted (int)
          - vat (Decimal for RMB, int for IDR)
          - total (Decimal for RMB, int for IDR)
          - vat_rate (Decimal)
    """
    subtotal_d = _to_decimal(subtotal)
    discount_d = _to_decimal(discount)
    vat_rate_d = _to_decimal(vat_rate)

    discounted_d = subtotal_d - discount_d

    vat_raw = discounted_d * vat_rate_d
    if currency == 'RMB':
        vat_d = vat_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        vat_d = vat_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    total_d = discounted_d + vat_d

    return {
        'subtotal': int(subtotal_d),
        'discount': int(discount_d),
        'discounted': int(discounted_d),
        'vat': vat_d if currency == 'RMB' else int(vat_d),
        'total': total_d if currency == 'RMB' else int(total_d),
        'vat_rate': vat_rate_d,
    }


def format_price_int(val, currency):
    """Format integer amounts (subtotal, discount, discounted) with symbol."""
    val_d = _to_decimal(val)
    formatted = f'{int(val_d):,}'
    symbol = _currency_symbol(currency)
    if currency == 'RMB':
        return f'{symbol}{formatted}'
    return f'{symbol} {formatted}'


def format_price_vat(val, currency):
    """Format VAT — RMB keeps 2 decimals, IDR integer."""
    val_d = _to_decimal(val)
    symbol = _currency_symbol(currency)
    if currency == 'RMB':
        return f'{symbol}{float(val_d):,.2f}'
    return f'{symbol} {int(val_d):,}'


def format_price_total(val, currency):
    """Format grand total — RMB keeps 2 decimals, IDR integer."""
    val_d = _to_decimal(val)
    symbol = _currency_symbol(currency)
    if currency == 'RMB':
        return f'{symbol}{float(val_d):,.2f}'
    return f'{symbol} {int(val_d):,}'


def vat_percent_label(vat_rate):
    """Return human-readable VAT percentage like '6%' or '1%'."""
    return f"{float(vat_rate) * 100:g}%"
