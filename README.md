# hermes-omniroute-plugin

Hermes Agent backends route through
[Omniroute](https://omniroute.josevictor.me), OpenAI-compatible model router.
One plugin, three capabilities:

- **Image generation** `POST /v1/images/generations`
- **Web search** `POST /v1/search`
- **Text-to-speech** `POST /v1/audio/speech`

## Install

Install from the Hermes web admin by pointing it at this repo, or copy into your
Hermes plugins dir:

```bash
git clone <this-repo> ~/.hermes/plugins/omniroute
# or: cp -r . ~/.hermes/plugins/omniroute
```

`register()` registers all three: image-gen, web-search, and TTS providers.

## Configure

Set the token (env preferred — config-file tokens are stored in plaintext):

```bash
export OMNIROUTE_TOKEN=...            # required (OMNIROUTE_API_KEY also accepted)
export OMNIROUTE_BASE_URL=...         # optional, default https://omniroute.josevictor.me/api/v1
export OMNIROUTE_IMAGE_MODEL=... optional, overrides config image model
export OMNIROUTE_SEARCH_PROVIDER=... optional, pin search provider e.g. tavily-search
export OMNIROUTE_TTS_MODEL=... optional, overrides config TTS model (default: openai/tts-1)
```

Default model when none is configured: `antigravity/gemini-3.1-flash-image`.

Enable it and select as the active image backend:

```bash
hermes plugins enable omniroute
hermes tools        # -> Image Generation -> Omniroute
```

Or set it directly in `~/.hermes/config.yaml`:

```yaml
image_gen:
  provider: omniroute
  omniroute:
    base_url: https://omniroute.josevictor.me/api/v1   # optional
    model: <model-id>                                  # optional
    # token: <token>                                   # optional, prefer OMNIROUTE_TOKEN
```

The model picker is populated from Omniroute's image registry
(`GET /v1/images/generations`) — ~140 text-to-image models across many
providers. The request size is derived per-model from each model's
`supported_sizes` (pixel dims or aspect-ratio strings), mapped from the
requested `landscape`/`square`/`portrait`; models exposing only `1024x1024`
always return square. Generated images are cached under
`$HERMES_HOME/cache/images/`.

> Note: a model only works if the Omniroute instance has credentials for that
> provider (otherwise it returns `No credentials for image provider: <name>`).

### Web search

Select Omniroute as the web search backend in `~/.hermes/config.yaml`:

```yaml
web:
  search_backend: omniroute    # or web.backend as a fallback for both search/extract
  omniroute:
    search_provider: tavily-search   # optional; else Omniroute auto-selects
```

Search is search-only (no extract). Omniroute auto-selects among its configured
providers (Serper, Brave, Exa, Tavily, Perplexity, Google PSE, SearXNG, …) unless
pinned `OMNIROUTE_SEARCH_PROVIDER` / `web.omniroute.search_provider`. Per-result
snippets capped 500 chars.

### Text-to-speech

Select Omniroute TTS backend in `~/.hermes/config.yaml`:

```yaml
tts:
 provider: omniroute
 omniroute:
   model: openai/tts-1      # optional; OMNIROUTE_TTS_MODEL env overrides
   token: <token>           # optional, prefer OMNIROUTE_TOKEN
```

Synthesizes via OpenAI-compatible `POST /v1/audio/speech` payload
(`{model, input, voice, response_format, speed}`). Audio formats: `mp3` and
`opus`. The voice catalog exposes the six standard OpenAI voices (alloy, echo,
fable, onyx, nova, shimmer). `list_models()` fetches `GET /v1/models` and
filters TTS-capable entries (ids containing `tts`, `speech`, `audio`).

The provider registers and appears in `hermes tools` without a token; the
token is only required at synthesis time. Token resolution mirrors the
shared Omniroute credentials: `OMNIROUTE_TOKEN` / `OMNIROUTE_API_KEY` env,
`tts.omniroute.token` config, falling back to `image_gen.omniroute.token`.
Unsupported audio formats are clamped to `mp3`.

## Usage

```
hermes -z "Generate an image of a corgi in a spacesuit"
hermes -z "Search the web for the latest Rust release notes"
```

Supported aspect ratios: `landscape`, `square`, `portrait`.

## Dev

`nix develop` opens a shell with Python + `requests` + `pyyaml` for local checks.
