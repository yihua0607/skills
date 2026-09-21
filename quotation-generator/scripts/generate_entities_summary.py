#!/usr/bin/env python3
"""Generate docs/entities-summary.md from config/entities.json."""
import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(SKILL_DIR, 'config', 'entities.json')
OUTPUT_PATH = os.path.join(SKILL_DIR, 'docs', 'entities-summary.md')


def _percent(value):
    number = value * 100
    return f'{number:g}%'


def _tax_summary(cfg):
    tax = f'{cfg.get("tax_label", "增值税")} {_percent(cfg["vat_rate"])}'
    if 'withholding_tax_rate' not in cfg:
        return tax
    label = cfg.get('withholding_tax_label', '预扣税')
    mode = '固定扣减' if cfg.get('withholding_tax_required') else '可选扣减'
    return f'{tax}；{label} {_percent(cfg["withholding_tax_rate"])}（{mode}）'


def render_entities_summary(raw):
    lines = [
        '# 签约主体摘要',
        '',
        '> 此文件由 `scripts/generate_entities_summary.py` 根据 '
        '`config/entities.json` 自动生成，请勿手工编辑。',
        '',
        '| `--entity` | 签约主体 | 税金 | 默认币种 | 允许币种 |',
        '|---|---|---|---|---|',
    ]
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue
        lines.append(
            f'| `{key}` | {cfg["company"]} | {_tax_summary(cfg)} | '
            f'{cfg["currency"]} | {" / ".join(cfg["allowed_currencies"])} |')

    lines.extend(['', '## 歧义称呼', ''])
    ambiguous = raw.get('_meta', {}).get('ambiguous_aliases', {})
    if ambiguous:
        lines.extend([
            '| 用户说法 | 候选主体 |',
            '|---|---|',
        ])
        for alias, candidates in ambiguous.items():
            labels = ' / '.join(
                f'`{key}`（{raw[key]["company"]}）' for key in candidates)
            lines.append(f'| {alias} | {labels} |')
    else:
        lines.append('当前没有配置歧义称呼。')

    lines.extend(['', '## 别名', ''])
    for key, cfg in raw.items():
        if key.startswith('_'):
            continue
        lines.append(f'- `{key}`：{" / ".join(cfg.get("aliases", []))}')
    return '\n'.join(lines).rstrip() + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='Fail if the generated reference is stale')
    args = parser.parse_args()
    with open(CONFIG_PATH, encoding='utf-8') as fh:
        expected = render_entities_summary(json.load(fh))
    if args.check:
        try:
            with open(OUTPUT_PATH, encoding='utf-8') as fh:
                actual = fh.read()
        except FileNotFoundError:
            actual = ''
        if actual != expected:
            print('❌ docs/entities-summary.md is stale; '
                  'run generate_entities_summary.py', file=sys.stderr)
            return 1
        print('✅ 签约主体摘要与 entities.json 一致')
        return 0
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as fh:
        fh.write(expected)
    print(f'✅ 已生成 {OUTPUT_PATH}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
