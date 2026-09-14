#!/usr/bin/env python3
"""Pre-flight validation for quotation data BEFORE running build_quotation.py.

Checks data structure, field coverage, and business rules without generating
a .docx — so the Agent can fix issues before the expensive build step.

Usage:
  python3 scripts/validate_data.py --data quotation.json --entity xian
  python3 scripts/validate_data.py --data quotation.json --entity jakarta

Exit codes:
  0 — all checks passed (may have warnings)
  1 — validation failed (errors found)
"""
import argparse
import json
import os
import sys

# Ensure imports work when this script is run directly as `python3 scripts/validate_data.py`.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from scripts.quotation_common import (
    calculate_amounts,
    format_price_int,
    format_price_vat,
    format_price_total,
    vat_percent_label,
    load_entity_config,
    price_magnitude_warnings,
    is_target_currency,
    is_process_time_disclaimer,
    TARGET_CURRENCIES,
)
from scripts.sync_payment_terms import check_payment_terms_reasonableness
from scripts.quotation_schema import validate_and_normalize_data as schema_validate_and_normalize_data


def validate_quotation_data(data, entity_key, entity_config, universal_excludes=None):
    """Validate quotation data and return list of errors and warnings.

    Schema-level checks (services, fee_details, process_data, doc_data, notes,
    discount, quote_meta structure) are delegated to quotation_schema.py. This
    function only adds entity-specific, business-level checks.
    """
    errors = []
    warnings = []

    if not isinstance(data, dict):
        errors.append('Quotation data must be a JSON object')
        return errors, warnings

    # ── Schema validation (delegated to quotation_schema.py) ──
    schema_ok = False
    validated = None
    try:
        validated = schema_validate_and_normalize_data(data)
        schema_ok = True
        warnings.extend(validated.get('warnings', []))
    except ValueError as exc:
        message = str(exc)
        if message.startswith('Invalid quotation data:\n- '):
            errors.extend(message.split('\n- ')[1:])
        else:
            errors.append(message)

    # ── Entity validation (entity-specific, not in schema module) ──
    if entity_key not in entity_config:
        errors.append(f'Unknown entity: {entity_key}. Available: {", ".join(entity_config.keys())}')
        return errors, warnings

    entity_cfg = entity_config[entity_key]
    entity_default_currency = entity_cfg['currency']
    currency = entity_default_currency

    meta = data.get('_meta', {})
    if meta is not None and not isinstance(meta, dict):
        errors.append('_meta must be an object when provided')
    elif isinstance(meta, dict):
        meta_entity = meta.get('applicable_entity')
        if meta_entity and meta_entity != entity_key:
            errors.append(f'_meta.applicable_entity ({meta_entity}) must match --entity ({entity_key})')
        target_currency = meta.get('target_currency')
        if target_currency:
            if not is_target_currency(target_currency):
                errors.append(f'_meta.target_currency ({target_currency}) must be one of: {", ".join(TARGET_CURRENCIES)}')
            else:
                currency = target_currency
        allowed_currencies = entity_cfg.get('allowed_currencies', [entity_default_currency])
        if currency not in allowed_currencies:
            errors.append(
                f'_meta.target_currency ({currency}) is not allowed for --entity ({entity_key}); '
                f'allowed: {", ".join(allowed_currencies)}')

    # ── 预扣税只支持配了 withholding_tax_rate 的主体（当前仅 thailand）──
    # 未配置的主体写 true 时，build 会静默按不扣税生成（报价单不会出现预扣税行），
    # 但 verify 拿 quotation.json 的 flag 比对成稿，必然报「expected in data but not
    # found in document」——问题会拖到最后一关才暴露，所以在这里直接拦下。
    wht_rate = entity_cfg.get('withholding_tax_rate')
    wht_flag = data.get('withholding_tax')
    if wht_rate is None and wht_flag is True:
        errors.append(
            f'withholding_tax 仅支持已配置 withholding_tax_rate 的签约主体，--entity {entity_key} '
            f'未配置。请删除该字段或改为 false；保留 true 会被 build 静默忽略，'
            f'并在 verify 阶段报「Withholding tax expected in data but not found in document」')
    elif wht_rate is not None and 'withholding_tax' not in data:
        warnings.append(
            f'--entity {entity_key} 支持预扣税（{float(wht_rate) * 100:g}%），但 quotation.json 缺少顶层 '
            f'withholding_tax 字段：本次不会扣除。按业务要求需先向用户确认——'
            f'要扣则填 true，明确不扣则填 false，不要留空')

    # ── Notes must not contain currency exchange / rate info ──
    notes = data.get('notes', [])
    if notes:
        rate_keywords = ['汇率', '折算', '原币种', '兑换']
        for i, note in enumerate(notes):
            text = note.get('text', '') if isinstance(note, dict) else str(note)
            for kw in rate_keywords:
                if kw in text:
                    errors.append(
                        f'notes[{i}] contains prohibited currency exchange keyword "{kw}". '
                        'Notes must not describe currency conversion or exchange rates.'
                    )
                    break
            # 「服务内容」表没有办理时间列，表下备注写办理时间免责声明无所指
            if is_process_time_disclaimer(text):
                errors.append(
                    f'notes[{i}] 是办理时间免责声明，必须删除：服务内容表没有「办理时间」列，'
                    '备注中的办理时间指不到任何上文。这句话要表达的信息已由文末标准备注第 1 条'
                    '（办理时间自「收集齐所需资料及信息」起算，法定节假日及办证机构休息日不算入）'
                    '完整覆盖——客户准备材料、签字盖章反馈、文件邮寄都在起算点之前，本就不计入。'
                    '直接删除即可，不要改写成别的措辞，也不必另找位置补写一遍。'
                    f'原文：{text}'
                )

    # ── Entity-specific checks that depend on validated data ──
    if schema_ok and validated is not None:
        all_prices = [item['price_int'] for group in validated['services'] for item in group['items']]

        # Price magnitude check (business-level guard against currency mix-ups)
        warnings.extend(price_magnitude_warnings(all_prices, currency))

        # Universal excludes — check per-service exclude lists for items that
        # should instead appear in notes (shared across all services)
        for i, fd in enumerate(validated['fee_details']):
            exclude = fd.get('exclude', [])
            if exclude and universal_excludes:
                overlap = [item for item in exclude if any(ue in item for ue in universal_excludes)]
                if overlap:
                    warnings.append(
                        f'fee_details[{i}].exclude contains universal items that should be in notes instead: '
                        f'{", ".join(overlap)}')

        # VAT sanity preview
        subtotal = sum(all_prices)
        discount_int = validated['discount_amount']
        wht_rate = entity_cfg.get('withholding_tax_rate')
        wht_enabled = validated.get('withholding_tax', False)
        amounts = calculate_amounts(
            subtotal, discount_int, entity_cfg['vat_rate'], currency,
            withholding_tax_rate=wht_rate if wht_enabled else None
        )
        vat_pct = vat_percent_label(amounts['vat_rate'])
        tax_label = entity_cfg.get('tax_label', '增值税')
        wht_info = f" | 预扣税={format_price_int(amounts.get('withholding_tax', 0), currency)}" if amounts.get('withholding_tax') is not None else ""
        print(f"  预估: 小计={format_price_int(amounts['subtotal'], currency)} | "
              f"优惠={format_price_int(amounts['discount'], currency)} | "
              f"{tax_label}({vat_pct})={format_price_vat(amounts['vat'], currency)}{wht_info} | "
              f"含税总计={format_price_total(amounts['total'], currency)}")

        # Payment terms reasonableness (user-managed; warn only)
        payment_terms = validated['quote_meta'].get('payment_terms')
        if payment_terms:
            warnings.extend(check_payment_terms_reasonableness(
                payment_terms,
                contract_total=amounts.get('total'),
                currency=currency,
            ))

    return errors, warnings


def main():
    entity_config, universal_excludes = load_entity_config()

    parser = argparse.ArgumentParser(
        description='Validate quotation data before building .docx — catch errors early.')
    parser.add_argument('--data', required=True, help='Quotation data file (.json)')
    parser.add_argument('--entity', required=True,
                        choices=list(entity_config.keys()),
                        help='Signing entity (required)')
    args = parser.parse_args()

    data_path = os.path.abspath(args.data)
    if not os.path.exists(data_path):
        print(f"❌ Data file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        print(f"❌ Cannot parse JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Validating: {data_path}")
    print(f"Entity: {args.entity} ({entity_config[args.entity]['company']})")

    errors, warnings = validate_quotation_data(data, args.entity, entity_config, universal_excludes)

    # ── Output results ──
    if warnings:
        for w in warnings:
            print(f"⚠️  {w}")

    if errors:
        print(f"\n❌ 数据校验失败: {len(errors)} 个问题")
        for e in errors:
            print(f"  - {e}")
        print("\n请修正以上问题后再运行 build_quotation.py")
        sys.exit(1)
    else:
        print("\n✅ 数据校验通过: 所有字段完整、金额合理")
        print("可以运行 build_quotation.py 生成报价单")
        sys.exit(0)


if __name__ == '__main__':
    main()
