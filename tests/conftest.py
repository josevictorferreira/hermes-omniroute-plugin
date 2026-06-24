"""Shared pytest fixtures/stubs Omniroute plugin test-suite.

The plugin imports Hermes-internal modules (`agent.*`, `hermes_cli.*`) that
are not installed in this repo. We stub them before importing the plugin, then
load the plugin directory as a proper package (`omniroute_plugin`) — mirroring
the real Hermes plugin loader, which sets `__path__` so relative imports
(`from ..config import ...`) resolve. Tests then `import omniroute_plugin`
or pull symbols from its submodules.
"""
from __future__ import annotations
import os

import importlib.util
import sys
import pytest
import types
from pathlib import Path

os.environ.setdefault("OMNIROUTE_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def _isolate_hermes_config(monkeypatch):
    """Save and restore ``hermes_cli.config`` around each test.

    The dashboard API tests replace this module with a test-specific mock
    (via ``load_plugin_api_with_config``).  Without cleanup, the mock leaks
    into subsequent provider tests and causes cross-contamination.
    """
    original = sys.modules.get("hermes_cli.config")
    yield
    if original is not None:
        sys.modules["hermes_cli.config"] = original
    else:
        sys.modules.pop("hermes_cli.config", None)

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PKG = "omniroute_plugin"


def _install_stubs() -> None:
    """Register lightweight stand-ins for Hermes-internal dependencies."""
    def stub(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__path__ = []  # mark as package so `agent.x` imports are happy
        sys.modules.setdefault(name, mod)
        return mod

    stub("agent")
    stub("hermes_cli")

    igp = stub("agent.image_gen_provider")
    igp.DEFAULT_ASPECT_RATIO = "1:1"
    igp.ImageGenProvider = type("ImageGenProvider", (), {})

    def _error_response(*, error, error_type="provider_error", provider="", model="", prompt="", aspect_ratio="1:1", **_):
        return {
            "success": False,
            "image": None,
            "error": error,
            "error_type": error_type,
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "provider": provider,
        }

    def _success_response(*, image, model, prompt, aspect_ratio, provider, modality="text", extra=None, **_):
        r = {
            "success": True,
            "image": image,
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "modality": modality,
            "provider": provider,
        }
        if extra:
            for k, v in extra.items():
                r.setdefault(k, v)
        return r

    igp.error_response = _error_response
    def _resolve_aspect(v="1:1", *a, **kw):
        if v in ("landscape", "square", "portrait"):
            return v
        try:
            parts = str(v).split(":")
            w, h = int(parts[0]), int(parts[1])
            if w > h:
                return "landscape"
            if h > w:
                return "portrait"
            return "square"
        except (ValueError, IndexError):
            return "landscape"
    igp.resolve_aspect_ratio = _resolve_aspect
    igp.save_b64_image = lambda *a, **k: Path("/tmp/test_image.png")
    igp.save_url_image = lambda *a, **k: Path("/tmp/test_image.png")
    igp.normalize_reference_images = lambda refs=None, *a, **k: list(refs) if refs else []
    igp.success_response = _success_response

    def _normalize_ref(v):
        if v is None:
            return None
        if isinstance(v, str):
            return [v]
        if isinstance(v, (list, tuple)):
            out = [s for s in v if isinstance(s, str) and s.strip()]
            return out or None
        return None

    igp.normalize_reference_images = _normalize_ref

    wsp = stub("agent.web_search_provider")
    wsp.WebSearchProvider = type("WebSearchProvider", (), {})

    ttsp = stub("agent.tts_provider")
    ttsp.TTSProvider = type("TTSProvider", (), {})

    tcp = stub("agent.transcription_provider")
    tcp.TranscriptionProvider = type("TranscriptionProvider", (), {})

    vgp = stub("agent.video_gen_provider")
    vgp.VideoGenProvider = type("VideoGenProvider", (), {})

    def _v_error_response(*, error, error_type="provider_error", provider="", model="", prompt="", aspect_ratio="", **_):
        return {
            "success": False,
            "video": None,
            "error": error,
            "error_type": error_type,
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "provider": provider,
        }

    def _v_success_response(*, video, model, prompt, modality="text", aspect_ratio="", duration=0, provider, extra=None, **_):
        r = {
            "success": True,
            "video": video,
            "model": model,
            "prompt": prompt,
            "modality": modality,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "provider": provider,
        }
        if extra:
            for k, v in extra.items():
                r.setdefault(k, v)
        return r

    vgp.error_response = _v_error_response
    vgp.success_response = _v_success_response
    vgp.save_bytes_video = lambda raw, *a, **k: Path("/tmp/test_video.mp4")

    hcfg = stub("hermes_cli.config")
    hcfg.load_config = lambda: {}

    # --- providers stubs (for model_provider plugin) ---
    prov = stub("providers")
    _registered_profiles = []
    prov._registered = _registered_profiles
    prov.register_provider = lambda p: _registered_profiles.append(p)

    prov_base = stub("providers.base")
    class _ProviderProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def fetch_models(self, *, api_key=None, timeout=8.0):
            return None
    prov_base.ProviderProfile = _ProviderProfile


def _load_plugin() -> None:
    """Load plugin dir as `omniroute_plugin` package (idempotent)."""
    if PKG in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        PKG,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(PLUGIN_DIR)]  # mirror real Hermes loader
    module.__package__ = PKG
    sys.modules[PKG] = module
    spec.loader.exec_module(module)


_install_stubs()
_load_plugin()

# --- load model_provider as a separate package ---
_MODEL_PROVIDER_PKG = "omniroute_model_provider"
_MODEL_PROVIDER_DIR = PLUGIN_DIR / "model_provider"


def _load_model_provider() -> None:
    """Load model_provider/ as ``omniroute_model_provider`` package."""
    if _MODEL_PROVIDER_PKG in sys.modules:
        return
    init = _MODEL_PROVIDER_DIR / "__init__.py"
    if not init.exists():
        return
    spec = importlib.util.spec_from_file_location(
        _MODEL_PROVIDER_PKG,
        init,
        submodule_search_locations=[str(_MODEL_PROVIDER_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(_MODEL_PROVIDER_DIR)]
    module.__package__ = _MODEL_PROVIDER_PKG
    sys.modules[_MODEL_PROVIDER_PKG] = module
    spec.loader.exec_module(module)


_load_model_provider()

import os as _os
_os.environ["OMNICONFTEST_LOADED"] = "1"
import builtins as _b
_print = _b.print
_print("[CONFTEST] ran; omniroute_plugin sys.modules:", "omniroute_plugin" in sys.modules, file=_b.__import__("sys").stderr)
