#!/usr/bin/env python3
"""Fetch and normalize ShanhaiMap service data from the aiCode API.

Uses markdownify for robust HTML-to-Markdown conversion (replaces the
hand-written regex converter), and outputs structured JSON on failure
so the Agent can handle errors gracefully.

Usage:
  python3 scripts/fetch_services.py '交通影响分析-2072516766550704130' '儿童玩具-SNI认证-2072304344749555713'
"""
import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import urllib.error


ENDPOINT = "https://server.shanhaimap.com/apis/jeecg-app/app/product/aiCode/resolve"
MAX_RETRIES = 2
TIMEOUT_SECONDS = 15
AI_CODE_PATTERN = re.compile(r'^.+-\d{19}$')
PURE_CODE_PATTERN = re.compile(r'^\d{19}$')


# ====== HTML-to-Markdown converter (using markdownify) ======

def html_to_markdown(raw_html):
    """Convert HTML content to Markdown using markdownify library.

    Falls back to a simple regex-based cleaner if markdownify is unavailable.
    markdownify handles: headings, bold/italic, lists (with nesting),
    tables, paragraphs, line breaks, and all common HTML elements reliably.
    """
    if not raw_html:
        return ""

    # Fast path: if no HTML tags, just normalize whitespace
    if not re.search(r'<[^>]+>', raw_html):
        text = html.unescape(raw_html).replace('\xa0', ' ')
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        return text

    try:
        from markdownify import markdownify as md
        # Use markdownify with options that preserve semantic structure:
        # - Convert headings, bold, italic, lists, tables
        # - Strip tags that don't have Markdown equivalents
        result = md(
            raw_html,
            heading_style='ATX',          # # style headings
            bullets='-',                   # - for unordered lists
            strong_em_symbol='**',         # **bold**, *italic*
            strip=['img', 'script', 'style'],  # strip non-content tags
            newline_style='BACKSLASH',     # line breaks within cells
            code_language='',              # no language hint for code blocks
        )
        # Normalize whitespace: remove excessive blank lines
        result = re.sub(r'\n{3,}', '\n\n', result)
        # Remove trailing whitespace on each line
        result = '\n'.join(line.rstrip() for line in result.split('\n'))
        return result.strip()

    except ImportError:
        # Fallback: simple regex-based cleaner (no external dependency)
        print("⚠️  markdownify not available, using fallback HTML cleaner", file=sys.stderr)
        return _html_to_markdown_fallback(raw_html)


def _html_to_markdown_fallback(raw_html):
    """Fallback HTML-to-Markdown converter when markdownify is unavailable.

    Handles: headings, bold/italic, line breaks, paragraph breaks.
    Does NOT handle: nested lists, complex tables, strike/underline.
    """
    if not raw_html:
        return ""

    text = raw_html

    # Headings
    for level in range(1, 7):
        prefix = '#' * level
        text = re.sub(rf'</\s*h{level}\s*>', '\n\n', text, flags=re.I)
        text = re.sub(rf'<\s*h{level}\s*(?:[^>]*)?\s*>', f'\n{prefix} ', text, flags=re.I)

    # Bold: <strong>, <b> → **text**
    text = re.sub(r'<\s*(strong|b)\s*(?:[^>]*)?\s*>(.*?)</\s*(strong|b)\s*>',
                  r'**\2**', text, flags=re.I)

    # Italic: <em>, <i> → *text*
    text = re.sub(r'<\s*(em|i)\s*(?:[^>]*)?\s*>(.*?)</\s*(em|i)\s*>',
                  r'*\2*', text, flags=re.I)

    # <br> → newline
    text = re.sub(r'<\s*br\s*/?\s*>', '\n', text, flags=re.I)

    # <p>, <div>, <section> → paragraph break
    text = re.sub(r'</\s*(p|div|section)\s*>', '\n\n', text, flags=re.I)
    text = re.sub(r'<\s*(p|div|section)\s*(?:[^>]*)?\s*>', '', text, flags=re.I)

    # Strip all remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode entities and normalize
    text = html.unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def clean_detail(value):
    """Clean HTML detail content, preserving semantic structure as Markdown."""
    text = value or ""
    if not re.search(r'<[^>]+>', text):
        text = html.unescape(text).replace('\xa0', ' ')
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        return text
    return html_to_markdown(text)


def parse_ai_code_arg(value):
    """Return the API aiCode exactly as provided, e.g. 服务名-19位编码."""
    raw = str(value).strip()
    if not raw:
        raise ValueError(f'Cannot parse aiCode from input: {value}. Expected non-empty 服务名-19位编码.')
    if PURE_CODE_PATTERN.fullmatch(raw):
        raise ValueError(f'不支持纯19位数字编码查询: {raw}. 请提供完整的 服务名-19位编码。')
    if not AI_CODE_PATTERN.fullmatch(raw):
        raise ValueError(f'Cannot parse aiCode from input: {value}. Expected 服务名-19位编码。')
    return raw


def request_services(ai_codes):
    """POST to the resolve endpoint with retry logic.

    Returns a dict with:
      - success: bool (True if any data was retrieved)
      - partial_failure: bool (True if some aiCodes failed)
      - services: list of normalized records
      - errors: list of error descriptions (if any failures)

    On complete network failure, outputs structured error JSON to stdout
    before exiting, so the Agent can parse it programmatically.
    """
    body = json.dumps({"aiCodes": ",".join(ai_codes)}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json; charset=UTF-8"},
        method="POST",
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("success") is False:
                    api_msg = payload.get("message") or "unknown error"
                    print(f"⚠️  API returned failure: {api_msg}", file=sys.stderr)
                return payload
        except urllib.error.URLError as exc:
            last_error = exc
            reason = exc.reason if hasattr(exc, 'reason') else str(exc)
            print(f"⚠️  Request attempt {attempt}/{MAX_RETRIES} failed: {reason}", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            last_error = exc
            print(f"⚠️  Request attempt {attempt}/{MAX_RETRIES} failed: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        except Exception as exc:
            last_error = exc
            print(f"⚠️  Request attempt {attempt}/{MAX_RETRIES} failed: {exc}", file=sys.stderr)

        # Brief pause before retry
        if attempt < MAX_RETRIES:
            import time
            time.sleep(1)

    # All retries exhausted — output structured error JSON for Agent consumption.
    # The upstream API itself does not provide an errors field; this is our
    # normalized wrapper so agents can surface the message consistently.
    error_output = {
        "success": False,
        "code": -1,
        "message": "无法获取服务详情，请检查网络",
        "error_type": "network_timeout",
        "last_error": str(last_error) if last_error else "unknown",
        "services": [],
        "errors": [{
            "aiCode": None,
            "message": "无法获取服务详情，请检查网络",
        }],
    }
    json.dump(error_output, sys.stdout, ensure_ascii=False, indent=2)
    print()
    sys.exit(1)


def iter_service_records(payload):
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        for item in result["items"]:
            data = item.get("data") or {}
            yield item.get("aiCode"), item.get("success"), item.get("message"), data
    elif isinstance(result, dict):
        yield result.get("aiCode"), payload.get("success"), payload.get("message"), result


def normalize_record(query_ai_code, ok, message, record, original_input=None):
    execute_unit = record.get("executeUnit") or {}
    usd_rate = record.get("rateToUsd")
    if usd_rate is None:
        usd_rate = record.get("usdRate")
    return {
        "原始输入": original_input,
        "查询aiCode": query_ai_code,
        "查询成功": ok,
        "查询消息": message,
        "服务名称": record.get("productName"),
        "服务编码": record.get("productCode"),
        "服务币种": record.get("currencyCode"),
        "服务价格": record.get("totalPrice"),
        "人民币兑换服务币种汇率": record.get("rateToCny"),
        "美元兑换服务币种汇率": usd_rate,
        "服务数量": execute_unit.get("quantity"),
        "服务单位": execute_unit.get("unitName"),
        "服务内容": clean_detail(record.get("detail")),
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch and normalize ShanhaiMap service data.")
    parser.add_argument("ai_code", nargs="+", help="One or more values like 服务名-19位编码.")
    args = parser.parse_args()

    try:
        parsed_ai_codes = [parse_ai_code_arg(value) for value in args.ai_code]
    except ValueError as exc:
        output = {
            "success": False,
            "code": -2,
            "message": str(exc),
            "services": [],
            "errors": [{"aiCode": None, "message": str(exc)}],
        }
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        print()
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)

    original_by_code = {}
    for parsed, original in zip(parsed_ai_codes, args.ai_code):
        original_by_code[parsed] = original
        suffix = parsed.rsplit('-', 1)[-1]
        original_by_code[suffix] = original
    payload = request_services(parsed_ai_codes)

    # Check for partial failures
    services = [
        normalize_record(query_ai_code, ok, message, record, original_by_code.get(str(query_ai_code)))
        for query_ai_code, ok, message, record in iter_service_records(payload)
    ]

    failed_services = [s for s in services if s.get("查询成功") is not True]
    has_item_failure = bool(failed_services)
    top_level_failure = payload.get("success") is False
    no_services = not services
    should_abort = top_level_failure or has_item_failure or no_services
    errors = [
        {
            "aiCode": fs.get("查询aiCode"),
            "message": fs.get("查询消息") or "未知错误",
        }
        for fs in failed_services
    ]

    if failed_services:
        print(f"❌ {len(failed_services)}/{len(services)} 个服务查询失败，已中断报价单生成:", file=sys.stderr)
        for fs in failed_services:
            print(f"  - {fs.get('查询aiCode', '?')}: {fs.get('查询消息', '未知错误')}", file=sys.stderr)

    if no_services:
        no_services_message = payload.get("message") or "未查询到任何服务"
        print(f"❌ 未查询到任何服务，已中断报价单生成: {no_services_message}", file=sys.stderr)
        errors.append({"aiCode": None, "message": no_services_message})

    output = {
        "success": False if should_abort else payload.get("success"),
        "code": payload.get("code"),
        "message": payload.get("message") or ("未查询到任何服务" if no_services else None),
        "partial_failure": has_item_failure,
        "services": services,
        "errors": errors,
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()

    if top_level_failure:
        api_message = payload.get("message") or "未知错误"
        print(f"❌ API message: {api_message}", file=sys.stderr)
        sys.exit(1)

    if has_item_failure or no_services:
        sys.exit(1)


if __name__ == "__main__":
    main()
