"""
Build quotation by editing the template's XML directly.
This ensures 100% format consistency with the original template.

Usage:
  python3 scripts/build_quotation.py --entity xian --data quotation.json --output /path/to/output.docx
  python3 scripts/build_quotation.py --entity jakarta --data quotation.json --output /path/to/output.docx

The script reads quotation data from JSON, edits the bundled template XML, and outputs a .docx.
Entity configuration is loaded from config/entities.json — no business data is hardcoded in this script.
"""
import zipfile, os, sys, argparse, tempfile, shutil, json, re
from datetime import date
from decimal import Decimal
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
    load_entity_config_with_meta,
    price_magnitude_warnings,
    CURRENCY_NAMES,
    is_target_currency,
    A4_PAGE_W,
    A4_PAGE_H,
    A4_MARGINS,
    header_image_spec,
    header_image_geometry,
    header_logo_spec,
    png_alpha_bottom_padding,
    MIN_PRINTABLE_INK_TOP_MM,
    MIN_LOGO_LINE_GAP_MM,
    EMU_PER_MM,
    is_process_time_disclaimer,
    xml_safe_text,
)
from scripts.sync_payment_terms import extract_payment_terms, check_payment_terms_reasonableness

# Skill root directory (where SKILL.md lives)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTITY_CONFIG, _, ENTITY_META = load_entity_config_with_meta()


def template_path_for_entity(entity_cfg):
    """Resolve a configured template while preventing paths outside the skill."""
    relative = entity_cfg['template_file']
    path = os.path.abspath(os.path.join(SKILL_DIR, relative))
    if not path.startswith(os.path.abspath(SKILL_DIR) + os.sep):
        raise ValueError(f'template_file must stay inside the skill directory: {relative}')
    if not os.path.isfile(path):
        raise ValueError(f'configured template_file does not exist: {relative}')
    return path


# Backward-compatible inventory used by template tests. Building the inventory
# performs no file I/O; only the selected entity is validated in main().
TEMPLATES = {
    key: os.path.abspath(os.path.join(SKILL_DIR, cfg['template_file']))
    for key, cfg in ENTITY_CONFIG.items()
}

# 「所需资料及信息」列的行缩进刻度：数据里行首每 1 个全角空格（U+3000）＝ 1 级 ＝ 360 twips
# （与 notes 的 indent 同一刻度：1 级 360、2 级 720）。0 级不写 w:ind，即顶格。
DOC_LINE_INDENT_TWIPS = 360
DOC_LINE_INDENT_MAX_LEVEL = 6


def extract_doc_line_indents(raw_data):
    """从原始 services[].documents 读出每行缩进，按 line_id 返回。

    必须在 validate_and_normalize_data() 之前调用：quotation_schema 对每个条目做 item.strip()，
    行首空白在归一化时会被剥掉，因此必须先提取。
    """
    out = {}
    if not isinstance(raw_data, dict):
        return out
    for service in raw_data.get('services') or []:
        if not isinstance(service, dict):
            continue
        line_id, docs = service.get('line_id'), service.get('documents')
        if not isinstance(line_id, str) or not isinstance(docs, list):
            continue
        line_id = line_id.strip()
        levels = []
        for item in docs:
            lead = len(item) - len(item.lstrip('\u3000')) if isinstance(item, str) else 0
            levels.append(min(lead, DOC_LINE_INDENT_MAX_LEVEL) * DOC_LINE_INDENT_TWIPS)
        out[line_id] = levels
    return out


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
                        help='Signing entity (required): ' + '/'.join(ENTITY_CONFIG))
    parser.add_argument('--output', default=None, help='Output .docx path (default: CWD)')
    parser.add_argument('--data', required=True, help='Quotation data file (.json)')
    parser.add_argument('--title-line1', default=None, help='Title first line (default: quote_meta.title_line1 or 报价单)')
    parser.add_argument('--title-line2', default=None, help='Title second line (default: quote_meta.title_line2 or 服务方案)')
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
        # 行缩进层级必须在归一化（item.strip() 会吃掉行首空白）之前从原始数据取
        doc_line_indents = extract_doc_line_indents(raw_quotation)
        quotation_data = validate_and_normalize_data(raw_quotation, data_path=args.data)
    except ValueError as exc:
        print(f'❌ {exc}', file=sys.stderr)
        sys.exit(2)
    for warning in quotation_data['warnings']:
        print(f'⚠️  WARNING: {warning}')

    try:
        TEMPLATE = template_path_for_entity(entity_cfg)
    except ValueError as exc:
        print(f'❌ {exc}', file=sys.stderr)
        sys.exit(2)
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
        # DrawingML / VML / OOXML extension namespaces used in template headers
        # (anchored logo, decorative shapes). Registering them keeps stdlib
        # ElementTree from renaming prefixes to ns0/ns1 on round-trip, which
        # would break mc:Ignorable and mc:Choice@Requires references.
        'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
        'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
        'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
        'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
        'wp14': 'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing',
        'wpc': 'http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas',
        'wpg': 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup',
        'wpi': 'http://schemas.microsoft.com/office/word/2010/wordprocessingInk',
        'wne': 'http://schemas.microsoft.com/office/word/2006/wordml',
        'w10': 'urn:schemas-microsoft-com:office:word',
        'wpsCustomData': 'http://www.wps.cn/officeDocument/2013/wpsCustomData',
        'v': 'urn:schemas-microsoft-com:vml',
        'o': 'urn:schemas-microsoft-com:office:office',
        'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
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

    def _set_run_size(rpr, sz):
        """Set (or add) w:sz / w:szCs on an rPr element. ``sz`` is half-points."""
        for tag in (w('sz'), w('szCs')):
            el = rpr.find(tag)
            if el is None:
                el = ET.SubElement(rpr, tag)
            el.set(w('val'), sz)

    # Schema order of w:sectPr children (ECMA-376). Used to insert a missing
    # pgSz/pgMar at a position Word accepts, rather than appending it at the end.
    _SECTPR_ORDER = (
        'headerReference', 'footerReference', 'footnotePr', 'endnotePr', 'type',
        'pgSz', 'pgMar', 'paperSrc', 'pgBorders', 'lnNumType', 'pgNumType', 'cols',
        'formProt', 'vAlign', 'noEndnote', 'titlePg', 'textDirection', 'bidi',
        'rtlGutter', 'docGrid', 'printerSettings', 'sectPrChange',
    )

    def _ensure_sectpr_child(sectPr, tag):
        """Return sectPr's `tag` child, creating it at its schema-correct position."""
        existing = sectPr.find(w(tag))
        if existing is not None:
            return existing
        el = ET.Element(w(tag))
        rank = _SECTPR_ORDER.index(tag)
        for i, child in enumerate(sectPr):
            name = child.tag.split('}')[-1]
            if name in _SECTPR_ORDER and _SECTPR_ORDER.index(name) > rank:
                sectPr.insert(i, el)
                return el
        sectPr.append(el)
        return el

    def normalize_page_setup(sectPr):
        """Force A4 portrait + canonical margins on a template's sectPr.

        The template's sectPr is otherwise copied verbatim, so a drifted or
        non-A4 template would silently produce a quotation that doesn't print
        on A4. Returns the page size the template actually had, so the caller
        can log the truth instead of asserting A4 unconditionally.
        """
        pgSz = _ensure_sectpr_child(sectPr, 'pgSz')
        original = (pgSz.get(w('w')), pgSz.get(w('h')))
        pgSz.set(w('w'), str(A4_PAGE_W))
        pgSz.set(w('h'), str(A4_PAGE_H))
        # Absent means portrait; a stale landscape flag would contradict A4 w/h.
        pgSz.attrib.pop(w('orient'), None)

        pgMar = _ensure_sectpr_child(sectPr, 'pgMar')
        for name, value in A4_MARGINS.items():
            pgMar.set(w(name), str(value))
        return original

    def _set_paragraph_jc(paragraph, jc):
        """Set (or add) w:jc on a paragraph's pPr, e.g. 'center' or 'right'."""
        pPr = paragraph.find(w('pPr'))
        if pPr is None:
            pPr = ET.SubElement(paragraph, w('pPr'))
        jc_el = pPr.find(w('jc'))
        if jc_el is None:
            jc_el = ET.SubElement(pPr, w('jc'))
        jc_el.set(w('val'), jc)

    def replace_paragraph_text(paragraph, value, sz=None):
        """Replace text runs in a paragraph while preserving drawing/image runs.
        Finds the first text run's rPr and uses it for the new text content.
        Falls back to make_run(value, sz='24') if no text run rPr found.
        When ``sz`` (half-point string, e.g. '16') is given, the run's font size
        is overridden to that value — used to shrink over-long header addresses."""
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
            rpr_copy = ET.fromstring(ET.tostring(orig_rpr))
            if sz is not None:
                _set_run_size(rpr_copy, sz)
            new_run = ET.Element(w('r'))
            new_run.append(rpr_copy)
            t = ET.SubElement(new_run, w('t'))
            t.text = xml_safe_text(value)
            new_children.append(new_run)
        else:
            new_children.append(make_run(value, sz=sz if sz is not None else '24'))
        # Rebuild paragraph
        paragraph.clear()
        for child in new_children:
            paragraph.append(child)
        return True

    def _add_header_image_relationship(unpack_dir, img_src):
        """把横幅图放进 word/media，并给 header1.xml.rels 加一条 image 关系，返回 rId。

        直接改 rels 文本而不是过 ElementTree，是为了原样保留模板 rels 的写法
        （默认命名空间、属性顺序），避免 ElementTree 重排成 ns0:Relationship。
        """
        media_dir = os.path.join(unpack_dir, 'word', 'media')
        os.makedirs(media_dir, exist_ok=True)
        shutil.copyfile(img_src, os.path.join(media_dir, 'header_banner.png'))

        rels_path = os.path.join(unpack_dir, 'word', '_rels', 'header1.xml.rels')
        with open(rels_path, encoding='utf-8') as fh:
            rels = fh.read()
        used = [int(m) for m in re.findall(r'Id="rId(\d+)"', rels)]
        rid = f'rId{max(used) + 1 if used else 1}'
        rels = rels.replace(
            '</Relationships>',
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/image" Target="media/header_banner.png"/>'
            '</Relationships>',
        )
        with open(rels_path, 'w', encoding='utf-8') as fh:
            fh.write(rels)
        return rid

    def apply_header_image(unpack_dir, entity_key, cfg, spec):
        """页眉改用整幅横幅图，保留模板原有的蓝色分隔线。

        横幅与蓝线锚在**同一段**，两个偏移量都从该段顶端起算 —— 沿用模板原有的
        logo/蓝线不变式（SKILL.md 流程规则 #4）。横幅用浮动锚定（wp:anchor +
        wrapNone）而非内联：内联时图片自带的透明边距照样占版面，页眉压不下去。
        锚定之后再按 PNG 的 alpha 边界用 a:srcRect 裁掉透明边距（PNG 文件本身不动），
        图片框因此就是图墨迹本身 —— 既不会伸到纸张上缘之外，也不会越过正文栏。

        模板的蓝线形状原样搬运（含中国模板的 mc:AlternateContent 包装），只改它的
        positionV 偏移 —— 粗细、颜色、虚线样式都与文字页眉主体完全一致。
        """
        header_path = os.path.join(unpack_dir, 'word', 'header1.xml')
        if not os.path.exists(header_path):
            print("⚠️  WARNING: word/header1.xml not found; cannot apply header image.")
            return

        img_src = os.path.join(SKILL_DIR, 'assets', spec['file'])
        if not os.path.exists(img_src):
            sys.exit(f'❌ 页眉图片不存在：{img_src}（检查 config/entities.json 的 header_image.file）')

        geom = header_image_geometry(spec, img_src)
        if spec['ink_top_mm'] < MIN_PRINTABLE_INK_TOP_MM:
            print(f"⚠️  WARNING: 页眉图墨迹顶距纸张上缘仅 {spec['ink_top_mm']}mm "
                  f"(< {MIN_PRINTABLE_INK_TOP_MM}mm) — 多数打印机不可打印区约 4.2mm，logo 顶部可能被切")

        WP_NS, WPS_NS = NS['wp'], NS['wps']
        A_NS = NS['a']
        PIC_NS = NS['pic']

        tree = ET.parse(header_path)
        root = tree.getroot()
        line_drawing = None
        for drawing in root.iter(w('drawing')):
            if drawing.find(f'.//{{{WPS_NS}}}wsp') is not None:
                line_drawing = drawing
        if line_drawing is None:
            sys.exit('❌ 模板页眉里找不到蓝色分隔线（wps 形状），无法保留蓝线')
        pos = line_drawing.find(f'{{{WP_NS}}}anchor/{{{WP_NS}}}positionV/{{{WP_NS}}}posOffset')
        if pos is None:
            sys.exit('❌ 蓝线锚点结构异常：找不到 wp:positionV/wp:posOffset')
        pos.text = str(geom['line_off_v'])
        # line_drawing 下面会被挂进全新的页眉树；原树不再序列化，不必先摘除。

        rid = _add_header_image_relationship(unpack_dir, img_src)
        cx, cy = geom['extent_cx'], geom['extent_cy']
        sr = geom['src_rect']
        banner = ET.fromstring(
            f'<w:drawing xmlns:w="{W}" xmlns:wp="{WP_NS}" xmlns:a="{A_NS}" '
            f'xmlns:pic="{PIC_NS}" xmlns:r="{NS["r"]}">'
            '<wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0"'
            ' relativeHeight="251658240" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
            '<wp:simplePos x="0" y="0"/>'
            f'<wp:positionH relativeFrom="column"><wp:posOffset>{geom["banner_off_h"]}</wp:posOffset></wp:positionH>'
            f'<wp:positionV relativeFrom="paragraph"><wp:posOffset>{geom["banner_off_v"]}</wp:posOffset></wp:positionV>'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
            '<wp:docPr id="901" name="HeaderBanner"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic><pic:nvPicPr><pic:cNvPr id="901" name="HeaderBanner"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/>'
            f'<a:srcRect l="{sr["l"]}" t="{sr["t"]}" r="{sr["r"]}" b="{sr["b"]}"/>'
            '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing>'
        )

        # 文字段落全部丢弃（文字已由图片承载），只留承载横幅 + 蓝线的那一段。
        # 该段的 space-after 决定正文首行位置，也就是页眉占用的总高度。
        hdr = ET.Element(w('hdr'))
        para = ET.SubElement(hdr, w('p'))
        pPr = ET.SubElement(para, w('pPr'))
        spacing = ET.SubElement(pPr, w('spacing'))
        spacing.set(w('before'), '0')
        spacing.set(w('after'), str(geom['reserve_twips']))
        _set_run_size(ET.SubElement(pPr, w('rPr')), '2')
        for element in (banner, line_drawing):
            run = ET.SubElement(para, w('r'))
            # 两个 run 用同一套 rPr：行高参与该段总高计算，不一致会让正文下移
            # （SKILL.md 流程规则 #4 的同一条约束）。
            _set_run_size(ET.SubElement(run, w('rPr')), '2')
            run.append(element)

        ET.ElementTree(hdr).write(header_path, xml_declaration=True, encoding='UTF-8')
        print(f"✅ 页眉改用横幅图 {spec['file']}"
              f"（按 alpha 边界裁掉透明边距 左{sr['l'] / 1000:.1f}% 上{sr['t'] / 1000:.1f}% "
              f"右{sr['r'] / 1000:.1f}% 下{sr['b'] / 1000:.1f}%；"
              f"图片框 {geom['ink_left_mm']:.1f}~{geom['ink_left_mm'] + geom['ink_w_mm']:.1f}mm × "
              f"{spec['ink_top_mm']:.1f}~{geom['ink_bottom_mm']:.1f}mm，"
              f"蓝线 {geom['line_mm']:.1f}mm / 正文首行 {spec['body_top_mm']}mm）")

    def apply_header_logo_shift(unpack_dir, entity_key, cfg, spec):
        """把文字页眉的 logo 沿页眉末段顶端下移 spec['shift_mm']（mm）。

        logo 与蓝线浮动锚定在**同一段**，两个偏移量都从该段顶端起算，所以只改 logo 的
        wp:positionV 即可 —— 蓝线原样不动：蓝线落点决定正文首行能避开多少（末段的高度
        就是这份余量），挪它会压到正文「公司名称：」。

        下移后 logo 墨迹的可见底边与蓝线之间必须仍有净空，否则蓝线压在 logo 上。净空按
        PNG 的 alpha 边界算，不按 wp:extent —— 位图自带的透明留白不参与视觉。"""
        shift_mm = spec['shift_mm']
        if not shift_mm:
            return

        header_path = os.path.join(unpack_dir, 'word', 'header1.xml')
        if not os.path.exists(header_path):
            print("⚠️  WARNING: word/header1.xml not found; cannot shift header logo.")
            return

        WP_NS, A_NS, R_NS = NS['wp'], NS['a'], NS['r']
        tree = ET.parse(header_path)
        root = tree.getroot()
        logo = line = None
        for anchor in root.iter(f'{{{WP_NS}}}anchor'):
            if anchor.find(f'.//{{{A_NS}}}blip') is not None:
                logo = anchor
            else:
                line = anchor
        if logo is None:
            print(f"⚠️  WARNING: {entity_key} 页眉里找不到 logo 锚点（含 a:blip 的 wp:anchor），"
                  f"跳过 logo 下移。")
            return

        pos = logo.find(f'{{{WP_NS}}}positionV/{{{WP_NS}}}posOffset')
        extent = logo.find(f'{{{WP_NS}}}extent')
        if pos is None or pos.text is None or extent is None:
            print(f"⚠️  WARNING: {entity_key} 页眉 logo 锚点缺少 positionV/posOffset 或 extent，"
                  f"跳过 logo 下移。")
            return

        # 墨迹可见高度 = wp:extent 减掉位图底部的透明留白。
        box_emu = int(extent.get('cy'))
        ink_emu = box_emu
        blip = logo.find(f'.//{{{A_NS}}}blip')
        rid = blip.get(f'{{{R_NS}}}embed') if blip is not None else None
        rels_path = os.path.join(unpack_dir, 'word', '_rels', 'header1.xml.rels')
        if rid and os.path.exists(rels_path):
            with open(rels_path, encoding='utf-8') as fh:
                match = re.search(r'Id="%s"[^>]*Target="([^"]*)"' % re.escape(rid), fh.read())
            if match:
                media = os.path.join(unpack_dir, 'word', match.group(1).replace('../', ''))
                if os.path.isfile(media):
                    with open(media, 'rb') as fh:
                        try:
                            ink_emu = int(round(box_emu * (1 - png_alpha_bottom_padding(fh.read()))))
                        except ValueError:
                            pass

        new_off = int(pos.text) + int(round(shift_mm * EMU_PER_MM))
        gap_txt = ''
        if line is not None:
            line_pos = line.find(f'{{{WP_NS}}}positionV/{{{WP_NS}}}posOffset')
            if line_pos is not None and line_pos.text is not None:
                gap_mm = (int(line_pos.text) - (new_off + ink_emu)) / EMU_PER_MM
                gap_txt = f"，logo 底距蓝线 {gap_mm:.2f}mm"
                if gap_mm < MIN_LOGO_LINE_GAP_MM:
                    print(f"⚠️  WARNING: {entity_key} 页眉 logo 下移 {shift_mm}mm 后距蓝线仅 "
                          f"{gap_mm:.2f}mm (< {MIN_LOGO_LINE_GAP_MM}mm)，两者会视觉粘连；"
                          f"请调小 config/entities.json 的 header_logo_shift_mm。")
        pos.text = str(new_off)

        tree.write(header_path, xml_declaration=True, encoding='UTF-8')
        print(f"✅ 页眉 logo 下移 {shift_mm}mm{gap_txt}（蓝线位置不变）")

    def apply_header(unpack_dir, entity_key):
        """Update the template header from entity-config ``header_lines``.

        Replaces the entire text content of existing text paragraphs — preserving
        logo/drawing runs, hyperlink fields, and decorative shapes — rather than
        just the last w:t element, so it works correctly even with multi-run text.
        Applied to every entity so the header text is driven solely by
        config/entities.json ``header_lines``.

        Entities configured with ``header_image`` skip all of that and get the
        banner-image header instead — see apply_header_image above."""
        cfg = ENTITY_CONFIG.get(entity_key, {})
        img_spec = header_image_spec(cfg, ENTITY_META.get('header_image_defaults'))
        if img_spec:
            apply_header_image(unpack_dir, entity_key, cfg, img_spec)
            return
        header_lines = cfg.get('header_lines')
        if not header_lines:
            print(f"⚠️  WARNING: No header override configured for entity={entity_key}; template header is unchanged.")
            return

        header_path = os.path.join(unpack_dir, 'word', 'header1.xml')
        if not os.path.exists(header_path):
            print("⚠️  WARNING: word/header1.xml not found; cannot update template header.")
            return

        tree = ET.parse(header_path)
        root = tree.getroot()
        paragraphs = [p for p in list(root) if p.tag == w('p')]
        text_paragraphs = [p for p in paragraphs if paragraph_text(p).strip()]

        header_addr_size = cfg.get('header_address_size_pt')
        addr_sz = str(int(round(header_addr_size * 2))) if header_addr_size else None

        lines_to_write = [l for l in header_lines if l]
        for i, line in enumerate(lines_to_write):
            # Company name is line 0, Web line is last — the address lines sit in
            # between. Shrink an over-long header address to the configured size.
            is_address = 0 < i < len(lines_to_write) - 1
            sz_override = addr_sz if (is_address and addr_sz) else None
            if i < len(text_paragraphs):
                replace_paragraph_text(text_paragraphs[i], line, sz=sz_override)
            else:
                new_p = make_para([make_run(line, sz=sz_override or '24')], spacing_after=0, line='280')
                root.append(new_p)
                text_paragraphs.append(new_p)

        surplus_start = len(lines_to_write)
        # Clear text from surplus text paragraphs (only reachable if a template
        # has more text paragraphs than header_lines), turning them empty.
        for p in text_paragraphs[surplus_start:]:
            for run in p.findall(w('r')):
                if run.find(w('t')) is not None and run.find(w('drawing')) is None:
                    p.remove(run)

        # Company name alignment: Singapore & Egypt right-aligned, all others
        # centered (the user's template layout rule).
        company_align = cfg.get('header_company_align', 'center')
        if company_align and text_paragraphs:
            _set_paragraph_jc(text_paragraphs[0], company_align)

        # Every template uses one compact layout: the text paragraphs are followed
        # by exactly one paragraph carrying the floating blue separator line. That
        # paragraph's own height is what keeps the body's first line clear of the
        # line, and the line's anchor is paragraph-relative — so resizing, removing
        # or adding paragraphs here moves the line onto the body. Only the header
        # text above is ever rewritten.

        tree.write(header_path, xml_declaration=True, encoding='UTF-8')
        print(f"✅ Updated template header for {cfg['company']}")

        # 文字段落的排版到此为止；logo 的垂直位置单独调整（图片页眉在上面就返回了）。
        logo_spec = header_logo_spec(cfg, ENTITY_META.get('header_logo_defaults'))
        if logo_spec:
            apply_header_logo_shift(unpack_dir, entity_key, cfg, logo_spec)

    def make_rpr(font=None, sz='24', bold=False, color=None, hint='eastAsia'):
        """Create a w:rPr element matching template pattern."""
        if font is None:
            font = FONT_NAME
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

    def make_run(text, font=None, sz='24', bold=False, color=None, hint='eastAsia'):
        """Create a w:r element.

        ElementTree 只转义 &<>，不会剔除 XML 非法字符，所以文本一律先过
        xml_safe_text()，否则控制字符会让 document.xml 无法解析。
        """
        if font is None:
            font = FONT_NAME
        r = ET.Element(w('r'))
        r.append(make_rpr(font, sz, bold, color, hint))
        t = ET.SubElement(r, w('t'))
        t.text = xml_safe_text(text)
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
        r1.append(make_rpr(FONT_NAME, SZ_BODY))
        t1 = ET.SubElement(r1, w('t'))
        t1.text = xml_safe_text(label)
        r2 = ET.Element(w('r'))
        r2.append(make_rpr(FONT_NAME, SZ_BODY))
        t2 = ET.SubElement(r2, w('t'))
        t2.text = xml_safe_text(value)
        return make_para([r1, r2], spacing_after=0, line='280')

    def make_section_header(text):
        """Make a section header like '1.服务内容' - bold, 14pt, black."""
        return make_para(
            [make_run(text, sz=SZ_SECTION, bold=True, color='000000')],
            spacing_after=0, line='280'
        )

    def make_title(text_part1, text_part2):
        """Make the blue centered title with bottom border.
        Run-level styles are set on the runs; paragraph-level only sets spacing/alignment/border."""
        return make_para(
            [
                make_run(text_part1, sz=SZ_TITLE_L, bold=True, color='4472C4'),
                make_run(text_part2, sz=SZ_TITLE_S, bold=True, color='4472C4'),
            ],
            spacing_before=200, spacing_after=280, line='280',
            jc='center', indent_left=936, indent_right=936,
            border_bottom_color='4472C4'
        )

    # ====== TABLE BUILDING ======
    # Template table style
    def make_tbl_pr(total_width=None):
        """Create table properties matching template.
        total_width: overrides default 9790; set to sum of gridCol for consistent layout."""
        tblPr = ET.Element(w('tblPr'))

        style = ET.SubElement(tblPr, w('tblStyle'))
        style.set(w('val'), '11')

        tblW = ET.SubElement(tblPr, w('tblW'))
        tblW.set(w('w'), str(total_width or 9790))
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
                [make_run(text, sz=SZ_BODY, bold=True)],
                spacing_after=0, line='280', jc='center'
            )],
            width, fill='BDD6EE', valign='center'
        )

    def make_data_cell(text, width, bold=False, jc=None, small=False, price=False, indents=None):
        """Create a data cell. text can be a string or list of strings (each becomes a paragraph).
        small=True: 11pt for notes/documents (10.5pt for non-China). price=True: 10pt for price column.
        indents: 与 text 等长的左缩进列表（twips，来自 extract_doc_line_indents）；
                 0/None 表示该行顶格（不写 w:ind）。仅当 text 是列表时生效。"""
        if small:
            sz = SZ_SMALL
        elif price:
            sz = SZ_PRICE
        else:
            sz = SZ_BODY
        if isinstance(text, list):
            paras = []
            for idx, line in enumerate(text):
                indent = indents[idx] if (indents and idx < len(indents)) else 0
                paras.append(make_para(
                    [make_run(line, sz=sz, bold=bold)],
                    spacing_after=0, line='280', jc=jc,
                    indent_left=(indent or None)
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
                [make_run(text, sz=SZ_BODY, bold=True)],
                spacing_after=0, line='280', jc='center'
            )],
            sum(COLS), span=len(COLS), valign='center'
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
    # Service content table column widths — unified across all templates
    # (序号, 服务内容, 数量, 价格, 备注)
    COLS = [431, 2714, 765, 1835, 4289]
    services_data = quotation_data['services']

    def with_code(service):
        return f'{service["code"]}-{service["name"]}'

    # 服务内容表没有办理时间列，其下 *备注： 区块不得出现办理时间免责声明。validate 会拦截，
    # 这里再兜底剔除一次，保证任何来源（沿用旧 quotation.json / 手工编辑）的数据都不会把它印进去。
    notes = []
    for note_text, note_indent in quotation_data['notes']:
        if is_process_time_disclaimer(note_text):
            print(f"⚠️  WARNING: 已剔除备注中的办理时间免责声明（服务内容表无办理时间列）: {note_text}",
                  file=sys.stderr)
            continue
        notes.append((note_text, note_indent))

    quote_meta = quotation_data.get('quote_meta', {})
    DISCOUNT_AMOUNT_INT = quotation_data['discount_amount']

    preserved_payment_terms = None
    preserve_payment_source = args.preserve_payment_from
    if not preserve_payment_source and os.path.exists(OUTPUT):
        preserve_payment_source = OUTPUT
    if preserve_payment_source and not args.overwrite_payment_terms:
        try:
            preserved_payment_terms = extract_payment_terms(os.path.abspath(preserve_payment_source))
            print(f"✅ 付款方式来源：旧报价单 {os.path.abspath(preserve_payment_source)}"
                  f"（如需改用 quotation.json 的条款，加 --overwrite-payment-terms）")
        except Exception as exc:
            print(
                f"❌ 无法从旧报价单保留付款方式: {preserve_payment_source}: {exc}。"
                "请询问用户并确认付款方式；用户明确提供后，将付款方式写入本次 quotation.json，"
                "并使用 --overwrite-payment-terms 重新生成。",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        # 没有旧 .docx 可沿用：说清楚条款从哪来，避免「手改过的付款方式」被静默换掉。
        if quote_meta.get('payment_terms'):
            print("付款方式来源：quotation.json（quote_meta.payment_terms）")
        else:
            print(f"付款方式来源：实体默认配置（{entity}）")
        if args.overwrite_payment_terms and preserve_payment_source:
            print(f"（--overwrite-payment-terms：已忽略旧报价单 {preserve_payment_source} 的付款方式）")
        # 修改报价单时按流程应输出新文件；此时同目录旧单子上的手改付款方式不会自动沿用。
        if not args.preserve_payment_from and not os.path.exists(OUTPUT):
            out_dir = os.path.dirname(OUTPUT) or os.getcwd()
            try:
                siblings = sorted(
                    n for n in os.listdir(out_dir)
                    if n.lower().endswith('.docx') and not n.startswith('~$')
                )[:3]
            except OSError:
                siblings = []
            if siblings:
                print("⚠️  提示：本次输出的是新文件，同目录已有报价单（" + "、".join(siblings) +
                      "）；它们上面手改过的付款方式不会自动沿用，"
                      "若需沿用请传 --preserve-payment-from <旧文件>。")

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
    if not is_target_currency(CURRENCY):
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
    VAT_RATE = Decimal(str(entity_cfg['vat_rate']))

    vat_label_pct = vat_percent_label(VAT_RATE)
    TAX_LABEL = entity_cfg.get('tax_label', '增值税')
    VAT_LABEL = f"{TAX_LABEL} {vat_label_pct}"
    VAT_NOTE = None
    VAT_NOTE = entity_cfg.get('tax_note')

    SUBTOTAL_D = sum(service['price_int'] for service in services_data)

    # Price magnitude guard — catch RMB/IDR data mix-up (shared with validate_data.py)
    all_prices = [service['price_int'] for service in services_data]
    for warning in price_magnitude_warnings(all_prices, CURRENCY):
        print(f"⚠️  WARNING: {warning}")

    # Withholding tax: enabled via quotation.json `withholding_tax: true`
    # Only applied if entity config defines a withholding_tax_rate (currently thailand only)
    WITHHOLDING_TAX_RATE = entity_cfg.get('withholding_tax_rate')
    WITHHOLDING_ENABLED = False
    if WITHHOLDING_TAX_RATE is not None:
        WITHHOLDING_ENABLED = bool(quotation_data.get('withholding_tax', False))
        if 'withholding_tax' not in raw_quotation:
            print("⚠️  WARNING: entity supports withholding tax (withholding_tax_rate set) but "
                  "quotation.json has no top-level `withholding_tax` field. WHT will NOT be "
                  "deducted. Add `withholding_tax: true` to the TOP level of quotation.json "
                  "(sibling of discount_amount, NOT inside _meta) to apply it.")
    elif quotation_data.get('withholding_tax', False):
        # 未配置 withholding_tax_rate 的主体写 true：以前这里什么都不说，报价单不出现预扣税行，
        # 直到 verify 比对 quotation.json 的 flag 才报错。明确喊出来，别让它拖到最后一关。
        print(f"⚠️  WARNING: quotation.json 顶层设置了 `withholding_tax: true`，但 --entity {entity} "
              f"未配置 withholding_tax_rate（预扣税目前仅泰国主体支持），本次生成不会扣除预扣税。"
              f"请删除该字段或改为 false。")

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

    print(f"Entity: {entity} | Currency: {CURRENCY} | {TAX_LABEL}: {vat_label_pct}")
    print(f"Subtotal: {fmt_price_int(SUBTOTAL_D)} | Discount: {fmt_price_int(DISCOUNT_D)} | Discounted: {fmt_price_int(DISCOUNTED_D)}")
    wht_info = f" | WHT: {fmt_price_int(WHT_D)}" if WITHHOLDING_ENABLED and WHT_D is not None else ""
    print(f"{TAX_LABEL}: {fmt_price_vat(VAT_D)}{wht_info} | Total: {fmt_price_total(GRAND_TOTAL_D)}")

    # Font sizes and name — unified across all templates: 仿宋 10pt body
    FONT_NAME = '仿宋'   # Chinese font name (same as FangSong, but matches template XML)
    SZ_BODY = '20'       # 10pt — info lines, service names, table headers, data cells
    SZ_SMALL = '22'      # 11pt — notes, process, documents
    SZ_PRICE = '20'      # 10pt — price column
    SZ_SECTION = '28'    # 14pt bold — section headers
    SZ_TITLE_L = '40'    # 20pt bold — title part 1
    SZ_TITLE_S = '40'    # 20pt bold — title part 2

    SZ_BANK = '22'        # 11pt — 银行信息（所有模版统一）
    SZ_VAT_MAIN = '20'    # 10pt — 增值税主体标签（所有模版统一）
    SZ_VAT_NOTE = '16'    # 8pt — 增值税税率注释（如泰国「以开发票时泰国现行税率为准」）

    # ====== BUILD BODY CONTENT ======
    body_children = []

    # 1. Header Info Lines — pad labels so colons align vertically.
    # CJK chars are 2 display-width each; target 8 display-width before the colon.
    def pad_label(text, target_dw=8):
        """Pad text with ASCII spaces so its display width equals target_dw."""
        current_dw = sum(2 if ord(c) > 127 else 1 for c in text)
        return text + ' ' * max(0, target_dw - current_dw)

    body_children.append(make_info_line(pad_label('公司名称') + '：', quote_meta.get('customer_name', '')))
    body_children.append(make_info_line(pad_label('联系人') + '：', quote_meta.get('contact_name', '')))
    body_children.append(make_info_line(pad_label('联系方式') + '：', quote_meta.get('contact_info', '')))
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
    body_children.append(make_info_line(pad_label('报价日期') + '：', QUOTE_DATE))
    body_children.append(make_info_line(pad_label('合同号') + '：', quote_meta.get('contract_no', '')))

    # 2. Title: CLI override → quote_meta → defaults
    title_line1 = args.title_line1 or quote_meta.get('title_line1') or '报价单'
    title_line2 = args.title_line2 or quote_meta.get('title_line2') or '服务方案'
    body_children.append(make_title(title_line1, title_line2))

    # 3. Section Header
    body_children.append(make_section_header('1.服务内容'))

    # 4. Service Content Table
    tbl = ET.Element(w('tbl'))
    tbl.append(make_tbl_pr(sum(COLS)))
    tbl.append(make_tbl_grid(COLS))

    # Header row
    hdr_row = ET.Element(w('tr'))
    hdr_trPr = ET.SubElement(hdr_row, w('trPr'))
    ET.SubElement(hdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(hdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')

    price_label = f'价格\n({CURRENCY_NAMES.get(CURRENCY, CURRENCY)})'
    hdr_texts = ['序号', '服务内容', '数量', price_label, '备注']
    for i, ht in enumerate(hdr_texts):
        lines = ht.split('\n')
        if len(lines) > 1:
            paras = []
            for line in lines:
                paras.append(make_para(
                    [make_run(line, sz=SZ_BODY, bold=True)],
                    spacing_after=0, line='280', jc='center'
                ))
            hdr_row.append(make_tc(paras, COLS[i], fill='BDD6EE', valign='center'))
        else:
            hdr_row.append(make_hdr_cell(ht, COLS[i]))
    tbl.append(hdr_row)

    # Service rows
    seq = 0
    current_category = object()
    for service in services_data:
        if service['category'] != current_category:
            current_category = service['category']
            if current_category:
                tbl.append(make_category_row(current_category))
        seq += 1
        price_display = format_price_display(service['price'], CURRENCY)
        cells = [
            make_data_cell(str(seq), COLS[0], jc='center'),
            make_data_cell(with_code(service), COLS[1]),
            make_data_cell(f"{service['quantity']} {service['unit']}", COLS[2], jc='center'),
            make_data_cell(price_display, COLS[3], jc='right', price=True),
            make_data_cell(service['note'].split('\n') if '\n' in service['note'] else service['note'], COLS[4], small=True),
        ]
        tbl.append(make_table_row(cells))

    # Summary rows — all use Decimal values
    # 汇总行布局（label 右对齐、金额左对齐，用模板原始 XML 核验）：
    #   默认统一：label span3 + amount span2（中国/新加坡/德音模板一致）
    #   泰国模板：小计行 = 空 c0 + label span2 + amount span2；税/总计行 = label span3 + amount span2
    #   雅加达模板为 6 列(span4/span2)，builder 生成 5 列，统一适配 span3/span2
    #   布局元组 = (leading_empty, label_span, amount_span, amount_align)
    DEFAULT_LAYOUT = (0, 3, 2, 'left')
    configured_layout = entity_cfg.get('summary_layout', {})
    subtotal_layout = tuple(configured_layout.get('subtotal', DEFAULT_LAYOUT))
    other_layout = tuple(configured_layout.get('other', DEFAULT_LAYOUT))

    def summary_row(label, value_d, fmt='int', highlight=False, note=None, label_sz=SZ_BODY):
        if fmt == 'vat':
            formatted = fmt_price_vat(value_d)
        elif fmt == 'total':
            formatted = fmt_price_total(value_d)
        elif fmt == 'tax':
            # 预扣税等税金：按币种保留小数（RMB/USD/THB/SGD 仅在有小数时显示小数位，IDR/VND 取整）
            formatted = fmt_price_vat(value_d)
        else:
            formatted = fmt_price_int(value_d)
        leading, label_span, amount_span, amount_align = (
            subtotal_layout if label == '小计' else other_layout)
        label_w = sum(COLS[leading:leading + label_span])
        amount_w = sum(COLS[leading + label_span:leading + label_span + amount_span])
        cells = []
        if leading:
            cells.append(make_empty_cell(COLS[0]))
        label_runs = [make_run(label, sz=label_sz)]
        if note:
            label_runs.append(make_run(note, sz=SZ_VAT_NOTE))
        cells.append(make_tc(
            [make_para(
                label_runs,
                spacing_after=0, line='280', jc='right'
            )],
            label_w, span=label_span, valign='center'
        ))
        cells.append(make_tc(
            [make_para(
                [make_run(formatted, sz=SZ_PRICE)],
                spacing_after=0, line='280', jc=amount_align
            )],
            amount_w, span=amount_span, valign='center',
            fill=('F2F2F2' if highlight else None)
        ))
        return make_table_row(cells)

    tbl.append(summary_row('小计', SUBTOTAL_D, fmt='int'))
    if DISCOUNT_D > 0:
        tbl.append(summary_row('优惠金额', DISCOUNT_D, fmt='int'))
    tbl.append(summary_row(VAT_LABEL, VAT_D, fmt='vat', note=VAT_NOTE, label_sz=SZ_VAT_MAIN))
    if WITHHOLDING_ENABLED and WHT_D is not None:
        wht_label = f"预扣税 {int(WITHHOLDING_TAX_RATE * 100)}%"
        tbl.append(summary_row(wht_label, -WHT_D, fmt='tax'))
    tbl.append(summary_row('含税总计', GRAND_TOTAL_D, fmt='total', highlight=True))

    body_children.append(tbl)

    # 5. Notes section
    body_children.append(make_para('', spacing_after=0, line='280'))
    body_children.append(make_para(
        [make_run('*备注：', sz=SZ_BODY, bold=True)],
        spacing_after=0, line='280'
    ))

    for note_text, indent in notes:
        body_children.append(make_para(
            [make_run(note_text, sz=SZ_SMALL)],
            spacing_after=0, line='280', indent_left=indent
        ))

    for i, service in enumerate(services_data):
        fd = service['fees']
        body_children.append(make_para(
            [make_run(f'{i+1}. {with_code(service)}', sz=SZ_SMALL, bold=True)],
            spacing_before=40, spacing_after=0, line='280', indent_left=360
        ))
        body_children.append(make_para(
            [make_run('费用包含：', sz=SZ_SMALL, bold=True)],
            spacing_after=0, line='280', indent_left=360
        ))
        for item in fd['include']:
            body_children.append(make_para(
                [make_run(item, sz=SZ_SMALL)],
                spacing_after=0, line='280', indent_left=540
            ))
        if fd['exclude']:
            body_children.append(make_para(
                [make_run('费用不含：', sz=SZ_SMALL, bold=True)],
                spacing_after=0, line='280', indent_left=360
            ))
            for item in fd['exclude']:
                body_children.append(make_para(
                    [make_run(item, sz=SZ_SMALL)],
                    spacing_after=0, line='280', indent_left=540
                ))
        if fd['note']:
            body_children.append(make_para(
                [make_run('备注：', sz=SZ_SMALL, bold=True)],
                spacing_after=0, line='280', indent_left=360
            ))
            for n_line in fd['note'].split('\n'):
                body_children.append(make_para(
                    [make_run(n_line, sz=SZ_SMALL)],
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
            [make_run(term, sz=SZ_SMALL)],
            spacing_after=0, line='280', indent_left=360
        ))

    # 7. Process & Deliverables Table
    body_children.append(make_para('', spacing_before=0, spacing_after=0))
    process_title = '3.服务流程、时间及交付文件清单'
    body_children.append(make_section_header(process_title))
    body_children.append(make_para('', spacing_before=0, spacing_after=120))

    PCOLS = [596, 2249, 872, 3679, 2671]  # 序号, 项目, 时间工作日, 流程, 服务完成后交付文件（与中国模板 gridCol 一致）

    ptbl = ET.Element(w('tbl'))
    ptbl.append(make_tbl_pr(sum(PCOLS)))
    ptbl.append(make_tbl_grid(PCOLS))

    phdr_row = ET.Element(w('tr'))
    phdr_trPr = ET.SubElement(phdr_row, w('trPr'))
    ET.SubElement(phdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(phdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')
    process_headers = ['序号', '服务内容', '办理时间', '流程', '服务完成后交付文件']
    for i, ht in enumerate(process_headers):
        if '\n' in ht:
            paras = []
            for line in ht.split('\n'):
                paras.append(make_para(
                    [make_run(line, sz=SZ_BODY, bold=True)],
                    spacing_after=0, line='280', jc='center'
                ))
            phdr_row.append(make_tc(paras, PCOLS[i], fill='BDD6EE', valign='center'))
        else:
            phdr_row.append(make_hdr_cell(ht, PCOLS[i]))
    ptbl.append(phdr_row)

    for i, service in enumerate(services_data):
        deliverables = service['deliverables']
        if isinstance(deliverables, list) and len(deliverables) > 1:
            # Only add numbers if not already numbered
            already_numbered = any(
                isinstance(item, str) and re.match(r'^\d+[.、）)]\s*', item)
                for item in deliverables
            )
            if not already_numbered:
                deliverables = [f"{j}. {item}" for j, item in enumerate(deliverables, 1)]
        cells = [
            make_data_cell(str(i+1), PCOLS[0], jc='center'),
            make_data_cell(with_code(service), PCOLS[1]),
            make_data_cell(service['days'], PCOLS[2], jc='center'),
            make_data_cell(service['process'], PCOLS[3], small=True),
            make_data_cell(deliverables, PCOLS[4], small=True),
        ]
        ptbl.append(make_table_row(cells))

    body_children.append(ptbl)

    # 8. Required Documents Table
    body_children.append(make_para('', spacing_before=0, spacing_after=0))
    doc_title = '4.所需资料及信息清单'
    body_children.append(make_section_header(doc_title))
    body_children.append(make_para('', spacing_before=0, spacing_after=120))

    DCOLS = [654, 2219, 7229]  # 序号, 项目, 所需材料（与中国模板 gridCol 一致）

    dtbl = ET.Element(w('tbl'))
    dtbl.append(make_tbl_pr(sum(DCOLS)))
    dtbl.append(make_tbl_grid(DCOLS))

    dhdr_row = ET.Element(w('tr'))
    dhdr_trPr = ET.SubElement(dhdr_row, w('trPr'))
    ET.SubElement(dhdr_trPr, w('trHeight')).set(w('val'), '564')
    ET.SubElement(dhdr_trPr, w('trHeight')).set(w('hRule'), 'atLeast')
    doc_headers = ['序号', '服务内容', '所需资料及信息']
    for i, ht in enumerate(doc_headers):
        dhdr_row.append(make_hdr_cell(ht, DCOLS[i]))
    dtbl.append(dhdr_row)

    indented_lines = 0
    for i, service in enumerate(services_data):
        indents = doc_line_indents.get(service['line_id']) or []
        indented_lines += sum(1 for v in indents if v)
        cells = [
            make_data_cell(str(i+1), DCOLS[0], jc='center'),
            make_data_cell(with_code(service), DCOLS[1]),
            make_data_cell(service['documents'], DCOLS[2], small=True, indents=indents),
        ]
        dtbl.append(make_table_row(cells))

    body_children.append(dtbl)
    if indented_lines:
        print(f'✅ 所需资料及信息行缩进：{indented_lines} 行'
              f'（层级取自行首全角空格，每级 {DOC_LINE_INDENT_TWIPS} twips）')

    # 9. Footer — 标准备注（固定 9 条，全部签约主体一致），带数字序号
    footer_notes = [
        '以上办理时间为收集齐所需资料及信息开始的官方办理时间，法定节假日及办证政府机构休息日不算入办理时间。',
        '甲方必须提供办理服务所需资料及信息，如因资料及信息不足或提供不及时耽误了办理进度，则所产生的额外费用由甲方承担。',
        '以上服务价格和办理时间按照甲方提供的信息及资料进行评估，如发生变化，则根据最新信息及资料进行价格和时间调整。',
        '未列入本报价单或不属于本报价单服务范围内的服务，将另行报价。',
        '本报价单中所列“（如需）”服务，须在办理过程中根据甲方实际需求及情况重新评估，最终服务价格、数量及办理时间以评估结果为准。',
        '如果当地法规发生变化，乙方有权调整价格及办理时间。',
    ]

    body_children.append(make_para('', spacing_before=0, spacing_after=0))
    body_children.append(make_para(
        [make_run('备注：', sz=SZ_BODY, bold=True)],
        spacing_before=80, spacing_after=0, line='280'
    ))
    for idx, note_text in enumerate(footer_notes, 1):
        body_children.append(make_para(
            [make_run(f'{idx}.{note_text}', sz=SZ_SMALL)],
            spacing_after=0, line='280'
        ))

    # 第 7 条 — 银行账户信息（loaded from entity config）
    bank_lines_by_currency = entity_cfg.get('bank_lines_by_currency', {})
    selected_bank_lines = bank_lines_by_currency.get(CURRENCY, entity_cfg['bank_lines'])

    body_children.append(make_para(
        [make_run('7.所有款项汇到指定的银行账户，银行账户信息如下：', sz=SZ_BANK, bold=True)],
        spacing_after=0, line='280'
    ))
    for line in selected_bank_lines:
        body_children.append(make_para(
            [make_run(line, sz=SZ_BANK, bold=True)],
            spacing_after=0, line='280', indent_left=420
        ))

    body_children.append(make_para(
        [make_run('8.本公司对客户提供的纸质或电子版的证件、资料负有妥善保管和保密义务，不得将上述秘密泄露给任何第三方或用于其他用途。', sz=SZ_SMALL)],
        spacing_after=0, line='280'
    ))
    body_children.append(make_para(
        [make_run('9.此报价从报价日起生效30天。', sz=SZ_SMALL)],
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
            [make_run(text, sz=SZ_BODY, bold=True)],
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
            [make_run(text, sz=SZ_SMALL)],
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

        # Apply header override from entity config for every template
        apply_header(UNPACK, entity)

        # Extract sectPr from the original template before editing
        orig_doc_path = os.path.join(UNPACK, 'word', 'document.xml')
        orig_tree = ET.parse(orig_doc_path)
        orig_root = orig_tree.getroot()
        orig_body = orig_root.find(f'{{{W}}}body')
        orig_sectPr = orig_body.find(f'{{{W}}}sectPr')

        if orig_sectPr is not None:
            original_size = normalize_page_setup(orig_sectPr)
            if original_size == (str(A4_PAGE_W), str(A4_PAGE_H)):
                print(f"✅ Page setup: A4 portrait ({A4_PAGE_W}x{A4_PAGE_H} DXA), "
                      f"标准页边距 — 可直接 A4 打印")
            else:
                print(f"  ⚠️  模板页面为 {original_size[0]} x {original_size[1]} DXA，"
                      f"非 A4；已强制归一为 A4 {A4_PAGE_W}x{A4_PAGE_H} + 标准页边距")
            sectPr_xml = ET.tostring(orig_sectPr, encoding='unicode')
        else:
            sectPr_xml = None
            print("  ⚠️  WARNING: No sectPr found in template!")
            print("  ⚠️  无法确认页面尺寸；生成的报价单可能不适合 A4 打印，请检查模板")

        # Remove existing body children
        orig_body.clear()

        # Add new body children
        for child in body_children:
            orig_body.append(child)

        # Restore sectPr (must be last child of body for valid OOXML)
        if sectPr_xml:
            orig_body.append(ET.fromstring(sectPr_xml))

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
    print(f"费用: 小计={fmt_price_int(SUBTOTAL_D)} | 优惠={fmt_price_int(DISCOUNT_D)} | {TAX_LABEL}={fmt_price_vat(VAT_D)} | 总计={fmt_price_total(GRAND_TOTAL_D)}")

if __name__ == '__main__':
    main()
