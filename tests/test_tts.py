"""TTS provider tests: identity, voice catalog, model/token resolution,
setup schema, and synthesize()/list_models() over mocked HTTP.

Merged from the former ``test_tts_provider.py`` (register() coverage moved to
``test_register.py``).
"""
import os
from unittest.mock import patch, MagicMock

import pytest

from omniroute_plugin.config import DEFAULT_TTS_MODEL, _resolve_tts_model, _resolve_tts_token
from omniroute_plugin.providers.tts import OmnirouteTTSProvider, _TTS_VOICE_CATALOG


def _make_provider():
    return OmnirouteTTSProvider()


def _mock_response(*, ok=True, content=b"FAKE-AUDIO", status_code=200, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.content = content
    resp.status_code = status_code
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Unit: identity / availability
# ---------------------------------------------------------------------------

class TestProviderIdentity:
    def test_name(self):
        assert _make_provider().name == "omniroute"

    def test_display_name(self):
        assert _make_provider().display_name == "Omniroute"

    @patch.dict(os.environ, {}, clear=False)
    def test_is_available_true_without_token(self):
        """is_available works without a token — only checks requests importable."""
        # Ensure no token in env
        env = {k: v for k, v in os.environ.items()
               if k not in ("OMNIROUTE_TOKEN", "OMNIROUTE_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            assert _make_provider().is_available() is True

    @patch.dict(os.environ, {}, clear=True)
    def test_is_available_false_when_requests_missing(self):
        """is_available returns False when requests cannot be imported."""
        import builtins
        real_import = builtins.__import__

        def _block_requests(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_block_requests):
            assert _make_provider().is_available() is False


# ---------------------------------------------------------------------------
# Unit: voice catalog
# ---------------------------------------------------------------------------

class TestVoiceCatalog:
    def test_list_voices_returns_six_entries(self):
        voices = _make_provider().list_voices()
        assert len(voices) == 6

    def test_list_voices_entries_have_id_and_name(self):
        for v in _make_provider().list_voices():
            assert "id" in v
            assert "name" in v

    def test_list_voices_returns_copies(self):
        """Mutating returned list must not affect the module-level catalog."""
        voices = _make_provider().list_voices()
        voices.clear()
        assert len(_TTS_VOICE_CATALOG) == 6  # original unchanged

    def test_list_voices_contains_known_ids(self):
        ids = {v["id"] for v in _make_provider().list_voices()}
        assert ids == {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def test_default_voice(self):
        assert _make_provider().default_voice() == "alloy"


# ---------------------------------------------------------------------------
# Unit: model resolution
# ---------------------------------------------------------------------------

class TestModelResolution:
    @patch.dict(os.environ, {}, clear=True)
    def test_default_model_from_constant(self):
        assert _resolve_tts_model() == DEFAULT_TTS_MODEL

    @patch.dict(os.environ, {"OMNIROUTE_TTS_MODEL": "openai/tts-1-hd"}, clear=True)
    def test_env_override(self):
        assert _resolve_tts_model() == "openai/tts-1-hd"

    @patch.dict(os.environ, {"OMNIROUTE_TTS_MODEL": "custom/model"}, clear=True)
    def test_explicit_arg_beats_env(self):
        assert _resolve_tts_model("arg/model") == "arg/model"

    @patch.dict(os.environ, {"OMNIROUTE_TTS_MODEL": "  "}, clear=True)
    def test_blank_env_falls_through(self):
        assert _resolve_tts_model() == DEFAULT_TTS_MODEL

    @patch.dict(os.environ, {}, clear=True)
    def test_none_arg_uses_default(self):
        assert _resolve_tts_model(None) == DEFAULT_TTS_MODEL

    def test_default_model_method(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _make_provider().default_model() == DEFAULT_TTS_MODEL


# ---------------------------------------------------------------------------
# Unit: token resolution
# ---------------------------------------------------------------------------

class TestTokenResolution:
    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-123"}, clear=True)
    def test_env_token(self):
        assert _resolve_tts_token() == "tok-123"

    @patch.dict(os.environ, {"OMNIROUTE_API_KEY": "key-456"}, clear=True)
    def test_api_key_alias(self):
        assert _resolve_tts_token() == "key-456"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok", "OMNIROUTE_API_KEY": "key"}, clear=True)
    def test_token_takes_precedence_over_api_key(self):
        assert _resolve_tts_token() == "tok"

    @patch.dict(os.environ, {}, clear=True)
    def test_no_token_returns_none(self):
        assert _resolve_tts_token() is None


# ---------------------------------------------------------------------------
# Unit: setup schema
# ---------------------------------------------------------------------------

class TestSetupSchema:
    def test_schema_has_required_keys(self):
        schema = _make_provider().get_setup_schema()
        assert schema["name"] == "Omniroute"
        assert "badge" in schema
        assert "tag" in schema
        assert "env_vars" in schema

    def test_schema_env_vars_has_omniroute_token(self):
        schema = _make_provider().get_setup_schema()
        keys = [ev["key"] for ev in schema["env_vars"]]
        assert "OMNIROUTE_TOKEN" in keys


# ---------------------------------------------------------------------------
# Integration: synthesize() with mocked HTTP
# ---------------------------------------------------------------------------

class TestSynthesize:
    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_success_writes_audio_and_returns_path(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response(content=b"AUDIO-BYTES")
        out = str(tmp_path / "out.mp3")
        provider = _make_provider()

        result = provider.synthesize("Hello world", out)

        assert result == os.path.abspath(out)
        with open(out, "rb") as f:
            assert f.read() == b"AUDIO-BYTES"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_payload_has_required_fields(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize("Hello", str(tmp_path / "o.mp3"))

        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["model"] == DEFAULT_TTS_MODEL
        assert payload["input"] == "Hello"
        assert payload["voice"] == "alloy"
        assert payload["response_format"] == "mp3"
        # speed must NOT be present when not explicitly provided
        assert "speed" not in payload

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_speed_included_when_provided(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize("Hello", str(tmp_path / "o.mp3"), speed=1.5)

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["speed"] == 1.5

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_custom_voice_and_model(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize(
            "Hello", str(tmp_path / "o.mp3"),
            voice="nova", model="openai/tts-1-hd",
        )

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["voice"] == "nova"
        assert kwargs["json"]["model"] == "openai/tts-1-hd"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_format_clamped_to_mp3_for_unsupported(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize("Hello", str(tmp_path / "o.mp3"), format="wav")

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["response_format"] == "mp3"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_format_opus_passed_through(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize("Hello", str(tmp_path / "o.opus"), format="opus")

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["response_format"] == "opus"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_posts_to_audio_speech_endpoint(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize("Hello", str(tmp_path / "o.mp3"))

        url = mock_post.call_args[0][0]
        assert url.endswith("/audio/speech")

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_authorization_header(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response()
        provider = _make_provider()
        provider.synthesize("Hello", str(tmp_path / "o.mp3"))

        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tok-test"

    def test_empty_text_raises_value_error(self, tmp_path):
        provider = _make_provider()
        with pytest.raises(ValueError):
            provider.synthesize("", str(tmp_path / "o.mp3"))

    def test_whitespace_text_raises_value_error(self, tmp_path):
        provider = _make_provider()
        with pytest.raises(ValueError):
            provider.synthesize("   ", str(tmp_path / "o.mp3"))

    @patch.dict(os.environ, {}, clear=True)
    def test_no_token_raises_runtime_error(self, tmp_path):
        provider = _make_provider()
        with pytest.raises(RuntimeError, match="OMNIROUTE_TOKEN"):
            provider.synthesize("Hello", str(tmp_path / "o.mp3"))

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_http_error_raises_runtime_error(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response(
            ok=False, status_code=500, text="Internal Server Error",
        )
        provider = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP 500"):
            provider.synthesize("Hello", str(tmp_path / "o.mp3"))

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_empty_audio_response_raises(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response(content=b"")
        provider = _make_provider()
        with pytest.raises(RuntimeError, match="empty audio"):
            provider.synthesize("Hello", str(tmp_path / "o.mp3"))

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_network_exception_raises_runtime_error(self, mock_post, tmp_path):
        mock_post.side_effect = ConnectionError("network down")
        provider = _make_provider()
        with pytest.raises(RuntimeError, match="request failed"):
            provider.synthesize("Hello", str(tmp_path / "o.mp3"))

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.post")
    def test_creates_parent_directory(self, mock_post, tmp_path):
        mock_post.return_value = _mock_response(content=b"X")
        provider = _make_provider()
        nested = str(tmp_path / "deep" / "nested" / "dir" / "out.mp3")
        provider.synthesize("Hello", nested)
        assert os.path.isfile(nested)


# ---------------------------------------------------------------------------
# Integration: list_models() with mocked HTTP
# ---------------------------------------------------------------------------

class TestListModels:
    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.get")
    def test_filters_tts_models(self, mock_get):
        mock_get.return_value = _mock_response_with_json({
            "data": [
                {"id": "openai/tts-1", "name": "TTS 1"},
                {"id": "openai/tts-1-hd", "name": "TTS 1 HD"},
                {"id": "openai/gpt-4o", "name": "GPT-4o"},
                {"id": "meta/llama-3", "name": "Llama 3"},
                {"id": "elevenlabs/speech-v2", "name": "Speech v2"},
            ]
        })
        models = _make_provider().list_models()
        ids = [m["id"] for m in models]
        assert "openai/tts-1" in ids
        assert "openai/tts-1-hd" in ids
        assert "elevenlabs/speech-v2" in ids
        assert "openai/gpt-4o" not in ids
        assert "meta/llama-3" not in ids

    @patch.dict(os.environ, {}, clear=True)
    def test_no_token_returns_empty(self):
        assert _make_provider().list_models() == []

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response(ok=False, status_code=401)
        assert _make_provider().list_models() == []

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.get")
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = ConnectionError("down")
        assert _make_provider().list_models() == []

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.get")
    def test_empty_data_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response_with_json({"data": []})
        assert _make_provider().list_models() == []

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "tok-test"}, clear=True)
    @patch("requests.get")
    def test_list_models_uses_models_endpoint(self, mock_get):
        mock_get.return_value = _mock_response_with_json({"data": []})
        _make_provider().list_models()
        url = mock_get.call_args[0][0]
        assert url.endswith("/models")


def _mock_response_with_json(json_data):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = json_data
    return resp
