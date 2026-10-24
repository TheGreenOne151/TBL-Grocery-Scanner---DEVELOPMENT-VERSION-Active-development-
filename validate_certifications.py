#!/usr/bin/env python3
"""
Validate the certification CSV/XLSX before committing.

Run this before every Leaping Bunny re-sync commit. It reads the file,
flags any row that looks like it went through a lossy encoding pass,
and exits non-zero if anything suspicious is found.

Usage:
    python validate_certifications.py                          # validates the .xlsx
    python validate_certifications.py --csv                    # validates the .csv
    python validate_certifications.py --file path/to/file      # validates a specific path
    python validate_certifications.py --strict                 # also rejects non-ASCII

Exit codes:
    0  clean
    1  suspicious rows found
    2  file not found
"""
import argparse
import csv
import re
import sys
from pathlib import Path

# Patterns that always indicate an encoding problem in a brand name.
# Order matters for the report: earlier patterns are more specific.
SUSPICIOUS_PATTERNS = [
    ("U+FFFD replacement char", re.compile(r"\uFFFD")),
    ("'?' adjacent to letter/digit", re.compile(r"[A-Za-z0-9]\?|\?[A-Za-z0-9]")),
    ("run of two or more '?'", re.compile(r"\?\?")),
    ("leading '?'", re.compile(r"^\s*\?")),
    ("trailing '?'", re.compile(r"\?\s*$")),
    ("control character", re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")),
]

# Columns to check. Brand is the critical one; the others are where
# secondary corruption shows up (e.g. 'Coffee iced' becoming 'Coffee ?ced').
COLUMNS_TO_CHECK = ("Product_Brand", "Category", "Notes",
                    "Certification_Categories", "Confidence")


def load_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_xlsx(path: Path):
    import pandas as pd
    df = pd.read_excel(path)
    return df.to_dict(orient="records")


def find_suspicious(rows):
    """Yield (row_index, column, value, pattern_name) tuples."""
    for i, row in enumerate(rows, start=1):
        for col in COLUMNS_TO_CHECK:
            val = row.get(col)
            if val is None:
                continue
            text = str(val)
            for name, pattern in SUSPICIOUS_PATTERNS:
                if pattern.search(text):
                    yield i, col, text, name
                    break  # one report per cell is enough


def roundtrip_ok(text: str) -> bool:
    """True if the string survives a UTF-8 encode/decode round-trip unchanged.

    This catches subtler losses than the regex checks: if any character
    was represented as a surrogate or replacement during ingest, the
    round-trip produces different bytes.
    """
    try:
        return text.encode("utf-8").decode("utf-8") == text
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=None,
                        help="path to a CSV or XLSX file")
    parser.add_argument("--csv", action="store_true",
                        help="validate the .csv instead of the .xlsx")
    parser.add_argument("--strict", action="store_true",
                        help="also reject any non-ASCII character (use only "
                             "if your ingest is supposed to be pure ASCII)")
    args = parser.parse_args()

    base = Path(__file__).parent
    if args.file:
        path = args.file
    elif args.csv:
        path = base / "comprehensive_grocery_certifications_COMPLETE_FINAL.csv"
    else:
        path = base / "comprehensive_grocery_certifications_COMPLETE.xlsx"

    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2

    rows = load_csv(path) if path.suffix.lower() == ".csv" else load_xlsx(path)

    print(f"Checking {len(rows)} rows in {path.name}...")

    hits = list(find_suspicious(rows))

    # Round-trip check on Product_Brand only (that's the column that
    # matters for lookups; other columns are diagnostic).
    rt_failures = []
    for i, row in enumerate(rows, start=1):
        brand = row.get("Product_Brand")
        if brand is not None and not roundtrip_ok(str(brand)):
            rt_failures.append((i, str(brand)))

    # Optional strict mode: reject any non-ASCII character at all.
    non_ascii = []
    if args.strict:
        for i, row in enumerate(rows, start=1):
            brand = row.get("Product_Brand")
            if brand is None:
                continue
            text = str(brand)
            if any(ord(c) > 127 for c in text):
                non_ascii.append((i, text))

    if hits:
        print(f"\n{len(hits)} suspicious cell(s) found:\n")
        for row_idx, col, val, reason in hits:
            print(f"  row {row_idx:>5}  {col}: {val!r}  ->  {reason}")

    if rt_failures:
        print(f"\n{len(rt_failures)} Product_Brand value(s) failed UTF-8 round-trip:\n")
        for row_idx, val in rt_failures:
            print(f"  row {row_idx:>5}  {val!r}")

    if non_ascii:
        print(f"\n{len(non_ascii)} Product_Brand value(s) contain non-ASCII "
              f"(strict mode):\n")
        for row_idx, val in non_ascii:
            print(f"  row {row_idx:>5}  {val!r}")

    if hits or rt_failures or non_ascii:
        print("\nFAIL — fix these rows before committing.")
        return 1

    print("\nOK — no suspicious encoding artifacts found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
