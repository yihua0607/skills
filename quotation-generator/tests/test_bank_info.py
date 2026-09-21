"""Bank-account config: per-currency accounts, and the entity detection that reads them.

An entity can hold a different account for each currency it quotes in. The account
holder name is whatever the bank recorded, and banks punctuate inconsistently
('PT. SHAN HAI MAP' / 'PT.SHAN HAI MAP' / 'PT SHAN HAI MAP'), so the comparison and
the detection both have to survive that without the quotation printing a name the
bank would reject.
"""
import json
import copy
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
from scripts.generate_bank_reference import render_bank_reference
from scripts.generate_entities_summary import render_entities_summary
from scripts.quotation_common import load_entity_config_with_meta, validate_entity_configs

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

    def test_slf_idr_and_usd_share_the_declared_bni_account(self):
        slf = self.config['slf']
        self.assertEqual(slf['allowed_currencies'], ['IDR', 'USD'])
        self.assertEqual(slf['bank_lines_by_currency']['IDR'], slf['bank_lines'])
        self.assertEqual(slf['bank_lines_by_currency']['USD'], slf['bank_lines'])
        joined = '\n'.join(slf['bank_lines'])
        self.assertIn('BNI Branch Central Park Mall', joined)
        self.assertIn('Hukum SEA Firma', joined)
        self.assertIn('1882660986', joined)
        self.assertIn('BNINIDJAXXX', joined)

    def test_stc_idr_and_usd_share_the_declared_bca_account(self):
        stc = self.config['stc']
        self.assertEqual(stc['allowed_currencies'], ['IDR', 'USD'])
        self.assertEqual(stc['bank_lines_by_currency']['IDR'], stc['bank_lines'])
        self.assertEqual(stc['bank_lines_by_currency']['USD'], stc['bank_lines'])
        self.assertEqual(stc['withholding_tax_rate'], 0.02)
        self.assertEqual(stc['withholding_tax_label'], 'PPH23')
        self.assertTrue(stc['withholding_tax_required'])
        joined = '\n'.join(stc['bank_lines'])
        self.assertIn('BCA (KCP CENTRAL PARK)', joined)
        self.assertIn('PT. SHM TAX CONSULTING', joined)
        self.assertIn('5485483133', joined)
        self.assertIn('CENAIDJA', joined)

    def test_shanghai_new_uses_its_declared_bank_of_china_account(self):
        joined = '\n'.join(self.config['shanghai_new']['bank_lines'])
        self.assertIn('上海山海图新企业咨询有限公司', joined)
        self.assertIn('91310113MAEW51431Q', joined)
        self.assertIn('4520 8977 3373', joined)
        self.assertIn('中国银行股份有限公司上海市虹桥会展中心支行', joined)
        self.assertIn('104290020130', joined)
        self.assertIn('BKCHCNBJ300', joined)
        self.assertNotIn('687482901201', joined)

    def test_by_currency_keys_are_all_offered(self):
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            for currency in cfg.get('bank_lines_by_currency', {}):
                with self.subTest(entity=key, currency=currency):
                    self.assertIn(currency, cfg['allowed_currencies'])

    def test_generated_reference_matches_entity_config(self):
        expected = render_bank_reference(self.config)
        path = os.path.join(SKILL_ROOT, 'docs', 'entity-bank-info.md')
        with open(path, encoding='utf-8') as fh:
            self.assertEqual(fh.read(), expected)

    def test_generated_entity_summary_matches_entity_config(self):
        expected = render_entities_summary(self.config)
        path = os.path.join(SKILL_ROOT, 'docs', 'entities-summary.md')
        with open(path, encoding='utf-8') as fh:
            self.assertEqual(fh.read(), expected)

    def test_entity_summary_contains_every_entity_and_ambiguous_alias(self):
        rendered = render_entities_summary(self.config)
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            self.assertIn(f'`{key}`', rendered)
            self.assertIn(cfg['company'], rendered)
        for alias, candidates in self.config['_meta']['ambiguous_aliases'].items():
            self.assertIn(alias, rendered)
            for candidate in candidates:
                self.assertIn(f'`{candidate}`', rendered)

    def test_config_meta_exposes_header_image_defaults(self):
        _, _, meta = load_entity_config_with_meta()
        self.assertEqual(meta['header_image_defaults']['ink_top_mm'], 6.0)
        self.assertEqual(meta['header_image_defaults']['line_gap_mm'], 1.0)
        self.assertEqual(meta['header_image_defaults']['body_top_mm'], 30.0)
        self.assertEqual(meta['header_image_defaults']['crop_padding_px'], 20)

    def test_required_entity_fields_have_one_authoritative_source(self):
        self.assertNotIn('required_entity_fields', self.config['_meta'])

    def test_every_entity_declares_swift_policy(self):
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            with self.subTest(entity=key):
                self.assertIn(cfg['swift_policy'], {'required', 'not_required'})

    def test_thailand_layout_and_tax_note_are_configuration(self):
        thailand = self.config['thailand']
        self.assertEqual(thailand['tax_note'], '（以开发票时泰国现行税率为准）')
        self.assertEqual(thailand['summary_layout']['subtotal'], [1, 2, 2, 'left'])
        self.assertEqual(thailand['summary_layout']['other'], [0, 3, 2, 'left'])

    def test_invalid_summary_layout_is_rejected(self):
        config = copy.deepcopy(self.config)
        config['thailand']['summary_layout']['subtotal'] = [1, 4, 2, 'diagonal']
        errors = validate_entity_configs(config)
        self.assertTrue(any('exceeds the 5-column table' in error for error in errors))
        self.assertTrue(any('alignment is invalid' in error for error in errors))

    def test_swift_policy_is_checked_against_every_account(self):
        config = copy.deepcopy(self.config)
        config['jakarta']['bank_lines_by_currency']['USD'] = [
            line for line in config['jakarta']['bank_lines_by_currency']['USD']
            if 'swift' not in line.casefold()
        ]
        errors = validate_entity_configs(config)
        self.assertTrue(any("jakarta' account 'USD' requires SWIFT" in error for error in errors))


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


class TestCurrencyAccountLabels(unittest.TestCase):
    """币种账户里的币种标记必须指它自己那个币种。

    越南主体的人民币账户与美元账户是同一个账号（104 799 1540），线上一度把美元
    那一份整段照抄给了人民币，连「(USD)」后缀一起印进报价单——客户照这个账号汇
    人民币会被退汇。埃及同样共用账户，但它印的是不带后缀的裸账号，才是对的样子。
    """

    CURRENCY_MARKERS = ('IDR', 'RMB', 'CNY', 'USD', 'SGD', 'THB', 'VND', 'EGP', 'MYR')

    def setUp(self):
        self.config = _entity_config()

    @staticmethod
    def _same_currency(a, b):
        return a == b or {a, b} == {'RMB', 'CNY'}

    def test_no_account_block_is_labelled_with_another_currencys_code(self):
        """通用断言：任何币种账户里都不该出现别的币种的 (XXX) 后缀。"""
        for key, cfg in self.config.items():
            if key.startswith('_'):
                continue
            for currency, lines in cfg.get('bank_lines_by_currency', {}).items():
                block = '\n'.join(lines)
                for other in self.CURRENCY_MARKERS:
                    if self._same_currency(currency, other):
                        continue
                    with self.subTest(entity=key, currency=currency, label=other):
                        self.assertNotIn(
                            f'({other})', block,
                            f'{key} 的 {currency} 账户里出现了 ({other}) 后缀——'
                            f'客户会照这个账号汇错币种')

    def test_vietnam_rmb_account_shares_the_number_but_not_the_usd_label(self):
        by_currency = self.config['vietnam']['bank_lines_by_currency']
        usd = '\n'.join(by_currency['USD'])
        rmb = '\n'.join(by_currency['RMB'])

        # 同一个账号，这是银行的安排，不是配置抄错。
        self.assertIn('104 799 1540', usd)
        self.assertIn('104 799 1540', rmb)
        # 但人民币那一份不能带美元后缀。
        self.assertIn('(USD)', usd)
        self.assertNotIn('(USD)', rmb)
        # VND 是另一个账号，别被上面的共用带偏。
        self.assertIn('104 799 1200', '\n'.join(by_currency['VND']))
