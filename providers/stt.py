"""Omniroute speech-to-text (transcription) provider.

Wraps ``POST /v1/audio/transcriptions`` (OpenAI-compatible multipart upload).
Hermes calls speech-to-text "transcription"; this provider plugs into the
``stt.provider`` config slot via ``register_transcription_provider``.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional

from agent.transcription_provider import TranscriptionProvider

from .._version import _PLUGIN_VERSION
from ..config import (
    _resolve_base_url,
    _resolve_stt_model,
    _resolve_stt_token,
)

logger = logging.getLogger(__name__)


# Known Omniroute transcription models. The transcription registry is not
# exposed as a listable catalog (unlike image-gen's GET /images/generations),
# and the model ids carry no filterable keyword, so this is a curated list.
# A model only works if the Omniroute instance has credentials for that
# provider — failure surfaces at transcribe() time, not here.
_STT_MODEL_CATALOG: List[Dict[str, Any]] = [
    {"id": "deepgram/nova-3", "display": "Deepgram Nova-3"},
    {"id": "assemblyai/best", "display": "AssemblyAI Best"},
]


class OmnirouteSTTProvider(TranscriptionProvider):
    """Omniroute ``/audio/transcriptions`` backend (OpenAI-compatible STT).

    Transcribes audio via ``POST /v1/audio/transcriptions`` with a multipart
    form-data payload (``{file, model, language, response_format}``) and
    returns the standard Hermes transcription envelope.

    Model resolution (first wins): explicit ``model`` arg,
    ``OMNIROUTE_STT_MODEL`` env, ``stt.omniroute.model`` config,
    ``DEFAULT_STT_MODEL``.

    Token resolution mirrors the shared Omniroute service credentials
    (``_resolve_stt_token``): ``OMNIROUTE_API_KEY`` env,
    ``stt.omniroute.token`` config, then the shared Omniroute token.
    """

    @property
    def name(self) -> str:
        return "omniroute"

    @property
    def display_name(self) -> str:
        return "Omniroute"

    def is_available(self) -> bool:
        """Return True when ``requests`` is importable.

        Like TTS, availability does **not** require a token — the provider
        shows up in ``hermes tools`` so users can configure it; the token is
        only required at transcribe time.
        """
        try:
            import requests  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        """Return the curated catalog of Omniroute transcription models."""
        return [dict(m) for m in _STT_MODEL_CATALOG]

    def default_model(self) -> Optional[str]:
        return _resolve_stt_model()

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "",
            "tag": "OpenAI-compatible STT via Omniroute",
            "env_vars": [
                {
                    "key": "OMNIROUTE_API_KEY",
                    "prompt": "Omniroute API key",
                    "url": "https://omniroute.josevictor.me",
                }
            ],
        }

    def transcribe(
        self,
        file_path: str,
        *,
        model: Optional[str] = None,
        language: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Transcribe the audio file at ``file_path``.

        Returns the standard envelope; never raises (exceptions are converted
        to ``{"success": False, ...}``).
        """
        token = _resolve_stt_token()
        if not token:
            return {
                "success": False,
                "transcript": "",
                "error": (
                    "OMNIROUTE_API_KEY not set. Export OMNIROUTE_API_KEY, "
                    "or set stt.omniroute.token in config.yaml."
                ),
                "provider": self.name,
            }

        try:
            import requests
        except ImportError:
            return {
                "success": False,
                "transcript": "",
                "error": "requests package not installed (pip install requests)",
                "provider": self.name,
            }

        resolved_model = _resolve_stt_model(model)
        base_url = _resolve_base_url()
        data: Dict[str, str] = {
            "model": resolved_model,
            "response_format": "json",
        }
        if language and language.strip():
            data["language"] = language.strip()

        try:
            with open(file_path, "rb") as audio_file:
                resp = requests.post(
                    f"{base_url}/audio/transcriptions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
                    },
                    files={"file": (os.path.basename(file_path), audio_file, mimetypes.guess_type(file_path)[0] or "application/octet-stream")},
                    data=data,
                    timeout=120,
                )
        except Exception as exc:
            logger.debug("Omniroute STT request failed", exc_info=True)
            return {
                "success": False,
                "transcript": "",
                "error": f"Omniroute STT request failed: {exc}",
                "provider": self.name,
            }

        if not resp.ok:
            return {
                "success": False,
                "transcript": "",
                "error": f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}",
                "provider": self.name,
            }

        try:
            transcript = str(resp.json().get("text", "")).strip()
        except Exception:
            logger.debug("Omniroute STT response parse failed", exc_info=True)
            return {
                "success": False,
                "transcript": "",
                "error": "Omniroute returned an unparseable transcription response",
                "provider": self.name,
            }

        if not transcript:
            return {
                "success": False,
                "transcript": "",
                "error": "Omniroute returned an empty transcript",
                "provider": self.name,
            }

        return {"success": True, "transcript": transcript, "provider": self.name}
