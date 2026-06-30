"""
Data_Profiling_Scripts.py
==========================
Data Governance Implementation Resources / 05_Operational_Workflows

Purpose
-------
Automates statistical profiling of tabular data sources (CSV, database
tables via SQLAlchemy, or pandas-readable files) to surface structure,
distribution, and anomaly signals that inform Data Steward review and
seed entries in the data catalog (data_elements table).

Typical use: run on a schedule or ad hoc against a new or changed data
asset, then attach the resulting profile report to its catalog entry.

Dependencies: pandas, numpy
    pip install pandas numpy
"""

from __future__ import annotations

import json
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnProfile:
    column_name: str
    inferred_type: str
    row_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    distinct_pct: float
    is_likely_unique_key: bool
    sample_values: list
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    mean: Optional[float] = None
    std_dev: Optional[float] = None
    most_common: Optional[list] = None


@dataclass
class AssetProfile:
    asset_name: str
    profiled_at: str
    row_count: int
    column_count: int
    duplicate_row_count: int
    columns: list


# ---------------------------------------------------------------------------
# Core profiling logic
# ---------------------------------------------------------------------------

def profile_column(series: pd.Series) -> ColumnProfile:
    total = len(series)
    null_count = int(series.isna().sum())
    non_null = series.dropna()
    distinct_count = int(non_null.nunique())

    inferred_type = str(series.dtype)
    is_numeric = pd.api.types.is_numeric_dtype(series)
    is_datetime = pd.api.types.is_datetime64_any_dtype(series)

    profile = ColumnProfile(
        column_name=series.name,
        inferred_type=inferred_type,
        row_count=total,
        null_count=null_count,
        null_pct=round(100 * null_count / total, 2) if total else 0.0,
        distinct_count=distinct_count,
        distinct_pct=round(100 * distinct_count / total, 2) if total else 0.0,
        is_likely_unique_key=(distinct_count == total and total > 0 and null_count == 0),
        sample_values=non_null.head(5).astype(str).tolist(),
    )

    if is_numeric:
        profile.min_value = str(non_null.min()) if not non_null.empty else None
        profile.max_value = str(non_null.max()) if not non_null.empty else None
        profile.mean = round(float(non_null.mean()), 4) if not non_null.empty else None
        profile.std_dev = round(float(non_null.std()), 4) if len(non_null) > 1 else None
    elif is_datetime:
        profile.min_value = str(non_null.min()) if not non_null.empty else None
        profile.max_value = str(non_null.max()) if not non_null.empty else None
    else:
        if not non_null.empty:
            top = non_null.astype(str).value_counts().head(5)
            profile.most_common = [{"value": k, "count": int(v)} for k, v in top.items()]

    return profile


def profile_dataframe(df: pd.DataFrame, asset_name: str) -> AssetProfile:
    columns = [profile_column(df[col]) for col in df.columns]
    return AssetProfile(
        asset_name=asset_name,
        profiled_at=datetime.now(timezone.utc).isoformat(),
        row_count=len(df),
        column_count=len(df.columns),
        duplicate_row_count=int(df.duplicated().sum()),
        columns=[asdict(c) for c in columns],
    )


# ---------------------------------------------------------------------------
# Anomaly flags (lightweight heuristics for steward attention)
# ---------------------------------------------------------------------------

def flag_anomalies(profile: AssetProfile, high_null_threshold: float = 20.0) -> list:
    """Returns a list of human-readable flags worth a Data Steward's attention."""
    flags = []
    if profile.duplicate_row_count > 0:
        flags.append(f"{profile.duplicate_row_count} duplicate rows detected across the full record.")
    for col in profile.columns:
        if col["null_pct"] >= high_null_threshold:
            flags.append(f"Column '{col['column_name']}' is {col['null_pct']}% null — review completeness.")
        if col["distinct_count"] == 1 and col["null_count"] < col["row_count"]:
            flags.append(f"Column '{col['column_name']}' has a single distinct value — possible constant or unused field.")
        if col["is_likely_unique_key"]:
            flags.append(f"Column '{col['column_name']}' looks like a candidate unique key (100% distinct, no nulls).")
    return flags


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def profile_to_json(profile: AssetProfile, path: str) -> None:
    with open(path, "w") as f:
        json.dump(asdict(profile), f, indent=2, default=str)


def profile_summary_table(profile: AssetProfile) -> pd.DataFrame:
    """Flattened, human-readable summary suitable for review or catalog upload."""
    rows = []
    for col in profile.columns:
        rows.append({
            "column": col["column_name"],
            "type": col["inferred_type"],
            "null_pct": col["null_pct"],
            "distinct_pct": col["distinct_pct"],
            "candidate_key": col["is_likely_unique_key"],
            "sample": ", ".join(col["sample_values"][:3]),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Profile a tabular data asset for governance review.")
    parser.add_argument("input_path", help="Path to a CSV/Parquet/Excel file to profile")
    parser.add_argument("--asset-name", default=None, help="Asset name for the report (defaults to filename)")
    parser.add_argument("--output-json", default=None, help="Optional path to write a JSON profile report")
    args = parser.parse_args()

    if args.input_path.endswith(".csv"):
        df = pd.read_csv(args.input_path)
    elif args.input_path.endswith(".parquet"):
        df = pd.read_parquet(args.input_path)
    elif args.input_path.endswith((".xlsx", ".xls")):
        df = pd.read_excel(args.input_path)
    else:
        raise ValueError("Unsupported file type. Use CSV, Parquet, or Excel.")

    asset_name = args.asset_name or args.input_path.split("/")[-1]
    profile = profile_dataframe(df, asset_name)

    print(f"\nProfile: {profile.asset_name}")
    print(f"Rows: {profile.row_count} | Columns: {profile.column_count} | Duplicate rows: {profile.duplicate_row_count}\n")
    print(profile_summary_table(profile).to_string(index=False))

    flags = flag_anomalies(profile)
    if flags:
        print("\nFlags for Steward Review:")
        for f in flags:
            print(f" - {f}")

    if args.output_json:
        profile_to_json(profile, args.output_json)
        print(f"\nFull profile written to {args.output_json}")


if __name__ == "__main__":
    main()
