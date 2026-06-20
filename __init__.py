"""Omniroute plugin — ``register(ctx)`` entry point.

Routes Hermes through Omniroute, an OpenAI-compatible model router, using raw
``requests`` (no extra SDK). One plugin, three providers registered from a
single ``register(ctx)``:

* image generation :class:`OmnirouteImageGenProvider` (``POST /v1/images/generations``)
* web search       :class:`OmnirouteWebSearchProvider` (``POST /v1/search``)
* text-to-speech   :class:`OmnirouteTTSProvider`       (``POST /v1/audio/speech``)

Shared credential/endpoint resolution (first hit wins):

* token    ``OMNIROUTE_TOKEN`` / ``OMNIROUTE_API_KEY`` env, then ``image_gen.omniroute.token`` config
* base_url ``OMNIROUTE_BASE_URL`` env, then ``image_gen.omniroute.base_url`` config, then ``DEFAULT_BASE_URL``

The heavy lifting lives in submodules: :mod:`config` (constants + resolution),
:mod:`providers` (one module per provider). This file is intentionally thin so
the Hermes plugin loader's contract — a top-level ``register`` and ``plugin.yaml``
at the plugin dir root — is unchanged.
"""

from __future__ import annotations

from ._version import __version__
from .config import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_TTS_MODEL
from .providers.image_gen import OmnirouteImageGenProvider
from .providers.tts import OmnirouteTTSProvider
from .providers.web_search import OmnirouteWebSearchProvider

__all__ = [
    "register",
    "__version__",
    "OmnirouteImageGenProvider",
    "OmnirouteWebSearchProvider",
    "OmnirouteTTSProvider",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TTS_MODEL",
]


def register(ctx) -> None:
    """Plugin entry point — register Omniroute image-gen, web-search and TTS providers."""
    ctx.register_image_gen_provider(OmnirouteImageGenProvider())
    ctx.register_web_search_provider(OmnirouteWebSearchProvider())
    ctx.register_tts_provider(OmnirouteTTSProvider())
