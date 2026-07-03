"""Unit tests for scripts/quotation_common.py."""
import unittest
from decimal import Decimal

from scripts.quotation_common import (
    parse_money_int,
    calculate_amounts,
    format_price_int,
    format_price_vat,
    format_price_total,
    vat_percent_label,
)


class TestQuotationCommon(unittest.TestCase):

    def test_parse_money_int(self):
        self.assertEqual(parse_money_int(1000, 'price'), 1000)
        self.assertEqual(parse_money_int('1,000,000', 'price'), 1000000)
        self.assertEqual(parse_money_int(' 500 ', 'price'), 500)

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
        self.assertEqual(format_price_int(1000000, 'IDR'), 'Rp 1,000,000')
        self.assertEqual(format_price_int(1000000, 'RMB'), '￥1,000,000')

    def test_format_price_vat_rmb(self):
        self.assertEqual(format_price_vat(Decimal('540.00'), 'RMB'), '￥540.00')

    def test_format_price_vat_idr(self):
        self.assertEqual(format_price_vat(1100000, 'IDR'), 'Rp 1,100,000')

    def test_format_price_total_rmb(self):
        self.assertEqual(format_price_total(Decimal('9540.00'), 'RMB'), '￥9,540.00')

    def test_vat_percent_label(self):
        self.assertEqual(vat_percent_label(Decimal('0.06')), '6%')
        self.assertEqual(vat_percent_label(Decimal('0.11')), '11%')
        self.assertEqual(vat_percent_label(Decimal('0.01')), '1%')


if __name__ == '__main__':
    unittest.main()
