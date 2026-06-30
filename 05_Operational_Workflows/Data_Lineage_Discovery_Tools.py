"""
Data_Lineage_Discovery_Tools.py
=================================
Data Governance Implementation Resources / 05_Operational_Workflows

Purpose
-------
Auto-discovers data lineage relationships by parsing SQL transformation
logic (e.g., ETL/ELT scripts, dbt models, stored procedures) to identify
source -> target table references. Produces candidate lineage records
matching the `data_lineage` table created by Data_Catalog_Setup_Scripts.sql,
for a Technical Data Steward to review and mark "Verified" per the
Metadata Management Procedures.

This is a lightweight, dependency-free static-analysis approach. It is
not a substitute for full SQL parsing in complex dialects, but covers
the common patterns found in most ETL/ELT and dbt-style SQL.

Dependencies: pandas (only needed for tabular export)
    pip install pandas
"""

from __future__ import annotations

import os
import re
import json
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

try:
    import pandas as pd
except ImportError:  # pandas is optional; only needed for to_csv export
    pd = None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LineageCandidate:
    source_asset: str
    target_asset: str
    process_name: str
    transformation_summary: str
    captured_method: str = "Automated"
    confidence: str = "Medium"  # Low / Medium / High, based on pattern match strength
    discovered_at: str = ""


# ---------------------------------------------------------------------------
# SQL pattern matching
# ---------------------------------------------------------------------------

# Matches: CREATE TABLE target AS SELECT ... FROM source [JOIN source2 ...]
CTAS_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+([a-zA-Z0-9_.\"]+)\s+AS\s+(.*?)(?=;|$)",
    re.IGNORECASE | re.DOTALL,
)

# Matches: INSERT INTO target ... SELECT ... FROM source
INSERT_SELECT_PATTERN = re.compile(
    r"INSERT\s+INTO\s+([a-zA-Z0-9_.\"]+).*?SELECT.*?FROM\s+(.*?)(?=;|$)",
    re.IGNORECASE | re.DOTALL,
)

# Matches: MERGE INTO target USING source
MERGE_PATTERN = re.compile(
    r"MERGE\s+INTO\s+([a-zA-Z0-9_.\"]+)\s+(?:AS\s+\w+\s+)?USING\s+([a-zA-Z0-9_.\"]+)",
    re.IGNORECASE,
)

# Extracts table references following FROM or JOIN within a SQL fragment
TABLE_REF_PATTERN = re.compile(r"(?:FROM|JOIN)\s+([a-zA-Z0-9_.\"]+)", re.IGNORECASE)


def _clean(name: str) -> str:
    return name.strip().strip('"').strip()


def extract_source_tables(sql_fragment: str) -> list:
    return list({_clean(m) for m in TABLE_REF_PATTERN.findall(sql_fragment)})


def discover_lineage_in_sql(sql_text: str, process_name: str) -> list:
    """Parses a single SQL script's text and returns candidate lineage records."""
    candidates = []
    discovered_at = datetime.now(timezone.utc).isoformat()

    for match in CTAS_PATTERN.finditer(sql_text):
        target = _clean(match.group(1))
        body = match.group(2)
        sources = extract_source_tables(body)
        for src in sources:
            if src.lower() == target.lower():
                continue
            candidates.append(LineageCandidate(
                source_asset=src, target_asset=target, process_name=process_name,
                transformation_summary="CREATE TABLE/VIEW ... AS SELECT",
                confidence="High", discovered_at=discovered_at,
            ))

    for match in INSERT_SELECT_PATTERN.finditer(sql_text):
        target = _clean(match.group(1))
        body = match.group(2)
        sources = extract_source_tables("FROM " + body)  # ensure leading FROM for regex
        for src in sources:
            if src.lower() == target.lower():
                continue
            candidates.append(LineageCandidate(
                source_asset=src, target_asset=target, process_name=process_name,
                transformation_summary="INSERT INTO ... SELECT FROM",
                confidence="High", discovered_at=discovered_at,
            ))

    for match in MERGE_PATTERN.finditer(sql_text):
        target = _clean(match.group(1))
        source = _clean(match.group(2))
        candidates.append(LineageCandidate(
            source_asset=source, target_asset=target, process_name=process_name,
            transformation_summary="MERGE INTO ... USING",
            confidence="High", discovered_at=discovered_at,
        ))

    return candidates


def discover_lineage_in_directory(directory: str, extensions=(".sql",)) -> list:
    """Scans a directory of SQL/ETL scripts and aggregates lineage candidates."""
    all_candidates = []
    for root, _, files in os.walk(directory):
        for fname in files:
            if not fname.lower().endswith(extensions):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    sql_text = f.read()
                candidates = discover_lineage_in_sql(sql_text, process_name=fname)
                all_candidates.extend(candidates)
            except Exception as exc:  # noqa: BLE001 - log and continue scanning
                print(f"Warning: could not parse {fpath}: {exc}")
    return all_candidates


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(candidates: list) -> list:
    seen = {}
    for c in candidates:
        key = (c.source_asset.lower(), c.target_asset.lower(), c.process_name)
        if key not in seen:
            seen[key] = c
    return list(seen.values())


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def to_csv(candidates: list, output_path: str) -> None:
    if pd is None:
        raise RuntimeError("pandas is required for CSV export. pip install pandas")
    df = pd.DataFrame([asdict(c) for c in candidates])
    df.to_csv(output_path, index=False)


def to_json(candidates: list, output_path: str) -> None:
    with open(output_path, "w") as f:
        json.dump([asdict(c) for c in candidates], f, indent=2)


def to_sql_inserts(candidates: list, output_path: str) -> None:
    """Generates INSERT statements against the data_lineage table.

    NOTE: source_asset_id / target_asset_id must already exist in
    data_assets; this generates lookups by asset_name as a starting
    point for a Technical Data Steward to adapt and verify.
    """
    lines = ["SET search_path TO data_governance;", ""]
    for c in candidates:
        lines.append(
            "INSERT INTO data_lineage (source_asset_id, target_asset_id, transformation_summary, "
            "process_name, captured_method)\n"
            "SELECT src.asset_id, tgt.asset_id, "
            f"'{c.transformation_summary}', '{c.process_name}', 'Automated'\n"
            f"FROM data_assets src, data_assets tgt\n"
            f"WHERE src.asset_name = '{c.source_asset}' AND tgt.asset_name = '{c.target_asset}';"
        )
    with open(output_path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Discover candidate data lineage from SQL/ETL scripts.")
    parser.add_argument("path", help="SQL file or directory of SQL/ETL scripts to scan")
    parser.add_argument("--format", choices=["csv", "json", "sql"], default="csv")
    parser.add_argument("--output", default="lineage_candidates.csv")
    args = parser.parse_args()

    if os.path.isdir(args.path):
        candidates = discover_lineage_in_directory(args.path)
    else:
        with open(args.path, "r", errors="ignore") as f:
            sql_text = f.read()
        candidates = discover_lineage_in_sql(sql_text, process_name=os.path.basename(args.path))

    candidates = deduplicate(candidates)

    if args.format == "csv":
        to_csv(candidates, args.output)
    elif args.format == "json":
        to_json(candidates, args.output)
    else:
        to_sql_inserts(candidates, args.output)

    print(f"Discovered {len(candidates)} candidate lineage relationship(s). Written to {args.output}")
    print("All automated discoveries require Technical Data Steward review before being marked Verified in the catalog.")


if __name__ == "__main__":
    main()
