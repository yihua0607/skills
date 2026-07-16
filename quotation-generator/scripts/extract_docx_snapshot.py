#!/usr/bin/env python3
"""Extract visible text and tables from a quotation .docx into JSON.

This is a read-only helper for preserving user edits before rebuilding from
quotation.json. It does not infer business meaning; the Agent compares this
snapshot with quotation.json and asks the user about ambiguous differences.

Usage:
  python3 scripts/extract_docx_snapshot.py --input edited.docx --output docx_snapshot.json
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

def text_of(el):
    return ''.join((t.text or '') for t in el.findall('.//' + w('t'))).strip()

def extract_xml_from_docx(docx_path, member):
    try:
        with zipfile.ZipFile(docx_path) as zf:
            if member not in zf.namelist():
                return None
            return ET.fromstring(zf.read(member))
    except zipfile.BadZipFile as exc:
        raise ValueError(f'Not a valid .docx file: {docx_path}') from exc

def extract_paragraphs(root):
    out = []
    if root is None:
        return out
    for idx, p in enumerate(root.findall('.//' + w('p')), start=1):
        text = text_of(p)
        if text:
            out.append({'index': idx, 'text': text})
    return out

def extract_tables(root):
    out = []
    if root is None:
        return out
    for t_idx, tbl in enumerate(root.findall('.//' + w('tbl')), start=1):
        rows = []
        for r_idx, tr in enumerate(tbl.findall(w('tr')), start=1):
            cells = []
            for c_idx, tc in enumerate(tr.findall(w('tc')), start=1):
                cells.append({'column': c_idx, 'text': text_of(tc)})
            rows.append({'row': r_idx, 'cells': cells})
        out.append({'table': t_idx, 'rows': rows})
    return out

def classify_paragraphs(paragraphs):
    sections = {
        'quote_meta_candidates': [],
        'payment_candidates': [],
        'bank_candidates': [],
        'signature_candidates': [],
        'all_text': paragraphs,
    }
    for item in paragraphs:
        text = item['text']
        if any(k in text for k in ('报价日期', '客户', '联系人', '联系方式', '合同号')):
            sections['quote_meta_candidates'].append(item)
        if any(k in text for k in ('付款', '支付', '发票', '合同金额')):
            sections['payment_candidates'].append(item)
        if any(k in text for k in ('开户', '账户', '账号', '银行', 'SWIFT', '税号')):
            sections['bank_candidates'].append(item)
        if re.search(r'(北京山海图|上海山海图|PT\. SHAN HAI MAP)', text):
            sections['signature_candidates'].append(item)
    return sections

def main():
    parser = argparse.ArgumentParser(description='Extract visible .docx text/tables into a JSON snapshot.')
    parser.add_argument('--input', required=True, help='Edited quotation .docx')
    parser.add_argument('--output', required=True, help='Output snapshot JSON path')
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)
    if not os.path.exists(input_path):
        print(f'❌ Input not found: {input_path}', file=sys.stderr)
        sys.exit(1)

    try:
        document = extract_xml_from_docx(input_path, 'word/document.xml')
        header = extract_xml_from_docx(input_path, 'word/header1.xml')
    except ValueError as exc:
        print(f'❌ {exc}', file=sys.stderr)
        sys.exit(1)

    paragraphs = extract_paragraphs(document)
    tables = extract_tables(document)
    header_paragraphs = extract_paragraphs(header)
    snapshot = {
        'source_docx': input_path,
        'header_paragraphs': header_paragraphs,
        'paragraphs': paragraphs,
        'tables': tables,
        'classified': classify_paragraphs(header_paragraphs + paragraphs),
        'usage_note': 'Compare this customer-visible snapshot with quotation.json. Map clear differences back to quote_meta/services/fee_details/process_data/doc_data/notes; ask the user about ambiguous business meaning before rebuilding.',
    }

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f'✅ Wrote snapshot: {output_path}')
    print(f'Paragraphs: {len(paragraphs)} | Tables: {len(tables)} | Header paragraphs: {len(header_paragraphs)}')

if __name__ == '__main__':
    main()
