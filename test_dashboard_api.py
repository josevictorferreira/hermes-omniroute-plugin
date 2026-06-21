"""Tests for the Omniroute dashboard plugin API.

Validates GET /config and POST /config endpoints, including:
  - config reading with env override detection
  - config saving with nested dotted paths
  - error handling
"""

import asyncio
import importlib.util
import os
import sys
import types

import pytest


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

    sys.modules["fastapi"].APIRouter = FakeRouter

    HERE = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "omniroute_dashboard_api", os.path.join(HERE, "dashboard", "plugin_api.py")
    )
    api_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_mod)
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
        }
        assert resp.env_override == {
            "token": False,
            "base_url": False,
            "image_model": False,
            "tts_model": False,
            "search_provider": False,
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
