# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single [Hermes Agent](https://hermes-agent.nousresearch.com) **backend plugin** that routes through [Omniroute](https://omniroute.josevictor.me), an OpenAI-compatible model router. The repo root *is* the plugin (`__init__.py` + `plugin.yaml`); installed via the Hermes web admin from the repo URL, or copied to `~/.hermes/plugins/omniroute/`. One `register(ctx)` registers **two** providers: image generation and web search.

## Architecture

`register(ctx)` wires both providers from the single `__init__.py`:
- `ctx.register_image_gen_provider(OmnirouteImageGenProvider())`
- `ctx.register_web_search_provider(OmnirouteWebSearchProvider())`

Both subclass Hermes ABCs (`agent.image_gen_provider.ImageGenProvider`, `agent.web_search_provider.WebSearchProvider`) that **only exist inside a Hermes install** — see Testing. They share the module-level `_resolve_token()` / `_resolve_base_url()` helpers (same Omniroute service + credentials).

**Image generation** (`OmnirouteImageGenProvider`): implement `name`, `generate()`, plus optional `display_name`/`is_available()`/`list_models()`/`default_model()`/`get_setup_schema()`. `generate()` returns `success_response(...)`/`error_response(...)` dicts built from Hermes helpers; b64/url output cached to `$HERMES_HOME/cache/images/` via `save_b64_image`/`save_url_image`.

**Web search** (`OmnirouteWebSearchProvider`): search-only (no `extract`). Implement `name`, `is_available()`, `search(query, limit=5)`. Unlike image gen, the web-search contract uses **raw dict envelopes, not helper functions**: success is `{"success": True, "data": {"web": [{title, url, description, position}]}}`, failure is `{"success": False, "error": str}`. `search()` POSTs to `/v1/search` with `{query, max_results, [provider]}` and maps Omniroute results (`title`/`url`/`snippet`/`position`) into that envelope. Snippets are capped at `_SEARCH_DESC_LIMIT` (500) — some Omniroute providers return full page text in `snippet`. Optional provider pinning via `OMNIROUTE_SEARCH_PROVIDER` env / `web.omniroute.search_provider`; otherwise Omniroute auto-selects. Hermes selects this backend via `web.search_backend: omniroute` (or `web.backend`).

Two non-obvious decisions drive the image-gen design:

- **Model catalog comes from `GET /v1/images/generations`, NOT the chat `/models` endpoint.** Omniroute keeps a *separate* image-model registry behind the generations path. The chat `/models` list (1400+ entries) does not validate against the image route — using it produces "Invalid image model" / 404 errors. `_fetch_registry()` hits the generations endpoint; `list_models()` filters it to text-capable models (drops pure image-edit endpoints with no `text` input modality).
- **Image size is per-model, derived from each model's `supported_sizes`.** Different models accept different size tokens — pixel dims (`1792x1024`) for some, aspect-ratio strings (`16:9`) for others, square-only (`1024x1024`) for many. `_pick_size()` maps the Hermes aspect (`landscape`/`square`/`portrait`) onto whatever that specific model supports (falling back to square). Do **not** hardcode a global size map — it will be rejected by most models.

Config resolution precedence (see module docstring): token = `OMNIROUTE_TOKEN`/`OMNIROUTE_API_KEY` env → `image_gen.omniroute.token` config; base_url = `OMNIROUTE_BASE_URL` env → config → `DEFAULT_BASE_URL`; model = `OMNIROUTE_IMAGE_MODEL` env → `image_gen.omniroute.model` → `image_gen.model` → `DEFAULT_MODEL` (if listed) → first listed. Config is read from Hermes' `config.yaml` via `hermes_cli.config.load_config`.

**Runtime gotcha:** a listed model only works if the Omniroute instance has credentials for that provider — otherwise generation returns `No credentials for image provider: <name>`. `list_models()` cannot detect this (the registry exposes no per-instance credential status), so all models are listed and failures surface at generation time. `DEFAULT_MODEL` is set to a provider known to be configured on the target instance.

## Dev environment

```bash
nix develop          # shell with python3 + requests + pyyaml (the flake is dev-only, not packaging)
```

The system Python lacks `requests`; always run code through `nix develop`.

## Testing

There is no test framework. Because `agent.image_gen_provider`, `agent.web_search_provider`, and `hermes_cli` only exist inside a Hermes install, verify by **stubbing those modules** before importing `__init__.py`, then drive the provider directly. (`__init__.py` imports both `agent.image_gen_provider` and `agent.web_search_provider` at module load, so both must be stubbed — `agent.web_search_provider.WebSearchProvider` just needs to be an empty base class.) Pattern:

```python
import sys, types
igp = types.ModuleType("agent.image_gen_provider")
igp.DEFAULT_ASPECT_RATIO = "square"
class _P: ...
igp.ImageGenProvider = _P
igp.resolve_aspect_ratio = lambda a: a if a in ("landscape","square","portrait") else "square"
igp.success_response = lambda **k: {"success": True, **k}
igp.error_response   = lambda **k: {"success": False, **k}
igp.save_b64_image   = lambda b64, prefix="x", extension="png": ...   # decode + write to verify bytes
igp.save_url_image   = lambda u, prefix="x": ...
sys.modules["agent"] = types.ModuleType("agent")
sys.modules["agent.image_gen_provider"] = igp
# then importlib-load __init__.py and call OmnirouteImageGenProvider().generate(...)
```

Run it under `nix develop` with a token in the env, e.g.:

```bash
nix develop --command bash -c 'OMNIROUTE_TOKEN="$OMNIROUTE_API_KEY" python3 your_test.py'
```

Pure-logic helpers (`_pick_size`, `_detect_extension`, `_orientation`) are unit-testable without a token; `list_models`/`generate` need a live token. To probe the API directly, `curl` `GET`/`POST` `https://omniroute.josevictor.me/api/v1/images/generations` with `Authorization: Bearer <token>`.

## Installing into Hermes

Copy/clone the repo to `~/.hermes/plugins/image_gen/omniroute/`, `hermes plugins enable omniroute`, and set `image_gen.provider: omniroute` in `~/.hermes/config.yaml`. See README.md for the full config block.
