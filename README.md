# hermes-omniroute-plugin

Hermes Agent backends that route through
[Omniroute](https://omniroute.josevictor.me), an OpenAI-compatible model router.
One plugin, two capabilities:

- **Image generation** — `POST /v1/images/generations`
- **Web search** — `POST /v1/search`

## Install

Install from the Hermes web admin by pointing it at this repo, or copy into your
Hermes plugins dir:

```bash
git clone <this-repo> ~/.hermes/plugins/omniroute
# or: cp -r . ~/.hermes/plugins/omniroute
```

`register()` registers both the image-gen and web-search providers.

## Configure

Set the token (env preferred — config-file tokens are stored in plaintext):

```bash
export OMNIROUTE_TOKEN=...            # required (OMNIROUTE_API_KEY also accepted)
export OMNIROUTE_BASE_URL=...         # optional, default https://omniroute.josevictor.me/api/v1
export OMNIROUTE_IMAGE_MODEL=...      # optional, overrides config image model
export OMNIROUTE_SEARCH_PROVIDER=...  # optional, pin a search provider e.g. tavily-search
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
pinned via `OMNIROUTE_SEARCH_PROVIDER` / `web.omniroute.search_provider`. Per-result
snippets are capped at 500 chars.

## Usage

```
hermes -z "Generate an image of a corgi in a spacesuit"
hermes -z "Search the web for the latest Rust release notes"
```

Supported aspect ratios: `landscape`, `square`, `portrait`.

## Dev

`nix develop` opens a shell with Python + `requests` + `pyyaml` for local checks.
