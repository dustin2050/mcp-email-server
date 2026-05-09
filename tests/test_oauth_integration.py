import base64
import hashlib
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from mcp_email_server.app import mcp
from mcp_email_server.cli import _configure_http_transport


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@pytest.fixture
def oauth_client(monkeypatch) -> Iterator[TestClient]:
    original_host = mcp.settings.host
    original_port = mcp.settings.port
    original_transport_security = mcp.settings.transport_security
    original_auth = mcp.settings.auth
    original_json_response = mcp.settings.json_response
    original_provider = mcp._auth_server_provider
    original_token_verifier = mcp._token_verifier
    original_session_manager = mcp._session_manager

    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "claude-desktop")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "topsecret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mail.example.com")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "http://127.0.0.1:43123/callback")
    monkeypatch.setenv("MCP_ENABLE_DNS_REBINDING_PROTECTION", "false")

    try:
        _configure_http_transport("testserver", 9557)
        mcp.settings.json_response = True
        mcp._session_manager = None

        with TestClient(mcp.streamable_http_app()) as client:
            yield client
    finally:
        mcp.settings.host = original_host
        mcp.settings.port = original_port
        mcp.settings.transport_security = original_transport_security
        mcp.settings.auth = original_auth
        mcp.settings.json_response = original_json_response
        mcp._auth_server_provider = original_provider
        mcp._token_verifier = original_token_verifier
        mcp._session_manager = original_session_manager


def _issue_access_token(client: TestClient) -> str:
    verifier = "integration-verifier-for-oauth-tests-1234567890"
    challenge = _pkce_challenge(verifier)
    response = client.get(
        "/authorize",
        params={
            "client_id": "claude-desktop",
            "redirect_uri": "http://127.0.0.1:43123/callback",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "state": "oauth-state",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    redirect_query = parse_qs(urlparse(response.headers["location"]).query)
    code = redirect_query["code"][0]

    token_response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:43123/callback",
            "client_id": "claude-desktop",
            "client_secret": "topsecret",
            "code_verifier": verifier,
        },
    )

    assert token_response.status_code == 200
    payload = token_response.json()
    assert payload["token_type"] == "Bearer"
    assert payload["scope"] == "mcp"
    return payload["access_token"]


def test_healthz_returns_ok(oauth_client: TestClient):
    response = oauth_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_oauth_metadata_advertises_revocation_and_s256(oauth_client: TestClient):
    response = oauth_client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    body = response.json()
    assert body["issuer"] == "https://mail.example.com/"
    assert body["authorization_endpoint"] == "https://mail.example.com/authorize"
    assert body["token_endpoint"] == "https://mail.example.com/token"
    assert body["revocation_endpoint"] == "https://mail.example.com/revoke"
    assert body["code_challenge_methods_supported"] == ["S256"]


def test_mcp_rejects_requests_without_bearer_token(oauth_client: TestClient):
    response = oauth_client.post(
        "/mcp",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "mcp-protocol-version": "2025-03-26",
        },
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_authorize_token_and_bearer_initialize_cycle(oauth_client: TestClient):
    access_token = _issue_access_token(oauth_client)

    response = oauth_client.post(
        "/mcp",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {access_token}",
            "mcp-protocol-version": "2025-03-26",
        },
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "email"
    assert response.headers["mcp-session-id"]


def test_revoke_invalidates_previously_usable_token(oauth_client: TestClient):
    access_token = _issue_access_token(oauth_client)

    first_response = oauth_client.post(
        "/mcp",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {access_token}",
            "mcp-protocol-version": "2025-03-26",
        },
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )

    assert first_response.status_code == 200

    revoke_response = oauth_client.post(
        "/revoke",
        data={
            "token": access_token,
            "token_type_hint": "access_token",
            "client_id": "claude-desktop",
            "client_secret": "topsecret",
        },
    )

    assert revoke_response.status_code == 200

    second_response = oauth_client.post(
        "/mcp",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {access_token}",
            "mcp-protocol-version": "2025-03-26",
        },
        json={
            "jsonrpc": "2.0",
            "id": "2",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )

    assert second_response.status_code == 401
