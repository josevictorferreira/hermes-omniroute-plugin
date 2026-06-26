"""Omniroute text-to-speech provider and voice/model catalogs.

Wraps ``POST /v1/audio/speech``. Voice catalog is a fixed OpenAI-compatible
set; models are filtered from ``GET /v1/models`` by keyword.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.tts_provider import TTSProvider

from .._version import _PLUGIN_VERSION
from ..config import (
    _resolve_base_url,
    _resolve_tts_model,
    _resolve_tts_token,
    _resolve_tts_voice,
)

logger = logging.getLogger(__name__)


_TTS_VOICE_CATALOG: List[Dict[str, Any]] = [
    {"id": "alloy", "name": "Alloy"},
    {"id": "echo", "name": "Echo"},
    {"id": "fable", "name": "Fable"},
    {"id": "onyx", "name": "Onyx"},
    {"id": "nova", "name": "Nova"},
    {"id": "shimmer", "name": "Shimmer"},
]

# Keywords used to filter /v1/models entries down to TTS-capable models.
_TTS_MODEL_KEYWORDS = ("tts", "speech", "audio")


class OmnirouteTTSProvider(TTSProvider):
    """Omniroute ``/audio/speech`` backend (OpenAI-compatible TTS).

    Synthesizes text via ``POST /v1/audio/speech`` with an OpenAI-compatible
    payload (``{model, input, voice, response_format, speed}``).  Audio bytes
    are written directly to ``output_path`` and the absolute path returned.

    Model resolution (first wins): explicit ``model`` arg,
    ``OMNIROUTE_TTS_MODEL`` env, ``tts.omniroute.model`` config,
    ``DEFAULT_TTS_MODEL``.

    Token resolution mirrors the shared Omniroute service credentials
    (``_resolve_tts_token``): ``OMNIROUTE_API_KEY`` env,
    ``tts.omniroute.token`` config, then the image-gen ``image_gen.omniroute.token``
    fallback.
    """

    @property
    def name(self) -> str:
        return "omniroute"

    @property
    def display_name(self) -> str:
        return "Omniroute"

    def is_available(self) -> bool:
        """Return True when ``requests`` is importable.

        Unlike image-gen/web-search, availability does **not** require a token —
        the provider shows up in ``hermes tools`` so users can configure it.
        """
        try:
            import requests  # noqa: F401
        except ImportError:
            return False
        return True

    def list_voices(self) -> List[Dict[str, Any]]:
        """Return the fixed OpenAI-compatible voice catalog."""
        return [dict(v) for v in _TTS_VOICE_CATALOG]

    def default_voice(self) -> Optional[str]:
        return _TTS_VOICE_CATALOG[0]["id"] if _TTS_VOICE_CATALOG else None

    def list_models(self) -> List[Dict[str, Any]]:
        """Return TTS-capable models from ``GET /v1/models``.

        Filters the full model catalog for entries whose ``id`` contains a
        TTS keyword (``tts``, ``speech``, ``audio``).  Requires a token; returns
        ``[]`` on any failure so callers fall through to ``default_model``.
        """
        token = _resolve_tts_token()
        if not token:
            return []
        try:
            import requests
        except ImportError:
            return []

        base_url = _resolve_base_url()
        try:
            resp = requests.get(
                f"{base_url}/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
                },
                timeout=30,
            )
            if not resp.ok:
                return []
            data = resp.json()
            models = (
                data.get("data", [])
                if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )
            result: List[Dict[str, Any]] = []
            for m in models:
                if not isinstance(m, dict):
                    continue
                mid = str(m.get("id", "")).lower()
                if any(kw in mid for kw in _TTS_MODEL_KEYWORDS):
                    entry: Dict[str, Any] = {"id": m.get("id")}
                    if m.get("name"):
                        entry["name"] = m["name"]
                    elif m.get("owned_by"):
                        entry["name"] = m["owned_by"]
                    result.append(entry)
            return result
        except Exception:
            logger.debug("Omniroute TTS list_models failed", exc_info=True)
            return []

    def default_model(self) -> Optional[str]:
        return _resolve_tts_model()

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "",
            "tag": "OpenAI-compatible TTS via Omniroute",
            "env_vars": [
                {
                    "key": "OMNIROUTE_API_KEY",
                    "prompt": "Omniroute API token",
                    "url": "https://omniroute.josevictor.me",
                }
            ],
        }

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: Optional[str] = None,
        model: Optional[str] = None,
        speed: Optional[float] = None,
        format: str = "mp3",
        **extra: Any,
    ) -> str:
        """Synthesize ``text`` and write audio bytes to ``output_path``.

        Returns the absolute path of the written file.  Raises on failure
        (missing token, network error, non-2xx response, empty body).
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required and must be a non-empty string")

        token = _resolve_tts_token()
        if not token:
            raise RuntimeError(
                "OMNIROUTE_API_KEY not set. Export OMNIROUTE_API_KEY, "
                "or set tts.omniroute.token in config.yaml."
            )

        try:
            import requests
        except ImportError:
            raise RuntimeError(
                "requests package not installed (pip install requests)"
            )

        resolved_model = _resolve_tts_model(model)
        resolved_voice = _resolve_tts_voice(voice) or self.default_voice()
        # Clamp format to supported audio types (mp3 / opus).
        fmt = (format or "mp3").lower().strip()
        if fmt not in ("mp3", "opus"):
            fmt = "mp3"

        payload: Dict[str, Any] = {
            "model": resolved_model,
            "input": text,
            "voice": resolved_voice,
            "response_format": fmt,
        }
        if speed is not None:
            payload["speed"] = speed

        base_url = _resolve_base_url()
        try:
            resp = requests.post(
                f"{base_url}/audio/speech",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
                },
                timeout=120,
            )
        except Exception as exc:
            logger.debug("Omniroute TTS request failed", exc_info=True)
            raise RuntimeError(f"Omniroute TTS request failed: {exc}") from exc

        if not resp.ok:
            raise RuntimeError(
                f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        audio_bytes = resp.content
        if not audio_bytes:
            raise RuntimeError("Omniroute returned empty audio response")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        return os.path.abspath(output_path)
