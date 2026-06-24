# providers/

One module per Hermes provider capability. Each subclasses a Hermes ABC imported at module top (`agent.image_gen_provider`, `agent.web_search_provider`, `agent.tts_provider`, `agent.transcription_provider`) — those only exist inside a Hermes install.

## THE RETURN CONTRACTS DIFFER (most important gotcha)
| Module | Method | Success | Failure |
|--------|--------|---------|---------|
| `image_gen.py` | `generate()` | `success_response(...)` helper | `error_response(...)` helper |
| `web_search.py` | `search()` | **raw dict** `{"success": True, "data": {"web": [...]}}` | **raw dict** `{"success": False, "error": str}` |
| `tts.py` | `synthesize()` | **returns abs path str**, writes bytes to `output_path` | **raises** `ValueError`/`RuntimeError` |
| `stt.py` | `transcribe()` | **raw envelope** `{"success": True, "transcript": str, "provider": "omniroute"}` | **raw envelope** `{"success": False, "transcript": "", "error": str, "provider": ...}` — **never raises** |

Web-search uses raw envelopes (no helper fns exist for it); TTS raises instead of returning error dicts; STT must NEVER raise (convert every exception to the error envelope). Do not unify these.

## PER-MODULE NOTES
- **image_gen.py**: `_fetch_registry()` caches `GET /images/generations`; `list_models()` drops pure image-edit models (no `text` input modality). `_resolve_model()` is a 5-step chain — env/provider-config trusted as-is, global `image_gen.model` validated against registry. `_detect_extension()` sniffs b64 magic bytes (providers return jpg/webp under a png default). `_pick_size()` maps aspect→model's `supported_sizes`, falls back to square then `_FALLBACK_SIZE`.
- **web_search.py**: search-only (no `extract`). Snippet capped at `_SEARCH_DESC_LIMIT` (500) — some providers dump full page text. Optional provider pin via `_resolve_search_provider()`.
- **tts.py**: `is_available()` does **not** require a token (unlike image-gen/web-search) — provider must show in `hermes tools` for setup. Fixed 6-voice OpenAI catalog. `list_models()` keyword-filters `GET /models` (`tts`/`speech`/`audio`). Format clamped to `mp3`/`opus`.
- **stt.py**: registered via `ctx.register_transcription_provider` (STT = "transcription" in Hermes; config slot `stt.provider`). `is_available()` token-free like TTS. `list_models()` returns a **curated static catalog** (`_STT_MODEL_CATALOG`) — the transcription registry is not listable and ids (`deepgram/nova-3`, `assemblyai/best`) carry no filterable keyword. `transcribe()` posts multipart `POST /audio/transcriptions`, parses `text` from JSON. Name `omniroute` must avoid the reserved built-in STT names (`local`/`local_command`/`groq`/`openai`/`mistral`/`xai`).

## CONVENTIONS
- `name`/`display_name` all return `"omniroute"`/`"Omniroute"`.
- `import requests` lazily inside each method, never at top — keeps import safe and lets `is_available()` detect the missing dep.
- All HTTP sends `User-Agent: hermes-omniroute-plugin/{_PLUGIN_VERSION}`.
- New provider: add module here, re-export in `providers/__init__.py`, wire in root `__init__.py` `register()`.
