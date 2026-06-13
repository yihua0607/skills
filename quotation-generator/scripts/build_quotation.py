"""
Build quotation by editing the template's XML directly.
This ensures 100% format consistency with the original template.

Usage:
  python3 scripts/build_quotation.py --template xian --output /path/to/output.docx
  python3 scripts/build_quotation.py --template jakarta --output /path/to/output.docx

The script reads the bundled template from assets/, edits its XML, and outputs a .docx.
"""
import zipfile, os, sys, argparse, tempfile
from datetime import date
from xml.etree import ElementTree as ET

# Skill root directory (where SKILL.md lives)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Template paths
TEMPLATES = {
    'xian': os.path.join(SKILL_DIR, 'assets', '报价单模板-西安公司.docx'),
    'jakarta': os.path.join(SKILL_DIR, 'assets', '报价单模板-雅加达公司.docx'),
}

# Parse CLI args
parser = argparse.ArgumentParser(description='Generate quotation from template')
parser.add_argument('--template', choices=['xian', 'jakarta'], default='xian', help='Template to use')
parser.add_argument('--output', default=None, help='Output .docx path (default: CWD)')
parser.add_argument('--vat-rate', type=float, default=None, help='VAT rate override (e.g. 0.06, 0.01, 0.11)')
args = parser.parse_args()

TEMPLATE = TEMPLATES[args.template]
if args.output:
    OUTPUT = os.path.abspath(args.output)
else:
    name = '报价单-西安公司' if args.template == 'xian' else '报价单-雅加达公司'
    OUTPUT = os.path.join(os.getcwd(), f'{name}.docx')
UNPACK = os.path.join(tempfile.gettempdir(), f'quotation-build-{os.getpid()}', '')

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
    'wp14': 'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing',
    'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
}

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'

# Register namespaces for clean output
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

# ====== XML BUILDING HELPERS ======
def w(tag):
    return f'{{{W}}}{tag}'

def w14(tag):
    return f'{{{W14}}}{tag}'

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

    # Default run props
    rPr_default = ET.SubElement(pPr, w('rPr'))
    rf = ET.SubElement(rPr_default, w('rFonts'))
    rf.set(w('hint'), 'default')
    rf.set(w('ascii'), font)
    rf.set(w('hAnsi'), font)
    rf.set(w('eastAsia'), font)
    if bold:
        ET.SubElement(rPr_default, w('b'))
        ET.SubElement(rPr_default, w('bCs'))
    if color:
        c_el = ET.SubElement(rPr_default, w('color'))
        c_el.set(w('val'), color)
    sz_el = ET.SubElement(rPr_default, w('sz'))
    sz_el.set(w('val'), sz)
    sz_cs = ET.SubElement(rPr_default, w('szCs'))
    sz_cs.set(w('val'), sz)
    lang = ET.SubElement(rPr_default, w('lang'))
    lang.set(w('val'), 'en-US')
    lang.set(w('eastAsia'), 'zh-CN')

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
    if '：' in label:
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
    small=True: 10.5pt for notes/documents. price=True: 11pt for price column (slightly smaller to avoid wrapping)."""
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
# ⚠️ RULES WHEN EDITING SERVICE DATA:
# 1. Quantity > 1 → name must include " ×N" (e.g. "建筑代表处 ×2"), NOT in notes
# 2. Quantity = 1 → name only, no quantity marker
# 3. Price column: total price only, no unit price
# 4. DISCOUNT_AMOUNT: set to 0 if no discount (hides "优惠价格" row automatically)
COLS = [555, 2514, 1050, 1800, 3871]  # Time wider, notes narrower, price roomy

# ⚠️ REPLACE THIS DATA for each quotation — it directly becomes the output.
# Format per service: {'id': '序号', 'name': '服务名', 'days': '工作日', 'price': '总价(整数)', 'note': '备注100-200字'}
# Quantity > 1: include " ×N" in name. No unit prices.
services_data = [
    {'category': None, 'items': [
        {'id': '1', 'name': '激活农业部标准证书/营业执照 ×3', 'days': '22', 'price': '300,000,000',
         'note': '根据BKPM印尼投资部规定，企业在正式运营前必须完成标准证书/营业执照激活。中高风险/高风险KBLI需分别激活对应的证书。激活须满足设施设备清单、专业负责人、SOP文件、社区批准函、原产地文件及相关资质资料等条件。未激活将无法合规运营、销售及出口。已激活证书在公司持续合规运营期间长期有效。'},
        {'id': '2', 'name': '公司注册', 'days': '15', 'price': '20,000,000',
         'note': '外资公司注册资本最低100亿印尼盾，实缴最低25亿印尼盾。须至少两位股东、一位董事和一位监事。经营范围分低风险至高风险四个等级，每个经营地点须在OSS系统登记RDTR空间详细规划。本服务含5个经营范围的注册，每增加一个收费3,000,000印尼盾。'},
        {'id': '3', 'name': '电力代表处 ×2', 'days': '15', 'price': '60,000,000',
         'note': '外国电力服务公司可设立KPJPTLA电力代表处，须与本地电力公司组建联合体承接项目，合同总额30%须分包给本地B级资质公司。母公司须有相关经营范围。注册时须委任PJTBU印尼籍担保人。注册后须办理SBU证书才具备施工资质。'},
        {'id': '4', 'name': '劳动部登记', 'days': '3', 'price': '30,000,000',
         'note': '根据印尼1981年第7号法规，每家公司须将员工登记在劳动部系统并每年更新申报。未按时登记或信息有误，公司管理层将被处以Rp1,000,000罚款或3个月监禁。'},
    ]},
]

# ====== PRICING CONFIGURATION ======
# Currency auto-set from template: xian→RMB, jakarta→IDR
CURRENCY = 'RMB' if args.template == 'xian' else 'IDR'
# VAT rate: CLI override → currency default
if args.vat_rate is not None:
    VAT_RATE = args.vat_rate
elif CURRENCY == 'RMB':
    VAT_RATE = 0.06  # Default RMB (use --vat-rate 0.01 for Shanghai)
else:
    VAT_RATE = 0.11  # Jakarta
DECIMAL_PLACES = 2 if CURRENCY == 'RMB' else 0
vat_label_pct = f"{VAT_RATE*100:.0f}%" if VAT_RATE*100 == int(VAT_RATE*100) else f"{VAT_RATE*100:.0f}%"
VAT_LABEL = f"增值税 {vat_label_pct}"

# Service totals (hardcoded from data)
# Auto-compute subtotal from service prices (replace commas, parse as int)
SUBTOTAL = sum(int(item['price'].replace(',', '')) for svc in services_data for item in svc['items'])
DISCOUNT_AMOUNT = 0  # Amount to reduce (not discounted price!)
DISCOUNTED = SUBTOTAL - DISCOUNT_AMOUNT  # Price after discount
VAT = round(DISCOUNTED * VAT_RATE, DECIMAL_PLACES)  # Tax calculated on discounted price
GRAND_TOTAL = DISCOUNTED + VAT  # Final total

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
body_children.append(make_title('印尼投资', '综合服务方案'))

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
            make_data_cell(item['name'], COLS[1], bold=True),
            make_data_cell(item['days'], COLS[2], jc='center'),
            make_data_cell(item['price'], COLS[3], jc='right', price=True),
            make_data_cell(item['note'], COLS[4], small=True),
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
    tbl.append(summary_row('优惠价格', DISCOUNT_AMOUNT, fmt='int'))
tbl.append(summary_row(VAT_LABEL, VAT, fmt='vat'))
tbl.append(summary_row('含税总计', GRAND_TOTAL, fmt='total', highlight=True))

body_children.append(tbl)

# 5. Notes section
body_children.append(make_para('', spacing_after=0, line='280'))
body_children.append(make_para(
    [make_run('*备注：', sz='24', bold=True)],
    spacing_after=0, line='280'
))

notes = [
    ('a. 以上办理时间均为收集齐资料开始的官方办理时间；', 360),
    ('b. 以上服务报价不包括：', 360),
    ('- 文件翻译费用（如需）；', 720),
    ('- 资料快递费用（国际）；', 720),
    ('- 相关资质办理费用；', 720),
    ('- 差旅费/考察费（如有）。', 720),
]
for note_text, indent in notes:
    body_children.append(make_para(
        [make_run(note_text, sz='21')],
        spacing_after=0, line='280', indent_left=indent
    ))

# Fee details — replace per quotation
fee_details = [
    {'name': '激活农业部标准证书/营业执照', 'include': ['服务费'], 'exclude': ['相关资质办理费', '差旅费/考察费（如有）', '文件翻译费'], 'note': '本服务仅包含激活一个经营范围。'},
    {'name': '公司注册', 'include': ['山海图服务费（审核资料、填表送文件）'], 'exclude': ['资料快递费（国际快递）', '文件翻译费（如需山海图把文件翻译到印尼文或英文）'], 'note': ''},
    {'name': '电力代表处', 'include': ['服务费', '资料快递费（本地）'], 'exclude': ['资料快递费（国际）', '文件翻译费'], 'note': '注册电力代表处之后，还需办理SBU证书才具备施工资质。'},
    {'name': '劳动部登记', 'include': ['服务费'], 'exclude': [], 'note': ''},
]

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

process_data = [
    {
        'name': '激活农业部标准证书/营业执照',
        'process': [
            '第一步：收集资料',
            '第二步：整理资料并准备申请函（5 个工作日）',
            '第三步：提交申请函以及资料要求（1 个工作日）',
            '第四步：农业部审核及反馈申请函（5 个工作日）',
            '第五步：补充修正并提交申请激活（如需）（5 个工作日）',
            '第六步：农业部审核资料（5 个工作日）',
            '第七步：激活标准证书/营业执照（1 个工作日）',
        ],
        'deliverables': [
            '1. 已激活的标准证书/营业执照',
        ],
    },
    {
        'name': '公司注册',
        'process': [
            '第一步：收集资料',
            '第二步：起草公司章程草稿（3 个工作日）',
            '第三步：客户签署公司章程草稿',
            '第四步：公证师颁发公司章程（2 个工作日）',
            '第五步：公证师办理司法部批文（2 个工作日）',
            '第六步：公证师颁发司法部批文（1 个工作日）',
            '第七步：办理公司税卡和税务登记证（2 个工作日）',
            '第八步：颁发公司税卡和税务登记证（1 个工作日）',
            '第九步：办理 PKKPR 空间利用适合性许可（1 个工作日）',
            '第十步：颁发 PKKPR 空间利用适合性许可（1 个工作日）',
            '第十一步：办理商业登记证（1 个工作日）',
            '第十二步：颁发商业登记证（1 个工作日）',
        ],
        'deliverables': [
            '1. 公司章程',
            '2. 司法部批文',
            '3. 税卡和税务登记证',
            '4. 商业登记证',
            '5. 已激活的标准证书（如中低风险）',
            '6. 未激活的标准证书（如中高风险）',
            '7. OSS 账户及密码',
            '8. Coretax 账户及密码',
            '9. 公章',
            '10. PKKPR 空间利用适合性许可',
        ],
    },
    {
        'name': '电力代表处',
        'process': [
            '第一步：收集资料',
            '第二步：起草代表处章程草稿（4 个工作日）',
            '第三步：客户签字代表处章程草稿',
            '第四步：颁发代表处章程（3 个工作日）',
            '第五步：颁发代表处税卡及税务登记证（5 个工作日）',
            '第六步：颁发商业登记证及未激活的营业执照（3 个工作日）',
        ],
        'deliverables': [
            '1. 代表处章程',
            '2. 代表处税卡及税务登记证（如果同时办理两种代表处（建筑和电力）则只收到1个税卡及税务登记证）',
            '3. 商业登记证',
            '4. 未激活的营业执照',
            '5. OSS 系统用户名及密码',
        ],
    },
    {
        'name': '劳动部登记',
        'process': [
            '第一步：收集所需材料',
            '第二步：注册 WLK 账户（1 个工作日）',
            '第三步：提交资料登记员工信息（2 个工作日）',
        ],
        'deliverables': [
            '1. WLK 账户及密码',
        ],
    },
]

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

doc_data = [
    {
        'name': '激活农业部标准证书/营业执照',
        'docs': [
            '1. 公司章程及变更',
            '2. 司法部批文及变更',
            '3. 商业登记证',
            '4. 公司税卡',
            '5. OSS 系统账户和密码',
            '6. 建筑设施、设备、工具等清单',
            '7. 建筑设施、设备、工具的照片',
            '8. 公司组织架构',
            '9. NIB 上的邮件授权',
            '10. 董事信息',
            '11. 环评文件（SPPL/UKL-UPL/RKL-RPL/AMDAL）（如需）',
            '12. 办公室和仓库平面图（如需）',
            '13. 相关负责人员的相关毕业证书和/或者培训证书（如需）',
            '14. 相关 SOP 文件',
            '15. 相关周边社区的批准函、协议、承诺函',
            '16. 原产地文件',
            '17. 现场检查会议记录（如需）',
            '18. 其他资质资料（如需）',
        ],
    },
    {
        'name': '公司注册',
        'docs': [
            '1. 股东证件及信息：',
            '（1）由个人当股东：',
            '     a. 印尼籍：身份证、税卡',
            '     b. 非印尼籍：护照',
            '（2）由外国公司当股东：',
            '     a. 公司章程（宣誓翻译）（英文版/印尼文版）',
            '     b. 营业执照（宣誓翻译）（英文版/印尼文版）',
            '     c. 证明外国公司董事长的法律文件（如公司章程内无董事长相关信息）',
            '     d. 外籍公司董事长/法人的护照',
            '（3）由印尼公司当股东：',
            '     a. 公司章程及变更（如有）',
            '     b. 司法部批文及变更（如有）',
            '     c. 公司税卡和税务登记证',
            '     d. 商业登记证',
            '     e. 租赁合同',
            '2. 董事及监事证件及信息：',
            '（1）印尼籍：身份证、税卡',
            '（2）非印尼籍：护照',
            '3. 注册公司 Excel 表（山海图提供）',
            '4. 地址信息及资料：',
            '（1）地址',
            '（2）谷歌定位',
            '（3）土地面积',
            '（4）建筑技术规范/图纸/平面图（如需）',
            '（5）多边形地图（如需）',
            '（6）IMB/PBG 建筑许可（如需）',
            '（7）租赁合同/买卖协议',
            '（8）SHM 土地所有权证书/SHGB 建筑使用权证书（如需）',
        ],
    },
    {
        'name': '电力代表处',
        'docs': [
            '1. 母公司文件：',
            '（1）母公司的公司章程（印尼文版宣誓翻译）（公证双认证/海牙认证）',
            '（2）母公司的营业执照（英文/印尼文版宣誓翻译）（公证双认证/海牙认证）',
            '（3）母公司董事长护照',
            '（4）母公司的邮箱地址及电话号码',
            '（5）针对印尼投资部的 LoA 委任函（经双认证/海牙认证）',
            '（6）针对印尼能源部的 LoA 委任函（经双认证/海牙认证）',
            '（7）印尼籍代表处企业负责人的 LoA 委任函',
            '（8）LoI 意向书（经双认证/海牙认证）',
            '（9）LoS 声明函（经双认证/海牙认证）',
            '2. 代表处文件：',
            '（1）首席代表的个人资料：',
            '     a. 印尼籍：身份证、个人税卡、电话号码、邮箱地址',
            '     b. 外籍：护照、本人手持护照的照片、电话号码、邮箱地址',
            '（2）电力代表处印尼籍负责人的个人资料：',
            '     a. 身份证',
            '     b. 个人税卡',
            '     c. 个人证件照（红底、必须穿正装）',
            '（3）印尼代表处办公地址的资料：',
            '     a. 租赁合同/住所证明',
            '     b. 建筑许可（如有）',
            '     c. 土地证（如有）',
        ],
    },
    {
        'name': '劳动部登记',
        'docs': [
            '1. 公司章程及变更（如有）',
            '2. 司法部批文及变更（如有）',
            '3. 商业登记证',
            '4. 公司税卡',
            '5. 公司社保账号',
            '6. 公司邮箱地址',
            '7. 董事和监事的证件：',
            '（1）印尼籍：身份证、税卡、电话号码、邮箱地址、居住地址',
            '（2）非印尼籍：护照、税卡（如有）、暂住许可证、电话号码、邮箱地址',
            '8. 公司负责人的证件（必须印尼籍）：身份证（未用来注册WLK账户）、居住地址、电话号码、户口本',
            '9. 员工信息：',
            '（1）印尼籍：身份证、职位、学历、劳动合同状态（固定期限/无限期）、入职日期、居住地址',
            '（2）非印尼籍：护照、暂住许可证、职位、学历、入职日期',
        ],
    },
]

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

# Document notes
body_children.append(make_para(
    [make_run('备注：客户必须提供办理服务所需材料，如因材料不足或材料提供不及时耽误了办理进度，所产生的额外费用则由客户承担。', sz='21')],
    spacing_before=80, spacing_after=0, line='280'
))
body_children.append(make_para(
    [make_run('办理起止时间计算：激活农业部标准证书/营业执照、公司注册和电力代表处的办理时间均以所需资料/文件收集齐全后开始算起，法定节假日及办证政府机构休息日不算入办理时间。', sz='21')],
    spacing_after=0, line='280'
))

# 9. Footer - Bank Info
body_children.append(make_para('', spacing_before=120, spacing_after=0))
body_children.append(make_para(
    [make_run('所有款项汇到山海图指定的银行账户，银行账户信息如下：', sz='24', bold=True)],
    spacing_after=0, line='280'
))
bank_lines = [
    '开户行：中国银行西安高新技术开发区支行',
    '户名：北京山海图科技有限公司西安分公司',
    '账号：1021 0955 7761',
] if CURRENCY == 'RMB' else [
    '银行：BCA (KCP CENTRAL PARK)',
    '户名：PT. SHAN HAI MAP',
    '账号：5485225789',
    'SWIFT：CENAIDJA',
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
sig_company = 'PT. SHAN HAI MAP' if args.template == 'jakarta' else '北京山海图科技有限公司西安分公司'
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

# Cleanup
import shutil
shutil.rmtree(UNPACK, ignore_errors=True)

print(f"✅ Done: {OUTPUT}")
print(f"费用: 小计={fmt_price_int(SUBTOTAL)} | 优惠={fmt_price_int(DISCOUNT_AMOUNT)} | VAT={fmt_price_vat(VAT)} | 总计={fmt_price_total(GRAND_TOTAL)}")
