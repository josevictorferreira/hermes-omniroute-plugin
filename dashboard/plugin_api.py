"""Omniroute dashboard plugin backend API routes.

Mounted under ``/api/plugins/omniroute/`` by the dashboard plugin system.

Provides a cohesive read/write surface for all Omniroute configuration
variables, mapping each env variable to its canonical config.yaml path:

  OMNIROUTE_TOKEN        → image_gen.omniroute.token
  OMNIROUTE_BASE_URL     → image_gen.omniroute.base_url
  OMNIROUTE_IMAGE_MODEL  → image_gen.omniroute.model
  OMNIROUTE_TTS_MODEL    → tts.omniroute.model
  OMNIROUTE_SEARCH_PROVIDER → web.omniroute.search_provider

Security note: the plugin API routes go through the dashboard auth
middleware just like core API routes (loopback token or gated cookie).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

try:
    from fastapi import APIRouter
except Exception:
    # Allows local unit tests without dashboard dependencies.
    class APIRouter:  # type: ignore[misc]
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

router = APIRouter()
logger = logging.getLogger(__name__)

# Config paths for each config variable (dotted key format used by
# hermes_cli.config._set_nested).
_CONFIG_KEYS = {
    "token": "image_gen.omniroute.token",
    "base_url": "image_gen.omniroute.base_url",
    "image_model": "image_gen.omniroute.model",
    "tts_model": "tts.omniroute.model",
    "search_provider": "web.omniroute.search_provider",
}

_ENV_VARS = {
    "token": "OMNIROUTE_TOKEN",
    "base_url": "OMNIROUTE_BASE_URL",
    "image_model": "OMNIROUTE_IMAGE_MODEL",
    "tts_model": "OMNIROUTE_TTS_MODEL",
    "search_provider": "OMNIROUTE_SEARCH_PROVIDER",
}


def _set_nested(config: Dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set value at arbitrarily nested dotted key path, creating intermediate dicts."""
    keys = dotted_key.split(".")
    current = config
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _load_hermes_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        return load_config()
    except Exception:
        return {}


def _save_hermes_config(config: Dict[str, Any]) -> None:
    from hermes_cli.config import save_config
    save_config(config)


def _get_config_value(config: Dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """Safely read nested config value."""
    try:
        from hermes_cli.config import cfg_get
        return cfg_get(config, *dotted_key.split("."), default=default)
    except Exception:
        return default


class OmnirouteConfig(BaseModel):
    token: str = Field(default="", description="Omniroute API token")
    base_url: str = Field(default="", description="Omniroute base URL")
    image_model: str = Field(default="", description="Default image generation model")
    tts_model: str = Field(default="", description="Default TTS model")
    search_provider: str = Field(default="", description="Pinned search provider (e.g. tavily-search)")


class ConfigResponse(BaseModel):
    config: Dict[str, Any]
    env_override: Dict[str, bool]
    defaults: Dict[str, str]


class ConfigSaveResponse(BaseModel):
    success: bool
    message: str = ""


@router.get("/config")
async def get_config() -> ConfigResponse:
    """Return current Omniroute configuration values and env override status."""
    config = _load_hermes_config()

    values = {}
    env_override = {}

    for key, dotted in _CONFIG_KEYS.items():
        values[key] = _get_config_value(config, dotted, default="")
        env_override[key] = bool(os.environ.get(_ENV_VARS[key]))

    defaults = {
        "token": "",
        "base_url": "https://omniroute.josevictor.me",
        "image_model": "flux-1.1-pro",
        "tts_model": "tts-1",
        "search_provider": "",
    }

    return ConfigResponse(
        config=values,
        env_override=env_override,
        defaults=defaults,
    )


@router.post("/config")
async def post_config(body: OmnirouteConfig) -> ConfigSaveResponse:
    """Save Omniroute configuration values to config.yaml."""
    try:
        config = _load_hermes_config()

        # Build a dict of values, excluding empty strings (treat as unset).
        for key, dotted in _CONFIG_KEYS.items():
            value = getattr(body, key)
            if value is not None and value.strip():
                _set_nested(config, dotted, value.strip())
            # If empty and already in config, keep existing value. We don't delete.

        _save_hermes_config(config)

        return ConfigSaveResponse(success=True, message="Configuration saved.")
    except Exception as exc:
        logger.error("Failed to save omniroute config: %s", exc)
        return ConfigSaveResponse(success=False, message=f"Save failed: {exc}")
