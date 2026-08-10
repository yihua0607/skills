#!/usr/bin/env python3
"""
Verify a generated quotation .docx for content consistency and format sanity.

Usage:
  python3 scripts/verify_quotation.py --input 报价单.docx
  python3 scripts/verify_quotation.py --input 报价单.docx --data quotation.json
  python3 scripts/verify_quotation.py --input 报价单.docx --entity jakarta

Checks:
  1. Header company name/address vs bank info consistency
  2. Signature company name vs header company name consistency
  3. Service name coverage (fee_details / process / docs all present)
  4. Font sanity (FangSong throughout)
  5. A4 page size (sectPr)
  6. Amount internal consistency (subtotal, discount, VAT, total)
  7. Cross-check with input data (if --data provided)
"""
import argparse, zipfile, os, sys, re, json, tempfile, shutil
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from scripts.sync_payment_terms import extract_payment_terms, check_payment_terms_reasonableness
from scripts.quotation_common import load_entity_config, currency_has_decimals


def w(tag):
    return f'{{{W}}}{tag}'


def normalize_company_name(name):
    """Normalize company name for comparison: strip '.' and collapse whitespace.

    Singapore entity (and others) may write the same legal name with or
    without a trailing/embedded dot (e.g. 'Pte.Ltd' vs 'Pte.Ltd.' or
    'PT. SHAN HAI MAP' vs 'PT SHAN HAI MAP'). A dot difference is a
    formatting artifact, not a real mismatch.
    """
    if not name:
        return ''
    return re.sub(r'\s+', ' ', name.replace('.', '')).strip()


def extract_paragraph_texts(root):
    """Extract text from all paragraphs in an XML element."""
    results = []
    for p in root.findall('.//' + w('p')):
        text = ''.join((t.text or '') for t in p.findall('.//' + w('t')))
        if text.strip():
            results.append((p, text.strip()))
    return results


def detect_currency(paragraph_texts):
    """Detect currency from document text."""
    for _, text in paragraph_texts:
        if '￥' in text or '¥' in text:
            return 'RMB'
        if 'Rp' in text:
            return 'IDR'
        if 'S$' in text:
            return 'SGD'
        if '฿' in text:
            return 'THB'
        if re.search(r'\$\s?\d', text):
            return 'USD'
    return 'RMB'


def detect_currency_from_tables(tables):
    """Detect currency from service/summary table amount cells."""
    for tbl in tables:
        for row in tbl.findall(w('tr')):
            cells = row.findall(w('tc'))
            # 未合并行：金额在 cells[3]（服务数据行/旧汇总行）；合并汇总行：金额是最后一个非空单元格
            candidates = []
            if len(cells) >= 4:
                candidates.append(''.join(
                    (t.text or '') for t in cells[3].findall('.//' + w('t'))
                ).strip())
            for cell in reversed(cells):
                txt = ''.join((t.text or '') for t in cell.findall('.//' + w('t'))).strip()
                if txt:
                    candidates.append(txt)
                    break
            for amount_text in candidates:
                if not amount_text:
                    continue
                if '￥' in amount_text or '¥' in amount_text:
                    return 'RMB'
                if re.search(r'\bRp\s*[\d,]', amount_text, flags=re.I):
                    return 'IDR'
                if re.search(r'\bS\$\s*[\d,]', amount_text):
                    return 'SGD'
                if '฿' in amount_text:
                    return 'THB'
                if re.search(r'\$\s*[\d,]', amount_text):
                    return 'USD'
    return None


def detect_entity(paragraph_texts, entity_config):
    """Detect signing entity from document text by matching bank lines."""
    bank_section = find_bank_info_section(paragraph_texts)
    if not bank_section:
        return None
    # Match by looking for unique bank info patterns
    for entity_key, cfg in entity_config.items():
        bank_lines = cfg.get('bank_lines', [])
        # Try matching on account number — most unique identifier
        for doc_line in bank_section:
            for cfg_line in bank_lines:
                # Extract account number patterns (Chinese or English)
                account_match = re.search(r'(账号|账户号码|银行账号|Account No)\s*[：:]*\s*(\S+)', cfg_line)
                if account_match:
                    account_num = account_match.group(2).replace(' ', '')
                    if account_num in doc_line.replace(' ', ''):
                        return entity_key
    # Fallback: match by company name in bank section
    for entity_key, cfg in entity_config.items():
        company = cfg.get('company', '')
        for doc_line in bank_section:
            if company in doc_line:
                return entity_key
    print("⚠️  WARNING: Could not auto-detect signing entity from bank info; entity-specific checks will be skipped.", file=sys.stderr)
    return None


def parse_formatted_amount(text, currency='RMB'):
    """Parse a formatted amount string into a number."""
    text = text.strip()
    if currency == 'RMB':
        text = text.replace('￥', '').replace('¥', '')
    elif currency == 'USD':
        text = text.replace('$', '')
    elif currency == 'THB':
        text = text.replace('฿', '')
    elif currency == 'SGD':
        text = text.replace('S$', '')
    else:
        text = re.sub(r'^Rp\s*', '', text, flags=re.I)
    text = text.replace(',', '').replace(' ', '')
    try:
        if '.' in text:
            return float(text)
        return int(text)
    except ValueError:
        return None


def find_bank_info_section(paragraph_texts):
    """Find bank info section paragraphs after the bank header line."""
    bank_lines = []
    started = False
    for _, text in paragraph_texts:
        if '所有款项汇到' in text:
            started = True
            continue
        if started:
            if '保密义务' in text or '报价从报价日起' in text:
                break
            bank_lines.append(text)
    return bank_lines


def extract_company_from_bank(bank_lines):
    """Extract company name from bank info lines."""
    for line in bank_lines:
        match = re.search(
            r'(账户名称|账号名称|开户名|户名|Beneficiary Name|Atas Nama)\s*[：:]\s*(.+)', line)
        if match:
            return match.group(2).strip()
    return None


def extract_address_from_bank(bank_lines):
    """Extract the company address from bank info lines.

    Skips bank-specific address lines (e.g. 'Bank Address:') so that
    only the company's own address is compared against the header.
    """
    for line in bank_lines:
        # Skip lines that are clearly the bank's own address
        if re.match(r'\s*(Bank Address|银行地址)\s*[：:]', line, flags=re.I):
            continue
        if re.match(r'\s*Beneficiary\s+Bank\s+Address\s*[：:]', line, flags=re.I):
            continue
        match = re.search(r'(地址|Address)\s*[：:]\s*(.+)', line, flags=re.I)
        if match:
            return match.group(2).strip()
    return None


def _company_candidates(entity_config):
    """Return company names sorted longest-first for subsidiary precedence."""
    return sorted(
        [cfg.get('company', '') for cfg in entity_config.values() if cfg.get('company')],
        key=len, reverse=True,
    )


def extract_signature_company(tables, entity_config):
    """Extract company name only from the signature table.

    The signature table is identified by the visible labels '报价人' and
    '同意报价人'. Matching the whole document is unsafe because bank/header
    sections also contain company names and can mask a missing or wrong
    signature company.
    """
    if not entity_config:
        return None
    candidates = _company_candidates(entity_config)
    for tbl in tables:
        rows = tbl.findall(w('tr'))
        signature_row_idx = None
        for idx, row in enumerate(rows):
            row_text = ''.join((t.text or '') for t in row.findall('.//' + w('t'))).strip()
            if '报价人' in row_text and '同意报价人' in row_text:
                signature_row_idx = idx
                break
        if signature_row_idx is None:
            continue

        # Search the label row and the following rows in this table only. This
        # supports templates that put the company on the same row or just below.
        for row in rows[signature_row_idx:signature_row_idx + 4]:
            row_text = ''.join((t.text or '') for t in row.findall('.//' + w('t'))).strip()
            for company in candidates:
                if company and company in row_text:
                    return company
        return None
    return None


def extract_service_names_from_table(tables):
    """Extract service names from the service content table (first table)."""
    names = []
    if not tables:
        return names
    # The first table is the service content table
    first_tbl = tables[0]
    rows = first_tbl.findall(w('tr'))
    for row in rows:
        cells = row.findall(w('tc'))
        if len(cells) >= 2:
            # Skip header rows (light blue fill) and summary rows
            tcPr = cells[1].find(w('tcPr'))
            if tcPr is not None:
                fill = tcPr.find(w('shd'))
                if fill is not None and fill.get(w('val')) == 'clear' and fill.get(w('fill')) == 'BDD6EE':
                    continue
                # Skip category rows (gridSpan=5)
                gridSpan = tcPr.find(w('gridSpan'))
                if gridSpan is not None:
                    continue
            name_text = ''.join((t.text or '') for t in cells[1].findall('.//' + w('t'))).strip()
            # Strip quantity suffix for comparison: "公司注册×2" → "公司注册"
            base_name = re.sub(r'\s*[x×]\d+$', '', name_text)
            # Skip summary rows like "小计", "优惠金额", "增值税 11%", "含税总计", "预扣税"
            summary_prefixes = ('服务内容', '小计', '优惠金额', '增值税', '含税总计', '预扣税')
            if base_name and not any(base_name.startswith(p) for p in summary_prefixes):
                names.append(base_name)
    return names


def _is_header_cell(tc):
    """Return True if a table cell is a header cell (light blue fill)."""
    tcPr = tc.find(w('tcPr'))
    if tcPr is None:
        return False
    fill = tcPr.find(w('shd'))
    if fill is not None and fill.get(w('val')) == 'clear' and fill.get(w('fill')) == 'BDD6EE':
        return True
    return False


def _is_category_row(tr):
    """Return True if a row spans all columns (category header)."""
    tc = tr.find(w('tc'))
    if tc is None:
        return False
    tcPr = tc.find(w('tcPr'))
    if tcPr is None:
        return False
    gridSpan = tcPr.find(w('gridSpan'))
    if gridSpan is not None:
        return True
    return False


def extract_names_from_table_column(tables, table_index, column_index):
    """Extract non-empty names from a specific column of a specific table.

    Skips header rows and category rows. Strips quantity suffixes.
    """
    names = []
    if not tables or table_index >= len(tables):
        return names
    tbl = tables[table_index]
    for row in tbl.findall(w('tr')):
        if _is_category_row(row):
            continue
        cells = row.findall(w('tc'))
        if len(cells) <= column_index:
            continue
        cell = cells[column_index]
        if _is_header_cell(cell):
            continue
        name_text = ''.join((t.text or '') for t in cell.findall('.//' + w('t'))).strip()
        # Strip quantity suffix for comparison: "公司注册×2" → "公司注册"
        base_name = re.sub(r'\s*[x×]\d+$', '', name_text)
        if base_name and base_name != '项目':
            names.append(base_name)
    return names


def extract_fee_section_names(paragraph_texts):
    """Extract service names from the fee details section.

    The fee details section starts after '*备注：' and ends before '2.付款条件：'.
    Service names are paragraphs matching 'N. 服务名'.
    """
    names = []
    in_fee_section = False
    for _, text in paragraph_texts:
        if text.startswith('*备注：'):
            in_fee_section = True
            continue
        if in_fee_section and ('2.付款条件' in text or '2.付款方式' in text):
            break
        if in_fee_section:
            fee_match = re.match(r'^\d+\.\s+(.+)$', text)
            if fee_match:
                names.append(fee_match.group(1).strip())
    return names


def extract_header_info(header_root):
    """Extract company name and address from header XML."""
    texts = extract_paragraph_texts(header_root)
    non_empty = [text for _, text in texts if text.strip()]
    company = non_empty[0] if len(non_empty) >= 1 else None
    address = non_empty[1] if len(non_empty) >= 2 else None
    return company, address


def check_address_similarity(addr1, addr2):
    """Check if two addresses are consistent, allowing for province prefix differences."""
    if not addr1 or not addr2:
        return False, "One or both addresses are empty"
    if addr1 == addr2:
        return True, "Exact match"
    core1 = re.sub(r'^.{2,6}(省|市)', '', addr1)
    core2 = re.sub(r'^.{2,6}(省|市)', '', addr2)
    if core1 == core2:
        return True, (
            f"Core address matches (prefix difference: "
            f"'{addr1}' vs '{addr2}')")
    if addr1.endswith(core2) or addr2.endswith(core1):
        return True, (
            f"Similar addresses (superset: "
            f"'{addr1}' vs '{addr2}')")
    return False, f"Addresses differ: header='{addr1}' vs bank='{addr2}'"


def check_fonts(document_root):
    """Check that fonts are consistently FangSong."""
    non_fangsong = set()
    for rpr in document_root.findall('.//' + w('rPr')):
        rfonts = rpr.find(w('rFonts'))
        if rfonts is not None:
            for attr_key in ['ascii', 'hAnsi', 'eastAsia', 'cs']:
                val = rfonts.get(w(attr_key))
                if val and val not in ('FangSong', 'Times New Roman'):
                    non_fangsong.add(val)
    return sorted(non_fangsong)


def check_page_size(document_root):
    """Check A4 page size in sectPr."""
    body = document_root.find(w('body'))
    if body is None:
        return ["No <w:body> element found"]
    sectPr = body.find(w('sectPr'))
    if sectPr is None:
        return ["No sectPr found - page size undefined"]
    pgSz = sectPr.find(w('pgSz'))
    if pgSz is None:
        return ["No pgSz in sectPr"]
    w_val = pgSz.get(w('w'))
    h_val = pgSz.get(w('h'))
    if w_val != '11906' or h_val != '16838':
        return [f"Page size is {w_val}x{h_val} DXA (expected 11906x16838 for A4)"]
    return []


def extract_summary_amounts(tables, currency):
    """Extract subtotal, discount, VAT, and total from the service content table."""
    amounts = {}
    for tbl in tables:
        rows = tbl.findall(w('tr'))
        for row in rows:
            cells = row.findall(w('tc'))
            # 汇总行已改为合并单元格（label 跨列 + 金额跨列；泰国小计额外有空单元格）。
            # 取「第一个非空文本」为 label、「最后一个非空文本」为金额，兼容合并/未合并两种结构。
            texts = [
                ''.join((t.text or '') for t in cell.findall('.//' + w('t'))).strip()
                for cell in cells
            ]
            non_empty = [i for i, t in enumerate(texts) if t]
            if len(non_empty) < 2:
                continue
            label = texts[non_empty[0]]
            amount_text = texts[non_empty[-1]]
            parsed = parse_formatted_amount(amount_text, currency)

            label_lower = label
            if '小计' in label_lower and parsed is not None:
                amounts['subtotal'] = parsed
            elif '优惠金额' in label_lower and parsed is not None:
                amounts['discount'] = parsed
            elif '增值税' in label_lower and parsed is not None:
                amounts['vat'] = parsed
                rate_match = re.search(r'(\d+(?:\.\d+)?)%', label_lower)
                if rate_match:
                    amounts['vat_rate'] = float(rate_match.group(1)) / 100
            elif '预扣税' in label_lower and parsed is not None:
                amounts['withholding_tax'] = parsed
                rate_match = re.search(r'(\d+(?:\.\d+)?)%', label_lower)
                if rate_match:
                    amounts['withholding_tax_rate'] = float(rate_match.group(1)) / 100
            elif '含税总计' in label_lower and parsed is not None:
                amounts['total'] = parsed
    return amounts


def verify_amounts(amounts, currency):
    """Verify internal consistency of extracted amounts."""
    issues = []
    if not amounts:
        issues.append("Could not extract summary amounts from document")
        return issues

    subtotal = amounts.get('subtotal')
    discount = amounts.get('discount', 0)
    vat = amounts.get('vat')
    total = amounts.get('total')
    vat_rate = amounts.get('vat_rate')

    if subtotal is None:
        issues.append("Subtotal not found in document")
        return issues

    # Check VAT calculation: VAT = (subtotal - discount) * vat_rate
    if vat_rate is not None and vat is not None:
        expected_vat = (subtotal - discount) * vat_rate
        if currency_has_decimals(currency):
            expected_vat = round(expected_vat, 2)
        else:
            expected_vat = round(expected_vat)
        tolerance = 0.02 if currency_has_decimals(currency) else 2
        if abs(vat - expected_vat) > tolerance:
            issues.append(
                f"VAT mismatch: document={vat}, expected={expected_vat} "
                f"(rate={vat_rate*100}% x ({subtotal}-{discount}))")

    # Check withholding tax calculation
    wht = amounts.get('withholding_tax')
    wht_rate = amounts.get('withholding_tax_rate')
    if wht_rate is not None and wht is not None:
        expected_wht = (subtotal - discount) * wht_rate
        if currency_has_decimals(currency):
            expected_wht = round(expected_wht, 2)
        else:
            expected_wht = round(expected_wht)
        tolerance = 0.02 if currency_has_decimals(currency) else 2
        # WHT is displayed as negative in document, but stored as positive in parsed amount
        # Use absolute value for comparison
        if abs(abs(wht) - expected_wht) > tolerance:
            issues.append(
                f"Withholding tax mismatch: document={wht}, expected={expected_wht} "
                f"(rate={wht_rate*100}% x ({subtotal}-{discount}))")

    # Check total = discounted subtotal + VAT - |WHT|
    if total is not None and vat is not None:
        expected_total = (subtotal - discount) + vat
        if wht is not None:
            expected_total -= abs(wht)
        tolerance = 0.02 if currency_has_decimals(currency) else 2
        if abs(total - expected_total) > tolerance:
            issues.append(
                f"Total mismatch: document={total}, expected={expected_total} "
                f"(subtotal={subtotal} - discount={discount} + vat={vat} - wht={wht})")

    return issues


def load_data_for_verify(data_path):
    if not data_path:
        return None, []
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            return json.load(f), []
    except Exception as exc:
        return None, [f"Cannot read data file: {exc}"]


def cross_check_with_data(document_amounts, data_path, currency):
    """Cross-check document amounts with input data file."""
    issues = []
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        issues.append(f"Cannot read data file: {exc}")
        return issues

    expected_subtotal = 0
    for group in data.get('services', []):
        for item in group.get('items', []):
            price = item.get('price', 0)
            if isinstance(price, str):
                price = int(price.replace(',', ''))
            # Price field is already the total price (not unit price), so do NOT multiply by quantity
            expected_subtotal += price

    doc_subtotal = document_amounts.get('subtotal')
    if doc_subtotal is not None and expected_subtotal != doc_subtotal:
        issues.append(
            f"Subtotal mismatch: document={doc_subtotal} vs data={expected_subtotal}")

    expected_discount = data.get('discount_amount', 0)
    doc_discount = document_amounts.get('discount', 0)
    if doc_discount is not None and expected_discount != doc_discount:
        issues.append(
            f"Discount mismatch: document={doc_discount} vs data={expected_discount}")

    # Check withholding tax from data
    expected_wht = data.get('withholding_tax', False)
    doc_wht = document_amounts.get('withholding_tax')
    if expected_wht and doc_wht is None:
        issues.append("Withholding tax expected in data but not found in document")
    elif not expected_wht and doc_wht is not None:
        issues.append("Withholding tax found in document but not expected in data")

    # Check service name coverage in fee_details / process_data / doc_data
    doc_service_names = set()
    for group in data.get('services', []):
        for item in group.get('items', []):
            name = item.get('name', '')
            base_name = re.sub(r'\s*[x×]\d+$', '', name)
            doc_service_names.add(base_name)

    for key in ['fee_details', 'process_data', 'doc_data']:
        records = data.get(key, [])
        seen_names = set()
        for record in records:
            name = record.get('name', '')
            if name:
                seen_names.add(name)
        missing = sorted(doc_service_names - seen_names)
        extra = sorted(seen_names - doc_service_names)
        if missing:
            issues.append(f"{key} missing services: {', '.join(missing)}")
        if extra:
            issues.append(f"{key} contains unknown services: {', '.join(extra)}")

    return issues


def main():
    parser = argparse.ArgumentParser(description='Verify a generated quotation .docx')
    parser.add_argument('--input', required=True,
                        help='Path to the generated .docx file')
    parser.add_argument('--data', default=None,
                        help='Optional: input quotation data JSON for cross-checking')
    parser.add_argument('--entity', default=None,
                        choices=['jakarta', 'beijing', 'xian', 'shenzhen', 'shanghai', 'shanghai_new', 'singapore', 'deyin', 'thailand'],
                        help='Expected signing entity (for config-based checks')
    args = parser.parse_args()

    entity_config, _ = load_entity_config()

    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    data_path = None
    data_for_verify = None
    if args.data:
        data_path = os.path.abspath(args.data)
        if not os.path.exists(data_path):
            print(f"❌ Data file not found: {data_path}")
            sys.exit(1)
        data_for_verify, data_load_issues = load_data_for_verify(data_path)
        if data_load_issues:
            for issue in data_load_issues:
                print(f"❌ {issue}")
            sys.exit(1)

    unpack_dir = tempfile.mkdtemp(prefix='quotation-verify-')
    all_issues = []
    all_warnings = []

    try:
        with zipfile.ZipFile(input_path, 'r') as zf:
            zf.extractall(unpack_dir)

        # ── Parse document.xml ──
        doc_xml_path = os.path.join(unpack_dir, 'word', 'document.xml')
        if not os.path.exists(doc_xml_path):
            print("❌ word/document.xml not found in .docx")
            sys.exit(1)

        doc_tree = ET.parse(doc_xml_path)
        doc_root = doc_tree.getroot()
        doc_body = doc_root.find(w('body'))
        para_texts = extract_paragraph_texts(doc_root)
        tables = doc_body.findall('.//' + w('tbl')) if doc_body is not None else []
        currency = detect_currency_from_tables(tables) or detect_currency(para_texts)
        print(f"货币: {currency}")

        actual_entity = detect_entity(para_texts, entity_config)
        cli_entity = args.entity
        meta_entity = None
        expected_currency = None
        if data_for_verify:
            meta = data_for_verify.get('_meta') if isinstance(data_for_verify, dict) else {}
            if isinstance(meta, dict):
                meta_entity = meta.get('applicable_entity')
                expected_currency = meta.get('target_currency')

        detected_entity = cli_entity or actual_entity or meta_entity
        if detected_entity:
            print(f"签约主体: {detected_entity}")
        else:
            print("⚠️  无法自动识别签约主体，部分交叉验证将跳过")
            all_warnings.append("无法识别签约主体，跳过主体配置交叉验证")

        entity_checks = [
            ('命令行主体', cli_entity),
            ('数据_meta主体', meta_entity),
            ('文档签约主体', actual_entity),
        ]
        for i, (left_label, left_value) in enumerate(entity_checks):
            if not left_value:
                continue
            for right_label, right_value in entity_checks[i + 1:]:
                if not right_value:
                    continue
                if left_value != right_value:
                    print(f"❌ {left_label} '{left_value}' 与 {right_label} '{right_value}' 不一致")
                    all_issues.append(f"{left_label} '{left_value}' ≠ {right_label} '{right_value}'")
        if expected_currency and expected_currency != currency:
            print(f"❌ 文档币种 '{currency}' 与数据目标币种 '{expected_currency}' 不一致")
            all_issues.append(f"文档币种 '{currency}' ≠ 数据目标币种 '{expected_currency}'")

        # ── 1. Header vs bank info consistency ──
        header_xml_path = os.path.join(unpack_dir, 'word', 'header1.xml')
        header_company = None
        header_address = None

        if os.path.exists(header_xml_path):
            header_tree = ET.parse(header_xml_path)
            header_root = header_tree.getroot()
            header_company, header_address = extract_header_info(header_root)
            print(f"页眉公司名: {header_company}")
            print(f"页眉地址: {header_address}")
        else:
            print("⚠️  header1.xml 不存在，跳过页眉与银行信息一致性检查")
            all_warnings.append("header1.xml 不存在，无法核对页眉与银行信息")

        bank_lines = find_bank_info_section(para_texts)
        bank_company = extract_company_from_bank(bank_lines)
        bank_address = extract_address_from_bank(bank_lines)
        print(f"银行公司名: {bank_company}")
        print(f"银行地址: {bank_address}")

        # Bank account company is the authoritative signing-entity anchor.
        if detected_entity and bank_company:
            cfg_company = entity_config.get(detected_entity, {}).get('company')
            if cfg_company and normalize_company_name(bank_company) != normalize_company_name(cfg_company):
                print(f"❌ 银行公司名 '{bank_company}' 与配置 '{cfg_company}' 不一致")
                all_issues.append(f"银行公司名 '{bank_company}' ≠ 配置 '{cfg_company}' (entity={detected_entity})")
            elif cfg_company:
                print(f"✅ 银行公司名与配置一致 (entity={detected_entity})")

        # Check company name
        if header_company and bank_company:
            if (header_company == bank_company
                    or normalize_company_name(header_company) == normalize_company_name(bank_company)):
                print("✅ 页眉公司名与银行信息一致")
            elif header_company in bank_company or bank_company in header_company:
                print(f"⚠️  页眉公司名 '{header_company}' 与银行 '{bank_company}' 相似但非完全一致")
                all_warnings.append(
                    f"页眉公司名 '{header_company}' vs 银行 '{bank_company}' - 非完全一致")
            else:
                print(f"❌ 页眉公司名 '{header_company}' 与银行 '{bank_company}' 不一致")
                all_issues.append(
                    f"页眉公司名 '{header_company}' ≠ 银行 '{bank_company}'")

        # Check address (only when bank info actually contains a company address)
        if header_address and bank_address:
            match, msg = check_address_similarity(header_address, bank_address)
            if match:
                print(f"✅ 页眉地址与银行地址一致: {msg}")
            else:
                print(f"❌ 页眉地址与银行地址不一致: {msg}")
                all_issues.append(msg)

        # ── 2. Signature company vs bank company ──
        sig_company = extract_signature_company(tables, entity_config)
        if sig_company:
            print(f"签名公司名: {sig_company}")
            if bank_company:
                if (sig_company == bank_company
                        or normalize_company_name(sig_company) == normalize_company_name(bank_company)):
                    print("✅ 签名公司名与银行公司名一致")
                else:
                    print(f"❌ 签名公司名 '{sig_company}' 与银行 '{bank_company}' 不一致")
                    all_issues.append(f"签名公司名 '{sig_company}' ≠ 银行 '{bank_company}'")
            else:
                print("❌ 银行信息中未找到公司名，无法核对签名公司名")
                all_issues.append("银行信息中未找到公司名，无法核对签名公司名")
        else:
            print("❌ 未在签名区域找到签名公司名")
            all_issues.append("未在签名区域找到签名公司名")

        # ── 3. Service name coverage ──
        doc_service_names = extract_service_names_from_table(tables)
        if doc_service_names:
            print(f"文档中的服务名: {', '.join(doc_service_names)}")
            service_set = set(doc_service_names)

            # Extract names from the three structured locations:
            # - Fee details section (paragraphs between *备注 and 2.付款条件)
            # - Process & deliverables table (second table, column 1)
            # - Required documents table (third table, column 1)
            fee_names = set(extract_fee_section_names(para_texts))
            process_names = set(extract_names_from_table_column(tables, 1, 1)) if len(tables) >= 2 else set()
            doc_names = set(extract_names_from_table_column(tables, 2, 1)) if len(tables) >= 3 else set()

            for section_name, section_set in [
                ('费用明细', fee_names),
                ('流程及交付', process_names),
                ('所需材料', doc_names),
            ]:
                missing = sorted(service_set - section_set)
                extra = sorted(section_set - service_set)
                if missing:
                    print(f"❌ {section_name}缺少服务: {', '.join(missing)}")
                    all_issues.append(f"{section_name}缺少服务: {', '.join(missing)}")
                if extra:
                    print(f"⚠️  {section_name}有多余服务: {', '.join(extra)}")
                    all_warnings.append(f"{section_name}有多余服务: {', '.join(extra)}")
                if not missing and not extra:
                    print(f"✅ {section_name}服务覆盖完整")
        else:
            all_warnings.append("未从文档提取到服务名列表")

        # ── 4. Font check ──
        non_fangsong = check_fonts(doc_root)
        if non_fangsong:
            for font in non_fangsong:
                print(f"⚠️  非仿宋字体: {font}")
                all_warnings.append(f"非仿宋字体: {font}")
        else:
            print("✅ 字体检查: 全文仿宋")

        # ── 5. Page size check ──
        page_issues = check_page_size(doc_root)
        if page_issues:
            for pi in page_issues:
                print(f"❌ {pi}")
                all_issues.append(pi)
        else:
            print("✅ 页面尺寸: A4 (11906x16838 DXA)")

        # ── 6. Amount extraction & verification ──
        amounts = extract_summary_amounts(tables, currency)

        if amounts:
            print(f"金额: 小计={amounts.get('subtotal')} "
                  f"优惠={amounts.get('discount', 0)} "
                  f"增值税={amounts.get('vat')} "
                  f"含税总计={amounts.get('total')}")
            amount_issues = verify_amounts(amounts, currency)
            for ai in amount_issues:
                print(f"❌ {ai}")
                all_issues.append(ai)
            if not amount_issues:
                print("✅ 金额内部一致性验证通过")

            try:
                payment_terms = extract_payment_terms(input_path)
                payment_warnings = check_payment_terms_reasonableness(
                    payment_terms,
                    contract_total=amounts.get('total'),
                    currency=currency,
                )
                for warning in payment_warnings:
                    print(f"⚠️  {warning}")
                    all_warnings.append(warning)
            except Exception as exc:
                warning = f"未能检查付款方式合理性: {exc}"
                print(f"⚠️  {warning}")
                all_warnings.append(warning)

            # ── 7. Cross-check with input data ──
            if data_path:
                data_issues = cross_check_with_data(amounts, data_path, currency)
                for di in data_issues:
                    print(f"❌ {di}")
                    all_issues.append(di)
                if not data_issues:
                    print("✅ 文档金额与输入数据一致")
        else:
            all_warnings.append("未从文档提取到金额汇总行，跳过金额验证")
            print("⚠️  未提取到金额汇总行")

        # ── Summary ──
        print("\n" + "=" * 50)
        if all_issues:
            print(f"❌ 验证失败: {len(all_issues)} 个问题")
            for issue in all_issues:
                print(f"  - {issue}")
            sys.exit(1)
        elif all_warnings:
            print(f"⚠️  验证通过，有 {len(all_warnings)} 个警告:")
            for warning in all_warnings:
                print(f"  - {warning}")
        else:
            print("✅ 验证通过: 所有检查OK")

    finally:
        shutil.rmtree(unpack_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
