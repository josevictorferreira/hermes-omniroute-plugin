"""Video-generation provider tests: identity, model/token resolution,
setup schema, model catalog, and generate() over mocked HTTP.

Token/model resolution reads env vars directly; tests set them via
``patch.dict(os.environ, ...)`` rather than patching resolvers (which
``video_gen.py`` binds at import time).
"""

import os
from unittest.mock import patch, MagicMock


from omniroute_plugin.config import (
    DEFAULT_VIDEO_MODEL,
    _resolve_video_model,
    _resolve_token,
)
from omniroute_plugin.providers.video_gen import OmnirouteVideoGenProvider


def _make_provider():
    return OmnirouteVideoGenProvider()


def _mock_response(*, ok=True, json_data=None, status_code=200, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


# ---------------------------------------------------------------------------
# Identity / availability
# ---------------------------------------------------------------------------


class TestProviderIdentity:
    def test_name(self):
        assert _make_provider().name == "omniroute"

    def test_display_name(self):
        assert _make_provider().display_name == "Omniroute"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    def test_is_available_with_requests(self):
        assert _make_provider().is_available() is True

    @patch.dict(os.environ, {}, clear=True)
    def test_is_available_without_token(self):
        assert _make_provider().is_available() is False

    def test_setup_schema_shape(self):
        schema = _make_provider().get_setup_schema()
        assert schema["name"] == "Omniroute"
        assert schema["env_vars"][0]["key"] == "OMNIROUTE_API_KEY"


# ---------------------------------------------------------------------------
# Model catalog / resolution
# ---------------------------------------------------------------------------


class TestModels:
    def test_list_models_returns_catalog_copy(self):
        provider = _make_provider()
        first = provider.list_models()
        first.clear()
        # Mutating the returned list must not affect subsequent calls.
        assert provider.list_models()  # still non-empty

    @patch.dict(os.environ, {}, clear=True)
    def test_default_model_falls_back_to_constant(self):
        assert _make_provider().default_model() == DEFAULT_VIDEO_MODEL

    @patch.dict(os.environ, {"OMNIROUTE_VIDEO_MODEL": "custom/model"}, clear=True)
    def test_resolve_model_explicit_arg_wins(self):
        assert _resolve_video_model("arg/model") == "arg/model"

    @patch.dict(os.environ, {"OMNIROUTE_VIDEO_MODEL": "env/model"}, clear=True)
    def test_resolve_model_env(self):
        assert _resolve_video_model() == "env/model"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "shared-key"}, clear=True)
    def test_resolve_token_from_shared_env(self):
        assert _resolve_token() == "shared-key"

    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_token_none_when_unset(self):
        assert _resolve_token() is None


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


class TestGenerate:
    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_success_b64(self, post):
        post.return_value = _mock_response(
            json_data={"created": 1, "data": [{"b64_json": "AAAA", "format": "mp4"}]}
        )
        result = _make_provider().generate("a cat playing piano", model="runway/gen-3")
        assert result["success"] is True
        assert result["video"] == "/tmp/test_video.mp4"
        assert result["model"] == "runway/gen-3"
        assert result["modality"] == "text"
        assert result["provider"] == "omniroute"
        # Verify request shape: URL + payload.
        _, kwargs = post.call_args
        assert kwargs["json"]["model"] == "runway/gen-3"
        assert kwargs["json"]["prompt"] == "a cat playing piano"
        assert kwargs["json"]["aspect_ratio"] == "16:9"
        assert "image_url" not in kwargs["json"]
        assert kwargs["headers"]["Authorization"] == "Bearer key"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_success_url(self, post):
        post.return_value = _mock_response(
            json_data={"data": [{"url": "https://x.test/v.mp4", "format": "mp4"}]}
        )
        result = _make_provider().generate("sunset", model="runway/gen-3")
        assert result["success"] is True
        assert result["video"] == "https://x.test/v.mp4"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_image_to_video_modality(self, post):
        post.return_value = _mock_response(
            json_data={"data": [{"b64_json": "AAAA", "format": "mp4"}]}
        )
        result = _make_provider().generate(
            "zoom in", model="runway/gen-3", image_url="https://x.test/img.png"
        )
        assert result["success"] is True
        assert result["modality"] == "image"
        _, kwargs = post.call_args
        assert kwargs["json"]["image_url"] == "https://x.test/img.png"

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_token(self):
        result = _make_provider().generate("anything", model="runway/gen-3")
        assert result["success"] is False
        assert "OMNIROUTE_API_KEY" in result["error"]
        assert result["provider"] == "omniroute"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    def test_empty_prompt(self):
        result = _make_provider().generate("   ", model="runway/gen-3")
        assert result["success"] is False
        assert "prompt" in result["error"]

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_http_error(self, post):
        post.return_value = _mock_response(
            ok=False, status_code=401, text="unauthorized"
        )
        result = _make_provider().generate("a cat", model="runway/gen-3")
        assert result["success"] is False
        assert "HTTP 401" in result["error"]

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_no_data(self, post):
        post.return_value = _mock_response(json_data={"data": []})
        result = _make_provider().generate("a cat", model="runway/gen-3")
        assert result["success"] is False
        assert "no video" in result["error"].lower()

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post", side_effect=RuntimeError("boom"))
    def test_request_exception_is_caught(self, post):
        result = _make_provider().generate("a cat", model="runway/gen-3")
        assert result["success"] is False
        assert "boom" in result["error"]
