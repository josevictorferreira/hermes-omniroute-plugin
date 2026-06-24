"""STT (transcription) provider tests: identity, model/token resolution,
setup schema, model catalog, and transcribe() over mocked HTTP.

Token/model resolution read env vars directly, so tests set them via
``patch.dict(os.environ, ...)`` rather than patching the resolver (which
``stt.py`` binds at import time).
"""
import os
from unittest.mock import patch, MagicMock

import pytest

from omniroute_plugin.config import DEFAULT_STT_MODEL, _resolve_stt_model, _resolve_stt_token
from omniroute_plugin.providers.stt import OmnirouteSTTProvider, _STT_MODEL_CATALOG


def _make_provider():
    return OmnirouteSTTProvider()


def _mock_response(*, ok=True, json_data=None, status_code=200, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "clip.mp3"
    p.write_bytes(b"FAKE-AUDIO-BYTES")
    return str(p)


# ---------------------------------------------------------------------------
# Identity / availability
# ---------------------------------------------------------------------------

class TestProviderIdentity:
    def test_name(self):
        assert _make_provider().name == "omniroute"

    def test_display_name(self):
        assert _make_provider().display_name == "Omniroute"

    def test_is_available_with_requests(self):
        assert _make_provider().is_available() is True

    def test_setup_schema_shape(self):
        schema = _make_provider().get_setup_schema()
        assert schema["name"] == "Omniroute"
        assert schema["env_vars"][0]["key"] == "OMNIROUTE_API_KEY"


# ---------------------------------------------------------------------------
# Model catalog / resolution
# ---------------------------------------------------------------------------

class TestModels:
    def test_list_models_returns_catalog_copy(self):
        models = _make_provider().list_models()
        assert [m["id"] for m in models] == [m["id"] for m in _STT_MODEL_CATALOG]
        # Mutating the returned list must not affect the module catalog.
        models.clear()
        assert _STT_MODEL_CATALOG

    @patch.dict(os.environ, {}, clear=True)
    def test_default_model_falls_back_to_constant(self):
        assert _make_provider().default_model() == DEFAULT_STT_MODEL

    @patch.dict(os.environ, {"OMNIROUTE_STT_MODEL": "custom/model"}, clear=True)
    def test_resolve_model_explicit_arg_wins(self):
        assert _resolve_stt_model("arg/model") == "arg/model"

    @patch.dict(os.environ, {"OMNIROUTE_STT_MODEL": "env/model"}, clear=True)
    def test_resolve_model_env(self):
        assert _resolve_stt_model() == "env/model"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "shared-key"}, clear=True)
    def test_resolve_token_from_shared_env(self):
        assert _resolve_stt_token() == "shared-key"

    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_token_none_when_unset(self):
        assert _resolve_stt_token() is None


# ---------------------------------------------------------------------------
# transcribe()
# ---------------------------------------------------------------------------

class TestTranscribe:
    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_success(self, post, audio_file):
        post.return_value = _mock_response(json_data={"text": "  hello world  ", "language": "en"})
        result = _make_provider().transcribe(audio_file, model="deepgram/nova-3", language="en")
        assert result == {"success": True, "transcript": "hello world", "provider": "omniroute"}
        # Verify multipart shape: model + language in data, file uploaded.
        _, kwargs = post.call_args
        assert kwargs["data"]["model"] == "deepgram/nova-3"
        assert kwargs["data"]["language"] == "en"
        assert "file" in kwargs["files"]

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_token(self, audio_file):
        result = _make_provider().transcribe(audio_file)
        assert result["success"] is False
        assert "OMNIROUTE_API_KEY" in result["error"]
        assert result["provider"] == "omniroute"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_http_error(self, post, audio_file):
        post.return_value = _mock_response(ok=False, status_code=401, text="unauthorized")
        result = _make_provider().transcribe(audio_file)
        assert result["success"] is False
        assert "HTTP 401" in result["error"]

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post")
    def test_empty_transcript(self, post, audio_file):
        post.return_value = _mock_response(json_data={"text": "   "})
        result = _make_provider().transcribe(audio_file)
        assert result["success"] is False
        assert "empty transcript" in result["error"]

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    @patch("requests.post", side_effect=RuntimeError("boom"))
    def test_request_exception_is_caught(self, post, audio_file):
        result = _make_provider().transcribe(audio_file)
        assert result["success"] is False
        assert "boom" in result["error"]

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key"}, clear=True)
    def test_missing_file_does_not_raise(self):
        result = _make_provider().transcribe("/nonexistent/clip.mp3")
        assert result["success"] is False
        assert result["provider"] == "omniroute"
