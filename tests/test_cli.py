import pytest
from unittest.mock import patch

from mcp_email_server.app import mcp
from mcp_email_server.cli import (
    WILDCARD_IPV4_BIND_HOST,
    _build_transport_security_settings,
    _configure_http_transport,
    _expand_allowed_hosts,
    _expand_allowed_origins,
    _split_csv,
)


@pytest.fixture
def restore_mcp_http_state():
    original_host = mcp.settings.host
    original_port = mcp.settings.port
    original_transport_security = mcp.settings.transport_security
    original_auth = mcp.settings.auth
    original_provider = mcp._auth_server_provider
    original_token_verifier = mcp._token_verifier

    try:
        yield
    finally:
        mcp.settings.host = original_host
        mcp.settings.port = original_port
        mcp.settings.transport_security = original_transport_security
        mcp.settings.auth = original_auth
        mcp._auth_server_provider = original_provider
        mcp._token_verifier = original_token_verifier


def test_split_csv_trims_empty_items():
    assert _split_csv(" localhost:*, mcp-email-server:* ,, ") == ["localhost:*", "mcp-email-server:*"]


def test_expand_allowed_hosts_adds_wildcard_port_for_bare_host():
    assert _expand_allowed_hosts(["mcp-email-server", "localhost:*", "[::1]"]) == [
        "mcp-email-server",
        "mcp-email-server:*",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    ]


def test_expand_allowed_origins_adds_wildcard_port_for_bare_origin():
    assert _expand_allowed_origins(["http://mcp-email-server", "http://localhost:*"]) == [
        "http://mcp-email-server",
        "http://mcp-email-server:*",
        "http://localhost:*",
    ]


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", " OFF "])
def test_transport_security_can_be_disabled_with_false_values(monkeypatch, value):
    monkeypatch.setenv("MCP_ENABLE_DNS_REBINDING_PROTECTION", value)

    settings = _build_transport_security_settings(WILDCARD_IPV4_BIND_HOST, 9557)

    assert settings.enable_dns_rebinding_protection is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "unexpected", ""])
def test_transport_security_stays_enabled_with_non_false_values(monkeypatch, value):
    monkeypatch.setenv("MCP_ENABLE_DNS_REBINDING_PROTECTION", value)

    settings = _build_transport_security_settings(WILDCARD_IPV4_BIND_HOST, 9557)

    assert settings.enable_dns_rebinding_protection is True


def test_transport_security_uses_explicit_allowed_hosts(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp-email-server,localhost:*")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "http://mcp-email-server,http://localhost:*")

    settings = _build_transport_security_settings(WILDCARD_IPV4_BIND_HOST, 9557)

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["mcp-email-server", "mcp-email-server:*", "localhost:*"]
    assert settings.allowed_origins == [
        "http://mcp-email-server",
        "http://mcp-email-server:*",
        "http://localhost:*",
    ]


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("MCP_ALLOWED_HOSTS", "*"),
        ("MCP_ALLOWED_ORIGINS", "*"),
    ],
)
def test_transport_security_wildcard_allowlist_disables_protection(monkeypatch, env_name, env_value):
    monkeypatch.setenv(env_name, env_value)

    settings = _build_transport_security_settings(WILDCARD_IPV4_BIND_HOST, 9557)

    assert settings.enable_dns_rebinding_protection is False


@pytest.mark.parametrize("host", [WILDCARD_IPV4_BIND_HOST, "::", ""])
def test_transport_security_defaults_to_loopback_for_wildcard_bind(monkeypatch, host):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

    settings = _build_transport_security_settings(host, 9557)

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    assert settings.allowed_origins == [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]


def test_transport_security_defaults_include_named_host(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

    settings = _build_transport_security_settings("mcp-email-server", 9557)

    assert settings.enable_dns_rebinding_protection is True
    assert "mcp-email-server" in settings.allowed_hosts
    assert "mcp-email-server:9557" in settings.allowed_hosts
    assert "mcp-email-server:*" in settings.allowed_hosts
    assert "http://mcp-email-server:9557" in settings.allowed_origins
    assert "http://mcp-email-server:*" in settings.allowed_origins


def test_configure_http_transport_updates_mcp_settings(monkeypatch, restore_mcp_http_state):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp-email-server:*")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "http://mcp-email-server:*")
    monkeypatch.delenv("MCP_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MCP_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)

    _configure_http_transport(WILDCARD_IPV4_BIND_HOST, 9557)

    assert mcp.settings.host == WILDCARD_IPV4_BIND_HOST
    assert mcp.settings.port == 9557
    assert mcp.settings.transport_security.allowed_hosts == ["mcp-email-server:*"]
    assert mcp.settings.transport_security.allowed_origins == ["http://mcp-email-server:*"]


def test_configure_http_transport_wires_oauth_when_env_present(monkeypatch, restore_mcp_http_state):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp-email-server:*")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "http://mcp-email-server:*")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")

    _configure_http_transport(WILDCARD_IPV4_BIND_HOST, 9557)

    assert mcp.settings.auth is not None
    assert str(mcp.settings.auth.issuer_url) == "https://mail.example.com/"
    assert str(mcp.settings.auth.resource_server_url) == "https://mail.example.com/mcp"
    assert mcp.settings.auth.required_scopes == ["mcp"]
    assert mcp._auth_server_provider is not None
    assert mcp._token_verifier is not None


def test_configure_http_transport_warns_when_oauth_env_absent(monkeypatch, restore_mcp_http_state):
    monkeypatch.delenv("MCP_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MCP_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_OAUTH_REDIRECT_URIS", raising=False)

    with patch("mcp_email_server.cli.logger.warning") as mock_warning:
        _configure_http_transport(WILDCARD_IPV4_BIND_HOST, 9557)

    mock_warning.assert_called_once_with("Running HTTP transport without OAuth authentication")
    assert mcp.settings.auth is None
