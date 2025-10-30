import os
import glob
import re
from datetime import datetime

def parse_table(file_path):
    rows = []
    header = None
    with open(file_path, 'r') as f:
        for line in f:
            if line.startswith('|') and not line.startswith('|---'):
                parts = [x.strip() for x in line.strip().split('|')[1:-1]]
                if not header:
                    header = parts
                else:
                    row = dict(zip(header, parts))
                    rows.append(row)
    return header, rows

def parse_merge_date(row):
    # Assumes column is named exactly "PR Merged Date" and format is DD-Mmm-YY
    date_str = row.get("PR Merged Date", "")
    try:
        return datetime.strptime(date_str, "%d-%b-%y")
    except Exception:
        return None

def main():
    folder = "extracted/worker/github"
    files = sorted(glob.glob(os.path.join(folder, "*.md")))
    all_rows = []
    header = None
    merge_dates = []

    print("File record counts:")
    for file in files:
        h, rows = parse_table(file)
        if header is None:
            header = h
        all_rows.extend(rows)
        print(f"{os.path.basename(file)}: {len(rows)} records")
        for row in rows:
            dt = parse_merge_date(row)
            if dt:
                merge_dates.append(dt)

    if not all_rows or not merge_dates:
        print("No records or merge dates found.")
        return

    # Sort all rows by merge date descending
    all_rows.sort(key=lambda r: parse_merge_date(r) or datetime.min, reverse=True)
    min_date = min(merge_dates).strftime("%Y%m%d")
    max_date = max(merge_dates).strftime("%Y%m%d")

    parent_folder = os.path.basename(os.path.dirname(folder))
    output_file = f"github-{parent_folder}-{min_date}-{max_date}.md"
    output_path = os.path.join(folder, output_file)

    with open(output_path, "w") as f:
        f.write("| " + " | ".join(header) + " |\n")
        f.write("|" + "|".join(["---"] * len(header)) + "|\n")
        for row in all_rows:
            f.write("| " + " | ".join(row.get(col, "") for col in header) + " |\n")

    print(f"\nMerged file: {output_file} ({len(all_rows)} records)")

if __name__ == "__main__":
    main()
