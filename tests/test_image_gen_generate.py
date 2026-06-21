"""HTTP flow tests for :meth:`OmnirouteImageGenProvider.generate`.

Covers request/response mapping, payload construction, headers, and the
full error-handling surface (auth, dependency, network, HTTP, malformed/empty
responses, cache I/O).
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

import omniroute_plugin as plugin
from omniroute_plugin.providers import image_gen as ig


def _error_response(**kw):
    return {"success": False, **kw}


def _success_response(**kw):
    return {"success": True, **kw}


@pytest.fixture(autouse=True)
def _preserve_response_kwargs(monkeypatch):
    """The conftest stubs discard kwargs; preserve them so tests can inspect error_type."""
    monkeypatch.setattr(ig, "error_response", _error_response)
    monkeypatch.setattr(ig, "success_response", _success_response)


def _provider_with_model(sizes=None):
    """Build a provider whose registry already contains a known model."""
    p = plugin.OmnirouteImageGenProvider()
    p._registry = {
        "test/model": {
            "id": "test/model",
            "name": "Test Model",
            "supported_sizes": sizes or ["1024x1024", "1792x1024"],
        }
    }
    return p


def _fake_b64_response(b64="QkFTRS02NA==", revised_prompt=None):
    """Return a mock requests response with a b64_json image payload."""
    body = {"data": [{"b64_json": b64}]}
    if revised_prompt is not None:
        body["data"][0]["revised_prompt"] = revised_prompt
    return _json_response(body)


def _fake_url_response(url="https://omniroute.example/img.png"):
    """Return a mock requests response with a URL image payload."""
    return _json_response({"data": [{"url": url}]})


def _json_response(body, *, ok=True, status_code=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = json.dumps(body)
    resp.json.return_value = body
    return resp


class TestGenerateValidation:
    """Early-exit error paths before any network call."""

    def test_empty_prompt(self):
        out = _provider_with_model().generate("   ")
        assert out["success"] is False
        assert "prompt" in out.get("error", "").lower()
        assert out.get("error_type") == "invalid_argument"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value=None)
    def test_no_token(self, _mock_token):
        out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "token" in out.get("error", "").lower()
        assert out.get("error_type") == "auth_required"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="tok")
    def test_missing_requests_package(self, _mock_token):
        with patch.dict(sys.modules, {"requests": None}):
            out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "requests" in out.get("error", "").lower()
        assert out.get("error_type") == "missing_dependency"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="tok")
    def test_no_model_resolved(self, _mock_token):
        p = _provider_with_model()
        p._resolve_model = lambda: None
        out = p.generate("a cat")
        assert out["success"] is False
        assert "model" in out.get("error", "").lower()
        assert out.get("error_type") == "invalid_argument"


class TestGenerateRequestMapping:
    """Verify the HTTP request sent to the Omniroute images endpoint."""

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_posts_to_images_generations_with_payload(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_b64_response()
        with patch.object(ig, "save_b64_image", return_value="/tmp/omniroute_123.png"):
            _provider_with_model().generate("a cat", aspect_ratio="1:1")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.example/images/generations"
        assert kwargs["json"] == {"model": "test/model", "prompt": "a cat", "size": "1024x1024"}
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"
        assert "hermes-omniroute-plugin/" in headers["User-Agent"]

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_aspect_ratio_maps_to_supported_size(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_b64_response()
        with patch.object(ig, "save_b64_image", return_value="/tmp/omniroute_123.png"),              patch.object(ig, "resolve_aspect_ratio", return_value="landscape"):
            _provider_with_model().generate("a cat", aspect_ratio="16:9")

        assert mock_post.call_args.kwargs["json"]["size"] == "1792x1024"


class TestGenerateResponseMapping:
    """Verify successful responses are mapped to Hermes success_response shape."""

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_b64_json_success(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_b64_response()
        with patch.object(ig, "save_b64_image", return_value="/tmp/omniroute_b64.png") as mock_save:
            out = _provider_with_model().generate("a cat")

        assert out["success"] is True
        assert out["image"] == "/tmp/omniroute_b64.png"
        assert out["model"] == "test/model"
        assert out["prompt"] == "a cat"
        assert out["provider"] == "omniroute"
        assert out["extra"]["size"] == "1024x1024"
        mock_save.assert_called_once()

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_url_fallback(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_url_response("https://omniroute.example/generated.webp")
        with patch.object(ig, "save_url_image", return_value="/tmp/omniroute_url.png") as mock_save:
            out = _provider_with_model().generate("a dog")

        assert out["success"] is True
        assert out["image"] == "/tmp/omniroute_url.png"
        mock_save.assert_called_once()

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_revised_prompt_added_to_extra(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_b64_response(revised_prompt="a fluffy cat")
        with patch.object(ig, "save_b64_image", return_value="/tmp/omniroute_b64.png"):
            out = _provider_with_model().generate("a cat")
        assert out["extra"]["revised_prompt"] == "a fluffy cat"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_extension_detected_from_b64_magic_bytes(self, mock_post, _mock_url, _mock_token):
        # JPEG magic bytes as base64: /9j/4AAQ... (0xff 0xd8 0xff)
        b64_jpeg = "/9j/4AAQSkZJRgABAQEASABIAAD"
        mock_post.return_value = _fake_b64_response(b64=b64_jpeg)
        with patch.object(ig, "save_b64_image", return_value="/tmp/omniroute_b64.jpg") as mock_save:
            _provider_with_model().generate("a cat")
        _, kwargs = mock_save.call_args
        assert kwargs.get("extension") == "jpg"


class TestGenerateErrorHandling:
    """Network and API-level failures."""

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_network_exception(self, mock_post, _mock_url, _mock_token):
        mock_post.side_effect = TimeoutError("connection timed out")
        out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "timed out" in out.get("error", "").lower()
        assert out.get("error_type") == "api_error"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_http_error(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({"error": "bad request"}, ok=False, status_code=400)
        out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "400" in out.get("error", "")
        assert out.get("error_type") == "api_error"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_non_json_response(self, mock_post, _mock_url, _mock_token):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("no json")
        mock_post.return_value = resp
        out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "non-json" in out.get("error", "").lower()
        assert out.get("error_type") == "empty_response"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_empty_data_array(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({"data": []})
        out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "no image data" in out.get("error", "").lower()
        assert out.get("error_type") == "empty_response"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_missing_b64_and_url(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _json_response({"data": [{}]})
        out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "neither b64_json nor url" in out.get("error", "").lower()
        assert out.get("error_type") == "empty_response"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_cache_save_failure(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_b64_response()
        with patch.object(ig, "save_b64_image", side_effect=OSError("disk full")):
            out = _provider_with_model().generate("a cat")
        assert out["success"] is False
        assert "cache" in out.get("error", "").lower()
        assert out.get("error_type") == "io_error"

    @patch("omniroute_plugin.providers.image_gen._resolve_token", return_value="sk-test")
    @patch("omniroute_plugin.providers.image_gen._resolve_base_url", return_value="https://api.example")
    @patch("requests.post")
    def test_url_cache_failure_warns_and_falls_back(self, mock_post, _mock_url, _mock_token):
        mock_post.return_value = _fake_url_response("https://omniroute.example/img.png")
        with patch.object(ig, "save_url_image", side_effect=OSError("disk full")):
            out = _provider_with_model().generate("a cat")
        assert out["success"] is True
        assert out["image"] == "https://omniroute.example/img.png"
