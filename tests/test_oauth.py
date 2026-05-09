from urllib.parse import parse_qs, urlparse

import pytest

from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull

from mcp_email_server.oauth import (
    DEFAULT_LOOPBACK_REDIRECT_URIS,
    MCPOAuthProvider,
    build_oauth_runtime_config_from_env,
)


def test_build_oauth_runtime_config_returns_none_when_oauth_not_configured(monkeypatch):
    monkeypatch.delenv("MCP_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MCP_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_OAUTH_REDIRECT_URIS", raising=False)

    assert build_oauth_runtime_config_from_env() is None


def test_provider_uses_static_client_metadata_from_env(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv(
        "MCP_OAUTH_REDIRECT_URIS",
        "http://127.0.0.1:43123/callback,http://localhost:43123/callback",
    )

    config = build_oauth_runtime_config_from_env()

    assert config is not None
    provider = MCPOAuthProvider(config)

    assert provider.client == OAuthClientInformationFull(
        client_id="claude-desktop",
        client_secret="topsecret",
        redirect_uris=[
            "http://127.0.0.1:43123/callback",
            "http://localhost:43123/callback",
        ],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="mcp",
        token_endpoint_auth_method="client_secret_post",
    )


def test_build_oauth_runtime_config_prefers_explicit_redirect_uris(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv(
        "MCP_OAUTH_REDIRECT_URIS",
        "http://127.0.0.1:43123/callback,http://localhost:43123/callback",
    )

    config = build_oauth_runtime_config_from_env()

    assert config is not None
    assert [str(uri) for uri in config.redirect_uris] == [
        "http://127.0.0.1:43123/callback",
        "http://localhost:43123/callback",
    ]


def test_build_oauth_runtime_config_uses_loopback_redirect_defaults(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.delenv("MCP_OAUTH_REDIRECT_URIS", raising=False)

    config = build_oauth_runtime_config_from_env()

    assert config is not None
    assert [str(uri) for uri in config.redirect_uris] == DEFAULT_LOOPBACK_REDIRECT_URIS


@pytest.mark.asyncio
async def test_authorize_returns_redirect_with_code_and_state(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")

    provider = MCPOAuthProvider(build_oauth_runtime_config_from_env())
    client = await provider.get_client("claude-desktop")

    assert client is not None

    redirect_url = await provider.authorize(
        client,
        AuthorizationParams(
            state="opaque-state",
            scopes=["mcp"],
            code_challenge="x" * 43,
            redirect_uri="http://127.0.0.1:43123/callback",
            redirect_uri_provided_explicitly=True,
        ),
    )

    parsed = urlparse(redirect_url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://127.0.0.1:43123/callback"
    assert query["state"] == ["opaque-state"]
    assert len(query["code"][0]) >= 32

    loaded = await provider.load_authorization_code(client, query["code"][0])
    assert loaded is not None
    assert loaded.client_id == "claude-desktop"
    assert loaded.scopes == ["mcp"]


@pytest.mark.asyncio
async def test_authorize_rejects_invalid_scope(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")

    provider = MCPOAuthProvider(build_oauth_runtime_config_from_env())
    client = await provider.get_client("claude-desktop")

    assert client is not None

    with pytest.raises(AuthorizeError) as exc_info:
        await provider.authorize(
            client,
            AuthorizationParams(
                state=None,
                scopes=["email"],
                code_challenge="x" * 43,
                redirect_uri="http://127.0.0.1:43123/callback",
                redirect_uri_provided_explicitly=True,
            ),
        )
    assert exc_info.value.error == "invalid_scope"
    assert exc_info.value.error_description is not None
    assert "scope" in exc_info.value.error_description


@pytest.mark.asyncio
async def test_authorize_rejects_malformed_pkce_challenge(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")

    provider = MCPOAuthProvider(build_oauth_runtime_config_from_env())
    client = await provider.get_client("claude-desktop")

    assert client is not None

    with pytest.raises(AuthorizeError) as exc_info:
        await provider.authorize(
            client,
            AuthorizationParams(
                state=None,
                scopes=["mcp"],
                code_challenge="not-valid+/=",
                redirect_uri="http://127.0.0.1:43123/callback",
                redirect_uri_provided_explicitly=True,
            ),
        )
    assert exc_info.value.error == "invalid_request"
    assert exc_info.value.error_description is not None
    assert "PKCE" in exc_info.value.error_description


@pytest.mark.asyncio
async def test_authorize_rejects_redirect_uri_outside_whitelist(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")

    provider = MCPOAuthProvider(build_oauth_runtime_config_from_env())
    client = await provider.get_client("claude-desktop")

    assert client is not None

    with pytest.raises(AuthorizeError) as exc_info:
        await provider.authorize(
            client,
            AuthorizationParams(
                state=None,
                scopes=["mcp"],
                code_challenge="x" * 43,
                redirect_uri="http://localhost:9999/callback",
                redirect_uri_provided_explicitly=True,
            ),
        )
    assert exc_info.value.error == "invalid_request"
    assert exc_info.value.error_description is not None
    assert "redirect" in exc_info.value.error_description
