#!/usr/bin/env python3
"""Currency conversion for quotation generation.

Rates come from the ShanhaiMap API response:
  * ``rateToCny`` = 1 CNY = N <服务币种>
  * ``rateToUsd`` = 1 USD = N <服务币种>

规则（2026-09-13 业务口径）：
  * 外币 → 人民币：用该外币的 ``rateToCny``（÷）
  * 外币 → 美元　：用该外币的 ``rateToUsd``（÷）
  * 人民币 → 外币 / 美元 → 外币：同一汇率反向（×）
  * 人民币 ↔ 美元：用一条外币的两个汇率做桥（两个数都在，不涉及缺数据）
  * 外币 → 外币（两侧都不是人民币/美元）：**API 没有直接汇率**，脚本拒绝换算，
    必须先向用户索取汇率，再用 ``--cross-rate N``（1 <from> = N <to>）换算

用法:
  # 外币 → 人民币（rateToCny 表示 1 CNY = N 服务币种，如 1 CNY = 2173.91 IDR）
  python3 scripts/convert_currency.py --amount 250000000 --from IDR --to RMB --rateToCny 2173.91

  # 人民币 → 外币（同一汇率反向）
  python3 scripts/convert_currency.py --amount 115000 --from RMB --to IDR --rateToCny 2173.91

  # 外币 → 美元（rateToUsd 表示 1 USD = N 服务币种，如 1 USD = 16000 IDR）
  python3 scripts/convert_currency.py --amount 250000000 --from IDR --to USD --rateToUsd 16000

  # 外币 → 外币：先向用户要汇率，再 --cross-rate（1 THB = N VND）
  python3 scripts/convert_currency.py --amount 100000 --from THB --to VND --cross-rate 1.05

  # 批量换算整批服务价格
  python3 scripts/convert_currency.py --query-result queried_services.json --to RMB

All calculations use Decimal for financial precision (ROUND_HALF_UP).
"""
import argparse
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

# 确保直接 `python3 scripts/convert_currency.py` 运行时能 import scripts.quotation_common。
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from scripts.quotation_common import (
    CURRENCIES,
    currency_symbol,
    normalize_currency_code,
)

# 汇率以这两个币种为基准（rateToCny / rateToUsd 的定义即基于它们）。
BASE_CURRENCIES = ('RMB', 'USD')
KNOWN_CURRENCIES = tuple(CURRENCIES)


def _to_decimal(value):
    """把 API 的字符串/数字汇率转成 Decimal；转不了、或非有限值返回 None。

    ``Decimal('nan')`` / ``Decimal('Infinity')`` 都能解析成功，但参与运算后不会抛
    异常，只会把金额一路污染成 NaN / Infinity 写进报价单，所以在解析层就挡掉。
    """
    if value is None:
        return None
    try:
        result = Decimal(str(value).replace(',', '').strip())
    except Exception:
        return None
    return result if result.is_finite() else None


def _rate_error(value, label):
    """汇率取值非法时返回给用户看的错误串；合法返回 None。

    0 会让除法直接 ArithmeticError 崩栈，负数会静默算出负金额，两者都是
    客户可见的财务错误，必须在换算前拦下。
    """
    if value is None or (value.is_finite() and value > 0):
        return None
    return (f'汇率 {label} 取值非法：{value}。汇率必须为正数'
            f'（例如 1 CNY = 2173.91 IDR → --rateToCny 2173.91）。'
            f'请向用户确认该币种的实时汇率后重新换算')


def _fmt(amount):
    """整数金额加千分位。"""
    return f'{int(amount):,}'


def _unknown_currency_error(code, role, rate_given):
    known = ', '.join(KNOWN_CURRENCIES)
    if rate_given:
        # 汇率已给出时，任何币种都能算——只提示一次代码不在已知表内。
        return None
    return (f'Unknown {role} currency: {code}. Known: {known}. '
            f'若该币种确有汇率，请显式传入 --rateToCny/--rateToUsd 后再换算')


def convert_single(amount: Decimal, from_currency: str, to_currency: str,
                   rateToCny: Decimal = None, rateToUsd: Decimal = None,
                   cross_rate: Decimal = None) -> dict:
    """Convert an amount between any of the supported currencies.

    规则见模块 docstring。返回 dict：original / converted / from_currency /
    to_currency / rate_used / rate_type / note；失败返回 ``{'error': ...}``。
    """
    from_c = normalize_currency_code(from_currency)
    to_c = normalize_currency_code(to_currency)

    # ── 币种合法性（未知币种仅在显式给了汇率时放行，避免拼错代码静默算出数）──
    if from_c not in CURRENCIES:
        err = _unknown_currency_error(from_c, 'source', rateToCny is not None or rateToUsd is not None or cross_rate is not None)
        if err:
            return {'error': err}
    if to_c not in CURRENCIES:
        err = _unknown_currency_error(to_c, 'target', rateToCny is not None or rateToUsd is not None or cross_rate is not None)
        if err:
            return {'error': err}

    if from_c == to_c:
        return {
            'original': str(amount),
            'converted': str(amount),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': '1',
            'rate_type': 'identity',
            'note': 'No conversion needed — same currency',
        }

    # ── 取值校验 ──
    # 放在同币种短路之后：同币种不需要汇率，调用方传进来的脏值不该让换算失败。
    if isinstance(amount, Decimal) and not amount.is_finite():
        return {'error': f'金额不是有限数值：{amount}，无法换算'}
    for value, label in ((rateToCny, 'rateToCny'), (rateToUsd, 'rateToUsd'),
                         (cross_rate, 'cross-rate')):
        err = _rate_error(value, label)
        if err:
            return {'error': err}

    from_is_base = from_c in BASE_CURRENCIES
    to_is_base = to_c in BASE_CURRENCIES

    # ── 外币 → 外币：API 无直接汇率，必须向用户索取 ──
    if not from_is_base and not to_is_base:
        if cross_rate is None:
            return {'error': (
                f'外币转外币（{from_c} → {to_c}）缺汇率数据：rateToCny/rateToUsd 只能换算'
                f'「外币 ↔ 人民币/美元」。请向用户索取汇率（例如 1 {from_c} = N {to_c}），'
                f'拿到后用 --cross-rate N 重新换算')}
        result = (amount * cross_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': str(cross_rate),
            'rate_type': 'crossRate',
            'note': (f'{from_c} {_fmt(amount)} × {cross_rate} = {to_c} {_fmt(result)}'
                     f'（交叉汇率由用户提供）'),
        }

    # ── 外币 → 人民币（÷ rateToCny）──
    if to_c == 'RMB' and not from_is_base:
        if rateToCny is None:
            return {'error': f'rateToCny is required for {from_c} → RMB conversion（1 CNY = N {from_c}）'}
        result = (amount / rateToCny).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': str(rateToCny),
            'rate_type': 'rateToCny',
            'note': f'{from_c} {_fmt(amount)} ÷ {rateToCny} = RMB {_fmt(result)}',
        }

    # ── 外币 → 美元（÷ rateToUsd）──
    if to_c == 'USD' and not from_is_base:
        if rateToUsd is None:
            return {'error': f'rateToUsd is required for {from_c} → USD conversion（1 USD = N {from_c}）'}
        result = (amount / rateToUsd).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': str(rateToUsd),
            'rate_type': 'rateToUsd',
            'note': f'{from_c} {_fmt(amount)} ÷ {rateToUsd} = USD {_fmt(result)}',
        }

    # ── 人民币 → 外币（× rateToCny，汇率属于目标外币）──
    if from_c == 'RMB' and not to_is_base:
        if rateToCny is None:
            return {'error': f'rateToCny is required for RMB → {to_c} conversion（1 CNY = N {to_c}）'}
        result = (amount * rateToCny).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': str(rateToCny),
            'rate_type': 'rateToCny',
            'note': f'RMB {_fmt(amount)} × {rateToCny} = {to_c} {_fmt(result)}',
        }

    # ── 美元 → 外币（× rateToUsd，汇率属于目标外币）──
    if from_c == 'USD' and not to_is_base:
        if rateToUsd is None:
            return {'error': f'rateToUsd is required for USD → {to_c} conversion（1 USD = N {to_c}）'}
        result = (amount * rateToUsd).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': str(rateToUsd),
            'rate_type': 'rateToUsd',
            'note': f'USD {_fmt(amount)} × {rateToUsd} = {to_c} {_fmt(result)}',
        }

    # ── 人民币 ↔ 美元：用外币的两个汇率做桥（两个数都在，不需要问用户）──
    if from_c == 'RMB' and to_c == 'USD':
        if rateToCny is None or rateToUsd is None:
            return {'error': 'Both rateToCny and rateToUsd are required for RMB → USD conversion（取同一条外币的两个汇率做桥）'}
        result = (amount * rateToCny / rateToUsd).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': f'{rateToCny}/{rateToUsd}',
            'rate_type': 'rateToCny+rateToUsd',
            'note': f'RMB {_fmt(amount)} × {rateToCny} ÷ {rateToUsd} = USD {_fmt(result)}',
        }

    if from_c == 'USD' and to_c == 'RMB':
        if rateToUsd is None or rateToCny is None:
            return {'error': 'Both rateToUsd and rateToCny are required for USD → RMB conversion（取同一条外币的两个汇率做桥）'}
        result = (amount * rateToUsd / rateToCny).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_c,
            'to_currency': to_c,
            'rate_used': f'{rateToUsd}/{rateToCny}',
            'rate_type': 'rateToUsd+rateToCny',
            'note': f'USD {_fmt(amount)} × {rateToUsd} ÷ {rateToCny} = RMB {_fmt(result)}',
        }

    return {'error': f'Unsupported conversion: {from_c} → {to_c}'}


def _rates_table(services):
    """汇总整批服务里出现过的人民币/美元汇率：{币种: {'rateToCny': D, 'rateToUsd': D}}。

    外币 → 外币被拒后，若整批里恰好有目标币种的服务，也能拿到它的汇率做桥。
    """
    table = {}
    for svc in services:
        code = normalize_currency_code(svc.get('服务币种'))
        if not code:
            continue
        slot = table.setdefault(code, {})
        for key, field in (('rateToCny', '人民币兑换服务币种汇率'),
                           ('rateToUsd', '美元兑换服务币种汇率')):
            value = _to_decimal(svc.get(field))
            if value is not None and key not in slot:
                slot[key] = value
    return table


def _pick_bridge(table, from_c, to_c):
    """人民币 ↔ 美元时挑一条两个汇率都有的外币做桥；优先 IDR。"""
    candidates = [c for c, r in table.items()
                  if c not in BASE_CURRENCIES and 'rateToCny' in r and 'rateToUsd' in r]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c != 'IDR', c))
    return candidates[0]


def batch_convert(query_result_path: str, to_currency: str) -> list:
    """Batch convert all service prices from an API query result JSON.

    读查文件里每个服务的价格/币种/汇率，换算到目标币种。外币 → 外币（两侧都不是
    人民币/美元）时返回错误，提示向用户索取汇率。
    """
    try:
        with open(query_result_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        return [{'error': f'Cannot read query result file: {exc}'}]

    services = data.get('services', [])
    if not services:
        return [{'error': 'No services found in query result'}]

    to_c = normalize_currency_code(to_currency)
    table = _rates_table(services)
    results = []

    for svc in services:
        name = svc.get('服务名称', 'unknown')
        price = svc.get('服务价格')
        from_norm = normalize_currency_code(svc.get('服务币种'))

        if price is None or from_norm is None:
            results.append({
                'service': name,
                'error': f'Missing price or currency: price={price}, currency={svc.get("服务币种")}',
            })
            continue

        try:
            if isinstance(price, str):
                price_d = Decimal(price.replace(',', '').strip())
            else:
                price_d = Decimal(str(price))
        except Exception:
            results.append({'service': name, 'error': f'Cannot parse price: {price}'})
            continue

        rateToCny_d = None
        rateToUsd_d = None

        if from_norm in BASE_CURRENCIES and to_c in BASE_CURRENCIES and from_norm != to_c:
            # 人民币 ↔ 美元：取一条外币的两个汇率做桥
            bridge = _pick_bridge(table, from_norm, to_c)
            if bridge:
                rateToCny_d = table[bridge].get('rateToCny')
                rateToUsd_d = table[bridge].get('rateToUsd')
        else:
            # 汇率的「非基准侧」= 两侧里不是人民币/美元的那个币种
            foreign = from_norm if from_norm not in BASE_CURRENCIES else to_c
            slot = dict(table.get(foreign, {}))
            own = {
                'rateToCny': _to_decimal(svc.get('人民币兑换服务币种汇率')),
                'rateToUsd': _to_decimal(svc.get('美元兑换服务币种汇率')),
            }
            if from_norm == foreign:
                # 服务本身就是该外币：优先用服务自带的汇率
                merged = {k: v for k, v in own.items() if v is not None}
                merged.update({k: v for k, v in slot.items() if k not in merged})
                slot = merged
            rateToCny_d = slot.get('rateToCny')
            rateToUsd_d = slot.get('rateToUsd')

        try:
            conversion = convert_single(price_d, from_norm, to_c, rateToCny_d, rateToUsd_d)
        except ArithmeticError as exc:
            # 取值校验应该已经拦住这类输入；留作兜底，免得一条脏数据掀翻整批换算。
            results.append({
                'service': name,
                'error': f'换算失败（{type(exc).__name__}）: {exc}；请检查汇率是否为 0',
            })
            continue
        conversion['service'] = name
        conversion['original_currency'] = from_norm
        results.append(conversion)

    return results


def format_output(result: dict) -> str:
    """Format a single conversion result for display."""
    if 'error' in result:
        prefix = f"[{result.get('service', '?')}] " if 'service' in result else ''
        return f"❌ {prefix}{result['error']}"

    service = result.get('service', '')
    prefix = f"{service}: " if service else ''

    original = result.get('original', '0')
    converted = result.get('converted', '0')
    from_c = result.get('from_currency', '?')
    to_c = result.get('to_currency', '?')
    note = result.get('note', '')

    # Format with currency symbols
    from_sym = currency_symbol(from_c)
    to_sym = currency_symbol(to_c)

    if from_c == to_c:
        return f"{prefix}{note}"

    # Format numbers with commas
    try:
        orig_formatted = f'{int(Decimal(original)):,}'
        conv_formatted = f'{int(Decimal(converted)):,}'
    except Exception:
        orig_formatted = original
        conv_formatted = converted

    from_display = f'{from_sym}{orig_formatted}' if from_sym else orig_formatted
    to_display = f'{to_sym}{conv_formatted}' if to_sym else conv_formatted

    return f"{prefix}{from_display} → {to_display}  ({note})"


def main():
    parser = argparse.ArgumentParser(
        description='Convert currency for quotation generation. All calculations use Decimal for precision.')

    # Single conversion mode
    parser.add_argument('--amount', type=str, default=None,
                        help='Amount to convert (integer or comma-formatted)')
    parser.add_argument('--from', dest='from_currency', default=None,
                        help=f'Source currency ({"|".join(KNOWN_CURRENCIES)})')
    parser.add_argument('--to', dest='to_currency', default=None,
                        help=f'Target currency ({"|".join(KNOWN_CURRENCIES)})')
    parser.add_argument('--rateToCny', type=str, default=None,
                        help='Exchange rate from API: 1 CNY = rateToCny service currency')
    parser.add_argument('--rateToUsd', type=str, default=None,
                        help='Exchange rate from API: 1 USD = rateToUsd service currency')
    parser.add_argument('--cross-rate', dest='cross_rate', type=str, default=None,
                        help='外币 → 外币专用：1 <from> = N <to>，N 由用户提供（API 无此汇率）')

    # Batch mode: convert all services from a query result
    parser.add_argument('--query-result', default=None,
                        help='Path to queried_services JSON for batch conversion')
    parser.add_argument('--json-only', action='store_true',
                        help='Print only structured JSON to stdout; human-readable lines go to stderr')

    args = parser.parse_args()

    # Batch mode
    if args.query_result:
        if not args.to_currency:
            parser.error('--to is required with --query-result')
        results = batch_convert(args.query_result, args.to_currency)

        if args.json_only:
            # Agent mode: human-readable → stderr, JSON → stdout
            for r in results:
                print(format_output(r), file=sys.stderr)
            output = {'conversions': results, 'to_currency': args.to_currency}
            json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            # Human mode: human-readable only on stdout
            for r in results:
                print(format_output(r))
        if any('error' in r for r in results):
            sys.exit(1)
        return

    # Single conversion mode
    if not args.amount or not args.from_currency or not args.to_currency:
        parser.error('--amount, --from, and --to are required for single conversion (or use --query-result)')

    # Parse amount
    try:
        amount_raw = args.amount.replace(',', '').strip()
        amount_d = Decimal(amount_raw)
    except Exception:
        print(f"❌ Cannot parse amount: {args.amount}（必须是有限数值）", file=sys.stderr)
        sys.exit(1)

    # Parse rates
    rateToCny_d = _to_decimal(args.rateToCny)
    if args.rateToCny is not None and rateToCny_d is None:
        print(f"❌ 无法使用 rateToCny: {args.rateToCny}（无法解析，或为 NaN/Infinity 等非有限值）",
              file=sys.stderr)
        sys.exit(1)
    rateToUsd_d = _to_decimal(args.rateToUsd)
    if args.rateToUsd is not None and rateToUsd_d is None:
        print(f"❌ 无法使用 rateToUsd: {args.rateToUsd}（无法解析，或为 NaN/Infinity 等非有限值）",
              file=sys.stderr)
        sys.exit(1)
    cross_rate_d = _to_decimal(args.cross_rate)
    if args.cross_rate is not None and cross_rate_d is None:
        print(f"❌ 无法使用 cross-rate: {args.cross_rate}（无法解析，或为 NaN/Infinity 等非有限值）",
              file=sys.stderr)
        sys.exit(1)

    try:
        result = convert_single(amount_d, args.from_currency, args.to_currency,
                                rateToCny_d, rateToUsd_d, cross_rate_d)
    except ArithmeticError as exc:
        # convert_single 内部已校验汇率取值，这里是兜底：宁可一行错误信息，
        # 也不要一段 traceback 让调用方误判成 SKILL 自身有 bug。
        print(f"❌ 换算失败（{type(exc).__name__}）: {exc}", file=sys.stderr)
        print("   常见原因：汇率为 0 或过于接近 0、金额超出 Decimal 精度。", file=sys.stderr)
        sys.exit(1)

    if 'error' in result:
        print(f"❌ {result['error']}", file=sys.stderr)
        sys.exit(1)

    # Human-readable output
    if args.json_only:
        print(format_output(result), file=sys.stderr)
        # Structured JSON output for Agent consumption — stdout is clean JSON
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(format_output(result))


if __name__ == '__main__':
    main()
