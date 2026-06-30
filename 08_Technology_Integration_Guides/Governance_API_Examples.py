"""
Governance_API_Examples.py
============================
Data Governance Implementation Resources / 08_Technology_Integration_Guides

Purpose
-------
Reference client showing common API interaction patterns for a data
governance platform's core REST API: assets, domains, stewards, and
policies. Written against a generic REST contract (`/api/v1/...`) that
mirrors the schema in Data_Catalog_Setup_Scripts.sql — adapt the base
URL, authentication, and endpoint paths to your actual platform
(Collibra, Informatica EDC, Talend, Power BI/Purview, or a custom
catalog service).

This is illustrative example code, not a production SDK. Review and
harden error handling, retries, and credential management before use
in a live integration.

Dependencies: requests
    pip install requests
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

@dataclass
class GovernanceAPIConfig:
    base_url: str = os.environ.get("GOVERNANCE_API_URL", "https://governance-api.example.com/api/v1")
    api_token: str = os.environ.get("GOVERNANCE_API_TOKEN", "")
    timeout_seconds: int = 30
    max_retries: int = 3


class GovernanceAPIClient:
    """Thin wrapper around the governance platform's REST API.

    Mirrors the resources documented in Data_Catalog_Setup_Scripts.sql:
    assets, elements, domains, stewards, classification levels.
    """

    def __init__(self, config: Optional[GovernanceAPIConfig] = None):
        self.config = config or GovernanceAPIConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.config.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.config.base_url}{path}"
        last_exc = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.config.timeout_seconds, **kwargs)
                if resp.status_code == 429:  # rate limited — back off and retry
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)  # exponential backoff
        raise RuntimeError(f"Request to {url} failed after {self.config.max_retries} attempts: {last_exc}")

    # -- Domains -------------------------------------------------------

    def list_domains(self) -> list:
        """GET /domains — returns business domains (Customer, Finance, etc.)"""
        return self._request("GET", "/domains").json()

    def create_domain(self, domain_name: str, owner: str, description: str = "") -> dict:
        payload = {"domain_name": domain_name, "domain_owner": owner, "description": description}
        return self._request("POST", "/domains", json=payload).json()

    # -- Assets ----------------------------------------------------------

    def list_assets(self, domain: Optional[str] = None, classification: Optional[str] = None) -> list:
        """GET /assets — supports optional filtering by domain/classification."""
        params = {}
        if domain:
            params["domain"] = domain
        if classification:
            params["classification"] = classification
        return self._request("GET", "/assets", params=params).json()

    def get_asset(self, asset_id: str) -> dict:
        return self._request("GET", f"/assets/{asset_id}").json()

    def create_asset(self, asset_name: str, asset_type: str, source_id: str,
                      domain_id: Optional[str] = None, classification_id: Optional[str] = None) -> dict:
        payload = {
            "asset_name": asset_name,
            "asset_type": asset_type,
            "source_id": source_id,
            "business_domain_id": domain_id,
            "classification_id": classification_id,
        }
        return self._request("POST", "/assets", json=payload).json()

    def update_asset_steward(self, asset_id: str, business_steward_id: str) -> dict:
        payload = {"business_steward_id": business_steward_id}
        return self._request("PATCH", f"/assets/{asset_id}", json=payload).json()

    # -- Stewards --------------------------------------------------------

    def list_stewards(self, domain: Optional[str] = None) -> list:
        params = {"domain": domain} if domain else {}
        return self._request("GET", "/stewards", params=params).json()

    def assign_steward(self, asset_id: str, steward_id: str, steward_type: str = "Business") -> dict:
        """steward_type: 'Business' or 'Technical'"""
        field = "business_steward_id" if steward_type == "Business" else "technical_steward_id"
        return self._request("PATCH", f"/assets/{asset_id}", json={field: steward_id}).json()

    # -- Classification ----------------------------------------------------

    def list_classification_levels(self) -> list:
        return self._request("GET", "/classification-levels").json()

    def classify_asset(self, asset_id: str, classification_name: str) -> dict:
        return self._request("PATCH", f"/assets/{asset_id}", json={"classification": classification_name}).json()


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

def example_register_and_classify_asset():
    client = GovernanceAPIClient()

    # 1. Find or create the business domain
    domains = client.list_domains()
    finance_domain = next((d for d in domains if d["domain_name"] == "Financial"), None)
    if not finance_domain:
        finance_domain = client.create_domain("Financial", owner="[Finance Steward]")

    # 2. Register a new asset
    asset = client.create_asset(
        asset_name="fact_sales",
        asset_type="Table",
        source_id="dw-001",
        domain_id=finance_domain["domain_id"],
    )

    # 3. Classify it
    client.classify_asset(asset["asset_id"], classification_name="Confidential")

    # 4. Assign stewards
    stewards = client.list_stewards(domain="Financial")
    if stewards:
        client.assign_steward(asset["asset_id"], stewards[0]["steward_id"], steward_type="Business")

    print(f"Registered and classified asset: {asset['asset_name']}")


if __name__ == "__main__":
    # Requires GOVERNANCE_API_URL and GOVERNANCE_API_TOKEN environment variables
    # pointing at your actual governance platform's API.
    example_register_and_classify_asset()
