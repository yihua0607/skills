#!/usr/bin/env python3
"""Generate references/entity-bank-info.md from config/entities.json."""
import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(SKILL_DIR, 'config', 'entities.json')
OUTPUT_PATH = os.path.join(SKILL_DIR, 'references', 'entity-bank-info.md')


def render_bank_reference(raw):
    lines = [
        '# 实体银行信息对照',
        '',
        '> 此文件由 `scripts/generate_bank_reference.py` 根据 '
        '`config/entities.json` 自动生成，请勿手工编辑。',
        '',
    ]
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue
        lines.extend([
            f'## {key}', '',
            f'- 签约主体：{cfg["company"]}',
            f'- 默认币种：{cfg["currency"]}',
            f'- 允许币种：{" / ".join(cfg["allowed_currencies"])}',
            f'- SWIFT 政策：{cfg["swift_policy"]}',
            '',
        ])
        bank_notes = cfg.get('bank_notes', [])
        if bank_notes:
            lines.extend(['### 银行说明', ''])
            lines.extend(f'- {note}' for note in bank_notes)
            lines.append('')
        by_currency = cfg.get('bank_lines_by_currency')
        if by_currency:
            for currency, bank_lines in by_currency.items():
                lines.extend([f'### {currency} 账户', ''])
                lines.extend(f'- {item}' for item in bank_lines)
                lines.append('')
            uncovered = [c for c in cfg['allowed_currencies'] if c not in by_currency]
            if uncovered:
                lines.extend([
                    f'### 默认账户（{" / ".join(uncovered)}）', '',
                    *(f'- {item}' for item in cfg['bank_lines']),
                    '',
                ])
        else:
            lines.extend(['### 通用账户', ''])
            lines.extend(f'- {item}' for item in cfg['bank_lines'])
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Fail if the generated reference is stale')
    args = parser.parse_args()
    with open(CONFIG_PATH, encoding='utf-8') as fh:
        expected = render_bank_reference(json.load(fh))
    if args.check:
        try:
            with open(OUTPUT_PATH, encoding='utf-8') as fh:
                actual = fh.read()
        except FileNotFoundError:
            actual = ''
        if actual != expected:
            print('❌ references/entity-bank-info.md is stale; run generate_bank_reference.py', file=sys.stderr)
            return 1
        print('✅ 银行信息参考文档与 entities.json 一致')
        return 0
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as fh:
        fh.write(expected)
    print(f'✅ 已生成 {OUTPUT_PATH}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
