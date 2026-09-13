"""Bank-account config: per-currency accounts, and the entity detection that reads them.

An entity can hold a different account for each currency it quotes in. The account
holder name is whatever the bank recorded, and banks punctuate inconsistently
('PT. SHAN HAI MAP' / 'PT.SHAN HAI MAP' / 'PT SHAN HAI MAP'), so the comparison and
the detection both have to survive that without the quotation printing a name the
bank would reject.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_ROOT not in sys.path:
    sys.path.insert(0, SKILL_ROOT)

from scripts.verify_quotation import (
    company_names_match,
    detect_entity,
    find_bank_info_section,
    normalize_company_name,
)

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _entity_config():
    with open(os.path.join(SKILL_ROOT, 'config', 'entities.json'), encoding='utf-8') as fh:
        return json.load(fh)


def _bank_section(*lines):
    """The paragraph list detect_entity sees, wrapped in the real section markers."""
    return [(None, '7.所有款项汇到指定的银行账户，银行账户信息如下：')] + \
           [(None, line) for line in lines]


def _run(script, args):
    return subprocess.run(
        [sys.executable, os.path.join(SKILL_ROOT, 'scripts', script)] + args,
        cwd=SKILL_ROOT, capture_output=True, text=True, encoding='utf-8')


class TestCompanyNameComparison(unittest.TestCase):

    def test_dot_spellings_of_one_name_compare_equal(self):
        """The three ways banks write the same holder name must all match."""
        spellings = ['PT. SHAN HAI MAP', 'PT.SHAN HAI MAP', 'PT SHAN HAI MAP']
        reference = spellings[0]
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                self.assertTrue(
                    company_names_match(spelling, reference),
                    f'{spelling!r} should match {reference!r}')
                self.assertEqual(
                    normalize_company_name(spelling),
                    normalize_company_name(reference))

    def test_normalization_does_not_glue_words_together(self):
        """Dropping the dot instead of spacing it would fuse 'PT.SHAN' into 'PTSHAN'."""
        self.assertNotIn('PTSHAN', normalize_company_name('PT.SHAN HAI MAP'))

    def test_different_companies_still_differ(self):
        self.assertFalse(company_names_match('PT. SHAN HAI MAP', 'PT DEIN TALENT SOLUTIONS'))


class TestDetectEntityFromBankSection(unittest.TestCase):

    def setUp(self):
        self.config = _entity_config()

    def test_detects_jakarta_from_its_usd_account(self):
        """jakarta bills USD from BNI, not from the default BCA account."""
        cfg = self.config['jakarta']['bank_lines_by_currency']['USD']
        self.assertEqual(detect_entity(_bank_section(*cfg), self.config), 'jakarta')

    def test_detects_jakarta_from_its_rmb_account(self):
        cfg = self.config['jakarta']['bank_lines_by_currency']['RMB']
        self.assertEqual(detect_entity(_bank_section(*cfg), self.config), 'jakarta')

    def test_detects_jakarta_from_its_default_account(self):
        self.assertEqual(
            detect_entity(_bank_section(*self.config['jakarta']['bank_lines']), self.config),
            'jakarta')

    def test_detects_deyin_from_its_usd_account(self):
        cfg = self.config['deyin']['bank_lines_by_currency']['USD']
        self.assertEqual(detect_entity(_bank_section(*cfg), self.config), 'deyin')

    def test_every_entity_detects_from_every_currency_it_offers(self):
        """Detection is the anchor for entity-specific checks; it must not depend
        on which currency variant happened to be configured as the default."""
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            variants = dict(cfg.get('bank_lines_by_currency', {}))
            variants.setdefault(cfg['currency'], cfg['bank_lines'])
            for currency, lines in variants.items():
                with self.subTest(entity=key, currency=currency):
                    self.assertEqual(
                        detect_entity(_bank_section(*lines), self.config), key)

    def test_no_bank_section_yields_no_entity(self):
        self.assertIsNone(detect_entity([(None, '一些无关的段落')], self.config))


class TestBankInfoConfigIntegrity(unittest.TestCase):

    def setUp(self):
        self.config = _entity_config()

    def test_section_markers_are_the_ones_the_detector_looks_for(self):
        """Guards the fixture: the detector only reads paragraphs after 所有款项汇到."""
        parsed = find_bank_info_section(_bank_section('银行名称：X', '银行账号：1234'))
        self.assertEqual(parsed, ['银行名称：X', '银行账号：1234'])

    def test_every_entity_has_a_usable_default_account(self):
        """`bank_lines` is the fallback for any currency without its own entry, and
        most entities bill every currency from one account, so it must be present
        and substantial."""
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            with self.subTest(entity=key):
                lines = cfg['bank_lines']
                self.assertGreaterEqual(len(lines), 3,
                                        f'{key}: default bank_lines look incomplete')
                self.assertTrue(all(isinstance(ln, str) and ln.strip() for ln in lines),
                                f'{key}: blank line in default bank_lines')

    def test_jakarta_holds_a_separate_account_per_currency(self):
        """jakarta bills IDR from BCA, USD from BNI and RMB from ICBC. These are the
        accounts the banks issued, so the numbers are pinned here — a quotation that
        prints the wrong one sends the customer's money to an account that will not
        accept it."""
        by_currency = self.config['jakarta']['bank_lines_by_currency']
        expected = {
            'IDR': ('5485225789', 'CENAIDJA', 'BCA'),
            'USD': ('6060677669', 'BNINIDJAXXX', 'BNI'),
            'RMB': ('0120020400000811366', 'ICBKIDJAXXX', 'ICBC'),
        }
        self.assertEqual(set(by_currency), set(expected))
        for currency, (account, swift, bank) in expected.items():
            with self.subTest(currency=currency):
                joined = '\n'.join(by_currency[currency])
                self.assertIn(account, joined)
                self.assertIn(swift, joined)
                self.assertIn(bank, joined)

    def test_jakarta_default_stays_the_local_currency_account(self):
        """The IDR account is both jakarta's default and its own currency entry."""
        self.assertEqual(self.config['jakarta']['bank_lines_by_currency']['IDR'],
                         self.config['jakarta']['bank_lines'])

    def test_by_currency_keys_are_all_offered(self):
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            for currency in cfg.get('bank_lines_by_currency', {}):
                with self.subTest(entity=key, currency=currency):
                    self.assertIn(currency, cfg['allowed_currencies'])


class TestPerCurrencyAccountInGeneratedDocx(unittest.TestCase):

    def _build(self, currency, tmpdir):
        with open(os.path.join(SKILL_ROOT, 'examples', 'minimal_quotation.json'),
                  encoding='utf-8') as fh:
            data = json.load(fh)
        data['_meta']['applicable_entity'] = 'jakarta'
        data['_meta']['target_currency'] = currency
        data_path = os.path.join(tmpdir, f'{currency}.json')
        with open(data_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        docx_path = os.path.join(tmpdir, f'jakarta-{currency}.docx')
        result = _run('build_quotation.py',
                      ['--entity', 'jakarta', '--data', data_path, '--output', docx_path])
        self.assertEqual(result.returncode, 0,
                         f'build failed for {currency}:\n{result.stdout}\n{result.stderr}')
        return data_path, docx_path

    def _bank_section(self, docx_path):
        with zipfile.ZipFile(docx_path) as zf:
            root = ET.fromstring(zf.read('word/document.xml'))
        paras = [''.join(t.text or '' for t in p.iter(f'{W}t')) for p in root.iter(f'{W}p')]
        started, out = False, []
        for text in paras:
            if '所有款项汇到' in text:
                started = True
                continue
            if started:
                if '保密义务' in text or '报价从报价日起' in text:
                    break
                out.append(text)
        return out

    def test_each_currency_prints_its_own_account_and_verifies(self):
        expected = _entity_config()['jakarta']['bank_lines_by_currency']
        with tempfile.TemporaryDirectory(prefix='bank-info-') as tmpdir:
            for currency, lines in expected.items():
                with self.subTest(currency=currency):
                    data_path, docx_path = self._build(currency, tmpdir)
                    self.assertEqual(self._bank_section(docx_path), lines)

                    # No --entity: the verifier has to recognise the entity from
                    # whichever account this currency uses.
                    result = _run('verify_quotation.py',
                                  ['--input', docx_path, '--data', data_path])
                    self.assertEqual(result.returncode, 0,
                                     f'verify failed for {currency}:\n{result.stdout}')
                    self.assertNotIn('Could not auto-detect', result.stdout + result.stderr)
                    self.assertIn(f'签约主体: jakarta', result.stdout)


if __name__ == '__main__':
    unittest.main()
