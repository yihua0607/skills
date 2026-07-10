"""Tests for fetch_services.py API failure handling."""
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import fetch_services


class TestFetchServices(unittest.TestCase):

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
        self.assertEqual(output["errors"], [{"aiCode": None, "message": "ok"}])

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
