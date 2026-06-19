"""Omniroute backends for Hermes — image generation and web search.

Routes Hermes through Omniroute, an OpenAI-compatible model router, using raw
``requests`` (no extra SDK). One plugin, two providers registered from a single
``register(ctx)``:

* image generation — :class:`OmnirouteImageGenProvider` (``POST /v1/images/generations``)
* web search        — :class:`OmnirouteWebSearchProvider` (``POST /v1/search``)

Shared credential/endpoint resolution (first hit wins):

* token    — ``OMNIROUTE_TOKEN`` / ``OMNIROUTE_API_KEY`` env, then
             ``image_gen.omniroute.token`` config
* base_url — ``OMNIROUTE_BASE_URL`` env, then ``image_gen.omniroute.base_url``
             config, then ``DEFAULT_BASE_URL``

Image model selection: ``OMNIROUTE_IMAGE_MODEL`` env, then ``image_gen.omniroute.model``
config, then ``image_gen.model`` config, then ``DEFAULT_MODEL`` (if listed), then the
first model from ``GET /images/generations``. The image catalog and each model's valid
sizes come from that listing (Omniroute's own image registry, distinct from the chat
``/models`` catalog); output (b64 or URL) is cached under ``$HERMES_HOME/cache/images/``.

Web search: optional provider pinning via ``OMNIROUTE_SEARCH_PROVIDER`` env (e.g.
``tavily-search``); otherwise Omniroute auto-selects from its configured providers.
"""

from __future__ import annotations

import base64
import logging
import os
import re
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
from agent.tts_provider import TTSProvider
from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://omniroute.josevictor.me/api/v1"

# Default TTS model when none configured. Users override via
# OMNIROUTE_TTS_MODEL env var or tts.omniroute.model config key.
DEFAULT_TTS_MODEL = "openai/tts-1"

# OpenAI-compatible TTS voice catalog (fixed set accepted by /v1/audio/speech).
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


def _read_plugin_version() -> str:
    """Read ``version:`` from plugin.yaml so User-Agent stays in sync across releases."""
    try:
        path = os.path.join(os.path.dirname(__file__), "plugin.yaml")
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^version:\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip("\"'")
    except Exception:
        logger.debug("Failed to read version from plugin.yaml", exc_info=True)
    return "0.2.0"


_PLUGIN_VERSION = _read_plugin_version()

# Cap per-result search snippet length (some providers return full page text).
_SEARCH_DESC_LIMIT = 500

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
                    "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
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


class OmnirouteWebSearchProvider(WebSearchProvider):
    """Omniroute ``POST /search`` backend (search-only; no extract)."""

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

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Query is required and must be a non-empty string"}

        token = _resolve_token()
        if not token:
            return {
                "success": False,
                "error": "OMNIROUTE_TOKEN not set. Export OMNIROUTE_TOKEN or OMNIROUTE_API_KEY.",
            }

        try:
            import requests
        except ImportError:
            return {"success": False, "error": "requests package not installed (pip install requests)"}

        payload: Dict[str, Any] = {"query": query, "max_results": limit}
        provider = _resolve_search_provider()
        if provider:
            payload["provider"] = provider

        try:
            resp = requests.post(
                f"{_resolve_base_url()}/search",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": f"hermes-omniroute-plugin/{_PLUGIN_VERSION}",
                },
                timeout=60,
            )
        except Exception as exc:
            logger.debug("Omniroute search request failed", exc_info=True)
            return {"success": False, "error": f"Omniroute search request failed: {exc}"}

        if not resp.ok:
            return {
                "success": False,
                "error": f"Omniroute returned HTTP {resp.status_code}: {resp.text[:500]}",
            }

        try:
            body = resp.json()
        except ValueError:
            return {"success": False, "error": "Omniroute returned a non-JSON response"}

        web: List[Dict[str, Any]] = []
        for idx, r in enumerate(body.get("results") or [], start=1):
            if not isinstance(r, dict):
                continue
            # Some providers return full page text in snippet/content; keep the
            # results list compact (the agent can fetch a URL for full content).
            desc = (r.get("snippet") or r.get("content") or "").strip()
            if len(desc) > _SEARCH_DESC_LIMIT:
                desc = desc[:_SEARCH_DESC_LIMIT].rstrip() + "…"
            web.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "description": desc,
                    "position": r.get("position") if r.get("position") is not None else idx,
                }
            )
        return {"success": True, "data": {"web": web}}


class OmnirouteTTSProvider(TTSProvider):
    """Omniroute ``/audio/speech`` backend (OpenAI-compatible TTS).

    Synthesizes text via ``POST /v1/audio/speech`` with an OpenAI-compatible
    payload (``{model, input, voice, response_format, speed}``).  Audio bytes
    are written directly to ``output_path`` and the absolute path returned.

    Model resolution (first wins): explicit ``model`` arg,
    ``OMNIROUTE_TTS_MODEL`` env, ``tts.omniroute.model`` config,
    ``DEFAULT_TTS_MODEL``.

    Token resolution mirrors the shared Omniroute service credentials
    (``_resolve_tts_token``): ``OMNIROUTE_TOKEN`` / ``OMNIROUTE_API_KEY`` env,
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
                    "key": "OMNIROUTE_TOKEN",
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
                "OMNIROUTE_TOKEN not set. Export OMNIROUTE_TOKEN or "
                "OMNIROUTE_API_KEY, or set tts.omniroute.token in config.yaml."
            )

        try:
            import requests
        except ImportError:
            raise RuntimeError(
                "requests package not installed (pip install requests)"
            )

        resolved_model = _resolve_tts_model(model)
        resolved_voice = voice or self.default_voice()
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


def register(ctx) -> None:
    """Plugin entry point — register Omniroute image-gen, web-search, and TTS providers."""
    ctx.register_image_gen_provider(OmnirouteImageGenProvider())
    ctx.register_web_search_provider(OmnirouteWebSearchProvider())
    ctx.register_tts_provider(OmnirouteTTSProvider())
