"""
Build quotation by editing the template's XML directly.
This ensures 100% format consistency with the original template.

Usage:
  python3 scripts/build_quotation.py --template xian --entity xian --data quotation.json --output /path/to/output.docx
  python3 scripts/build_quotation.py --template jakarta --data quotation.json --output /path/to/output.docx

The script reads quotation data from JSON/YAML, edits the bundled template XML, and outputs a .docx.
"""
import zipfile, os, sys, argparse, tempfile, shutil, json, re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

# Skill root directory (where SKILL.md lives)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Template paths
TEMPLATES = {
    'xian': os.path.join(SKILL_DIR, 'assets', '报价单模板-西安公司.docx'),
    'shenzhen': os.path.join(SKILL_DIR, 'assets', '报价单模板-深圳公司.docx'),
    'jakarta': os.path.join(SKILL_DIR, 'assets', '报价单模板-雅加达公司.docx'),
}


REQUIRED_TOP_LEVEL_KEYS = ('services', 'fee_details', 'process_data', 'doc_data')


def load_quotation_data(path):
    """Load quotation data from JSON, or YAML when PyYAML is available."""
    if not path:
        raise ValueError('Missing --data. Provide a JSON/YAML quotation data file.')

    data_path = os.path.abspath(path)
    if not os.path.exists(data_path):
        raise ValueError(f'Data file not found: {data_path}')

    ext = os.path.splitext(data_path)[1].lower()
    with open(data_path, 'r', encoding='utf-8') as f:
        if ext == '.json':
            return json.load(f)
        if ext in ('.yaml', '.yml'):
            try:
                import yaml
            except ImportError as exc:
                raise ValueError('YAML input requires PyYAML. Use JSON to keep the skill dependency-free.') from exc
            return yaml.safe_load(f)
        raise ValueError('Unsupported data file type. Use .json, .yaml, or .yml.')


def parse_money_int(value, path):
    """Parse service price / discount values. These must be whole-number totals."""
    if isinstance(value, bool):
        raise ValueError(f'{path} must be an integer amount, not boolean')
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.replace(',', '').strip()
        if raw.isdigit():
            return int(raw)
    raise ValueError(f'{path} must be an integer amount or comma-formatted integer string')


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


def validate_and_normalize_data(data):
    """Validate input data before touching the Word template."""
    errors = []
    warnings = []

    if not isinstance(data, dict):
        raise ValueError('Quotation data must be a JSON/YAML object')

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

    def validate_named_records(key, list_fields, allow_note=False):
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

    fee_details = validate_named_records('fee_details', ['include', 'exclude'], allow_note=True)
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

    discount_amount = data.get('discount_amount', 0)
    try:
        discount_amount = parse_money_int(discount_amount, 'discount_amount')
        if discount_amount < 0:
            errors.append('discount_amount must be >= 0')
    except ValueError as exc:
        errors.append(str(exc))
        discount_amount = 0

    discount_mode = data.get('discount_mode')
    if discount_mode == 'post_tax':
        errors.append('discount_mode=post_tax is not supported; discounts always apply to pre-tax subtotal')
    elif discount_mode not in (None, 'pre_tax'):
        errors.append('discount_mode is deprecated; omit it because discounts always apply to pre-tax subtotal')
    elif discount_mode == 'pre_tax':
        warnings.append('discount_mode is no longer needed; discounts always apply to pre-tax subtotal')

    subtotal = sum(item['price_int'] for group in normalized_services for item in group['items'])
    if discount_amount > subtotal:
        errors.append('discount_amount cannot exceed subtotal')

    if errors:
        raise ValueError('Invalid quotation data:\n- ' + '\n- '.join(errors))
    return {
        'services': normalized_services,
        'fee_details': fee_details,
        'process_data': process_data,
        'doc_data': doc_data,
        'notes': normalized_notes,
        'doc_notes_text': normalized_doc_notes,
        'discount_amount': discount_amount,
        'warnings': warnings,
    }

def main():

    # Parse CLI args
    parser = argparse.ArgumentParser(description='Generate quotation from template')
    parser.add_argument('--template', choices=['xian', 'shenzhen', 'jakarta'], default='xian', help='Template to use')
    parser.add_argument('--output', default=None, help='Output .docx path (default: CWD)')
    parser.add_argument('--data', required=True, help='Quotation data file (.json, .yaml, .yml)')
    parser.add_argument('--vat-rate', type=float, default=None, help='VAT rate override (e.g. 0.06, 0.01, 0.11)')
    parser.add_argument('--entity', choices=['beijing', 'xian', 'shanghai', 'shanghai_new'], default=None, help='Signing entity (required for xian template)')
    parser.add_argument('--title-line1', default='印尼投资', help='Title first line (default: 印尼投资)')
    parser.add_argument('--title-line2', default='综合服务方案', help='Title second line (default: 综合服务方案)')
    args = parser.parse_args()

    try:
        quotation_data = validate_and_normalize_data(load_quotation_data(args.data))
    except ValueError as exc:
        print(f'❌ {exc}', file=sys.stderr)
        sys.exit(2)
    for warning in quotation_data['warnings']:
        print(f'⚠️  WARNING: {warning}')

    TEMPLATE = TEMPLATES[args.template]
    if args.output:
        OUTPUT = os.path.abspath(args.output)
    else:
        if args.template == 'xian':
            name = '报价单-西安公司'
        elif args.template == 'shenzhen':
            name = '报价单-深圳公司'
        else:
            name = '报价单-雅加达公司'
        OUTPUT = os.path.join(os.getcwd(), f'{name}.docx')
    UNPACK = os.path.join(tempfile.gettempdir(), f'quotation-build-{os.getpid()}', '')

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
        """Create a w:r element."""
        r = ET.Element(w('r'))
        r.append(make_rpr(font, sz, bold, color, hint))
        t = ET.SubElement(r, w('t'))
        t.text = text
        return r

    def make_tab():
        """Create a w:tab element."""
        return ET.Element(w('tab'))

    def make_para(runs_or_text, spacing_before=0, spacing_after=0, line='280',
                  jc=None, indent_left=None, indent_right=None,
                  border_bottom_color=None, font='FangSong', sz='24',
                  bold=False, color=None):
        """Create a paragraph matching template pattern."""
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

        # Add runs
        if isinstance(runs_or_text, str):
            runs = [make_run(runs_or_text, font, sz, bold, color)]
        else:
            runs = runs_or_text

        for r_item in runs:
            if isinstance(r_item, str):
                p.append(make_run(r_item, font, sz, bold, color))
            else:
                p.append(r_item)

        return p

    def make_info_line(label, value):
        """Make a header info line matching template."""
        if '：' in label or ':' in label:
            # Pattern A: "公司名称     ：" + value (colon already in label, no tab)
            r1 = ET.Element(w('r'))
            r1.append(make_rpr('FangSong', '24'))
            t1 = ET.SubElement(r1, w('t'))
            t1.text = label
            r2 = ET.Element(w('r'))
            r2.append(make_rpr('FangSong', '24'))
            t2 = ET.SubElement(r2, w('t'))
            t2.text = value
            return make_para([r1, r2], spacing_after=0, line='280')
        else:
            # Pattern B: label + tab + "：value" (colon added after tab)
            r1 = ET.Element(w('r'))
            r1.append(make_rpr('FangSong', '24'))
            t1 = ET.SubElement(r1, w('t'))
            t1.text = label
            tab = ET.Element(w('tab'))
            r2 = ET.Element(w('r'))
            r2.append(make_rpr('FangSong', '24'))
            t2 = ET.SubElement(r2, w('t'))
            t2.text = f'：{value}'
            return make_para([r1, tab, r2], spacing_after=0, line='280')

    def make_section_header(text):
        """Make a section header like '1.服务内容' - bold, 14pt, black."""
        return make_para(
            [make_run(text, sz='28', bold=True, color='000000')],
            spacing_after=0, line='280'
        )

    def make_title(text_part1, text_part2):
        """Make the blue centered title with bottom border."""
        return make_para(
            [
                make_run(text_part1, sz='38', bold=True, color='4472C4'),
                make_run(text_part2, sz='36', bold=True, color='4472C4'),
            ],
            spacing_before=200, spacing_after=280, line='280',
            jc='center', indent_left=936, indent_right=936,
            border_bottom_color='4472C4', font='FangSong', sz='44',
            bold=True, color='4472C4'
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
    COLS = [555, 2514, 1050, 1800, 3871]  # Time wider, notes narrower, price roomy
    services_data = quotation_data['services']
    fee_details = quotation_data['fee_details']
    process_data = quotation_data['process_data']
    doc_data = quotation_data['doc_data']
    notes = quotation_data['notes']
    doc_notes_text = quotation_data['doc_notes_text']
    DISCOUNT_AMOUNT = quotation_data['discount_amount']

    # ====== PRICING CONFIGURATION ======
    # Currency auto-set from template: xian/shenzhen→RMB, jakarta→IDR
    CURRENCY = 'IDR' if args.template == 'jakarta' else 'RMB'
    # VAT rate: CLI override → entity default → currency default
    if args.vat_rate is not None:
        VAT_RATE = args.vat_rate
    elif args.entity in ('shanghai', 'shanghai_new'):
        VAT_RATE = 0.01  # Shanghai entities
    elif CURRENCY == 'RMB':
        VAT_RATE = 0.06  # Default RMB
    else:
        VAT_RATE = 0.11  # Jakarta
    DECIMAL_PLACES = 2 if CURRENCY == 'RMB' else 0
    if args.template == 'xian' and args.entity is None:
        print("⚠️  WARNING: --template xian used without --entity. Defaulting to xian entity (西安). "
              "For Beijing/Shanghai entities, pass --entity beijing/shanghai/shanghai_new.")
    vat_label_pct = f"{VAT_RATE*100:.0f}%" if VAT_RATE*100 == int(VAT_RATE*100) else f"{VAT_RATE*100:.0f}%"
    VAT_LABEL = f"增值税 {vat_label_pct}"

    # Auto-compute subtotal from validated service prices.
    SUBTOTAL = sum(item['price_int'] for svc in services_data for item in svc['items'])

    # Price magnitude guard — catch RMB/IDR data mix-up
    all_prices = [item['price_int'] for svc in services_data for item in svc['items']]
    if CURRENCY == 'IDR' and any(p < 1_000_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update services_data from a previous RMB quote?")
    elif CURRENCY == 'RMB' and any(p >= 1_000_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?")
    # Discounts always reduce the pre-tax subtotal before VAT is calculated.
    DISCOUNTED = SUBTOTAL - DISCOUNT_AMOUNT

    # Use Decimal for financial calculations to avoid float precision issues.
    vat_rate_d = Decimal(str(VAT_RATE))
    vat_d = Decimal(DISCOUNTED) * vat_rate_d
    if DECIMAL_PLACES == 2:
        VAT = float(vat_d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    else:
        VAT = int(vat_d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    GRAND_TOTAL = DISCOUNTED + VAT

    # Format helpers with precision rules:
    # - Service prices, subtotal, discount: ALWAYS integer (both RMB and IDR)
    # - VAT and grand total: RMB allows 2dp, IDR must be integer
    def fmt_price_int(val):
        """Format service prices / subtotal / discount — always integer."""
        return f'{int(round(val)):,}'

    def fmt_price_vat(val):
        """Format VAT — RMB always 2dp, IDR integer."""
        if CURRENCY == 'RMB':
            return f'{val:,.2f}'  # Always show 2 decimal places
        else:
            return f'{int(round(val)):,}'

    def fmt_price_total(val):
        """Format grand total — RMB always 2dp, IDR integer."""
        if CURRENCY == 'RMB':
            return f'{val:,.2f}'  # Always show 2 decimal places
        else:
            return f'{int(round(val)):,}'

    print(f"Currency: {CURRENCY} | VAT: {VAT_RATE*100}%")
    print(f"Subtotal: {fmt_price_int(SUBTOTAL)} | Discount: {fmt_price_int(DISCOUNT_AMOUNT)} | Discounted: {fmt_price_int(DISCOUNTED)}")
    print(f"VAT: {fmt_price_vat(VAT)} | Total: {fmt_price_total(GRAND_TOTAL)}")

    # ====== BUILD BODY CONTENT ======
    body_children = []

    # 1. Header Info Lines
    body_children.append(make_info_line('公司名称     ：', ''))
    body_children.append(make_info_line('联系人', ''))
    body_children.append(make_info_line('联系方式     ：', ''))
    QUOTE_DATE = f"{date.today().year}年{date.today().month}月{date.today().day}日"
    body_children.append(make_info_line('报价日期', QUOTE_DATE))
    body_children.append(make_info_line('订单号       ：', ''))

    # 2. Title
    body_children.append(make_title(args.title_line1, args.title_line2))

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

    price_label = '价格\n(人民币)' if CURRENCY == 'RMB' else '价格\n(印尼盾)'
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
    for svc in services_data:
        if svc['category']:
            tbl.append(make_category_row(svc['category']))
        for item in svc['items']:
            cells = [
                make_data_cell(item['id'], COLS[0], jc='center'),
                make_data_cell(item['display_name'], COLS[1], bold=True),
                make_data_cell(item['days'], COLS[2], jc='center'),
                make_data_cell(item['price'], COLS[3], jc='right', price=True),
                make_data_cell(item['note'].split('\n') if '\n' in item['note'] else item['note'], COLS[4], small=True),
            ]
            tbl.append(make_table_row(cells))

    # Summary rows — static values
    def summary_row(label, value, fmt='int', highlight=False):
        if fmt == 'vat':
            formatted = fmt_price_vat(value)
        elif fmt == 'total':
            formatted = fmt_price_total(value)
        else:
            formatted = fmt_price_int(value)
        cells = [
            make_empty_cell(COLS[0]),
            make_data_cell(label, COLS[1], bold=True, jc='right'),
            make_empty_cell(COLS[2]),
            make_data_cell(formatted, COLS[3], bold=True, jc='right', price=True),
            make_empty_cell(COLS[4]),
        ]
        if highlight:
            cells[3] = make_tc(
                [make_para(
                    [make_run(formatted, sz='20', bold=True)],
                    spacing_after=0, line='280', jc='right'
                )],
                COLS[3], fill='F2F2F2', valign='center'
            )
        return make_table_row(cells)

    tbl.append(summary_row('小计', SUBTOTAL, fmt='int'))
    if DISCOUNT_AMOUNT > 0:
        tbl.append(summary_row('优惠金额', DISCOUNT_AMOUNT, fmt='int'))
    tbl.append(summary_row(VAT_LABEL, VAT, fmt='vat'))
    tbl.append(summary_row('含税总计', GRAND_TOTAL, fmt='total', highlight=True))

    body_children.append(tbl)

    # 5. Notes section
    body_children.append(make_para('', spacing_after=0, line='280'))
    body_children.append(make_para(
        [make_run('*备注：', sz='24', bold=True)],
        spacing_after=0, line='280'
    ))

    # General notes below the service table, loaded from --data.
    for note_text, indent in notes:
        body_children.append(make_para(
            [make_run(note_text, sz='21')],
            spacing_after=0, line='280', indent_left=indent
        ))

    # Per-service fee breakdown, loaded from --data.
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

    # 6. Payment Terms
    body_children.append(make_para('', spacing_before=100, spacing_after=0))
    body_children.append(make_section_header('2.付款条件：'))
    payment_terms = ['合同签订后支付合同金额的 70%，剩余 30% 在所有服务完成后 5 个工作日内支付。'] if CURRENCY == 'RMB' else ['甲方应在收到发票后 5 个工作日内支付合同金额的 100%。']
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

    # Service workflow steps and deliverables, loaded from --data.
    ptbl = ET.Element(w('tbl'))
    ptbl.append(make_tbl_pr())
    ptbl.append(make_tbl_grid(PCOLS))

    # Header
    phdr_row = ET.Element(w('tr'))
    phdr_trPr = ET.SubElement(phdr_row, w('trPr'))
    ET.SubElement(phdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(phdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')
    for i, ht in enumerate(['序号', '项目', '流程', '服务完成后交付文件']):
        phdr_row.append(make_hdr_cell(ht, PCOLS[i]))
    ptbl.append(phdr_row)

    for i, pd in enumerate(process_data):
        cells = [
            make_data_cell(str(i+1), PCOLS[0], jc='center'),
            make_data_cell(pd['name'], PCOLS[1], bold=True),
            make_data_cell(pd['process'], PCOLS[2], small=True),
            make_data_cell(pd['deliverables'], PCOLS[3], small=True),
        ]
        ptbl.append(make_table_row(cells))

    body_children.append(ptbl)

    # 8. Required Documents Table
    body_children.append(make_para('', spacing_before=0, spacing_after=0))
    body_children.append(make_section_header('4.所需材料清单'))
    body_children.append(make_para('', spacing_before=0, spacing_after=120))

    DCOLS = [555, 2400, 6835]

    # Required documents, loaded from --data.
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

    # Footer notes below the materials table, loaded from --data.
    for i, note_text in enumerate(doc_notes_text):
        body_children.append(make_para(
            [make_run(note_text, sz='21')],
            spacing_before=80 if i == 0 else 0, spacing_after=0, line='280'
        ))

    # 9. Footer - Bank Info
    body_children.append(make_para('', spacing_before=120, spacing_after=0))
    body_children.append(make_para(
        [make_run('所有款项汇到山海图指定的银行账户，银行账户信息如下：', sz='24', bold=True)],
        spacing_after=0, line='280'
    ))
    if args.template == 'jakarta':
        bank_lines = [
            '银行：BCA (KCP CENTRAL PARK)',
            '户名：PT. SHAN HAI MAP',
            '账号：5485225789',
            'SWIFT：CENAIDJA',
        ]
    elif args.template == 'shenzhen':
        bank_lines = [
            '统一社会信用代码（或税号）：91440300MA5HXMAEXM',
            '开户行：中国银行股份有限公司深圳高新区支行',
            '开户名：北京山海图科技有限公司深圳分公司',
            '账号：7770 7729 1133',
            '地址：深圳市南山区招商街道花果山社区南海大道1052号至卓飞高大厦(海翔广场)717',
        ]
    else:  # xian template — covers 4 entities
        if args.entity == 'beijing':
            bank_lines = [
                '（银行信息待确认，请联系财务确认北京公司收款账户）',
            ]
        elif args.entity == 'shanghai':
            bank_lines = [
                '（银行信息待确认，请联系财务确认上海公司收款账户）',
            ]
        elif args.entity == 'shanghai_new':
            bank_lines = [
                '（银行信息待确认，请联系财务确认上海新企业收款账户）',
            ]
        else:  # xian (default)
            bank_lines = [
                '开户行：中国银行西安高新技术开发区支行',
                '户名：北京山海图科技有限公司西安分公司',
                '账号：1021 0955 7761',
            ]
    for line in bank_lines:
        body_children.append(make_para(
            [make_run(line, sz='21')],
            spacing_after=0, line='280'
        ))

    body_children.append(make_para('', spacing_before=40, spacing_after=0))
    body_children.append(make_para(
        [make_run('山海图应对客户提供的纸板或电子版的证件、资料负有妥善保管和保密义务，不得将上述秘密泄露给任何第三方或用于其他用途。', sz='21')],
        spacing_after=0, line='280'
    ))
    body_children.append(make_para(
        [make_run('此报价从报价日起生效30天。', sz='21')],
        spacing_after=0, line='280'
    ))

    # 10. Signatures
    body_children.append(make_para('', spacing_before=80, spacing_after=0))

    # Signature table
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

    # Row 2
    # Signature company
    if args.template == 'jakarta':
        sig_company = 'PT. SHAN HAI MAP'
    elif args.template == 'shenzhen':
        sig_company = '北京山海图科技有限公司深圳分公司'
    elif args.entity == 'beijing':
        sig_company = '北京山海图科技有限公司'
    elif args.entity == 'shanghai':
        sig_company = '北京山海图科技有限公司上海分公司'
    elif args.entity == 'shanghai_new':
        sig_company = '上海山海图新企业咨询有限公司'
    else:
        sig_company = '北京山海图科技有限公司西安分公司'
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
        # Create output .docx by re-zipping the unpacked directory
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
    print(f"费用: 小计={fmt_price_int(SUBTOTAL)} | 优惠={fmt_price_int(DISCOUNT_AMOUNT)} | VAT={fmt_price_vat(VAT)} | 总计={fmt_price_total(GRAND_TOTAL)}")

if __name__ == '__main__':
    main()
