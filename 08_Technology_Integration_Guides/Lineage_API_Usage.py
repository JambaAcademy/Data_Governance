"""
Lineage_API_Usage.py
======================
Data Governance Implementation Resources / 08_Technology_Integration_Guides

Purpose
-------
Reference client for registering lineage relationships (e.g., discovered
by Data_Lineage_Discovery_Tools.py) into a governance platform's lineage
API, and for traversing lineage graphs for impact analysis. Mirrors the
data_lineage schema from Data_Catalog_Setup_Scripts.sql.

Illustrative example code — adapt to your platform's actual API
contract (Collibra, Informatica EDC, Talend, Microsoft Purview, or a
custom catalog service).

Dependencies: requests
    pip install requests
"""

from __future__ import annotations

import os
import csv
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class LineageAPIConfig:
    base_url: str = os.environ.get("GOVERNANCE_API_URL", "https://governance-api.example.com/api/v1")
    api_token: str = os.environ.get("GOVERNANCE_API_TOKEN", "")
    timeout_seconds: int = 30


class LineageAPIClient:
    """Client for registering and traversing lineage relationships."""

    def __init__(self, config: Optional[LineageAPIConfig] = None):
        self.config = config or LineageAPIConfig()
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

    # -- Registering lineage --------------------------------------------------

    def register_lineage(self, source_asset_id: str, target_asset_id: str,
                          transformation_summary: str = "", process_name: str = "",
                          captured_method: str = "Manual") -> dict:
        payload = {
            "source_asset_id": source_asset_id,
            "target_asset_id": target_asset_id,
            "transformation_summary": transformation_summary,
            "process_name": process_name,
            "captured_method": captured_method,  # 'Manual' or 'Automated'
        }
        return self._request("POST", "/lineage", json=payload).json()

    def register_lineage_from_discovery_csv(self, csv_path: str) -> dict:
        """Loads candidate lineage records produced by
        Data_Lineage_Discovery_Tools.py (CSV format) and registers them,
        resolving asset names to IDs first.

        NOTE: All registered-via-discovery lineage should be flagged for
        Technical Data Steward review before being trusted for compliance
        reporting (see Metadata_Management_Procedures.md).
        """
        summary = {"registered": 0, "skipped_unresolved_assets": 0, "errors": []}
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                source_id = self._resolve_asset_id(row["source_asset"])
                target_id = self._resolve_asset_id(row["target_asset"])
                if not source_id or not target_id:
                    summary["skipped_unresolved_assets"] += 1
                    continue
                try:
                    self.register_lineage(
                        source_asset_id=source_id,
                        target_asset_id=target_id,
                        transformation_summary=row.get("transformation_summary", ""),
                        process_name=row.get("process_name", ""),
                        captured_method=row.get("captured_method", "Automated"),
                    )
                    summary["registered"] += 1
                except requests.exceptions.HTTPError as exc:
                    summary["errors"].append({"row": row, "error": str(exc)})
        return summary

    def _resolve_asset_id(self, asset_name: str) -> Optional[str]:
        resp = self._request("GET", "/assets", params={"asset_name": asset_name})
        results = resp.json()
        return results[0]["asset_id"] if results else None

    # -- Traversal / impact analysis -------------------------------------------

    def get_upstream_lineage(self, asset_id: str, depth: int = 5) -> dict:
        """Returns the upstream lineage graph (sources feeding this asset)
        up to `depth` hops — useful for root-cause analysis on a data issue."""
        return self._request("GET", f"/lineage/{asset_id}/upstream", params={"depth": depth}).json()

    def get_downstream_lineage(self, asset_id: str, depth: int = 5) -> dict:
        """Returns the downstream lineage graph (consumers of this asset)
        up to `depth` hops — useful for impact analysis before a schema
        change or to scope a data quality incident (see
        Data_Quality_Incident_Response.md, Phase 4: Root Cause Investigation)."""
        return self._request("GET", f"/lineage/{asset_id}/downstream", params={"depth": depth}).json()

    def impact_analysis(self, asset_id: str) -> dict:
        """Convenience wrapper combining downstream lineage with asset
        criticality flags, to estimate the blast radius of a change."""
        downstream = self.get_downstream_lineage(asset_id)
        affected_assets = downstream.get("nodes", [])
        critical_count = sum(1 for a in affected_assets if a.get("is_critical_data_element"))
        return {
            "asset_id": asset_id,
            "total_downstream_assets": len(affected_assets),
            "critical_downstream_assets": critical_count,
            "affected_assets": affected_assets,
        }

    # -- Verification workflow --------------------------------------------------

    def list_unverified_lineage(self) -> list:
        """Returns lineage records captured automatically but not yet
        reviewed/verified by a Technical Data Steward."""
        return self._request("GET", "/lineage", params={"verified": False}).json()

    def verify_lineage(self, lineage_id: str, verified_by: str) -> dict:
        payload = {"verified": True, "verified_by": verified_by}
        return self._request("PATCH", f"/lineage/{lineage_id}", json=payload).json()


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

def example_load_discovered_lineage():
    client = LineageAPIClient()
    summary = client.register_lineage_from_discovery_csv("lineage_candidates.csv")  # from Data_Lineage_Discovery_Tools.py
    print(f"Registered {summary['registered']} lineage relationships "
          f"({summary['skipped_unresolved_assets']} skipped due to unresolved assets).")


def example_impact_analysis_before_change():
    client = LineageAPIClient()
    impact = client.impact_analysis(asset_id="asset-fact-sales-001")
    print(f"Changing this asset would affect {impact['total_downstream_assets']} downstream assets, "
          f"{impact['critical_downstream_assets']} of which are Critical Data Elements.")
    if impact["critical_downstream_assets"] > 0:
        print("Recommend Technical Data Steward review before proceeding with the change.")


if __name__ == "__main__":
    # Requires GOVERNANCE_API_URL and GOVERNANCE_API_TOKEN environment variables.
    example_impact_analysis_before_change()
