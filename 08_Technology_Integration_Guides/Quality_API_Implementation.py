"""
Quality_API_Implementation.py
================================
Data Governance Implementation Resources / 08_Technology_Integration_Guides

Purpose
-------
Reference client for pushing data quality rule results (e.g., produced
by Data_Quality_Rules_Library.py) into a governance platform's quality
API, and for reading back quality scorecards for reporting. Mirrors the
data_quality_rules / data_quality_results schema from
Data_Catalog_Setup_Scripts.sql.

Illustrative example code — adapt to your platform's actual API
contract (Collibra Data Quality, Informatica IDQ, Talend Data Quality,
or a custom quality service).

Dependencies: requests
    pip install requests
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests


@dataclass
class QualityAPIConfig:
    base_url: str = os.environ.get("GOVERNANCE_API_URL", "https://governance-api.example.com/api/v1")
    api_token: str = os.environ.get("GOVERNANCE_API_TOKEN", "")
    timeout_seconds: int = 30


class QualityAPIClient:
    """Client for registering quality rules and publishing rule run results."""

    def __init__(self, config: Optional[QualityAPIConfig] = None):
        self.config = config or QualityAPIConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.config.api_token}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.config.base_url}{path}"
        resp = self.session.request(method, url, timeout=self.config.timeout_seconds, **kwargs)
        resp.raise_for_status()
        return resp

    # -- Rule definitions ----------------------------------------------------

    def register_rule(self, element_id: str, rule_type: str, rule_description: str,
                       severity: str = "Medium", rule_expression: str = "") -> dict:
        """rule_type: Completeness, Validity, Uniqueness, Consistency, Timeliness, Accuracy
        (matches the dimensions in Data_Quality_Rules_Library.py)"""
        payload = {
            "element_id": element_id,
            "rule_type": rule_type,
            "rule_description": rule_description,
            "rule_expression": rule_expression,
            "severity": severity,
            "active": True,
        }
        return self._request("POST", "/quality/rules", json=payload).json()

    def list_rules(self, asset_id: Optional[str] = None, active_only: bool = True) -> list:
        params = {"active": active_only}
        if asset_id:
            params["asset_id"] = asset_id
        return self._request("GET", "/quality/rules", params=params).json()

    def deactivate_rule(self, rule_id: str) -> dict:
        return self._request("PATCH", f"/quality/rules/{rule_id}", json={"active": False}).json()

    # -- Publishing results ----------------------------------------------------

    def publish_result(self, rule_id: str, records_evaluated: int, records_failed: int,
                        run_timestamp: Optional[str] = None) -> dict:
        pass_rate = round(100.0 * (records_evaluated - records_failed) / records_evaluated, 2) if records_evaluated else 100.0
        status = "Pass" if records_failed == 0 else ("Warning" if pass_rate >= 95 else "Fail")
        payload = {
            "rule_id": rule_id,
            "records_evaluated": records_evaluated,
            "records_failed": records_failed,
            "pass_rate": pass_rate,
            "status": status,
            "run_timestamp": run_timestamp or datetime.now(timezone.utc).isoformat(),
        }
        return self._request("POST", "/quality/results", json=payload).json()

    def publish_results_from_scorecard(self, scorecard_rows: list, rule_id_lookup: dict) -> dict:
        """Bridges Data_Quality_Rules_Library.py's RuleResult.to_dict() output
        (a list of dicts) to this API, using rule_id_lookup to map local
        rule_name -> registered rule_id in the platform.

        Returns a summary of publish successes/failures.
        """
        summary = {"published": 0, "skipped": 0, "errors": []}
        for row in scorecard_rows:
            rule_id = rule_id_lookup.get(row["rule_name"])
            if rule_id is None:
                summary["skipped"] += 1
                continue
            try:
                self.publish_result(
                    rule_id=rule_id,
                    records_evaluated=row["records_evaluated"],
                    records_failed=row["records_failed"],
                    run_timestamp=row.get("run_timestamp"),
                )
                summary["published"] += 1
            except requests.exceptions.HTTPError as exc:
                summary["errors"].append({"rule_name": row["rule_name"], "error": str(exc)})
        return summary

    # -- Reading scorecards ----------------------------------------------------

    def get_asset_scorecard(self, asset_id: str) -> dict:
        """Returns the current quality scorecard for an asset: average pass
        rate, rules monitored, and per-rule breakdown."""
        return self._request("GET", f"/quality/scorecards/{asset_id}").json()

    def get_domain_scorecard(self, domain: str) -> dict:
        return self._request("GET", "/quality/scorecards", params={"domain": domain}).json()

    def get_failing_rules(self, threshold_pass_rate: float = 95.0) -> list:
        """Returns currently failing rules below the SLA threshold, matching
        the compliance logic in Compliance_Reporting_Scripts.sql."""
        return self._request("GET", "/quality/results/failing", params={"threshold": threshold_pass_rate}).json()


# ---------------------------------------------------------------------------
# Example usage: bridging the local Python rules library to the platform API
# ---------------------------------------------------------------------------

def example_run_and_publish():
    """Demonstrates the typical end-to-end flow:
    1. Run local rule suite (Data_Quality_Rules_Library.py)
    2. Publish results to the governance platform via this API client
    """
    # from Data_Quality_Rules_Library import run_rule_suite, check_completeness, ...
    # scorecard_df = run_rule_suite([...])
    # scorecard_rows = scorecard_df.to_dict("records")

    client = QualityAPIClient()

    # Example: map local rule names to platform rule IDs (in practice, fetch
    # this mapping once and cache it, or maintain a rule registry)
    rule_id_lookup = {
        "completeness_customer_id": "rule-001",
        "uniqueness_customer_id": "rule-002",
    }

    scorecard_rows = [
        {"rule_name": "completeness_customer_id", "records_evaluated": 1000, "records_failed": 12},
        {"rule_name": "uniqueness_customer_id", "records_evaluated": 1000, "records_failed": 3},
    ]

    summary = client.publish_results_from_scorecard(scorecard_rows, rule_id_lookup)
    print(f"Published {summary['published']} results, skipped {summary['skipped']}, "
          f"{len(summary['errors'])} errors.")


if __name__ == "__main__":
    # Requires GOVERNANCE_API_URL and GOVERNANCE_API_TOKEN environment variables.
    example_run_and_publish()
