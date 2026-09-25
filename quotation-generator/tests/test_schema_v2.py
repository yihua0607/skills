"""Contract tests for the intentionally incompatible quotation schema v2."""
import copy
import json
import os
import re
import tempfile
import unittest

from scripts.quotation_schema import validate_and_normalize_data


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_CODE = 'BPOM 化妆品延期注册-2098636528563453953'


class TestSchemaV2(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'examples', 'minimal_quotation.json'), encoding='utf-8') as handle:
            self.data = json.load(handle)

    def test_service_is_single_source_of_truth(self):
        normalized = validate_and_normalize_data(self.data)
        service = normalized['services'][0]
        self.assertIn('fees', service)
        self.assertIn('process', service)
        self.assertIn('deliverables', service)
        self.assertIn('documents', service)
        self.assertNotIn('fee_details', normalized)

    def test_contract_number_is_canonicalized_to_uppercase(self):
        self.data['quote_meta']['contract_no'] = 'shm/ii/s8888-a'
        normalized = validate_and_normalize_data(self.data)
        self.assertEqual(normalized['quote_meta']['contract_no'], 'SHM/II/S8888-A')

    def test_old_parallel_collections_do_not_satisfy_missing_service_fields(self):
        broken = copy.deepcopy(self.data)
        service = broken['services'][0]
        fee_details = [{'name': service['name'], **service.pop('fees')}]
        broken['fee_details'] = fee_details
        with self.assertRaisesRegex(ValueError, r'services\[0\]\.fees'):
            validate_and_normalize_data(broken)

    def test_duplicate_names_are_allowed_but_line_ids_are_not(self):
        duplicate = copy.deepcopy(self.data['services'][0])
        duplicate['ai_code'] = None
        duplicate['line_id'] = '2'
        self.data['services'].append(duplicate)
        validate_and_normalize_data(self.data)
        self.data['services'][1]['line_id'] = '1'
        with self.assertRaisesRegex(ValueError, 'Duplicate service line_id'):
            validate_and_normalize_data(self.data)

    def test_schema_version_two_is_required(self):
        self.data['_meta']['schema_version'] = 1
        with self.assertRaisesRegex(ValueError, '_meta.schema_version must be 2'):
            validate_and_normalize_data(self.data)

    def test_quantity_is_required(self):
        del self.data['services'][0]['quantity']
        with self.assertRaisesRegex(ValueError, r'services\[0\]\.quantity must be an integer >= 1'):
            validate_and_normalize_data(self.data)

    def test_ai_code_requires_query_result_file(self):
        self.data['services'][0]['ai_code'] = AI_CODE
        with self.assertRaisesRegex(ValueError, r'query_result_file is required'):
            validate_and_normalize_data(self.data)

    def test_unknown_fields_fail_with_suggestion(self):
        self.data['witholding_tax'] = True
        with self.assertRaisesRegex(ValueError, r'Unknown field: witholding_tax; did you mean withholding_tax'):
            validate_and_normalize_data(self.data)

    def test_unknown_fields_are_rejected_at_each_schema_level(self):
        cases = [
            (lambda data: data['_meta'].__setitem__('target_currncy', 'RMB'), '_meta.target_currncy'),
            (lambda data: data['quote_meta'].__setitem__('payment_term', []), 'quote_meta.payment_term'),
            (lambda data: data['services'][0].__setitem__('units', '项'), 'services\\[0\\].units'),
            (lambda data: data['services'][0]['fees'].__setitem__('includes', []), 'services\\[0\\].fees.includes'),
        ]
        for mutate, expected in cases:
            with self.subTest(expected=expected):
                candidate = copy.deepcopy(self.data)
                mutate(candidate)
                with self.assertRaisesRegex(ValueError, expected):
                    validate_and_normalize_data(candidate)

    def test_noncontiguous_categories_fail(self):
        first = self.data['services'][0]
        first['category'] = 'A'
        second = copy.deepcopy(first)
        second['line_id'] = '2'
        second['category'] = 'B'
        third = copy.deepcopy(first)
        third['line_id'] = '3'
        self.data['services'].extend([second, third])
        with self.assertRaisesRegex(ValueError, r'category is not contiguous: A'):
            validate_and_normalize_data(self.data)

    def test_query_source_checks_code_and_warns_about_descriptive_drift(self):
        service = self.data['services'][0]
        service['ai_code'] = AI_CODE
        payload = {'services': [{
            '原始输入': service['ai_code'], '服务编码': 'WRONG',
            '服务名称': 'API 名称', '服务数量': 2, '服务单位': '件',
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, 'quotation.json')
            source_path = os.path.join(tmpdir, 'queried.json')
            self.data['_meta']['query_result_file'] = 'queried.json'
            with open(source_path, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
            with self.assertRaisesRegex(ValueError, r'code does not match query result'):
                validate_and_normalize_data(self.data, data_path=data_path)

    def test_query_source_warns_when_name_quantity_or_unit_differs(self):
        service = self.data['services'][0]
        service['ai_code'] = AI_CODE
        payload = {'services': [{
            '原始输入': service['ai_code'], '服务编码': service['code'],
            '服务名称': 'API 名称', '服务数量': 2, '服务单位': '件',
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, 'quotation.json')
            self.data['_meta']['query_result_file'] = 'queried.json'
            with open(os.path.join(tmpdir, 'queried.json'), 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
            normalized = validate_and_normalize_data(self.data, data_path=data_path)
        self.assertEqual(len([w for w in normalized['warnings'] if 'differs from query result' in w]), 3)

    def test_query_source_reports_missing_and_omitted_ai_codes(self):
        quoted = self.data['services'][0]
        quoted['ai_code'] = AI_CODE
        omitted_code = '另一个服务-1234567890123456789'
        payload = {'services': [{
            '原始输入': omitted_code, '服务编码': 'ID9999', '服务名称': '另一个服务',
            '服务数量': 1, '服务单位': '项',
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, 'quotation.json')
            self.data['_meta']['query_result_file'] = 'queried.json'
            with open(os.path.join(tmpdir, 'queried.json'), 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
            with self.assertRaisesRegex(
                    ValueError, rf'ai_code not found in query result: {re.escape(quoted["ai_code"])}'):
                validate_and_normalize_data(self.data, data_path=data_path)

    def test_query_source_warns_about_unquoted_results(self):
        quoted = self.data['services'][0]
        quoted['ai_code'] = AI_CODE
        omitted_code = '另一个服务-1234567890123456789'
        payload = {'services': [
            {'原始输入': quoted['ai_code'], '服务编码': quoted['code'],
             '服务名称': quoted['name'], '服务数量': quoted['quantity'], '服务单位': quoted['unit']},
            {'原始输入': omitted_code, '服务编码': 'ID9999',
             '服务名称': '另一个服务', '服务数量': 1, '服务单位': '项'},
        ]}
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, 'quotation.json')
            self.data['_meta']['query_result_file'] = 'queried.json'
            with open(os.path.join(tmpdir, 'queried.json'), 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
            normalized = validate_and_normalize_data(self.data, data_path=data_path)
        self.assertTrue(any(omitted_code in warning for warning in normalized['warnings']))


if __name__ == '__main__':
    unittest.main()
