"""Omniroute web-search provider.

Wraps ``POST /v1/search``. Optional provider pinning via
``OMNIROUTE_SEARCH_PROVIDER`` env (e.g. ``tavily-search``); otherwise Omniroute
auto-selects configured providers.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

from .._version import _PLUGIN_VERSION
from ..config import (
    _SEARCH_DESC_LIMIT,
    _resolve_base_url,
    _resolve_search_provider,
    _resolve_token,
)

logger = logging.getLogger(__name__)


class OmnirouteWebSearchProvider(WebSearchProvider):
    """Omniroute ``POST /search`` backend (search-only; no extract)."""

    @property
    def name(self) -> str:
        return "omniroute"

    @property
    def display_name(self) -> str:
        return "Omniroute"

    def is_available(self) -> bool:
        if not _resolve_token():
            return False
        try:
            import requests  # noqa: F401
        except ImportError:
            return False
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Query is required and must be a non-empty string"}

        token = _resolve_token()
        if not token:
            return {
                "success": False,
                "error": "OMNIROUTE_TOKEN not set. Export OMNIROUTE_TOKEN or OMNIROUTE_API_KEY.",
            }

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests package not installed (pip install requests)"}

        payload: Dict[str, Any] = {"query": query, "max_results": limit}
        provider = _resolve_search_provider()
        if provider:
            payload["provider"] = provider

        try:
            resp = requests.post(
                f"{_resolve_base_url()}/search",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
                },
                timeout=60,
            )
        except Exception as exc:
            logger.debug("Omniroute search request failed", exc_info=True)
            return {"success": False, "error": f"Omniroute search request failed: {exc}"}

        if not resp.ok:
            return {
                "success": False,
                "error": f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}",
            }

        try:
            body = resp.json()
        except ValueError:
            return {"success": False, "error": "Omniroute returned a non-JSON response"}

        web: List[Dict[str, Any]] = []
        for idx, r in enumerate(body.get("results") or [], start=1):
            if not isinstance(r, dict):
                continue
            # Some providers return full page text in snippet/content; keep the
            # results list compact (the agent can fetch a URL for full content).
            desc = (r.get("snippet") or r.get("content") or "").strip()
            if len(desc) > _SEARCH_DESC_LIMIT:
                desc = desc[:_SEARCH_DESC_LIMIT].rstrip() + "…"
            web.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "description": desc,
                    "position": r.get("position") if r.get("position") is not None else idx,
                }
            )
        return {"success": True, "data": {"web": web}}
