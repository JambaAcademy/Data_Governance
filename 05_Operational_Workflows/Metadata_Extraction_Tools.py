"""
Metadata_Extraction_Tools.py
==============================
Data Governance Implementation Resources / 05_Operational_Workflows

Purpose
-------
Extracts technical metadata from common data sources (relational
databases via SQLAlchemy, CSV/Parquet files, or a directory of files)
and formats it for loading into the data catalog (`data_assets` and
`data_elements` tables from Data_Catalog_Setup_Scripts.sql).

This implements the "Automation and Tooling" guidance in
Metadata_Management_Procedures.md — automated technical metadata
capture that a Technical Data Steward then reviews and verifies.

Dependencies: pandas, sqlalchemy (optional, only needed for DB extraction)
    pip install pandas sqlalchemy
"""

from __future__ import annotations

import os
import json
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Data structures matching the catalog schema
# ---------------------------------------------------------------------------

@dataclass
class ElementMetadata:
    column_name: str
    data_type: str
    nullable: Optional[bool] = None
    business_definition: str = ""          # left blank for steward enrichment
    is_pii: bool = False                   # heuristic flag; steward must confirm
    is_critical_data_element: bool = False


@dataclass
class AssetMetadata:
    asset_name: str
    asset_type: str
    schema_name: Optional[str]
    source_type: str
    extracted_at: str
    row_count_estimate: Optional[int]
    elements: list


# ---------------------------------------------------------------------------
# Heuristic PII detection (flags candidates; steward must confirm)
# ---------------------------------------------------------------------------

PII_NAME_HINTS = [
    "ssn", "social_security", "email", "phone", "address", "dob",
    "date_of_birth", "passport", "driver_license", "credit_card",
    "bank_account", "national_id", "first_name", "last_name", "full_name",
]


def looks_like_pii(column_name: str) -> bool:
    name = column_name.lower()
    return any(hint in name for hint in PII_NAME_HINTS)


# ---------------------------------------------------------------------------
# Extraction from a pandas DataFrame (covers CSV/Parquet/Excel inputs)
# ---------------------------------------------------------------------------

def extract_from_dataframe(df: pd.DataFrame, asset_name: str, source_type: str = "File") -> AssetMetadata:
    elements = []
    for col in df.columns:
        elements.append(ElementMetadata(
            column_name=col,
            data_type=str(df[col].dtype),
            nullable=bool(df[col].isna().any()),
            is_pii=looks_like_pii(col),
        ))
    return AssetMetadata(
        asset_name=asset_name,
        asset_type="File",
        schema_name=None,
        source_type=source_type,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        row_count_estimate=len(df),
        elements=[asdict(e) for e in elements],
    )


# ---------------------------------------------------------------------------
# Extraction from a relational database via SQLAlchemy
# ---------------------------------------------------------------------------

def extract_from_database(connection_string: str, schema: Optional[str] = None) -> list:
    """Returns a list of AssetMetadata, one per table/view in the schema.

    Requires: pip install sqlalchemy
    Example connection_string: 'postgresql://user:pass@host:5432/dbname'
    """
    from sqlalchemy import create_engine, inspect

    engine = create_engine(connection_string)
    inspector = inspect(engine)
    assets = []

    table_names = inspector.get_table_names(schema=schema)
    for table_name in table_names:
        columns = inspector.get_columns(table_name, schema=schema)
        elements = [
            ElementMetadata(
                column_name=col["name"],
                data_type=str(col["type"]),
                nullable=col.get("nullable", True),
                is_pii=looks_like_pii(col["name"]),
            )
            for col in columns
        ]
        assets.append(AssetMetadata(
            asset_name=table_name,
            asset_type="Table",
            schema_name=schema,
            source_type="Database",
            extracted_at=datetime.now(timezone.utc).isoformat(),
            row_count_estimate=None,  # populate via separate COUNT(*) job if needed
            elements=[asdict(e) for e in elements],
        ))

    return assets


# ---------------------------------------------------------------------------
# Extraction from a directory of flat files
# ---------------------------------------------------------------------------

def extract_from_directory(directory: str, extensions=(".csv", ".parquet")) -> list:
    """Scans a directory and profiles each matching file's schema."""
    assets = []
    for fname in sorted(os.listdir(directory)):
        if not fname.lower().endswith(extensions):
            continue
        fpath = os.path.join(directory, fname)
        try:
            if fname.lower().endswith(".csv"):
                df = pd.read_csv(fpath, nrows=1000)  # sample for schema inference
            else:
                df = pd.read_parquet(fpath)
            assets.append(extract_from_dataframe(df, asset_name=fname, source_type="File"))
        except Exception as exc:  # noqa: BLE001 - log and continue scanning
            print(f"Warning: could not extract metadata from {fname}: {exc}")
    return assets


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def to_catalog_load_csv(assets: list, output_path: str) -> None:
    """Flattens extracted metadata into a CSV matching data_elements columns,
    ready for review and bulk load into the catalog."""
    rows = []
    for asset in assets:
        for el in asset.elements if hasattr(asset, "elements") else asset["elements"]:
            asset_name = asset.asset_name if hasattr(asset, "asset_name") else asset["asset_name"]
            rows.append({
                "asset_name": asset_name,
                "column_name": el["column_name"] if isinstance(el, dict) else el.column_name,
                "data_type": el["data_type"] if isinstance(el, dict) else el.data_type,
                "is_pii_candidate": el["is_pii"] if isinstance(el, dict) else el.is_pii,
                "business_definition": "",  # to be filled by Business Data Steward
                "is_critical_data_element": False,
            })
    pd.DataFrame(rows).to_csv(output_path, index=False)


def to_json(assets: list, output_path: str) -> None:
    serializable = [asdict(a) if not isinstance(a, dict) else a for a in assets]
    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extract technical metadata for the data catalog.")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_file = sub.add_parser("file", help="Extract metadata from a single CSV/Parquet/Excel file")
    p_file.add_argument("path")
    p_file.add_argument("--output", default="metadata_extract.csv")

    p_dir = sub.add_parser("directory", help="Scan a directory of flat files")
    p_dir.add_argument("path")
    p_dir.add_argument("--output", default="metadata_extract.csv")

    p_db = sub.add_parser("database", help="Extract metadata from a database (requires sqlalchemy)")
    p_db.add_argument("connection_string")
    p_db.add_argument("--schema", default=None)
    p_db.add_argument("--output", default="metadata_extract.csv")

    args = parser.parse_args()

    if args.mode == "file":
        if args.path.endswith(".csv"):
            df = pd.read_csv(args.path)
        elif args.path.endswith(".parquet"):
            df = pd.read_parquet(args.path)
        else:
            df = pd.read_excel(args.path)
        assets = [extract_from_dataframe(df, asset_name=os.path.basename(args.path))]
    elif args.mode == "directory":
        assets = extract_from_directory(args.path)
    elif args.mode == "database":
        assets = extract_from_database(args.connection_string, schema=args.schema)
    else:
        raise ValueError("Unknown mode")

    to_catalog_load_csv(assets, args.output)
    print(f"Extracted metadata for {len(assets)} asset(s). Review file: {args.output}")
    print("Remember: business_definition and is_critical_data_element require Business Data Steward review before loading into the catalog.")


if __name__ == "__main__":
    main()
