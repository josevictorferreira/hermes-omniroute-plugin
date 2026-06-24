"""Omniroute web-search + web-extract provider.

Wraps ``POST /v1/search`` (search) and ``POST /v1/web/fetch`` (extract). Optional
provider pinning via ``OMNIROUTE_SEARCH_PROVIDER`` / ``OMNIROUTE_FETCH_PROVIDER``
env (e.g. ``tavily-search``); otherwise Omniroute auto-selects configured
providers.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

from .._version import _PLUGIN_VERSION
from ..config import (
    _SEARCH_DESC_LIMIT,
    _resolve_base_url,
    _resolve_fetch_provider,
    _resolve_search_provider,
    _resolve_token,
)

logger = logging.getLogger(__name__)


class OmnirouteWebSearchProvider(WebSearchProvider):
    """Omniroute web backend: ``POST /search`` (search) + ``POST /web/fetch`` (extract)."""

    @property
    def name(self) -> str:
        return "omniroute"

    @property
    def display_name(self) -> str:
        return "Omniroute"

    def supports_extract(self) -> bool:
        return True

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

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract page content from one or more URLs via ``POST /web/fetch``.

        Omniroute's ``/web/fetch`` takes a single URL per call, so this loops
        and returns one result dict per input URL. A per-URL failure is reported
        in that URL's ``error`` field rather than aborting the whole batch.
        Unknown ``kwargs`` (``format``, ``max_chars``, …) are ignored.
        """
        if isinstance(urls, str):
            urls = [urls]
        urls = [u.strip() for u in (urls or []) if isinstance(u, str) and u.strip()]
        if not urls:
            return []

        token = _resolve_token()
        if not token:
            err = "OMNIROUTE_TOKEN not set. Export OMNIROUTE_TOKEN or OMNIROUTE_API_KEY."
            return [self._extract_error(u, err) for u in urls]

        try:
            import requests
        except ImportError:
            err = "requests package not installed (pip install requests)"
            return [self._extract_error(u, err) for u in urls]

        base_url = _resolve_base_url()
        provider = _resolve_fetch_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
        }
        return [self._extract_one(requests, base_url, headers, provider, u) for u in urls]

    def _extract_one(self, requests, base_url, headers, provider, url):
        payload: Dict[str, Any] = {"url": url}
        if provider:
            payload["provider"] = provider

        try:
            resp = requests.post(
                f"{base_url}/web/fetch", json=payload, headers=headers, timeout=60
            )
        except Exception as exc:
            logger.debug("Omniroute fetch request failed", exc_info=True)
            return self._extract_error(url, f"Omniroute fetch request failed: {exc}")

        if not resp.ok:
            return self._extract_error(
                url, f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            body = resp.json()
        except ValueError:
            return self._extract_error(url, "Omniroute returned a non-JSON response")

        if isinstance(body.get("error"), dict):
            msg = body["error"].get("message") or "unknown error"
            return self._extract_error(url, f"Omniroute fetch error: {msg}")

        content = body.get("content") or ""
        meta = dict(body["metadata"]) if isinstance(body.get("metadata"), dict) else {}
        if body.get("provider"):
            meta["provider"] = body["provider"]
        if body.get("links"):
            meta["links"] = body["links"]
        if body.get("screenshot_url"):
            meta["screenshot_url"] = body["screenshot_url"]

        return {
            "url": body.get("url") or url,
            "title": meta.get("title") or "",
            "content": content,
            "raw_content": content,
            "metadata": meta,
        }

    @staticmethod
    def _extract_error(url: str, error: str) -> Dict[str, Any]:
        return {"url": url, "title": "", "content": "", "raw_content": "", "error": error}
