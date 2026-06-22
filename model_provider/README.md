# OmniRoute Model Provider

Hermes Agent model provider plugin that routes chat/completions through
[OmniRoute](https://omniroute.josevictor.me) — an OpenAI-compatible model router.

## Install

```bash
cp -r model_provider/ ~/.hermes/plugins/model-providers/omniroute/
```

## Configure

Set your API key (env preferred):

```bash
export OMNIROUTE_TOKEN=...         # or OMNIROUTE_API_KEY
export OMNIROUTE_BASE_URL=...     # optional, default https://omniroute.josevictor.me/api/v1
```

Or configure in `~/.hermes/config.yaml`:

```yaml
model:
  provider: omniroute
  omniroute:
    base_url: https://omniroute.josevictor.me/api/v1
    model: openai/gpt-4o-mini
```

## Models

Model list is fetched live from `GET /v1/models`. When the endpoint is
unreachable, curated fallback models are shown instead.
