"""
Build quotation by editing the template's XML directly.
This ensures 100% format consistency with the original template.

Usage:
  python3 scripts/build_quotation.py --entity xian --data quotation.json --output /path/to/output.docx
  python3 scripts/build_quotation.py --entity jakarta --data quotation.json --output /path/to/output.docx

The script reads quotation data from JSON, edits the bundled template XML, and outputs a .docx.
Entity configuration is loaded from config/entities.json — no business data is hardcoded in this script.
"""
import zipfile, os, sys, argparse, tempfile, shutil, json
from datetime import date
from xml.etree import ElementTree as ET

# Ensure imports work when this script is run directly as `python3 scripts/build_quotation.py`.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from scripts.quotation_common import (
    calculate_amounts,
    format_price_int,
    format_price_vat,
    format_price_total,
    format_price_display,
    vat_percent_label,
    load_entity_config,
    CURRENCY_NAMES,
)
from scripts.sync_payment_terms import extract_payment_terms, check_payment_terms_reasonableness

# Skill root directory (where SKILL.md lives)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Template paths
TEMPLATES = {
    'china': os.path.join(SKILL_DIR, 'assets', '报价单模板-中国公司.docx'),
    'jakarta': os.path.join(SKILL_DIR, 'assets', '报价单模板-雅加达公司.docx'),
    'singapore': os.path.join(SKILL_DIR, 'assets', '报价单模版-新加坡公司.docx'),
    'deyin': os.path.join(SKILL_DIR, 'assets', '报价单模版-德音人力.docx'),
    'thailand': os.path.join(SKILL_DIR, 'assets', '报价单模板-泰国公司.docx'),
    'vietnam': os.path.join(SKILL_DIR, 'assets', '报价单模版-越南公司.docx'),
}

ENTITY_CONFIG, _ = load_entity_config()


def load_quotation_data(path):
    """Load quotation data from JSON."""
    if not path:
        raise ValueError('Missing --data. Provide a JSON quotation data file.')

    data_path = os.path.abspath(path)
    if not os.path.exists(data_path):
        raise ValueError(f'Data file not found: {data_path}')

    ext = os.path.splitext(data_path)[1].lower()
    if ext != '.json':
        raise ValueError('Unsupported data file type. Use .json.')
    with open(data_path, 'r', encoding='utf-8') as f:
        return json.load(f)


from scripts.quotation_schema import validate_and_normalize_data

def main():

    # Parse CLI args — entity is now always required (including jakarta)
    parser = argparse.ArgumentParser(description='Generate quotation from template')
    parser.add_argument('--entity', required=True,
                        choices=list(ENTITY_CONFIG.keys()),
                        help='Signing entity (required): jakarta/beijing/xian/shenzhen/shanghai/shanghai_new/singapore/deyin/thailand/vietnam')
    parser.add_argument('--output', default=None, help='Output .docx path (default: CWD)')
    parser.add_argument('--data', required=True, help='Quotation data file (.json)')
    parser.add_argument('--vat-rate', type=float, default=None, help='VAT rate override (e.g. 0.06, 0.01, 0.11)')
    parser.add_argument('--title-line1', default=None, help='Title first line (default: quote_meta.title_line1 or 印尼投资)')
    parser.add_argument('--title-line2', default=None, help='Title second line (default: quote_meta.title_line2 or 综合服务方案)')
    parser.add_argument('--quote-date', default=None, help='Quote date override (default: today, format: YYYY-MM-DD)')
    parser.add_argument('--preserve-payment-from', default=None,
                        help='Existing .docx whose visible payment terms should be preserved for this rebuild')
    parser.add_argument('--overwrite-payment-terms', action='store_true',
                        help='Use quotation.json/entity payment terms even when rebuilding over an existing .docx')
    args = parser.parse_args()

    entity = args.entity
    entity_cfg = ENTITY_CONFIG[entity]
    template_key = entity_cfg['template']

    try:
        raw_quotation = load_quotation_data(args.data)
        quotation_data = validate_and_normalize_data(raw_quotation)
    except ValueError as exc:
        print(f'❌ {exc}', file=sys.stderr)
        sys.exit(2)
    for warning in quotation_data['warnings']:
        print(f'⚠️  WARNING: {warning}')

    TEMPLATE = TEMPLATES[template_key]
    if args.output:
        OUTPUT = os.path.abspath(args.output)
    else:
        name = f'报价单-{entity_cfg["company"]}'
        OUTPUT = os.path.join(os.getcwd(), f'{name}.docx')
    UNPACK = tempfile.mkdtemp(prefix='quotation-build-')

    NS = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
    }

    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    # Register namespaces for clean output
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)

    # ====== XML BUILDING HELPERS ======
    def w(tag):
        return f'{{{W}}}{tag}'

    def paragraph_text(paragraph):
        return ''.join((t.text or '') for t in paragraph.findall('.//' + w('t')))

    def replace_paragraph_text(paragraph, value):
        """Replace text runs in a paragraph while preserving drawing/image runs.
        Finds the first text run's rPr and uses it for the new text content.
        Falls back to make_run(value, sz='24') if no text run rPr found."""
        pPr = paragraph.find(w('pPr'))
        # Find the first TEXT run's rPr (w:r with w:t but no w:drawing)
        orig_rpr = None
        for run in paragraph.findall(w('r')):
            if run.find(w('t')) is not None and run.find(w('drawing')) is None:
                orig_rpr = run.find(w('rPr'))
                break
        # Remove all text runs (ones with w:t), preserve drawing runs and pPr
        new_children = []
        if pPr is not None:
            new_children.append(pPr)
        for child in list(paragraph):
            if child.tag == w('pPr'):
                continue
            if child.tag == w('r') and child.find(w('t')) is not None and child.find(w('drawing')) is None:
                continue  # Skip text runs
            new_children.append(child)  # Preserve drawing runs and other elements
        # Add a single text run with the new value
        if orig_rpr is not None:
            new_run = ET.Element(w('r'))
            new_run.append(ET.fromstring(ET.tostring(orig_rpr)))
            t = ET.SubElement(new_run, w('t'))
            t.text = str(value)
            new_children.append(new_run)
        else:
            new_children.append(make_run(value, sz='24'))
        # Rebuild paragraph
        paragraph.clear()
        for child in new_children:
            paragraph.append(child)
        return True

    def apply_china_header(unpack_dir, entity_key):
        """Update the China template header for entities with defined header lines.
        Replaces entire text content of existing paragraphs rather than just
        the last w:t element, so it works correctly even with multi-run text."""
        cfg = ENTITY_CONFIG.get(entity_key, {})
        header_lines = cfg.get('header_lines')
        if not header_lines:
            print(f"⚠️  WARNING: No header override configured for entity={entity_key}; template header is unchanged.")
            return

        header_path = os.path.join(unpack_dir, 'word', 'header1.xml')
        if not os.path.exists(header_path):
            print("⚠️  WARNING: word/header1.xml not found; cannot update China template header.")
            return

        tree = ET.parse(header_path)
        root = tree.getroot()
        paragraphs = [p for p in list(root) if p.tag == w('p')]
        text_paragraphs = [p for p in paragraphs if paragraph_text(p).strip()]

        lines_to_write = [l for l in header_lines if l]
        for i, line in enumerate(lines_to_write):
            if i < len(text_paragraphs):
                replace_paragraph_text(text_paragraphs[i], line)
            else:
                new_p = make_para([make_run(line, sz='24')], spacing_after=0, line='280')
                root.append(new_p)
                text_paragraphs.append(new_p)

        surplus_start = len(lines_to_write)
        # Don't remove surplus paragraphs — clear their text to preserve
        # the header's overall spacing, so the decorative blue line stays
        # at the same position as the original template.
        for p in text_paragraphs[surplus_start:]:
            for run in p.findall(w('r')):
                if run.find(w('t')) is not None and run.find(w('drawing')) is None:
                    p.remove(run)

        tree.write(header_path, xml_declaration=True, encoding='UTF-8')
        print(f"✅ Updated template header for {cfg['company']}")

    def make_rpr(font='FangSong', sz='24', bold=False, color=None, hint='eastAsia'):
        """Create a w:rPr element matching template pattern."""
        rpr = ET.Element(w('rPr'))
        rf = ET.SubElement(rpr, w('rFonts'))
        rf.set(w('hint'), hint)
        rf.set(w('ascii'), font)
        rf.set(w('hAnsi'), font)
        rf.set(w('eastAsia'), font)
        rf.set(w('cs'), font)

        if bold:
            ET.SubElement(rpr, w('b'))
            ET.SubElement(rpr, w('bCs'))

        if color:
            c = ET.SubElement(rpr, w('color'))
            c.set(w('val'), color)

        sz_el = ET.SubElement(rpr, w('sz'))
        sz_el.set(w('val'), sz)
        sz_cs = ET.SubElement(rpr, w('szCs'))
        sz_cs.set(w('val'), sz)

        lang = ET.SubElement(rpr, w('lang'))
        lang.set(w('val'), 'en-US')
        lang.set(w('eastAsia'), 'zh-CN')

        return rpr

    def make_run(text, font='FangSong', sz='24', bold=False, color=None, hint='eastAsia'):
        """Create a w:r element. XML special characters are escaped automatically by ElementTree."""
        r = ET.Element(w('r'))
        r.append(make_rpr(font, sz, bold, color, hint))
        t = ET.SubElement(r, w('t'))
        t.text = str(text)
        return r

    def make_para(runs_or_text, spacing_before=0, spacing_after=0, line='280',
                  jc=None, indent_left=None, indent_right=None,
                  border_bottom_color=None):
        """Create a paragraph matching template pattern.
        When runs_or_text is a list of run elements, only paragraph-level
        spacing/alignment/border settings are applied — run-level styles
        come from the run elements themselves."""
        p = ET.Element(w('p'))
        pPr = ET.SubElement(p, w('pPr'))

        # Spacing
        sp = ET.SubElement(pPr, w('spacing'))
        sp.set(w('after'), str(spacing_after))
        sp.set(w('line'), line)
        sp.set(w('lineRule'), 'auto')
        if spacing_before:
            sp.set(w('before'), str(spacing_before))

        # Border
        if border_bottom_color:
            pBdr = ET.SubElement(pPr, w('pBdr'))
            bottom = ET.SubElement(pBdr, w('bottom'))
            bottom.set(w('val'), 'single')
            bottom.set(w('color'), border_bottom_color)
            bottom.set(w('sz'), '4')
            bottom.set(w('space'), '1')

        # Indent
        if indent_left or indent_right:
            ind = ET.SubElement(pPr, w('ind'))
            if indent_left:
                ind.set(w('left'), str(indent_left))
            if indent_right:
                ind.set(w('right'), str(indent_right))

        # Alignment
        if jc:
            jc_el = ET.SubElement(pPr, w('jc'))
            jc_el.set(w('val'), jc)

        # Add runs — if list of ET.Element, append directly; strings auto-wrap with default style
        if isinstance(runs_or_text, str):
            p.append(make_run(runs_or_text))
        else:
            for r_item in runs_or_text:
                if isinstance(r_item, str):
                    p.append(make_run(r_item))
                else:
                    p.append(r_item)

        return p

    def make_info_line(label, value):
        """Make a header info line matching template — unified pattern with space-padded label + colon + value."""
        r1 = ET.Element(w('r'))
        r1.append(make_rpr('FangSong', '24'))
        t1 = ET.SubElement(r1, w('t'))
        t1.text = label
        r2 = ET.Element(w('r'))
        r2.append(make_rpr('FangSong', '24'))
        t2 = ET.SubElement(r2, w('t'))
        t2.text = value
        return make_para([r1, r2], spacing_after=0, line='280')

    def make_section_header(text):
        """Make a section header like '1.服务内容' - bold, 14pt, black."""
        return make_para(
            [make_run(text, sz='28', bold=True, color='000000')],
            spacing_after=0, line='280'
        )

    def make_title(text_part1, text_part2):
        """Make the blue centered title with bottom border.
        Run-level styles are set on the runs; paragraph-level only sets spacing/alignment/border."""
        return make_para(
            [
                make_run(text_part1, sz='38', bold=True, color='4472C4'),
                make_run(text_part2, sz='36', bold=True, color='4472C4'),
            ],
            spacing_before=200, spacing_after=280, line='280',
            jc='center', indent_left=936, indent_right=936,
            border_bottom_color='4472C4'
        )

    # ====== TABLE BUILDING ======
    # Template table style
    def make_tbl_pr():
        """Create table properties matching template."""
        tblPr = ET.Element(w('tblPr'))

        style = ET.SubElement(tblPr, w('tblStyle'))
        style.set(w('val'), '11')

        tblW = ET.SubElement(tblPr, w('tblW'))
        tblW.set(w('w'), '9790')
        tblW.set(w('type'), 'dxa')

        tblInd = ET.SubElement(tblPr, w('tblInd'))
        tblInd.set(w('w'), '-439')
        tblInd.set(w('type'), 'dxa')

        # Borders
        tblBorders = ET.SubElement(tblPr, w('tblBorders'))
        for edge in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
            b = ET.SubElement(tblBorders, w(edge))
            b.set(w('val'), 'single')
            b.set(w('color'), 'auto')
            b.set(w('sz'), '4')
            b.set(w('space'), '0')

        layout = ET.SubElement(tblPr, w('tblLayout'))
        layout.set(w('type'), 'fixed')

        # Cell margins — vertical padding for readability
        cellMar = ET.SubElement(tblPr, w('tblCellMar'))
        for edge in ['top', 'left', 'bottom', 'right']:
            cm = ET.SubElement(cellMar, w(edge))
            cm.set(w('w'), '60' if edge in ['top', 'bottom'] else '108')
            cm.set(w('type'), 'dxa')

        return tblPr

    def make_tbl_grid(cols):
        """Create table grid."""
        grid = ET.Element(w('tblGrid'))
        for w_val in cols:
            gc = ET.SubElement(grid, w('gridCol'))
            gc.set(w('w'), str(w_val))
        return grid

    def make_tc_pr(width, span=None, fill=None, valign='center'):
        """Create table cell properties."""
        tcPr = ET.Element(w('tcPr'))

        tcW = ET.SubElement(tcPr, w('tcW'))
        tcW.set(w('w'), str(width))
        tcW.set(w('type'), 'dxa')

        if span:
            gs = ET.SubElement(tcPr, w('gridSpan'))
            gs.set(w('val'), str(span))

        if fill:
            shd = ET.SubElement(tcPr, w('shd'))
            shd.set(w('val'), 'clear')
            shd.set(w('color'), 'auto')
            shd.set(w('fill'), fill)

        if valign:
            va = ET.SubElement(tcPr, w('vAlign'))
            va.set(w('val'), valign)

        return tcPr

    def make_tc(paragraphs, width, span=None, fill=None, valign='top'):
        """Create a table cell."""
        tc = ET.Element(w('tc'))
        tc.append(make_tc_pr(width, span, fill, valign))

        for p_item in paragraphs:
            if isinstance(p_item, str):
                # Simple text cell - auto-format
                tc.append(make_para(
                    [make_run(p_item, sz='24')],
                    spacing_after=0, line='280'
                ))
            else:
                tc.append(p_item)

        return tc

    def make_hdr_cell(text, width):
        """Create a header cell with light blue background."""
        return make_tc(
            [make_para(
                [make_run(text, sz='24', bold=True)],
                spacing_after=0, line='280', jc='center'
            )],
            width, fill='BDD6EE', valign='center'
        )

    def make_data_cell(text, width, bold=False, jc=None, small=False, price=False):
        """Create a data cell. text can be a string or list of strings (each becomes a paragraph).
        small=True: 10.5pt for notes/documents. price=True: 10pt for price column (compact to avoid wrapping)."""
        if small:
            sz = '21'  # 10.5pt
        elif price:
            sz = '20'  # 10pt — compact to prevent wrapping in price column
        else:
            sz = '24'  # 12pt body
        if isinstance(text, list):
            paras = []
            for line in text:
                paras.append(make_para(
                    [make_run(line, sz=sz, bold=bold)],
                    spacing_after=0, line='280', jc=jc
                ))
        else:
            paras = [make_para(
                [make_run(text, sz=sz, bold=bold)],
                spacing_after=0, line='280', jc=jc
            )]
        return make_tc(paras, width)

    def make_category_row(text):
        """Create a category header row spanning all columns."""
        tr = ET.Element(w('tr'))
        trPr = ET.SubElement(tr, w('trPr'))
        trH = ET.SubElement(trPr, w('trHeight'))
        trH.set(w('val'), '430')
        trH.set(w('hRule'), 'atLeast')

        tc = make_tc(
            [make_para(
                [make_run(text, sz='24', bold=True)],
                spacing_after=0, line='280', jc='center'
            )],
            9790, span=5, valign='center'
        )
        tr.append(tc)
        return tr

    def make_table_row(cells_data, row_height='430'):
        """Create a regular table row."""
        tr = ET.Element(w('tr'))
        trPr = ET.SubElement(tr, w('trPr'))
        trH = ET.SubElement(trPr, w('trHeight'))
        trH.set(w('val'), row_height)
        trH.set(w('hRule'), 'atLeast')

        for cd in cells_data:
            tr.append(cd)
        return tr

    def make_empty_cell(width):
        """Create an empty cell."""
        return make_tc(
            [make_para([make_run('', sz='24')], spacing_after=0, line='280')],
            width
        )

    # ====== DATA ======
    COLS = [555, 2514, 1050, 2000, 3671]  # Price col wider for currency symbol; notes narrower
    services_data = quotation_data['services']
    fee_details = quotation_data['fee_details']
    process_data = quotation_data['process_data']
    doc_data = quotation_data['doc_data']
    notes = quotation_data['notes']
    doc_notes_text = quotation_data['doc_notes_text']
    quote_meta = quotation_data.get('quote_meta', {})
    DISCOUNT_AMOUNT_INT = quotation_data['discount_amount']

    preserved_payment_terms = None
    preserve_payment_source = args.preserve_payment_from
    if not preserve_payment_source and os.path.exists(OUTPUT):
        preserve_payment_source = OUTPUT
    if preserve_payment_source and not args.overwrite_payment_terms:
        try:
            preserved_payment_terms = extract_payment_terms(os.path.abspath(preserve_payment_source))
            print(f"✅ Preserved payment terms from existing .docx: {os.path.abspath(preserve_payment_source)}")
        except Exception as exc:
            print(
                f"❌ 无法从旧报价单保留付款方式: {preserve_payment_source}: {exc}。"
                "请询问用户并确认付款方式；用户明确提供后，将付款方式写入本次 quotation.json，"
                "并使用 --overwrite-payment-terms 重新生成。",
                file=sys.stderr,
            )
            sys.exit(2)

    # ====== PRICING CONFIGURATION (shared module) ======
    # All financial calculations go through scripts.quotation_common to keep
    # validate_data.py and build_quotation.py consistent.

    meta = quotation_data.get('_meta', {}) if isinstance(quotation_data.get('_meta', {}), dict) else {}
    meta_entity = meta.get('applicable_entity')
    if meta_entity and meta_entity != entity:
        print(
            f"❌ _meta.applicable_entity ({meta_entity}) must match --entity ({entity})",
            file=sys.stderr,
        )
        sys.exit(2)
    CURRENCY = meta.get('target_currency') or entity_cfg['currency']
    if CURRENCY not in ('IDR', 'RMB', 'USD', 'SGD', 'THB', 'VND'):
        print(f"❌ Unsupported target currency: {CURRENCY}", file=sys.stderr)
        sys.exit(2)
    allowed_currencies = entity_cfg.get('allowed_currencies', [entity_cfg['currency']])
    if CURRENCY not in allowed_currencies:
        print(
            f"❌ _meta.target_currency ({CURRENCY}) is not allowed for --entity ({entity}); "
            f"allowed: {', '.join(allowed_currencies)}",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.vat_rate is not None:
        VAT_RATE = float(args.vat_rate)
    else:
        VAT_RATE = float(entity_cfg['vat_rate'])

    vat_label_pct = vat_percent_label(VAT_RATE)
    VAT_LABEL = f"增值税 {vat_label_pct}"
    if template_key == 'thailand':
        # 与泰国模板一致：注明税率以开票时泰国现行税率为准
        VAT_LABEL = f"增值税 {vat_label_pct}（以开发票时泰国现行税率为准）"

    SUBTOTAL_D = sum(item['price_int'] for svc in services_data for item in svc['items'])

    # Price magnitude guard — catch RMB/IDR data mix-up
    all_prices = [item['price_int'] for svc in services_data for item in svc['items']]
    if CURRENCY == 'IDR' and any(p < 1_000_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update services_data from a previous RMB quote?")
    elif CURRENCY == 'VND' and any(p < 1_000_000 for p in all_prices if p > 0):
        print("⚠️  WARNING: Some prices appear too small for VND (min: ₫ 1,000,000). Did you forget to update from a previous RMB/USD quote?")
    elif CURRENCY == 'RMB' and any(p >= 1_000_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?")
    elif CURRENCY == 'USD' and any(p < 50 for p in all_prices if p > 0):
        print("⚠️  WARNING: Some prices appear too small for USD (min: $50). Did you forget to convert from IDR?")
    elif CURRENCY == 'USD' and any(p >= 500_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too large for USD (>= 500,000). Did you forget to convert from IDR?")

    # Withholding tax: enabled via quotation.json `withholding_tax: true`
    # Only applied if entity config defines a withholding_tax_rate
    WITHHOLDING_TAX_RATE = entity_cfg.get('withholding_tax_rate')
    WITHHOLDING_ENABLED = False
    if WITHHOLDING_TAX_RATE is not None:
        WITHHOLDING_ENABLED = bool(quotation_data.get('withholding_tax', False))
        if 'withholding_tax' not in raw_quotation:
            print("⚠️  WARNING: entity supports withholding tax (withholding_tax_rate set) but "
                  "quotation.json has no top-level `withholding_tax` field. WHT will NOT be "
                  "deducted. Add `withholding_tax: true` to the TOP level of quotation.json "
                  "(sibling of discount_amount, NOT inside _meta) to apply it.")

    amounts = calculate_amounts(
        SUBTOTAL_D, DISCOUNT_AMOUNT_INT, VAT_RATE, CURRENCY,
        withholding_tax_rate=WITHHOLDING_TAX_RATE if WITHHOLDING_ENABLED else None
    )
    DISCOUNT_D = amounts['discount']
    DISCOUNTED_D = amounts['discounted']
    VAT_D = amounts['vat']
    WHT_D = amounts.get('withholding_tax')
    GRAND_TOTAL_D = amounts['total']

    # Local aliases so the rest of the template code can stay unchanged.
    def fmt_price_int(val_d):
        return format_price_int(val_d, CURRENCY)

    def fmt_price_vat(val_d):
        return format_price_vat(val_d, CURRENCY)

    def fmt_price_total(val_d):
        return format_price_total(val_d, CURRENCY)

    print(f"Entity: {entity} | Currency: {CURRENCY} | VAT: {vat_label_pct}")
    print(f"Subtotal: {fmt_price_int(SUBTOTAL_D)} | Discount: {fmt_price_int(DISCOUNT_D)} | Discounted: {fmt_price_int(DISCOUNTED_D)}")
    wht_info = f" | WHT: {fmt_price_int(WHT_D)}" if WITHHOLDING_ENABLED and WHT_D is not None else ""
    print(f"VAT: {fmt_price_vat(VAT_D)}{wht_info} | Total: {fmt_price_total(GRAND_TOTAL_D)}")

    # ====== BUILD BODY CONTENT ======
    body_children = []

    # 1. Header Info Lines
    body_children.append(make_info_line('公司名称     ：', quote_meta.get('customer_name', '')))
    body_children.append(make_info_line('联系人      ：', quote_meta.get('contact_name', '')))
    body_children.append(make_info_line('联系方式     ：', quote_meta.get('contact_info', '')))
    # Quote date: CLI override → quote_meta → today
    quote_date_raw = args.quote_date or quote_meta.get('quote_date')
    if quote_date_raw:
        try:
            qd = date.fromisoformat(quote_date_raw)
            QUOTE_DATE = f"{qd.year}年{qd.month}月{qd.day}日"
        except ValueError:
            print(
                f"❌ Invalid quote date '{quote_date_raw}'. Use YYYY-MM-DD format.",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        QUOTE_DATE = f"{date.today().year}年{date.today().month}月{date.today().day}日"
    body_children.append(make_info_line('报价日期     ：', QUOTE_DATE))
    body_children.append(make_info_line('合同号       ：', quote_meta.get('contract_no', '')))

    # 2. Title: CLI override → quote_meta → defaults
    title_line1 = args.title_line1 or quote_meta.get('title_line1') or '印尼投资'
    title_line2 = args.title_line2 or quote_meta.get('title_line2') or '综合服务方案'
    body_children.append(make_title(title_line1, title_line2))

    # 3. Section Header
    body_children.append(make_section_header('1.服务内容'))

    # 4. Service Content Table
    tbl = ET.Element(w('tbl'))
    tbl.append(make_tbl_pr())
    tbl.append(make_tbl_grid(COLS))

    # Header row
    hdr_row = ET.Element(w('tr'))
    hdr_trPr = ET.SubElement(hdr_row, w('trPr'))
    ET.SubElement(hdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(hdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')

    price_label = f'价格\n({CURRENCY_NAMES.get(CURRENCY, CURRENCY)})'
    hdr_texts = ['序号', '服务内容', '时间\n工作日', price_label, '备注']
    for i, ht in enumerate(hdr_texts):
        lines = ht.split('\n')
        if len(lines) > 1:
            paras = []
            for line in lines:
                paras.append(make_para(
                    [make_run(line, sz='24', bold=True)],
                    spacing_after=0, line='280', jc='center'
                ))
            hdr_row.append(make_tc(paras, COLS[i], fill='BDD6EE', valign='center'))
        else:
            hdr_row.append(make_hdr_cell(ht, COLS[i]))
    tbl.append(hdr_row)

    # Service rows
    seq = 0
    for svc in services_data:
        if svc['category']:
            tbl.append(make_category_row(svc['category']))
        for item in svc['items']:
            seq += 1
            price_display = format_price_display(item["price"], CURRENCY)
            cells = [
                make_data_cell(str(seq), COLS[0], jc='center'),
                make_data_cell(item['display_name'], COLS[1], bold=True),
                make_data_cell(item['days'], COLS[2], jc='center'),
                make_data_cell(price_display, COLS[3], jc='right', price=True),
                make_data_cell(item['note'].split('\n') if '\n' in item['note'] else item['note'], COLS[4], small=True),
            ]
            tbl.append(make_table_row(cells))

    # Summary rows — all use Decimal values
    # 汇总行布局（label 右对齐、金额左对齐，用模板原始 XML 核验）：
    #   默认统一：label span3 + amount span2（中国/新加坡/德音模板一致）
    #   泰国模板：小计行 = 空 c0 + label span2 + amount span2；税/总计行 = label span3 + amount span2
    #   雅加达模板为 6 列(span4/span2)，builder 生成 5 列，统一适配 span3/span2
    #   布局元组 = (leading_empty, label_span, amount_span, amount_align)
    DEFAULT_LAYOUT = (0, 3, 2, 'left')
    SUMMARY_LAYOUT = {
        'thailand': ((1, 2, 2, 'left'), (0, 3, 2, 'left')),   # (小计行, 其他行)
    }

    def summary_row(label, value_d, fmt='int', highlight=False):
        if fmt == 'vat':
            formatted = fmt_price_vat(value_d)
        elif fmt == 'total':
            formatted = fmt_price_total(value_d)
        elif fmt == 'tax':
            # 预扣税等税金：按币种保留小数（RMB/USD/THB/SGD 仅在有小数时显示小数位，IDR/VND 取整）
            formatted = fmt_price_vat(value_d)
        else:
            formatted = fmt_price_int(value_d)
        subtotal_l, others_l = SUMMARY_LAYOUT.get(template_key, (DEFAULT_LAYOUT, DEFAULT_LAYOUT))
        leading, label_span, amount_span, amount_align = (subtotal_l if label == '小计' else others_l)
        label_w = sum(COLS[leading:leading + label_span])
        amount_w = sum(COLS[leading + label_span:leading + label_span + amount_span])
        cells = []
        if leading:
            cells.append(make_empty_cell(COLS[0]))
        cells.append(make_tc(
            [make_para(
                [make_run(label, sz='24', bold=True)],
                spacing_after=0, line='280', jc='right'
            )],
            label_w, span=label_span, valign='center'
        ))
        cells.append(make_tc(
            [make_para(
                [make_run(formatted, sz='20', bold=True)],
                spacing_after=0, line='280', jc=amount_align
            )],
            amount_w, span=amount_span, valign='center',
            fill=('F2F2F2' if highlight else None)
        ))
        return make_table_row(cells)

    tbl.append(summary_row('小计', SUBTOTAL_D, fmt='int'))
    if DISCOUNT_D > 0:
        tbl.append(summary_row('优惠金额', DISCOUNT_D, fmt='int'))
    tbl.append(summary_row(VAT_LABEL, VAT_D, fmt='vat'))
    if WITHHOLDING_ENABLED and WHT_D is not None:
        wht_label = f"预扣税 {int(WITHHOLDING_TAX_RATE * 100)}%"
        tbl.append(summary_row(wht_label, -WHT_D, fmt='tax'))
    tbl.append(summary_row('含税总计', GRAND_TOTAL_D, fmt='total', highlight=True))

    body_children.append(tbl)

    # 5. Notes section
    body_children.append(make_para('', spacing_after=0, line='280'))
    body_children.append(make_para(
        [make_run('*备注：', sz='24', bold=True)],
        spacing_after=0, line='280'
    ))

    for note_text, indent in notes:
        body_children.append(make_para(
            [make_run(note_text, sz='21')],
            spacing_after=0, line='280', indent_left=indent
        ))

    for i, fd in enumerate(fee_details):
        body_children.append(make_para(
            [make_run(f'{i+1}. {fd["name"]}', sz='21', bold=True)],
            spacing_before=40, spacing_after=0, line='280', indent_left=360
        ))
        body_children.append(make_para(
            [make_run('费用包含：', sz='21', bold=True)],
            spacing_after=0, line='280', indent_left=360
        ))
        for item in fd['include']:
            body_children.append(make_para(
                [make_run(item, sz='21')],
                spacing_after=0, line='280', indent_left=540
            ))
        if fd['exclude']:
            body_children.append(make_para(
                [make_run('费用不含：', sz='21', bold=True)],
                spacing_after=0, line='280', indent_left=360
            ))
            for item in fd['exclude']:
                body_children.append(make_para(
                    [make_run(item, sz='21')],
                    spacing_after=0, line='280', indent_left=540
                ))
        if fd['note']:
            body_children.append(make_para(
                [make_run('备注：', sz='21', bold=True)],
                spacing_after=0, line='280', indent_left=360
            ))
            for n_line in fd['note'].split('\n'):
                body_children.append(make_para(
                    [make_run(n_line, sz='21')],
                    spacing_after=0, line='280', indent_left=540
                ))

    # 6. Payment Terms — visible edits in an existing .docx win on rebuild.
    body_children.append(make_para('', spacing_before=100, spacing_after=0))
    body_children.append(make_section_header('2.付款条件：'))
    payment_terms = preserved_payment_terms or quote_meta.get('payment_terms') or entity_cfg.get('payment_terms', [])
    for warning in check_payment_terms_reasonableness(payment_terms, contract_total=GRAND_TOTAL_D, currency=CURRENCY):
        print(f'⚠️  WARNING: {warning}', file=sys.stderr)
    for term in payment_terms:
        body_children.append(make_para(
            [make_run(term, sz='21')],
            spacing_after=0, line='280', indent_left=360
        ))

    # 7. Process & Deliverables Table
    body_children.append(make_para('', spacing_before=0, spacing_after=0))
    body_children.append(make_section_header('3.服务流程及交付材料清单'))
    body_children.append(make_para('', spacing_before=0, spacing_after=120))

    PCOLS = [555, 2100, 3635, 3500]

    ptbl = ET.Element(w('tbl'))
    ptbl.append(make_tbl_pr())
    ptbl.append(make_tbl_grid(PCOLS))

    phdr_row = ET.Element(w('tr'))
    phdr_trPr = ET.SubElement(phdr_row, w('trPr'))
    ET.SubElement(phdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(phdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')
    for i, ht in enumerate(['序号', '项目', '流程', '服务完成后交付文件']):
        phdr_row.append(make_hdr_cell(ht, PCOLS[i]))
    ptbl.append(phdr_row)

    for i, pd in enumerate(process_data):
        deliverables = pd['deliverables']
        if isinstance(deliverables, list) and len(deliverables) > 1:
            deliverables = [
                item if item.startswith('•') else f"• {item}"
                for item in deliverables
            ]
        cells = [
            make_data_cell(str(i+1), PCOLS[0], jc='center'),
            make_data_cell(pd['name'], PCOLS[1], bold=True),
            make_data_cell(pd['process'], PCOLS[2], small=True),
            make_data_cell(deliverables, PCOLS[3], small=True),
        ]
        ptbl.append(make_table_row(cells))

    body_children.append(ptbl)

    # 8. Required Documents Table
    body_children.append(make_para('', spacing_before=0, spacing_after=0))
    body_children.append(make_section_header('4.所需材料清单'))
    body_children.append(make_para('', spacing_before=0, spacing_after=120))

    DCOLS = [555, 2400, 6835]

    dtbl = ET.Element(w('tbl'))
    dtbl.append(make_tbl_pr())
    dtbl.append(make_tbl_grid(DCOLS))

    dhdr_row = ET.Element(w('tr'))
    dhdr_trPr = ET.SubElement(dhdr_row, w('trPr'))
    ET.SubElement(dhdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(dhdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')
    for i, ht in enumerate(['序号', '项目', '所需材料']):
        dhdr_row.append(make_hdr_cell(ht, DCOLS[i]))
    dtbl.append(dhdr_row)

    for i, dd in enumerate(doc_data):
        cells = [
            make_data_cell(str(i+1), DCOLS[0], jc='center'),
            make_data_cell(dd['name'], DCOLS[1], bold=True),
            make_data_cell(dd['docs'], DCOLS[2], small=True),
        ]
        dtbl.append(make_table_row(cells))

    body_children.append(dtbl)

    for i, note_text in enumerate(doc_notes_text):
        body_children.append(make_para(
            [make_run(note_text, sz='21')],
            spacing_before=80 if i == 0 else 0, spacing_after=0, line='280'
        ))

    # 9. Footer - Bank Info — loaded from entity config
    bank_lines_by_currency = entity_cfg.get('bank_lines_by_currency', {})
    selected_bank_lines = bank_lines_by_currency.get(CURRENCY, entity_cfg['bank_lines'])

    body_children.append(make_para('', spacing_before=120, spacing_after=0))
    body_children.append(make_para(
        [make_run('所有款项汇到指定的银行账户，银行账户信息如下：', sz='24', bold=True)],
        spacing_after=0, line='280'
    ))
    for line in selected_bank_lines:
        body_children.append(make_para(
            [make_run(line, sz='21')],
            spacing_after=0, line='280'
        ))

    body_children.append(make_para('', spacing_before=40, spacing_after=0))
    body_children.append(make_para(
        [make_run('对于客户提供的纸质或电子版的证件、资料，应负有妥善保管和保密义务，不得将上述秘密泄露给任何第三方或用于其他用途。', sz='21')],
        spacing_after=0, line='280'
    ))
    body_children.append(make_para(
        [make_run('此报价从报价日起生效30天。', sz='21')],
        spacing_after=0, line='280'
    ))

    # 10. Signatures — company name from entity config
    body_children.append(make_para('', spacing_before=80, spacing_after=0))

    stbl = ET.Element(w('tbl'))
    stblPr = ET.SubElement(stbl, w('tblPr'))
    stblW = ET.SubElement(stblPr, w('tblW'))
    stblW.set(w('w'), '9790')
    stblW.set(w('type'), 'dxa')
    stblLayout = ET.SubElement(stblPr, w('tblLayout'))
    stblLayout.set(w('type'), 'fixed')

    stblGrid = ET.SubElement(stbl, w('tblGrid'))
    for wv in [4895, 4895]:
        gc = ET.SubElement(stblGrid, w('gridCol'))
        gc.set(w('w'), str(wv))

    sig_company = entity_cfg['company']

    # Row 1
    sig_row1 = ET.Element(w('tr'))
    for text in ['报价人：', '同意报价人：']:
        tc = ET.Element(w('tc'))
        tcPr = ET.SubElement(tc, w('tcPr'))
        tcW = ET.SubElement(tcPr, w('tcW'))
        tcW.set(w('w'), '4895')
        tcW.set(w('type'), 'dxa')
        tcMar = ET.SubElement(tcPr, w('tcMar'))
        for edge in ['top', 'bottom']:
            cm = ET.SubElement(tcMar, w(edge))
            cm.set(w('w'), '40')
            cm.set(w('type'), 'dxa')
        for edge in ['left', 'right']:
            cm = ET.SubElement(tcMar, w(edge))
            cm.set(w('w'), '80')
            cm.set(w('type'), 'dxa')
        tc.append(make_para(
            [make_run(text, sz='24', bold=True)],
            spacing_after=0, line='280'
        ))
        sig_row1.append(tc)
    stbl.append(sig_row1)

    # Row 2 — signature company from config
    sig_row2 = ET.Element(w('tr'))
    for text in [sig_company, '']:
        tc = ET.Element(w('tc'))
        tcPr = ET.SubElement(tc, w('tcPr'))
        tcW = ET.SubElement(tcPr, w('tcW'))
        tcW.set(w('w'), '4895')
        tcW.set(w('type'), 'dxa')
        tcMar = ET.SubElement(tcPr, w('tcMar'))
        for edge in ['top', 'bottom']:
            cm = ET.SubElement(tcMar, w(edge))
            cm.set(w('w'), '10')
            cm.set(w('type'), 'dxa')
        for edge in ['left', 'right']:
            cm = ET.SubElement(tcMar, w(edge))
            cm.set(w('w'), '80')
            cm.set(w('type'), 'dxa')
        tc.append(make_para(
            [make_run(text, sz='21')],
            spacing_after=0, line='280'
        ))
        sig_row2.append(tc)
    stbl.append(sig_row2)

    body_children.append(stbl)

    # ====== UNPACK TEMPLATE ======
    print(f"Unpacking template: {TEMPLATE}")
    os.makedirs(UNPACK, exist_ok=True)
    try:
        with zipfile.ZipFile(TEMPLATE, 'r') as zf:
            zf.extractall(UNPACK)
        print(f"  Unpacked to {UNPACK}")

        # Apply header override for China/Singapore template entities
        if template_key in ('china', 'singapore', 'thailand'):
            apply_china_header(UNPACK, entity)

        # Extract sectPr from the original template before editing
        orig_doc_path = os.path.join(UNPACK, 'word', 'document.xml')
        orig_tree = ET.parse(orig_doc_path)
        orig_root = orig_tree.getroot()
        orig_body = orig_root.find(f'{{{W}}}body')
        orig_sectPr = orig_body.find(f'{{{W}}}sectPr')

        if orig_sectPr is not None:
            pgSz = orig_sectPr.find(f'{{{W}}}pgSz')
            if pgSz is not None:
                print(f"  Original page: {pgSz.get(f'{{{W}}}w')} x {pgSz.get(f'{{{W}}}h')} DXA (A4)")
            sectPr_xml = ET.tostring(orig_sectPr, encoding='unicode')
        else:
            sectPr_xml = None
            print("  ⚠️  WARNING: No sectPr found in template!")

        # Remove existing body children
        orig_body.clear()

        # Add new body children
        for child in body_children:
            orig_body.append(child)

        # Restore sectPr (must be last child of body for valid OOXML)
        if sectPr_xml:
            orig_body.append(ET.fromstring(sectPr_xml))
            print("✅ Restored sectPr → A4 paper, printable")

        # Write back document.xml
        orig_tree.write(orig_doc_path, xml_declaration=True, encoding='UTF-8')
        print(f"✅ document.xml updated with {len(list(orig_body))} body elements")

        # ====== REPACK ======
        print(f"Repacking to: {OUTPUT}")
        with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(UNPACK):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, UNPACK)
                    zf.write(full_path, arcname)
    finally:
        # Cleanup temp files — guaranteed even if an exception occurs
        shutil.rmtree(UNPACK, ignore_errors=True)

    print(f"✅ Done: {OUTPUT}")
    print(f"费用: 小计={fmt_price_int(SUBTOTAL_D)} | 优惠={fmt_price_int(DISCOUNT_D)} | VAT={fmt_price_vat(VAT_D)} | 总计={fmt_price_total(GRAND_TOTAL_D)}")

if __name__ == '__main__':
    main()
