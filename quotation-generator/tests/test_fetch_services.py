"""Tests for fetch_services.py API failure handling."""
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_ROOT not in sys.path:
    sys.path.insert(0, SKILL_ROOT)

from scripts import fetch_services


class TestFetchServices(unittest.TestCase):

    @staticmethod
    def _codes(count):
        return [f'服务{i}-{i:019d}' for i in range(1, count + 1)]

    def test_accepts_one_ai_code(self):
        codes = self._codes(1)
        self.assertEqual(fetch_services.parse_ai_codes(codes), codes)

    def test_accepts_twenty_ai_codes_in_one_request(self):
        codes = self._codes(20)
        payload = {'success': True, 'code': 200, 'message': 'ok', 'result': {'items': []}}
        with patch.object(fetch_services, '_request_services_once', return_value=payload) as request:
            result = fetch_services.request_services(codes)
        request.assert_called_once_with(codes)
        self.assertTrue(result['success'])

    def test_splits_twenty_one_ai_codes_into_twenty_plus_one(self):
        codes = self._codes(21)

        def response(chunk):
            return {
                'success': True,
                'code': 200,
                'message': 'ok',
                'result': {'items': [
                    {'aiCode': code, 'success': True, 'message': 'ok', 'data': {}}
                    for code in chunk
                ]},
            }

        with patch.object(fetch_services, '_request_services_once', side_effect=response) as request:
            result = fetch_services.request_services(codes)
        self.assertEqual([len(call.args[0]) for call in request.call_args_list], [20, 1])
        self.assertEqual(len(result['result']['items']), 21)
        self.assertEqual([len(batch['requested_ai_codes']) for batch in result['batch_results']], [20, 1])
        self.assertEqual(result['batch_results'][1]['returned_ai_codes'], [codes[20]])

    def test_missing_returned_ai_code_aborts_with_explicit_error(self):
        codes = self._codes(2)
        payload = {
            'success': True, 'code': 200, 'message': 'ok',
            'result': {'items': [{
                'aiCode': codes[0].rsplit('-', 1)[-1], 'success': True,
                'message': 'ok', 'data': {'detail': '服务内容'},
            }]},
            'batch_results': [],
        }
        stdout = io.StringIO()
        with patch.object(sys, 'argv', ['fetch_services.py', *codes]), \
                patch.object(fetch_services, 'request_services', return_value=payload), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fetch_services.main()
        output = json.loads(stdout.getvalue())
        self.assertFalse(output['success'])
        self.assertIn(
            {'aiCode': codes[1], 'message': 'API 未返回该 aiCode 的查询结果'},
            output['errors'])

    def test_unexpected_and_duplicate_returned_ai_codes_abort(self):
        codes = self._codes(1)
        suffix = codes[0].rsplit('-', 1)[-1]
        payload = {
            'success': True, 'code': 200, 'message': 'ok',
            'result': {'items': [
                {'aiCode': suffix, 'success': True, 'message': 'ok', 'data': {'detail': 'A'}},
                {'aiCode': suffix, 'success': True, 'message': 'ok', 'data': {'detail': 'B'}},
                {'aiCode': '9999999999999999999', 'success': True, 'message': 'ok',
                 'data': {'detail': 'C'}},
            ]},
        }
        stdout = io.StringIO()
        with patch.object(sys, 'argv', ['fetch_services.py', *codes]), \
                patch.object(fetch_services, 'request_services', return_value=payload), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fetch_services.main()
        messages = {item['message'] for item in json.loads(stdout.getvalue())['errors']}
        self.assertIn('API 重复返回该 aiCode', messages)
        self.assertIn('API 返回了未请求的 aiCode', messages)

    def test_accepts_comma_separated_and_independent_arguments(self):
        codes = self._codes(3)
        parsed = fetch_services.parse_ai_codes([
            f'  {codes[0]} , {codes[1]}  ',
            codes[2],
        ])
        self.assertEqual(parsed, codes)

    def test_request_body_always_uses_comma_joined_ai_codes_string(self):
        codes = self._codes(2)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {'success': True, 'result': {'items': []}}).encode('utf-8')
        with patch.object(fetch_services.urllib.request, 'urlopen', return_value=response) as urlopen:
            fetch_services._request_services_once(codes)
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data.decode('utf-8')), {'aiCodes': ','.join(codes)})

    def _http_error(self, code, message='request failed'):
        return urllib.error.HTTPError(
            fetch_services.ENDPOINT, code, message, {},
            io.BytesIO(json.dumps({'message': message}).encode('utf-8')))

    def test_non_retryable_http_400_stops_after_one_attempt(self):
        stdout = io.StringIO()
        urlopen = unittest.mock.Mock(side_effect=self._http_error(400, 'AI Code 无效'))
        with patch.object(fetch_services.urllib.request, 'urlopen', urlopen), \
                patch.object(fetch_services.time, 'sleep') as sleep, \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fetch_services.request_services(['测试-2072516766550704130'])
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())['message'], 'AI Code 无效')

    def test_http_429_retries_at_most_three_total_attempts(self):
        stdout = io.StringIO()
        urlopen = unittest.mock.Mock(side_effect=[
            self._http_error(429, 'too many requests'),
            self._http_error(429, 'too many requests'),
            self._http_error(429, 'too many requests'),
        ])
        with patch.object(fetch_services.urllib.request, 'urlopen', urlopen), \
                patch.object(fetch_services.time, 'sleep') as sleep, \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fetch_services.request_services(['测试-2072516766550704130'])
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_http_500_retries_then_returns_success(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {'success': True, 'result': {'items': []}}).encode('utf-8')
        urlopen = unittest.mock.Mock(side_effect=[self._http_error(500, 'server error'), response])
        with patch.object(fetch_services.urllib.request, 'urlopen', urlopen), \
                patch.object(fetch_services.time, 'sleep') as sleep, \
                redirect_stderr(io.StringIO()):
            result = fetch_services.request_services(['测试-2072516766550704130'])
        self.assertTrue(result['success'])
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_api_success_false_exits_nonzero_and_preserves_message(self):
        api_message = "aiCode 不存在或已失效"
        payload = {
            "success": False,
            "code": 500,
            "message": api_message,
            "result": None,
        }

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "测试服务-2072516766550704130"]), \
                patch.object(fetch_services, "request_services", return_value=payload), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                fetch_services.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn(api_message, stderr.getvalue())

        output = json.loads(stdout.getvalue())
        self.assertFalse(output["success"])
        self.assertEqual(output["message"], api_message)
        self.assertEqual(output["services"], [])

    def test_item_success_false_exits_nonzero_and_preserves_error(self):
        item_message = "服务编码已下架"
        payload = {
            "success": True,
            "code": 200,
            "message": "ok",
            "result": {
                "items": [
                    {
                        "aiCode": "2072516766550704130",
                        "success": True,
                        "message": "success",
                        "data": {
                            "productName": "交通影响分析",
                            "productCode": "P001",
                            "currencyCode": "IDR",
                            "totalPrice": 250000000,
                            "rateToCny": "2173.91",
                            "rateToUsd": "16000",
                            "executeUnit": {"quantity": 1, "unitName": "项"},
                            "detail": "服务内容",
                        },
                    },
                    {
                        "aiCode": "2072304344749555713",
                        "success": False,
                        "message": item_message,
                        "data": {},
                    },
                ]
            },
        }

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "交通影响分析-2072516766550704130", "儿童玩具-SNI认证-2072304344749555713"]), \
                patch.object(fetch_services, "request_services", return_value=payload), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                fetch_services.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn(item_message, stderr.getvalue())
        self.assertIn("已中断报价单生成", stderr.getvalue())

        output = json.loads(stdout.getvalue())
        self.assertFalse(output["success"])
        self.assertTrue(output["partial_failure"])
        self.assertEqual(output["errors"], [{"aiCode": "2072304344749555713", "message": item_message}])

    def test_all_item_failures_exit_nonzero(self):
        payload = {
            "success": True,
            "code": 200,
            "message": "ok",
            "result": {
                "items": [
                    {
                        "aiCode": "2072516766550704130",
                        "success": False,
                        "message": "服务不存在",
                        "data": {},
                    },
                    {
                        "aiCode": "2072304344749555713",
                        "success": False,
                        "message": "服务已失效",
                        "data": {},
                    },
                ]
            },
        }

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "交通影响分析-2072516766550704130", "儿童玩具-SNI认证-2072304344749555713"]), \
                patch.object(fetch_services, "request_services", return_value=payload), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                fetch_services.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("服务不存在", stderr.getvalue())
        self.assertIn("服务已失效", stderr.getvalue())

        output = json.loads(stdout.getvalue())
        self.assertFalse(output["success"])
        self.assertTrue(output["partial_failure"])
        self.assertEqual(len(output["errors"]), 2)

    def test_success_true_with_no_services_exits_nonzero(self):
        payload = {
            "success": True,
            "code": 200,
            "message": "ok",
            "result": {"items": []},
        }

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "测试服务-2072516766550704130"]), \
                patch.object(fetch_services, "request_services", return_value=payload), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                fetch_services.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("未查询到任何服务", stderr.getvalue())

        output = json.loads(stdout.getvalue())
        self.assertFalse(output["success"])
        self.assertEqual(output["services"], [])
        self.assertIn({"aiCode": None, "message": "ok"}, output["errors"])

    def test_labeled_ai_code_args_send_full_service_name_and_19_digit_code(self):
        captured = {}

        def fake_request(ai_codes):
            captured["ai_codes"] = ai_codes
            return {
                "success": True,
                "code": 200,
                "message": "ok",
                "result": {
                    "items": [{
                        "aiCode": "2070441813847769898",
                        "success": True,
                        "message": "success",
                        "data": {
                            "productName": "电力代表处注册",
                            "productCode": "P-ELEC",
                            "currencyCode": "IDR",
                            "totalPrice": 10000000,
                            "rateToCny": "2173.91",
                            "rateToUsd": "16000",
                            "executeUnit": {"quantity": 1, "unitName": "项"},
                            "detail": "服务内容",
                        },
                    }]
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "电力代表处注册-2070441813847769898"]), \
                patch.object(fetch_services, "request_services", side_effect=fake_request), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            fetch_services.main()

        self.assertEqual(captured["ai_codes"], ["电力代表处注册-2070441813847769898"])
        output = json.loads(stdout.getvalue())
        self.assertTrue(output["success"])
        self.assertEqual(output["services"][0]["原始输入"], "电力代表处注册-2070441813847769898")

    def test_ai_code_requires_19_digit_suffix(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "测试服务-12345"]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                fetch_services.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("服务名-19位编码", stderr.getvalue())
        output = json.loads(stdout.getvalue())
        self.assertFalse(output["success"])
        self.assertIn("服务名-19位编码", output["message"])

    def test_raw_19_digit_code_is_rejected(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_services.py", "2070441813847769898"]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as cm:
                fetch_services.main()

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("不支持纯19位数字编码", stderr.getvalue())
        output = json.loads(stdout.getvalue())
        self.assertFalse(output["success"])
        self.assertIn("不支持纯19位数字编码", output["message"])


if __name__ == "__main__":
    unittest.main()
