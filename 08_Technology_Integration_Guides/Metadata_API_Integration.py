"""
Metadata_API_Integration.py
=============================
Data Governance Implementation Resources / 08_Technology_Integration_Guides

Purpose
-------
Reference client for pushing technical metadata extracted by
Metadata_Extraction_Tools.py (Section 05) into a governance platform's
metadata/catalog API. Demonstrates batch upsert patterns, pagination
when reading metadata back, and basic conflict handling.

Illustrative example code — adapt to your platform's actual API
contract (Collibra, Informatica EDC, Talend, Microsoft Purview, or a
custom catalog service).

Dependencies: requests, pandas (optional, for CSV batch loading)
    pip install requests pandas
"""

from __future__ import annotations

import os
import csv
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import requests


@dataclass
class MetadataAPIConfig:
    base_url: str = os.environ.get("GOVERNANCE_API_URL", "https://governance-api.example.com/api/v1")
    api_token: str = os.environ.get("GOVERNANCE_API_TOKEN", "")
    timeout_seconds: int = 30
    batch_size: int = 100


class MetadataAPIClient:
    """Client for technical/business metadata operations against the catalog."""

    def __init__(self, config: Optional[MetadataAPIConfig] = None):
        self.config = config or MetadataAPIConfig()
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

    # -- Single element operations -----------------------------------------

    def upsert_element(self, asset_id: str, column_name: str, data_type: str,
                        business_definition: str = "", is_pii: bool = False,
                        is_critical_data_element: bool = False) -> dict:
        """Creates or updates a single data element's metadata."""
        payload = {
            "asset_id": asset_id,
            "column_name": column_name,
            "data_type": data_type,
            "business_definition": business_definition,
            "is_pii": is_pii,
            "is_critical_data_element": is_critical_data_element,
        }
        return self._request("PUT", f"/assets/{asset_id}/elements/{column_name}", json=payload).json()

    def get_element(self, asset_id: str, column_name: str) -> dict:
        return self._request("GET", f"/assets/{asset_id}/elements/{column_name}").json()

    # -- Batch operations --------------------------------------------------

    def batch_upsert_elements(self, elements: list) -> dict:
        """Upserts a list of element dicts in batches of config.batch_size.

        Each dict should have: asset_id, column_name, data_type, and
        optionally business_definition, is_pii, is_critical_data_element.
        """
        results = {"succeeded": 0, "failed": 0, "errors": []}
        batch_size = self.config.batch_size

        for i in range(0, len(elements), batch_size):
            batch = elements[i:i + batch_size]
            try:
                resp = self._request("POST", "/elements/batch-upsert", json={"elements": batch})
                body = resp.json()
                results["succeeded"] += body.get("succeeded", len(batch))
            except requests.exceptions.HTTPError as exc:
                results["failed"] += len(batch)
                results["errors"].append({"batch_start": i, "error": str(exc)})
            time.sleep(0.2)  # gentle rate limiting between batches

        return results

    def load_elements_from_csv(self, csv_path: str) -> list:
        """Reads a CSV in the format produced by Metadata_Extraction_Tools.py
        (asset_name, column_name, data_type, is_pii_candidate, business_definition,
        is_critical_data_element) and resolves asset_name -> asset_id via lookup,
        then batch-upserts.

        NOTE: assumes assets are already registered (see Governance_API_Examples.py).
        """
        elements = []
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                asset_id = self._resolve_asset_id(row["asset_name"])
                if asset_id is None:
                    continue  # asset not yet registered; register first
                elements.append({
                    "asset_id": asset_id,
                    "column_name": row["column_name"],
                    "data_type": row["data_type"],
                    "is_pii": row.get("is_pii_candidate", "False") == "True",
                    "business_definition": row.get("business_definition", ""),
                    "is_critical_data_element": row.get("is_critical_data_element", "False") == "True",
                })
        return elements

    def _resolve_asset_id(self, asset_name: str) -> Optional[str]:
        resp = self._request("GET", "/assets", params={"asset_name": asset_name})
        results = resp.json()
        return results[0]["asset_id"] if results else None

    # -- Reading metadata back (with pagination) ----------------------------

    def iter_all_elements(self, page_size: int = 200) -> Iterable[dict]:
        """Generator yielding every element in the catalog, handling pagination."""
        page = 1
        while True:
            resp = self._request("GET", "/elements", params={"page": page, "page_size": page_size})
            body = resp.json()
            items = body.get("items", [])
            if not items:
                break
            yield from items
            if not body.get("has_next", False):
                break
            page += 1

    # -- Completeness check (mirrors Metadata Management Procedures) --------

    def find_incomplete_metadata(self) -> list:
        """Returns elements missing required fields per the Metadata Quality
        Standards in Metadata_Management_Procedures.md."""
        incomplete = []
        for element in self.iter_all_elements():
            missing = []
            if not element.get("business_definition"):
                missing.append("business_definition")
            if element.get("classification") is None:
                missing.append("classification")
            if missing:
                incomplete.append({"element": element, "missing_fields": missing})
        return incomplete


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

def example_load_extraction_results():
    client = MetadataAPIClient()
    elements = client.load_elements_from_csv("metadata_extract.csv")  # output of Metadata_Extraction_Tools.py
    results = client.batch_upsert_elements(elements)
    print(f"Loaded {results['succeeded']} elements, {results['failed']} failed.")
    if results["errors"]:
        for err in results["errors"]:
            print(f"  Batch starting at {err['batch_start']}: {err['error']}")


def example_completeness_audit():
    client = MetadataAPIClient()
    incomplete = client.find_incomplete_metadata()
    print(f"Found {len(incomplete)} elements with incomplete metadata.")
    for item in incomplete[:10]:
        print(f"  {item['element'].get('column_name')}: missing {item['missing_fields']}")


if __name__ == "__main__":
    # Requires GOVERNANCE_API_URL and GOVERNANCE_API_TOKEN environment variables.
    example_completeness_audit()
