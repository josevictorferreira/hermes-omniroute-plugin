"""Omniroute video-generation provider.

Wraps ``POST /v1/videos/generations`` (OpenAI-style video endpoint). Supports
text-to-video (``prompt`` only) and image-to-video (``prompt`` + ``image_url``);
the provider routes within the chosen model family based on whether
``image_url`` was passed, exactly like Hermes' built-in video-gen backends.

Omniroute returns the rendered video as ``data[0].b64_json`` (primary path) or
``data[0].url``. Base64 payloads are decoded and cached locally via
``save_bytes_video``; URLs are passed through verbatim.

Model selection (first wins): explicit ``model`` arg, ``OMNIROUTE_VIDEO_MODEL``
env, ``video_gen.omniroute.model`` config, ``DEFAULT_VIDEO_MODEL``. Token /
base URL resolution reuses the shared Omniroute service credentials
(``_resolve_token`` / ``_resolve_base_url``).
"""

from __future__ import annotations

import base64 as _b64
import logging
from typing import Any, Dict, List, Optional

from agent.video_gen_provider import (
    VideoGenProvider,
    error_response,
    save_bytes_video,
    success_response,
)

from .._version import _PLUGIN_VERSION
from ..config import (
    _resolve_base_url,
    _resolve_token,
    _resolve_video_model,
)

logger = logging.getLogger(__name__)


class OmnirouteVideoGenProvider(VideoGenProvider):
    """Omniroute ``/videos/generations`` backend (OpenAI-compatible video)."""

    def __init__(self) -> None:
        self._default_model: Optional[str] = None

    @property
    def name(self) -> str:
        return "omniroute"

    @property
    def display_name(self) -> str:
        return "Omniroute"

    def is_available(self) -> bool:
        if not _resolve_token():
            return False
        try:
            import requests  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        """Return a static catalog for the default model family.

        Omniroute exposes no video model catalog endpoint (the media models live
        behind ``POST /v1/videos/generations``, not ``GET /v1/models``), so we
        surface the configured default as the single selectable family.
        """
        model = _resolve_video_model()
        return [
            {
                "id": model,
                "display": model,
                "modalities": ["text", "image"],
            }
        ]

    def default_model(self) -> Optional[str]:
        return _resolve_video_model()

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "resolutions": ["480p", "720p", "1080p"],
            "min_duration": 1,
            "max_duration": 10,
            "supports_audio": False,
            "supports_negative_prompt": False,
            "max_reference_images": 0,
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "paid",
            "tag": "Video generation via Omniroute (/v1/videos/generations)",
            "env_vars": [
                {
                    "key": "OMNIROUTE_API_KEY",
                    "prompt": "Omniroute API token",
                    "url": "https://omniroute.josevictor.me/",
                }
            ],
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return error_response(
                error="prompt is required for video generation",
                error_type="invalid_argument",
                provider="omniroute",
                aspect_ratio=aspect_ratio,
            )

        token = _resolve_token()
        if not token:
            return error_response(
                error=(
                    "Omniroute token not configured. Set OMNIROUTE_API_KEY "
                    "env var, or omniroute.settings.api_key."
                ),
                error_type="auth_error",
                provider="omniroute",
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        resolved_model = _resolve_video_model(model)
        if not resolved_model:
            return error_response(
                error=(
                    "No video model configured. Set video_gen.omniroute.model "
                    "in config.yaml or OMNIROUTE_VIDEO_MODEL."
                ),
                error_type="invalid_argument",
                provider="omniroute",
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        base_url = _resolve_base_url()

        # Image-to-video when a source image is supplied, else text-to-video.
        is_image_to_video = isinstance(image_url, str) and image_url.strip()
        modality = "image" if is_image_to_video else "text"

        # Build payload. Omniroute's Zod schemas strip unknown keys, so it is
        # safe to forward every supported parameter; each upstream provider
        # ignores what it does not understand.
        payload: Dict[str, Any] = {
            "model": resolved_model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
        }
        if is_image_to_video:
            payload["image_url"] = image_url.strip()
        if duration is not None:
            payload["duration"] = duration
        if negative_prompt and negative_prompt.strip():
            payload["negative_prompt"] = negative_prompt.strip()
        if seed is not None:
            payload["seed"] = seed
        if audio is not None:
            payload["audio"] = audio

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
        }

        try:
            import requests
        except ImportError:
            return error_response(
                error="requests package not installed (pip install requests)",
                error_type="missing_dependency",
                provider="omniroute",
                model=resolved_model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        try:
            resp = requests.post(
                f"{base_url}/videos/generations",
                json=payload,
                headers=headers,
                timeout=300,  # video rendering is far slower than images
            )
        except Exception as exc:
            logger.debug("Omniroute request failed", exc_info=True)
            return error_response(
                error=f"Omniroute request failed: {exc}",
                error_type="api_error",
                provider="omniroute",
                model=resolved_model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        if not resp.ok:
            return error_response(
                error=f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}",
                error_type="api_error",
                provider="omniroute",
                model=resolved_model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        try:
            data = (resp.json() or {}).get("data") or []
        except (ValueError, TypeError):
            data = []
        if not data:
            return error_response(
                error="Omniroute returned no video data",
                error_type="api_error",
                provider="omniroute",
                model=resolved_model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        first = data[0] if isinstance(data, list) else data
        if not isinstance(first, dict):
            first = {}

        fmt = (first.get("format") or "mp4").lower() or "mp4"

        try:
            url = first.get("url")
            b64 = first.get("b64_json")
            if url and isinstance(url, str) and url.strip():
                # Pass HTTP URLs straight through; avoids a second heavy fetch
                # and a local cache the agent may not need.
                video_out: str = url.strip()
            elif b64 and isinstance(b64, str) and b64.strip():
                raw = _b64.b64decode(b64)
                video_out = str(
                    save_bytes_video(raw, prefix="omniroute", extension=fmt)
                )
            else:
                return error_response(
                    error="Omniroute returned no video url or b64_json",
                    error_type="api_error",
                    provider="omniroute",
                    model=resolved_model,
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                )
        except Exception as exc:
            logger.debug("Failed to materialize Omniroute video", exc_info=True)
            return error_response(
                error=f"Failed to save video: {exc}",
                error_type="io_error",
                provider="omniroute",
                model=resolved_model,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        return success_response(
            video=video_out,
            model=resolved_model,
            prompt=prompt,
            modality=modality,
            aspect_ratio=aspect_ratio,
            duration=duration if duration is not None else 0,
            provider="omniroute",
        )
