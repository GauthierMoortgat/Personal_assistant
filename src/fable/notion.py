"""Thin Notion API client + property extraction helpers."""

from __future__ import annotations

from typing import Any

import requests

API_BASE = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"


class NotionError(RuntimeError):
    pass


class Notion:
    def __init__(self, token: str):
        if not token:
            raise NotionError(
                "No Notion token. Set NOTION_API_KEY or add notion_token to fable.toml "
                "(run `fable init` for setup help)."
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": API_VERSION,
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        resp = self.session.request(method, f"{API_BASE}{path}", json=payload, timeout=30)
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise NotionError(f"Notion API {resp.status_code}: {message}")
        return resp.json()

    def query_database(self, database_id: str, filter: dict | None = None,
                       sorts: list | None = None) -> list[dict]:
        """Query a database, following pagination to the end."""
        results: list[dict] = []
        payload: dict[str, Any] = {"page_size": 100}
        if filter:
            payload["filter"] = filter
        if sorts:
            payload["sorts"] = sorts
        while True:
            data = self._request("POST", f"/databases/{database_id}/query", payload)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            payload["start_cursor"] = data["next_cursor"]

    def create_page(self, database_id: str, properties: dict) -> dict:
        return self._request(
            "POST", "/pages", {"parent": {"database_id": database_id}, "properties": properties}
        )

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def append_blocks(self, block_id: str, children: list[dict]) -> dict:
        return self._request("PATCH", f"/blocks/{block_id}/children", {"children": children})

    def get_block_children(self, block_id: str) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while True:
            path = f"/blocks/{block_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = self._request("GET", path)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data["next_cursor"]


# ---------------------------------------------------------------------------
# Property extraction (Notion property payload -> plain values)

def plain_text(rich: list[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in rich or [])


def prop_value(prop: dict | None) -> Any:
    """Extract a plain value from any Notion property payload."""
    if not prop:
        return None
    kind = prop.get("type")
    value = prop.get(kind)
    if value is None:
        return None
    if kind in ("title", "rich_text"):
        return plain_text(value)
    if kind in ("select", "status"):
        return value.get("name")
    if kind == "date":
        return value.get("start")
    if kind == "unique_id":
        prefix = value.get("prefix")
        number = value.get("number")
        return f"{prefix}-{number}" if prefix else str(number)
    if kind == "people":
        return [person.get("name") or person.get("id") for person in value]
    return value
