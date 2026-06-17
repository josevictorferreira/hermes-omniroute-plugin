"""Tests for _resolve_model() registry validation fix (issue #1).

Verifies that a global image_gen.model not present in Omniroute's image
registry is rejected and falls through to DEFAULT_MODEL / first-available,
while Omniroute-specific overrides (env var, image_gen.omniroute.model)
are trusted as-is.
"""

import importlib.util
import os
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Stub out Hermes-internal dependencies so __init__.py can be imported.
# ---------------------------------------------------------------------------

for mod_name in (
    "agent",
    "agent.image_gen_provider",
    "agent.web_search_provider",
    "hermes_cli",
    "hermes_cli.config",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = type(sys)("stub")

sys.modules["agent.image_gen_provider"].ImageGenProvider = object
sys.modules["agent.image_gen_provider"].WebSearchProvider = object
for attr in (
    "DEFAULT_ASPECT_RATIO",
    "error_response",
    "resolve_aspect_ratio",
    "save_b64_image",
    "save_url_image",
    "success_response",
):
    setattr(sys.modules["agent.image_gen_provider"], attr, attr)
sys.modules["agent.web_search_provider"].WebSearchProvider = object
sys.modules["hermes_cli.config"].load_config = lambda: {}

# Load the plugin module from file.
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "omniroute_plugin", os.path.join(HERE, "__init__.py")
)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

OmnirouteImageGenProvider = plugin.OmnirouteImageGenProvider
DEFAULT_MODEL = plugin.DEFAULT_MODEL

# Sample registry mimicking GET /images/generations.
SAMPLE_REGISTRY = {
    "antigravity/gemini-3.1-flash-image": {
        "id": "antigravity/gemini-3.1-flash-image",
        "name": "Gemini 3.1 Flash Image",
        "input_modalities": ["text"],
    },
    "openai/dall-e-3": {
        "id": "openai/dall-e-3",
        "name": "DALL-E 3",
        "input_modalities": ["text"],
    },
    "edit/edit-only-model": {
        "id": "edit/edit-only-model",
        "name": "Edit Only",
        "input_modalities": ["image"],
    },
}


def _provider_with_registry():
    p = OmnirouteImageGenProvider()
    p._registry = SAMPLE_REGISTRY
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnvOverride:
    """Step 1: OMNIROUTE_IMAGE_MODEL env — trusted, no validation."""

    @patch.dict(os.environ, {"OMNIROUTE_IMAGE_MODEL": "custom/model-x"})
    def test_env_override_trusted(self):
        p = _provider_with_registry()
        assert p._resolve_model() == "custom/model-x"


class TestOmnirouteConfig:
    """Step 2: image_gen.omniroute.model — trusted, no validation."""

    def test_omniroute_config_trusted(self):
        p = _provider_with_registry()
        with patch.object(plugin, "_omniroute_config", return_value={"model": "custom/omni-model"}):
            assert p._resolve_model() == "custom/omni-model"


class TestGlobalConfigValidation:
    """Step 3: image_gen.model — VALIDATED against registry."""

    def test_global_model_in_registry_accepted(self):
        p = _provider_with_registry()
        with patch.object(plugin, "_load_config", return_value={"model": "openai/dall-e-3"}):
            assert p._resolve_model() == "openai/dall-e-3"

    def test_global_model_not_in_registry_rejected(self):
        """Core bug fix: gpt-image-2-medium not in registry → fall through."""
        p = _provider_with_registry()
        with patch.object(plugin, "_load_config", return_value={"model": "gpt-image-2-medium"}):
            assert p._resolve_model() == DEFAULT_MODEL

    def test_global_model_empty_string_falls_through(self):
        p = _provider_with_registry()
        with patch.object(plugin, "_load_config", return_value={"model": "  "}):
            assert p._resolve_model() == DEFAULT_MODEL

    def test_global_model_none_falls_through(self):
        p = _provider_with_registry()
        with patch.object(plugin, "_load_config", return_value={}):
            assert p._resolve_model() == DEFAULT_MODEL


class TestFallback:
    """Steps 4-5: DEFAULT_MODEL and first-available."""

    def test_default_model_selected(self):
        p = _provider_with_registry()
        assert p._resolve_model() == DEFAULT_MODEL

    def test_first_available_when_default_missing(self):
        registry = {
            "custom/only-model": {
                "id": "custom/only-model",
                "name": "Only",
                "input_modalities": ["text"],
            }
        }
        p = OmnirouteImageGenProvider()
        p._registry = registry
        assert p._resolve_model() == "custom/only-model"

    def test_empty_registry_returns_default_string(self):
        p = OmnirouteImageGenProvider()
        p._registry = {}
        assert p._resolve_model() == DEFAULT_MODEL


class TestPrecedence:
    """Verify resolution order is maintained after the fix."""

    @patch.dict(os.environ, {"OMNIROUTE_IMAGE_MODEL": "env/model"})
    def test_env_beats_global_config(self):
        p = _provider_with_registry()
        with patch.object(plugin, "_load_config", return_value={"model": "openai/dall-e-3"}):
            assert p._resolve_model() == "env/model"

    def test_omniroute_config_beats_invalid_global(self):
        p = _provider_with_registry()
        with (
            patch.object(plugin, "_omniroute_config", return_value={"model": "openai/dall-e-3"}),
            patch.object(plugin, "_load_config", return_value={"model": "gpt-image-2-medium"}),
        ):
            assert p._resolve_model() == "openai/dall-e-3"
