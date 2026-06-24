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
        def put(self, *a, **kw):
            def decorator(fn):
                self.routes.append(("put", fn))
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


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestModelCapabilities:
    """GET /models?capability=… routes to the right catalog and filters it."""

    def _api(self, monkeypatch):
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        sys.modules.pop("omniroute_dashboard_api", None)
        return load_plugin_api_with_config(
            mock_config={"image_gen": {"omniroute": {"token": "t"}}}
        )

    def test_normalize_root_strips_known_suffixes(self, monkeypatch):
        api = self._api(monkeypatch)
        assert api._normalize_root("https://x.com/api/v1") == "https://x.com"
        assert api._normalize_root("https://x.com/v1/") == "https://x.com"
        assert api._normalize_root("https://x.com/api") == "https://x.com"
        assert api._normalize_root("https://x.com") == "https://x.com"

    def test_image_capability_hits_images_endpoint(self, monkeypatch):
        api = self._api(monkeypatch)
        urls = []

        class FakeRequest:
            def __init__(self, url, *a, **kw):
                urls.append(url)

        monkeypatch.setattr(api.urllib.request, "Request", FakeRequest)
        monkeypatch.setattr(
            api.urllib.request, "urlopen",
            lambda *a, **kw: _FakeResp(json.dumps({"data": [{"id": "openai/gpt-image-2"}]})),
        )
        resp = asyncio.run(api.get_models(capability="image"))
        assert any(u.endswith("/v1/images/generations") for u in urls), urls
        assert [m.id for m in resp.models] == ["openai/gpt-image-2"]

    def test_tts_capability_filters_by_keyword(self, monkeypatch):
        api = self._api(monkeypatch)
        urls = []

        class FakeRequest:
            def __init__(self, url, *a, **kw):
                urls.append(url)

        data = json.dumps({"data": [
            {"id": "openai/tts-1"},
            {"id": "gemini/flash-tts-preview"},
            {"id": "openai/gpt-4o"},
            {"id": "anthropic/claude-4"},
        ]})
        monkeypatch.setattr(api.urllib.request, "Request", FakeRequest)
        monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **kw: _FakeResp(data))
        resp = asyncio.run(api.get_models(capability="tts"))
        ids = [m.id for m in resp.models]
        assert ids == ["gemini/flash-tts-preview", "openai/tts-1"]
        assert any(u.endswith("/v1/models") for u in urls), urls

    def test_chat_capability_excludes_non_chat_types(self, monkeypatch):
        api = self._api(monkeypatch)
        data = json.dumps({"data": [
            {"id": "openai/gpt-4o"},
            {"id": "openai/tts-1", "type": "audio"},
            {"id": "openai/gpt-image-2", "type": "image"},
            {"id": "cohere/embed", "type": "embedding"},
        ]})
        monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **kw: _FakeResp(data))
        resp = asyncio.run(api.get_models(capability="chat"))
        assert [m.id for m in resp.models] == ["openai/gpt-4o"]

    def test_models_use_settings_store_token(self, monkeypatch):
        """A token saved via /settings (omniroute.settings.api_key) is used."""
        monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
        sys.modules.pop("omniroute_dashboard_api", None)
        api = load_plugin_api_with_config(
            mock_config={"omniroute": {"settings": {"api_key": "store-key"}}}
        )
        monkeypatch.setattr(
            api.urllib.request, "urlopen",
            lambda *a, **kw: _FakeResp(json.dumps({"data": []})),
        )
        resp = asyncio.run(api.get_models(capability="chat"))
        assert resp.error == ""

    def test_settings_base_url_normalized_no_double_v1(self, monkeypatch):
        """A base_url ending in /api/v1 must not yield /api/v1/v1/models."""
        monkeypatch.delenv("OMNIROUTE_BASE_URL", raising=False)
        monkeypatch.setenv("OMNIROUTE_TOKEN", "t")
        sys.modules.pop("omniroute_dashboard_api", None)
        api = load_plugin_api_with_config(
            mock_config={"omniroute": {"settings": {"base_url": "https://x.com/api/v1"}}}
        )
        urls = []

        class FakeRequest:
            def __init__(self, url, *a, **kw):
                urls.append(url)

        monkeypatch.setattr(api.urllib.request, "Request", FakeRequest)
        monkeypatch.setattr(
            api.urllib.request, "urlopen",
            lambda *a, **kw: _FakeResp(json.dumps({"data": []})),
        )
        asyncio.run(api.get_models(capability="chat"))
        assert urls == ["https://x.com/v1/models"], urls


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
            "model": {"omniroute": {"model": "openai/gpt-4o"}},
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
        # Verify it was saved to the documented nested path (model.omniroute.model)
        model_section = saved.get("model", {})
        omniroute_section = model_section.get("omniroute", {})
        assert omniroute_section.get("model") == "anthropic/claude-4"

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

    def test_post_config_base_url_not_required(self):
        """base_url has a default, so a blank base_url must not block a save."""
        saved = {}

        def mock_save(c):
            saved.update(c)

        api = load_plugin_api_with_config(mock_config={}, mock_save=mock_save)

        class FakeBody:
            token = "test-token"
            base_url = ""
            image_model = ""
            tts_model = ""
            search_provider = ""
            model_provider_model = "anthropic/claude-4"

        resp = asyncio.run(api.post_config(FakeBody()))
        assert resp.success is True

    def test_post_config_validation_passes_with_resolved_token(self):
        """A token from the settings store satisfies validation even when the
        request body omits it (connection is managed via /settings)."""
        saved = {}

        def mock_save(c):
            saved.update(c)

        api = load_plugin_api_with_config(
            mock_config={"omniroute": {"settings": {"api_key": "store-key"}}},
            mock_save=mock_save,
        )

        class FakeBody:
            token = ""
            base_url = ""
            image_model = ""
            tts_model = ""
            search_provider = ""
            model_provider_model = "anthropic/claude-4"

        resp = asyncio.run(api.post_config(FakeBody()))
        assert resp.success is True
        assert saved["model"]["omniroute"]["model"] == "anthropic/claude-4"

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

# ---------------------------------------------------------------------------
# Provider settings endpoint tests (api_key + base_url only)
# ---------------------------------------------------------------------------

class TestSettingsEndpoints:
    """Tests for the limited OmniRoute provider settings API surface.

    Only ``api_key`` and ``base_url`` are exposed through
    GET/PUT /settings.  TTS model, image model, search provider and
    model-provider selection must remain untouched by these endpoints.
    """

    def test_get_settings_returns_defaults_when_empty(self):
        api = load_plugin_api_with_config(mock_config={})
        resp = asyncio.run(api.get_settings())
        assert resp.settings.api_key == ""
        assert resp.settings.base_url == "https://omniroute.josevictor.me"
        assert resp.has_env_override == {"api_key": False, "base_url": False}

    def test_get_settings_reads_from_settings_store(self):
        api = load_plugin_api_with_config(mock_config={
            "omniroute": {
                "settings": {
                    "api_key": "sk-test-key",
                    "base_url": "https://custom.example.com/api/v1",
                }
            }
        })
        resp = asyncio.run(api.get_settings())
        assert resp.settings.base_url == "https://custom.example.com/api/v1"
        # API key should be masked in the response.
        assert resp.settings.api_key.startswith("sk-t")
        assert resp.settings.api_key.endswith("-key")
        assert "***" in resp.settings.api_key

    def test_get_settings_env_vars_override_and_flagged(self, monkeypatch):
        monkeypatch.setenv("OMNIROUTE_TOKEN", "env-token")
        monkeypatch.setenv("OMNIROUTE_BASE_URL", "https://env.example.com")
        api = load_plugin_api_with_config(mock_config={
            "omniroute": {
                "settings": {
                    "api_key": "stored-key",
                    "base_url": "https://stored.example.com",
                }
            }
        })
        resp = asyncio.run(api.get_settings())
        assert resp.settings.api_key.startswith("env-")
        assert resp.settings.base_url == "https://env.example.com"
        assert resp.has_env_override == {"api_key": True, "base_url": True}

    def test_put_settings_saves_api_key_and_base_url(self):
        saved = {}
        def mock_save(cfg):
            saved.clear()
            saved.update(cfg)

        api = load_plugin_api_with_config(mock_config={}, mock_save=mock_save)
        body = api.OmniRouteProviderSettings(
            api_key="sk-new-key",
            base_url="https://new.example.com/api/v1/",
        )
        resp = asyncio.run(api.put_settings(body))
        assert resp.success is True
        assert resp.message == "Settings saved."
        assert resp.settings.api_key == "sk-new-key"
        assert resp.settings.base_url == "https://new.example.com/api/v1"

        # Verify persisted structure.
        assert saved["omniroute"]["settings"]["api_key"] == "sk-new-key"
        assert saved["omniroute"]["settings"]["base_url"] == "https://new.example.com/api/v1"

    def test_put_settings_blank_values_preserve_existing(self):
        saved = {}
        def mock_save(cfg):
            saved.clear()
            saved.update(cfg)

        api = load_plugin_api_with_config(mock_config={
            "omniroute": {
                "settings": {
                    "api_key": "existing-key",
                    "base_url": "https://existing.example.com",
                }
            }
        }, mock_save=mock_save)
        body = api.OmniRouteProviderSettings(api_key="", base_url="")
        resp = asyncio.run(api.put_settings(body))
        assert resp.success is True
        assert saved["omniroute"]["settings"]["api_key"] == "existing-key"
        assert saved["omniroute"]["settings"]["base_url"] == "https://existing.example.com"

    def test_put_settings_rejects_extra_fields(self):
        api = load_plugin_api_with_config(mock_config={})
        with pytest.raises(Exception):
            # Pydantic with ConfigDict(extra="forbid") must reject unknown fields.
            api.OmniRouteProviderSettings(
                api_key="sk-key",
                base_url="https://x.com",
                tts_model="tts-1",
            )

    def test_put_settings_does_not_touch_other_config(self):
        saved = {}
        def mock_save(cfg):
            saved.clear()
            saved.update(cfg)

        api = load_plugin_api_with_config(mock_config={
            "image_gen": {"omniroute": {"model": "image-model", "token": "legacy-token"}},
        }, mock_save=mock_save)
        body = api.OmniRouteProviderSettings(
            api_key="sk-key",
            base_url="https://x.com",
        )
        resp = asyncio.run(api.put_settings(body))
        assert resp.success is True

        # Verify the settings were written.
        assert saved["omniroute"]["settings"]["api_key"] == "sk-key"
        assert saved["omniroute"]["settings"]["base_url"] == "https://x.com"
        # Verify existing image_gen config sections are untouched.
        assert saved["image_gen"]["omniroute"]["model"] == "image-model"
        assert saved["image_gen"]["omniroute"]["token"] == "legacy-token"


class TestSettingsStoreClientResolution:
    """Tests that config.py resolution helpers read from the new settings store."""

    def test_resolve_base_url_prefers_env_then_settings_then_legacy(self, monkeypatch):
        from omniroute_plugin.config import _resolve_base_url, _omniroute_config
        # Patch helpers to avoid reading real config.yaml
        import omniroute_plugin.config as cfg_mod

        calls = []
        original_load_settings = cfg_mod._load_settings_config
        original_omni_config = cfg_mod._omniroute_config

        def fake_settings():
            calls.append("settings")
            return {"base_url": "https://settings.example.com/api/v1"}

        def fake_legacy():
            calls.append("legacy")
            return {"base_url": "https://legacy.example.com/api/v1"}

        cfg_mod._load_settings_config = fake_settings
        cfg_mod._omniroute_config = fake_legacy

        try:
            # Env set → env wins
            monkeypatch.setenv("OMNIROUTE_BASE_URL", "https://env.example.com/api/v1")
            assert _resolve_base_url() == "https://env.example.com/api/v1"

            # No env → settings store wins
            monkeypatch.delenv("OMNIROUTE_BASE_URL", raising=False)
            assert _resolve_base_url() == "https://settings.example.com/api/v1"
            assert "settings" in calls

            # No env, empty settings → legacy wins
            cfg_mod._load_settings_config = lambda: {}
            calls.clear()
            assert _resolve_base_url() == "https://legacy.example.com/api/v1"
            assert "legacy" in calls
        finally:
            cfg_mod._load_settings_config = original_load_settings
            cfg_mod._omniroute_config = original_omni_config

    def test_resolve_token_prefers_env_then_settings_then_legacy(self, monkeypatch):
        from omniroute_plugin.config import _resolve_token
        import omniroute_plugin.config as cfg_mod

        original_load_settings = cfg_mod._load_settings_config
        original_omni_config = cfg_mod._omniroute_config

        def fake_settings():
            return {"api_key": "settings-key"}

        def fake_legacy():
            return {"token": "legacy-token"}

        cfg_mod._load_settings_config = fake_settings
        cfg_mod._omniroute_config = fake_legacy

        try:
            monkeypatch.setenv("OMNIROUTE_API_KEY", "env-key")
            assert _resolve_token() == "env-key"

            monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
            monkeypatch.delenv("OMNIROUTE_TOKEN", raising=False)
            assert _resolve_token() == "settings-key"

            cfg_mod._load_settings_config = lambda: {}
            assert _resolve_token() == "legacy-token"
        finally:
            cfg_mod._load_settings_config = original_load_settings
            cfg_mod._omniroute_config = original_omni_config
