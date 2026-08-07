#!/usr/bin/env python3
"""Pre-flight validation for quotation data BEFORE running build_quotation.py.

Checks data structure, field coverage, and business rules without generating
a .docx — so the Agent can fix issues before the expensive build step.

Usage:
  python3 scripts/validate_data.py --data quotation.json --entity xian
  python3 scripts/validate_data.py --data quotation.json --entity jakarta

Exit codes:
  0 — all checks passed (may have warnings)
  1 — validation failed (errors found)
"""
import argparse
import json
import os
import sys

# Ensure imports work when this script is run directly as `python3 scripts/validate_data.py`.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from scripts.quotation_common import (
    calculate_amounts,
    format_price_int,
    format_price_vat,
    format_price_total,
    vat_percent_label,
    load_entity_config,
)
from scripts.sync_payment_terms import check_payment_terms_reasonableness
from scripts.quotation_schema import validate_and_normalize_data as schema_validate_and_normalize_data


def validate_quotation_data(data, entity_key, entity_config, universal_excludes=None):
    """Validate quotation data and return list of errors and warnings.

    Schema-level checks (services, fee_details, process_data, doc_data, notes,
    discount, quote_meta structure) are delegated to quotation_schema.py. This
    function only adds entity-specific, business-level checks.
    """
    errors = []
    warnings = []

    if not isinstance(data, dict):
        errors.append('Quotation data must be a JSON object')
        return errors, warnings

    # ── Schema validation (delegated to quotation_schema.py) ──
    schema_ok = False
    validated = None
    try:
        validated = schema_validate_and_normalize_data(data)
        schema_ok = True
        warnings.extend(validated.get('warnings', []))
    except ValueError as exc:
        message = str(exc)
        if message.startswith('Invalid quotation data:\n- '):
            errors.extend(message.split('\n- ')[1:])
        else:
            errors.append(message)

    # ── Entity validation (entity-specific, not in schema module) ──
    if entity_key not in entity_config:
        errors.append(f'Unknown entity: {entity_key}. Available: {", ".join(entity_config.keys())}')
        return errors, warnings

    entity_cfg = entity_config[entity_key]
    entity_default_currency = entity_cfg['currency']
    currency = entity_default_currency

    meta = data.get('_meta', {})
    if meta is not None and not isinstance(meta, dict):
        errors.append('_meta must be an object when provided')
    elif isinstance(meta, dict):
        meta_entity = meta.get('applicable_entity')
        if meta_entity and meta_entity != entity_key:
            errors.append(f'_meta.applicable_entity ({meta_entity}) must match --entity ({entity_key})')
        target_currency = meta.get('target_currency')
        if target_currency:
            if target_currency not in ('IDR', 'RMB', 'USD', 'SGD'):
                errors.append(f'_meta.target_currency ({target_currency}) must be IDR, RMB, USD, or SGD')
            else:
                currency = target_currency
        allowed_currencies = entity_cfg.get('allowed_currencies', [entity_default_currency])
        if currency not in allowed_currencies:
            errors.append(
                f'_meta.target_currency ({currency}) is not allowed for --entity ({entity_key}); '
                f'allowed: {", ".join(allowed_currencies)}')

    # ── Notes must not contain currency exchange / rate info ──
    notes = data.get('notes', [])
    if notes:
        rate_keywords = ['汇率', '折算', '原币种', '兑换']
        for i, note in enumerate(notes):
            text = note.get('text', '') if isinstance(note, dict) else str(note)
            for kw in rate_keywords:
                if kw in text:
                    errors.append(
                        f'notes[{i}] contains prohibited currency exchange keyword "{kw}". '
                        'Notes must not describe currency conversion or exchange rates.'
                    )
                    break

    # ── Entity-specific checks that depend on validated data ──
    if schema_ok and validated is not None:
        all_prices = [item['price_int'] for group in validated['services'] for item in group['items']]

        # Price magnitude check (business-level guard against currency mix-ups)
        if all_prices:
            if currency == 'IDR' and any(p < 1_000_000 for p in all_prices if p > 0):
                warnings.append('Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update from a previous RMB quote?')
            elif currency == 'RMB' and any(p >= 1_000_000 for p in all_prices):
                warnings.append('Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?')
            elif currency == 'USD' and any(p < 50 for p in all_prices if p > 0):
                warnings.append('Some prices appear too small for USD (min: $50). Did you forget to convert from IDR?')
            elif currency == 'USD' and any(p >= 500_000 for p in all_prices):
                warnings.append('Some prices appear too large for USD (>= 500,000). Did you forget to convert from IDR?')

        # Universal excludes — check per-service exclude lists for items that
        # should instead appear in notes (shared across all services)
        for i, fd in enumerate(validated['fee_details']):
            exclude = fd.get('exclude', [])
            if exclude and universal_excludes:
                overlap = [item for item in exclude if any(ue in item for ue in universal_excludes)]
                if overlap:
                    warnings.append(
                        f'fee_details[{i}].exclude contains universal items that should be in notes instead: '
                        f'{", ".join(overlap)}')

        # VAT sanity preview
        subtotal = sum(all_prices)
        discount_int = validated['discount_amount']
        amounts = calculate_amounts(subtotal, discount_int, entity_cfg['vat_rate'], currency)
        vat_pct = vat_percent_label(amounts['vat_rate'])
        print(f"  预估: 小计={format_price_int(amounts['subtotal'], currency)} | "
              f"优惠={format_price_int(amounts['discount'], currency)} | "
              f"增值税({vat_pct})={format_price_vat(amounts['vat'], currency)} | "
              f"含税总计={format_price_total(amounts['total'], currency)}")

        # Payment terms reasonableness (user-managed; warn only)
        payment_terms = validated['quote_meta'].get('payment_terms')
        if payment_terms:
            warnings.extend(check_payment_terms_reasonableness(
                payment_terms,
                contract_total=amounts.get('total'),
                currency=currency,
            ))

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description='Validate quotation data before building .docx — catch errors early.')
    parser.add_argument('--data', required=True, help='Quotation data file (.json)')
    parser.add_argument('--entity', required=True,
                        choices=['jakarta', 'beijing', 'xian', 'shenzhen', 'shanghai', 'shanghai_new', 'singapore', 'deyin'],
                        help='Signing entity (required')
    args = parser.parse_args()

    entity_config, universal_excludes = load_entity_config()

    data_path = os.path.abspath(args.data)
    if not os.path.exists(data_path):
        print(f"❌ Data file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        print(f"❌ Cannot parse JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Validating: {data_path}")
    print(f"Entity: {args.entity} ({entity_config[args.entity]['company']})")

    errors, warnings = validate_quotation_data(data, args.entity, entity_config, universal_excludes)

    # ── Output results ──
    if warnings:
        for w in warnings:
            print(f"⚠️  {w}")

    if errors:
        print(f"\n❌ 数据校验失败: {len(errors)} 个问题")
        for e in errors:
            print(f"  - {e}")
        print("\n请修正以上问题后再运行 build_quotation.py")
        sys.exit(1)
    else:
        print("\n✅ 数据校验通过: 所有字段完整、金额合理")
        print("可以运行 build_quotation.py 生成报价单")
        sys.exit(0)


if __name__ == '__main__':
    main()
