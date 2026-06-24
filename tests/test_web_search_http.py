"""HTTP flow tests for :meth:`OmnirouteWebSearchProvider.search`.

Covers request/response mapping, payload construction, headers, and error
handling (auth, dependency, network, HTTP, malformed responses, snippet
truncation, and provider pinning).
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch


import omniroute_plugin as plugin
from omniroute_plugin.config import _SEARCH_DESC_LIMIT


def _provider():
    return plugin.OmnirouteWebSearchProvider()


def _json_response(body, *, ok=True, status_code=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = json.dumps(body)
    resp.json.return_value = body
    return resp


class TestSearchValidation:
    """Early-exit error paths before any network call."""

    def test_empty_query(self):
        out = _provider().search("   ")
        assert out["success"] is False
        assert "query" in out["error"].lower()

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value=None)
    def test_no_token(self, _mock_token):
        out = _provider().search("cats")
        assert out["success"] is False
        assert "omniroute_api_key" in out["error"].lower()

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="tok")
    def test_missing_requests_package(self, _mock_token):
        with patch.dict(sys.modules, {"requests": None}):
            out = _provider().search("cats")
        assert out["success"] is False
        assert "requests" in out["error"].lower()


class TestSearchRequestMapping:
    """Verify the HTTP request sent to the Omniroute search endpoint."""

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_posts_to_search_with_payload(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({"results": []})
        _provider().search("latest ai news", limit=10)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.example/search"
        assert kwargs["json"] == {"query": "latest ai news", "max_results": 10}
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"
        assert "hermes-omniroute-plugin/" in headers["User-Agent"]

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_search_provider_pinned_in_payload(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({"results": []})
        with patch("omniroute_plugin.providers.web_search._resolve_search_provider", return_value="tavily-search"):
            _provider().search("weather")
        assert mock_post.call_args.kwargs["json"]["provider"] == "tavily-search"


class TestSearchResponseMapping:
    """Verify successful responses are mapped to Hermes web-search shape."""

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_maps_result_fields(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({
            "results": [
                {"title": "A", "url": "https://a.test", "snippet": "snippet a", "position": 0},
                {"title": "B", "url": "https://b.test", "content": "content b", "position": 2},
            ]
        })
        out = _provider().search("test")
        assert out["success"] is True
        assert out["data"]["web"] == [
            {"title": "A", "url": "https://a.test", "description": "snippet a", "position": 0},
            {"title": "B", "url": "https://b.test", "description": "content b", "position": 2},
        ]

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_snippet_truncated_to_limit(self, mock_post, _mock_url, _mock_token):
        long_snippet = "x" * (_SEARCH_DESC_LIMIT + 50)
        mock_post.return_value = _json_response({
            "results": [{"title": "A", "url": "https://a.test", "snippet": long_snippet}]
        })
        out = _provider().search("test")
        desc = out["data"]["web"][0]["description"]
        assert len(desc) <= _SEARCH_DESC_LIMIT + 1  # +1 for trailing ellipsis
        assert desc.endswith("…")

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_missing_position_falls_to_one_based_index(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({
            "results": [
                {"title": "A", "url": "https://a.test", "snippet": "a"},
                {"title": "B", "url": "https://b.test", "snippet": "b"},
            ]
        })
        out = _provider().search("test")
        positions = [r["position"] for r in out["data"]["web"]]
        assert positions == [1, 2]


class TestSearchErrorHandling:
    """Network and API-level failures."""

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_network_exception(self, mock_post, _mock_url, _mock_token):
        mock_post.side_effect = ConnectionError("dns failed")
        out = _provider().search("test")
        assert out["success"] is False
        assert "dns failed" in out["error"].lower()

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_http_error(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({"error": "rate limited"}, ok=False, status_code=429)
        out = _provider().search("test")
        assert out["success"] is False
        assert "429" in out["error"]

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_non_json_response(self, mock_post, _mock_url, _mock_token):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("no json")
        mock_post.return_value = resp
        out = _provider().search("test")
        assert out["success"] is False
        assert "non-json" in out["error"].lower()
