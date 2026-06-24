# hermes-omniroute-plugin

Hermes Agent backends route through
[Omniroute](https://omniroute.josevictor.me) OpenAI-compatible model router.
One plugin, six capabilities:

- **Model provider**      `POST /v1/chat/completions` (Hermes AIAgent routing)
- **Image generation**    `POST /v1/images/generations`
- **Web search**          `POST /v1/search`
- **Web extract**         `POST /v1/web/fetch` (URL scraping)
- **Text-to-speech**      `POST /v1/audio/speech`
- **Speech-to-text**      `POST /v1/audio/transcriptions`

## Install

Install from the Hermes web admin by pointing it at this repo, or copy into your
Hermes plugins dir:

```bash
git clone <this-repo> ~/.hermes/plugins/omniroute
# or: cp -r . ~/.hermes/plugins/omniroute
```

`register()` registers all four: image-gen, web-search, TTS, and STT providers.

The model provider lives in `model_provider/` and must be installed separately:

```bash
cp -r model_provider/ ~/.hermes/plugins/model-providers/omniroute/
```

This registers OmniRoute as a model provider for `hermes chat` / `AIAgent` routing
(OpenAI-compatible `POST /v1/chat/completions`).

## Configure

Set the token (env preferred — config-file tokens are stored in plaintext):

```bash
export OMNIROUTE_TOKEN=...            # required (OMNIROUTE_API_KEY also accepted)
export OMNIROUTE_BASE_URL=...         # optional, default https://omniroute.josevictor.me/api/v1
export OMNIROUTE_IMAGE_MODEL=... optional, overrides config image model
export OMNIROUTE_SEARCH_PROVIDER=... optional, pin search provider e.g. tavily-search
export OMNIROUTE_FETCH_PROVIDER=... optional, pin extract/scrape provider e.g. tavily-search
export OMNIROUTE_TTS_MODEL=... optional, overrides config TTS model (default: openai/tts-1)
export OMNIROUTE_STT_MODEL=... optional, overrides config STT model (default: deepgram/nova-3)
```

Default model when none is configured: `antigravity/gemini-3.1-flash-image`.

### Dashboard

The OmniRoute tab in the Hermes dashboard configures the connection (API key,
base URL) and lets you **pick** the image, TTS, and provider (chat) models from
searchable dropdowns populated live from OmniRoute's catalog
(`/v1/images/generations` for image, `/v1/models` filtered for TTS/chat).
Connection values persist to the `omniroute.settings` store; model selections
persist to their canonical Hermes paths (`image_gen.omniroute.model`,
`tts.omniroute.model`, `model.omniroute.model`). Env vars still take precedence
and are flagged as read-only in the UI.

### Model provider

Select OmniRoute as your model provider in `~/.hermes/config.yaml`:

```yaml
model:
  provider: omniroute
  omniroute:
    base_url: https://omniroute.josevictor.me/api/v1   # optional
    model: openai/gpt-4o-mini                         # optional
    # token: <token>                                  # optional, prefer OMNIROUTE_TOKEN
```

The model list is fetched live from `GET /v1/models` on the first use
(or when explicitly listing models). Fallback models are curated when the
live catalog is unreachable.

```bash
hermes -z "What is the capital of France?" --provider omniroute --model openai/gpt-4o
```

### Image generation

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

Omniroute auto-selects among its configured providers (Serper, Brave, Exa,
Tavily, Perplexity, Google PSE, SearXNG, …) unless pinned
`OMNIROUTE_SEARCH_PROVIDER` / `web.omniroute.search_provider`. Per-result
snippets capped 500 chars. URL scraping is handled by the separate **Web
extract** capability below.

### Web extract (URL scraping)

Select Omniroute as the extract backend in `~/.hermes/config.yaml` to scrape
full page content from URLs:

```yaml
web:
  extract_backend: omniroute   # or web.backend as a fallback for both search/extract
  omniroute:
    fetch_provider: tavily-search   # optional; else Omniroute auto-selects
```

The same `OmnirouteWebSearchProvider` services both `web_search` and
`web_extract` (it advertises `supports_extract()`); no separate registration is
needed. Extract wraps `POST /v1/web/fetch`, which takes one URL per call — the
provider loops over the requested URLs and returns one result per URL
(`{url, title, content, raw_content, metadata}`), with `content` as page
markdown and `provider`/`links`/`screenshot_url` folded into `metadata`. A
per-URL failure is reported in that URL's `error` field rather than aborting the
whole batch. Pin a provider with `OMNIROUTE_FETCH_PROVIDER` /
`web.omniroute.fetch_provider`.

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

### Speech-to-text

Hermes calls speech-to-text "transcription". Select Omniroute as the STT
backend in `~/.hermes/config.yaml`:

```yaml
stt:
  provider: omniroute
  omniroute:
    model: deepgram/nova-3   # optional; OMNIROUTE_STT_MODEL env overrides
    # token: <token>         # optional, prefer OMNIROUTE_API_KEY
```

Transcribes via OpenAI-compatible `POST /v1/audio/transcriptions` (multipart
upload: `{file, model, language, response_format}`) and returns the transcript
text. Known transcription models: `deepgram/nova-3`, `assemblyai/best`. Like
TTS, the provider registers and appears in `hermes tools` without a token; the
token is only required at transcribe time, and resolves from `OMNIROUTE_API_KEY`
/ `OMNIROUTE_TOKEN` env, `stt.omniroute.token` config, then the shared Omniroute
credentials.

> Note: a model only works if the Omniroute instance has credentials for that
> transcription provider — failure surfaces at transcribe time.

## Usage

```
hermes -z "Generate an image of a corgi in a spacesuit"
hermes -z "Search the web for the latest Rust release notes"
```

Supported aspect ratios: `landscape`, `square`, `portrait`.

## Dev

`nix develop` opens a shell with Python + `requests` + `pyyaml` for local checks.
