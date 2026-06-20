"""Shared pytest fixtures/stubs for the Omniroute plugin test-suite.

The plugin imports Hermes-internal modules (``agent.*``, ``hermes_cli.*``) that
are not installed in this repo. We stub them before importing the plugin, then
load the plugin directory as a proper package (``omniroute_plugin``) — mirroring
the real Hermes plugin loader, which sets ``__path__`` so relative imports
(``from ..config import ...``) resolve. Tests then ``import omniroute_plugin``
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
        mod.__path__ = []  # mark as package so `from agent.x import y` is happy
        sys.modules.setdefault(name, mod)
        return mod

    stub("agent")
    stub("hermes_cli")

    igp = stub("agent.image_gen_provider")
    igp.DEFAULT_ASPECT_RATIO = "1:1"
    igp.ImageGenProvider = type("ImageGenProvider", (), {})
    igp.error_response = lambda *a, **k: {"success": False}
    igp.resolve_aspect_ratio = lambda *a, **k: "1:1"
    igp.save_b64_image = lambda *a, **k: None
    igp.save_url_image = lambda *a, **k: None
    igp.success_response = lambda *a, **k: {"success": True}

    wsp = stub("agent.web_search_provider")
    wsp.WebSearchProvider = type("WebSearchProvider", (), {})

    ttsp = stub("agent.tts_provider")
    ttsp.TTSProvider = type("TTSProvider", (), {})

    hcfg = stub("hermes_cli.config")
    hcfg.load_config = lambda: {}


def _load_plugin() -> None:
    """Load the plugin dir as the ``omniroute_plugin`` package (idempotent)."""
    if PKG in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        PKG,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(PLUGIN_DIR)]  # mirror the real Hermes loader
    module.__package__ = PKG
    sys.modules[PKG] = module
    spec.loader.exec_module(module)


_install_stubs()
_load_plugin()

import os as _os
_os.environ["OMNICONFTEST_LOADED"]="1"
import builtins as _b
_print=_b.print
_print("[CONFTEST] ran; omniroute_plugin in sys.modules:", "omniroute_plugin" in sys.modules, file=_b.__import__("sys").stderr)
