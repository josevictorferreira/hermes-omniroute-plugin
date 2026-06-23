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

from pydantic import BaseModel, ConfigDict, Field

try:
    from fastapi import APIRouter
except Exception:
    # Allows local unit tests without dashboard dependencies.
    class APIRouter:  # type: ignore[misc]
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

        def put(self, *_args, **_kwargs):
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


# Config paths for the limited OmniRoute provider settings store.
# Only ``api_key`` and ``base_url`` are exposed here; model selection
# (TTS model, provider model, image model, search provider) is governed
# by Hermes global config sections and must NOT leak into this store.
_SETTINGS_CONFIG_KEYS = {
    "api_key": "omniroute.settings.api_key",
    "base_url": "omniroute.settings.base_url",
}

# Env vars still win over the settings store.
_SETTINGS_ENV_VARS = {
    "api_key": ("OMNIROUTE_TOKEN", "OMNIROUTE_API_KEY"),
    "base_url": "OMNIROUTE_BASE_URL",
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
        import copy
        return copy.deepcopy(load_config())
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

class OmniRouteProviderSettings(BaseModel):
    """Strict settings model for the OmniRoute provider.

    Only ``api_key`` and ``base_url`` are configurable.  Extra fields are
    rejected at validation time, ensuring the dashboard cannot accidentally
    inject model-selection values (TTS model, image model, etc.) — those
    must come from the Hermes global config.
    """
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(
        default="",
        description="OmniRoute API key (token used in Authorization: Bearer …)",
    )
    base_url: str = Field(
        default="",
        description="OmniRoute base URL (e.g. https://omniroute.example.com/api/v1)",
    )


class SettingsResponse(BaseModel):
    """Returned by GET /settings."""
    settings: OmniRouteProviderSettings
    has_env_override: Dict[str, bool] = Field(
        default_factory=dict,
        description="Per-field flag indicating an env var is overriding the stored value.",
    )


class SettingsSaveResponse(BaseModel):
    """Returned by PUT /settings."""
    success: bool
    message: str = ""
    settings: Optional[OmniRouteProviderSettings] = None



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


# ---------------------------------------------------------------------------
# Settings endpoints — limited surface (api_key + base_url only)
# ---------------------------------------------------------------------------

def _load_settings_config() -> Dict[str, Any]:
    """Read the ``omniroute.settings`` section from config.yaml."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("omniroute") if isinstance(cfg, dict) else None
        if not isinstance(section, dict):
            return {}
        settings = section.get("settings")
        return settings if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _resolve_settings_api_key(config: Dict[str, Any]) -> Optional[str]:
    """Resolve API key for the settings endpoint: env → settings store."""
    for var in _SETTINGS_ENV_VARS["api_key"]:
        env = os.environ.get(var)
        if env:
            return env.strip()
    value = config.get("api_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_settings_base_url(config: Dict[str, Any]) -> str:
    """Resolve base URL for the settings endpoint: env → settings store → default."""
    env = os.environ.get(_SETTINGS_ENV_VARS["base_url"])
    if env:
        return env.strip().rstrip("/")
    value = config.get("base_url")
    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("/")
    return _DEFAULT_MODELS_BASE_URL


@router.get("/settings")
async def get_settings() -> SettingsResponse:
    """Return current OmniRoute provider settings (api_key, base_url only).

    TTS model, image model, search provider and model-provider selection
    are intentionally absent — those come from Hermes global config.
    """
    settings_cfg = _load_settings_config()

    api_key = _resolve_settings_api_key(settings_cfg) or ""
    base_url = _resolve_settings_base_url(settings_cfg)

    # Mask the API key in the response — only first/last 4 chars visible.
    masked_key = api_key
    if len(api_key) > 8:
        masked_key = api_key[:4] + "***" + api_key[-4:]
    elif api_key:
        masked_key = "***" + api_key[-4:]

    has_env = {
        "api_key": bool(os.environ.get(_SETTINGS_ENV_VARS["api_key"][0]))
            or bool(os.environ.get(_SETTINGS_ENV_VARS["api_key"][1])),
        "base_url": bool(os.environ.get(_SETTINGS_ENV_VARS["base_url"])),
    }

    return SettingsResponse(
        settings=OmniRouteProviderSettings(api_key=masked_key, base_url=base_url),
        has_env_override=has_env,
    )


@router.put("/settings")
async def put_settings(body: OmniRouteProviderSettings) -> SettingsSaveResponse:
    """Save OmniRoute provider settings (api_key, base_url only).

    Strict validation: any extra fields in the request body cause a 422.
    This endpoint does NOT touch TTS model, image model, search provider,
    or model-provider config — those live in Hermes global config sections.
    """
    try:
        config = _load_hermes_config()

        # Ensure the omniroute.settings section exists.
        if "omniroute" not in config or not isinstance(config.get("omniroute"), dict):
            config["omniroute"] = {}
        if "settings" not in config["omniroute"] or not isinstance(config["omniroute"].get("settings"), dict):
            config["omniroute"]["settings"] = {}

        settings_store = config["omniroute"]["settings"]

        # Only write non-empty values; preserve existing if field is blank.
        if body.api_key.strip():
            settings_store["api_key"] = body.api_key.strip()
        if body.base_url.strip():
            settings_store["base_url"] = body.base_url.strip().rstrip("/")

        _save_hermes_config(config)
        logger.info("OmniRoute provider settings saved successfully.")

        return SettingsSaveResponse(
            success=True,
            message="Settings saved.",
            settings=OmniRouteProviderSettings(
                api_key=settings_store.get("api_key", ""),
                base_url=settings_store.get("base_url", ""),
            ),
        )
    except Exception as exc:
        logger.error("Failed to save OmniRoute provider settings: %s", exc)
        return SettingsSaveResponse(
            success=False,
            message=f"Save failed: {exc}",
        )
