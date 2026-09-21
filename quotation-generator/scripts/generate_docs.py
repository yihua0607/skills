#!/usr/bin/env python3
"""Generate or check all human-readable documents derived from entities.json."""
import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATORS = (
    'generate_entities_summary.py',
    'generate_bank_reference.py',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='Fail if any generated document is stale')
    args = parser.parse_args()
    extra = ['--check'] if args.check else []
    for script in GENERATORS:
        result = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, script), *extra])
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
