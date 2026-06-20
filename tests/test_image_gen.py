"""Image-generation tests.

Merged from the former ``test_omniroute.py`` (size/orientation validation) and
``test_resolve_model.py`` (``_resolve_model()`` registry validation).
"""
import os
from unittest.mock import patch

import pytest

import omniroute_plugin as plugin
from omniroute_plugin.providers.image_gen import _is_valid_size, _pick_size


def test_is_valid_size_rejects_extra_separators_x():
    assert _is_valid_size("1024x1024xjunk") is False


def test_is_valid_size_rejects_extra_separators_colon():
    assert _is_valid_size("16:9:1") is False


def test_is_valid_size_rejects_extra_separators_colon2():
    assert _is_valid_size("1024:1024:extra") is False


def test_is_valid_size_accepts_valid_x():
    assert _is_valid_size("1024x1024") is True


def test_is_valid_size_accepts_valid_colon():
    assert _is_valid_size("16:9") is True


def test_pick_size_skips_malformed_tokens():
    assert _pick_size(["1024x1024xjunk", "16:9:1", "1024x1024"], "square") == "1024x1024"


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
        with patch("omniroute_plugin.providers.image_gen._omniroute_config", return_value={"model": "custom/omni-model"}):
            assert p._resolve_model() == "custom/omni-model"


class TestGlobalConfigValidation:
    """Step 3: image_gen.model — VALIDATED against registry."""

    def test_global_model_in_registry_accepted(self):
        p = _provider_with_registry()
        with patch("omniroute_plugin.providers.image_gen._load_config", return_value={"model": "openai/dall-e-3"}):
            assert p._resolve_model() == "openai/dall-e-3"

    def test_global_model_not_in_registry_rejected(self):
        """Core bug fix: gpt-image-2-medium not in registry → fall through."""
        p = _provider_with_registry()
        with patch("omniroute_plugin.providers.image_gen._load_config", return_value={"model": "gpt-image-2-medium"}):
            assert p._resolve_model() == DEFAULT_MODEL

    def test_global_model_empty_string_falls_through(self):
        p = _provider_with_registry()
        with patch("omniroute_plugin.providers.image_gen._load_config", return_value={"model": "  "}):
            assert p._resolve_model() == DEFAULT_MODEL

    def test_global_model_none_falls_through(self):
        p = _provider_with_registry()
        with patch("omniroute_plugin.providers.image_gen._load_config", return_value={}):
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
        with patch("omniroute_plugin.providers.image_gen._load_config", return_value={"model": "openai/dall-e-3"}):
            assert p._resolve_model() == "env/model"

    def test_omniroute_config_beats_invalid_global(self):
        p = _provider_with_registry()
        with (
            patch("omniroute_plugin.providers.image_gen._omniroute_config", return_value={"model": "openai/dall-e-3"}),
            patch("omniroute_plugin.providers.image_gen._load_config", return_value={"model": "gpt-image-2-medium"}),
        ):
            assert p._resolve_model() == "openai/dall-e-3"
