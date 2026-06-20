"""Shared configuration / credential resolution for the Omniroute plugin.

Centralises base constants and the env->config fallback chain used by all three
providers (image-gen, web-search, TTS). Providers import the helpers they need
from here.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://omniroute.josevictor.me/api/v1"

# Default TTS model when none configured. Users override via
# OMNIROUTE_TTS_MODEL env var or tts.omniroute.model config key.
DEFAULT_TTS_MODEL = "openai/tts-1"



# Cap per-result search snippet length (some providers return full page text).
_SEARCH_DESC_LIMIT = 500


# for its provider; users can override via image_gen.omniroute.model or the
# OMNIROUTE_IMAGE_MODEL env var.
DEFAULT_MODEL = "antigravity/gemini-3.1-flash-image"

_FALLBACK_SIZE = "1024x1024"


def _load_config() -> Dict[str, Any]:
    """Read the ``image_gen`` section from config.yaml ({} on any failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _omniroute_config() -> Dict[str, Any]:
    sub = _load_config().get("omniroute")
    return sub if isinstance(sub, dict) else {}


def _web_omniroute_config() -> Dict[str, Any]:
    """Read the ``web.omniroute`` section from config.yaml ({} on any failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        web = cfg.get("web") if isinstance(cfg, dict) else None
        if not isinstance(web, dict):
            return {}
        sub = web.get("omniroute")
        return sub if isinstance(sub, dict) else {}
    except Exception as exc:
        logger.debug("Could not load web.omniroute config: %s", exc)
        return {}


def _resolve_base_url() -> str:
    env = os.environ.get("OMNIROUTE_BASE_URL")
    if env:
        return env.strip().rstrip("/")
    value = _omniroute_config().get("base_url")
    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("/")
    return DEFAULT_BASE_URL


def _resolve_token() -> Optional[str]:
    for var in ("OMNIROUTE_TOKEN", "OMNIROUTE_API_KEY"):
        env = os.environ.get(var)
        if env:
            return env.strip()
    value = _omniroute_config().get("token")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _tts_omniroute_config() -> Dict[str, Any]:
    """Read ``tts.omniroute`` section from config.yaml ({} on any failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        tts = cfg.get("tts") if isinstance(cfg, dict) else None
        if not isinstance(tts, dict):
            return {}
        sub = tts.get("omniroute")
        return sub if isinstance(sub, dict) else {}
    except Exception as exc:
        logger.debug("Could not load tts.omniroute config: %s", exc)
        return {}


def _resolve_tts_model(model: Optional[str] = None) -> Optional[str]:
    """Resolve TTS model: explicit arg > OMNIROUTE_TTS_MODEL env > tts.omniroute.model > DEFAULT_TTS_MODEL."""
    if model and model.strip():
        return model.strip()
    env = os.environ.get("OMNIROUTE_TTS_MODEL")
    if env and env.strip():
        return env.strip()
    value = _tts_omniroute_config().get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_TTS_MODEL


def _resolve_tts_token() -> Optional[str]:
    """Resolve TTS token: env > tts.omniroute.token > image_gen.omniroute.token (shared service)."""
    for var in ("OMNIROUTE_TOKEN", "OMNIROUTE_API_KEY"):
        env = os.environ.get(var)
        if env:
            return env.strip()
    value = _tts_omniroute_config().get("token")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _resolve_token()


def _resolve_search_provider() -> Optional[str]:
    """Optional pinned Omniroute search provider (else Omniroute auto-selects).

    Resolution order:
      1. ``OMNIROUTE_SEARCH_PROVIDER`` env var
      2. ``web.omniroute.search_provider`` (documented config path)
      3. ``image_gen.omniroute.search_provider`` (legacy fallback)
    """
    env = os.environ.get("OMNIROUTE_SEARCH_PROVIDER")
    if env:
        return env.strip()
    # web.omniroute.search_provider is the documented path; check it first.
    value = _web_omniroute_config().get("search_provider")
    if isinstance(value, str) and value.strip():
        return value.strip()
    # Fallback: image_gen.omniroute.search_provider (original code path).
    value = _omniroute_config().get("search_provider")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
