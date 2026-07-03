"""End-to-end smoke tests for quotation-generator skill.

Each test copies an example data file into a temporary directory, runs
validate_data.py → build_quotation.py → verify_quotation.py, and asserts that
the generated .docx is a valid OOXML package.
"""
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


def _assert_docx_valid(test_case, docx_path):
    """Assert that the generated file is a valid .docx with valid document.xml."""
    test_case.assertTrue(os.path.exists(docx_path), f"Output .docx not found: {docx_path}")
    test_case.assertTrue(zipfile.is_zipfile(docx_path), f"Output is not a valid zip file: {docx_path}")
    with zipfile.ZipFile(docx_path, 'r') as zf:
        test_case.assertIn('word/document.xml', zf.namelist(), "Missing word/document.xml")
        xml_bytes = zf.read('word/document.xml')
        # This will raise ParseError if the XML is malformed (e.g. unescaped special chars)
        ET.fromstring(xml_bytes)


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
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['_meta']['applicable_entity'] = 'jakarta'
            data['_meta']['target_currency'] = 'IDR'
            with open(data_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            output_path = os.path.join(tmpdir, '报价单-西安-测试.docx')
            rc, out, err = _run_script(
                'build_quotation.py',
                ['--entity', 'xian', '--data', data_path, '--output', output_path]
            )
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script('verify_quotation.py', ['--input', output_path, '--data', data_path])
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
            self.assertEqual(rc, 0, f"build_quotation.py failed:\nstdout: {out}\nstderr: {err}")

            rc, out, err = _run_script('verify_quotation.py', ['--entity', 'xian', '--input', output_path, '--data', data_path])
            self.assertNotEqual(rc, 0)
            self.assertIn('数据_meta主体', out)

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


if __name__ == '__main__':
    unittest.main()
