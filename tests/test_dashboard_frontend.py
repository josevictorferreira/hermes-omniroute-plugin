"""Frontend integration tests for the OmniRoute dashboard plugin.

The dashboard frontend is a single plain-IIFE JavaScript file that consumes
``window.__HERMES_PLUGIN_SDK__``.  Because the repo has no JavaScript build
chain or test runner, these tests validate the compiled asset statically and
programmatically to ensure the UI exposes the intended surface:

* connection fields: OmniRoute API key + base URL (via the ``/settings`` API)
* model fields: image, TTS and provider (chat) model, each a searchable input
  backed by a ``<datalist>`` populated from ``/models?capability=…``
* model selections persist through the ``/config`` API
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
    """Ensure the distributed JS asset matches the connection+models contract."""

    def test_javascript_is_syntactically_valid(self):
        """``node --check`` must accept the IIFE without errors."""
        result = subprocess.run(
            ["node", "--check", str(JS_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_connection_fields_present(self, js_source: str):
        """The connection group must define api_key and base_url."""
        conn_keys = re.findall(r'key:\s*"(api_key|base_url)"', js_source)
        assert set(conn_keys) == {"api_key", "base_url"}, conn_keys

    def test_model_fields_present(self, js_source: str):
        """The model group must define image, TTS and provider model fields."""
        for key in ("image_model", "tts_model", "model_provider_model"):
            assert key in js_source, f"Missing model field: {key}"

    def test_model_capabilities_declared(self, js_source: str):
        """Each model field is tied to a catalog capability."""
        caps = set(re.findall(r'capability:\s*"(image|tts|chat)"', js_source))
        assert caps == {"image", "tts", "chat"}, caps

    def test_uses_settings_and_config_and_models_endpoints(self, js_source: str):
        """The UI talks to /settings (connection), /config (models) and /models."""
        assert "/settings" in js_source
        assert "/config" in js_source
        assert "/models?capability=" in js_source

    def test_http_methods_cover_both_stores(self, js_source: str):
        """PUT persists the connection (/settings); POST persists models (/config)."""
        methods = set(re.findall(r'method:\s*"(GET|PUT|POST|PATCH|DELETE)"', js_source))
        assert methods == {"PUT", "POST"}, methods

    def test_model_fields_use_datalist(self, js_source: str):
        """Model inputs must be backed by a <datalist> for searchable picking."""
        assert "datalist" in js_source
        assert "list:" in js_source

    def test_does_not_round_trip_masked_api_key(self, js_source: str):
        """Saving must only send the API key when the user changed it."""
        assert "loadedApiKey" in js_source
        assert "apiKeyChanged" in js_source

    def test_manifest_describes_model_selection(self):
        """manifest.json should advertise model selection."""
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        assert "API key" in manifest
        assert "base URL" in manifest or "base url" in manifest.lower()
        assert "model" in manifest.lower()

    def test_css_styles_model_select(self):
        """The CSS must style the datalist-backed select input (not hide it)."""
        css = CSS_PATH.read_text(encoding="utf-8")
        assert ".omniroute-select" in css
        assert "display: none" not in css.split(".omniroute-select", 1)[1][:80]
        assert ".omniroute-helper-text" in css
