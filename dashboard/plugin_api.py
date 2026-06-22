"""Omniroute dashboard plugin backend API routes.

Mounted under ``/api/plugins/omniroute/`` by the dashboard plugin system.

Provides a cohesive read/write surface for all Omniroute configuration
variables, mapping each env variable to its canonical config.yaml path:

  OMNIROUTE_TOKEN        → image_gen.omniroute.token
  OMNIROUTE_BASE_URL     → image_gen.omniroute.base_url
  OMNIROUTE_IMAGE_MODEL  → image_gen.omniroute.model
  OMNIROUTE_TTS_MODEL    → tts.omniroute.model
  OMNIROUTE_SEARCH_PROVIDER → web.omniroute.search_provider
OMNIROUTE_MODEL → model.omniroute.default

Security note: the plugin API routes go through the dashboard auth
middleware just like core API routes (loopback token or gated cookie).
"""

import logging
import os
from typing import Any, Dict, List, Optional

import json
import urllib.error
import urllib.request

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

_DEFAULT_MODELS_BASE_URL = "https://omniroute.josevictor.me"

# Config paths for each config variable (dotted key format used by
# hermes_cli.config._set_nested).
_CONFIG_KEYS = {
    "token": "image_gen.omniroute.token",
    "base_url": "image_gen.omniroute.base_url",
    "image_model": "image_gen.omniroute.model",
    "tts_model": "tts.omniroute.model",
    'search_provider': 'web.omniroute.search_provider',
    'model_provider_model': 'model.omniroute.default',
}

_ENV_VARS = {
    "token": "OMNIROUTE_TOKEN",
    "base_url": "OMNIROUTE_BASE_URL",
    "image_model": "OMNIROUTE_IMAGE_MODEL",
    "tts_model": "OMNIROUTE_TTS_MODEL",
    'search_provider': 'OMNIROUTE_SEARCH_PROVIDER',
    'model_provider_model': 'OMNIROUTE_MODEL',
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
    model_provider_model: str = Field(default="", description="Default model for OmniRoute as model provider")


class ConfigResponse(BaseModel):
    config: Dict[str, Any]
    env_override: Dict[str, bool]
    defaults: Dict[str, str]


class ConfigSaveResponse(BaseModel):
    success: bool
    message: str = ""


class ModelEntry(BaseModel):
    id: str
    name: str = ""
    provider: str = ""


class ModelsResponse(BaseModel):
    models: List[ModelEntry]
    error: str = ""


def _resolve_base_url(config: Dict[str, Any]) -> str:
    """Resolve the Omniroute base URL: env > config > default."""
    env = os.environ.get("OMNIROUTE_BASE_URL")
    if env and env.strip():
        return env.strip().rstrip("/")
    val = _get_config_value(config, "image_gen.omniroute.base_url", default="")
    if isinstance(val, str) and val.strip():
        return val.strip().rstrip("/")
    return _DEFAULT_MODELS_BASE_URL


def _resolve_token(config: Dict[str, Any]) -> Optional[str]:
    """Resolve the Omniroute token: env > config."""
    for var in ("OMNIROUTE_TOKEN", "OMNIROUTE_API_KEY"):
        env = os.environ.get(var)
        if env and env.strip():
            return env.strip()
    val = _get_config_value(config, "image_gen.omniroute.token", default="")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


@router.get("/models")
async def get_models() -> ModelsResponse:
    """Fetch available models from the Omniroute /v1/models endpoint."""
    config = _load_hermes_config()
    base_url = _resolve_base_url(config)
    token = _resolve_token(config)

    if not token:
        return ModelsResponse(models=[], error="API token required. Set OMNIROUTE_TOKEN or configure it below.")

    url = base_url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return ModelsResponse(models=[], error=f"Omniroute API returned HTTP {exc.code}")
    except Exception as exc:
        logger.error("Failed to fetch models from %s: %s", url, exc)
        return ModelsResponse(models=[], error=f"Failed to reach Omniroute: {exc}")

    models: List[ModelEntry] = []
    for item in data.get("data", []):
        models.append(ModelEntry(
            id=item.get("id", ""),
            name=item.get("name", item.get("id", "")),
            provider=item.get("owned_by", item.get("provider", "")),
        ))
    models.sort(key=lambda m: m.id)
    return ModelsResponse(models=models, error="")


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
    # Validate required fields when model provider is configured.
    if body.model_provider_model and body.model_provider_model.strip():
        if not body.token.strip():
            return ConfigSaveResponse(success=False, message="API token is required when selecting an OmniRoute model provider model.")
        if not body.base_url.strip():
            return ConfigSaveResponse(success=False, message="Base URL is required when selecting an OmniRoute model provider model.")

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
