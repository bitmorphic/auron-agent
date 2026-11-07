#!/usr/bin/env python3
"""
Independently validate sales_clean.csv numeric findings.
Computes category revenue totals from raw CSV without relying on model output.
"""
import sys
import csv
from pathlib import Path
from collections import defaultdict

DATA_PATH = Path("/home/auron/auron/data/samples/sales_clean.csv")


def main():
    print("=== Independent Dataset Validation ===")
    print(f"File: {DATA_PATH}")

    if not DATA_PATH.exists():
        print(f"ERROR: File not found: {DATA_PATH}")
        sys.exit(1)

    print(f"File size: {DATA_PATH.stat().st_size:,} bytes")

    revenue_by_category = defaultdict(float)
    row_count = 0
    header = None
    category_col = None
    revenue_col = None
    errors = 0

    with open(DATA_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        print(f"Columns: {header}")

        # Identify columns (case-insensitive)
        col_map = {c.lower(): c for c in header}
        category_col = col_map.get("category") or col_map.get("product_category")
        revenue_col = col_map.get("revenue") or col_map.get("total_revenue") or col_map.get("sales")

        if not category_col:
            print(f"ERROR: No 'category' column found. Columns: {header}")
            sys.exit(1)
        if not revenue_col:
            print(f"ERROR: No 'revenue' column found. Columns: {header}")
            sys.exit(1)

        print(f"Using category column: {category_col!r}")
        print(f"Using revenue column:  {revenue_col!r}")
        print()

        for row in reader:
            row_count += 1
            try:
                cat = row[category_col].strip()
                rev = float(row[revenue_col])
                revenue_by_category[cat] += rev
            except (ValueError, KeyError) as e:
                errors += 1

    print(f"Total rows processed: {row_count:,}")
    print(f"Parse errors:         {errors}")
    print()

    # Sort by total revenue descending
    ranked = sorted(revenue_by_category.items(), key=lambda x: x[1], reverse=True)

    print("Revenue by Category (ranked highest to lowest):")
    total_revenue = sum(v for _, v in ranked)
    for rank, (cat, rev) in enumerate(ranked, start=1):
        pct = (rev / total_revenue * 100) if total_revenue > 0 else 0
        print(f"  #{rank:2d}  {cat:<30s}  ${rev:>12,.2f}  ({pct:.1f}%)")

    print()
    print(f"Grand total revenue: ${total_revenue:>12,.2f}")
    print()

    if ranked:
        winner = ranked[0]
        print(f"CONCLUSION: Highest-revenue category = '{winner[0]}' with ${winner[1]:,.2f}")
    print()
    print("=== Validation Complete ===")


if __name__ == "__main__":
    main()

