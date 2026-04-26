#!/usr/bin/env python3
"""
MARICHI Validator — Zero-Loss Verification

Usage:
    python validate.py <original_file> <received_file>

Checks:
  • File sizes match
  • SHA-256 of entire file
  • SHA-256 per 1 MB block (identify which blocks are corrupted)
  • Byte-by-byte diff → exact offset of first error
  • Bit-level error count
  • Final verdict: PERFECT / DEGRADED / SIZE_MISMATCH / CORRUPT

Exit codes:
  0 = PERFECT (zero data loss)
  1 = any difference found
"""

import argparse
import sys
from marichi.validator import Validator


def main():
    parser = argparse.ArgumentParser(
        description="MARICHI — Zero-Loss Data Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("original", help="Original (source) file")
    parser.add_argument("received", help="Received (decoded) file")
    args = parser.parse_args()

    v = Validator(args.original, args.received)
    report = v.run()

    sys.exit(0 if report.verdict == "PERFECT" else 1)


if __name__ == "__main__":
    main()
