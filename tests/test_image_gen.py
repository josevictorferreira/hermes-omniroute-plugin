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




# ---------------------------------------------------------------------------
# Image editing (image-to-image) tests
# ---------------------------------------------------------------------------

from io import BytesIO
from unittest.mock import MagicMock, patch, call


class TestCapabilities:
    """capabilities() should advertise both text and image modalities."""

    def test_capabilities_advertises_text_and_image(self):
        caps = OmnirouteImageGenProvider().capabilities()
        assert "text" in caps["modalities"]
        assert "image" in caps["modalities"]
        assert caps["max_reference_images"] >= 1


def _make_provider():
    p = OmnirouteImageGenProvider()
    p._registry = SAMPLE_REGISTRY
    return p


def _mock_api_resp(b64="iVBORw0KGgo=", url=None, status=200):
    m = MagicMock()
    m.ok = status < 400
    m.status_code = status
    m.text = "error body"
    m.json.return_value = {"data": [{"b64_json": b64, "url": url}]}
    return m


class TestGenerateRouting:
    """generate() must route to /images/edits when source images are provided,
    and to /images/generations when no source images are given."""

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("omniroute_plugin.providers.image_gen.save_b64_image")
    @patch("requests.post")
    def test_routes_to_edits_when_image_url_given(
        self, mock_post, mock_save, mock_load
    ):
        mock_post.return_value = _mock_api_resp()

        provider = _make_provider()
        provider.generate(
            "make blue",
            image_url="https://example.com/photo.png",
        )

        called_url = mock_post.call_args[0][0]
        assert "/images/edits" in called_url
        assert "json" in mock_post.call_args.kwargs
        payload = mock_post.call_args.kwargs["json"]
        assert payload["image"] == "https://example.com/photo.png"
        assert payload["prompt"] == "make blue"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("omniroute_plugin.providers.image_gen.save_b64_image")
    @patch("requests.post")
    def test_routes_to_edits_when_reference_images_given(
        self, mock_post, mock_save, mock_load
    ):
        mock_save.return_value = "/cache/img.png"
        mock_post.return_value = _mock_api_resp()
        mock_load.return_value = (b"img-bytes", "input.png")

        with patch(
            "omniroute_plugin.providers.image_gen.normalize_reference_images"
        ) as mock_norm:
            mock_norm.return_value = ["https://example.com/ref1.png"]
            provider = _make_provider()
            result = provider.generate(
                "transform the style",
                reference_image_urls=["https://example.com/ref1.png"],
            )

        called_url = mock_post.call_args[0][0]
        assert "/images/edits" in called_url

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen.save_b64_image")
    @patch("requests.post")
    def test_routes_to_generations_when_no_images(self, mock_post, mock_save):
        mock_save.return_value = "/cache/img.png"
        mock_post.return_value = _mock_api_resp()

        provider = _make_provider()
        result = provider.generate("a sunset over mountains")

        called_url = mock_post.call_args[0][0]
        assert "/images/generations" in called_url
        assert "json" in mock_post.call_args.kwargs
        assert "files" not in mock_post.call_args.kwargs

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("omniroute_plugin.providers.image_gen.save_b64_image")
    @patch("requests.post")
    def test_edit_uses_json_payload(self, mock_post, mock_save, mock_load):
        mock_save.return_value = "/cache/img.png"
        mock_post.return_value = _mock_api_resp()

        provider = _make_provider()
        provider.generate("edit this", image_url="https://example.com/photo.png")

        kwargs = mock_post.call_args.kwargs
        assert "json" in kwargs
        assert "files" not in kwargs
        payload = kwargs["json"]
        assert payload["prompt"] == "edit this"
        assert payload["image"] == "https://example.com/photo.png"
        assert payload["model"]
        assert payload["size"]

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen.save_b64_image")
    @patch("requests.post")
    def test_edit_sets_modality_image(self, mock_post, mock_save):
        mock_save.return_value = "/cache/img.png"
        mock_post.return_value = _mock_api_resp()

        with patch("omniroute_plugin.providers.image_gen._load_image_bytes") as mock_load,              patch("omniroute_plugin.providers.image_gen.success_response") as mock_ok:
            mock_load.return_value = (b"data", "img.png")
            mock_ok.return_value = {"success": True}
            provider = _make_provider()
            provider.generate("edit", image_url="https://example.com/img.png")

            assert mock_ok.call_args.kwargs.get("modality") == "image"


class TestEditErrorHandling:
    """Error paths for the image-editing flow."""

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("requests.post")
    def test_edit_payload_forwards_local_image_url(self, mock_post, mock_load):
        """Local image paths are forwarded as-is; no file IO happens."""
        mock_post.return_value = _mock_api_resp()
        provider = _make_provider()

        provider.generate("edit", image_url="/nonexistent/file.png")

        mock_load.assert_not_called()
        payload = mock_post.call_args.kwargs["json"]
        assert payload["image"] == "/nonexistent/file.png"
        assert payload["prompt"] == "edit"

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("requests.post")
    def test_edit_http_error_returns_error(self, mock_post, mock_load):
        mock_load.return_value = (b"data", "img.png")
        mock_post.return_value = _mock_api_resp(status=401)

        provider = _make_provider()
        result = provider.generate("edit", image_url="https://example.com/img.png")

        assert result["success"] is False

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("requests.post")
    def test_edit_network_exception_returns_error(self, mock_post, mock_load):
        mock_load.return_value = (b"data", "img.png")
        mock_post.side_effect = ConnectionError("timeout")

        provider = _make_provider()
        result = provider.generate("edit", image_url="https://example.com/img.png")

        assert result["success"] is False

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("requests.post")
    def test_edit_empty_response_returns_error(self, mock_post, mock_load):
        mock_load.return_value = (b"data", "img.png")
        empty_resp = MagicMock()
        empty_resp.ok = True
        empty_resp.status_code = 200
        empty_resp.json.return_value = {"data": []}
        mock_post.return_value = empty_resp

        provider = _make_provider()
        result = provider.generate("edit", image_url="https://example.com/img.png")

        assert result["success"] is False


class TestLoadImageBytes:
    """Test the _load_image_bytes helper function."""

    def test_load_local_file(self, tmp_path):
        from omniroute_plugin.providers.image_gen import _load_image_bytes

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        data, filename = _load_image_bytes(str(img))
        assert data.startswith(b"\x89PNG")
        assert filename == "test.png"

    @patch("requests.get")
    def test_load_url(self, mock_get):
        from omniroute_plugin.providers.image_gen import _load_image_bytes

        mock_resp = MagicMock()
        mock_resp.content = b"image-bytes"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        data, filename = _load_image_bytes("https://example.com/photo.jpg")
        assert data == b"image-bytes"
        assert filename == "photo.jpg"

    def test_load_data_uri(self):
        from omniroute_plugin.providers.image_gen import _load_image_bytes

        data, filename = _load_image_bytes("data:image/png;base64,iVBORw0KGgo=")
        assert len(data) > 0
        assert "image" in filename


class TestEditMultipleImages:
    """Test that multiple source images are handled correctly."""

    @patch.dict(os.environ, {"OMNIROUTE_TOKEN": "test-token"})
    @patch("omniroute_plugin.providers.image_gen._load_image_bytes")
    @patch("omniroute_plugin.providers.image_gen.save_b64_image")
    @patch("requests.post")
    def test_multiple_reference_images_uploaded(
        self, mock_post, mock_save, mock_load
    ):
        mock_save.return_value = "/cache/img.png"
        mock_post.return_value = _mock_api_resp()
        mock_load.side_effect = [
            (b"img1", "photo1.png"),
            (b"img2", "photo2.png"),
            (b"img3", "photo3.png"),
        ]

        with patch(
            "omniroute_plugin.providers.image_gen.normalize_reference_images"
        ) as mock_norm:
            mock_norm.return_value = [
                "https://example.com/ref1.png",
                "https://example.com/ref2.png",
            ]
            provider = _make_provider()
            provider.generate(
                "combine these",
                image_url="https://example.com/main.png",
                reference_image_urls=[
                    "https://example.com/ref1.png",
                    "https://example.com/ref2.png",
                ],
            )

        kwargs = mock_post.call_args.kwargs
        assert "json" in kwargs
        payload = kwargs["json"]
        assert payload["image"] == "https://example.com/main.png"
        assert payload["reference_images"] == [
            "https://example.com/ref1.png",
            "https://example.com/ref2.png",
        ]
        assert mock_load.call_count == 0
