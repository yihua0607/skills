#!/usr/bin/env python3
"""Currency conversion for quotation generation.

Uses rates from the ShanhaiMap API response (rateToCny / rateToUsd)
to convert service prices between IDR, RMB, and USD.

Usage:
  # Convert IDR → RMB (using rateToCny from API)
  python3 scripts/convert_currency.py --amount 250000000 --from IDR --to RMB --rateToCny 0.00046

  # Convert RMB → IDR (using rateToCny from API, inverted)
  python3 scripts/convert_currency.py --amount 115000 --from RMB --to IDR --rateToCny 0.00046

  # Convert IDR → USD (using rateToUsd from API)
  python3 scripts/convert_currency.py --amount 250000000 --from IDR --to USD --rateToUsd 0.000063

  # Batch convert multiple amounts from API query result
  python3 scripts/convert_currency.py --query-result queried_services.json --to RMB

All calculations use Decimal for financial precision.
"""
import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_UP


def convert_single(amount: Decimal, from_currency: str, to_currency: str,
                   rateToCny: Decimal = None, rateToUsd: Decimal = None) -> dict:
    """Convert a single amount between IDR, RMB, and USD.

    Conversion logic:
      - IDR → RMB: round(totalPrice / rateToCny)
      - RMB → IDR: round(totalPrice * rateToCny)
      - IDR → USD: round(totalPrice / rateToUsd)
      - USD → IDR: round(totalPrice * rateToUsd)
      - RMB → USD: via IDR if only rateToCny available; via direct if rateToUsd provided
      - Same currency: no conversion needed

    Returns dict with: original, converted, from_currency, to_currency, rate_used, rate_type
    """
    if from_currency == to_currency:
        return {
            'original': str(amount),
            'converted': str(amount),
            'from_currency': from_currency,
            'to_currency': to_currency,
            'rate_used': '1',
            'rate_type': 'identity',
            'note': 'No conversion needed — same currency',
        }

    # Determine which rate to use
    if from_currency == 'IDR' and to_currency == 'RMB':
        if rateToCny is None:
            return {'error': 'rateToCny is required for IDR → RMB conversion'}
        result = (amount / rateToCny).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_currency,
            'to_currency': to_currency,
            'rate_used': str(rateToCny),
            'rate_type': 'rateToCny',
            'note': f'IDR {int(amount):,} ÷ {rateToCny} = RMB {int(result):,}',
        }

    elif from_currency == 'RMB' and to_currency == 'IDR':
        if rateToCny is None:
            return {'error': 'rateToCny is required for RMB → IDR conversion'}
        result = (amount * rateToCny).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_currency,
            'to_currency': to_currency,
            'rate_used': str(rateToCny),
            'rate_type': 'rateToCny',
            'note': f'RMB {int(amount):,} × {rateToCny} = IDR {int(result):,}',
        }

    elif from_currency == 'IDR' and to_currency == 'USD':
        if rateToUsd is None:
            return {'error': 'rateToUsd is required for IDR → USD conversion'}
        result = (amount / rateToUsd).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_currency,
            'to_currency': to_currency,
            'rate_used': str(rateToUsd),
            'rate_type': 'rateToUsd',
            'note': f'IDR {int(amount):,} ÷ {rateToUsd} = USD {int(result):,}',
        }

    elif from_currency == 'USD' and to_currency == 'IDR':
        if rateToUsd is None:
            return {'error': 'rateToUsd is required for USD → IDR conversion'}
        result = (amount * rateToUsd).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return {
            'original': str(amount),
            'converted': str(result),
            'from_currency': from_currency,
            'to_currency': to_currency,
            'rate_used': str(rateToUsd),
            'rate_type': 'rateToUsd',
            'note': f'USD {int(amount):,} × {rateToUsd} = IDR {int(result):,}',
        }

    elif from_currency == 'RMB' and to_currency == 'USD':
        if rateToUsd is not None:
            # Use IDR as bridge: RMB → IDR → USD
            # IDR_per_RMB = rateToCny, USD_per_IDR = rateToUsd
            # USD = RMB × rateToCny ÷ rateToUsd
            # But we need rateToCny for this path
            if rateToCny is None:
                return {'error': 'Both rateToCny and rateToUsd needed for RMB → USD via IDR bridge'}
            result = (amount * rateToCny / rateToUsd).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            return {
                'original': str(amount),
                'converted': str(result),
                'from_currency': from_currency,
                'to_currency': to_currency,
                'rate_used': f'{rateToCny}/{rateToUsd}',
                'rate_type': 'rateToCny+rateToUsd',
                'note': f'RMB {int(amount):,} × {rateToCny} ÷ {rateToUsd} = USD {int(result):,}',
            }
        return {'error': 'rateToUsd is required for RMB → USD conversion'}

    elif from_currency == 'USD' and to_currency == 'RMB':
        if rateToUsd is not None and rateToCny is not None:
            # USD → IDR → RMB: USD × rateToUsd ÷ rateToCny
            result = (amount * rateToUsd / rateToCny).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            return {
                'original': str(amount),
                'converted': str(result),
                'from_currency': from_currency,
                'to_currency': to_currency,
                'rate_used': f'{rateToUsd}/{rateToCny}',
                'rate_type': 'rateToUsd+rateToCny',
                'note': f'USD {int(amount):,} × {rateToUsd} ÷ {rateToCny} = RMB {int(result):,}',
            }
        return {'error': 'Both rateToUsd and rateToCny needed for USD → RMB conversion'}

    else:
        return {'error': f'Unsupported conversion: {from_currency} → {to_currency}'}


def batch_convert(query_result_path: str, to_currency: str) -> list:
    """Batch convert all service prices from an API query result JSON.

    Reads the query result, extracts each service's price, currency, and rates,
    then converts to the target currency.
    """
    try:
        with open(query_result_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        return [{'error': f'Cannot read query result file: {exc}'}]

    services = data.get('services', [])
    if not services:
        return [{'error': 'No services found in query result'}]

    results = []
    for svc in services:
        price = svc.get('服务价格')
        from_currency = svc.get('服务币种')
        rateToCny_raw = svc.get('人民币兑换服务币种汇率')
        rateToUsd_raw = svc.get('美元兑换服务币种汇率')

        # Normalize currency codes
        currency_map = {'RMB': 'RMB', 'CNY': 'RMB', 'IDR': 'IDR', 'USD': 'USD'}
        from_norm = currency_map.get(from_currency, from_currency)

        if price is None or from_norm is None:
            results.append({
                'service': svc.get('服务名称', 'unknown'),
                'error': f'Missing price or currency: price={price}, currency={from_currency}',
            })
            continue

        # Parse price
        try:
            if isinstance(price, str):
                price_d = Decimal(price.replace(',', '').strip())
            else:
                price_d = Decimal(str(price))
        except Exception:
            results.append({
                'service': svc.get('服务名称', 'unknown'),
                'error': f'Cannot parse price: {price}',
            })
            continue

        # Parse rates
        rateToCny_d = None
        rateToUsd_d = None
        if rateToCny_raw is not None:
            try:
                rateToCny_d = Decimal(str(rateToCny_raw))
            except Exception:
                pass
        if rateToUsd_raw is not None:
            try:
                rateToUsd_d = Decimal(str(rateToUsd_raw))
            except Exception:
                pass

        conversion = convert_single(price_d, from_norm, to_currency, rateToCny_d, rateToUsd_d)
        conversion['service'] = svc.get('服务名称', 'unknown')
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
    symbol_map = {'RMB': '￥', 'IDR': 'Rp', 'USD': '$'}
    from_sym = symbol_map.get(from_c, '')
    to_sym = symbol_map.get(to_c, '')

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
                        choices=['IDR', 'RMB', 'USD'],
                        help='Source currency')
    parser.add_argument('--to', dest='to_currency', default=None,
                        choices=['IDR', 'RMB', 'USD'],
                        help='Target currency')
    parser.add_argument('--rateToCny', type=str, default=None,
                        help='Exchange rate: 1 IDR = rateToCny RMB (from API)')
    parser.add_argument('--rateToUsd', type=str, default=None,
                        help='Exchange rate: 1 IDR = rateToUsd USD (from API)')

    # Batch mode: convert all services from a query result
    parser.add_argument('--query-result', default=None,
                        help='Path to queried_services JSON for batch conversion')

    args = parser.parse_args()

    # Batch mode
    if args.query_result:
        if not args.to_currency:
            parser.error('--to is required with --query-result')
        results = batch_convert(args.query_result, args.to_currency)
        for r in results:
            print(format_output(r))

        # Output structured JSON for Agent consumption
        output = {'conversions': results, 'to_currency': args.to_currency}
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    # Single conversion mode
    if not args.amount or not args.from_currency or not args.to_currency:
        parser.error('--amount, --from, and --to are required for single conversion (or use --query-result)')

    # Parse amount
    try:
        amount_raw = args.amount.replace(',', '').strip()
        amount_d = Decimal(amount_raw)
    except Exception:
        print(f"❌ Cannot parse amount: {args.amount}", file=sys.stderr)
        sys.exit(1)

    # Parse rates
    rateToCny_d = None
    rateToUsd_d = None
    if args.rateToCny is not None:
        try:
            rateToCny_d = Decimal(args.rateToCny)
        except Exception:
            print(f"❌ Cannot parse rateToCny: {args.rateToCny}", file=sys.stderr)
            sys.exit(1)
    if args.rateToUsd is not None:
        try:
            rateToUsd_d = Decimal(args.rateToUsd)
        except Exception:
            print(f"❌ Cannot parse rateToUsd: {args.rateToUsd}", file=sys.stderr)
            sys.exit(1)

    result = convert_single(amount_d, args.from_currency, args.to_currency, rateToCny_d, rateToUsd_d)

    if 'error' in result:
        print(f"❌ {result['error']}", file=sys.stderr)
        sys.exit(1)

    # Human-readable output
    print(format_output(result))

    # Structured JSON output for Agent consumption
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == '__main__':
    main()
