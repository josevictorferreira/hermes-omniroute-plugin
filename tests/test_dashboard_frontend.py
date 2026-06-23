"""Frontend integration tests for the OmniRoute dashboard plugin.

The dashboard frontend is a single plain-IIFE JavaScript file that consumes
``window.__HERMES_PLUGIN_SDK__``.  Because the repo has no JavaScript build
chain or test runner, these tests validate the compiled asset statically and
programmatically to ensure the UI exposes *only* the intended surface:

* exactly two configurable inputs: OmniRoute API key and base URL
* calls the limited ``/settings`` backend endpoints, not the legacy ``/config``
* points users toward the main Hermes settings for model/provider selection
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
JS_PATH = PLUGIN_DIR / "dashboard" / "dist" / "index.js"
CSS_PATH = PLUGIN_DIR / "dashboard" / "dist" / "style.css"
MANIFEST_PATH = PLUGIN_DIR / "dashboard" / "manifest.json"


@pytest.fixture
def js_source() -> str:
    assert JS_PATH.exists(), f"Dashboard JS missing: {JS_PATH}"
    return JS_PATH.read_text(encoding="utf-8")


class TestDashboardStaticConstraints:
    """Ensure the distributed JS asset matches the reduced UI contract."""

    def test_javascript_is_syntactically_valid(self):
        """``node --check`` must accept the IIFE without errors."""
        result = subprocess.run(
            ["node", "--check", str(JS_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_only_two_field_definitions(self, js_source: str):
        """FIELDS array must contain exactly api_key and base_url."""
        field_keys = re.findall(
            r'key:\s*"(api_key|base_url|token|image_model|tts_model|search_provider|model_provider_model)"',
            js_source,
        )
        assert field_keys == ["api_key", "base_url"], f"Unexpected field keys: {field_keys}"

    def test_no_legacy_model_fields(self, js_source: str):
        """Legacy model/provider fields must not be present."""
        disallowed = [
            "image_model",
            "tts_model",
            "search_provider",
            "model_provider_model",
            "OMNIROUTE_IMAGE_MODEL",
            "OMNIROUTE_TTS_MODEL",
            "OMNIROUTE_SEARCH_PROVIDER",
            "OMNIROUTE_MODEL",
        ]
        for token in disallowed:
            assert token not in js_source, f"Dashboard frontend references legacy field: {token}"

    def test_uses_settings_endpoints(self, js_source: str):
        """All fetchJSON calls must target the limited ``/settings`` endpoint."""
        endpoints = re.findall(r'fetchJSON\("([^"]+)"', js_source)
        assert endpoints == [
            "/api/plugins/omniroute/settings",
            "/api/plugins/omniroute/settings",
        ], f"Unexpected endpoints: {endpoints}"

    def test_http_methods_match_settings_crud(self, js_source: str):
        """``GET`` (default) and ``PUT`` are the verbs used against ``/settings``."""
        methods = re.findall(r'method:\s*"(GET|PUT|POST|PATCH|DELETE)"', js_source)
        assert set(methods) == {"PUT"}, f"Unexpected methods: {methods}"

    def test_helper_text_mentions_hermes_settings(self, js_source: str):
        """The UI must direct users to the main Hermes config for models."""
        assert "main Hermes settings" in js_source
        assert "TTS" in js_source
        assert "Image" in js_source
        assert "Web" in js_source or "Model" in js_source

    def test_manifest_describes_limited_surface(self):
        """manifest.json should not promise model configuration."""
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        assert "API key" in manifest
        assert "base url" in manifest.lower()
        assert "Model selection" in manifest

    def test_css_contains_helper_text_style(self):
        """The CSS must style the helper-text block."""
        css = CSS_PATH.read_text(encoding="utf-8")
        assert ".omniroute-helper-text" in css
