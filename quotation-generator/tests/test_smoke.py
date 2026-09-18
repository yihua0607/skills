"""End-to-end smoke tests for quotation-generator skill.

Each test copies an example data file into a temporary directory, runs
validate_data.py → build_quotation.py → verify_quotation.py, and asserts that
the generated .docx is a valid OOXML package.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import struct
import tempfile
import unittest
import zipfile
import zlib
from xml.etree import ElementTree as ET

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# API 服务「备注」段里的样板文。服务内容表只有 序号/服务内容/数量/价格/备注 五列，
# 没有办理时间列，这句「上述办理时间……」在表下备注里指不到任何上文。
_PROCESS_TIME_DISCLAIMER = (
    '上述办理时间不包括收集材料时间、检查资料时间、客户签字盖章反馈时间、'
    '客户修改或补充资料的时间和文件邮寄时间。'
)


def _docx_texts(docx_path, part='word/document.xml'):
    """All visible text runs in one .docx part, stripped."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with zipfile.ZipFile(docx_path) as zf:
        root = ET.fromstring(zf.read(part))
    return [t.text.strip() for t in root.iter(f'{{{W}}}t') if t.text and t.text.strip()]


def _png_alpha_bottom_padding(data):
    """Fraction of a PNG's height that is transparent below its last inked row.

    Word scales the whole bitmap — transparent slack included — to the anchor's
    wp:extent, so a logo whose PNG has empty rows under the mark still draws its
    visible bottom edge above the bottom of that box. Anything reasoning about the
    gap between the mark and the blue separator has to use the ink, not the box.

    Stdlib only (the templates ship 8-bit RGBA, non-interlaced PNGs), so the test
    suite needs no imaging dependency.
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, chunks, width = 8, [], None
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


def _png_alpha_bbox(data):
    """A banner PNG's ink bounding box as (x0, y0, x1, y1), x1/y1 exclusive.

    This is the same quantity an entity's ``header_image.bbox_px`` must hold, and
    it is measured from the file rather than read back from the config on purpose:
    build derives both the crop and the scale factor from ``bbox_px``, so a config
    that merely *agrees with itself* still renders a shifted, mis-scaled banner.
    Only the PNG can say what the right answer is.

    Stdlib only, same decoder assumptions as _png_alpha_bottom_padding.
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, chunks, width = 8, [], None
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
    x0 = y0 = None
    x1 = y1 = -1
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
        inked = [x for x in range(width) if scan[x * 4 + 3] > 0]
        if inked:
            if y0 is None:
                y0 = y
            y1 = y
            if x0 is None or inked[0] < x0:
                x0 = inked[0]
            if inked[-1] > x1:
                x1 = inked[-1]
        prev = scan
    if y0 is None:
        raise ValueError('banner PNG is fully transparent')
    return (x0, y0, x1 + 1, y1 + 1)


def _run_script(name, args, cwd=SKILL_ROOT):
    """Run a script from the scripts/ directory and return (returncode, stdout, stderr)."""
    cmd = [sys.executable, os.path.join(SKILL_ROOT, 'scripts', name)] + args
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
    )
    return result.returncode, result.stdout, result.stderr


def _copy_example(tmpdir, example_name):
    """Copy an example JSON into the temp directory and return its path."""
    src = os.path.join(SKILL_ROOT, 'examples', example_name)
    dst = os.path.join(tmpdir, 'quotation.json')
    shutil.copy(src, dst)
    return dst


def _build_for_entity(test_case, tmpdir, entity, example='minimal_quotation.json', currency=None):
    """Copy an example, repoint its `_meta` at `entity`, build.

    Returns (data_path, output_path). build refuses a data file whose
    `_meta.applicable_entity` disagrees with `--entity`, so every test that wants
    an entity other than the example's own has to rewrite that field. The example's
    `target_currency` is dropped too (unless `currency` is given) so the entity's
    own default applies — the examples are priced in RMB, which not every entity
    accepts.
    """
    data_path = _copy_example(tmpdir, example)
    with open(data_path, encoding='utf-8') as f:
        data = json.load(f)
    meta = data.setdefault('_meta', {})
    meta['applicable_entity'] = entity
    if currency:
        meta['target_currency'] = currency
    else:
        meta.pop('target_currency', None)
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    output_path = os.path.join(tmpdir, f'报价单-{entity}-测试.docx')
    rc, out, err = _run_script(
        'build_quotation.py',
        ['--entity', entity, '--data', data_path, '--output', output_path]
    )
    test_case.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")
    return data_path, output_path


def _drop_drawings_from_header(src_path, dst_path, keep_shape):
    """Remove the banner/separator drawing from header1.xml, everything else intact.

    `keep_shape` picks which ones survive: True keeps the wordprocessingShape (the
    blue separator), False keeps the picture (the banner)."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'

    def w(tag):
        return f'{{{W}}}{tag}'

    with zipfile.ZipFile(src_path) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}

    root = ET.fromstring(parts['word/header1.xml'])
    for drawing in list(root.iter(w('drawing'))):
        is_shape = drawing.find(f'.//{{{WPS}}}wsp') is not None
        if is_shape == keep_shape:
            continue
        for run in root.iter(w('r')):
            if drawing in list(run):
                run.remove(drawing)
                break
    parts['word/header1.xml'] = ET.tostring(root, xml_declaration=True, encoding='UTF-8')

    with zipfile.ZipFile(dst_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, blob in parts.items():
            zf.writestr(name, blob)


def _header_trailing_paragraph_count(docx_path):
    """Count empty paragraphs following the last text paragraph in header1.xml.

    Every *text* header ends with exactly one such paragraph, and it carries both
    the blue separator line and the logo. Its height is what reserves room so the
    body's first line stays clear of the line, so build must not change it.

    Only meaningful for entities that still render a text header — the ones with
    ``header_image`` get a single paragraph holding an anchored banner and carry
    no text at all. See test_image_header_replaces_text_header_with_anchored_banner.
    """
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with zipfile.ZipFile(docx_path) as zf:
        root = ET.fromstring(zf.read('word/header1.xml'))
    paras = [p for p in root if p.tag == f'{{{W}}}p']
    texts = [''.join(t.text or '' for t in p.iter(f'{{{W}}}t')).strip() for p in paras]
    last = max(i for i, t in enumerate(texts) if t)
    return len(paras) - last - 1


def _assert_docx_valid(test_case, docx_path):
    """Assert that the generated file is a valid .docx with valid document.xml."""
    test_case.assertTrue(os.path.exists(docx_path), f"Output .docx not found: {docx_path}")
    test_case.assertTrue(zipfile.is_zipfile(docx_path), f"Output is not a valid zip file: {docx_path}")
    with zipfile.ZipFile(docx_path, 'r') as zf:
        test_case.assertIn('word/document.xml', zf.namelist(), "Missing word/document.xml")
        xml_bytes = zf.read('word/document.xml')
        # This will raise ParseError if the XML is malformed (e.g. unescaped special chars)
        ET.fromstring(xml_bytes)


def _replace_docx_visible_text(src_path, dst_path, old_text, new_text, part='word/document.xml'):
    """Replace visible text in one .docx part while keeping the package valid."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    def w(tag):
        return f'{{{W}}}{tag}'

    replaced = False
    with zipfile.ZipFile(src_path, 'r') as src, zipfile.ZipFile(dst_path, 'w', zipfile.ZIP_DEFLATED) as dst:
        part_xml = src.read(part)
        root = ET.fromstring(part_xml)
        for text_el in root.findall('.//' + w('t')):
            if text_el.text == old_text:
                text_el.text = new_text
                replaced = True
        modified_xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        for item in src.infolist():
            data = modified_xml if item.filename == part else src.read(item.filename)
            dst.writestr(item, data)
    if not replaced:
        raise AssertionError(f"Text not found in {src_path}:{part}: {old_text}")


def _make_non_a4_template(src_docx, dst_docx, w_dxa=12240, h_dxa=15840):
    """Copy a template with its page size forced to US Letter, simulating drift."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with zipfile.ZipFile(src_docx) as z:
        items = {n: z.read(n) for n in z.namelist()}
    root = ET.fromstring(items['word/document.xml'])
    pg = root.find(f'{{{W}}}body/{{{W}}}sectPr/{{{W}}}pgSz')
    pg.set(f'{{{W}}}w', str(w_dxa))
    pg.set(f'{{{W}}}h', str(h_dxa))
    items['word/document.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    with zipfile.ZipFile(dst_docx, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in items.items():
            z.writestr(name, data)
    return dst_docx


def _tamper_page_margin(src_path, dst_path, name, value):
    """Copy a .docx with one sectPr page margin changed (simulating drift)."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with zipfile.ZipFile(src_path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    root = ET.fromstring(items['word/document.xml'])
    pgMar = root.find(f'{{{W}}}body/{{{W}}}sectPr/{{{W}}}pgMar')
    pgMar.set(f'{{{W}}}{name}', str(value))
    items['word/document.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    with zipfile.ZipFile(dst_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for item_name, data in items.items():
            z.writestr(item_name, data)


class TestQuotationSmoke(unittest.TestCase):

    def test_jakarta_end_to_end(self):
        """Smoke test using the Jakarta sample (IDR, 11% VAT)."""
        with tempfile.TemporaryDirectory(prefix='quotation-smoke-') as tmpdir:
            data_path = _copy_example(tmpdir, 'sample_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-雅加达-测试.docx')

            rc, out, err = _run_script('validate_data.py', ['--entity', 'jakarta', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('数据校验通过', out)

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'jakarta', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertTrue(os.path.exists(output_path))

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('验证通过', out)

            _assert_docx_valid(self, output_path)

    def test_xian_end_to_end(self):
        """Smoke test using the minimal sample for Xian (RMB, 6% VAT)."""
        with tempfile.TemporaryDirectory(prefix='quotation-smoke-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('数据校验通过', out)

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('验证通过', out)

            _assert_docx_valid(self, output_path)

    def test_xml_escape_regression(self):
        """Regression test: service content with & < > must not break the .docx."""
        with tempfile.TemporaryDirectory(prefix='quotation-xml-escape-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Inject XML special characters into multiple text fields.
            data['services'][0]['items'][0]['note'] = (
                "本服务包含 A&B 公司 <特殊> 流程，价格 > 1000 元。"
            )
            data['fee_details'][0]['include'][0] = "服务费 & 资料费"
            data['process_data'][0]['process'][0] = "第一步：收集资料 <电子版>"
            data['process_data'][0]['deliverables'][0] = "1. 认证结果 & 报告"
            data['doc_data'][0]['docs'][0] = "1. 产品资料 <原件> > 100 页"

            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-西安-转义测试.docx')

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            _assert_docx_valid(self, output_path)

            # Verify that the special characters round-trip into visible text.
            with zipfile.ZipFile(output_path, 'r') as zf:
                xml_bytes = zf.read('word/document.xml')
                root = ET.fromstring(xml_bytes)
                all_text = ''.join(t.text or '' for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                self.assertIn('A&B', all_text)
                self.assertIn('<特殊>', all_text)
                self.assertIn('> 1000', all_text)
                self.assertIn('<电子版>', all_text)
                self.assertIn('<原件>', all_text)

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")

    def test_validate_rejects_missing_service_note(self):
        """Preflight validation must reject data that build_quotation.py would reject."""
        with tempfile.TemporaryDirectory(prefix='quotation-missing-note-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            del data['services'][0]['items'][0]['note']
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('services[0].items[0].note is required', out)

    def test_process_time_disclaimer_matcher_does_not_fire_on_unrelated_notes(self):
        """判定要认得免责声明，但不能误杀恰好同时提到时间与不含项的备注。

        validate 对命中是报错（阻断流程），误杀比漏放更贵，所以要求「不包括」紧跟在
        「办理时间」后面，而不是两者出现在同一段就算。
        """
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
        from quotation_common import is_process_time_disclaimer

        for text in (
            _PROCESS_TIME_DISCLAIMER,
            '上述办理时间不包括收集材料时间。',
            '办理时间并不包括客户签字反馈时间。',
            '****备注****：办理时间不包括邮寄时间',
        ):
            with self.subTest(text=text):
                self.assertTrue(is_process_time_disclaimer(text),
                                "免责声明必须被识别")

        for text in (
            '以上服务报价不包括：文件翻译费用（如需）、资料快递费用（国际）。',
            '上述价格不包括政府规费，办理时间以官方审批为准。',
            '办理时间为 11 个工作日，费用不包含官费。',
            '以上办理时间为收集齐所需资料及信息开始的官方办理时间，法定节假日及办证政府机构休息日不算入办理时间。',
            # 「办理时间不包括 X」这个壳子本身不是免责声明：正常业务说明也这么写，
            # 误杀的代价是 validate 拦下 + build 删掉合法信息。
            '办理时间不包括节假日',
            '办理时间不包括周末及法定节假日，实际以官方受理为准。',
            '该服务办理时间不包括客户准备材料的时间',
            '备案办理时间不包括公示期',
            '',
        ):
            with self.subTest(text=text):
                self.assertFalse(is_process_time_disclaimer(text),
                                 "普通备注不得被误判为办理时间免责声明")

    def test_validate_rejects_process_time_disclaimer_in_notes(self):
        """备注不得出现办理时间免责声明——服务内容表没有办理时间列，「上述」指不到东西。

        API 的服务「备注」段普遍带这句样板文，Agent 汇总基本信息时容易顺手带进 notes。
        """
        with tempfile.TemporaryDirectory(prefix='quotation-note-disclaimer-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['notes'].insert(0, {
                'text': _PROCESS_TIME_DISCLAIMER,
                'indent': 360,
            })
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('是办理时间免责声明，必须删除', out)

    def test_build_drops_process_time_disclaimer_from_notes(self):
        """旧数据（沿用旧 quotation.json）由 build 兜底剔除，其余备注保留。"""
        with tempfile.TemporaryDirectory(prefix='quotation-note-drop-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            kept_note = data['notes'][0]['text']
            data['notes'].insert(0, {'text': _PROCESS_TIME_DISCLAIMER, 'indent': 360})
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-备注.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('已剔除备注中的办理时间免责声明', err)

            texts = _docx_texts(output_path)
            self.assertNotIn(_PROCESS_TIME_DISCLAIMER, texts,
                             "办理时间免责声明不得出现在成稿中")
            self.assertIn(kept_note, texts, "其余备注必须原样保留")

    def test_verify_rejects_process_time_disclaimer_in_built_docx(self):
        """verify 独立复核成稿，命中即失败（说明绕过了正常构建路径）。"""
        with tempfile.TemporaryDirectory(prefix='quotation-note-verify-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-备注.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--input', output_path, '--data', data_path, '--entity', 'xian']
            )
            self.assertEqual(rc, 0, f"clean build must verify:\nstdout: {out}\nstderr: {err}")

            with open(data_path, encoding='utf-8') as f:
                note_text = json.load(f)['notes'][0]['text']
            edited_path = os.path.join(tmpdir, 'edited.docx')
            _replace_docx_visible_text(output_path, edited_path, note_text, _PROCESS_TIME_DISCLAIMER)
            shutil.copy(edited_path, output_path)

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--input', output_path, '--data', data_path, '--entity', 'xian']
            )
            self.assertNotEqual(rc, 0)
            self.assertIn('备注中出现办理时间免责声明', out)

    def test_validate_rejects_service_without_code(self):
        """服务编码必填——「服务内容」列要渲染成「编码-服务名」。

        没有编码服务行就没有可对账的产品标识；API 一直返回 服务编码，缺编码是
        Agent 汇总时漏抄，必须在 preflight 就拦下。
        """
        with tempfile.TemporaryDirectory(prefix='quotation-missing-code-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            del data['services'][0]['items'][0]['code']
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('services[0].items[0].code is required', out)

    def test_build_renders_code_in_service_content(self):
        """成稿「服务内容」列必须是「编码-服务名」，而不是裸服务名。"""
        with tempfile.TemporaryDirectory(prefix='quotation-code-render-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-编码.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            texts = _docx_texts(output_path)
            self.assertIn('ID1504-BPOM 化妆品延期注册', texts,
                          "服务内容必须渲染为「编码-服务名」")

    def test_verify_rejects_service_content_without_code(self):
        """verify 独立复核：成稿服务内容掉了编码就要失败。

        三处渲染共用同一份数据，「所有服务都没编码」时彼此自洽、覆盖检查照样通过，
        只有跟数据文件的原始服务名逐字对照才能发现。
        """
        with tempfile.TemporaryDirectory(prefix='quotation-code-verify-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-编码.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--input', output_path, '--data', data_path, '--entity', 'xian']
            )
            self.assertEqual(rc, 0, f"clean build must verify:\nstdout: {out}\nstderr: {err}")
            self.assertIn('服务编码检查', out)

            edited_path = os.path.join(tmpdir, 'edited.docx')
            _replace_docx_visible_text(
                output_path, edited_path,
                'ID1504-BPOM 化妆品延期注册', 'BPOM 化妆品延期注册')
            shutil.copy(edited_path, output_path)

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--input', output_path, '--data', data_path, '--entity', 'xian']
            )
            self.assertNotEqual(rc, 0)
            self.assertIn('服务内容缺少服务编码', out)

    def test_build_rejects_invalid_quote_date(self):
        """Build must fail rather than silently replacing an invalid visible date."""
        with tempfile.TemporaryDirectory(prefix='quotation-bad-date-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['quote_meta']['quote_date'] = '2026-13-40'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-坏日期.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertNotEqual(rc, 0)
            self.assertIn('Invalid quote date', err)
            self.assertFalse(os.path.exists(output_path))

    def test_rebuild_same_output_preserves_existing_payment_terms(self):
        """Rebuilding services/discounts should not overwrite edited payment terms."""
        with tempfile.TemporaryDirectory(prefix='quotation-payment-preserve-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            shutil.copy(src, data_path)
            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            edited_path = os.path.join(tmpdir, '报价单-西安-手改付款.docx')

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"initial build failed:\nstdout: {out}\nstderr: {err}")

            old_term = '合同签订后支付合同金额的 70%，剩余 30% 在所有服务完成后 5 个工作日内支付。'
            edited_term = '客户手动修改付款方式：合同签订后一次性支付合同金额的 100%。'
            _replace_docx_visible_text(output_path, edited_path, old_term, edited_term)
            shutil.copy(edited_path, output_path)

            with open(data_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['discount_amount'] = 1000
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"rebuild failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式来源：旧报价单', out)

            with zipfile.ZipFile(output_path, 'r') as zf:
                xml_bytes = zf.read('word/document.xml')
                root = ET.fromstring(xml_bytes)
                all_text = ''.join(t.text or '' for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                self.assertIn(edited_term, all_text)
                self.assertNotIn(old_term, all_text)
                self.assertIn('1,000', all_text)

    def test_rebuild_stops_without_overwriting_when_payment_terms_cannot_be_extracted(self):
        """A rebuild must fail closed when visible payment terms cannot be preserved."""
        with tempfile.TemporaryDirectory(prefix='quotation-payment-preserve-failure-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-西安-付款标题已修改.docx')
            edited_path = os.path.join(tmpdir, '报价单-西安-付款标题已修改-临时.docx')

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"initial build failed:\nstdout: {out}\nstderr: {err}")

            _replace_docx_visible_text(
                output_path,
                edited_path,
                '2.付款条件：',
                '2.结算安排：',
            )
            shutil.copy(edited_path, output_path)
            with open(output_path, 'rb') as f:
                original_bytes = f.read()

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )

            self.assertEqual(rc, 2, f"rebuild should stop:\nstdout: {out}\nstderr: {err}")
            self.assertIn('请询问用户并确认付款方式', err)
            with open(output_path, 'rb') as f:
                self.assertEqual(f.read(), original_bytes)

            confirmed_term = '用户确认付款方式：首付款 40%，尾款 60%。'
            with open(data_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['quote_meta']['payment_terms'] = [confirmed_term]
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script(
                'build_quotation.py',
                [
                    '--entity', 'xian',
                    '--data', data_path,
                    '--output', output_path,
                    '--overwrite-payment-terms',
                ]
            )
            self.assertEqual(rc, 0, f"confirmed rebuild failed:\nstdout: {out}\nstderr: {err}")
            with zipfile.ZipFile(output_path, 'r') as zf:
                root = ET.fromstring(zf.read('word/document.xml'))
                all_text = ''.join(
                    t.text or ''
                    for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t')
                )
                self.assertIn(confirmed_term, all_text)

    def test_rebuild_default_output_preserves_existing_payment_terms(self):
        """Default output path should also preserve edited payment terms when the file exists."""
        with tempfile.TemporaryDirectory(prefix='quotation-default-output-preserve-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            shutil.copy(src, data_path)
            output_path = os.path.join(tmpdir, '报价单-北京山海图科技有限公司西安分公司.docx')
            edited_path = os.path.join(tmpdir, '报价单-西安-默认输出手改付款.docx')

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path],
                cwd=tmpdir,
            )
            self.assertEqual(rc, 0, f"initial default build failed:\nstdout: {out}\nstderr: {err}")
            self.assertTrue(os.path.exists(output_path))

            old_term = '合同签订后支付合同金额的 70%，剩余 30% 在所有服务完成后 5 个工作日内支付。'
            edited_term = '客户手动修改默认输出付款方式：尾款以最终确认邮件为准。'
            _replace_docx_visible_text(output_path, edited_path, old_term, edited_term)
            shutil.copy(edited_path, output_path)

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path],
                cwd=tmpdir,
            )
            self.assertEqual(rc, 0, f"default rebuild failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式来源：旧报价单', out)

            with zipfile.ZipFile(output_path, 'r') as zf:
                root = ET.fromstring(zf.read('word/document.xml'))
                all_text = ''.join(t.text or '' for t in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                self.assertIn(edited_term, all_text)
                self.assertNotIn(old_term, all_text)

    def test_numbered_payment_terms_are_preserved_and_checked(self):
        """Numbered payment term lines are terms, not section headings."""
        with tempfile.TemporaryDirectory(prefix='quotation-numbered-payment-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            shutil.copy(src, data_path)
            output_path = os.path.join(tmpdir, '报价单-西安-编号付款.docx')

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"initial build failed:\nstdout: {out}\nstderr: {err}")

            old_term = '合同签订后支付合同金额的 70%，剩余 30% 在所有服务完成后 5 个工作日内支付。'
            numbered_terms = '1. 首付款 70%，金额 ￥70,000\n2. 尾款 50%，金额 ￥60,000'
            edited_path = os.path.join(tmpdir, '报价单-西安-编号付款-已编辑.docx')
            _replace_docx_visible_text(output_path, edited_path, old_term, numbered_terms)
            shutil.copy(edited_path, output_path)

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"rebuild failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式来源：旧报价单', out)
            self.assertIn('付款方式比例合计', err)
            self.assertIn('付款方式金额合计', err)

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify should warn only:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式比例合计', out)
            self.assertIn('付款方式金额合计', out)

    def test_payment_terms_percentage_over_100_warns_but_does_not_fail(self):
        """Payment terms are user-managed, but obviously invalid percentages should warn."""
        with tempfile.TemporaryDirectory(prefix='quotation-payment-warning-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['quote_meta']['payment_terms'] = ['首付款 70%，金额 ￥70,000；尾款 50%，金额 ￥60,000。']
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-付款比例提醒.docx')
            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py should warn only:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式比例合计', out)
            self.assertIn('付款方式金额合计', out)

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py should warn only:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式比例合计', err)
            self.assertIn('付款方式金额合计', err)

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify_quotation.py should warn only:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式比例合计', out)
            self.assertIn('付款方式金额合计', out)

    def test_payment_terms_amount_over_contract_without_percentage_warns(self):
        """Payment amount warnings must work even when no percentages are present."""
        with tempfile.TemporaryDirectory(prefix='quotation-payment-amount-warning-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['quote_meta']['payment_terms'] = ['首付款 ￥70,000；尾款 ￥60,000。']
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py should warn only:\nstdout: {out}\nstderr: {err}")
            self.assertIn('付款方式金额合计', out)

    def test_verify_fails_when_signature_company_missing(self):
        """Signature company must be present and match the bank account company."""
        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

        def w(tag):
            return f'{{{W}}}{tag}'

        with tempfile.TemporaryDirectory(prefix='quotation-missing-signature-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            broken_path = os.path.join(tmpdir, '报价单-西安-缺签名公司.docx')

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            with zipfile.ZipFile(output_path, 'r') as src, zipfile.ZipFile(broken_path, 'w', zipfile.ZIP_DEFLATED) as dst:
                document_xml = src.read('word/document.xml')
                root = ET.fromstring(document_xml)
                tables = root.findall('.//' + w('tbl'))
                for tbl in tables:
                    rows = tbl.findall(w('tr'))
                    if not rows:
                        continue
                    first_row_text = ''.join((t.text or '') for t in rows[0].findall('.//' + w('t')))
                    if '报价人' in first_row_text and '同意报价人' in first_row_text and len(rows) > 1:
                        for text_el in rows[1].findall('.//' + w('t')):
                            text_el.text = ''
                        break
                modified_xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)

                for item in src.infolist():
                    data = modified_xml if item.filename == 'word/document.xml' else src.read(item.filename)
                    dst.writestr(item, data)

            rc, out, err = _run_script('verify_quotation.py', ['--input', broken_path, '--data', data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('未在签名区域找到签名公司名', out)

    def test_verify_fails_when_meta_entity_or_currency_mismatch(self):
        """Verification must compare document entity/currency with quotation.json _meta."""
        with tempfile.TemporaryDirectory(prefix='quotation-meta-mismatch-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            bad_data_path = os.path.join(tmpdir, 'quotation-bad-meta.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            bad_data = json.loads(json.dumps(data))
            bad_data['_meta']['applicable_entity'] = 'jakarta'
            bad_data['_meta']['target_currency'] = 'IDR'
            with open(bad_data_path, 'w', encoding='utf-8') as f:
                json.dump(bad_data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', bad_data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('文档签约主体', out)
            self.assertIn('文档币种', out)

    def test_verify_fails_when_cli_entity_conflicts_with_meta(self):
        """--entity must not mask a conflicting quotation.json _meta entity."""
        with tempfile.TemporaryDirectory(prefix='quotation-cli-meta-mismatch-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'jakarta'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertNotEqual(rc, 0)
            self.assertIn('_meta.applicable_entity', err)
            self.assertFalse(os.path.exists(output_path))

    def test_build_rejects_meta_target_currency_not_allowed_for_entity(self):
        """Build must reject target currencies that the selected entity cannot quote."""
        with tempfile.TemporaryDirectory(prefix='quotation-build-currency-mismatch-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['target_currency'] = 'IDR'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertNotEqual(rc, 0)
            self.assertIn('_meta.target_currency', err)
            self.assertFalse(os.path.exists(output_path))

    def test_validate_rejects_meta_target_currency_mismatch(self):
        """Preflight should catch _meta currency drift before build."""
        with tempfile.TemporaryDirectory(prefix='quotation-meta-currency-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['target_currency'] = 'IDR'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('_meta.target_currency', out)

    def test_validate_rejects_withholding_tax_for_entity_without_rate(self):
        """预扣税只有配了 withholding_tax_rate 的主体支持（当前仅 thailand）。

        非泰国主体写 true 时 build 会静默按不扣税生成，verify 才报「expected in data but
        not found in document」——预检必须提前拦下，否则问题拖到最后一步才暴露。
        """
        with tempfile.TemporaryDirectory(prefix='quotation-wht-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['withholding_tax'] = True

            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'xian', '--data', data_path])
            self.assertNotEqual(rc, 0, f"validate 应拦下非泰国主体的 withholding_tax:\n{out}")
            self.assertIn('withholding_tax 仅支持', out)

    def test_validate_warns_when_thailand_omits_withholding_tax(self):
        """泰国主体漏填 withholding_tax：预检要提醒（SKILL 要求必须先问用户），但不阻断。"""
        with tempfile.TemporaryDirectory(prefix='quotation-wht-th-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'thailand'
            data['_meta']['target_currency'] = 'THB'
            data.pop('withholding_tax', None)

            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'thailand', '--data', data_path])
            self.assertEqual(rc, 0, f"漏填只应提醒，不应阻断:\n{out}")
            self.assertIn('缺少顶层 withholding_tax 字段', out)

    def test_thailand_withholding_tax_is_rendered_and_verifies(self):
        """泰国 + withholding_tax: true：报价单出现预扣税行，verify 的金额公式复核通过。"""
        with tempfile.TemporaryDirectory(prefix='quotation-wht-render-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            output_path = os.path.join(tmpdir, '报价单-泰国-预扣税.docx')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'thailand'
            data['_meta']['target_currency'] = 'THB'
            data['services'][0]['items'][0]['price'] = 52000
            data['withholding_tax'] = True

            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('validate_data.py', ['--entity', 'thailand', '--data', data_path])
            self.assertEqual(rc, 0, f"validate failed:\n{out}\n{err}")

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'thailand', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build failed:\n{out}\n{err}")

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--entity', 'thailand', '--input', output_path, '--data', data_path]
            )
            self.assertEqual(rc, 0, f"verify failed:\n{out}\n{err}")

            with zipfile.ZipFile(output_path) as zf:
                root = ET.fromstring(zf.read('word/document.xml'))
            all_text = ''.join(t.text or '' for t in root.iter(
                '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
            self.assertIn('预扣税 3%', all_text)

    def test_build_reports_payment_terms_source_for_new_output(self):
        """输出到新文件时，build 必须说清付款方式取自哪里，并提示旧单子上的手改不会被沿用。"""
        with tempfile.TemporaryDirectory(prefix='quotation-payment-source-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            edited_path = os.path.join(tmpdir, '报价单-旧版.docx')
            new_path = os.path.join(tmpdir, '报价单-新版.docx')

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', edited_path]
            )
            self.assertEqual(rc, 0, f"首次 build failed:\n{out}\n{err}")

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', new_path]
            )
            self.assertEqual(rc, 0, f"第二次 build failed:\n{out}\n{err}")
            self.assertIn('付款方式来源：quotation.json', out)
            self.assertIn('--preserve-payment-from', out,
                          f"输出新文件且有同目录旧单子时必须提示保留入口:\n{out}")

    def test_convert_currency_json_only_stdout_is_parseable_json(self):
        """--json-only keeps stdout machine-readable for agents."""
        rc, out, err = _run_script(
            'convert_currency.py',
            ['--amount', '250000000', '--from', 'IDR', '--to', 'RMB', '--rateToCny', '2173.91', '--json-only']
        )
        self.assertEqual(rc, 0, f"convert_currency.py failed:\nstdout: {out}\nstderr: {err}")
        parsed = json.loads(out)
        self.assertEqual(parsed['from_currency'], 'IDR')
        self.assertEqual(parsed['to_currency'], 'RMB')
        self.assertIn('IDR', err)

    def test_batch_convert_exits_nonzero_on_any_error(self):
        """Batch conversion must fail at process level when any service conversion fails."""
        with tempfile.TemporaryDirectory(prefix='quotation-convert-error-') as tmpdir:
            query_path = os.path.join(tmpdir, 'queried_services.json')
            with open(query_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'success': True,
                    'services': [{
                        '服务名称': '缺汇率服务',
                        '服务币种': 'IDR',
                        '服务价格': 250000000,
                        '人民币兑换服务币种汇率': None,
                        '美元兑换服务币种汇率': None,
                    }],
                }, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('convert_currency.py', ['--query-result', query_path, '--to', 'RMB'])
            self.assertNotEqual(rc, 0)
            self.assertIn('rateToCny is required', out)


    def test_single_convert_no_json_only_stdout_is_human_readable(self):
        """Without --json-only, stdout must not contain JSON — human-readable only."""
        rc, out, err = _run_script(
            'convert_currency.py',
            ['--amount', '250000000', '--from', 'IDR', '--to', 'RMB', '--rateToCny', '2173.91']
        )
        self.assertEqual(rc, 0, f"convert_currency.py failed:\nstdout: {out}\nstderr: {err}")
        # Human-readable output must be present.
        self.assertIn('IDR', out)
        self.assertIn('RMB', out)
        # JSON keys must NOT appear in stdout.
        self.assertNotIn('"from_currency"', out)
        self.assertNotIn('"converted"', out)

    def test_batch_convert_no_json_only_stdout_is_human_readable(self):
        """Batch mode without --json-only must not dump JSON to stdout."""
        with tempfile.TemporaryDirectory(prefix='quotation-nojson-batch-') as tmpdir:
            query_path = os.path.join(tmpdir, 'queried_services.json')
            with open(query_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'success': True,
                    'services': [{
                        '服务名称': '测试服务',
                        '服务币种': 'IDR',
                        '服务价格': 250000000,
                        '人民币兑换服务币种汇率': '2173.91',
                        '美元兑换服务币种汇率': '16000',
                    }],
                }, f, ensure_ascii=False, indent=2)

            rc, out, err = _run_script('convert_currency.py', ['--query-result', query_path, '--to', 'RMB'])
            self.assertEqual(rc, 0, f"convert_currency.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('IDR', out)
            self.assertIn('RMB', out)
            # JSON structure must NOT appear in stdout.
            self.assertNotIn('"conversions"', out)

    def test_shanghai_end_to_end(self):
        """Smoke test using shanghai entity (RMB, 1% VAT)."""
        with tempfile.TemporaryDirectory(prefix='quotation-smoke-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'shanghai'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-上海-测试.docx')

            rc, out, err = _run_script('validate_data.py', ['--entity', 'shanghai', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('数据校验通过', out)
            self.assertIn('1%', out)  # Verify 1% VAT rate

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'shanghai', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('增值税: 1%', out)

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('验证通过', out)

            _assert_docx_valid(self, output_path)

    def test_shanghai_new_end_to_end(self):
        """Smoke test using shanghai_new entity (RMB, 1% VAT)."""
        with tempfile.TemporaryDirectory(prefix='quotation-smoke-') as tmpdir:
            src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
            data_path = os.path.join(tmpdir, 'quotation.json')
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'shanghai_new'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-上海新企业-测试.docx')

            rc, out, err = _run_script('validate_data.py', ['--entity', 'shanghai_new', '--data', data_path])
            self.assertEqual(rc, 0, f"validate_data.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('数据校验通过', out)
            self.assertIn('1%', out)  # Verify 1% VAT rate

            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'shanghai_new', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('增值税: 1%', out)

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('验证通过', out)

            _assert_docx_valid(self, output_path)

    def test_verify_accepts_header_address_that_differs_from_bank_branch(self):
        """A registered office and its bank branch may sit in different cities.

        Vietnam's registered office is in Ho Chi Minh City while the account is held
        at a Hanoi branch, so verify must validate the header address against the
        entity's own configured address instead of demanding equality with the bank
        address.
        """
        with tempfile.TemporaryDirectory(prefix='quotation-vn-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'vietnam'
            data['_meta']['target_currency'] = 'VND'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-越南-测试.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'vietnam', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--entity', 'vietnam', '--input', output_path, '--data', data_path]
            )
            self.assertNotIn(
                '页眉地址与银行地址不一致', out,
                "header address and bank branch address are allowed to differ")
            self.assertEqual(rc, 0, f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")
            self.assertIn('验证通过', out)

    def test_verify_rejects_header_address_from_another_entity(self):
        """Tolerating a header/bank-branch difference must not disable the check.

        The header address is now validated against the entity's own configured
        address, so a header carrying some *other* entity's address still fails
        verification.
        """
        # Uses a text-header entity on purpose: for the ones configured with
        # ``header_image`` there is no header address text left to tamper with, and
        # verify skips the ownership check there by design (see
        # test_verify_skips_header_ownership_checks_for_image_headers).
        with tempfile.TemporaryDirectory(prefix='quotation-tamper-') as tmpdir:
            data_path, output_path = _build_for_entity(self, tmpdir, 'thailand')

            # Swap thailand's header address for vietnam's, leaving everything else intact.
            tampered_path = os.path.join(tmpdir, '报价单-泰国-篡改.docx')
            _replace_docx_visible_text(
                output_path, tampered_path,
                'Thanapoom Tower, 25th floor Unit A2, 1550 New Petchaburi Rd, ',
                'Tầng 6, Số 89 Phan Đình Phùng, Phường Phú Nhuận,TPHCM.',
                part='word/header1.xml')

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--entity', 'thailand', '--input', tampered_path, '--data', data_path]
            )
            self.assertNotEqual(
                rc, 0, f"verify_quotation.py accepted a foreign header address:\n{out}")
            self.assertIn('不属于本主体配置地址', out)

    def test_verify_skips_header_ownership_checks_for_image_headers(self):
        """图片页眉没有可核对的文字，verify 必须**显式**跳过那两项检查。

        「显式跳过」和「因为取不到文字而顺带没跑」在输出上长得一样，但对以后维护的人
        不是一回事：后者意味着页眉被改回文字版、或者横幅整个丢了，都没人会发现。所以
        这里两头都要断言——跳过有说明，而图片页眉该有的自查照跑、丢了会拦下。
        """
        for entity in ('jakarta', 'xian'):
            with self.subTest(entity=entity):
                with tempfile.TemporaryDirectory(prefix=f'quotation-imgchk-{entity}-') as tmpdir:
                    data_path, output_path = _build_for_entity(
                        self, tmpdir, entity,
                        example='sample_quotation.json' if entity == 'jakarta'
                                else 'minimal_quotation.json')

                    rc, out, err = _run_script(
                        'verify_quotation.py',
                        ['--entity', entity, '--input', output_path, '--data', data_path])
                    self.assertEqual(rc, 0,
                                     f"verify_quotation.py failed:\nstdout: {out}\nstderr: {err}")
                    self.assertIn('跳过「页眉公司名 vs 银行」与「页眉地址归属」检查', out,
                                  "verify 跳过了图片页眉的两项检查，却没有说明")
                    self.assertIn('页眉横幅图版式与配置一致', out,
                                  "verify 没有自查图片页眉的版式")

                    # 跳过的是「内容核对」，不是「页眉整个不看了」：横幅没了仍须拦下。
                    stripped = os.path.join(tmpdir, '报价单-缺横幅.docx')
                    _drop_drawings_from_header(output_path, stripped, keep_shape=True)
                    rc, out, err = _run_script(
                        'verify_quotation.py',
                        ['--entity', entity, '--input', stripped, '--data', data_path])
                    self.assertNotEqual(
                        rc, 0, f"verify_quotation.py accepted a header with no banner:\n{out}")
                    self.assertIn('页眉里找不到横幅图', out)

    def test_all_entity_templates_share_one_header_layout(self):
        """Every entity template must use the same compact header layout.

        Canonical layout: company-name paragraph, address line(s), the "Web:" line,
        then exactly one trailing paragraph that carries the logo and the blue
        separator line. No spacer paragraphs, and no template missing the separator.
        """
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
        from build_quotation import TEMPLATES

        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        for key, path in sorted(TEMPLATES.items()):
            with self.subTest(template=key):
                with zipfile.ZipFile(path) as zf:
                    root = ET.fromstring(zf.read('word/header1.xml'))
                paras = [p for p in root if p.tag == f'{{{W}}}p']
                texts = [''.join(t.text or '' for t in p.iter(f'{{{W}}}t')).strip() for p in paras]
                last = max(i for i, t in enumerate(texts) if t)

                self.assertTrue(
                    texts[last].startswith('Web:'),
                    f"{key}: last text paragraph should be the Web line, got {texts[last]!r}")
                trailing = paras[last + 1:]
                self.assertEqual(
                    len(trailing), 1,
                    f"{key}: expected exactly 1 trailing paragraph after the Web line, "
                    f"got {len(trailing)} — templates must all use the same layout")
                self.assertIsNone(
                    paras[last].find(f'.//{{{W}}}drawing'),
                    f"{key}: separator must not be anchored inside the Web paragraph")
                self.assertIsNotNone(
                    trailing[0].find(f'.//{{{W}}}drawing'),
                    f"{key}: trailing paragraph must carry the blue separator line")
                spacing = trailing[0].find(f'{{{W}}}pPr/{{{W}}}spacing')
                self.assertNotEqual(
                    spacing.get(f'{{{W}}}line') if spacing is not None else None, '0',
                    f"{key}: trailing paragraph must not be collapsed to zero height")

    def test_logo_and_separator_share_one_anchor_paragraph(self):
        """The logo and the blue separator must be anchored in the same paragraph.

        Both anchors measure their offset from their own paragraph's top, so a shared
        paragraph makes the clearance

            line_y - logo_ink_bottom
                = separator_offset - logo_offset - logo_ink_height

        a constant of the template. While the logo hung off the header's first
        paragraph and the line off its last, only the line's offset rode the header
        text block — whose height is font-metric dependent — so on a machine without
        微软雅黑 the two drifted together and the line ran into the logo.
        """
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
        from build_quotation import TEMPLATES

        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
        A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        EMU_PER_PT = 12700

        for key, path in sorted(TEMPLATES.items()):
            with self.subTest(template=key):
                with zipfile.ZipFile(path) as zf:
                    root = ET.fromstring(zf.read('word/header1.xml'))
                    targets = {rel.get('Id'): rel.get('Target') for rel in ET.fromstring(
                        zf.read('word/_rels/header1.xml.rels'))}
                    logo_png = zf.read('word/' + targets[
                        next(root.iter(f'{{{A}}}blip')).get(f'{{{R}}}embed')].lstrip('/'))
                paras = [p for p in root if p.tag == f'{{{W}}}p']

                logo = line = None
                for anchor in root.iter(f'{{{WP}}}anchor'):
                    if anchor.find(f'.//{{{A}}}blip') is not None:
                        logo = anchor
                    else:
                        line = anchor
                self.assertIsNotNone(logo, f"{key}: header has no logo anchor")
                self.assertIsNotNone(line, f"{key}: header has no separator anchor")

                def owning_paragraph(anchor):
                    for index, para in enumerate(paras):
                        if any(a is anchor for a in para.iter(f'{{{WP}}}anchor')):
                            return index
                    return None

                self.assertEqual(
                    owning_paragraph(logo), owning_paragraph(line),
                    f"{key}: logo and separator are anchored in different paragraphs, "
                    f"so their clearance rides the header text block's height and "
                    f"changes with the CJK font")

                def offset(anchor):
                    return int(anchor.find(f'{{{WP}}}positionV/{{{WP}}}posOffset').text)

                # Clearance to the mark's visible bottom edge, not to the bitmap box:
                # the box is what the offsets position, the ink is what a reader sees.
                box = int(logo.find(f'{{{WP}}}extent').get('cy'))
                ink = box * (1 - _png_alpha_bottom_padding(logo_png))
                clearance = offset(line) - offset(logo) - ink
                self.assertGreater(
                    clearance, 0,
                    f"{key}: the separator sits {clearance / EMU_PER_PT:.2f}pt below the "
                    f"logo's bottom edge — the blue line is overlapping the logo")

    def test_trailing_header_paragraph_takes_its_height_from_its_mark(self):
        """No run in the header's trailing paragraph may declare a font size.

        That paragraph's height is the room the body gets below the blue line, and a
        paragraph's line height follows the largest font among its runs — even for a
        run holding nothing but a floating drawing. Carrying the logo into that
        paragraph therefore also carried the size that run needed where it used to
        live (w:sz 40/44 = 20/22pt, against a mark that declares none), which drops
        the body by roughly 11pt on every page. Only the mark sets the height here;
        the logo's size comes from wp:extent.
        """
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
        from build_quotation import TEMPLATES

        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        for key, path in sorted(TEMPLATES.items()):
            with self.subTest(template=key):
                with zipfile.ZipFile(path) as zf:
                    root = ET.fromstring(zf.read('word/header1.xml'))
                last = [p for p in root if p.tag == f'{{{W}}}p'][-1]
                for run in last.findall(f'{{{W}}}r'):
                    size = run.find(f'{{{W}}}rPr/{{{W}}}sz')
                    self.assertIsNone(
                        size,
                        f"{key}: a run in the trailing header paragraph declares "
                        f"sz={size.get(f'{{{W}}}val') if size is not None else None}, so it "
                        f"now sets that paragraph's line height and pushes the body "
                        f"down the page")

    def test_build_normalizes_non_a4_template_to_a4(self):
        """A drifted (non-A4) template must still yield an A4-printable quotation.

        build_quotation.py copies the template's sectPr verbatim, so a Letter-sized
        template used to produce a Letter-sized quotation while the log claimed
        "Restored sectPr → A4 paper, printable" — a false A4 guarantee.
        """
        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        with tempfile.TemporaryDirectory(prefix='quotation-a4-') as tmpdir:
            letter = _make_non_a4_template(
                os.path.join(SKILL_ROOT, 'assets', '报价单模板-中国公司.docx'),
                os.path.join(tmpdir, 'letter.docx'))

            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'xian'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            output_path = os.path.join(tmpdir, 'out.docx')

            sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
            import build_quotation
            saved_template = build_quotation.TEMPLATES['china']
            saved_argv = sys.argv
            build_quotation.TEMPLATES['china'] = letter
            sys.argv = ['build_quotation.py', '--entity', 'xian',
                        '--data', data_path, '--output', output_path]
            try:
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    build_quotation.main()
            finally:
                build_quotation.TEMPLATES['china'] = saved_template
                sys.argv = saved_argv
            log = captured.getvalue()

            with zipfile.ZipFile(output_path) as zf:
                root = ET.fromstring(zf.read('word/document.xml'))
            sect = root.find(f'{{{W}}}body/{{{W}}}sectPr')
            pg = sect.find(f'{{{W}}}pgSz')
            self.assertEqual(
                (pg.get(f'{{{W}}}w'), pg.get(f'{{{W}}}h')), ('11906', '16838'),
                f"a non-A4 template must be normalized to A4, got "
                f"{pg.get(f'{{{W}}}w')}x{pg.get(f'{{{W}}}h')}\n{log}")
            self.assertIsNone(pg.get(f'{{{W}}}orient'),
                              "normalized page must not carry a stale orient attribute")

            mar = sect.find(f'{{{W}}}pgMar')
            for key, expected in (('top', '679'), ('right', '1133'),
                                  ('bottom', '1155'), ('left', '1440')):
                self.assertEqual(
                    mar.get(f'{{{W}}}{key}'), expected,
                    f"margin {key} must be normalized to the canonical value")

            self.assertNotIn(
                '12240 x 15840 DXA (A4)', log,
                "build must not describe a non-A4 page as A4")

    def test_verify_rejects_drifted_page_margins(self):
        """A4 print-safety must cover margins, not just page size.

        Page size alone can be right while the margins push content outside what
        a printer can reach, so verify checks both.
        """
        with tempfile.TemporaryDirectory(prefix='quotation-margin-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'xian'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            drifted_path = os.path.join(tmpdir, '报价单-西安-页边距漂移.docx')
            _tamper_page_margin(output_path, drifted_path, 'right', 2000)

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--input', drifted_path, '--data', data_path]
            )
            self.assertNotEqual(
                rc, 0, f"verify accepted a drifted right margin:\n{out}")
            self.assertIn("Page margin 'right'", out)

    def test_long_company_names_are_right_aligned(self):
        """Company names too long to center must be right-aligned in the header.

        Centering one of these runs its left end into the floating logo, which is
        anchored in the same paragraph band. Right-aligning pins the name's right
        edge to the right margin, which keeps it clear of the logo. Thailand was
        the last entity still centered (and the only one actually colliding).
        """
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
        from quotation_common import load_entity_config

        entity_config, _ = load_entity_config()
        for entity in ('thailand', 'vietnam', 'egypt', 'singapore'):
            with self.subTest(entity=entity):
                self.assertEqual(
                    entity_config[entity].get('header_company_align'), 'right',
                    f"{entity}: company name is long enough to collide with the logo, "
                    f"so it must be configured right-aligned")

        with tempfile.TemporaryDirectory(prefix='quotation-align-') as tmpdir:
            data_path = _copy_example(tmpdir, 'minimal_quotation.json')
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'thailand'
            data['_meta']['target_currency'] = 'THB'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-泰国-对齐.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'thailand', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            with zipfile.ZipFile(output_path) as zf:
                root = ET.fromstring(zf.read('word/header1.xml'))
            paras = [p for p in root if p.tag == f'{{{W}}}p']
            company_para = next(
                p for p in paras
                if ''.join(t.text or '' for t in p.iter(f'{{{W}}}t')).strip())
            jc = company_para.find(f'{{{W}}}pPr/{{{W}}}jc')
            self.assertIsNotNone(jc, "company name paragraph carries no jc")
            self.assertEqual(jc.get(f'{{{W}}}val'), 'right',
                             "built thailand header must right-align the company name")

    def test_build_preserves_header_layout(self):
        """build_quotation.py must leave a text header's geometry intact.

        The separator's floating anchor sits a fixed offset below its paragraph, so
        editing the paragraph's height or dropping it moves the blue line off its
        intended position — onto the body's first line ("公司名称：") or the logo
        anchored in that same paragraph.

        Entities configured with ``header_image`` are out of scope here: build
        replaces their header wholesale, and the layout that must hold for them is
        asserted in test_image_header_replaces_text_header_with_anchored_banner.

        Note the ``china`` template has no case below: every entity that uses it
        (beijing/xian/shenzhen/shanghai/shanghai_new) is an image header now, so
        build never renders its text header. The template file itself is still
        covered by test_all_entity_templates_share_one_header_layout.
        """
        cases = [
            ('thailand', '报价单模板-泰国公司.docx'),
            ('singapore', '报价单模版-新加坡公司.docx'),
            ('egypt', '报价单模版-埃及公司.docx'),
            ('malaysia', '报价单模版-马来西亚公司.docx'),
        ]
        for entity, template_name in cases:
            with self.subTest(entity=entity):
                template = os.path.join(SKILL_ROOT, 'assets', template_name)
                with tempfile.TemporaryDirectory(prefix='quotation-header-') as tmpdir:
                    _, output_path = _build_for_entity(self, tmpdir, entity)
                    self.assertEqual(
                        _header_trailing_paragraph_count(output_path),
                        _header_trailing_paragraph_count(template),
                        f"{entity}: build changed the header's trailing paragraphs, "
                        f"so the blue line no longer sits where the template puts it")

    def test_image_header_replaces_text_header_with_anchored_banner(self):
        """每个配了 header_image 的主体，页眉必须是一幅锚定的横幅图。

        横幅是浮动锚定（wp:anchor + wrapNone），不占版式高度；原图按 alpha 边界用
        a:srcRect 裁掉透明边距（PNG 文件本身不动），所以图片框就是图墨迹本身——可见
        内容一个像素不变，框却不会顶到纸张上缘、也不会越过正文栏。版式靠锚点偏移量
        摆位，靠末段的 space-after 给正文留白。所以这里除了核对「图在不在」，还要核对
        它落的位置与 entities.json 算出来的一致、且确实没越界。
        """
        import sys as _sys
        _sys.path.insert(0, os.path.join(SKILL_ROOT, 'scripts'))
        from quotation_common import (
            header_image_spec, header_image_geometry, text_column_mm,
            MIN_PRINTABLE_INK_TOP_MM,
        )

        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
        A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'
        R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

        # Read the raw JSON rather than load_entity_config(), which strips `_meta`
        # (where header_image_defaults lives) and validates fields this test
        # doesn't care about.
        with open(os.path.join(SKILL_ROOT, 'config', 'entities.json'), encoding='utf-8') as f:
            config = json.load(f)
        defaults = config.get('_meta', {}).get('header_image_defaults')
        image_entities = sorted(
            key for key, cfg in config.items()
            if not key.startswith('_') and header_image_spec(cfg, defaults))
        self.assertTrue(image_entities, "no entity configures header_image")

        for entity in image_entities:
            with self.subTest(entity=entity):
                spec = header_image_spec(config[entity], defaults)
                png_path = os.path.join(SKILL_ROOT, 'assets', spec['file'])
                self.assertTrue(os.path.exists(png_path), f"{entity}: 页眉图不存在 {png_path}")
                geom = header_image_geometry(spec, png_path)

                # bbox_px 必须是这张图**实测**的 alpha 边界。build 的裁剪与缩放都从
                # 它推出来，所以配置自洽不代表版面对——填错只会让横幅被裁歪、被拉伸，
                # 而下面那些「build == entities.json」的断言照样全过。只有图本身能
                # 提供正确答案，故这里独立量一次（换图时最容易忘的就是同步这个值）。
                with open(png_path, 'rb') as f:
                    measured = _png_alpha_bbox(f.read())
                self.assertEqual(
                    tuple(spec['bbox_px']), measured,
                    f"{entity}: header_image.bbox_px {spec['bbox_px']} 与 "
                    f"{spec['file']} 实测 alpha 边界 {list(measured)} 不符")

                with tempfile.TemporaryDirectory(prefix=f'quotation-img-{entity}-') as tmpdir:
                    _, output_path = _build_for_entity(self, tmpdir, entity)

                    # 页眉顶/页脚底距纸张边缘 0.4cm —— 图片页眉靠它把整幅图顶上去
                    with zipfile.ZipFile(output_path) as zf:
                        doc_root = ET.fromstring(zf.read('word/document.xml'))
                        header_root = ET.fromstring(zf.read('word/header1.xml'))
                        targets = {rel.get('Id'): rel.get('Target') for rel in ET.fromstring(
                            zf.read('word/_rels/header1.xml.rels'))}

                    pg_mar = doc_root.find(f'.//{{{W}}}sectPr/{{{W}}}pgMar')
                    self.assertEqual(pg_mar.get(f'{{{W}}}header'), '227',
                                     f"{entity}: 页眉距纸张上缘应为 0.4cm (227 DXA)")
                    self.assertEqual(pg_mar.get(f'{{{W}}}footer'), '227',
                                     f"{entity}: 页脚距纸张下缘应为 0.4cm (227 DXA)")

                    paras = [p for p in header_root if p.tag == f'{{{W}}}p']
                    self.assertEqual(
                        len(paras), 1,
                        f"{entity}: 图片页眉应只有 1 段（承载横幅与蓝线），实得 {len(paras)}")
                    self.assertEqual(
                        ''.join(t.text or '' for t in paras[0].iter(f'{{{W}}}t')).strip(), '',
                        f"{entity}: 图片页眉段不应再残留文字页眉")

                    banner = line = None
                    for drawing in paras[0].iter(f'{{{W}}}drawing'):
                        anchor = drawing.find(f'{{{WP}}}anchor')
                        if anchor is None:
                            # 内联图会真实占位，等于把裁剪/留白问题又请回来
                            self.fail(f"{entity}: 页眉图必须是浮动锚定，不能是 wp:inline")
                        if drawing.find(f'.//{{{WPS}}}wsp') is not None:
                            line = anchor
                        elif drawing.find(f'.//{{{A}}}blip') is not None:
                            banner = anchor
                    self.assertIsNotNone(banner, f"{entity}: 页眉里没有横幅图")
                    self.assertIsNotNone(line, f"{entity}: 页眉里没有蓝色分隔线")
                    self.assertIsNotNone(
                        banner.find(f'{{{WP}}}wrapNone'),
                        f"{entity}: 横幅图缺 wrapNone，浮动图会占版式高度")

                    # 必须按 alpha 边界裁剪：不裁的话透明边距会把图片框撑到纸张上缘
                    # 之外、横向越过正文栏（这正是这次改版要修掉的问题）。
                    src_rect = banner.find(f'.//{{{A}}}srcRect')
                    self.assertIsNotNone(
                        src_rect, f"{entity}: 横幅图没有按 alpha 边界裁剪（缺 a:srcRect）")
                    for side, expected in geom['src_rect'].items():
                        self.assertEqual(
                            src_rect.get(side), str(expected),
                            f"{entity}: 横幅裁剪 srcRect {side} 与 entities.json 的 "
                            f"bbox_px 不符")
                    self.assertEqual(
                        banner.find(f'{{{WP}}}extent').get('cx'), str(geom['extent_cx']),
                        f"{entity}: 横幅宽度应为裁剪后的墨迹宽度")
                    self.assertEqual(
                        banner.find(f'{{{WP}}}extent').get('cy'), str(geom['extent_cy']),
                        f"{entity}: 横幅高度应为裁剪后的墨迹高度（按原图长宽比）")

                    for tag, expected in (('positionH', str(geom['banner_off_h'])),
                                          ('positionV', str(geom['banner_off_v']))):
                        self.assertEqual(
                            banner.find(f'{{{WP}}}{tag}/{{{WP}}}posOffset').text, expected,
                            f"{entity}: 横幅 {tag} 与 entities.json 算出的值不符")

                    # 嵌入的就是原图本身，一个像素都没动
                    embed = banner.find(f'.//{{{A}}}blip').get(f'{{{R}}}embed')
                    with zipfile.ZipFile(output_path) as zf:
                        embedded = zf.read('word/' + targets[embed].lstrip('/'))
                    with open(png_path, 'rb') as f:
                        self.assertEqual(
                            embedded, f.read(),
                            f"{entity}: 嵌入的横幅图与 assets/ 原图不一致（被改动过）")

                    self.assertGreaterEqual(
                        spec['ink_top_mm'], MIN_PRINTABLE_INK_TOP_MM,
                        f"{entity}: 图墨顶 {spec['ink_top_mm']}mm 太贴近纸张上缘，"
                        f"会落进打印机不可打印区")

                    # 裁过之后图片框就是图墨迹本身，所以框本身也不许越界：横向落在
                    # 正文栏内，纵向压在正文首行之上（顶边由上面的可打印区检查兜住）。
                    col_left_mm, col_w_mm = text_column_mm()
                    self.assertGreaterEqual(
                        geom['ink_left_mm'], col_left_mm - 0.1,
                        f"{entity}: 横幅左缘 {geom['ink_left_mm']:.2f}mm 越出正文栏左边界 "
                        f"{col_left_mm:.2f}mm")
                    self.assertLessEqual(
                        geom['ink_left_mm'] + geom['ink_w_mm'], col_left_mm + col_w_mm + 0.1,
                        f"{entity}: 横幅右缘越出正文栏右边界 {col_left_mm + col_w_mm:.2f}mm")
                    self.assertLessEqual(
                        geom['ink_bottom_mm'], spec['body_top_mm'] + 0.1,
                        f"{entity}: 横幅框底 {geom['ink_bottom_mm']:.2f}mm 压过正文首行 "
                        f"{spec['body_top_mm']}mm")


class TestDocLineIndent(unittest.TestCase):
    """doc_data[].docs 的行首全角空格 → 段落左缩进 w:ind（v1.17.1）。

    一个数组元素渲染成一个段落；行首每 1 个全角空格（U+3000）＝ 1 级 ＝ 360 twips。
    层级必须在数据归一化（item.strip() 会吃掉行首空白）之前从原始 JSON 读取，这里用成稿回读校验。
    """

    _W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    _HEADER = '所需资料及信息'

    def _build_docx(self, tmpdir, docs, tag):
        with open(os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json'), encoding='utf-8') as f:
            data = json.load(f)
        data['doc_data'][0]['docs'] = docs
        data_path = os.path.join(tmpdir, f'{tag}.json')
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        out = os.path.join(tmpdir, f'{tag}.docx')
        rc, out_text, err = _run_script(
            'build_quotation.py', ['--entity', 'xian', '--data', data_path, '--output', out])
        self.assertEqual(rc, 0, f'build failed:\n{out_text}\n{err}')
        return out, out_text

    def _doc_column(self, docx_path):
        """返回「所需资料及信息」列每行的 (文本, w:ind left 或 0)。"""
        w = lambda tag: f'{{{self._W}}}{tag}'  # noqa: E731
        with zipfile.ZipFile(docx_path) as zf:
            root = ET.fromstring(zf.read('word/document.xml'))
        for tbl in root.iter(w('tbl')):
            rows = list(tbl.findall(w('tr')))
            if not rows:
                continue
            header = [''.join(t.text or '' for t in tc.iter(w('t'))) for tc in rows[0].findall(w('tc'))]
            if not any(self._HEADER in h for h in header):
                continue
            col = next(i for i, h in enumerate(header) if self._HEADER in h)
            out = []
            for row in rows[1:]:
                cells = row.findall(w('tc'))
                if len(cells) <= col:
                    continue
                for p in cells[col].findall(w('p')):
                    text = ''.join(t.text or '' for t in p.iter(w('t')))
                    if not text.strip():
                        continue
                    left = 0
                    ind = p.find(f'{w("pPr")}/{w("ind")}')
                    if ind is not None and ind.get(w('left')):
                        left = int(ind.get(w('left')))
                    out.append((text, left))
            return out
        self.fail('未找到「所需资料及信息」表')

    def test_indented_lines_get_w_ind(self):
        docs = [
            '1. 股东证件及信息：',
            '\u3000（1）由个人当股东：',
            '\u3000\u3000a. 印尼籍：身份证、税卡',
            '\u3000（2）由外国公司当股东：',
        ]
        with tempfile.TemporaryDirectory(prefix='quotation-indent-') as tmpdir:
            docx, out_text = self._build_docx(tmpdir, docs, 'indent')
            lines = self._doc_column(docx)
            self.assertEqual([lv for _, lv in lines], [0, 360, 720, 360],
                             f'行缩进不符：{lines}')
            # 层级标记本身不应留在文本里（schema 的 strip 负责剥掉）
            self.assertTrue(all('\u3000' not in t for t, _ in lines), f'行首全角空格未被剥掉：{lines}')
            self.assertIn('所需资料及信息行缩进：3 行', out_text)

    def test_plain_lines_stay_flat(self):
        """没有层级标注的旧数据必须照旧顶格（纯增量，不写 w:ind）。"""
        docs = ['1. 护照首页扫描件', '2. 营业执照副本']
        with tempfile.TemporaryDirectory(prefix='quotation-flat-') as tmpdir:
            docx, out_text = self._build_docx(tmpdir, docs, 'flat')
            lines = self._doc_column(docx)
            self.assertEqual([lv for _, lv in lines], [0, 0], f'不应有缩进：{lines}')
            self.assertNotIn('所需资料及信息行缩进', out_text)

    def test_half_width_and_nbsp_are_not_levels(self):
        """半角空格 / NBSP / 填充字符都不算层级（会被 strip 或零位移），只有全角空格算。"""
        docs = ['1. 顶格行', '   a. 半角空格缩进无效', '\u00a0b. NBSP 无效', '\u3164c. 填充字符无效',
                '\u3000（1）全角空格有效']
        with tempfile.TemporaryDirectory(prefix='quotation-fill-') as tmpdir:
            docx, _ = self._build_docx(tmpdir, docs, 'fill')
            lines = self._doc_column(docx)
            self.assertEqual([lv for _, lv in lines], [0, 0, 0, 0, 360], f'层级判定不符：{lines}')


if __name__ == '__main__':
    unittest.main()
