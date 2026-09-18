#!/usr/bin/env python3
"""Resolve a human-entered signing-entity name using config aliases."""
import argparse
import json
import os
import sys

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_DIR not in sys.path:
    sys.path.insert(0, _SKILL_DIR)

from scripts.quotation_common import resolve_entity_alias


def main():
    parser = argparse.ArgumentParser(description='Resolve a signing entity alias.')
    parser.add_argument('query', help='Entity name or phrase mentioning it')
    args = parser.parse_args()
    result = resolve_entity_alias(args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'matched' else 2


if __name__ == '__main__':
    sys.exit(main())
