"""Unit tests for scripts/quotation_common.py."""
import os
import sys
import unittest
from decimal import Decimal

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_ROOT not in sys.path:
    sys.path.insert(0, SKILL_ROOT)

from scripts.quotation_common import (
    parse_money_int,
    calculate_amounts,
    format_price_int,
    format_price_vat,
    format_price_total,
    vat_percent_label,
    resolve_entity_alias,
)
from scripts.verify_quotation import parse_formatted_amount, verify_amounts
from scripts.sync_payment_terms import check_payment_terms_reasonableness


class TestQuotationCommon(unittest.TestCase):

    def test_resolve_entity_alias(self):
        self.assertEqual(resolve_entity_alias('请用德音人力报价')['entity'], 'deyin')
        self.assertEqual(resolve_entity_alias('SCI')['entity'], 'sci')

    def test_resolve_entity_alias_refuses_ambiguous_country(self):
        result = resolve_entity_alias('印尼')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(set(result['candidates']), {'jakarta', 'sci', 'deyin'})

    def test_resolve_entity_alias_finds_ambiguous_country_in_phrase(self):
        result = resolve_entity_alias('请用印尼主体报价')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(set(result['candidates']), {'jakarta', 'sci', 'deyin'})

    def test_resolve_entity_alias_prefers_specific_nested_alias(self):
        result = resolve_entity_alias('请用上海新企业报价')
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['entity'], 'shanghai_new')

    def test_sci_is_an_independent_abbreviation_not_an_english_substring(self):
        self.assertEqual(resolve_entity_alias('请用 SCI 报价')['entity'], 'sci')
        self.assertEqual(resolve_entity_alias('science consulting quote')['status'], 'no_match')

    def test_alias_normalization_ignores_unicode_punctuation(self):
        result = resolve_entity_alias('请用 PT：SHAN/HAI MAP【报价】')
        self.assertEqual(result['entity'], 'jakarta')

    def test_resolve_entity_alias_detects_multiple_mentions(self):
        result = resolve_entity_alias('比较北京和西安主体')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(set(result['candidates']), {'beijing', 'xian'})

    def test_verifier_uses_half_up_rounding(self):
        amounts = {
            'subtotal': 107,
            'discount': 0,
            'vat_rate': 0.025,
            'vat': 2.68,
            'total': 109.68,
        }
        self.assertEqual(verify_amounts(amounts, 'RMB'), [])

    def test_verifier_rejects_bankers_rounding_result(self):
        amounts = {
            'subtotal': 107,
            'discount': 0,
            'vat_rate': 0.025,
            'vat': 2.67,
            'total': 109.67,
        }
        self.assertTrue(verify_amounts(amounts, 'RMB'))

    def test_formatted_amount_parser_always_returns_decimal(self):
        self.assertEqual(parse_formatted_amount('￥1,234.50'), Decimal('1234.50'))
        self.assertIsInstance(parse_formatted_amount('1,234'), Decimal)

    def test_verifier_rejects_missing_required_summary_fields(self):
        issues = verify_amounts({'subtotal': Decimal('100')}, 'RMB')
        self.assertIn('Tax amount not found in document', issues)
        self.assertIn('Tax rate not found in document', issues)
        self.assertIn('Total not found in document', issues)

    def test_verifier_reports_incomplete_withholding_pair_without_crashing(self):
        amounts = {
            'subtotal': Decimal('100'),
            'vat_rate': Decimal('0.06'),
            'vat': Decimal('6'),
            'total': Decimal('106'),
            'withholding_tax_rate': Decimal('0.03'),
        }
        issues = verify_amounts(amounts, 'RMB')
        self.assertIn('Withholding tax amount and rate must both be present or both be absent', issues)

    def test_parse_money_int(self):
        self.assertEqual(parse_money_int(1000, 'price'), Decimal('1000'))
        self.assertEqual(parse_money_int('1,000,000', 'price'), Decimal('1000000'))
        self.assertEqual(parse_money_int(' 500 ', 'price'), Decimal('500'))
        self.assertIsInstance(parse_money_int('500', 'price'), Decimal)

    def test_parse_money_int_rejects_boolean(self):
        with self.assertRaises(ValueError):
            parse_money_int(True, 'price')

    def test_parse_money_int_rejects_invalid_string(self):
        with self.assertRaises(ValueError):
            parse_money_int('abc', 'price')

    def test_calculate_amounts_idr(self):
        amounts = calculate_amounts(10_000_000, 0, Decimal('0.11'), 'IDR')
        self.assertEqual(amounts['subtotal'], 10_000_000)
        self.assertEqual(amounts['discount'], 0)
        self.assertEqual(amounts['discounted'], 10_000_000)
        self.assertEqual(amounts['vat'], 1_100_000)
        self.assertEqual(amounts['total'], 11_100_000)
        self.assertTrue(all(isinstance(amounts[key], Decimal) for key in (
            'subtotal', 'discount', 'discounted', 'vat', 'total')))

    def test_calculate_amounts_rmb(self):
        amounts = calculate_amounts(10000, 1000, Decimal('0.06'), 'RMB')
        self.assertEqual(amounts['subtotal'], 10000)
        self.assertEqual(amounts['discount'], 1000)
        self.assertEqual(amounts['discounted'], 9000)
        # VAT = 9000 * 0.06 = 540.00
        self.assertEqual(amounts['vat'], Decimal('540.00'))
        self.assertEqual(amounts['total'], Decimal('9540.00'))

    def test_calculate_amounts_rmb_rounding(self):
        amounts = calculate_amounts(1000, 0, Decimal('0.06'), 'RMB')
        self.assertEqual(amounts['vat'], Decimal('60.00'))

    def test_format_price_int(self):
        self.assertEqual(format_price_int(1000000, 'IDR'), '1,000,000')
        self.assertEqual(format_price_int(1000000, 'RMB'), '1,000,000')
        self.assertEqual(format_price_int(1000000, 'USD'), '1,000,000')

    def test_format_price_vat_rmb(self):
        self.assertEqual(format_price_vat(Decimal('540.00'), 'RMB'), '540')

    def test_format_price_vat_usd(self):
        self.assertEqual(format_price_vat(Decimal('60.00'), 'USD'), '60')

    def test_format_price_vat_idr(self):
        self.assertEqual(format_price_vat(1100000, 'IDR'), '1,100,000')

    def test_format_price_total_rmb(self):
        self.assertEqual(format_price_total(Decimal('9540.00'), 'RMB'), '9,540')

    def test_format_price_total_usd(self):
        self.assertEqual(format_price_total(Decimal('1060.00'), 'USD'), '1,060')

    def test_vat_percent_label(self):
        self.assertEqual(vat_percent_label(Decimal('0.06')), '6%')
        self.assertEqual(vat_percent_label(Decimal('0.11')), '11%')
        self.assertEqual(vat_percent_label(Decimal('0.01')), '1%')
        self.assertEqual(vat_percent_label(Decimal('0.10')), '10%')

    def test_payment_amount_check_keeps_large_idr_integer_precision(self):
        warnings = check_payment_terms_reasonableness(
            ['支付 IDR 9,007,199,254,740,993'],
            contract_total=Decimal('9007199254740992'),
            currency='IDR',
        )
        self.assertTrue(any('大于合同含税总计' in warning for warning in warnings))


if __name__ == '__main__':
    unittest.main()
