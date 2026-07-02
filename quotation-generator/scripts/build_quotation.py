"""
Build quotation by editing the template's XML directly.
This ensures 100% format consistency with the original template.

Usage:
  python3 scripts/build_quotation.py --entity xian --data quotation.json --output /path/to/output.docx
  python3 scripts/build_quotation.py --entity jakarta --data quotation.json --output /path/to/output.docx

The script reads quotation data from JSON/YAML, edits the bundled template XML, and outputs a .docx.
Entity configuration is loaded from config/entities.json — no business data is hardcoded in this script.
"""
import zipfile, os, sys, argparse, tempfile, shutil, json, re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

# Skill root directory (where SKILL.md lives)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Template paths
TEMPLATES = {
    'china': os.path.join(SKILL_DIR, 'assets', '报价单模板-中国公司.docx'),
    'jakarta': os.path.join(SKILL_DIR, 'assets', '报价单模板-雅加达公司.docx'),
}

# Load entity configuration from external JSON — keeps business data out of the script.
ENTITY_CONFIG_PATH = os.path.join(SKILL_DIR, 'config', 'entities.json')

def load_entity_config():
    """Load entity configuration from config/entities.json.

    Validates that every entity contains all required fields.
    Skips the _meta key (used for schema metadata and universal excludes).
    """
    if not os.path.exists(ENTITY_CONFIG_PATH):
        print(f"❌ Entity config not found: {ENTITY_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(ENTITY_CONFIG_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    required_fields = ('template', 'company', 'header_lines', 'vat_rate',
                       'currency', 'payment_terms', 'bank_lines')
    errors = []
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue  # Skip meta/annotation keys
        for field in required_fields:
            if field not in cfg:
                errors.append(f"Entity '{key}' missing required field: {field}")

    if errors:
        print(f"❌ Invalid entity config:\n- " + '\n- '.join(errors), file=sys.stderr)
        sys.exit(1)

    # Remove _meta before returning — it's not an entity
    cleaned = {k: v for k, v in raw.items() if not k.startswith('_')}
    return cleaned

ENTITY_CONFIG = load_entity_config()

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

    discount_amount = data.get('discount_amount', 0)
    try:
        discount_amount = parse_money_int(discount_amount, 'discount_amount')
        if discount_amount < 0:
            errors.append('discount_amount must be >= 0')
    except ValueError as exc:
        errors.append(str(exc))
        discount_amount = 0

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

    # Parse CLI args — entity is now always required (including jakarta)
    parser = argparse.ArgumentParser(description='Generate quotation from template')
    parser.add_argument('--entity', required=True,
                        choices=list(ENTITY_CONFIG.keys()),
                        help='Signing entity (required): jakarta/beijing/xian/shenzhen/shanghai/shanghai_new')
    parser.add_argument('--output', default=None, help='Output .docx path (default: CWD)')
    parser.add_argument('--data', required=True, help='Quotation data file (.json, .yaml, .yml)')
    parser.add_argument('--vat-rate', type=float, default=None, help='VAT rate override (e.g. 0.06, 0.01, 0.11)')
    parser.add_argument('--title-line1', default='印尼投资', help='Title first line (default: 印尼投资)')
    parser.add_argument('--title-line2', default='综合服务方案', help='Title second line (default: 综合服务方案)')
    parser.add_argument('--quote-date', default=None, help='Quote date override (default: today, format: YYYY-MM-DD)')
    args = parser.parse_args()

    entity = args.entity
    entity_cfg = ENTITY_CONFIG[entity]
    template_key = entity_cfg['template']

    try:
        quotation_data = validate_and_normalize_data(load_quotation_data(args.data))
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

    def paragraph_text(paragraph):
        return ''.join((t.text or '') for t in paragraph.findall('.//' + w('t')))

    def replace_paragraph_text(paragraph, value):
        """Replace ALL text content in a paragraph with a single clean run.
        Removes existing w:r elements and adds one new run with the value,
        preserving paragraph-level formatting (w:pPr)."""
        # Keep pPr if it exists
        pPr = paragraph.find(w('pPr'))
        # Remove all existing children except pPr
        for child in list(paragraph):
            if child.tag != w('pPr'):
                paragraph.remove(child)
        # Add a single run with the new text
        paragraph.append(make_run(value, sz='24'))
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
        for p in text_paragraphs[surplus_start:]:
            root.remove(p)

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
        """Create a w:r element."""
        r = ET.Element(w('r'))
        r.append(make_rpr(font, sz, bold, color, hint))
        t = ET.SubElement(r, w('t'))
        t.text = text
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
    DISCOUNT_AMOUNT_INT = quotation_data['discount_amount']

    # ====== PRICING CONFIGURATION (full Decimal chain) ======
    # All financial amounts travel as Decimal through the calculation pipeline.
    # Only converted to string at the final formatting stage.

    CURRENCY = entity_cfg['currency']
    CURRENCY_SYMBOL = '￥' if CURRENCY == 'RMB' else 'Rp'
    DECIMAL_PLACES = 2 if CURRENCY == 'RMB' else 0

    # VAT rate: CLI override → entity config → currency default
    if args.vat_rate is not None:
        VAT_RATE = Decimal(str(args.vat_rate))
    else:
        VAT_RATE = Decimal(str(entity_cfg['vat_rate']))

    # Use :g format to strip unnecessary trailing zeros (e.g. 11.0% → 11%, 1.0% → 1%)
    vat_pct = float(VAT_RATE * 100)
    vat_label_pct = f"{vat_pct:g}%"
    VAT_LABEL = f"增值税 {vat_label_pct}"

    # All amounts in Decimal for precision throughout the pipeline
    SUBTOTAL_D = Decimal(sum(item['price_int'] for svc in services_data for item in svc['items']))
    DISCOUNT_D = Decimal(DISCOUNT_AMOUNT_INT)

    # Price magnitude guard — catch RMB/IDR data mix-up
    all_prices = [item['price_int'] for svc in services_data for item in svc['items']]
    if CURRENCY == 'IDR' and any(p < 1_000_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update services_data from a previous RMB quote?")
    elif CURRENCY == 'RMB' and any(p >= 1_000_000 for p in all_prices):
        print("⚠️  WARNING: Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?")

    # Discounted subtotal
    DISCOUNTED_D = SUBTOTAL_D - DISCOUNT_D

    # VAT calculation — fully Decimal, quantize at output
    vat_d = DISCOUNTED_D * VAT_RATE
    if DECIMAL_PLACES == 2:
        VAT_D = vat_d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        VAT_D = vat_d.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    # Grand total
    GRAND_TOTAL_D = DISCOUNTED_D + VAT_D

    # Format helpers with precision rules + currency symbol:
    # - Service prices, subtotal, discount: ALWAYS integer (both RMB and IDR)
    # - VAT and grand total: RMB allows 2dp, IDR must be integer
    # - ￥ for RMB (no space), Rp for IDR (with space)
    def fmt_price_int(val_d):
        """Format service prices / subtotal / discount — Decimal input, always integer with currency symbol."""
        formatted = f'{int(val_d):,}'
        return f'￥{formatted}' if CURRENCY == 'RMB' else f'Rp {formatted}'

    def fmt_price_vat(val_d):
        """Format VAT — Decimal input, RMB always 2dp, IDR integer, with currency symbol."""
        if CURRENCY == 'RMB':
            return f'￥{float(val_d):,.2f}'
        else:
            return f'Rp {int(val_d):,}'

    def fmt_price_total(val_d):
        """Format grand total — Decimal input, RMB always 2dp, IDR integer, with currency symbol."""
        if CURRENCY == 'RMB':
            return f'￥{float(val_d):,.2f}'
        else:
            return f'Rp {int(val_d):,}'

    print(f"Entity: {entity} | Currency: {CURRENCY} | VAT: {vat_label_pct}")
    print(f"Subtotal: {fmt_price_int(SUBTOTAL_D)} | Discount: {fmt_price_int(DISCOUNT_D)} | Discounted: {fmt_price_int(DISCOUNTED_D)}")
    print(f"VAT: {fmt_price_vat(VAT_D)} | Total: {fmt_price_total(GRAND_TOTAL_D)}")

    # ====== BUILD BODY CONTENT ======
    body_children = []

    # 1. Header Info Lines
    body_children.append(make_info_line('公司名称     ：', ''))
    body_children.append(make_info_line('联系人      ：', ''))
    body_children.append(make_info_line('联系方式     ：', ''))
    # Quote date: use --quote-date if provided, otherwise today
    if args.quote_date:
        try:
            qd = date.fromisoformat(args.quote_date)
            QUOTE_DATE = f"{qd.year}年{qd.month}月{qd.day}日"
        except ValueError:
            print(f"⚠️  WARNING: Invalid --quote-date format '{args.quote_date}', using today.", file=sys.stderr)
            QUOTE_DATE = f"{date.today().year}年{date.today().month}月{date.today().day}日"
    else:
        QUOTE_DATE = f"{date.today().year}年{date.today().month}月{date.today().day}日"
    body_children.append(make_info_line('报价日期     ：', QUOTE_DATE))
    body_children.append(make_info_line('合同号       ：', ''))

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

    price_label = '价格\n(总价)'
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
            price_display = f'￥{item["price"]}' if CURRENCY == 'RMB' else f'Rp {item["price"]}'
            cells = [
                make_data_cell(item['id'], COLS[0], jc='center'),
                make_data_cell(item['display_name'], COLS[1], bold=True),
                make_data_cell(item['days'], COLS[2], jc='center'),
                make_data_cell(price_display, COLS[3], jc='right', price=True),
                make_data_cell(item['note'].split('\n') if '\n' in item['note'] else item['note'], COLS[4], small=True),
            ]
            tbl.append(make_table_row(cells))

    # Summary rows — all use Decimal values
    def summary_row(label, value_d, fmt='int', highlight=False):
        if fmt == 'vat':
            formatted = fmt_price_vat(value_d)
        elif fmt == 'total':
            formatted = fmt_price_total(value_d)
        else:
            formatted = fmt_price_int(value_d)
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

    tbl.append(summary_row('小计', SUBTOTAL_D, fmt='int'))
    if DISCOUNT_D > 0:
        tbl.append(summary_row('优惠金额', DISCOUNT_D, fmt='int'))
    tbl.append(summary_row(VAT_LABEL, VAT_D, fmt='vat'))
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

    # 6. Payment Terms — loaded from entity config
    body_children.append(make_para('', spacing_before=100, spacing_after=0))
    body_children.append(make_section_header('2.付款条件：'))
    payment_terms = entity_cfg.get('payment_terms', [])
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
    body_children.append(make_para('', spacing_before=120, spacing_after=0))
    body_children.append(make_para(
        [make_run('所有款项汇到山海图指定的银行账户，银行账户信息如下：', sz='24', bold=True)],
        spacing_after=0, line='280'
    ))
    for line in entity_cfg['bank_lines']:
        body_children.append(make_para(
            [make_run(line, sz='21')],
            spacing_after=0, line='280'
        ))

    body_children.append(make_para('', spacing_before=40, spacing_after=0))
    body_children.append(make_para(
        [make_run('山海图应对客户提供的纸质或电子版的证件、资料负有妥善保管和保密义务，不得将上述秘密泄露给任何第三方或用于其他用途。', sz='21')],
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

        # Apply header override for China template entities
        if template_key == 'china':
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
