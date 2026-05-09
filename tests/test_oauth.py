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
