"""OmniRoute model provider plugin for Hermes Agent.

Registers OmniRoute as a model provider, enabling chat/completion routing
through OmniRoute's OpenAI-compatible API (https://omniroute.josevictor.me).

Credential resolution (first hit wins):
- Token:    OMNIROUTE_API_KEY env
- Base URL: OMNIROUTE_BASE_URL env, then default

Installation: copy this directory to
$HERMES_HOME/plugins/model-providers/omniroute/
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://omniroute.josevictor.me/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"

_FALLBACK_MODELS = (
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-3.5-sonnet",
    "google/gemini-2.0-flash",
    "deepseek/deepseek-chat",
)


class OmniRouteProfile(ProviderProfile):
    """OmniRoute OpenAI-compatible model router profile."""

    def fetch_models(
        self, *, api_key: Optional[str] = None, timeout: float = 8.0
    ) -> Optional[List[str]]:
        """Fetch available models from OmniRoute GET /v1/models."""
        import httpx

        base_url = getattr(self, "base_url", DEFAULT_BASE_URL)
        token = api_key or os.environ.get("OMNIROUTE_API_KEY", "")

        headers: Dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{base_url}/models", headers=headers, timeout=timeout
                )
                resp.raise_for_status()
                data: Dict[str, Any] = resp.json()
                models: List[str] = []
                for m in data.get("data", []):
                    model_id = m.get("id")
                    if model_id:
                        models.append(model_id)
                return models
        except Exception as exc:
            logger.warning("OmniRoute model list fetch failed: %s", exc)
            return None


_profile = OmniRouteProfile(
    name="omniroute",
    aliases=("omni",),
    display_name="OmniRoute",
    description="OmniRoute — OpenAI-compatible model router",
    signup_url="https://omniroute.josevictor.me",
    env_vars=("OMNIROUTE_API_KEY", "OMNIROUTE_BASE_URL"),
    base_url=os.environ.get("OMNIROUTE_BASE_URL", DEFAULT_BASE_URL),
    auth_type="api_key",
    fallback_models=_FALLBACK_MODELS,
    default_aux_model=DEFAULT_MODEL,
)

register_provider(_profile)
