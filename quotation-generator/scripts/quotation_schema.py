"""Validate the v2 quotation schema: one flat service is one source of truth."""
import difflib
import json
import os
import re

from scripts.quotation_common import parse_money_int

AI_CODE_RE = re.compile(r'^.+-\d{19}$')
TOP_LEVEL_FIELDS = {
    '_meta', 'quote_meta', 'services', 'discount_amount', 'withholding_tax', 'notes',
}
META_FIELDS = {
    'schema_version', 'description', 'applicable_entity', 'query_result_file',
    'source_currency', 'target_currency', 'exchange_rates',
}
QUOTE_META_FIELDS = {
    'title_line1', 'title_line2', 'quote_date', 'customer_name', 'contact_name',
    'contact_info', 'contract_no', 'payment_terms',
    # 签名栏「报价人」覆盖值：不填 = 用默认（企微会话取使用者姓名，其他环境留空）；
    # 填了就用它；显式填空串 = 强制留空（见 scripts/quoter_name.py）
    'quoter_name',
}
SERVICE_FIELDS = {
    'line_id', 'ai_code', 'code', 'name', 'category', 'quantity', 'unit', 'days',
    'price', 'note', 'fees', 'process', 'deliverables', 'documents',
}
FEE_FIELDS = {'include', 'exclude', 'note'}


def reject_unknown_fields(obj, allowed, path, errors):
    if not isinstance(obj, dict):
        return
    for key in sorted(set(obj) - allowed):
        suggestion = difflib.get_close_matches(key, allowed, n=1, cutoff=0.72)
        hint = f'; did you mean {suggestion[0]}?' if suggestion else ''
        prefix = f'{path}.' if path else ''
        errors.append(f'Unknown field: {prefix}{key}{hint}')


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
    value = obj.get(key, [])
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


def validate_service(service, index, errors, warnings):
    path = f'services[{index}]'
    if not isinstance(service, dict):
        errors.append(f'{path} must be an object')
        return None
    reject_unknown_fields(service, SERVICE_FIELDS, path, errors)
    line_id = require_text(service, 'line_id', path, errors)
    code = require_text(service, 'code', path, errors)
    name = require_text(service, 'name', path, errors)
    unit = require_text(service, 'unit', path, errors)
    days = require_text(service, 'days', path, errors)
    note = require_text(service, 'note', path, errors)

    ai_code = service.get('ai_code')
    if ai_code is not None:
        if not isinstance(ai_code, str) or not ai_code.strip():
            errors.append(f'{path}.ai_code must be non-empty text or null')
            ai_code = None
        else:
            ai_code = ai_code.strip()
            if not AI_CODE_RE.fullmatch(ai_code):
                errors.append(f'{path}.ai_code is not a complete aiCode: {ai_code}')

    category = service.get('category')
    if category is not None:
        if not isinstance(category, str):
            errors.append(f'{path}.category must be text or null')
            category = None
        else:
            category = category.strip() or None

    quantity = service.get('quantity')
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        errors.append(f'{path}.quantity must be an integer >= 1')
        quantity = 1
    if name and re.search(r'\s*[x×]\d+$', name):
        errors.append(f'{path}.name must not include quantity suffix; use quantity instead')

    try:
        price_int = parse_money_int(service.get('price'), f'{path}.price')
        if price_int < 0:
            errors.append(f'{path}.price must be >= 0')
    except ValueError as exc:
        errors.append(str(exc))
        price_int = 0

    fees = service.get('fees')
    if not isinstance(fees, dict):
        errors.append(f'{path}.fees is required and must be an object')
        fees = {}
    reject_unknown_fields(fees, FEE_FIELDS, f'{path}.fees', errors)
    include = require_text_list(fees, 'include', f'{path}.fees', errors)
    exclude = optional_text_list(fees, 'exclude', f'{path}.fees', errors)
    fee_note = fees.get('note', '')
    if fee_note is None:
        fee_note = ''
    if not isinstance(fee_note, str):
        errors.append(f'{path}.fees.note must be text')
        fee_note = ''

    process = require_text_list(service, 'process', path, errors)
    deliverables = require_text_list(service, 'deliverables', path, errors)
    documents = require_text_list(service, 'documents', path, errors)
    if name and len(note) < 40:
        warnings.append(f'{path}.note is short; confirm it contains enough basic information')

    return {
        'line_id': line_id, 'ai_code': ai_code, 'code': code, 'name': name,
        'display_name': f'{name}×{quantity}' if quantity > 1 else name,
        'category': category, 'quantity': quantity, 'unit': unit, 'days': days,
        'price': f'{price_int:,}', 'price_int': price_int, 'note': note,
        'fees': {'include': include, 'exclude': exclude, 'note': fee_note.strip()},
        'process': process, 'deliverables': deliverables, 'documents': documents,
    }


def validate_source_data(data, data_path, services, errors, warnings):
    """Cross-check quoted services against the fetch output named in _meta."""
    meta = data.get('_meta') if isinstance(data.get('_meta'), dict) else {}
    relative = meta.get('query_result_file')
    if not relative:
        if any(service.get('ai_code') is not None for service in services):
            errors.append(
                '_meta.query_result_file is required when any services[].ai_code is not null'
            )
        return
    if not isinstance(relative, str) or not relative.strip():
        errors.append('_meta.query_result_file must be non-empty text when provided')
        return
    if not data_path:
        warnings.append('source integrity was not checked because quotation data path was not provided')
        return
    source_path = relative if os.path.isabs(relative) else os.path.join(os.path.dirname(os.path.abspath(data_path)), relative)
    try:
        with open(source_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        errors.append(f'Cannot read _meta.query_result_file: {source_path}: {exc}')
        return
    records = payload.get('services') if isinstance(payload, dict) else None
    if isinstance(payload, dict) and payload.get('success') is False:
        errors.append(
            f'_meta.query_result_file reports failure: {payload.get("message") or "unknown error"}'
        )
    if not isinstance(records, list):
        errors.append(f'_meta.query_result_file has no services list: {source_path}')
        return

    by_code = {}
    duplicate_codes = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        full = record.get('原始输入') or record.get('查询aiCode')
        if isinstance(full, str) and AI_CODE_RE.fullmatch(full.strip()):
            full = full.strip()
            if full in by_code:
                duplicate_codes.add(full)
            by_code[full] = record
            if record.get('查询成功') is False:
                errors.append(
                    f'query result service reports failure: {full}: '
                    f'{record.get("查询消息") or "unknown error"}'
                )
    for ai_code in sorted(duplicate_codes):
        errors.append(f'duplicate aiCode in query result: {ai_code}')
    quoted_codes = set()
    for index, service in enumerate(services):
        ai_code = service.get('ai_code')
        if ai_code is None:
            continue
        quoted_codes.add(ai_code)
        record = by_code.get(ai_code)
        if record is None:
            errors.append(f'services[{index}].ai_code not found in query result: {ai_code}')
            continue
        expected_code = record.get('服务编码')
        if expected_code != service.get('code'):
            errors.append(
                f'services[{index}].code does not match query result: '
                f'{service.get("code")} != {expected_code}'
            )
        for field, source_field in (('name', '服务名称'), ('quantity', '服务数量'), ('unit', '服务单位')):
            source_value = record.get(source_field)
            if source_value is not None and source_value != service.get(field):
                warnings.append(
                    f'services[{index}].{field} differs from query result: '
                    f'{service.get(field)!r} != {source_value!r}'
                )
    omitted = sorted(set(by_code) - quoted_codes)
    if omitted:
        warnings.append('query result services not included in quotation: ' + ', '.join(omitted))


def validate_and_normalize_data(data, data_path=None):
    """Validate input data before touching the Word template."""
    errors, warnings = [], []
    if not isinstance(data, dict):
        raise ValueError('Quotation data must be a JSON object')
    reject_unknown_fields(data, TOP_LEVEL_FIELDS, '', errors)
    meta = data.get('_meta')
    if not isinstance(meta, dict):
        errors.append('_meta is required and must be an object')
        meta = {'schema_version': 2}
    else:
        reject_unknown_fields(meta, META_FIELDS, '_meta', errors)
        if meta.get('schema_version') != 2:
            errors.append('_meta.schema_version must be 2')

    raw_services = data.get('services')
    if not isinstance(raw_services, list) or not raw_services:
        errors.append('services must be a non-empty list')
        raw_services = []
    services = []
    for index, service in enumerate(raw_services):
        normalized = validate_service(service, index, errors, warnings)
        if normalized is not None:
            services.append(normalized)
    ids = [service['line_id'] for service in services if service['line_id']]
    for line_id in sorted({value for value in ids if ids.count(value) > 1}):
        errors.append(f'Duplicate service line_id: {line_id}')
    seen_categories = set()
    previous_category = object()
    for index, service in enumerate(services):
        category = service['category']
        if category != previous_category:
            if category is not None and category in seen_categories:
                errors.append(
                    f'services[{index}].category is not contiguous: {category}'
                )
            if category is not None:
                seen_categories.add(category)
            previous_category = category

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
            text, indent = note.get('text'), note.get('indent', 360)
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

    raw_meta = data.get('quote_meta', {})
    if raw_meta is None:
        raw_meta = {}
    if not isinstance(raw_meta, dict):
        errors.append('quote_meta must be an object when provided')
        raw_meta = {}
    reject_unknown_fields(raw_meta, QUOTE_META_FIELDS, 'quote_meta', errors)
    quote_meta = {}
    for key in ('title_line1', 'title_line2', 'quote_date', 'customer_name', 'contact_name', 'contact_info', 'contract_no', 'quoter_name'):
        value = raw_meta.get(key)
        if value is not None:
            if not isinstance(value, str):
                errors.append(f'quote_meta.{key} must be text when provided')
            else:
                quote_meta[key] = value.strip()
    payment_terms = raw_meta.get('payment_terms')
    normalized_terms = None
    if payment_terms is not None:
        if not isinstance(payment_terms, list) or not payment_terms:
            errors.append('quote_meta.payment_terms must be a non-empty list when provided')
            normalized_terms = []
        else:
            normalized_terms = []
            for i, item in enumerate(payment_terms):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f'payment_terms[{i}] must be non-empty text')
                else:
                    normalized_terms.append(item.strip())
    quote_meta['payment_terms'] = normalized_terms

    try:
        discount = parse_money_int(data.get('discount_amount', 0), 'discount_amount')
        if discount < 0:
            errors.append('discount_amount must be >= 0')
    except ValueError as exc:
        errors.append(str(exc))
        discount = 0
    withholding = data.get('withholding_tax', False)
    if not isinstance(withholding, bool):
        errors.append('withholding_tax must be a boolean (true/false) when provided')
        withholding = False
    if discount > sum(service['price_int'] for service in services):
        errors.append('discount_amount cannot exceed subtotal')
    validate_source_data(data, data_path, services, errors, warnings)
    if errors:
        raise ValueError('Invalid quotation data:\n- ' + '\n- '.join(errors))
    return {
        '_meta': meta, 'services': services, 'notes': normalized_notes,
        'quote_meta': quote_meta, 'discount_amount': discount,
        'withholding_tax': withholding, 'warnings': warnings,
    }
