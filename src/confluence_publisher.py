"""
confluence_publisher.py
Creates or updates a Confluence page via the Atlassian REST API.
"""

import os
import base64
import requests


class ConfluencePublisher:
    def __init__(self):
        base = os.environ["CONFLUENCE_BASE_URL"].rstrip("/")
        # Ensure the URL ends with /wiki
        if not base.endswith("/wiki"):
            base = base + "/wiki" if not base.endswith("/wiki") else base
        self.base_url = base
        self.email = os.environ["CONFLUENCE_EMAIL"]
        self.api_token = os.environ["CONFLUENCE_API_TOKEN"]
        self.space_key = os.environ["CONFLUENCE_SPACE_KEY"]
        self.parent_page_id = os.environ["CONFLUENCE_PARENT_PAGE_ID"]

        encoded = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/rest/api/{path}"
        resp = requests.request(method, url, headers=self.headers, timeout=60, **kwargs)
        resp.raise_for_status()
        return resp

    def find_page(self, title: str) -> dict | None:
        resp = self._api(
            "GET", "content",
            params={"type": "page", "spaceKey": self.space_key, "title": title, "expand": "version"},
        )
        results = resp.json().get("results", [])
        return results[0] if results else None

    def create_page(self, title: str, html_body: str) -> dict:
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": self.space_key},
            "ancestors": [{"id": self.parent_page_id}],
            "body": {"storage": {"value": html_body, "representation": "storage"}},
        }
        return self._api("POST", "content", json=payload).json()

    def update_page(self, page_id: str, version: int, title: str, html_body: str) -> dict:
        payload = {
            "type": "page",
            "title": title,
            "version": {"number": version + 1},
            "body": {"storage": {"value": html_body, "representation": "storage"}},
        }
        return self._api("PUT", f"content/{page_id}", json=payload).json()

    def publish(self, title: str, html_body: str) -> dict:
        """Create a new page or update an existing one with the same title."""
        existing = self.find_page(title)
        if existing:
            page_id = existing["id"]
            ver = existing["version"]["number"]
            print(f"[Confluence] Updating page ID={page_id} (v{ver} → v{ver + 1})")
            result = self.update_page(page_id, ver, title, html_body)
        else:
            print(f"[Confluence] Creating new page: '{title}'")
            result = self.create_page(title, html_body)

        webui = result.get("_links", {}).get("webui", "")
        base = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
        # Remove /wiki suffix for display URL construction since webui already includes it
        if base.endswith("/wiki"):
            base = base[:-5]
        full_url = f"{base}{webui}" if webui else "N/A"
        print(f"[Confluence] Page URL: {full_url}")
        return result
