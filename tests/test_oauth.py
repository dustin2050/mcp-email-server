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


@pytest.mark.asyncio
async def test_exchange_authorization_code_mints_access_and_refresh_tokens(monkeypatch):
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
            state=None,
            scopes=["mcp"],
            code_challenge="x" * 43,
            redirect_uri="http://127.0.0.1:43123/callback",
            redirect_uri_provided_explicitly=True,
        ),
    )
    auth_code_value = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, auth_code_value)

    assert auth_code is not None

    token = await provider.exchange_authorization_code(client, auth_code)

    assert token.token_type == "Bearer"  # noqa: S105
    assert token.scope == "mcp"
    assert token.refresh_token is not None
    assert token.expires_in == 3600
    assert await provider.load_authorization_code(client, auth_code_value) is None
    assert await provider.load_access_token(token.access_token) is not None
    assert await provider.load_refresh_token(client, token.refresh_token) is not None


@pytest.mark.asyncio
async def test_load_access_and_refresh_token_honor_expiry(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")

    provider = MCPOAuthProvider(build_oauth_runtime_config_from_env())
    client = await provider.get_client("claude-desktop")

    assert client is not None

    provider.access_tokens["expired-access"] = provider.access_token_model(
        token="expired-access",
        client_id="claude-desktop",
        scopes=["mcp"],
        expires_at=1,
    )
    provider.refresh_tokens["expired-refresh"] = provider.refresh_token_model(
        token="expired-refresh",
        client_id="claude-desktop",
        scopes=["mcp"],
        expires_at=1,
    )

    assert await provider.load_access_token("expired-access") is None
    assert await provider.load_refresh_token(client, "expired-refresh") is None
    assert "expired-access" not in provider.access_tokens
    assert "expired-refresh" not in provider.refresh_tokens


@pytest.mark.asyncio
async def test_exchange_refresh_token_rotates_both_tokens(monkeypatch):
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
            state=None,
            scopes=["mcp"],
            code_challenge="x" * 43,
            redirect_uri="http://127.0.0.1:43123/callback",
            redirect_uri_provided_explicitly=True,
        ),
    )
    auth_code_value = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, auth_code_value)

    assert auth_code is not None

    first_token = await provider.exchange_authorization_code(client, auth_code)
    first_refresh = await provider.load_refresh_token(client, first_token.refresh_token)

    assert first_refresh is not None

    second_token = await provider.exchange_refresh_token(client, first_refresh, ["mcp"])

    assert second_token.access_token != first_token.access_token
    assert second_token.refresh_token != first_token.refresh_token
    assert await provider.load_access_token(first_token.access_token) is None
    assert await provider.load_refresh_token(client, first_token.refresh_token) is None
    assert await provider.load_access_token(second_token.access_token) is not None
    assert await provider.load_refresh_token(client, second_token.refresh_token) is not None


@pytest.mark.asyncio
async def test_revoke_token_removes_access_and_refresh_pair(monkeypatch):
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
            state=None,
            scopes=["mcp"],
            code_challenge="x" * 43,
            redirect_uri="http://127.0.0.1:43123/callback",
            redirect_uri_provided_explicitly=True,
        ),
    )
    auth_code_value = parse_qs(urlparse(redirect_url).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, auth_code_value)

    assert auth_code is not None

    token = await provider.exchange_authorization_code(client, auth_code)
    access = await provider.load_access_token(token.access_token)
    refresh = await provider.load_refresh_token(client, token.refresh_token)

    assert access is not None
    assert refresh is not None

    await provider.revoke_token(access)

    assert await provider.load_access_token(token.access_token) is None
    assert await provider.load_refresh_token(client, token.refresh_token) is None
