"""Shared pytest fixtures/stubs Omniroute plugin test-suite.

The plugin imports Hermes-internal modules (`agent.*`, `hermes_cli.*`) that
are not installed in this repo. We stub them before importing the plugin, then
load the plugin directory as a proper package (`omniroute_plugin`) — mirroring
the real Hermes plugin loader, which sets `__path__` so relative imports
(`from ..config import ...`) resolve. Tests then `import omniroute_plugin`
or pull symbols from its submodules.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

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
    igp.resolve_aspect_ratio = lambda v="1:1", *a, **k: v if v in ("landscape", "square", "portrait") else "landscape"
    igp.save_b64_image = lambda *a, **k: Path("/tmp/test_image.png")
    igp.save_url_image = lambda *a, **k: Path("/tmp/test_image.png")
    igp.normalize_reference_images = lambda refs=None, *a, **k: list(refs) if refs else []
    igp.success_response = _success_response

    wsp = stub("agent.web_search_provider")
    wsp.WebSearchProvider = type("WebSearchProvider", (), {})

    ttsp = stub("agent.tts_provider")
    ttsp.TTSProvider = type("TTSProvider", (), {})

    hcfg = stub("hermes_cli.config")
    hcfg.load_config = lambda: {}


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

import os as _os
_os.environ["OMNICONFTEST_LOADED"] = "1"
import builtins as _b
_print = _b.print
_print("[CONFTEST] ran; omniroute_plugin in sys.modules:", "omniroute_plugin" in sys.modules, file=_b.__import__("sys").stderr)
