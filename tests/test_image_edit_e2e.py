"""End-to-end tests for Omniroute image-editing integration.

These tests exercise the full `generate()` flow when ``image_url`` is supplied,
validating that the provider correctly routes to Omniroute's
``POST /v1/images/edits`` endpoint.

The current implementation does **not** yet support image editing; these tests
verify the gap and serve as the acceptance criteria for the feature.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import zlib
from unittest.mock import MagicMock, patch

import pytest

import omniroute_plugin as plugin
from omniroute_plugin.providers.image_gen import OmnirouteImageGenProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_REGISTRY = {
    "openai/gpt-image-2": {
        "id": "openai/gpt-image-2",
        "name": "GPT Image 2",
        "input_modalities": ["text", "image"],
        "supported_sizes": ["1024x1024", "1792x1024", "1024x1792"],
    },
    "together/black-forest-labs/FLUX.2-pro": {
        "id": "together/black-forest-labs/FLUX.2-pro",
        "name": "FLUX.2 Pro",
        "input_modalities": ["text", "image"],
        "supported_sizes": ["1024x1024"],
    },
    "stability-ai/inpaint": {
        "id": "stability-ai/inpaint",
        "name": "Inpaint",
        "input_modalities": ["image"],
    },
}


def _fake_post(*, ok=True, status_code=200, json_data=None, text_data=None, side_effect=None):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    resp.text = text_data or json.dumps(json_data or {})
    if side_effect:
        resp.raise_for_status.side_effect = side_effect
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> OmnirouteImageGenProvider:
    p = OmnirouteImageGenProvider()
    p._registry = SAMPLE_REGISTRY.copy()
    return p


@pytest.fixture(autouse=True)
def _env_token(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_TOKEN", "test-token-42")


# ---------------------------------------------------------------------------
# 1. Request formatting & endpoint routing
# ---------------------------------------------------------------------------

class TestImageEditRequestFormatting:
    """Verify that `generate(..., image_url=...)` produces the correct HTTP call."""

    def test_routes_to_images_edits_endpoint(self, provider):
        """When image_url is supplied the provider must POST to /images/edits."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        calls = mock_req.post.call_args_list
        assert len(calls) == 1
        url = calls[0][0][0]
        assert "/images/edits" in url, f"Expected /images/edits in URL, got {url}"

    def test_routes_to_images_generations_without_image(self, provider):
        """Without image_url the provider must continue to POST to /images/generations."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            provider.generate("a beautiful sunset", model="openai/gpt-image-2")

        calls = mock_req.post.call_args_list
        assert len(calls) == 1
        url = calls[0][0][0]
        assert "/images/generations" in url, f"Expected /images/generations in URL, got {url}"

    def test_edit_payload_includes_prompt_and_image(self, provider):
        """The edits payload must contain prompt and image fields."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            provider.generate(
                "make it blue",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        payload = mock_req.post.call_args[1]["json"]
        assert payload["prompt"] == "make it blue"
        assert "image" in payload, "Payload must include 'image' field"

    def test_edit_includes_auth_header(self, provider):
        """The edits request must carry the Bearer token."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            provider.generate(
                "make it blue",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        headers = mock_req.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test-token-42"


# ---------------------------------------------------------------------------
# 2. Response handling
# ---------------------------------------------------------------------------

class TestImageEditResponseHandling:
    """Verify successful and partial responses from /images/edits."""

    def test_successful_b64_response_returns_success_with_modality_image(self, provider):
        """A valid b64_json response must be surfaced as success with modality 'image'."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={
                "data": [{"b64_json": base64.b64encode(b"pngdata").decode(), "revised_prompt": "Revised: sun added"}]
            }
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is True
        assert result.get("modality") == "image", (
            f"Expected modality='image' for image edits, got modality={result.get('modality')!r}"
        )
        assert result.get("provider") == "omniroute"

    @pytest.mark.xfail(reason="image editing not yet implemented (valygar t_dcadc0a7)", strict=False)
    def test_successful_url_response(self, provider):
        """A valid URL response must be surfaced as success."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={
                "data": [{"url": "https://cdn.example.com/img.png", "revised_prompt": "revised"}]
            }
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is True
        assert result.get("image") == "https://cdn.example.com/img.png"

    def test_empty_data_returns_error(self, provider):
        """An empty data array must produce a clear error response."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(json_data={"data": []})
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is False

    def test_non_json_response_returns_error(self, provider):
        """Non-JSON bodies must be handled gracefully."""
        mock_req = MagicMock()
        resp = _fake_post(ok=True)
        resp.json.side_effect = ValueError("not json")
        resp.text = "<html>bad gateway</html>"
        resp.status_code = 200
        mock_req.post.return_value = resp
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is False

    def test_missing_both_b64_and_url_returns_error(self, provider):
        """When neither b64_json nor url is present, provider must error."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(json_data={"data": [{}]})
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is False


# ---------------------------------------------------------------------------
# 3. Authentication & error scenarios
# ---------------------------------------------------------------------------

class TestImageEditAuthAndErrors:
    """Verify auth, missing deps, and API error paths."""

    def test_missing_token_returns_auth_error(self, monkeypatch):
        """Without OMNIROUTE_TOKEN the provider must reject immediately."""
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)

        provider = OmnirouteImageGenProvider()
        provider._registry = SAMPLE_REGISTRY.copy()

        mock_req = MagicMock()
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate("add a sun", image_url="/path/to/image.png")

        assert result["success"] is False
        assert result.get("error_type") == "auth_required"
        mock_req.post.assert_not_called()

    def test_http_4xx_returns_api_error(self, provider):
        """A 4xx from Omniroute must be surfaced with the response body."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            ok=False,
            status_code=400,
            text_data=json.dumps({"error": {"message": "Image edit not supported by built-in provider"}}),
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is False
        assert result.get("error_type") == "api_error"

    def test_http_5xx_returns_api_error(self, provider):
        """A 5xx from Omniroute must be surfaced as api_error."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(ok=False, status_code=503, text_data="Service Unavailable")
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is False
        assert result.get("error_type") == "api_error"

    def test_network_error_returns_api_error(self, provider):
        """Connection failures must produce api_error, not an unhandled exception."""
        mock_req = MagicMock()
        mock_req.post.side_effect = ConnectionError("Network unreachable")
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate(
                "add a sun",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        assert result["success"] is False
        assert result.get("error_type") == "api_error"


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------

class TestImageEditEdgeCases:
    """Boundary conditions for image-editing requests."""

    def test_empty_prompt_rejected(self, provider):
        """Empty prompt must be rejected before any HTTP call."""
        mock_req = MagicMock()
        with patch.dict("sys.modules", {"requests": mock_req}):
            result = provider.generate("", image_url="/path/to/image.png")

        assert result["success"] is False
        assert result.get("error_type") == "invalid_argument"
        mock_req.post.assert_not_called()

    def test_reference_images_are_included(self, provider):
        """reference_image_urls should be forwarded to the edits endpoint."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            provider.generate(
                "match this style",
                image_url="/path/to/image.png",
                reference_image_urls=["/path/ref1.png", "/path/ref2.png"],
                model="openai/gpt-image-2",
            )

        calls = mock_req.post.call_args_list
        assert len(calls) == 1

    def test_capabilities_advertises_image_modality(self, provider):
        """Once editing is supported, capabilities() should advertise 'image' modality."""
        caps = provider.capabilities()
        assert "image" in caps.get("modalities", []), (
            "Provider must advertise 'image' modality so Hermes knows edits are supported."
        )

    def test_size_is_resolved_for_edit_requests(self, provider):
        """The provider should still resolve size/aspect for edit requests."""
        mock_req = MagicMock()
        mock_req.post.return_value = _fake_post(
            json_data={"data": [{"b64_json": base64.b64encode(b"hello").decode()}]}
        )
        with patch.dict("sys.modules", {"requests": mock_req}):
            provider.generate(
                "make it wider",
                aspect_ratio="landscape",
                image_url="/path/to/image.png",
                model="openai/gpt-image-2",
            )

        payload = mock_req.post.call_args[1]["json"]
        assert "size" in payload
