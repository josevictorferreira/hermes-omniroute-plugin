"""Omniroute image-generation provider and size/orientation helpers.

Wraps ``POST /v1/images/generations``. Model selection validates the global
``image_gen.model`` against Omniroute's image registry; per-instance overrides
(env / ``image_gen.omniroute.model``) are trusted as-is.
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

try:  # Added in newer Hermes; older installs lack it (would break plugin load).
    from agent.image_gen_provider import normalize_reference_images
except ImportError:
    def normalize_reference_images(urls: Any) -> List[str]:
        """Fallback for Hermes builds without the helper: coerce to a clean URL list."""
        if not urls:
            return []
        if isinstance(urls, str):
            urls = [urls]
        return [u.strip() for u in urls if isinstance(u, str) and u.strip()]

from .._version import _PLUGIN_VERSION
from ..config import (
    DEFAULT_MODEL,
    _FALLBACK_SIZE,
    _load_config,
    _omniroute_config,
    _resolve_base_url,
    _resolve_token,
)

logger = logging.getLogger(__name__)


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


def _is_valid_size(s: str) -> bool:
    """Return True if *s* looks like a real size token (e.g. ``1024x1024`` or ``16:9``)."""
    sep = ":" if ":" in s else ("x" if "x" in s.lower() else None)
    if sep is None:
        return False
    try:
        parts = s.lower().split(sep)
        if len(parts) != 2:
            return False
        w, h = int(parts[0]), int(parts[1])
        return w > 0 and h > 0
    except (ValueError, TypeError, IndexError):
        return False


def _pick_size(supported: Optional[List[str]], aspect: str) -> str:
    """Map a Hermes aspect ratio to one of a model's supported sizes."""
    options = [s for s in (supported or []) if isinstance(s, str) and _is_valid_size(s)]
    if not options:
        return _FALLBACK_SIZE
    for s in options:  # exact orientation match
        if _orientation(s) == aspect:
            return s
    for s in options:  # else prefer square
        if _orientation(s) == "square":
            return s
    return options[0]



# ---------------------------------------------------------------------------
# Source-image loading (for image-to-image / edit)
# ---------------------------------------------------------------------------


def _load_image_bytes(ref: str) -> tuple[bytes, str]:
    """Load image bytes from a URL or local file path.

    Returns ``(data, filename)``. Raises on any network / IO error so the
    caller can surface a clean error_response.
    """
    ref = ref.strip()
    lower = ref.lower()
    if lower.startswith(("http://", "https://")):
        import requests

        resp = requests.get(ref, timeout=60)
        resp.raise_for_status()
        name = ref.split("?", 1)[0].rsplit("/", 1)[-1] or "image.png"
        return resp.content, name
    if lower.startswith("data:"):
        header, _, b64 = ref.partition(",")
        ext = "png"
        if "image/" in header:
            ext = header.split("image/", 1)[1].split(";", 1)[0] or "png"
        return base64.b64decode(b64), f"image.{ext}"
    # Local file path.
    with open(ref, "rb") as fh:
        data = fh.read()
    name = os.path.basename(ref) or "image.png"
    return data, name


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
        registry = self._fetch_registry()
        # Step 1: OMNIROUTE_IMAGE_MODEL env (explicit override — trust it)
        env = os.environ.get("OMNIROUTE_IMAGE_MODEL")
        if env and env.strip():
            return env.strip()
        # Step 2: image_gen.omniroute.model config (provider-specific — trust it)
        sub = _omniroute_config().get("model")
        if isinstance(sub, str) and sub.strip():
            return sub.strip()
        # Step 3: image_gen.model config (global — may not be in Omniroute registry)
        top = _load_config().get("model")
        if isinstance(top, str) and top.strip():
            candidate = top.strip()
            if candidate in registry:
                return candidate
            logger.info(
                "Global image_gen.model %r not in Omniroute registry; "
                "falling back to default model selection.",
                candidate,
            )
        # Step 4: DEFAULT_MODEL if listed in registry
        ids = [x["id"] for x in self.list_models()]
        if DEFAULT_MODEL in ids:
            return DEFAULT_MODEL
        # Step 5: first available model from registry
        return ids[0] if ids else DEFAULT_MODEL

    def capabilities(self) -> dict[str, Any]:
        """Advertise text-to-image and image-to-image/edit modalities."""
        return {"modalities": ["text", "image"], "max_reference_images": 16}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Omniroute",
            "badge": "paid",
            "tag": "OpenAI-compatible image generation via the Omniroute router",
            "env_vars": [
                {
                    "key": "OMNIROUTE_API_KEY",
                    "prompt": "Omniroute API token",
                    "url": "https://omniroute.josevictor.me",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
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
                    "OMNIROUTE_API_KEY not set. Run `hermes tools` > Image "
                    "Generation > Omniroute to configure, or export "
                    "OMNIROUTE_API_KEY."
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

        # Collect source images for image-to-image / edit.
        sources: List[str] = []
        if isinstance(image_url, str) and image_url.strip():
            sources.append(image_url.strip())
        refs = normalize_reference_images(reference_image_urls) or []
        sources.extend(refs)
        sources = sources[:16]  # cap at 16 source images
        is_edit = bool(sources)
        modality = "image" if is_edit else "text"

        common_headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
        }

        try:
            if is_edit:
                edit_payload = {
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                }
                if image_url and image_url.strip():
                    edit_payload["image"] = image_url.strip()
                if refs:
                    edit_payload["reference_images"] = refs
                resp = requests.post(
                    f"{base_url}/images/edits",
                    json=edit_payload,
                    headers={**common_headers, "Content-Type": "application/json"},
                    timeout=120,
                )
            else:
                payload = {"model": model, "prompt": prompt, "size": size}
                resp = requests.post(
                    f"{base_url}/images/generations",
                    json=payload,
                    headers={**common_headers, "Content-Type": "application/json"},
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
        except Exception:
            logger.debug("Omniroute non-JSON response", exc_info=True)
            return error_response(
                error="Omniroute returned non-JSON response",
                error_type="empty_response",
                provider="omniroute",
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not data:
            return error_response(
                error="Omniroute returned empty data array",
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

        image_ref = None
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
                    "Omniroute image URL not cached (%s); falling back to bare URL (%s).",
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
            modality=modality,
            extra=extra,
        )
