"""Omniroute image generation backend.

Routes Hermes image generation through Omniroute, an OpenAI-compatible model
router (``POST /v1/images/generations``). Implemented as an
:class:`ImageGenProvider` using raw ``requests`` (mirrors the bundled xAI
backend) so no extra SDK dependency is required.

Resolution precedence (first hit wins):

* token    — ``OMNIROUTE_TOKEN`` / ``OMNIROUTE_API_KEY`` env, then
             ``image_gen.omniroute.token`` config
* base_url — ``OMNIROUTE_BASE_URL`` env, then ``image_gen.omniroute.base_url``
             config, then ``DEFAULT_BASE_URL``
* model    — ``OMNIROUTE_IMAGE_MODEL`` env, then ``image_gen.omniroute.model``
             config, then ``image_gen.model`` config, then ``DEFAULT_MODEL``
             (if listed), then the first model from ``GET /images/generations``

The model catalog and each model's valid sizes come from Omniroute's
``GET /images/generations`` listing (its own image registry — distinct from the
chat ``/models`` catalog). Output (b64 or URL) is cached under
``$HERMES_HOME/cache/images/``.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://omniroute.josevictor.me/api/v1"

# Preferred default model when none is configured. Instance must have credentials
# for its provider; users can override via image_gen.omniroute.model or the
# OMNIROUTE_IMAGE_MODEL env var.
DEFAULT_MODEL = "antigravity/gemini-3.1-flash-image"

_FALLBACK_SIZE = "1024x1024"


def _orientation(size: str) -> str:
    """Classify a supported-size token as landscape / portrait / square.

    Handles both pixel dims ("1792x1024") and ratio strings ("16:9").
    """
    sep = ":" if ":" in size else ("x" if "x" in size.lower() else None)
    if sep is None:
        return "square"
    try:
        w, h = (int(p) for p in size.lower().split(sep)[:2])
    except (ValueError, TypeError):
        return "square"
    if w > h:
        return "landscape"
    if h > w:
        return "portrait"
    return "square"


def _detect_extension(b64: str, default: str = "png") -> str:
    """Sniff image format from base64 magic bytes so cached files are honest.

    Omniroute proxies many providers; some return JPEG/WebP even though the
    default cache extension is png. Returns a lowercase extension string.
    """
    try:
        head = b64.split(",", 1)[1] if b64.startswith("data:") else b64
        chunk = head[:24]
        chunk = chunk[: len(chunk) - (len(chunk) % 4)]
        raw = base64.b64decode(chunk)
    except Exception:
        return default
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith(b"\x89PNG"):
        return "png"
    if raw.startswith(b"GIF8"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return default


def _pick_size(supported: Optional[List[str]], aspect: str) -> str:
    """Map a Hermes aspect ratio to one of a model's supported sizes."""
    options = [s for s in (supported or []) if isinstance(s, str)]
    if not options:
        return _FALLBACK_SIZE
    for s in options:  # exact orientation match
        if _orientation(s) == aspect:
            return s
    for s in options:  # else prefer square
        if _orientation(s) == "square":
            return s
    return options[0]


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


class OmnirouteImageGenProvider(ImageGenProvider):
    """Omniroute ``/images/generations`` backend."""

    def __init__(self) -> None:
        # id -> model metadata dict from GET /images/generations
        self._registry: Optional[Dict[str, Dict[str, Any]]] = None

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

    def _fetch_registry(self) -> Dict[str, Dict[str, Any]]:
        """Load Omniroute's image-model registry (GET /images/generations)."""
        if self._registry is not None:
            return self._registry

        registry: Dict[str, Dict[str, Any]] = {}
        token = _resolve_token()
        if token:
            try:
                import requests

                resp = requests.get(
                    f"{_resolve_base_url()}/images/generations",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                resp.raise_for_status()
                for m in resp.json().get("data") or []:
                    if isinstance(m, dict) and isinstance(m.get("id"), str):
                        registry[m["id"]] = m
            except Exception as exc:
                logger.debug("Could not fetch Omniroute image models: %s", exc)

        self._registry = registry
        return registry

    def list_models(self) -> List[Dict[str, Any]]:
        models: List[Dict[str, Any]] = []
        for mid, m in self._fetch_registry().items():
            # Skip pure image-editing endpoints (no text input) — not text-to-image.
            if "text" not in (m.get("input_modalities") or ["text"]):
                continue
            models.append({"id": mid, "display": m.get("name") or mid, "price": "varies"})
        return models

    def default_model(self) -> Optional[str]:
        return self._resolve_model()

    def _resolve_model(self) -> Optional[str]:
        env = os.environ.get("OMNIROUTE_IMAGE_MODEL")
        if env:
            return env.strip()
        sub = _omniroute_config().get("model")
        if isinstance(sub, str) and sub.strip():
            return sub.strip()
        top = _load_config().get("model")
        if isinstance(top, str) and top.strip():
            return top.strip()
        ids = [x["id"] for x in self.list_models()]
        if DEFAULT_MODEL in ids:
            return DEFAULT_MODEL
        return ids[0] if ids else DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Omniroute",
            "badge": "paid",
            "tag": "OpenAI-compatible image generation via the Omniroute router",
            "env_vars": [
                {
                    "key": "OMNIROUTE_TOKEN",
                    "prompt": "Omniroute API token (or set OMNIROUTE_API_KEY)",
                    "url": "https://omniroute.josevictor.me",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="omniroute",
                aspect_ratio=aspect,
            )

        token = _resolve_token()
        if not token:
            return error_response(
                error=(
                    "OMNIROUTE_TOKEN not set. Run `hermes tools` -> Image "
                    "Generation -> Omniroute to configure, or export "
                    "OMNIROUTE_TOKEN."
                ),
                error_type="auth_required",
                provider="omniroute",
                aspect_ratio=aspect,
            )

        try:
            import requests
        except ImportError:
            return error_response(
                error="requests package not installed (pip install requests)",
                error_type="missing_dependency",
                provider="omniroute",
                aspect_ratio=aspect,
            )

        model = kwargs.get("model") or self._resolve_model()
        if not model:
            return error_response(
                error=(
                    "No image model configured. Set image_gen.omniroute.model "
                    "in config.yaml or OMNIROUTE_IMAGE_MODEL."
                ),
                error_type="invalid_argument",
                provider="omniroute",
                aspect_ratio=aspect,
            )

        base_url = _resolve_base_url()
        meta = self._fetch_registry().get(model) or {}
        size = _pick_size(meta.get("supported_sizes"), aspect)
        payload = {"model": model, "prompt": prompt, "size": size}

        try:
            resp = requests.post(
                f"{base_url}/images/generations",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": "hermes-omniroute-plugin/0.1.0",
                },
                timeout=120,
            )
        except Exception as exc:
            logger.debug("Omniroute request failed", exc_info=True)
            return error_response(
                error=f"Omniroute request failed: {exc}",
                error_type="api_error",
                provider="omniroute",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not resp.ok:
            return error_response(
                error=f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}",
                error_type="api_error",
                provider="omniroute",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            data = resp.json().get("data") or []
        except ValueError:
            return error_response(
                error="Omniroute returned a non-JSON response",
                error_type="empty_response",
                provider="omniroute",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not data:
            return error_response(
                error="Omniroute returned no image data",
                error_type="empty_response",
                provider="omniroute",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = data[0] if isinstance(data[0], dict) else {}
        b64 = first.get("b64_json")
        url = first.get("url")
        revised_prompt = first.get("revised_prompt")

        if b64:
            try:
                image_ref = str(
                    save_b64_image(b64, prefix="omniroute", extension=_detect_extension(b64))
                )
            except Exception as exc:
                return error_response(
                    error=f"Could not save image to cache: {exc}",
                    error_type="io_error",
                    provider="omniroute",
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
        elif url:
            try:
                image_ref = str(save_url_image(url, prefix="omniroute"))
            except Exception as exc:
                logger.warning(
                    "Omniroute image URL %s could not be cached (%s); "
                    "falling back to bare URL.",
                    url,
                    exc,
                )
                image_ref = url
        else:
            return error_response(
                error="Omniroute response contained neither b64_json nor URL",
                error_type="empty_response",
                provider="omniroute",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        extra: Dict[str, Any] = {"size": size}
        if revised_prompt:
            extra["revised_prompt"] = revised_prompt

        return success_response(
            image=image_ref,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="omniroute",
            extra=extra,
        )


def register(ctx) -> None:
    """Plugin entry point — register the Omniroute provider."""
    ctx.register_image_gen_provider(OmnirouteImageGenProvider())
