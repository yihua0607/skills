"""XML 安全性：非法控制字符不得弄坏产物，verify 必须给出可执行的报错。

背景：ElementTree 只转义 ``&<>``，不剔除 XML 1.0 非法字符。从 Word/PDF 复制粘贴
带进来的 NUL/ESC/BEL，或手写 JSON 里的 ``\\u0007``，会一路写进 ``w:t``：document.xml
变成 not well-formed，但 build 照常打印成功、退出码 0；verify 再抛出未捕获的
ParseError traceback —— 故障于是被误判成「SKILL 有 bug」，真实原因却是输入数据脏。

这里锁死三个环节：清洗函数本身、所有写入口都被清洗覆盖、以及真坏掉时报错可执行。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_ROOT not in sys.path:
    sys.path.insert(0, SKILL_ROOT)

from scripts.quotation_common import xml_safe_text

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

# XML 1.0 Char = #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]。
# C0 控制符里除 \t \n \r 外全部非法；孤立代理项落在 D7FF/E000 之间，U+FFFE/U+FFFF
# 落在 FFFD 之后，同样非法。注意 DEL(0x7F) 在 [#x20-#xD7FF] 内，是合法字符，不在此列。
ILLEGAL_CHARS = {
    'NUL': '\x00', 'BEL': '\x07', 'ESC': '\x1b', 'BS': '\x08', 'SO': '\x0e',
    'U+FFFE': chr(0xFFFE), 'U+FFFF': chr(0xFFFF),
    'lone surrogate': '\ud800',
}


def _run_script(name, args):
    result = subprocess.run(
        [sys.executable, os.path.join(SKILL_ROOT, 'scripts', name)] + args,
        cwd=SKILL_ROOT, capture_output=True, text=True, encoding='utf-8')
    return result.returncode, result.stdout, result.stderr


def _docx_texts(docx_path):
    with zipfile.ZipFile(docx_path) as zf:
        root = ET.fromstring(zf.read('word/document.xml'))
    return [t.text for t in root.iter(f'{W}t') if t.text]


class TestXmlSafeText(unittest.TestCase):

    def test_illegal_control_chars_are_dropped(self):
        for name, ch in ILLEGAL_CHARS.items():
            with self.subTest(char=name):
                self.assertEqual(xml_safe_text(f'客户{ch}名称'), '客户名称')

    def test_tab_newline_carriage_return_are_legal_and_kept(self):
        """这三个是 XML 1.0 允许的空白，不能一起被删掉。"""
        text = '制表' + chr(9) + '换行' + chr(10) + '回车' + chr(13) + '保留'
        self.assertEqual(xml_safe_text(text), text)

    def test_soft_breaks_become_newlines(self):
        """VT/FF 是 Word 粘贴的软换行、分页残留，映射成换行比直接丢弃更贴近原意。"""
        self.assertEqual(xml_safe_text('第一行\x0b第二行'), '第一行\n第二行')
        self.assertEqual(xml_safe_text('第一页\x0c第二页'), '第一页\n第二页')

    def test_legal_boundary_code_points_are_kept(self):
        for cp in (0xD7FF, 0xE000, 0xFFFD, 0x10000, 0x10FFFF):
            ch = chr(cp)
            with self.subTest(codepoint=hex(cp)):
                self.assertEqual(xml_safe_text('a' + ch + 'b'), 'a' + ch + 'b')

    def test_non_string_values_are_coerced(self):
        self.assertEqual(xml_safe_text(250000000), '250000000')
        self.assertEqual(xml_safe_text(None), 'None')

    def test_result_serializes_into_well_formed_xml(self):
        """出口是 ET——函数的意义就在于 ET.tostring 之后还能被 ET 解析回来。"""
        node = ET.Element('t')
        node.text = xml_safe_text('PT\x1b Nusantara\x07' + ''.join(ILLEGAL_CHARS.values()))
        ET.fromstring(ET.tostring(node))


class TestEveryWriteSiteIsGuarded(unittest.TestCase):
    """配置里的银行账号、页眉地址同样绕过了 validate，所以清洗必须落在写入口。"""

    def test_no_w_t_text_assignment_bypasses_xml_safe_text(self):
        src_path = os.path.join(SKILL_ROOT, 'scripts', 'build_quotation.py')
        with open(src_path, encoding='utf-8') as fh:
            lines = fh.readlines()

        unguarded = []
        for i, line in enumerate(lines, 1):
            m = re.match(r'\s*(\w+)\.text = (.*)$', line)
            if not m:
                continue
            target, value = m.group(1), m.group(2).strip()
            # pos.text 是计算出来的 EMU 偏移量，不是用户文本。
            if target == 'pos':
                continue
            if not value.startswith('xml_safe_text('):
                unguarded.append(f'{src_path}:{i}: {line.strip()}')

        self.assertEqual(
            unguarded, [],
            '这些 w:t 写入点没有过 xml_safe_text，控制字符会再次弄坏 document.xml:\n'
            + '\n'.join(unguarded))


class TestBuildSurvivesControlChars(unittest.TestCase):
    """端到端：脏数据进得去，产物仍然打得开，正文不能被误删。"""

    def _build_with_control_chars(self, tmpdir):
        src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
        with open(src, encoding='utf-8') as fh:
            data = json.load(fh)

        dirty_name = 'PT Nusantara\x1b Jaya\x07'
        dirty_service = 'BPOM 化妆品延期注册\x00'
        data['quote_meta']['customer_name'] = dirty_name
        data['services'][0]['name'] = dirty_service

        data_path = os.path.join(tmpdir, 'quotation.json')
        with open(data_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

        output_path = os.path.join(tmpdir, '报价单.docx')
        rc, out, err = _run_script('build_quotation.py', [
            '--entity', data['_meta']['applicable_entity'],
            '--data', data_path, '--output', output_path])
        return rc, out, err, data_path, output_path

    def test_document_xml_stays_well_formed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, err, _, output_path = self._build_with_control_chars(tmpdir)
            self.assertEqual(rc, 0, f'build failed:\n{out}\n{err}')

            with zipfile.ZipFile(output_path) as zf:
                raw = zf.read('word/document.xml')
            # 修复前这里抛 ParseError：build 报成功，产物却打不开。
            ET.fromstring(raw)

            for name, ch in (('ESC', '\x1b'), ('BEL', '\x07'), ('NUL', '\x00')):
                self.assertNotIn(
                    ch.encode('utf-8'), raw,
                    f'产物里仍有 {name}，清洗没走到这条路径')

    def test_visible_text_survives_the_scrub(self):
        """只删非法字符，人名/服务名本身不能被连带丢掉。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, err, _, output_path = self._build_with_control_chars(tmpdir)
            self.assertEqual(rc, 0, f'build failed:\n{out}\n{err}')

            haystack = '\n'.join(_docx_texts(output_path))
            self.assertIn('PT Nusantara', haystack)
            self.assertIn('Jaya', haystack)
            self.assertIn('BPOM 化妆品延期注册', haystack)

    def test_verify_passes_on_the_scrubbed_document(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, err, data_path, output_path = self._build_with_control_chars(tmpdir)
            self.assertEqual(rc, 0, f'build failed:\n{out}\n{err}')

            rc, out, err = _run_script('verify_quotation.py', [
                '--input', output_path, '--data', data_path])
            self.assertEqual(rc, 0, f'verify failed:\n{out}\n{err}')
            self.assertIn('验证通过', out)


class TestVerifyReportsUnparseableInput(unittest.TestCase):

    def _build_clean(self, tmpdir):
        src = os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json')
        data_path = os.path.join(tmpdir, 'quotation.json')
        shutil.copy(src, data_path)
        with open(data_path, encoding='utf-8') as fh:
            entity = json.load(fh)['_meta']['applicable_entity']
        output_path = os.path.join(tmpdir, '报价单.docx')
        rc, out, err = _run_script('build_quotation.py', [
            '--entity', entity, '--data', data_path, '--output', output_path])
        self.assertEqual(rc, 0, f'build failed:\n{out}\n{err}')
        return data_path, output_path

    def _corrupt_document_xml(self, tmpdir, src_path):
        """把原始 ESC 字节塞进 document.xml —— 绕过 build，模拟手工改过的/旧版产物。"""
        corrupt_path = os.path.join(tmpdir, 'corrupt.docx')
        with zipfile.ZipFile(src_path) as zin, \
                zipfile.ZipFile(corrupt_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'word/document.xml':
                    marker = '报价单'.encode('utf-8')
                    self.assertIn(marker, data, '找不到可注入的位置')
                    data = data.replace(marker, '报价\x1b单'.encode('utf-8'), 1)
                zout.writestr(item, data)
        return corrupt_path

    def test_unparseable_document_reports_a_one_liner_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, output_path = self._build_clean(tmpdir)
            corrupt_path = self._corrupt_document_xml(tmpdir, output_path)

            rc, out, err = _run_script('verify_quotation.py', ['--input', corrupt_path])

            self.assertEqual(rc, 1)
            self.assertNotIn('Traceback', out + err, '不该让调用方看到原始 traceback')
            self.assertIn('不是合法 XML', out)
            # 报错必须指向真实原因（脏数据），而不是让调用方去联系 SKILL 作者。
            self.assertIn('控制字符', out)

    def test_non_zip_input_reports_a_one_liner_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            notzip = os.path.join(tmpdir, 'notzip.docx')
            with open(notzip, 'w', encoding='utf-8') as fh:
                fh.write('<html><body>502 Bad Gateway</body></html>')

            rc, out, err = _run_script('verify_quotation.py', ['--input', notzip])

            self.assertEqual(rc, 1)
            self.assertNotIn('Traceback', out + err, '不该让调用方看到原始 traceback')
            self.assertIn('不是有效的 zip 包', out)


if __name__ == '__main__':
    unittest.main()
