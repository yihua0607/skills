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
import tempfile
import unittest
import zipfile
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


def _header_trailing_paragraph_count(docx_path):
    """Count empty paragraphs following the last text paragraph in header1.xml.

    Two header layouts exist. In the china/egypt/deyin/singapore/malaysia
    templates the blue separator line is anchored in its own trailing paragraph;
    in the jakarta/vietnam/thailand templates it is anchored inside the "Web:"
    paragraph and the trailing empties are deliberate spacers that reserve room
    so the body's first line stays clear of the line."""
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
            self.assertIn('Preserved payment terms', out)

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
            self.assertIn('Preserved payment terms', out)

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
            self.assertIn('Preserved payment terms', out)
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
        with tempfile.TemporaryDirectory(prefix='quotation-tamper-') as tmpdir:
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

            # Swap xian's header address for shenzhen's, leaving everything else intact.
            tampered_path = os.path.join(tmpdir, '报价单-西安-篡改.docx')
            _replace_docx_visible_text(
                output_path, tampered_path,
                '西安市高新区科技路林凯国际大厦15层1501-01-03室',
                '深圳市南山区南海大道1052号海翔广场717',
                part='word/header1.xml')

            rc, out, err = _run_script(
                'verify_quotation.py',
                ['--entity', 'xian', '--input', tampered_path, '--data', data_path]
            )
            self.assertNotEqual(
                rc, 0, f"verify_quotation.py accepted a foreign header address:\n{out}")
            self.assertIn('不属于本主体配置地址', out)

    def test_all_entity_templates_share_one_header_layout(self):
        """Every entity template must use the same compact header layout.

        Canonical layout: logo/company-name paragraph, address line(s), the "Web:"
        line, then exactly one trailing paragraph that carries the blue separator
        line at ~3pt below its top. No spacer paragraphs, and no template missing
        the separator.
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
        """build_quotation.py must leave the template's header geometry intact.

        The separator's floating anchor sits a fixed offset below its paragraph, so
        editing the paragraph's height or dropping it moves the blue line off its
        intended position — onto the body's first line ("公司名称：").
        """
        cases = [
            ('jakarta', 'sample_quotation.json', '报价单模板-雅加达公司.docx'),
            ('xian', 'minimal_quotation.json', '报价单模板-中国公司.docx'),
        ]
        for entity, example, template_name in cases:
            with self.subTest(entity=entity):
                template = os.path.join(SKILL_ROOT, 'assets', template_name)
                with tempfile.TemporaryDirectory(prefix='quotation-header-') as tmpdir:
                    data_path = _copy_example(tmpdir, example)
                    output_path = os.path.join(tmpdir, '报价单-页眉测试.docx')
                    rc, out, err = _run_script(
                        'build_quotation.py',
                        ['--entity', entity, '--data', data_path, '--output', output_path]
                    )
                    self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")
                    self.assertEqual(
                        _header_trailing_paragraph_count(output_path),
                        _header_trailing_paragraph_count(template),
                        f"{entity}: build changed the header's trailing paragraphs, "
                        f"so the blue line no longer sits where the template puts it")


if __name__ == '__main__':
    unittest.main()
