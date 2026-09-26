#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from contract_common import extract_quotation_fields, resolve_party_snapshot, split_contact


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract contract fields from a quotation DOCX")
    parser.add_argument("quotation")
    args = parser.parse_args()
    quotation = Path(args.quotation).resolve()
    party_b = resolve_party_snapshot(quotation)
    fields = extract_quotation_fields(quotation)
    contact_email_or_wechat, contact_phone = split_contact(fields["contact_info"])
    print(json.dumps({
        "entity": party_b["entity"],
        "party_b": party_b,
        "party_a": {
            "customer_name": fields["customer_name"],
            "representative": fields["contact_name"],
            "email_or_wechat": contact_email_or_wechat,
            "phone": contact_phone,
        },
        "contract_no": fields["contract_no"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
