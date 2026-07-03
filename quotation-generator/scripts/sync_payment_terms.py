#!/usr/bin/env python3
"""Sync manually edited payment terms from a generated quotation .docx back to quotation.json.

Use this before rebuilding an existing quotation when the user may have edited
payment terms directly in Word/WPS. The .docx is treated as the latest source
for the visible payment terms, and quotation.json is updated so later rebuilds
preserve those edits.

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
    if re.match(r'^\d+[.．、]', compact):
        return True
    return any(marker in compact for marker in (
        '服务流程及交付材料清单',
        '所需资料',
        '银行账户',
        '保密义务',
    ))


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
