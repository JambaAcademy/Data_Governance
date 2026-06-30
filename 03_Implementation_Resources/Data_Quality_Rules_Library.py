"""
Data_Quality_Rules_Library.py
==============================
Data Governance Implementation Resources / 03_Implementation_Resources

Purpose
-------
A reusable library of common data quality rule checks aligned to the
six dimensions defined in the Data Quality Standards (Completeness,
Validity, Uniqueness, Consistency, Timeliness, Accuracy). Designed to
run against pandas DataFrames so it can be plugged into notebooks,
batch jobs, or orchestration tools (Airflow, dbt Python models, etc.).

Each rule function returns a `RuleResult` with enough detail to log
directly into the `data_quality_results` table created by
Data_Catalog_Setup_Scripts.sql.

Dependencies: pandas, numpy
    pip install pandas numpy
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass
class RuleResult:
    rule_name: str
    dimension: str
    column: Optional[str]
    records_evaluated: int
    records_failed: int
    status: str = field(init=False)
    pass_rate: float = field(init=False)
    sample_failures: list = field(default_factory=list)
    run_timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self):
        if self.records_evaluated == 0:
            self.pass_rate = 100.0
        else:
            self.pass_rate = round(
                100.0 * (self.records_evaluated - self.records_failed) / self.records_evaluated, 2
            )
        if self.records_failed == 0:
            self.status = "Pass"
        elif self.pass_rate >= 95:
            self.status = "Warning"
        else:
            self.status = "Fail"

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "dimension": self.dimension,
            "column": self.column,
            "records_evaluated": self.records_evaluated,
            "records_failed": self.records_failed,
            "pass_rate": self.pass_rate,
            "status": self.status,
            "run_timestamp": self.run_timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# 1. Completeness
# ---------------------------------------------------------------------------

def check_completeness(df: pd.DataFrame, column: str, threshold: float = 100.0) -> RuleResult:
    """Flags null/empty values in a column.

    threshold: minimum acceptable % populated (informational; status still
    derives from pass_rate via RuleResult).
    """
    total = len(df)
    is_missing = df[column].isna() | (df[column].astype(str).str.strip() == "")
    failed = int(is_missing.sum())
    return RuleResult(
        rule_name=f"completeness_{column}",
        dimension="Completeness",
        column=column,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[is_missing].head(5).index.tolist(),
    )


def check_required_columns_present(df: pd.DataFrame, required_columns: Iterable[str]) -> RuleResult:
    """Confirms a dataset contains all expected columns (schema completeness)."""
    missing_cols = [c for c in required_columns if c not in df.columns]
    return RuleResult(
        rule_name="required_columns_present",
        dimension="Completeness",
        column=None,
        records_evaluated=len(list(required_columns)),
        records_failed=len(missing_cols),
        sample_failures=missing_cols,
    )


# ---------------------------------------------------------------------------
# 2. Validity
# ---------------------------------------------------------------------------

def check_regex_format(df: pd.DataFrame, column: str, pattern: str) -> RuleResult:
    """Validates values against a regex pattern (e.g. email, phone, postal code)."""
    total = len(df)
    compiled = re.compile(pattern)
    values = df[column].astype(str)
    is_valid = values.apply(lambda v: bool(compiled.match(v)) if v not in ("nan", "None", "") else False)
    failed = int((~is_valid).sum())
    return RuleResult(
        rule_name=f"validity_format_{column}",
        dimension="Validity",
        column=column,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[~is_valid, column].head(5).tolist(),
    )


def check_value_in_set(df: pd.DataFrame, column: str, allowed_values: Iterable) -> RuleResult:
    """Validates that values fall within an approved domain (reference data check)."""
    total = len(df)
    allowed = set(allowed_values)
    is_valid = df[column].isin(allowed)
    failed = int((~is_valid).sum())
    return RuleResult(
        rule_name=f"validity_domain_{column}",
        dimension="Validity",
        column=column,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[~is_valid, column].dropna().unique().tolist()[:5],
    )


def check_numeric_range(df: pd.DataFrame, column: str, min_value=None, max_value=None) -> RuleResult:
    """Validates numeric values fall within an acceptable range."""
    total = len(df)
    series = pd.to_numeric(df[column], errors="coerce")
    in_range = pd.Series(True, index=df.index)
    if min_value is not None:
        in_range &= series >= min_value
    if max_value is not None:
        in_range &= series <= max_value
    in_range &= series.notna()
    failed = int((~in_range).sum())
    return RuleResult(
        rule_name=f"validity_range_{column}",
        dimension="Validity",
        column=column,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[~in_range, column].head(5).tolist(),
    )


# Common patterns for reuse with check_regex_format
COMMON_PATTERNS = {
    "email": r"^[\w\.\-+]+@[\w\-]+\.[a-zA-Z]{2,}$",
    "us_zip": r"^\d{5}(-\d{4})?$",
    "us_phone": r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$",
    "iso_date": r"^\d{4}-\d{2}-\d{2}$",
}


# ---------------------------------------------------------------------------
# 3. Uniqueness
# ---------------------------------------------------------------------------

def check_uniqueness(df: pd.DataFrame, columns: Iterable[str]) -> RuleResult:
    """Flags duplicate records based on one or more key columns."""
    cols = list(columns)
    total = len(df)
    dup_mask = df.duplicated(subset=cols, keep=False)
    failed = int(dup_mask.sum())
    return RuleResult(
        rule_name=f"uniqueness_{'_'.join(cols)}",
        dimension="Uniqueness",
        column=", ".join(cols),
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[dup_mask, cols].drop_duplicates().head(5).to_dict("records"),
    )


# ---------------------------------------------------------------------------
# 4. Consistency
# ---------------------------------------------------------------------------

def check_cross_field_consistency(
    df: pd.DataFrame, condition: Callable[[pd.DataFrame], pd.Series], rule_name: str
) -> RuleResult:
    """Generic cross-field business rule check.

    `condition` should return a boolean Series that is True where the
    record PASSES the rule (e.g. df['end_date'] >= df['start_date']).
    """
    total = len(df)
    passes = condition(df)
    failed = int((~passes).sum())
    return RuleResult(
        rule_name=rule_name,
        dimension="Consistency",
        column=None,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[~passes].head(5).index.tolist(),
    )


def check_referential_integrity(
    df: pd.DataFrame, column: str, reference_values: Iterable
) -> RuleResult:
    """Confirms foreign-key-style values exist in a reference/parent dataset."""
    total = len(df)
    ref_set = set(reference_values)
    is_valid = df[column].isin(ref_set) | df[column].isna()
    failed = int((~is_valid).sum())
    return RuleResult(
        rule_name=f"referential_integrity_{column}",
        dimension="Consistency",
        column=column,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[~is_valid, column].dropna().unique().tolist()[:5],
    )


# ---------------------------------------------------------------------------
# 5. Timeliness
# ---------------------------------------------------------------------------

def check_freshness(df: pd.DataFrame, date_column: str, max_age_hours: float) -> RuleResult:
    """Flags records whose timestamp is older than the allowed SLA."""
    total = len(df)
    ts = pd.to_datetime(df[date_column], errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    is_fresh = ts >= cutoff
    failed = int((~is_fresh).sum())
    return RuleResult(
        rule_name=f"timeliness_{date_column}",
        dimension="Timeliness",
        column=date_column,
        records_evaluated=total,
        records_failed=failed,
        sample_failures=df.loc[~is_fresh, date_column].head(5).astype(str).tolist(),
    )


# ---------------------------------------------------------------------------
# 6. Accuracy
# ---------------------------------------------------------------------------

def check_accuracy_against_reference(
    df: pd.DataFrame, df_key: str, ref_df: pd.DataFrame, ref_key: str, compare_columns: Iterable[str]
) -> RuleResult:
    """Compares values against a trusted reference/source-of-truth dataset.

    Joins df to ref_df on the key columns and flags rows where any of the
    compare_columns differ.
    """
    merged = df.merge(ref_df, left_on=df_key, right_on=ref_key, suffixes=("", "_ref"))
    total = len(merged)
    mismatch = pd.Series(False, index=merged.index)
    for col in compare_columns:
        ref_col = f"{col}_ref" if f"{col}_ref" in merged.columns else col
        mismatch |= merged[col] != merged[ref_col]
    failed = int(mismatch.sum())
    return RuleResult(
        rule_name=f"accuracy_vs_reference_{df_key}",
        dimension="Accuracy",
        column=", ".join(compare_columns),
        records_evaluated=total,
        records_failed=failed,
        sample_failures=merged.loc[mismatch, [df_key, *compare_columns]].head(5).to_dict("records"),
    )


# ---------------------------------------------------------------------------
# Rule runner / scorecard
# ---------------------------------------------------------------------------

def run_rule_suite(rules: Iterable[Callable[[], RuleResult]]) -> pd.DataFrame:
    """Executes a list of zero-arg callables (use functools.partial to bind
    args to the check_* functions) and returns a tidy scorecard DataFrame.

    Example
    -------
    from functools import partial

    suite = [
        partial(check_completeness, df, "customer_id"),
        partial(check_regex_format, df, "email", COMMON_PATTERNS["email"]),
        partial(check_uniqueness, df, ["customer_id"]),
    ]
    scorecard = run_rule_suite(suite)
    """
    results = [r() if callable(r) else r for r in rules]
    rows = [r.to_dict() for r in results]
    return pd.DataFrame(rows)


def overall_quality_score(scorecard: pd.DataFrame) -> float:
    """Simple unweighted average pass rate across all rules in a scorecard."""
    if scorecard.empty:
        return 100.0
    return round(scorecard["pass_rate"].mean(), 2)


# ---------------------------------------------------------------------------
# Example usage (remove or adapt when integrating into a pipeline)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from functools import partial

    sample = pd.DataFrame({
        "customer_id": [1, 2, 2, 4, None],
        "email": ["a@example.com", "bad-email", "c@example.com", "d@example.com", "e@example.com"],
        "status": ["Active", "Active", "Inactive", "Pending", "Unknown"],
        "order_total": [100, -5, 250, 75, 9999999],
    })

    suite = [
        partial(check_completeness, sample, "customer_id"),
        partial(check_regex_format, sample, "email", COMMON_PATTERNS["email"]),
        partial(check_uniqueness, sample, ["customer_id"]),
        partial(check_value_in_set, sample, "status", ["Active", "Inactive", "Pending"]),
        partial(check_numeric_range, sample, "order_total", min_value=0, max_value=100000),
    ]

    scorecard = run_rule_suite(suite)
    print(scorecard.to_string(index=False))
    print(f"\nOverall Quality Score: {overall_quality_score(scorecard)}%")
