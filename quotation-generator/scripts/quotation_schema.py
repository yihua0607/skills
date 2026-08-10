"""Shared quotation data schema validation and normalization.

Both build_quotation.py and validate_data.py import this module so preflight
validation cannot drift away from the builder's required fields.
"""
import re

from scripts.quotation_common import parse_money_int

REQUIRED_TOP_LEVEL_KEYS = ('services', 'fee_details', 'process_data', 'doc_data')

def require_text(obj, key, path, errors):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f'{path}.{key} is required and must be non-empty text')
        return ''
    return value.strip()


def require_text_list(obj, key, path, errors):
    value = obj.get(key)
    if not isinstance(value, list) or not value:
        errors.append(f'{path}.{key} is required and must be a non-empty list')
        return []
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f'{path}.{key}[{i}] must be non-empty text')
        else:
            out.append(item.strip())
    return out


def optional_text_list(obj, key, path, errors):
    """Like require_text_list but allows empty/missing lists (returns [] if absent or empty)."""
    value = obj.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f'{path}.{key} must be a list when provided')
        return []
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f'{path}.{key}[{i}] must be non-empty text')
        else:
            out.append(item.strip())
    return out


def validate_and_normalize_data(data):
    """Validate input data before touching the Word template."""
    errors = []
    warnings = []

    if not isinstance(data, dict):
        raise ValueError('Quotation data must be a JSON object')

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f'Missing top-level key: {key}')

    services_data = data.get('services')
    if not isinstance(services_data, list) or not services_data:
        errors.append('services must be a non-empty list')
        services_data = []

    service_names = []
    normalized_services = []
    for group_idx, group in enumerate(services_data):
        path = f'services[{group_idx}]'
        if not isinstance(group, dict):
            errors.append(f'{path} must be an object')
            continue
        category = group.get('category')
        if category is not None and not isinstance(category, str):
            errors.append(f'{path}.category must be text or null')
            category = None
        items = group.get('items')
        if not isinstance(items, list) or not items:
            errors.append(f'{path}.items must be a non-empty list')
            continue
        normalized_items = []
        for item_idx, item in enumerate(items):
            item_path = f'{path}.items[{item_idx}]'
            if not isinstance(item, dict):
                errors.append(f'{item_path} must be an object')
                continue
            name = require_text(item, 'name', item_path, errors)
            service_id = require_text(item, 'id', item_path, errors)
            days = require_text(item, 'days', item_path, errors)
            note = require_text(item, 'note', item_path, errors)
            quantity = item.get('quantity', 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                errors.append(f'{item_path}.quantity must be an integer >= 1')
                quantity = 1
            if name and re.search(r'\s*[x×]\d+$', name):
                errors.append(f'{item_path}.name must not include quantity suffix; use quantity instead')
            try:
                price_int = parse_money_int(item.get('price'), f'{item_path}.price')
                if price_int < 0:
                    errors.append(f'{item_path}.price must be >= 0')
            except ValueError as exc:
                errors.append(str(exc))
                price_int = 0
            if name:
                service_names.append(name)
                if len(note) < 40:
                    warnings.append(f'{item_path}.note is short; confirm it contains enough basic information')
            display_name = f'{name}×{quantity}' if quantity > 1 else name
            normalized_items.append({
                'id': service_id,
                'name': name,
                'display_name': display_name,
                'quantity': quantity,
                'days': days,
                'price': f'{price_int:,}',
                'price_int': price_int,
                'note': note,
            })
        normalized_services.append({'category': category, 'items': normalized_items})

    duplicate_names = sorted({name for name in service_names if service_names.count(name) > 1})
    for name in duplicate_names:
        errors.append(f'Duplicate service name: {name}')
    service_name_set = set(service_names)

    def validate_named_records(key, list_fields, optional_fields=None, allow_note=False):
        """Validate named records. list_fields are required (must be non-empty).
        optional_fields allow empty/missing lists."""
        records = data.get(key)
        if not isinstance(records, list) or not records:
            errors.append(f'{key} must be a non-empty list')
            return []
        seen = []
        normalized = []
        for i, record in enumerate(records):
            path = f'{key}[{i}]'
            if not isinstance(record, dict):
                errors.append(f'{path} must be an object')
                continue
            name = require_text(record, 'name', path, errors)
            if name:
                seen.append(name)
                if name not in service_name_set:
                    errors.append(f'{path}.name does not match any services item: {name}')
            out = {'name': name}
            for field in list_fields:
                out[field] = require_text_list(record, field, path, errors)
            for field in (optional_fields or []):
                out[field] = optional_text_list(record, field, path, errors)
            if allow_note:
                note = record.get('note', '')
                if note is None:
                    note = ''
                if not isinstance(note, str):
                    errors.append(f'{path}.note must be text')
                    note = ''
                out['note'] = note
            normalized.append(out)
        missing = sorted(service_name_set - set(seen))
        extra = sorted(set(seen) - service_name_set)
        if missing:
            errors.append(f'{key} missing services: {", ".join(missing)}')
        if extra:
            errors.append(f'{key} contains unknown services: {", ".join(extra)}')
        return normalized

    fee_details = validate_named_records('fee_details', ['include'], optional_fields=['exclude'], allow_note=True)
    process_data = validate_named_records('process_data', ['process', 'deliverables'])
    doc_data = validate_named_records('doc_data', ['docs'])

    notes = data.get('notes', [])
    if notes is None:
        notes = []
    if not isinstance(notes, list):
        errors.append('notes must be a list when provided')
        notes = []
    normalized_notes = []
    for i, note in enumerate(notes):
        path = f'notes[{i}]'
        if isinstance(note, dict):
            text = note.get('text')
            indent = note.get('indent', 360)
        elif isinstance(note, (list, tuple)) and len(note) == 2:
            text, indent = note
        else:
            errors.append(f'{path} must be an object with text/indent or a 2-item pair')
            continue
        if not isinstance(text, str) or not text.strip():
            errors.append(f'{path}.text must be non-empty text')
            continue
        if not isinstance(indent, int) or indent < 0:
            errors.append(f'{path}.indent must be a non-negative integer')
            indent = 360
        normalized_notes.append((text.strip(), indent))

    doc_notes_text = data.get('doc_notes_text', data.get('doc_notes', []))
    if doc_notes_text is None:
        doc_notes_text = []
    if not isinstance(doc_notes_text, list):
        errors.append('doc_notes_text must be a list when provided')
        doc_notes_text = []
    normalized_doc_notes = []
    for i, item in enumerate(doc_notes_text):
        if not isinstance(item, str) or not item.strip():
            errors.append(f'doc_notes_text[{i}] must be non-empty text')
        else:
            normalized_doc_notes.append(item.strip())

    quote_meta_raw = data.get('quote_meta', {})
    if quote_meta_raw is None:
        quote_meta_raw = {}
    if not isinstance(quote_meta_raw, dict):
        errors.append('quote_meta must be an object when provided')
        quote_meta_raw = {}

    quote_meta = {}
    for key in ('title_line1', 'title_line2', 'quote_date', 'customer_name', 'contact_name', 'contact_info', 'contract_no'):
        value = quote_meta_raw.get(key)
        if value is not None:
            if not isinstance(value, str):
                errors.append(f'quote_meta.{key} must be text when provided')
            else:
                quote_meta[key] = value.strip()

    payment_terms = quote_meta_raw.get('payment_terms')
    normalized_payment_terms = None
    if payment_terms is not None:
        if not isinstance(payment_terms, list) or not payment_terms:
            errors.append('quote_meta.payment_terms must be a non-empty list when provided')
            normalized_payment_terms = []
        else:
            normalized_payment_terms = []
            for i, item in enumerate(payment_terms):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f'payment_terms[{i}] must be non-empty text')
                else:
                    normalized_payment_terms.append(item.strip())
    quote_meta['payment_terms'] = normalized_payment_terms

    discount_amount = data.get('discount_amount', 0)
    try:
        discount_amount = parse_money_int(discount_amount, 'discount_amount')
        if discount_amount < 0:
            errors.append('discount_amount must be >= 0')
    except ValueError as exc:
        errors.append(str(exc))
        discount_amount = 0

    # Withholding tax flag (optional, default False)
    withholding_tax = data.get('withholding_tax', False)
    if not isinstance(withholding_tax, bool):
        errors.append('withholding_tax must be a boolean (true/false) when provided')
        withholding_tax = False

    subtotal = sum(item['price_int'] for group in normalized_services for item in group['items'])
    if discount_amount > subtotal:
        errors.append('discount_amount cannot exceed subtotal')

    if errors:
        raise ValueError('Invalid quotation data:\n- ' + '\n- '.join(errors))
    return {
        '_meta': data.get('_meta', {}),
        'services': normalized_services,
        'fee_details': fee_details,
        'process_data': process_data,
        'doc_data': doc_data,
        'notes': normalized_notes,
        'doc_notes_text': normalized_doc_notes,
        'quote_meta': quote_meta,
        'discount_amount': discount_amount,
        'withholding_tax': withholding_tax,
        'warnings': warnings,
    }
