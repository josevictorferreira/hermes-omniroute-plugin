"""Tests for the Omniroute dashboard plugin API.

Validates GET /config and POST /config endpoints, including:
  - config reading with env override detection
  - config saving with nested dotted paths
  - error handling
"""

import asyncio
import importlib.util
import json
import os
import sys
import types
import urllib.error

import pytest


# Skip entire module when dashboard dependencies are not installed.
# CI installs only pytest requests; fastapi/pydantic optional here.
pytest.importorskip("fastapi")
pytest.importorskip("pydantic")


@pytest.fixture(autouse=True)
def _clear_omniroute_env(monkeypatch):
    """Ensure dashboard API tests don't inherit OMNIROUTE env vars from host."""
    for _key in [
        "OMNIROUTE_TOKEN",
        "OMNIROUTE_API_KEY",
        "OMNIROUTE_BASE_URL",
        "OMNIROUTE_IMAGE_MODEL",
        "OMNIROUTE_TTS_MODEL",
        "OMNIROUTE_SEARCH_PROVIDER",
    ]:
        monkeypatch.delenv(_key, raising=False)


# ---------------------------------------------------------------------------
# Helpers for building fresh mock configs
# ---------------------------------------------------------------------------

def make_mock_config(**sections):
    return dict(sections)


def _deep_get(obj, keys, default):
    current = obj
    for k in keys:
        if not isinstance(current, dict) or k not in current:
            return default
        current = current[k]
    return current


# ---------------------------------------------------------------------------
# Test fixture: import module with mocked hermes_cli.config
# ---------------------------------------------------------------------------

def load_plugin_api_with_config(mock_config=None, mock_save=None):
    """Import dashboard/plugin_api.py with a mock hermes_cli.config."""
    mock_cfg = mock_config or {}
    mock_save_fn = mock_save or (lambda c: None)

    config_mod = types.ModuleType("hermes_cli.config")
    config_mod.load_config = lambda: mock_cfg
    config_mod.save_config = mock_save_fn
    config_mod.cfg_get = lambda cfg, *keys, default=None: _deep_get(cfg, keys, default)
    sys.modules["hermes_cli.config"] = config_mod
    sys.modules["hermes_cli"] = sys.modules.get("hermes_cli", types.ModuleType("hermes_cli"))

    # Fresh FastAPI mock so APIRouter works as a real-ish object.
    class FakeRouter:
        def __init__(self):
            self.routes = []
        def get(self, *a, **kw):
            def decorator(fn):
                self.routes.append(("get", a, fn))
                return fn
            return decorator
        def post(self, *a, **kw):
            def decorator(fn):
                self.routes.append(("post", a, fn))
                return fn
            return decorator

    sys.modules.setdefault("fastapi", types.ModuleType("fastapi"))
    sys.modules["fastapi"].APIRouter = FakeRouter

    HERE = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "omniroute_dashboard_api", os.path.join(HERE, "..", "dashboard", "plugin_api.py")
    )
    api_mod = importlib.util.module_from_spec(spec)
    sys.modules["omniroute_dashboard_api"] = api_mod
    spec.loader.exec_module(api_mod)
    if hasattr(api_mod, "ConfigResponse"):
        api_mod.ConfigResponse.model_rebuild(_types_namespace=api_mod.__dict__)
    return api_mod


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutinefunction(coro) else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_returns_empty_config_when_no_config_yaml(self):
        api = load_plugin_api_with_config(mock_config={})
        resp = asyncio.run(api.get_config())
        assert resp.config == {
            "token": "",
            "base_url": "",
            "image_model": "",
            "tts_model": "",
            "search_provider": "",
            "model_provider_model": "",
        }
        assert resp.env_override == {
            "token": False,
            "base_url": False,
            "image_model": False,
            "tts_model": False,
            "search_provider": False,
            "model_provider_model": False,
        }

    def test_reads_config_values(self):
        cfg = {
            "image_gen": {
                "omniroute": {
                    "token": "test-token",
                    "base_url": "https://custom.example.com",
                    "model": "custom-image-model",
                }
            },
            "tts": {"omniroute": {"model": "custom-tts-model"}},
            "web": {"omniroute": {"search_provider": "tavily"}},
        }
        api = load_plugin_api_with_config(mock_config=cfg)
        resp = asyncio.run(api.get_config())
        assert resp.config["token"] == "test-token"
        assert resp.config["base_url"] == "https://custom.example.com"
        assert resp.config["image_model"] == "custom-image-model"
        assert resp.config["tts_model"] == "custom-tts-model"
        assert resp.config["search_provider"] == "tavily"

    def test_env_override_detected(self, monkeypatch):
        monkeypatch.setenv("OMNIROUTE_TOKEN", "env-token")
        monkeypatch.setenv("OMNIROUTE_BASE_URL", "https://env.example.com")
        api = load_plugin_api_with_config(mock_config={})
        resp = asyncio.run(api.get_config())
        assert resp.env_override["token"] is True
        assert resp.env_override["base_url"] is True
        assert resp.env_override["image_model"] is False


class TestPostConfig:
    def test_saves_values_to_config(self):
        saved = []
        def capture_save(config):
            saved.append(config)

        api = load_plugin_api_with_config(mock_config={}, mock_save=capture_save)
        body = api.OmnirouteConfig(
            token="new-token",
            base_url="https://new.example.com",
            image_model="new-image",
            tts_model="new-tts",
            search_provider="new-search",
        )
        resp = asyncio.run(api.post_config(body))
        assert resp.success is True
        assert len(saved) == 1
        config = saved[0]
        assert config["image_gen"]["omniroute"]["token"] == "new-token"
        assert config["image_gen"]["omniroute"]["base_url"] == "https://new.example.com"
        assert config["image_gen"]["omniroute"]["model"] == "new-image"
        assert config["tts"]["omniroute"]["model"] == "new-tts"
        assert config["web"]["omniroute"]["search_provider"] == "new-search"

    def test_does_not_overwrite_existing_sections(self):
        saved = []
        def capture_save(config):
            saved.append(config)

        initial = {
            "image_gen": {
                "omniroute": {
                    "token": "old-token",
                    "model": "old-image",
                    "other_key": "preserved",
                },
                "other_provider": "value",
            },
            "tts": {
                "omniroute": {"model": "old-tts"},
                "other_provider": "value",
            },
        }
        api = load_plugin_api_with_config(mock_config=initial, mock_save=capture_save)
        body = api.OmnirouteConfig(
            token="new-token",
            base_url="",
            image_model="new-image",
            tts_model="new-tts",
            search_provider="",
        )
        resp = asyncio.run(api.post_config(body))
        assert resp.success is True
        config = saved[0]
        assert config["image_gen"]["omniroute"]["other_key"] == "preserved"
        assert config["image_gen"]["other_provider"] == "value"
        assert config["tts"]["other_provider"] == "value"

    def test_empty_values_dont_remove_existing_keys(self):
        saved = []
        def capture_save(config):
            saved.append(config)

        initial = {
            "image_gen": {
                "omniroute": {
                    "token": "existing-token",
                    "base_url": "https://existing.example.com",
                }
            },
        }
        api = load_plugin_api_with_config(mock_config=initial, mock_save=capture_save)
        body = api.OmnirouteConfig(
            token="",
            base_url="",
            image_model="",
            tts_model="",
            search_provider="",
        )
        resp = asyncio.run(api.post_config(body))
        assert resp.success is True
        config = saved[0]
        assert config["image_gen"]["omniroute"]["token"] == "existing-token"
        assert config["image_gen"]["omniroute"]["base_url"] == "https://existing.example.com"

    def test_saves_only_non_empty_values(self):
        saved = []
        def capture_save(config):
            saved.append(config)

        api = load_plugin_api_with_config(mock_config={}, mock_save=capture_save)
        body = api.OmnirouteConfig(
            token="only-token",
            base_url="",
            image_model="",
            tts_model="",
            search_provider="",
        )
        resp = asyncio.run(api.post_config(body))
        assert resp.success is True
        config = saved[0]
        assert config["image_gen"]["omniroute"]["token"] == "only-token"
        assert "model" not in config["image_gen"]["omniroute"]



# ---------------------------------------------------------------------------
# Tests: GET /models endpoint
# ---------------------------------------------------------------------------

class TestGetModels:
    def _load_with_config(self, mock_config=None, mock_save=None):
        # Reuse the existing helper but need a fresh import
        old = sys.modules.pop("omniroute_dashboard_api", None)
        api = load_plugin_api_with_config(mock_config=mock_config, mock_save=mock_save)
        return api

    def test_returns_error_when_no_token(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        api = self._load_with_config(mock_config={})
        resp = asyncio.run(api.get_models())
        assert resp.models == []
        assert "token" in resp.error.lower()

    def test_returns_error_when_no_token_config_only(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        api = self._load_with_config(mock_config={
            "image_gen": {"omniroute": {"base_url": "https://omniroute.example.com"}},
        })
        resp = asyncio.run(api.get_models())
        assert resp.models == []
        assert "token" in resp.error.lower()

    def test_returns_error_when_api_unreachable(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        api = self._load_with_config(mock_config={
            "image_gen": {"omniroute": {"token": "test-token"}},
        })

        def fake_urlopen(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
        resp = asyncio.run(api.get_models())
        assert resp.models == []
        assert "refused" in resp.error or "Failed" in resp.error

    def test_returns_models_on_success(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        api = self._load_with_config(mock_config={
            "image_gen": {"omniroute": {"token": "test-token"}},
        })

        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        api_data = json.dumps({
            "data": [
                {"id": "openai/gpt-4o", "name": "GPT-4o"},
                {"id": "anthropic/claude-4", "name": "Claude 4", "owned_by": "anthropic"},
                {"id": "google/gemini-2.5-pro"},
            ]
        })

        monkeypatch.setattr(
            api.urllib.request, "urlopen",
            lambda *a, **kw: FakeResponse(api_data)
        )
        resp = asyncio.run(api.get_models())
        ids = [m.id for m in resp.models]
        assert "anthropic/claude-4" in ids
        assert "google/gemini-2.5-pro" in ids
        assert "openai/gpt-4o" in ids
        assert resp.error == ""

    def test_models_sorted_by_id(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        api = self._load_with_config(mock_config={
            "image_gen": {"omniroute": {"token": "test-token"}},
        })

        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        api_data = json.dumps({
            "data": [
                {"id": "z-model"},
                {"id": "a-model"},
                {"id": "m-model"},
            ]
        })

        monkeypatch.setattr(
            api.urllib.request, "urlopen",
            lambda *a, **kw: FakeResponse(api_data)
        )
        resp = asyncio.run(api.get_models())
        ids = [m.id for m in resp.models]
        assert ids == ["a-model", "m-model", "z-model"]

    def test_uses_env_token(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        monkeypatch.setenv("OMNIROUTE_TOKEN", "env-token")
        api = self._load_with_config(mock_config={})

        def fake_urlopen(*args, **kwargs):
            raise urllib.error.URLError("stop")

        monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
        resp = asyncio.run(api.get_models())
        # Should NOT say token required because env token is set
        assert "token required" not in resp.error.lower()

    def test_uses_env_base_url(self, monkeypatch):
        monkeypatch.setenv("OMNIROUTE_TOKEN", "env-token")
        monkeypatch.setenv("OMNIROUTE_BASE_URL", "https://custom.example.com")

        called_url = []
        api = self._load_with_config(mock_config={})

        # Capture the URL passed to Request
        original_request = api.urllib.request.Request

        class FakeRequest(original_request):
            def __init__(self, url, *args, **kwargs):
                called_url.append(url)
                # Do not call super().__init__; original may try to validate URL

        def fake_urlopen(*args, **kwargs):
            raise urllib.error.URLError("stop")

        monkeypatch.setattr(api.urllib.request, "Request", FakeRequest)
        monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
        asyncio.run(api.get_models())
        assert any("custom.example.com" in u for u in called_url)


# ---------------------------------------------------------------------------
# Tests: model_provider_model in config
# ---------------------------------------------------------------------------

class TestModelProviderConfig:
    def test_get_config_includes_model_provider_default(self):
        api = load_plugin_api_with_config(mock_config={})
        resp = asyncio.run(api.get_config())
        assert "model_provider_model" in resp.config
        assert resp.config["model_provider_model"] == ""

    def test_get_config_reads_model_provider_model(self):
        cfg = {
            "model": {"omniroute": {"default": "openai/gpt-4o"}},
        }
        api = load_plugin_api_with_config(mock_config=cfg)
        resp = asyncio.run(api.get_config())
        assert resp.config["model_provider_model"] == "openai/gpt-4o"

    def test_post_config_saves_model_provider_model(self):
        saved = {}

        def mock_save(c):
            saved.update(c)

        api = load_plugin_api_with_config(mock_config={}, mock_save=mock_save)

        class FakeBody:
            token = "test-token"
            base_url = "https://omniroute.josevictor.me"
            image_model = ""
            tts_model = ""
            search_provider = ""
            model_provider_model = "anthropic/claude-4"

        resp = asyncio.run(api.post_config(FakeBody()))
        assert resp.success is True
        # Verify it was saved to the right nested path
        model_section = saved.get("model", {})
        omniroute_section = model_section.get("omniroute", {})
        assert omniroute_section.get("default") == "anthropic/claude-4"

    def test_post_config_validation_requires_token(self):
        api = load_plugin_api_with_config(mock_config={})

        class FakeBody:
            token = ""
            base_url = "https://omniroute.josevictor.me"
            image_model = ""
            tts_model = ""
            search_provider = ""
            model_provider_model = "anthropic/claude-4"

        resp = asyncio.run(api.post_config(FakeBody()))
        assert resp.success is False
        assert "token" in resp.message.lower()

    def test_post_config_validation_requires_base_url(self):
        api = load_plugin_api_with_config(mock_config={})

        class FakeBody:
            token = "test-token"
            base_url = ""
            image_model = ""
            tts_model = ""
            search_provider = ""
            model_provider_model = "anthropic/claude-4"

        resp = asyncio.run(api.post_config(FakeBody()))
        assert resp.success is False
        assert "base url" in resp.message.lower() or "url" in resp.message.lower()

    def test_post_config_no_validation_without_model(self):
        saved = {}

        def mock_save(c):
            saved.update(c)

        api = load_plugin_api_with_config(mock_config={}, mock_save=mock_save)

        class FakeBody:
            token = ""
            base_url = ""
            image_model = ""
            tts_model = ""
            search_provider = ""
            model_provider_model = ""

        resp = asyncio.run(api.post_config(FakeBody()))
        assert resp.success is True

    def test_env_override_detected_for_model_provider(self, monkeypatch):
        monkeypatch.setenv("OMNIROUTE_MODEL", "test-model")
        api = load_plugin_api_with_config(mock_config={})
        resp = asyncio.run(api.get_config())
        assert resp.env_override.get("model_provider_model") is True
