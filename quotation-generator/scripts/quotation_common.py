#!/usr/bin/env python3
"""Shared utilities for quotation data validation and building.

Provides money parsing, VAT calculation, currency formatting, and entity
config loading used by validate_data.py, build_quotation.py, and
verify_quotation.py to ensure consistent behaviour across the pipeline.
"""
import json
import os
import re
import struct
import sys
import unicodedata
import zlib
from decimal import Decimal, ROUND_HALF_UP


# 币种元数据（唯一权威来源）：code -> (中文名, 符号, 是否目标币种, 是否整数币种)。
# 目标币种 = 报价单可直接用于签约/收款的币种（签约主体本币 + RMB/USD）。
CURRENCIES = {
    'RMB': ('人民币', '￥', True, False),
    'IDR': ('印尼盾', 'Rp', True, True),
    'USD': ('美元',   '$',  True, False),
    'SGD': ('新币',   'S$', True, False),
    'THB': ('泰铢',   '฿',  True, False),
    'VND': ('越南盾', '₫',  True, True),
    'EGP': ('埃及镑', 'E£', True, False),
    'MYR': ('马币',   'RM', True, False),
}

# 源币种别名：统一归一化到已知 code（如 API 返回 CNY 时视作 RMB）。
CURRENCY_ALIASES = {
    'CNY': 'RMB',
}

CURRENCY_NAMES = {code: info[0] for code, info in CURRENCIES.items()}
CURRENCY_NAME_TO_CODE = {info[0]: code for code, info in CURRENCIES.items()}
INTEGER_ONLY_CURRENCIES = tuple(code for code, info in CURRENCIES.items() if info[3])
TARGET_CURRENCIES = tuple(code for code, info in CURRENCIES.items() if info[2])


def currency_has_decimals(currency):
    """币种的金额是否保留 2 位小数。仅 IDR/VND 省略小数，其余一律保留。"""
    return currency not in INTEGER_ONLY_CURRENCIES


def currency_symbol(code):
    """返回币种符号（如 '￥'/'Rp'/'S$'）；未知 code 返回空串。"""
    info = CURRENCIES.get(code)
    return info[1] if info else ''


def normalize_currency_code(code):
    """按别名归一化源币种 code（如 CNY -> RMB）；未知 code 原样返回。"""
    if code is None:
        return None
    return CURRENCY_ALIASES.get(code, code)


def is_target_currency(code):
    """code 是否为报价单支持的签约/收款币种（本币 + RMB/USD）。"""
    return code in TARGET_CURRENCIES

# Path to entity configuration, resolved relative to this module's location.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_CONFIG_PATH = os.path.join(_SKILL_DIR, 'config', 'entities.json')

REQUIRED_ENTITY_FIELDS = (
    'template', 'template_file', 'company', 'header_lines', 'vat_rate', 'currency',
    'allowed_currencies', 'bank_lines', 'swift_policy',
)

# Canonical A4 page geometry, in DXA (twips; 20 DXA = 1pt). Every entity template
# carries exactly this setup, and build normalizes to it so a drifted or non-A4
# template can never produce a quotation that won't print on A4. build enforces it;
# verify re-checks it independently.
A4_PAGE_W = 11906
A4_PAGE_H = 16838
A4_MARGINS = {
    'top': 679,      # 33.95pt
    'right': 1133,   # 56.65pt
    'bottom': 1155,  # 57.75pt
    'left': 1440,    # 72pt
    'header': 227,   # 0.4cm from the paper edge (页眉顶到纸张上缘)
    'footer': 227,   # 0.4cm from the paper edge (页脚底到纸张下缘)
    'gutter': 0,
}
# Width of the text column the body flows into: 11906 - 1440 - 1133 = 9333 DXA.
A4_TEXT_WIDTH = A4_PAGE_W - A4_MARGINS['left'] - A4_MARGINS['right']

TWIPS_PER_MM = 1440 / 25.4            # 56.6929
EMU_PER_MM = 36000

# ---- 图片页眉 ---------------------------------------------------------------
# 7 个主体（beijing / xian / shenzhen / shanghai / shanghai_new / jakarta / deyin）
# 的页眉用整幅横幅图取代文字，蓝线保留。其余主体仍走文字页眉。
#
# 两个要点：
#   1. 横幅是**浮动锚定**（wp:anchor + wrapNone），不是内联。内联时图片自带的透明边距
#      照样占版面，页眉压不下去。
#   2. 横幅与蓝线必须锚在**同一段**，两个偏移量都从该段顶端起算 —— 沿用模板原有的
#      logo/蓝线不变式（见 SKILL.md 流程规则 #4）。
#
# 横幅一律按 PNG 的 alpha 边界**裁掉透明边距**（a:srcRect 裁，PNG 文件本身一个字节都不
# 动），所以图片框 == 图墨迹：可见内容一个像素不变，框却不会再伸到纸张上缘之外、也不会
# 越过正文栏。原图的透明上边距有 5.6~6.3mm、下边距 6.3~7.0mm、左右各 3.4~7.0mm，不裁的
# 话框会顶到纸边（实测雅加达框顶距纸边仅 0.37mm）并一直压到正文首行前 0.66mm。早先的做
# 法是让框顶伸到纸张上缘之上、靠那一截全透明来兜，位置依赖针对单个渲染器标定的漂移常量，
# Word/WPS 下并不成立；裁掉之后框有多大就是墨迹有多大，与渲染器无关。
HEADER_IMAGE_DEFAULTS = {
    'ink_top_mm': 6.0,    # 图片墨迹顶距纸张上缘；必须 ≥ MIN_PRINTABLE_INK_TOP_MM
    'line_gap_mm': 3.0,   # 图片墨迹底 → 蓝线
    'body_top_mm': 30.0,  # 正文首行距纸张上缘，即页眉占用的总高度
}
# 多数激光打印机的不可打印区约 4.2mm，图墨迹顶低于这条线会被切掉。
MIN_PRINTABLE_INK_TOP_MM = 5.0


def text_column_mm():
    """正文栏的左缘位置与宽度（mm），由 A4 页边距算出。

    横幅的可见部分默认与正文栏左右对齐；``ink_left_mm`` / ``ink_width_cm`` 可覆盖。
    """
    left = A4_MARGINS['left'] / TWIPS_PER_MM
    width = (A4_PAGE_W - A4_MARGINS['left'] - A4_MARGINS['right']) / TWIPS_PER_MM
    return left, width

# 页眉距纸边（A4_MARGINS['header'] = 227 DXA）是**名义**落点，实测页眉首段顶端与两个
# 锚点的实际落点都跟名义值差一点点。差值由段落行高决定，推不出来，所以量一次写死：
# 渲染一张 A4 首页，量图墨迹顶与蓝线的位置，与名义值相减。**改动 A4_MARGINS['header']
# 或页眉段落结构后必须重量**，否则图墨顶会偏离 ink_top_mm，可能掉进不可打印区。
HEADER_IMAGE_NOMINAL_TOP_MM = A4_MARGINS['header'] / TWIPS_PER_MM   # 4.0
HEADER_IMAGE_INK_DRIFT_MM = 0.2     # 图墨迹实际落点比名义低 0.2mm
HEADER_IMAGE_LINE_DRIFT_MM = 0.5    # 蓝线实际落点比名义高 0.5mm
# 正文首行落在 HEADER_IMAGE_RESERVE_CALIB_BODY_TOP_MM 时，页眉段的 space-after 取
# HEADER_IMAGE_RESERVE_CALIB_TWIPS；此后每 ±1 twip 正文首行同向移动 1/56.6929 mm。
HEADER_IMAGE_RESERVE_CALIB_TWIPS = 1330
HEADER_IMAGE_RESERVE_CALIB_BODY_TOP_MM = 30.1


def read_png_size(path):
    """读 PNG 的宽高（像素）。IHDR 紧跟 8 字节文件头，宽高各 4 字节大端。

    只为了拿宽高比算横幅的显示尺寸，无需解码图像 —— 这样 build 不必依赖图像库。
    """
    with open(path, 'rb') as fh:
        head = fh.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n' or head[12:16] != b'IHDR':
        raise ValueError(f'不是有效的 PNG 文件: {path}')
    return struct.unpack('>II', head[16:24])


def png_alpha_bottom_padding(data):
    """PNG 底部透明留白占其高度的比例，0.0~1.0。

    Word 会把整张位图（含透明留白）缩放到锚点的 wp:extent，所以位图下方若有空行，
    图墨迹的可见底边就落在 wp:extent 底边之上。凡是判断「图底到蓝线还有多少净空」的
    地方，都必须按墨迹算，不能按图片框算。

    只用标准库（模板自带的都是 8bit RGBA、非隔行 PNG），build 与测试都不必依赖图像库。
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, chunks, width, height = 8, [], None, None
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b'IHDR':
            width, height, depth, colour, _, _, interlace = struct.unpack('>IIBBBBB', body)
            if (depth, colour, interlace) != (8, 6, 0):
                raise ValueError(f'unsupported PNG: depth={depth} colour={colour}')
        elif ctype == b'IDAT':
            chunks.append(body)
        elif ctype == b'IEND':
            break
        pos += 12 + length

    raw = zlib.decompress(b''.join(chunks))
    stride = width * 4
    prev = bytearray(stride)
    last_inked = -1
    for y in range(height):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        ftype, scan = line[0], bytearray(line[1:])
        if ftype:  # undo the per-scanline PNG filter
            for i in range(stride):
                left = scan[i - 4] if i >= 4 else 0
                up = prev[i]
                upleft = prev[i - 4] if i >= 4 else 0
                if ftype == 1:
                    scan[i] = (scan[i] + left) & 0xFF
                elif ftype == 2:
                    scan[i] = (scan[i] + up) & 0xFF
                elif ftype == 3:
                    scan[i] = (scan[i] + (left + up) // 2) & 0xFF
                elif ftype == 4:
                    p = left + up - upleft
                    pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                    pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                    scan[i] = (scan[i] + pred) & 0xFF
        if max(scan[3::4]) > 8:
            last_inked = y
        prev = scan
    return 0.0 if last_inked < 0 else (height - 1 - last_inked) / height


def header_image_spec(entity_cfg, defaults=None):
    """合并默认值、_meta.header_image_defaults 与实体的 header_image。

    实体没配 header_image（仍是文字页眉）时返回 None。
    """
    own = (entity_cfg or {}).get('header_image')
    if not own:
        return None
    spec = dict(HEADER_IMAGE_DEFAULTS)
    if defaults:
        spec.update(defaults)
    spec.update(own)
    if not spec.get('bbox_px'):
        raise ValueError(f'header_image 缺少 bbox_px（{spec.get("file")}）：'
                         f'需要 PNG alpha 边界的 [x0, y0, x1, y1] 像素坐标，用来裁掉透明边距')
    return spec


# ---- 文字页眉 logo 垂直位置 --------------------------------------------------
# 文字页眉（singapore / thailand / vietnam / egypt / malaysia）的 logo 与蓝线都浮动
# 锚定在页眉**末段**上，两者间距（蓝线偏移 − logo 偏移 − logo 可见高度）因此是模板
# 常量：五套模板实测 3.52~3.53mm，彼此一致。原设计里 logo 偏上，与蓝线之间的留白比
# 视觉需要的多，故统一把它下移 HEADER_LOGO_DEFAULTS['shift_mm']。
#
# **只动 logo，不动蓝线**：蓝线的落点决定正文首行能避开多少（页眉末段的高度就是这份
# 余量），挪蓝线会压到正文「公司名称：」。下移量因此受限于 logo 底与蓝线之间的净空，
# 由 MIN_LOGO_LINE_GAP_MM 兜底。
#
# 图片页眉（印尼/中国主体）不适用：logo 已经印在横幅图里，header1.xml 中没有独立的
# logo 锚点，header_logo_spec() 对它们返回 None。
HEADER_LOGO_DEFAULTS = {
    'shift_mm': 1.0,   # logo 沿页眉末段顶端下移的距离；0 = 保持模板原位
}
# logo 底边与蓝线之间必须保留的最小净空；低于此值两者在视觉上粘连。
MIN_LOGO_LINE_GAP_MM = 1.0


def header_logo_spec(entity_cfg, defaults=None):
    """文字页眉的 logo 下移量（mm）。

    图片页眉主体返回 None —— 它们的 logo 在横幅图里，没有可独立调整的锚点。
    实体可用 ``header_logo_shift_mm`` 覆盖默认值（0 = 保持模板原位）。
    """
    cfg = entity_cfg or {}
    if cfg.get('header_image'):
        return None
    spec = dict(HEADER_LOGO_DEFAULTS)
    if defaults:
        spec.update(defaults)
    override = cfg.get('header_logo_shift_mm')
    if override is not None:
        spec['shift_mm'] = override
    return spec


def header_image_geometry(spec, png_path):
    """算出横幅的锚点参数与裁剪比例。除标明的 mm 值外，一律是 EMU / twips / 千分之一百分比。

    锚点偏移量都从**页眉首段顶端**起算，所以先减掉名义落点；图墨迹与蓝线的漂移方向相反
    （图墨迹偏低、蓝线偏高），故分别加修正。

    图片框 == 图墨迹：按 alpha 边界（``bbox_px``）裁掉透明边距后，横幅框顶正好落在
    ``ink_top_mm`` 上、左右正好落在正文栏边界上，不会再伸到纸张或正文栏之外。
    """
    px_w, px_h = read_png_size(png_path)
    x0, y0, x1, y1 = spec['bbox_px']
    if not (0 <= x0 < x1 <= px_w and 0 <= y0 < y1 <= px_h):
        raise ValueError(f'header_image.bbox_px {spec["bbox_px"]} 超出 PNG 尺寸 '
                         f'{px_w}x{px_h}（{png_path}）')

    col_left_mm, col_w_mm = text_column_mm()
    ink_left_mm = spec.get('ink_left_mm') or col_left_mm
    ink_w_mm = (spec.get('ink_width_cm') or col_w_mm / 10) * 10
    # 以墨迹宽度定缩放比例，高度按原图长宽比推出来，横幅不会被拉伸。
    px_per_mm = (x1 - x0) / ink_w_mm
    ink_h_mm = (y1 - y0) / px_per_mm

    line_mm = spec['ink_top_mm'] + ink_h_mm + spec['line_gap_mm']
    return {
        'extent_cx': int(round(ink_w_mm * EMU_PER_MM)),
        'extent_cy': int(round(ink_h_mm * EMU_PER_MM)),
        # 图片框已裁到墨迹边界，左缘直接落在 ink_left_mm 上。positionH 的
        # relativeFrom="column"，原点就是左边距，故减掉正文栏左缘。
        'banner_off_h': int(round((ink_left_mm - col_left_mm) * EMU_PER_MM)),
        'banner_off_v': int(round((spec['ink_top_mm'] - HEADER_IMAGE_NOMINAL_TOP_MM
                                   - HEADER_IMAGE_INK_DRIFT_MM) * EMU_PER_MM)),
        'line_off_v': int(round((line_mm - HEADER_IMAGE_NOMINAL_TOP_MM
                                 + HEADER_IMAGE_LINE_DRIFT_MM) * EMU_PER_MM)),
        'reserve_twips': int(round(HEADER_IMAGE_RESERVE_CALIB_TWIPS
                                   + (spec['body_top_mm'] - HEADER_IMAGE_RESERVE_CALIB_BODY_TOP_MM)
                                   * TWIPS_PER_MM)),
        # a:srcRect 的裁剪比例，单位千分之一百分比（100000 = 100%）。
        'src_rect': {
            'l': int(round(x0 / px_w * 100000)),
            't': int(round(y0 / px_h * 100000)),
            'r': int(round((px_w - x1) / px_w * 100000)),
            'b': int(round((px_h - y1) / px_h * 100000)),
        },
        'ink_left_mm': ink_left_mm,
        'ink_w_mm': ink_w_mm,
        'ink_h_mm': ink_h_mm,
        'ink_top_mm': spec['ink_top_mm'],
        'ink_bottom_mm': spec['ink_top_mm'] + ink_h_mm,
        'line_mm': line_mm,
    }

# 「服务内容」表只有 序号/服务内容/数量/价格/备注 五列，没有办理时间列；紧跟在它下面的
# *备注： 区块因此不能出现「上述办理时间不包括收集材料时间……」这类免责声明——办理时间
# 在更靠后的流程表里，「上述」在这里指不到任何上文。
# 这句话本身仍然是需要的，只是位置不对：不要整条删掉，也不要改写成别的措辞（见 SKILL.md）。
# API 的服务「备注」段普遍带这句样板文，Agent 汇总基本信息时容易顺手带进 notes：
# validate 报错拦下、build 兜底剔除并告警、verify 复核成稿，三处共用这一判定。
#
# 判定要认得出样板文，又不能误杀恰好提到时间的正常备注——误杀会让合法信息被 validate
# 拦下、被 build 删掉。所以除了「办理时间 + 近邻的不包括」这个壳子，还要求紧跟其后出现
# 样板文列举的时间项之一：只有壳子不算数。「办理时间不包括节假日」「办理时间不包括周末及
# 法定节假日」都是正常业务说明，必须放行。
PROCESS_TIME_DISCLAIMER_ITEMS = (
    '收集材料时间', '收集资料时间', '检查资料时间', '检查材料时间',
    '签字盖章', '反馈时间', '修改或补充', '补充资料', '邮寄时间', '快递时间',
)
PROCESS_TIME_DISCLAIMER_RE = re.compile(
    r'办理时间[^。；;\n]{0,10}不(?:包括|包含)[^。；;\n]*(?:'
    + '|'.join(PROCESS_TIME_DISCLAIMER_ITEMS)
    + r')'
)


def is_process_time_disclaimer(text):
    """text 是否为「办理时间不包括……」类免责声明（notes 中不允许出现）。"""
    return bool(PROCESS_TIME_DISCLAIMER_RE.search(text or ''))


# XML 1.0 合法字符为 #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]。
# 其余字符（NUL、BEL、退格、ESC 等 C0 控制符，以及孤立代理项）写进 w:t 会让
# document.xml 变成 not well-formed：ElementTree 只转义 &<>，不剔除非法字符，
# 于是 build 打印成功、产物却打不开。这类字符常来自从 Word/PDF 复制或手写 JSON 转义。
_XML_OK_WHITESPACE = ('\t', '\n', '\r')
# \x0b（垂直制表）与 \x0c（换页）在 Word 粘贴内容里是软换行/分页的常见残留，映射成换行保留原意。
_XML_SOFT_BREAK_RE = re.compile('[\x0b\x0c]')


def _is_xml_char(ch):
    """ch 是否为 XML 1.0 合法字符（Char 产生式）。

    用代码点比较而非正则字符类，避免 U+D7FF / U+E000 这类边界被写成字面字符后难以辨认。
    字面字符后难以辨认。0xD800-0xDFFF（孤立代理项）落在两个区间之间，自然被排除。
    """
    code = ord(ch)
    return (ch in _XML_OK_WHITESPACE
            or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD
            or code >= 0x10000)


def xml_safe_text(value):
    """把任意值转成可安全写入 w:t 的字符串。

    \x0b / \x0c 映射为换行；其余 XML 非法字符直接丢弃。
    纯函数、无副作用，便于在各写入点统一调用。
    """
    text = value if isinstance(value, str) else str(value)
    if _XML_SOFT_BREAK_RE.search(text):
        text = _XML_SOFT_BREAK_RE.sub('\n', text)
    return ''.join(ch for ch in text if _is_xml_char(ch))


def validate_entity_configs(raw):
    """Return cross-field configuration errors for all signing entities."""
    errors = []
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue
        for field in REQUIRED_ENTITY_FIELDS:
            if field not in cfg:
                errors.append(f"Entity '{key}' missing required field: {field}")

        swift_policy = cfg.get('swift_policy')
        if swift_policy not in ('required', 'not_required'):
            errors.append(
                f"Entity '{key}' swift_policy must be 'required' or 'not_required'")
        variants = [('default', cfg.get('bank_lines', []))]
        variants.extend((currency, lines) for currency, lines in
                        cfg.get('bank_lines_by_currency', {}).items())
        for variant, lines in variants:
            has_swift = any('swift' in str(line).casefold() for line in lines)
            if swift_policy == 'required' and not has_swift:
                errors.append(f"Entity '{key}' account '{variant}' requires SWIFT but none is configured")
            if swift_policy == 'not_required' and has_swift:
                errors.append(f"Entity '{key}' account '{variant}' has SWIFT but policy is not_required")

        bank_notes = cfg.get('bank_notes', [])
        if not isinstance(bank_notes, list) or not all(
                isinstance(note, str) and note.strip() for note in bank_notes):
            errors.append(f"Entity '{key}' bank_notes must be a list of non-empty strings")

        if 'tax_note' in cfg and (not isinstance(cfg['tax_note'], str) or not cfg['tax_note'].strip()):
            errors.append(f"Entity '{key}' tax_note must be non-empty text")
        if 'summary_layout' in cfg:
            layout = cfg['summary_layout']
            if not isinstance(layout, dict):
                errors.append(f"Entity '{key}' summary_layout must be an object")
                continue
            for row_name in ('subtotal', 'other'):
                row = layout.get(row_name)
                if not isinstance(row, list) or len(row) != 4:
                    errors.append(
                        f"Entity '{key}' summary_layout.{row_name} must contain 4 values")
                    continue
                leading, label_span, amount_span, align = row
                if (isinstance(leading, bool) or not isinstance(leading, int) or leading < 0 or
                        isinstance(label_span, bool) or not isinstance(label_span, int) or label_span < 1 or
                        isinstance(amount_span, bool) or not isinstance(amount_span, int) or amount_span < 1):
                    errors.append(
                        f"Entity '{key}' summary_layout.{row_name} spans must be valid integers")
                elif leading + label_span + amount_span > 5:
                    errors.append(
                        f"Entity '{key}' summary_layout.{row_name} exceeds the 5-column table")
                if align not in ('left', 'center', 'right'):
                    errors.append(
                        f"Entity '{key}' summary_layout.{row_name} alignment is invalid: {align}")
    return errors


def load_entity_config_with_meta():
    """Load and validate entity configuration from config/entities.json.

    Returns a tuple of (entities, universal_excludes, meta):
      - entities: dict of entity_key → config, with _meta keys removed.
      - universal_excludes: list of common exclude item seeds (may be empty).

    Exits with code 1 if the config file is missing or any entity is
    missing a required field.
    """
    if not os.path.exists(ENTITY_CONFIG_PATH):
        print(f"❌ Entity config not found: {ENTITY_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(ENTITY_CONFIG_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    # Validate every entity has all required fields (skip _meta annotation keys).
    errors = validate_entity_configs(raw)
    if errors:
        print(f"❌ Invalid entity config:\n- " + '\n- '.join(errors), file=sys.stderr)
        sys.exit(1)

    # Return only entity keys (strip _meta / annotation entries).
    entities = {k: v for k, v in raw.items() if not k.startswith('_')}

    meta = raw.get('_meta', {})
    universal_excludes = []
    if isinstance(meta, dict):
        universal_excludes = meta.get('universal_excludes', [])

    # 付款条件：仅 jakarta/deyin 显式声明「收到发票后 100%」，其余主体
    # 共用 _meta.payment_terms_default，未显式声明时在此注入。
    default_terms = meta.get('payment_terms_default') if isinstance(meta, dict) else None
    if default_terms:
        for cfg in entities.values():
            cfg.setdefault('payment_terms', default_terms)

    return entities, universal_excludes, meta


def load_entity_config():
    """Backward-compatible entity loader returning entities and excludes only."""
    entities, universal_excludes, _ = load_entity_config_with_meta()
    return entities, universal_excludes


def normalize_entity_alias(value):
    """Normalize human-entered entity aliases for deterministic matching."""
    if not isinstance(value, str):
        return ''
    return ''.join(
        ch for ch in unicodedata.normalize('NFKC', value).casefold()
        if ch.isalnum()
    )


def _alias_occurs(query, alias):
    """Match aliases in prose; short ASCII aliases such as SCI require boundaries."""
    normalized_alias = normalize_entity_alias(alias)
    if not normalized_alias:
        return False
    if normalized_alias.isascii() and normalized_alias.isalnum() and len(normalized_alias) <= 4:
        return re.search(
            rf'(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])',
            query, flags=re.IGNORECASE,
        ) is not None
    return normalized_alias in normalize_entity_alias(query)


def resolve_entity_alias(query, entities=None, ambiguous_aliases=None):
    """Resolve a user phrase without guessing through ambiguous entity names."""
    if entities is None or ambiguous_aliases is None:
        with open(ENTITY_CONFIG_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        entities = entities or {k: v for k, v in raw.items() if not k.startswith('_')}
        meta = raw.get('_meta', {}) if isinstance(raw.get('_meta'), dict) else {}
        ambiguous_aliases = ambiguous_aliases or meta.get('ambiguous_aliases', {})

    normalized = normalize_entity_alias(query)
    if not normalized:
        return {'status': 'no_match', 'query': query, 'candidates': []}

    aliases_by_entity = {}
    exact = []
    matches = []
    for key, cfg in entities.items():
        aliases = set(cfg.get('aliases') or []) | {key, cfg.get('company', '')}
        aliases = {a for a in aliases if isinstance(a, str) and a.strip()}
        aliases_by_entity[key] = aliases
        if any(normalize_entity_alias(a) == normalized for a in aliases):
            exact.append(key)
        for alias in aliases:
            if _alias_occurs(query, alias):
                matches.append((alias, {key}))

    ambiguous_exact = []
    for alias, candidates in (ambiguous_aliases or {}).items():
        if normalize_entity_alias(alias) == normalized:
            ambiguous_exact.extend(candidates)
        if _alias_occurs(query, alias):
            matches.append((alias, set(candidates)))

    candidates = sorted(set(exact) | set(ambiguous_exact))
    if not candidates:
        # Discard a less-specific match only when its normalized alias is a proper
        # substring of another matched alias (上海 vs 上海新企业). Separate mentions
        # such as 北京 + 西安 remain ambiguous regardless of alias length.
        maximal = []
        for alias, alias_candidates in matches:
            norm = normalize_entity_alias(alias)
            if any(norm != normalize_entity_alias(other) and
                   norm in normalize_entity_alias(other) for other, _ in matches):
                continue
            maximal.append((alias, alias_candidates))
        candidates = sorted(set().union(*(c for _, c in maximal)) if maximal else set())

    if len(candidates) == 1:
        key = candidates[0]
        matched_aliases = sorted(
            (a for a in aliases_by_entity[key] if _alias_occurs(query, a)),
            key=lambda a: len(normalize_entity_alias(a)), reverse=True)
        if not matched_aliases:
            matched_aliases = [a for a in aliases_by_entity[key]
                               if normalize_entity_alias(a) == normalized]
        return {'status': 'matched', 'query': query, 'entity': key,
                'matched_alias': matched_aliases[0], 'candidates': candidates}
    if len(candidates) > 1:
        return {'status': 'ambiguous', 'query': query, 'candidates': candidates}
    return {'status': 'no_match', 'query': query, 'candidates': []}


def parse_money_int(value, path):
    """Parse an integer-valued amount as Decimal without float conversion."""
    if isinstance(value, bool):
        raise ValueError(f'{path} must be an integer amount, not boolean')
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        raw = value.replace(',', '').strip()
        if raw.isdigit():
            return Decimal(raw)
    raise ValueError(f'{path} must be an integer amount or comma-formatted integer string')


def _to_decimal(value):
    """Convert int/float/str to Decimal."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def calculate_amounts(subtotal, discount, vat_rate, currency, withholding_tax_rate=None):
    """Calculate discounted subtotal, VAT, and grand total.

    Args:
        subtotal: integer or Decimal, sum of service prices.
        discount: integer or Decimal, discount amount (0 if none).
        vat_rate: Decimal or float, e.g. 0.06 for 6%.
        currency: 税金保留 2 位小数的币种（除 IDR/VND 外全部：RMB/USD/SGD/THB/EGP/MYR）；
            仅 IDR/VND 取整数。
        withholding_tax_rate: Decimal or float, e.g. 0.03 for 3% WHT. None if no WHT.

    Returns:
        dict with:
          - subtotal/discount/discounted/vat/total (Decimal)
          - vat_rate (Decimal)
          - withholding_tax (Decimal/int or None)
          - withholding_tax_rate (Decimal or None)
    """
    subtotal_d = _to_decimal(subtotal)
    discount_d = _to_decimal(discount)
    vat_rate_d = _to_decimal(vat_rate)
    has_decimals = currency_has_decimals(currency)

    discounted_d = subtotal_d - discount_d

    vat_raw = discounted_d * vat_rate_d
    if has_decimals:
        vat_d = vat_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        vat_d = vat_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    # Withholding tax
    wht_d = None
    wht_rate_d = None
    if withholding_tax_rate is not None:
        wht_rate_d = _to_decimal(withholding_tax_rate)
        wht_raw = discounted_d * wht_rate_d
        if has_decimals:
            wht_d = wht_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            wht_d = wht_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    total_d = discounted_d + vat_d - (wht_d or Decimal('0'))

    result = {
        'subtotal': subtotal_d,
        'discount': discount_d,
        'discounted': discounted_d,
        'vat': vat_d,
        'total': total_d,
        'vat_rate': vat_rate_d,
        'withholding_tax': None,
        'withholding_tax_rate': None,
    }
    if wht_d is not None:
        result['withholding_tax'] = wht_d
        result['withholding_tax_rate'] = wht_rate_d
    return result


def format_price_display(price_str, currency):
    """Return price string without currency symbol.

    报价单价格一律不带货币符号（￥/Rp/$/S$/฿/₫）：币种由价格列头「价格 (币种名)」
    标注，不在每个金额上重复附加符号。currency 参数保留以兼容调用方签名。
    """
    return price_str


def format_price_int(val, currency):
    """Format integer amounts (subtotal, discount, discounted) — 不带货币符号。"""
    val_d = _to_decimal(val)
    formatted = f'{int(val_d):,}'
    return format_price_display(formatted, currency)


def _format_amount_smart(val_d, currency):
    """2 位小数币种的金额：仅当有小数值时显示小数位，整数金额省略 .00。"""
    val_d = _to_decimal(val_d).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if val_d == val_d.to_integral_value():
        return format_price_display(f'{int(val_d):,}', currency)
    return format_price_display(f'{val_d:,.2f}', currency)


def format_price_vat(val, currency):
    """Format VAT — RMB/USD/THB/SGD 仅在有小数时显示小数位，IDR/VND 恒为整数。"""
    val_d = _to_decimal(val)
    if currency_has_decimals(currency):
        return _format_amount_smart(val_d, currency)
    return format_price_display(f'{int(val_d):,}', currency)


def format_price_total(val, currency):
    """Format grand total — RMB/USD/THB/SGD 仅在有小数时显示小数位，IDR/VND 恒为整数。"""
    val_d = _to_decimal(val)
    if currency_has_decimals(currency):
        return _format_amount_smart(val_d, currency)
    return format_price_display(f'{int(val_d):,}', currency)


def vat_percent_label(vat_rate):
    """Return human-readable VAT percentage like '6%' or '1%'."""
    value = _to_decimal(vat_rate) * Decimal('100')
    label = format(value, 'f')
    if '.' in label:
        label = label.rstrip('0').rstrip('.')
    return f"{label or '0'}%"


def price_magnitude_warnings(prices, currency):
    """Return warnings when prices look like the wrong magnitude for the currency.

    Shared by validate_data.py (preflight) and build_quotation.py (build time)
    to catch RMB/IDR/USD mix-ups. Only positive prices are considered — a 0
    price marks BPO/percentage pricing and must not trip the "too small" checks.
    """
    warnings = []
    positive = [p for p in prices if p > 0]
    if not positive:
        return warnings
    if currency == 'IDR' and any(p < 1_000_000 for p in positive):
        warnings.append('Some prices appear too small for IDR (min: Rp 1,000,000). Did you forget to update from a previous RMB quote?')
    if currency == 'VND' and any(p < 1_000_000 for p in positive):
        warnings.append('Some prices appear too small for VND (min: ₫ 1,000,000). Did you forget to update from a previous RMB/USD quote?')
    if currency == 'RMB' and any(p >= 1_000_000 for p in positive):
        warnings.append('Some prices appear too large for RMB (>= 1,000,000). Did you forget to convert from IDR?')
    if currency == 'USD' and any(p < 50 for p in positive):
        warnings.append('Some prices appear too small for USD (min: $50). Did you forget to convert from IDR?')
    if currency == 'USD' and any(p >= 500_000 for p in positive):
        warnings.append('Some prices appear too large for USD (>= 500,000). Did you forget to convert from IDR?')
    return warnings
