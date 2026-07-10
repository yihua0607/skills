#!/usr/bin/env python3
"""Compatibility helper for extracting payment terms from a generated quotation .docx.

Normal rebuilds do not need to write payment terms back to quotation.json:
build_quotation.py preserves visible payment terms from an existing .docx.
This script remains available for legacy/manual workflows and dry-run checks.

Usage:
  python3 scripts/sync_payment_terms.py --input existing.docx --data quotation.json
  python3 scripts/sync_payment_terms.py --input existing.docx --data quotation.json --dry-run
"""
import argparse
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def w(tag):
    return f'{{{W}}}{tag}'


def extract_paragraph_texts(docx_path):
    if not os.path.exists(docx_path):
        raise FileNotFoundError(f'DOCX not found: {docx_path}')
    try:
        with zipfile.ZipFile(docx_path) as zf:
            xml = zf.read('word/document.xml')
    except KeyError as exc:
        raise ValueError('Invalid .docx: word/document.xml not found') from exc
    except zipfile.BadZipFile as exc:
        raise ValueError(f'Invalid .docx file: {docx_path}') from exc

    root = ET.fromstring(xml)
    texts = []
    for p in root.findall('.//' + w('p')):
        text = ''.join((t.text or '') for t in p.findall('.//' + w('t'))).strip()
        if text:
            texts.append(text)
    return texts


def is_payment_heading(text):
    compact = re.sub(r'\s+', '', text)
    return '付款条件' in compact or '付款方式' in compact


def is_next_section_heading(text):
    compact = re.sub(r'\s+', '', text)
    if is_payment_heading(compact):
        return False
    section_markers = (
        '服务流程及交付材料清单',
        '所需资料',
        '银行账户',
        '保密义务',
    )
    if any(marker in compact for marker in section_markers):
        return True
    return False


def extract_payment_terms(docx_path):
    texts = extract_paragraph_texts(docx_path)
    start = None
    for i, text in enumerate(texts):
        if is_payment_heading(text):
            start = i + 1
            break
    if start is None:
        raise ValueError('Payment terms section not found in .docx')

    terms = []
    for text in texts[start:]:
        if is_next_section_heading(text):
            break
        cleaned = text.strip()
        if cleaned:
            terms.append(cleaned)

    if not terms:
        raise ValueError('Payment terms section found, but no payment terms were extracted')
    return terms


def _parse_money_amounts(text):
    amounts = []
    occupied = []

    def overlaps(start, end):
        return any(start < used_end and end > used_start for used_start, used_end in occupied)

    currency_before = re.compile(
        r'(?:￥|¥|\$|Rp|RMB|CNY|USD|IDR)\s*([0-9][0-9,]*(?:\.\d+)?)',
        flags=re.I,
    )
    currency_after = re.compile(
        r'([0-9][0-9,]*(?:\.\d+)?)\s*(?:元|人民币|美元|美金|印尼盾)',
        flags=re.I,
    )

    for pattern in (currency_before, currency_after):
        for match in pattern.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            raw = match.group(1).replace(',', '')
            try:
                amounts.append(float(raw))
                occupied.append((match.start(), match.end()))
            except ValueError:
                continue
    return amounts


def check_payment_terms_reasonableness(terms, contract_total=None, currency=None):
    """Return warnings for obviously unreasonable visible payment terms.

    Payment terms are user-managed, so this helper is intentionally conservative:
    it warns only when explicit percentages exceed 100%, or explicit payment
    amounts exceed the contract total.
    """
    if not terms:
        return []
    text = '；'.join(str(term) for term in terms if term)
    percentages = []
    for match in re.finditer(r'(\d+(?:\.\d+)?)\s*%', text):
        try:
            percentages.append(float(match.group(1)))
        except ValueError:
            continue

    warnings = []
    total = sum(percentages)
    if any(value > 100 for value in percentages):
        warnings.append('付款方式中存在超过 100% 的单项付款比例，请确认。')
    if total > 100:
        label = f'{total:g}%'
        parts = ' + '.join(f'{value:g}%' for value in percentages)
        warnings.append(f'付款方式比例合计为 {label}（{parts}），超过 100%，请提醒用户确认。')

    if contract_total is not None:
        money_amounts = _parse_money_amounts(text)
        money_total = sum(money_amounts)
        try:
            contract_total_num = float(contract_total)
        except (TypeError, ValueError):
            contract_total_num = None
        if money_amounts and contract_total_num is not None and money_total > contract_total_num:
            currency_label = currency or ''
            money_label = f'{money_total:,.2f}'.rstrip('0').rstrip('.')
            contract_label = f'{contract_total_num:,.2f}'.rstrip('0').rstrip('.')
            parts = ' + '.join(f'{value:,.2f}'.rstrip('0').rstrip('.') for value in money_amounts)
            warnings.append(
                f'付款方式金额合计为 {currency_label}{money_label}（{parts}），'
                f'大于合同含税总计 {currency_label}{contract_label}，请提醒用户确认。')
    return warnings


def load_json(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f'Data file not found: {path}')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description='Sync payment terms from an edited quotation .docx into quotation.json.')
    parser.add_argument('--input', required=True, help='Existing edited quotation .docx')
    parser.add_argument('--data', required=True, help='quotation.json to update')
    parser.add_argument('--dry-run', action='store_true', help='Print extracted terms without writing JSON')
    args = parser.parse_args()

    try:
        terms = extract_payment_terms(args.input)
        data = load_json(args.data)
    except Exception as exc:
        print(f'❌ {exc}', file=sys.stderr)
        sys.exit(1)

    print('Extracted quote_meta.payment_terms:')
    for term in terms:
        print(f'  - {term}')

    quote_meta = data.get('quote_meta')
    if not isinstance(quote_meta, dict):
        quote_meta = {}
    old_terms = quote_meta.get('payment_terms')
    if old_terms == terms:
        print('✅ quotation.json already has the same quote_meta.payment_terms')
        return

    if args.dry_run:
        print('Dry run only: quotation.json was not modified')
        return

    quote_meta['payment_terms'] = terms
    data['quote_meta'] = quote_meta
    with open(args.data, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(f'✅ Updated quote_meta.payment_terms in {args.data}')


if __name__ == '__main__':
    main()
