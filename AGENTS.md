# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-20 20:47 | **Commit:** 869caf0 | **Branch:** main

## OVERVIEW
A single Hermes Agent **backend plugin** that routes through Omniroute (an OpenAI-compatible model router) using raw `requests`. The repo root *is* the plugin: `register(ctx)` registers **five** providers — image generation, web search, text-to-speech, speech-to-text, video generation.

## STRUCTURE
```
.
├── __init__.py      # thin: register(ctx) + re-exports. Loader contract — keep top-level register
├── plugin.yaml      # Hermes plugin manifest (name, version, kind: backend)
├── _version.py      # reads version: from plugin.yaml → User-Agent (keep in sync)
├── config.py        # constants + env→config credential/model resolution chains
├── providers/       # one module per provider (see providers/AGENTS.md)
└── tests/           # pytest; stubs Hermes-internal modules before import
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Add/change a provider | `providers/<name>.py` + register in `__init__.py` |
| Credential / base_url / model resolution | `config.py` (`_resolve_*` fns) |
| Bump version | `plugin.yaml` `version:` only — `_version.py` reads it |
| Config keys & install docs | `README.md` |

## CONVENTIONS
- **No SDK** — raw `requests`, imported lazily *inside* methods (so `is_available()` can report missing dep, and module import never hard-fails).
- All Hermes-internal imports (`agent.*`, `hermes_cli.*`) only exist inside a Hermes install. Never add a hard dependency on them at import time beyond the existing `from agent.X import Y`.
- Config helpers return `{}`/`None` on **any** failure (never raise) — resolution chains degrade gracefully.
- `_version.py` default `"0.3.0"` must track `plugin.yaml`.

## ANTI-PATTERNS (THIS PROJECT)
- **DO NOT** hardcode a global image size map — sizes are per-model via `supported_sizes` (`_pick_size`). Most models reject a global map.
- **DO NOT** use the chat `/models` endpoint for the image catalog — image models live behind `GET /v1/images/generations`. Chat `/models` (1400+) produces "Invalid image model" / 404.
- **DO NOT** assume a listed image model works — it only does if the Omniroute instance has that provider's credentials; failure surfaces at `generate()` time, not in `list_models()`.

## COMMANDS
```bash
nix develop                                          # dev shell: python3 + requests + pyyaml (NO pytest)
# Run tests (flake lacks pytest; system python lacks requests/httpx — use an ad-hoc shell with all three)
nix-shell -p 'python3.withPackages(ps:[ps.requests ps.httpx ps.pytest])' --run 'python3 -m pytest tests -q'
```

## NOTES
- `DEFAULT_MODEL` (`antigravity/gemini-3.1-flash-image`) is set to a provider known-configured on the target instance.
- Token: env `OMNIROUTE_TOKEN`/`OMNIROUTE_API_KEY` preferred over config-file (plaintext).
- `.omo/` `.omc/` `.claude/` are gitignored tool state — ignore them.

## References

Always check both references when in doupt about the integrations
- **Omniroute API Documentation** https://omniroute.josevictor.me/docs 
- **Hermes Plugins Documentation** https://hermes-agent.nousresearch.com/docs/developer-guide 