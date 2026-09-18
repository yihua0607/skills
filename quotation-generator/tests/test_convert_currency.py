"""Currency conversion: every supported currency, and the cases that must refuse.

业务口径（2026-09-13）：
  * 外币 → 人民币：该外币的 rateToCny（÷）
  * 外币 → 美元　：该外币的 rateToUsd（÷）
  * 人民币/美元 → 外币：同一汇率反向（×）
  * 人民币 ↔ 美元：一条外币的两个汇率做桥
  * 外币 → 外币：API 没有直接汇率，必须向用户索取（--cross-rate N，1 from = N to）

外币侧的汇率只对「它自己的那个币种」成立，所以这里对 8 个币种逐个跑一遍——
以前脚本只认 IDR/RMB/USD，其余 5 个币种只能手算，正是最容易出一位之差的地方。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_ROOT not in sys.path:
    sys.path.insert(0, SKILL_ROOT)

from scripts.convert_currency import batch_convert, convert_single

# 1 CNY = rateToCny 该币种；1 USD = rateToUsd 该币种
RATES = {
    'IDR': (2250, 17710),
    'SGD': (5.45, 7.85),
    'THB': (4.85, 6.98),
    'VND': (3650, 26000),
    'EGP': (6.95, 48.5),
    'MYR': (1.55, 4.75),
}
FOREIGN = tuple(RATES)


def _run(args):
    return subprocess.run(
        [sys.executable, os.path.join(SKILL_ROOT, 'scripts', 'convert_currency.py')] + args,
        cwd=SKILL_ROOT, capture_output=True, text=True, encoding='utf-8')


class TestForeignToBase(unittest.TestCase):

    def test_every_foreign_currency_converts_to_rmb_and_usd(self):
        """8 币种里除人民币/美元外的每个币种都能按 rateToCny / rateToUsd 换算。"""
        for code, (to_cny, to_usd) in RATES.items():
            with self.subTest(currency=code):
                amount = Decimal('1000000')
                res = convert_single(amount, code, 'RMB', Decimal(str(to_cny)), Decimal(str(to_usd)))
                self.assertNotIn('error', res, res)
                self.assertEqual(res['rate_type'], 'rateToCny')
                self.assertEqual(int(res['converted']),
                                 int((amount / Decimal(str(to_cny))).quantize(Decimal('1'))))

                res = convert_single(amount, code, 'USD', Decimal(str(to_cny)), Decimal(str(to_usd)))
                self.assertNotIn('error', res, res)
                self.assertEqual(res['rate_type'], 'rateToUsd')
                self.assertEqual(int(res['converted']),
                                 int((amount / Decimal(str(to_usd))).quantize(Decimal('1'))))

    def test_missing_rate_is_reported_per_currency(self):
        for code in FOREIGN:
            with self.subTest(currency=code):
                res = convert_single(Decimal('1000'), code, 'RMB')
                self.assertIn('rateToCny is required', res['error'])
                res = convert_single(Decimal('1000'), code, 'USD')
                self.assertIn('rateToUsd is required', res['error'])


class TestBaseToForeign(unittest.TestCase):

    def test_rmb_and_usd_to_foreign_multiply_by_the_same_rate(self):
        for code, (to_cny, to_usd) in RATES.items():
            with self.subTest(currency=code):
                res = convert_single(Decimal('1000'), 'RMB', code, Decimal(str(to_cny)))
                self.assertNotIn('error', res, res)
                self.assertEqual(int(res['converted']), int(Decimal('1000') * Decimal(str(to_cny))))

                res = convert_single(Decimal('1000'), 'USD', code, None, Decimal(str(to_usd)))
                self.assertNotIn('error', res, res)
                self.assertEqual(int(res['converted']), int(Decimal('1000') * Decimal(str(to_usd))))


class TestForeignToForeign(unittest.TestCase):

    def test_refuses_without_rate_and_tells_agent_to_ask_the_user(self):
        """外币 → 外币没有汇率数据：必须拒绝并提示向用户索取，绝不自己估。"""
        for a, b in (('THB', 'VND'), ('MYR', 'SGD'), ('EGP', 'IDR'), ('VND', 'MYR')):
            with self.subTest(pair=f'{a}->{b}'):
                res = convert_single(Decimal('1000'), a, b, Decimal('2250'), Decimal('17710'))
                self.assertIn('error', res)
                self.assertIn('外币转外币', res['error'])
                self.assertIn('向用户索取汇率', res['error'])
                self.assertIn('--cross-rate', res['error'])
                # 关键：绝不能给出一个凭空算出来的数字
                self.assertNotIn('converted', res)

    def test_cross_rate_from_user_is_applied(self):
        """用户给出汇率后（1 THB = N VND）可以换算。"""
        res = convert_single(Decimal('100000'), 'THB', 'VND', cross_rate=Decimal('1.05'))
        self.assertNotIn('error', res, res)
        self.assertEqual(res['converted'], '105000')
        self.assertEqual(res['rate_type'], 'crossRate')
        self.assertIn('用户提供', res['note'])


class TestBaseBridge(unittest.TestCase):

    def test_rmb_to_usd_uses_a_bridge_currency(self):
        res = convert_single(Decimal('10000'), 'RMB', 'USD', Decimal('2250'), Decimal('17710'))
        self.assertNotIn('error', res, res)
        self.assertEqual(res['rate_type'], 'rateToCny+rateToUsd')
        self.assertEqual(int(res['converted']), int(Decimal('10000') * 2250 / 17710))

    def test_bridge_requires_both_rates(self):
        res = convert_single(Decimal('10000'), 'RMB', 'USD', Decimal('2250'))
        self.assertIn('error', res)


class TestRoundingAndIdentity(unittest.TestCase):

    def test_half_up_rounding(self):
        """2.5 → 3、3.5 → 4（ROUND_HALF_UP，与官网四舍五入一致，不是截断）。"""
        res = convert_single(Decimal('5000'), 'IDR', 'RMB', Decimal('2000'))
        self.assertEqual(res['converted'], '3')
        res = convert_single(Decimal('7000'), 'IDR', 'RMB', Decimal('2000'))
        self.assertEqual(res['converted'], '4')

    def test_known_reference_value(self):
        """18,400,000 ÷ 2,250 = 8,178（文档曾误写为「截断得 8,177」）。"""
        res = convert_single(Decimal('18400000'), 'IDR', 'RMB', Decimal('2250'))
        self.assertEqual(res['converted'], '8178')

    def test_same_currency_is_identity(self):
        res = convert_single(Decimal('123'), 'THB', 'THB')
        self.assertEqual(res['converted'], '123')
        self.assertEqual(res['rate_type'], 'identity')

    def test_cny_alias_normalizes_to_rmb(self):
        res = convert_single(Decimal('30000000'), 'CNY', 'IDR', Decimal('2250'))
        self.assertNotIn('error', res, res)
        self.assertEqual(res['to_currency'], 'IDR')

    def test_unknown_currency_needs_an_explicit_rate(self):
        res = convert_single(Decimal('100'), 'HKD', 'RMB')
        self.assertIn('Unknown source currency', res['error'])
        res = convert_single(Decimal('100'), 'HKD', 'RMB', Decimal('1.08'))
        self.assertNotIn('error', res, res)
        self.assertEqual(res['converted'], '93')


class TestBatchConversion(unittest.TestCase):

    def _write(self, tmpdir, services):
        path = os.path.join(tmpdir, 'queried_services.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'success': True, 'services': services}, fh, ensure_ascii=False, indent=2)
        return path

    def test_batch_handles_every_source_currency_to_rmb(self):
        services = []
        for code, (to_cny, to_usd) in RATES.items():
            services.append({'服务名称': f'{code} 服务', '服务币种': code, '服务价格': '1000000',
                             '人民币兑换服务币种汇率': to_cny, '美元兑换服务币种汇率': to_usd})
        with tempfile.TemporaryDirectory(prefix='convert-batch-') as tmpdir:
            path = self._write(tmpdir, services)
            results = batch_convert(path, 'RMB')
        self.assertEqual(len(results), len(RATES))
        for res in results:
            with self.subTest(service=res['service']):
                self.assertNotIn('error', res, res)
                self.assertEqual(res['to_currency'], 'RMB')

    def test_batch_rmb_service_to_foreign_uses_that_currencys_rate(self):
        """人民币定价的服务 + 整批里有泰铢服务 → 可以用泰铢的汇率报 THB。"""
        services = [
            {'服务名称': '人民币服务', '服务币种': 'RMB', '服务价格': '10000',
             '人民币兑换服务币种汇率': 1.0, '美元兑换服务币种汇率': 7.3},
            {'服务名称': '泰铢服务', '服务币种': 'THB', '服务价格': '48500',
             '人民币兑换服务币种汇率': 4.85, '美元兑换服务币种汇率': 6.98},
        ]
        with tempfile.TemporaryDirectory(prefix='convert-batch-thb-') as tmpdir:
            path = self._write(tmpdir, services)
            results = batch_convert(path, 'THB')
        rmb = [r for r in results if r['service'] == '人民币服务'][0]
        self.assertNotIn('error', rmb, rmb)
        self.assertEqual(rmb['converted'], '48500')      # 10000 × 4.85
        thb = [r for r in results if r['service'] == '泰铢服务'][0]
        self.assertEqual(thb['rate_type'], 'identity')

    def test_batch_foreign_to_foreign_asks_for_a_rate(self):
        services = [{'服务名称': '泰铢服务', '服务币种': 'THB', '服务价格': '100000',
                     '人民币兑换服务币种汇率': 4.85, '美元兑换服务币种汇率': 6.98}]
        with tempfile.TemporaryDirectory(prefix='convert-batch-cross-') as tmpdir:
            path = self._write(tmpdir, services)
            results = batch_convert(path, 'VND')
        self.assertIn('error', results[0])
        self.assertIn('向用户索取汇率', results[0]['error'])

    def test_batch_missing_rate_still_names_the_rate_field(self):
        """历史行为：缺汇率时报错文案要含 rateToCny is required（有测试依赖）。"""
        services = [{'服务名称': '缺汇率', '服务币种': 'IDR', '服务价格': '1000',
                     '人民币兑换服务币种汇率': None, '美元兑换服务币种汇率': None}]
        with tempfile.TemporaryDirectory(prefix='convert-batch-missing-') as tmpdir:
            path = self._write(tmpdir, services)
            results = batch_convert(path, 'RMB')
        self.assertIn('rateToCny is required', results[0]['error'])


class TestCliSurface(unittest.TestCase):

    def test_cli_accepts_a_non_idr_foreign_currency(self):
        """以前 `--from MYR` 直接被 argparse 拒；现在 8 币种都收。"""
        rc = _run(['--amount', '10000', '--from', 'MYR', '--to', 'RMB', '--rateToCny', '1.55',
                   '--json-only'])
        self.assertEqual(rc.returncode, 0, rc.stderr)
        parsed = json.loads(rc.stdout)
        self.assertEqual(parsed['from_currency'], 'MYR')
        self.assertEqual(parsed['converted'], '6452')

    def test_cli_cross_rate_passes_through(self):
        rc = _run(['--amount', '100000', '--from', 'THB', '--to', 'VND', '--cross-rate', '1.05',
                   '--json-only'])
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertEqual(json.loads(rc.stdout)['converted'], '105000')

    def test_cli_refuses_foreign_to_foreign_and_exits_nonzero(self):
        rc = _run(['--amount', '100000', '--from', 'THB', '--to', 'VND'])
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn('向用户索取汇率', rc.stderr)
        self.assertEqual(rc.stdout.strip(), '')


if __name__ == '__main__':
    unittest.main()


class TestInvalidRatesAreRefused(unittest.TestCase):
    """0 / 负数 / NaN / Infinity 这类汇率必须在换算前被拦下。

    以前 0 会让除法直接抛 ArithmeticError，从 CLI 一路冒成 traceback（调用方看到
    的是「脚本崩了」而不是「汇率填错了」）；负数更糟——它不抛异常，只是安安静静地
    把负金额写进报价单；NaN/Infinity 则一路写进产物，金额栏印出 "NaN"。
    """

    NAN = Decimal('NaN')
    INF = Decimal('Infinity')

    def test_zero_rate_is_refused_not_crashed(self):
        result = convert_single(Decimal('250000000'), 'IDR', 'RMB', rateToCny=Decimal('0'))
        self.assertIn('error', result)
        self.assertIn('取值非法', result['error'])

    def test_negative_rate_is_refused(self):
        for rate in (Decimal('-2173.91'), Decimal('-1')):
            with self.subTest(rate=str(rate)):
                result = convert_single(Decimal('250000000'), 'IDR', 'RMB', rateToCny=rate)
                self.assertIn('error', result)
                self.assertIn('取值非法', result['error'])

    def test_every_rate_slot_is_checked(self):
        """三个汇率入参走的是同一道校验，别只堵住 rateToCny。"""
        cases = [
            ('rateToCny', dict(rateToCny=Decimal('0'))),
            ('rateToUsd', dict(rateToUsd=Decimal('-1'))),
            ('cross-rate', dict(cross_rate=Decimal('0'))),
        ]
        for label, kwargs in cases:
            with self.subTest(slot=label):
                result = convert_single(Decimal('100000'), 'THB', 'VND', **kwargs)
                self.assertIn('error', result)
                self.assertIn(label, result['error'])

    def test_non_finite_rates_are_refused(self):
        for rate in (self.NAN, self.INF, Decimal('-Infinity')):
            with self.subTest(rate=str(rate)):
                result = convert_single(Decimal('250000000'), 'IDR', 'RMB', rateToCny=rate)
                self.assertIn('error', result,
                              f'{rate} 不该被当成合法汇率算出一笔钱')

    def test_non_finite_amount_is_refused(self):
        result = convert_single(self.NAN, 'IDR', 'RMB', rateToCny=Decimal('2173.91'))
        self.assertIn('error', result)
        self.assertIn('有限数值', result['error'])

    def test_same_currency_ignores_a_dirty_rate(self):
        """同币种不需要汇率，调用方顺手传进来的脏值不该让换算失败。"""
        result = convert_single(Decimal('115000'), 'RMB', 'RMB', rateToCny=Decimal('0'))
        self.assertNotIn('error', result)
        self.assertEqual(result['converted'], '115000')

    def test_a_valid_rate_still_converts(self):
        """反例：校验不能把正常汇率一起拦掉。"""
        result = convert_single(Decimal('250000000'), 'IDR', 'RMB',
                                rateToCny=Decimal('2173.91'))
        self.assertNotIn('error', result)
        self.assertEqual(result['converted'], '115000')

    def test_batch_reports_the_bad_rate_without_killing_the_batch(self):
        """一条脏汇率只该毁掉它自己那条服务。"""
        services = [
            {'服务名称': '坏汇率服务', '服务币种': 'IDR', '服务价格': '250000000',
             '人民币兑换服务币种汇率': 0, '美元兑换服务币种汇率': 16000},
            {'服务名称': '好汇率服务', '服务币种': 'THB', '服务价格': '48500',
             '人民币兑换服务币种汇率': 4.85, '美元兑换服务币种汇率': 6.98},
        ]
        with tempfile.TemporaryDirectory(prefix='convert-badrate-') as tmpdir:
            path = os.path.join(tmpdir, 'queried_services.json')
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump({'success': True, 'services': services}, fh,
                          ensure_ascii=False, indent=2)
            results = batch_convert(path, 'RMB')

        bad = [r for r in results if r['service'] == '坏汇率服务'][0]
        good = [r for r in results if r['service'] == '好汇率服务'][0]
        self.assertIn('error', bad)
        self.assertNotIn('error', good, good)
        self.assertEqual(good['converted'], '10000')      # 48500 ÷ 4.85

    def test_nan_rate_from_the_api_is_treated_as_missing(self):
        """API 回字符串 "nan" 时，Decimal 解析得动，但不是一个能用的汇率。"""
        services = [{'服务名称': 'IDR 服务', '服务币种': 'IDR', '服务价格': '250000000',
                     '人民币兑换服务币种汇率': 'nan', '美元兑换服务币种汇率': 'Infinity'}]
        with tempfile.TemporaryDirectory(prefix='convert-nanrate-') as tmpdir:
            path = os.path.join(tmpdir, 'queried_services.json')
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump({'success': True, 'services': services}, fh,
                          ensure_ascii=False, indent=2)
            results = batch_convert(path, 'RMB')
        self.assertIn('error', results[0])
        self.assertNotIn('NaN', json.dumps(results, ensure_ascii=False))


class TestInvalidRateCliSurface(unittest.TestCase):

    def test_zero_rate_exits_nonzero_without_a_traceback(self):
        rc = _run(['--amount', '250000000', '--from', 'IDR', '--to', 'RMB',
                   '--rateToCny', '0'])
        self.assertNotEqual(rc.returncode, 0)
        self.assertNotIn('Traceback', rc.stdout + rc.stderr)
        self.assertIn('取值非法', rc.stderr)
        self.assertEqual(rc.stdout.strip(), '')

    def test_negative_rate_exits_nonzero_without_a_traceback(self):
        rc = _run(['--amount', '250000000', '--from', 'IDR', '--to', 'RMB',
                   '--rateToCny=-2173.91'])
        self.assertNotEqual(rc.returncode, 0)
        self.assertNotIn('Traceback', rc.stdout + rc.stderr)
        self.assertIn('取值非法', rc.stderr)

    def test_non_finite_rate_exits_nonzero(self):
        for raw in ('nan', 'Infinity', '-Infinity'):
            with self.subTest(rate=raw):
                rc = _run(['--amount', '250000000', '--from', 'IDR', '--to', 'RMB',
                           '--rateToCny', raw])
                self.assertNotEqual(rc.returncode, 0)
                self.assertNotIn('Traceback', rc.stdout + rc.stderr)
