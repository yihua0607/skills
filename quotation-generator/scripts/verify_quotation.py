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
  3. Service codes are rendered consistently
  4. Font sanity (FangSong throughout)
  5. A4 page size (sectPr)
  6. Amount internal consistency (subtotal, discount, VAT, total)
  7. Cross-check with input data (if --data provided)
"""
import argparse, zipfile, os, sys, re, json, tempfile, shutil
from decimal import Decimal
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from scripts.sync_payment_terms import extract_payment_terms, check_payment_terms_reasonableness
from scripts.quotation_common import (
    load_entity_config_with_meta,
    calculate_amounts,
    currency_has_decimals,
    CURRENCY_NAME_TO_CODE,
    A4_PAGE_W,
    A4_PAGE_H,
    A4_MARGINS,
    is_process_time_disclaimer,
    header_image_spec,
    header_image_geometry,
    header_logo_spec,
    png_alpha_bottom_padding,
    text_column_mm,
    MIN_PRINTABLE_INK_TOP_MM,
    MIN_LOGO_LINE_GAP_MM,
    EMU_PER_MM,
)
from scripts.quotation_schema import validate_and_normalize_data

WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
A_NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
WPS = '{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}'


def w(tag):
    return f'{{{W}}}{tag}'


def normalize_company_name(name):
    """Normalize company name for comparison: treat '.' as a separator, collapse spaces.

    Banks record the same legal name with the dot punctuated differently
    ('PT. SHAN HAI MAP', 'PT.SHAN HAI MAP', 'PT SHAN HAI MAP'), and which one
    appears in a given account's records is not ours to choose - the account
    holder name printed on a quotation has to match the bank's own record or the
    transfer gets rejected. So all three spellings must compare equal.

    Deleting the dot is not enough: it glues the surrounding words together
    ('PT.SHAN' -> 'PTSHAN') while a dot followed by a space leaves them apart
    ('PT. SHAN' -> 'PT SHAN'). Replacing it with a space normalizes both.
    """
    if not name:
        return ''
    return re.sub(r'\s+', ' ', name.replace('.', ' ')).strip()


def _strip_diacritics(text):
    """Remove diacritical marks for fuzzy comparison (e.g. 'Ệ' → 'E')."""
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn')


def company_names_match(name1, name2):
    """Check if two company names refer to the same entity.

    Tries progressively looser comparisons:
    1. Dot-normalized (existing behavior)
    2. Case-insensitive
    3. Diacritic-insensitive (for Vietnamese etc.)
    """
    n1 = normalize_company_name(name1)
    n2 = normalize_company_name(name2)
    if n1 == n2:
        return True
    if n1.casefold() == n2.casefold():
        return True
    return _strip_diacritics(n1).casefold() == _strip_diacritics(n2).casefold()


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
        if '₫' in text:
            return 'VND'
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
                if '₫' in amount_text:
                    return 'VND'
                if re.search(r'\$\s*[\d,]', amount_text):
                    return 'USD'
    return None


def detect_currency_from_header(tables):
    """从价格列头「价格 (币种名)」识别文档币种（价格已不带货币符号）。"""
    name_to_code = CURRENCY_NAME_TO_CODE
    for tbl in tables:
        for row in tbl.findall(w('tr')):
            for cell in row.findall(w('tc')):
                txt = ''.join((t.text or '') for t in cell.findall('.//' + w('t')))
                if '价格' in txt:
                    for name, code in name_to_code.items():
                        if name in txt:
                            return code
    return None


def detect_entity(paragraph_texts, entity_config):
    """Detect signing entity from document text by matching bank lines."""
    bank_section = find_bank_info_section(paragraph_texts)
    if not bank_section:
        return None
    # Match by looking for unique bank info patterns. An entity quotes in several
    # currencies and may hold a different account for each (jakarta bills IDR from
    # BCA, USD from BNI, RMB from ICBC), so every currency's variant is a candidate
    # — matching only bank_lines would miss any document quoting a non-default one.
    for entity_key, cfg in entity_config.items():
        variants = [cfg.get('bank_lines', [])]
        variants += list(cfg.get('bank_lines_by_currency', {}).values())
        # Try matching on account number — most unique identifier
        for doc_line in bank_section:
            for bank_lines in variants:
                for cfg_line in bank_lines:
                    # Extract account number patterns (Chinese or English)
                    # Allow optional text between keyword and colon; capture digits/spaces (account number)
                    account_match = re.search(r'(账号|账户号码|银行账号|Account Number|Account No)\b[^：:]*[：:]\s*([\d ]+)', cfg_line)
                    if account_match:
                        account_num = account_match.group(2).replace(' ', '')
                        if account_num in doc_line.replace(' ', ''):
                            return entity_key
    # Fallback: match the account holder name against each entity's registered name.
    # Comparing the extracted holder (not the raw line) lets the dot-insensitive
    # comparison absorb bank spellings like 'PT.SHAN HAI MAP'.
    bank_company = extract_company_from_bank(bank_section)
    if bank_company:
        for entity_key, cfg in entity_config.items():
            if company_names_match(bank_company, cfg.get('company', '')):
                return entity_key
    # Last resort: the registered name appearing verbatim anywhere in the section
    for entity_key, cfg in entity_config.items():
        company = cfg.get('company', '')
        for doc_line in bank_section:
            if company and company in doc_line:
                return entity_key
    print("⚠️  WARNING: Could not auto-detect signing entity from bank info; entity-specific checks will be skipped.", file=sys.stderr)
    return None


def parse_formatted_amount(text, currency='RMB'):
    """Parse a formatted amount string into Decimal without float conversion."""
    text = text.strip()
    if currency == 'RMB':
        text = text.replace('￥', '').replace('¥', '')
    elif currency == 'USD':
        text = text.replace('$', '')
    elif currency == 'THB':
        text = text.replace('฿', '')
    elif currency == 'SGD':
        text = text.replace('S$', '')
    elif currency == 'VND':
        text = text.replace('₫', '')
    else:
        text = re.sub(r'^Rp\s*', '', text, flags=re.I)
    text = text.replace(',', '').replace(' ', '')
    try:
        return Decimal(text)
    except Exception:
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
        # Allow optional text (e.g. Vietnamese/Indonesian labels) between keyword and colon
        match = re.search(
            r'(账户名称|账号名称|开户名|户名|Beneficiary Name|Account Name|Atas Nama)\b[^：:]*[：:]\s*(.+)', line)
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
        # Allow optional text (e.g. Vietnamese/Indonesian labels) between keyword and colon
        match = re.search(r'(地址|Address)\b[^：:]*[：:]\s*(.+)', line, flags=re.I)
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


def extract_service_names_from_table(tables, tax_label='增值税'):
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
            summary_prefixes = ('服务内容', '小计', '优惠金额', tax_label, '含税总计', '预扣税')
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
        if base_name and base_name not in ('项目', '服务内容'):
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
    return False, f"Addresses differ: '{addr1}' vs '{addr2}'"


def configured_header_addresses(entity_cfg):
    """The address lines an entity's header is expected to carry.

    `header_lines` is ordered [company name, address line(s)..., 'Web: ...'];
    the company name and the Web line are not addresses.
    """
    lines = [ln.strip() for ln in (entity_cfg or {}).get('header_lines') or []
             if ln and ln.strip()]
    return [ln for ln in lines[1:] if not ln.startswith('Web:')]


def check_header_image(header_root, spec, png_path):
    """图片页眉自查，返回 (issues, warnings)。

    页眉换成整幅位图后，公司名和地址都印在图里、XML 里没有对应文字可核对——这正是
    换图的目的（图改不动，也就不会跟银行信息对不上）。所以这里不再核对内容，改核
    「版式有没有被改坏」：横幅在不在、锚点参数是否仍等于 entities.json 算出来的值、
    有没有按 alpha 边界裁掉透明边距、图片框有没有越出纸张或正文栏、蓝线还在不在、
    图墨顶有没有掉进打印机不可打印区。
    """
    issues = []
    geom = header_image_geometry(spec, png_path)

    banner_anchor = None
    has_line = False
    for drawing in header_root.iter(w('drawing')):
        if drawing.find('.//' + WPS + 'wsp') is not None:
            has_line = True
        anchor = drawing.find(WP + 'anchor')
        if anchor is not None and drawing.find('.//' + A_NS + 'blip') is not None:
            banner_anchor = anchor

    if banner_anchor is None:
        issues.append(f"页眉里找不到横幅图（entity 配了 header_image: {spec['file']}，"
                      f"但 header1.xml 中没有锚定的图片）")
        return issues, []
    if not has_line:
        issues.append("页眉里找不到蓝色分隔线")

    extent = banner_anchor.find(WP + 'extent')
    pos_h = banner_anchor.find(WP + 'positionH/' + WP + 'posOffset')
    pos_v = banner_anchor.find(WP + 'positionV/' + WP + 'posOffset')
    if extent is None or pos_h is None or pos_v is None:
        issues.append("横幅图锚点结构不完整（缺 extent / positionH / positionV）")
        return issues, []

    for label, actual, expected in (
        ('水平偏移 positionH', pos_h.text, str(geom['banner_off_h'])),
        ('垂直偏移 positionV', pos_v.text, str(geom['banner_off_v'])),
        ('宽度 extent cx', extent.get('cx'), str(geom['extent_cx'])),
        ('高度 extent cy', extent.get('cy'), str(geom['extent_cy'])),
    ):
        if actual != expected:
            issues.append(f"横幅图{label} = {actual}，按 entities.json 应为 {expected}"
                          f"（页眉版式与配置不符）")

    # 横幅按 alpha 边界裁掉透明边距后，图片框 == 图墨迹，所以「框」和「墨迹」是同一个
    # 矩形：框越界就是墨迹越界，不再有「框顶伸到纸外但全透明所以没关系」的余地。
    src_rect = banner_anchor.find('.//' + A_NS + 'srcRect')
    if src_rect is None:
        issues.append("横幅图没有按 alpha 边界裁剪（缺 a:srcRect）——透明边距会撑大图片框，"
                      "框顶顶到纸张上缘、并越过正文栏")
    else:
        for side, expected in geom['src_rect'].items():
            actual = src_rect.get(side)
            if actual != str(expected):
                issues.append(f"横幅图裁剪 srcRect {side} = {actual}，按 entities.json 的 "
                              f"bbox_px 应为 {expected}（页眉版式与配置不符）")

    if spec['ink_top_mm'] < MIN_PRINTABLE_INK_TOP_MM:
        issues.append(f"页眉图墨迹顶距纸张上缘 {spec['ink_top_mm']}mm < "
                      f"{MIN_PRINTABLE_INK_TOP_MM}mm，多数打印机不可打印区约 4.2mm，"
                      f"logo 顶部会被裁掉")

    # 图片框已经裁到墨迹边界，所以框本身就是墨迹：横向必须落在正文栏内，纵向不许压到
    # 正文首行。（顶边由上面的 MIN_PRINTABLE 检查兜住，它比纸张上缘更严。）
    col_left_mm, col_w_mm = text_column_mm()
    col_right_mm = col_left_mm + col_w_mm
    box_right_mm = geom['ink_left_mm'] + geom['ink_w_mm']
    tol = 0.1   # mm，容忍 entities.json 里 mm 取值的舍入
    if geom['ink_left_mm'] < col_left_mm - tol:
        issues.append(f"页眉横幅图片框左缘 {geom['ink_left_mm']:.2f}mm 越出正文栏左边界 "
                      f"{col_left_mm:.2f}mm")
    if box_right_mm > col_right_mm + tol:
        issues.append(f"页眉横幅图片框右缘 {box_right_mm:.2f}mm 越出正文栏右边界 "
                      f"{col_right_mm:.2f}mm")
    if geom['ink_bottom_mm'] > spec['body_top_mm'] + tol:
        issues.append(f"页眉横幅图片框底缘 {geom['ink_bottom_mm']:.2f}mm 压过正文首行 "
                      f"{spec['body_top_mm']:.2f}mm")
    return issues, []


def _header_logo_anchors(header_root):
    """返回页眉里的 (logo 锚点, 蓝线锚点)，取不到的那个为 None。"""
    logo = line = None
    for anchor in header_root.iter(WP + 'anchor'):
        if anchor.find('.//' + A_NS + 'blip') is not None:
            logo = anchor
        else:
            line = anchor
    return logo, line


def _header_logo_png(word_dir, logo_anchor):
    """按 header1.xml.rels 把 logo 锚点引用的位图读出来；找不到返回 None。"""
    R_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    blip = logo_anchor.find('.//' + A_NS + 'blip')
    rels_path = os.path.join(word_dir, '_rels', 'header1.xml.rels')
    if blip is None or not os.path.exists(rels_path):
        return None
    rid = blip.get(R_NS + 'embed')
    if not rid:
        return None
    with open(rels_path, encoding='utf-8') as fh:
        match = re.search(r'Id="%s"[^>]*Target="([^"]*)"' % re.escape(rid), fh.read())
    if not match:
        return None
    target = os.path.join(word_dir, match.group(1).replace('../', ''))
    if not os.path.isfile(target):
        return None
    with open(target, 'rb') as fh:
        return fh.read()


def check_header_logo(header_root, spec, word_dir, template_anchor=None):
    """文字页眉的 logo 垂直位置自查，返回 (issues, warnings)。

    与横幅自查同理，核的是「版式有没有被改坏」而不是内容：logo 锚点在不在、下移量是否
    等于 entities.json 里算出来的值、蓝线有没有被动过、logo 墨迹底边与蓝线之间是否还留
    着净空。净空按 PNG 的 alpha 边界算——位图自带的透明留白不参与视觉。

    ``template_anchor`` 传模板 header1.xml 里 logo 锚点的原始 posOffset，用来核对下移量
    确实是「模板原位 + 配置值」；拿不到模板时跳过这一项，其余检查照做。
    """
    issues, warnings = [], []
    shift_emu = int(round(spec['shift_mm'] * EMU_PER_MM))

    logo, line = _header_logo_anchors(header_root)
    if logo is None:
        issues.append("文字页眉里找不到 logo 锚点（含 a:blip 的 wp:anchor）")
        return issues, warnings
    if line is None:
        issues.append("文字页眉里找不到蓝色分隔线")
        return issues, warnings

    pos_v = logo.find(WP + 'positionV/' + WP + 'posOffset')
    extent = logo.find(WP + 'extent')
    line_v = line.find(WP + 'positionV/' + WP + 'posOffset')
    if pos_v is None or extent is None or line_v is None:
        issues.append("logo/蓝线锚点结构不完整（缺 posOffset 或 extent）")
        return issues, warnings

    # logo 与蓝线必须锚在同一段：两个偏移量都从该段顶端起算，间距才是模板常量。
    if template_anchor is not None and int(pos_v.text) != template_anchor + shift_emu:
        issues.append(f"页眉 logo 垂直偏移 = {pos_v.text}，按模板原位 {template_anchor} + "
                      f"配置下移 {spec['shift_mm']}mm 应为 {template_anchor + shift_emu}"
                      f"（页眉版式与配置不符）")

    # 墨迹可见高度 = wp:extent 减掉位图底部的透明留白。
    box_emu = int(extent.get('cy'))
    ink_emu = box_emu
    png = _header_logo_png(word_dir, logo)
    if png:
        try:
            ink_emu = int(round(box_emu * (1 - png_alpha_bottom_padding(png))))
        except ValueError as exc:
            warnings.append(f"无法按 alpha 边界量 logo 可见高度（{exc}），净空按图片框估算")

    clearance_mm = (int(line_v.text) - (int(pos_v.text) + ink_emu)) / EMU_PER_MM
    if clearance_mm <= 0:
        issues.append(f"页眉 logo 墨迹底边越过蓝线 {abs(clearance_mm):.2f}mm —— 蓝线压在 logo 上")
    elif clearance_mm < MIN_LOGO_LINE_GAP_MM:
        warnings.append(f"页眉 logo 底边距蓝线仅 {clearance_mm:.2f}mm "
                        f"(< {MIN_LOGO_LINE_GAP_MM}mm)，两者视觉上会粘连")

    return issues, warnings


def check_fonts(document_root):
    """Check that fonts are consistently FangSong."""
    non_fangsong = set()
    for rpr in document_root.findall('.//' + w('rPr')):
        rfonts = rpr.find(w('rFonts'))
        if rfonts is not None:
            for attr_key in ['ascii', 'hAnsi', 'eastAsia', 'cs']:
                val = rfonts.get(w(attr_key))
                if val and val not in ('FangSong', '仿宋', 'Times New Roman'):
                    non_fangsong.add(val)
    return sorted(non_fangsong)


def check_page_size(document_root):
    """Check A4 portrait page size and canonical margins in sectPr.

    build normalizes both, so a mismatch here means the output did not come from
    the normal build path (or was edited afterwards) and may not print correctly
    on A4.
    """
    body = document_root.find(w('body'))
    if body is None:
        return ["No <w:body> element found"]
    sectPr = body.find(w('sectPr'))
    if sectPr is None:
        return ["No sectPr found - page size undefined"]
    pgSz = sectPr.find(w('pgSz'))
    if pgSz is None:
        return ["No pgSz in sectPr"]

    issues = []
    w_val = pgSz.get(w('w'))
    h_val = pgSz.get(w('h'))
    if w_val != str(A4_PAGE_W) or h_val != str(A4_PAGE_H):
        issues.append(
            f"Page size is {w_val}x{h_val} DXA "
            f"(expected {A4_PAGE_W}x{A4_PAGE_H} for A4)")
    if pgSz.get(w('orient')) == 'landscape':
        issues.append("Page orientation is landscape (expected portrait for A4)")

    pgMar = sectPr.find(w('pgMar'))
    if pgMar is None:
        issues.append("No pgMar in sectPr - margins undefined, not print-safe")
    else:
        for name, expected in A4_MARGINS.items():
            got = pgMar.get(w(name))
            if got is not None and got != str(expected):
                issues.append(
                    f"Page margin '{name}' is {got} DXA (expected {expected})")
    return issues


def check_process_time_disclaimer(paragraph_texts):
    """备注中不得出现办理时间免责声明。

    「服务内容」表没有办理时间列，表下 *备注： 区块里的「上述办理时间……」指不到任何
    上文；build 会剔除，所以这里命中说明成稿绕过了正常构建路径。
    """
    return [
        f"备注中出现办理时间免责声明（服务内容表无办理时间列）: {text}"
        for _, text in paragraph_texts
        if is_process_time_disclaimer(text)
    ]


def extract_summary_amounts(tables, currency, tax_label='增值税'):
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
            elif tax_label in label_lower and parsed is not None:
                amounts['vat'] = parsed
                rate_match = re.search(r'(\d+(?:\.\d+)?)%', label_lower)
                if rate_match:
                    amounts['vat_rate'] = Decimal(rate_match.group(1)) / Decimal('100')
            elif '预扣税' in label_lower and parsed is not None:
                amounts['withholding_tax'] = parsed
                rate_match = re.search(r'(\d+(?:\.\d+)?)%', label_lower)
                if rate_match:
                    amounts['withholding_tax_rate'] = Decimal(rate_match.group(1)) / Decimal('100')
            elif '含税总计' in label_lower and parsed is not None:
                amounts['total'] = parsed
    return amounts


def verify_amounts(amounts, currency):
    """Verify internal consistency of extracted amounts."""
    issues = []
    if not amounts:
        issues.append("Could not extract summary amounts from document")
        return issues

    required = {
        'subtotal': 'Subtotal not found in document',
        'vat': 'Tax amount not found in document',
        'vat_rate': 'Tax rate not found in document',
        'total': 'Total not found in document',
    }
    for field, message in required.items():
        if amounts.get(field) is None:
            issues.append(message)

    wht = amounts.get('withholding_tax')
    wht_rate = amounts.get('withholding_tax_rate')
    if (wht is None) != (wht_rate is None):
        issues.append('Withholding tax amount and rate must both be present or both be absent')
    if issues:
        return issues

    subtotal = Decimal(str(amounts['subtotal']))
    discount = amounts.get('discount', 0)
    discount = Decimal(str(discount))
    vat = Decimal(str(amounts['vat']))
    total = Decimal(str(amounts['total']))
    vat_rate = Decimal(str(amounts['vat_rate']))
    wht = Decimal(str(wht)) if wht is not None else None
    wht_rate = Decimal(str(wht_rate)) if wht_rate is not None else None

    # Reuse the build/validate Decimal + ROUND_HALF_UP calculation.
    expected = calculate_amounts(
        subtotal, discount, vat_rate, currency,
        withholding_tax_rate=wht_rate,
    )

    # Check VAT calculation: VAT = (subtotal - discount) * vat_rate
    expected_vat = Decimal(str(expected['vat']))
    tolerance = Decimal('0.001') if currency_has_decimals(currency) else Decimal('0.1')
    if abs(vat - expected_vat) > tolerance:
        issues.append(
            f"VAT mismatch: document={vat}, expected={expected_vat} "
            f"(rate={vat_rate*100}% x ({subtotal}-{discount}))")

    # Check withholding tax calculation
    if wht_rate is not None and wht is not None:
        expected_wht = expected['withholding_tax']
        tolerance = Decimal('0.001') if currency_has_decimals(currency) else Decimal('0.1')
        # WHT is displayed as negative in document, but stored as positive in parsed amount
        # Use absolute value for comparison
        if abs(abs(wht) - Decimal(str(expected_wht))) > tolerance:
            issues.append(
                f"Withholding tax mismatch: document={wht}, expected={expected_wht} "
                f"(rate={wht_rate*100}% x ({subtotal}-{discount}))")

    # Check total = discounted subtotal + VAT - |WHT|
    expected_total = Decimal(str(expected['total']))
    tolerance = Decimal('0.001') if currency_has_decimals(currency) else Decimal('0.1')
    if abs(total - expected_total) > tolerance:
        issues.append(
            f"Total mismatch: document={total}, expected={expected_total} "
            f"(subtotal={subtotal} - discount={discount} + vat={vat} - wht={wht})")

    return issues


def check_service_codes(doc_service_names, data_for_verify):
    """服务内容列必须以「编码-服务名」渲染，缺编码即报错。

    用数据文件里的原始服务名对照成稿，防止编码在渲染中丢失。
    """
    if not data_for_verify or not doc_service_names:
        return []
    bare_names = set()
    for service in data_for_verify.get('services', []):
        name = (service.get('name') or '').strip()
        if name:
            bare_names.add(name)
    return [
        f"服务内容缺少服务编码: {doc_name}"
        f"（数据文件中该服务未填 code，成稿应渲染为「编码-{doc_name}」）"
        for doc_name in doc_service_names
        if doc_name in bare_names
    ]


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
            raw_data = json.load(f)
        data = validate_and_normalize_data(raw_data, data_path=data_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"Input data validation failed: {exc}")
        return issues

    expected_subtotal = sum(
        (Decimal(service['price_int']) for service in data['services']), Decimal('0')
    )

    doc_subtotal = document_amounts.get('subtotal')
    if doc_subtotal is not None and expected_subtotal != doc_subtotal:
        issues.append(
            f"Subtotal mismatch: document={doc_subtotal} vs data={expected_subtotal}")

    expected_discount = Decimal(data['discount_amount'])
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

    return issues


def main():
    entity_config, _, entity_meta = load_entity_config_with_meta()

    parser = argparse.ArgumentParser(description='Verify a generated quotation .docx')
    parser.add_argument('--input', required=True,
                        help='Path to the generated .docx file')
    parser.add_argument('--data', default=None,
                        help='Optional: input quotation data JSON for cross-checking')
    parser.add_argument('--entity', default=None,
                        choices=list(entity_config.keys()),
                        help='Expected signing entity (for config-based checks)')
    args = parser.parse_args()

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
        actual_entity = detect_entity(para_texts, entity_config)
        cli_entity = args.entity
        meta_entity = None
        expected_currency = None
        if data_for_verify:
            meta = data_for_verify.get('_meta') if isinstance(data_for_verify, dict) else {}
            if isinstance(meta, dict):
                meta_entity = meta.get('applicable_entity')
                expected_currency = meta.get('target_currency')

        # 文档币种从价格列头「价格 (币种名)」识别（价格已不带货币符号），旧版带符号文档回退符号检测。
        doc_currency = detect_currency_from_header(tables) or detect_currency_from_tables(tables) or detect_currency(para_texts)

        # 金额解析所用币种以权威来源为准：_meta.target_currency（--data）> --entity 默认币种 > 文档识别。
        if expected_currency:
            currency = expected_currency
        elif args.entity and args.entity in entity_config:
            currency = entity_config[args.entity].get('currency', 'RMB')
        else:
            currency = doc_currency or 'RMB'
        print(f"货币: {currency}")

        detected_entity = cli_entity or actual_entity or meta_entity
        if detected_entity:
            print(f"签约主体: {detected_entity}")
        else:
            print("⚠️  无法自动识别签约主体，部分交叉验证将跳过")
            all_warnings.append("无法识别签约主体，跳过主体配置交叉验证")

        # 税金行标签（马来西亚用「销售与服务税」SST，其余「增值税」）
        tax_label = '增值税'
        if detected_entity and detected_entity in entity_config:
            tax_label = entity_config[detected_entity].get('tax_label', '增值税')

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
        if expected_currency and doc_currency and doc_currency != expected_currency:
            print(f"❌ 文档币种 '{doc_currency}' 与数据目标币种 '{expected_currency}' 不一致")
            all_issues.append(f"文档币种 '{doc_currency}' ≠ 数据目标币种 '{expected_currency}'")

        # ── 1. Header vs bank info consistency ──
        header_xml_path = os.path.join(unpack_dir, 'word', 'header1.xml')
        header_company = None
        header_address = None

        # 配了 header_image 的主体，页眉是一整幅位图：公司名和地址都印在图里，XML 里
        # 没有对应文字，人工也改不动。所以「页眉公司名 vs 银行」「页眉地址归属」这两项
        # 对它天然不适用，显式跳过并说明，而不是等 extract_header_info 取不到文字后
        # 静默不检查——那样页眉万一被改回文字、或横幅整个丢了，都不会有人发现。
        img_spec = header_image_spec(entity_config.get(detected_entity or '', {}),
                                     entity_meta.get('header_image_defaults'))
        if img_spec:
            print(f"页眉形式: 横幅图 {img_spec['file']}")
            print("ℹ️  页眉公司名/地址均在图内、XML 中无可核对文字，"
                  "跳过「页眉公司名 vs 银行」与「页眉地址归属」检查")
            if not os.path.exists(header_xml_path):
                print("❌ header1.xml 不存在，图片页眉没有生效")
                all_issues.append("header1.xml 不存在，图片页眉没有生效")
            else:
                img_path = os.path.join(SKILL_DIR, 'assets', img_spec['file'])
                if not os.path.exists(img_path):
                    print(f"❌ 页眉图片不存在：{img_path}")
                    all_issues.append(
                        f"页眉图片不存在：{img_path}（检查 config/entities.json 的 "
                        f"header_image.file）")
                else:
                    img_issues, _ = check_header_image(
                        ET.parse(header_xml_path).getroot(), img_spec, img_path)
                    for issue in img_issues:
                        print(f"❌ {issue}")
                        all_issues.append(issue)
                    if not img_issues:
                        geom = header_image_geometry(img_spec, img_path)
                        print(f"✅ 页眉横幅图版式与配置一致，且未越界"
                              f"（图片框 {geom['ink_left_mm']:.1f}~"
                              f"{geom['ink_left_mm'] + geom['ink_w_mm']:.1f}mm × "
                              f"{img_spec['ink_top_mm']:.1f}~{geom['ink_bottom_mm']:.1f}mm / "
                              f"蓝线 {geom['line_mm']:.1f}mm / "
                              f"正文首行 {img_spec['body_top_mm']}mm）")
        elif os.path.exists(header_xml_path):
            header_tree = ET.parse(header_xml_path)
            header_root = header_tree.getroot()
            header_company, header_address = extract_header_info(header_root)
            print(f"页眉公司名: {header_company}")
            print(f"页眉地址: {header_address}")

            logo_spec = header_logo_spec(entity_config.get(detected_entity or '', {}),
                                         entity_meta.get('header_logo_defaults'))
            if logo_spec:
                # 模板里 logo 的原始偏移是「下移量」的基准，从模板现读，不从配置反推——
                # 配置只说移动了多少，改成什么样由模板原始版式决定。
                template_anchor = None
                tpl_rel = entity_config.get(detected_entity or '', {}).get('template_file')
                if tpl_rel:
                    tpl_path = os.path.join(SKILL_DIR, tpl_rel)
                    if os.path.isfile(tpl_path):
                        try:
                            with zipfile.ZipFile(tpl_path) as zf:
                                tpl_root = ET.fromstring(zf.read('word/header1.xml'))
                            tpl_logo, _ = _header_logo_anchors(tpl_root)
                            if tpl_logo is not None:
                                tpl_v = tpl_logo.find(WP + 'positionV/' + WP + 'posOffset')
                                if tpl_v is not None:
                                    template_anchor = int(tpl_v.text)
                        except (KeyError, ET.ParseError, ValueError):
                            template_anchor = None
                if template_anchor is None:
                    all_warnings.append("读不到模板里 logo 的原始偏移，"
                                        "跳过「下移量 = 模板原位 + 配置值」核对")

                logo_issues, logo_warnings = check_header_logo(
                    header_root, logo_spec,
                    os.path.join(unpack_dir, 'word'), template_anchor)
                for issue in logo_issues:
                    print(f"❌ {issue}")
                    all_issues.append(issue)
                for warning in logo_warnings:
                    print(f"⚠️  {warning}")
                    all_warnings.append(warning)
                if not logo_issues:
                    print(f"✅ 页眉 logo 版式与配置一致（模板原位下移 {logo_spec['shift_mm']}mm）")
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
            if cfg_company and not company_names_match(bank_company, cfg_company):
                print(f"❌ 银行公司名 '{bank_company}' 与配置 '{cfg_company}' 不一致")
                all_issues.append(f"银行公司名 '{bank_company}' ≠ 配置 '{cfg_company}' (entity={detected_entity})")
            elif cfg_company:
                print(f"✅ 银行公司名与配置一致 (entity={detected_entity})")

        # Check company name
        if header_company and bank_company:
            if company_names_match(header_company, bank_company):
                print("✅ 页眉公司名与银行信息一致")
            elif header_company in bank_company or bank_company in header_company:
                print(f"⚠️  页眉公司名 '{header_company}' 与银行 '{bank_company}' 相似但非完全一致")
                all_warnings.append(
                    f"页眉公司名 '{header_company}' vs 银行 '{bank_company}' - 非完全一致")
            else:
                print(f"❌ 页眉公司名 '{header_company}' 与银行 '{bank_company}' 不一致")
                all_issues.append(
                    f"页眉公司名 '{header_company}' ≠ 银行 '{bank_company}'")

        # The header carries the entity's registered office; the bank section carries
        # the account-holding branch. Those legitimately differ - Vietnam is registered
        # in Ho Chi Minh City but banks in Hanoi - so equality between the two is NOT
        # required. What must hold is that the header address is the one configured for
        # the entity we detected, which is what ties the header to the right entity.
        if header_address:
            configured = configured_header_addresses(entity_config.get(detected_entity or '', {}))
            if not configured:
                print("⚠️  配置中没有可核对的页眉地址，跳过页眉地址归属检查")
                all_warnings.append(
                    f"配置中无可核对的页眉地址，无法核对页眉地址归属 (entity={detected_entity})")
            else:
                match = next(
                    (msg for ok, msg in
                     (check_address_similarity(header_address, addr) for addr in configured)
                     if ok), None)
                if match:
                    print(f"✅ 页眉地址与本主体配置一致 (entity={detected_entity}): {match}")
                else:
                    print(f"❌ 页眉地址 '{header_address}' 不属于本主体配置地址 {configured}")
                    all_issues.append(
                        f"页眉地址 '{header_address}' ∉ 配置地址 {configured} "
                        f"(entity={detected_entity})")

            if bank_address and bank_address != header_address:
                print("ℹ️  页眉地址与开户行地址不同（注册地与开户行可不同城），不作为问题")

        # ── 2. Signature company vs bank company ──
        sig_company = extract_signature_company(tables, entity_config)
        if sig_company:
            print(f"签名公司名: {sig_company}")
            if bank_company:
                if company_names_match(sig_company, bank_company):
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
        doc_service_names = extract_service_names_from_table(tables, tax_label)
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

            code_issues = check_service_codes(doc_service_names, data_for_verify)
            if code_issues:
                for ci in code_issues:
                    print(f"❌ {ci}")
                    all_issues.append(ci)
            elif data_for_verify:
                print("✅ 服务编码检查: 服务内容均带服务编码")
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

        # ── 5. A4 print-safety check (page size + orientation + margins) ──
        page_issues = check_page_size(doc_root)
        if page_issues:
            for pi in page_issues:
                print(f"❌ {pi}")
                all_issues.append(pi)
        else:
            print(f"✅ 页面尺寸: A4 纵向 ({A4_PAGE_W}x{A4_PAGE_H} DXA)，"
                  f"页边距符合标准，可直接 A4 打印")

        # ── 6. 备注不得出现办理时间免责声明（服务内容表没有办理时间列）──
        disclaimer_issues = check_process_time_disclaimer(para_texts)
        if disclaimer_issues:
            for di in disclaimer_issues:
                print(f"❌ {di}")
                all_issues.append(di)
        else:
            print("✅ 备注检查: 无办理时间免责声明")

        # ── 7. Amount extraction & verification ──
        amounts = extract_summary_amounts(tables, currency, tax_label)

        if amounts:
            print(f"金额: 小计={amounts.get('subtotal')} "
                  f"优惠={amounts.get('discount', 0)} "
                  f"{tax_label}={amounts.get('vat')} "
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

            # ── 8. Cross-check with input data ──
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

    except zipfile.BadZipFile:
        print(f"❌ 无法打开 .docx（不是有效的 zip 包）: {input_path}")
        print("   常见原因：文件传输被截断，或该路径下的内容其实是 HTML 报错页而非 docx。")
        print("   处理建议：确认 build 已完整写出文件后重新生成，再跑 verify。")
        sys.exit(1)
    except ET.ParseError as exc:
        print(f"❌ word/document.xml 不是合法 XML: {exc}")
        print("   这通常是输入数据含有 XML 非法字符（NUL、ESC、BEL 等控制符）导致的产物损坏，")
        print("   而不是 SKILL 本身的问题——请检查 quotation.json 与实体配置中的文本字段，")
        print("   剔除控制字符后重新 build。")
        sys.exit(1)
    finally:
        shutil.rmtree(unpack_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
