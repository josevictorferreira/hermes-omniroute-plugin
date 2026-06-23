# Changelog

## [0.3.0] - 2026-06-22

### Added
- **Image editing**: New endpoint routing for image editing via JSON payload (#14)
- **Dashboard**: Model provider configuration area in Omniroute dashboard (#15, #8e54969)
- **Dashboard**: Dedicated Omniroute configuration section (38cc856)
- **TTS**: Text-to-Speech capability via `/v1/audio/speech` (0b92972)
- **Docs**: AGENTS.md for providers (68d54f7)
- **CI**: GitHub Actions workflow to run full test suite (72785f4)

### Fixed
- **CI**: Missing FastAPI dependency (97d7133, #16)
- **CI**: Workflow syntax corrections and simplified test commands (02e9d40)
- **CI**: OMNIROUTE_TOKEN dummy env for web search tests (e0be4ec)
- **CI**: Pydantic install and FastAPI model_rebuild mock fixes (e5001fd)
- **Tests**: Clear OMNIROUTE env vars in dashboard API tests (46820d6)
- **Tests**: Provider unit tests and dashboard integration test fix (f44978f, #15)

### Changed
- **Dashboard**: Reduced OmniRoute settings page to API key + base URL only; model/provider selection deferred to main Hermes config
- runs push anybranch (2b1fd61)

## [0.2.0] - Previous release

Initial stable release with image generation, web search, and TTS providers.
