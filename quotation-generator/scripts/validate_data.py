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
import re

# Ensure imports work when this script is run directly as `python3 scripts/validate_data.py`.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from scripts.quotation_common import (
    parse_money_int,
    calculate_amounts,
    format_price_int,
    format_price_vat,
    format_price_total,
    vat_percent_label,
)
from scripts.sync_payment_terms import check_payment_terms_reasonableness
from scripts.quotation_schema import validate_and_normalize_data as schema_validate_and_normalize_data

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_CONFIG_PATH = os.path.join(SKILL_DIR, 'config', 'entities.json')

REQUIRED_ENTITY_FIELDS = [
    'template', 'company', 'header_lines', 'vat_rate', 'currency',
    'allowed_currencies', 'payment_terms', 'bank_lines',
]


def load_entity_config():
    if not os.path.exists(ENTITY_CONFIG_PATH):
        print(f"❌ Entity config not found: {ENTITY_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(ENTITY_CONFIG_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    # Schema validation — skip _meta keys
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue
        for field in REQUIRED_ENTITY_FIELDS:
            if field not in cfg:
                print(f"❌ Entity '{key}' missing required field: {field}", file=sys.stderr)
                sys.exit(1)
    # Remove meta keys, return only entity configs
    config = {k: v for k, v in raw.items() if not k.startswith('_')}
    # Extract universal_excludes from _meta
    universal_excludes = []
    meta = raw.get('_meta', {})
    if isinstance(meta, dict):
        universal_excludes = meta.get('universal_excludes', [])
    return config, universal_excludes


def validate_quotation_data(data, entity_key, entity_config, universal_excludes=None):
    """Validate quotation data and return list of errors and warnings."""
    errors = []
    warnings = []

    if not isinstance(data, dict):
        errors.append('Quotation data must be a JSON object')
        return errors, warnings

    try:
        build_validated = schema_validate_and_normalize_data(data)
        warnings.extend(build_validated.get('warnings', []))
    except ValueError as exc:
        message = str(exc)
        if message.startswith('Invalid quotation data:\n- '):
            errors.extend(message.split('\n- ')[1:])
        else:
            errors.append(message)

    # ── Required top-level keys ──
    required_keys = ('services', 'fee_details', 'process_data', 'doc_data')
    for key in required_keys:
        if key not in data:
            errors.append(f'Missing top-level key: {key}')

    # ── Entity validation ──
    if entity_key not in entity_config:
        errors.append(f'Unknown entity: {entity_key}. Available: {", ".join(entity_config.keys())}')
        return errors, warnings

    entity_cfg = entity_config[entity_key]
    entity_default_currency = entity_cfg['currency']
    currency = entity_default_currency

    meta = data.get('_meta', {})
    if meta is not None and not isinstance(meta, dict):
        errors.append('_meta must be an object when provided')
        meta = {}
    if isinstance(meta, dict):
        meta_entity = meta.get('applicable_entity')
        if meta_entity and meta_entity != entity_key:
            errors.append(f'_meta.applicable_entity ({meta_entity}) must match --entity ({entity_key})')
        target_currency = meta.get('target_currency')
        if target_currency:
            if target_currency not in ('IDR', 'RMB', 'USD'):
                errors.append(f'_meta.target_currency ({target_currency}) must be IDR, RMB, or USD')
            else:
                currency = target_currency
        allowed_currencies = entity_cfg.get('allowed_currencies', [entity_default_currency])
        if currency not in allowed_currencies:
            errors.append(
                f'_meta.target_currency ({currency}) is not allowed for --entity ({entity_key}); '
                f'allowed: {", ".join(allowed_currencies)}')

    # ── Services ──
    services_data = data.get('services')
    if not isinstance(services_data, list) or not services_data:
        errors.append('services must be a non-empty list')
        services_data = []

    service_names = []
    all_prices = []
    for group_idx, group in enumerate(services_data):
        path = f'services[{group_idx}]'
        if not isinstance(group, dict):
            errors.append(f'{path} must be an object')
            continue
        items = group.get('items')
        if not isinstance(items, list) or not items:
            errors.append(f'{path}.items must be a non-empty list')
            continue
        for item_idx, item in enumerate(items):
            item_path = f'{path}.items[{item_idx}]'
            if not isinstance(item, dict):
                errors.append(f'{item_path} must be an object')
                continue
            # name
            name = item.get('name')
            if not isinstance(name, str) or not name.strip():
                errors.append(f'{item_path}.name is required and must be non-empty text')
            else:
                if re.search(r'\s*[x×]\d+$', name):
                    errors.append(f'{item_path}.name must not include quantity suffix; use quantity instead')
                service_names.append(name.strip())
            # id
            id_val = item.get('id')
            if not isinstance(id_val, str) or not id_val.strip():
                errors.append(f'{item_path}.id is required and must be non-empty text')
            # days
            days = item.get('days')
            if not isinstance(days, str) or not days.strip():
                errors.append(f'{item_path}.days is required and must be non-empty text')
            # quantity
            quantity = item.get('quantity', 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                errors.append(f'{item_path}.quantity must be an integer >= 1')
            # price
            try:
                price_int = parse_money_int(item.get('price'), f'{item_path}.price')
                if price_int < 0:
                    errors.append(f'{item_path}.price must be >= 0')
                all_prices.append(price_int)
            except ValueError as exc:
                errors.append(str(exc))
            # note
            note = item.get('note')
            if not isinstance(note, str) or not note.strip():
                errors.append(f'{item_path}.note is required and must be non-empty text')
            elif len(note) < 40:
                warnings.append(f'{item_path}.note is short ({len(note)} chars); confirm it contains enough basic information')

    # Duplicate service names
    duplicate_names = sorted({n for n in service_names if service_names.count(n) > 1})
    for name in duplicate_names:
        errors.append(f'Duplicate service name: {name}')

    service_name_set = set(service_names)

    # ── Price magnitude check ──
    if all_prices and currency:
        if currency == 'IDR' and any(p < 1_000_000 for p in all_prices if p > 0):
            warnings.append('Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update from a previous RMB quote?')
        elif currency == 'RMB' and any(p >= 1_000_000 for p in all_prices):
            warnings.append('Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?')
        elif currency == 'USD' and any(p < 50 for p in all_prices if p > 0):
            warnings.append('Some prices appear too small for USD (min: $50). Did you forget to convert from IDR?')
        elif currency == 'USD' and any(p >= 500_000 for p in all_prices):
            warnings.append('Some prices appear too large for USD (>= 500,000). Did you forget to convert from IDR?')

    # ── Fee details ──
    fee_details = data.get('fee_details')
    if not isinstance(fee_details, list) or not fee_details:
        errors.append('fee_details must be a non-empty list')
    else:
        seen_fee_names = []
        for i, fd in enumerate(fee_details):
            path = f'fee_details[{i}]'
            if not isinstance(fd, dict):
                errors.append(f'{path} must be an object')
                continue
            name = fd.get('name')
            if not isinstance(name, str) or not name.strip():
                errors.append(f'{path}.name is required')
            else:
                seen_fee_names.append(name.strip())
                if name.strip() not in service_name_set:
                    errors.append(f'{path}.name "{name.strip()}" does not match any services item')
            # include (required)
            include = fd.get('include')
            if not isinstance(include, list) or not include:
                errors.append(f'{path}.include must be a non-empty list')
            else:
                for j, item in enumerate(include):
                    if not isinstance(item, str) or not item.strip():
                        errors.append(f'{path}.include[{j}] must be non-empty text')
            # exclude (optional, can be [])
            exclude = fd.get('exclude')
            if exclude is not None:
                if not isinstance(exclude, list):
                    errors.append(f'{path}.exclude must be a list when provided')
                else:
                    for j, item in enumerate(exclude):
                        if not isinstance(item, str) or not item.strip():
                            errors.append(f'{path}.exclude[{j}] must be non-empty text')
                    # Check: exclude should only contain service-specific items, not universal excludes
                    if exclude and universal_excludes:
                        overlap = [item for item in exclude if any(ue in item for ue in universal_excludes)]
                        if overlap:
                            warnings.append(
                                f'{path}.exclude contains universal items that should be in notes instead: '
                                f'{", ".join(overlap)}')
            # note (optional)
            note = fd.get('note')
            if note is not None and not isinstance(note, str):
                errors.append(f'{path}.note must be text when provided')

        missing_fee = sorted(service_name_set - set(seen_fee_names))
        if missing_fee:
            errors.append(f'fee_details missing services: {", ".join(missing_fee)}')

    # ── Process data ──
    process_data = data.get('process_data')
    if not isinstance(process_data, list) or not process_data:
        errors.append('process_data must be a non-empty list')
    else:
        seen_process_names = []
        for i, pd in enumerate(process_data):
            path = f'process_data[{i}]'
            if not isinstance(pd, dict):
                errors.append(f'{path} must be an object')
                continue
            name = pd.get('name')
            if not isinstance(name, str) or not name.strip():
                errors.append(f'{path}.name is required')
            else:
                seen_process_names.append(name.strip())
                if name.strip() not in service_name_set:
                    errors.append(f'{path}.name "{name.strip()}" does not match any services item')
            # process (required, non-empty list)
            process = pd.get('process')
            if not isinstance(process, list) or not process:
                errors.append(f'{path}.process must be a non-empty list')
            else:
                for j, step in enumerate(process):
                    if not isinstance(step, str) or not step.strip():
                        errors.append(f'{path}.process[{j}] must be non-empty text')
            # deliverables (required, non-empty list)
            deliverables = pd.get('deliverables')
            if not isinstance(deliverables, list) or not deliverables:
                errors.append(f'{path}.deliverables must be a non-empty list')
            else:
                for j, item in enumerate(deliverables):
                    if not isinstance(item, str) or not item.strip():
                        errors.append(f'{path}.deliverables[{j}] must be non-empty text')

        missing_process = sorted(service_name_set - set(seen_process_names))
        if missing_process:
            errors.append(f'process_data missing services: {", ".join(missing_process)}')

    # ── Doc data ──
    doc_data = data.get('doc_data')
    if not isinstance(doc_data, list) or not doc_data:
        errors.append('doc_data must be a non-empty list')
    else:
        seen_doc_names = []
        for i, dd in enumerate(doc_data):
            path = f'doc_data[{i}]'
            if not isinstance(dd, dict):
                errors.append(f'{path} must be an object')
                continue
            name = dd.get('name')
            if not isinstance(name, str) or not name.strip():
                errors.append(f'{path}.name is required')
            else:
                seen_doc_names.append(name.strip())
                if name.strip() not in service_name_set:
                    errors.append(f'{path}.name "{name.strip()}" does not match any services item')
            # docs (required, non-empty list)
            docs = dd.get('docs')
            if not isinstance(docs, list) or not docs:
                errors.append(f'{path}.docs must be a non-empty list')
            else:
                for j, item in enumerate(docs):
                    if not isinstance(item, str) or not item.strip():
                        errors.append(f'{path}.docs[{j}] must be non-empty text')

        missing_doc = sorted(service_name_set - set(seen_doc_names))
        if missing_doc:
            errors.append(f'doc_data missing services: {", ".join(missing_doc)}')

    # ── Notes ──
    notes = data.get('notes')
    if notes is not None:
        if not isinstance(notes, list):
            errors.append('notes must be a list when provided')
        else:
            for i, note in enumerate(notes):
                path = f'notes[{i}]'
                if isinstance(note, dict):
                    text = note.get('text')
                    indent = note.get('indent', 360)
                    if not isinstance(text, str) or not text.strip():
                        errors.append(f'{path}.text must be non-empty text')
                    if not isinstance(indent, int) or indent < 0:
                        errors.append(f'{path}.indent must be a non-negative integer')
                elif isinstance(note, (list, tuple)) and len(note) == 2:
                    text, indent = note
                    if not isinstance(text, str) or not text.strip():
                        errors.append(f'{path} text must be non-empty')
                    if not isinstance(indent, int) or indent < 0:
                        errors.append(f'{path} indent must be non-negative')
                else:
                    errors.append(f'{path} must be an object with text/indent or a 2-item pair')

    # ── Discount ──
    discount_amount = data.get('discount_amount', 0)
    try:
        discount_int = parse_money_int(discount_amount, 'discount_amount')
        if discount_int < 0:
            errors.append('discount_amount must be >= 0')
    except ValueError as exc:
        errors.append(str(exc))
        discount_int = 0

    # ── Subtotal vs discount ──
    subtotal = sum(all_prices)
    if discount_int > subtotal:
        errors.append(f'discount_amount ({discount_int}) cannot exceed subtotal ({subtotal})')

    # ── VAT sanity check (pre-flight) ──
    amounts = calculate_amounts(subtotal, discount_int, entity_cfg['vat_rate'], currency)
    vat_pct = vat_percent_label(amounts['vat_rate'])
    print(f"  预估: 小计={format_price_int(amounts['subtotal'], currency)} | "
          f"优惠={format_price_int(amounts['discount'], currency)} | "
          f"增值税({vat_pct})={format_price_vat(amounts['vat'], currency)} | "
          f"含税总计={format_price_total(amounts['total'], currency)}")

    # ── doc_notes_text ──
    doc_notes_text = data.get('doc_notes_text', data.get('doc_notes', []))
    if doc_notes_text is not None:
        if not isinstance(doc_notes_text, list):
            errors.append('doc_notes_text must be a list when provided')
        else:
            for i, item in enumerate(doc_notes_text):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f'doc_notes_text[{i}] must be non-empty text')

    # ── quote_meta overrides ──
    quote_meta = data.get('quote_meta', {})
    if quote_meta is None:
        quote_meta = {}
    if not isinstance(quote_meta, dict):
        errors.append('quote_meta must be an object when provided')
        quote_meta = {}
    else:
        for key in ('title_line1', 'title_line2', 'quote_date', 'customer_name', 'contact_name', 'contact_info', 'contract_no'):
            value = quote_meta.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f'quote_meta.{key} must be text when provided')
        quote_date = quote_meta.get('quote_date')
        if isinstance(quote_date, str) and quote_date.strip():
            try:
                from datetime import date
                date.fromisoformat(quote_date.strip())
            except ValueError:
                errors.append('quote_meta.quote_date must use YYYY-MM-DD format when provided')

    payment_terms = quote_meta.get('payment_terms')
    if payment_terms is not None:
        if not isinstance(payment_terms, list) or not payment_terms:
            errors.append('quote_meta.payment_terms must be a non-empty list when provided')
        else:
            for i, item in enumerate(payment_terms):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f'payment_terms[{i}] must be non-empty text')
            if not errors:
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
                        choices=['jakarta', 'beijing', 'xian', 'shenzhen', 'shanghai', 'shanghai_new'],
                        help='Signing entity (required)')
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
