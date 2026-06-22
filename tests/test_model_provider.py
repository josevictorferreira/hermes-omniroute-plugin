"""Unit tests for the OmniRoute model provider plugin."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def registered_profile():
    """Return the profile registered by the model_provider module."""
    providers_mod = sys.modules["providers"]
    assert providers_mod._registered, "register_provider was never called"
    return providers_mod._registered[-1]


class TestRegistration:
    def test_profile_registered(self, registered_profile):
        """register_provider was called exactly once with a profile."""
        providers_mod = sys.modules["providers"]
        assert len(providers_mod._registered) >= 1

    def test_profile_name(self, registered_profile):
        assert registered_profile.name == "omniroute"

    def test_profile_aliases(self, registered_profile):
        assert "omni" in registered_profile.aliases

    def test_profile_display_name(self, registered_profile):
        assert registered_profile.display_name == "OmniRoute"

    def test_profile_auth_type(self, registered_profile):
        assert registered_profile.auth_type == "api_key"

    def test_profile_base_url(self, registered_profile):
        assert "omniroute.josevictor.me" in registered_profile.base_url

    def test_profile_env_vars(self, registered_profile):
        assert "OMNIROUTE_TOKEN" in registered_profile.env_vars
        assert "OMNIROUTE_API_KEY" in registered_profile.env_vars
        assert "OMNIROUTE_BASE_URL" in registered_profile.env_vars

    def test_profile_fallback_models(self, registered_profile):
        assert len(registered_profile.fallback_models) > 0

    def test_profile_default_aux_model(self, registered_profile):
        assert registered_profile.default_aux_model


class TestFetchModels:
    def test_fetch_models_success(self, registered_profile):
        """fetch_models parses OpenAI-style /v1/models response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "object": "list",
            "data": [
                {"id": "openai/gpt-4o", "object": "model"},
                {"id": "anthropic/claude-3.5-sonnet", "object": "model"},
                {"id": "openai/gpt-4o-mini", "object": "model"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            result = registered_profile.fetch_models(api_key="test-token")

        assert result is not None
        assert "openai/gpt-4o" in result
        assert "anthropic/claude-3.5-sonnet" in result
        assert "openai/gpt-4o-mini" in result
        assert len(result) == 3

    def test_fetch_models_sends_auth_header(self, registered_profile):
        """fetch_models sends Bearer token in Authorization header."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "test-model"}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            registered_profile.fetch_models(api_key="my-secret-token")

        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer my-secret-token"

    def test_fetch_models_uses_env_token(self, registered_profile):
        """fetch_models falls back to OMNIROUTE_TOKEN env var."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "m"}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch.dict("os.environ", {"OMNIROUTE_TOKEN": "env-token"}):
            with patch("httpx.Client", return_value=mock_client):
                registered_profile.fetch_models()

        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer env-token"

    def test_fetch_models_network_error_returns_none(self, registered_profile):
        """fetch_models returns None on network failure."""
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = registered_profile.fetch_models(api_key="test")

        assert result is None

    def test_fetch_models_http_error_returns_none(self, registered_profile):
        """fetch_models returns None on HTTP error status."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            result = registered_profile.fetch_models(api_key="bad")

        assert result is None

    def test_fetch_models_empty_response(self, registered_profile):
        """fetch_models handles empty model list."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            result = registered_profile.fetch_models()

        assert result is not None
        assert result == []

    def test_fetch_models_hits_correct_url(self, registered_profile):
        """fetch_models calls {base_url}/models endpoint."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "m"}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            registered_profile.fetch_models(api_key="tok")

        url = mock_client.get.call_args.args[0]
        assert url.endswith("/models")
