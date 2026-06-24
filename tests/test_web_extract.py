"""HTTP flow tests for :meth:`OmnirouteWebSearchProvider.extract`.

Covers request/response mapping for ``POST /web/fetch``, per-URL looping,
provider pinning, metadata folding, and per-URL error isolation (auth,
dependency, network, HTTP, malformed response, API error body).
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import omniroute_plugin as plugin


def _provider():
    return plugin.OmnirouteWebSearchProvider()


def _json_response(body, *, ok=True, status_code=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = json.dumps(body)
    resp.json.return_value = body
    return resp


def _fetch_body(url="https://a.test", content="hello", **extra):
    body = {
        "provider": "tavily-search",
        "url": url,
        "content": content,
        "links": [],
        "metadata": None,
        "screenshot_url": None,
    }
    body.update(extra)
    return body


class TestExtractCapability:
    def test_supports_extract(self):
        assert _provider().supports_extract() is True


class TestExtractValidation:
    """Early-exit / per-URL guard paths before (or instead of) network calls."""

    def test_empty_list(self):
        assert _provider().extract([]) == []

    def test_blank_urls_filtered_to_empty(self):
        assert _provider().extract(["", "   ", None]) == []

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value=None)
    def test_no_token_returns_per_url_error(self, _mock_token):
        out = _provider().extract(["https://a.test"])
        assert len(out) == 1
        assert out[0]["url"] == "https://a.test"
        assert "omniroute_api_key" in out[0]["error"].lower()

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="tok")
    def test_missing_requests_package(self, _mock_token):
        with patch.dict(sys.modules, {"requests": None}):
            out = _provider().extract(["https://a.test"])
        assert "requests" in out[0]["error"].lower()


class TestExtractRequestMapping:
    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_posts_to_web_fetch_with_payload(self, mock_post, _url, _token):
        mock_post.return_value = _json_response(_fetch_body())
        _provider().extract(["https://a.test"])

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.example/web/fetch"
        assert kwargs["json"] == {"url": "https://a.test"}
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"
        assert "hermes-omniroute-plugin/" in headers["User-Agent"]

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_fetch_provider_pinned_in_payload(self, mock_post, _url, _token):
        mock_post.return_value = _json_response(_fetch_body())
        with patch("omniroute_plugin.providers.web_search._resolve_fetch_provider", return_value="tavily-search"):
            _provider().extract(["https://a.test"])
        assert mock_post.call_args.kwargs["json"]["provider"] == "tavily-search"

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_str_arg_normalized_to_single_url(self, mock_post, _url, _token):
        mock_post.return_value = _json_response(_fetch_body())
        out = _provider().extract("https://a.test")
        assert len(out) == 1
        assert mock_post.call_count == 1

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_loops_one_call_per_url(self, mock_post, _url, _token):
        mock_post.side_effect = [
            _json_response(_fetch_body(url="https://a.test", content="A")),
            _json_response(_fetch_body(url="https://b.test", content="B")),
        ]
        out = _provider().extract(["https://a.test", "https://b.test"])
        assert mock_post.call_count == 2
        assert [r["url"] for r in out] == ["https://a.test", "https://b.test"]
        assert [r["content"] for r in out] == ["A", "B"]


class TestExtractResponseMapping:
    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_content_mapped_to_content_and_raw_content(self, mock_post, _url, _token):
        mock_post.return_value = _json_response(_fetch_body(content="# Title\nbody"))
        out = _provider().extract(["https://a.test"])[0]
        assert out["content"] == "# Title\nbody"
        assert out["raw_content"] == "# Title\nbody"
        assert "error" not in out

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_metadata_and_title_folded(self, mock_post, _url, _token):
        body = _fetch_body(
            metadata={"title": "Hello", "author": "x"},
            links=["https://a.test/1"],
            screenshot_url="https://shot",
        )
        mock_post.return_value = _json_response(body)
        out = _provider().extract(["https://a.test"])[0]
        assert out["title"] == "Hello"
        assert out["metadata"]["author"] == "x"
        assert out["metadata"]["provider"] == "tavily-search"
        assert out["metadata"]["links"] == ["https://a.test/1"]
        assert out["metadata"]["screenshot_url"] == "https://shot"

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_null_metadata_yields_empty_dict_and_blank_title(self, mock_post, _url, _token):
        mock_post.return_value = _json_response(_fetch_body())
        out = _provider().extract(["https://a.test"])[0]
        assert out["title"] == ""
        assert out["metadata"] == {"provider": "tavily-search"}


class TestExtractErrorHandling:
    """Failures are isolated per-URL: one bad URL never drops the batch."""

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_network_exception_isolated(self, mock_post, _url, _token):
        mock_post.side_effect = [
            ConnectionError("dns failed"),
            _json_response(_fetch_body(url="https://b.test", content="B")),
        ]
        out = _provider().extract(["https://a.test", "https://b.test"])
        assert "dns failed" in out[0]["error"].lower()
        assert out[1]["content"] == "B"
        assert "error" not in out[1]

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_http_error(self, mock_post, _url, _token):
        mock_post.return_value = _json_response({"error": "nope"}, ok=False, status_code=429)
        out = _provider().extract(["https://a.test"])[0]
        assert "429" in out["error"]

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_api_error_body(self, mock_post, _url, _token):
        mock_post.return_value = _json_response(
            {"error": {"message": "Invalid request", "type": "invalid_request_error"}}
        )
        out = _provider().extract(["https://a.test"])[0]
        assert "invalid request" in out["error"].lower()

    @patch("omniroute_plugin.providers.web_search._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.web_search._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_non_json_response(self, mock_post, _url, _token):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("no json")
        mock_post.return_value = resp
        out = _provider().extract(["https://a.test"])[0]
        assert "non-json" in out["error"].lower()
