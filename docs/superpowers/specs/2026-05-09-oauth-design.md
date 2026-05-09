# OAuth 2.1 for `mcp-email-server` — Design Spec

**Date:** 2026-05-09  
**Repo:** `mcp-email-server`  
**Fork:** `dustin2050/mcp-email-server`  
**Branch:** `feature/oauth`

## 1. Scope

Add OAuth 2.1 authentication for the HTTP transports of `mcp-email-server` when deployed on Railway:

- `streamable-http` and `sse` must support MCP OAuth authorization server endpoints.
- `stdio` must remain usable without OAuth and without behavior changes.
- The server must expose OAuth metadata, authorization, token, revocation, and protected-resource metadata endpoints through FastMCP’s built-in auth routes.
- The server must expose `/healthz` for Railway health checks.
- The implementation must use one static confidential client configured from environment variables.
- The implementation must include tests for provider behavior, HTTP auth flow, CLI wiring, and docs updates.

## 2. Verified Library Interface

The installed `mcp` package is the source of truth for the implementation.

### 2.1 Provider protocol

`OAuthAuthorizationServerProvider` requires these async methods:

- `get_client(client_id)`
- `register_client(client_info)`
- `authorize(client, params)`
- `load_authorization_code(client, authorization_code)`
- `exchange_authorization_code(client, authorization_code)`
- `load_refresh_token(client, refresh_token)`
- `exchange_refresh_token(client, refresh_token, scopes)`
- `load_access_token(token)`
- `revoke_token(token)`

### 2.2 Concrete auth models

The installed library provides concrete Pydantic models:

- `AuthorizationParams`
- `AuthorizationCode`
- `RefreshToken`
- `AccessToken`

Relevant fields:

- `AuthorizationParams.code_challenge` exists, but `code_challenge_method` does not. The handler already validates `code_challenge_method == "S256"` before the provider is called.
- `AuthorizationCode` stores `redirect_uri`, `redirect_uri_provided_explicitly`, and optional `resource`.
- `AccessToken` includes optional `resource`.

### 2.3 FastMCP auth wiring

`FastMCP.__init__` accepts:

- `auth_server_provider=...`
- `auth=AuthSettings(...)`

Important implementation detail:

- If `auth` is provided, FastMCP requires either `auth_server_provider` or `token_verifier`.
- `AuthSettings` in the installed library requires both `issuer_url` and `resource_server_url`.

### 2.4 Difference from the original task text

The task text mentions wiring only `AuthSettings(issuer_url=MCP_PUBLIC_URL)`. The installed library requires `resource_server_url` too. Implementation will therefore set:

- `issuer_url = MCP_PUBLIC_URL`
- `resource_server_url = MCP_PUBLIC_URL + "/mcp"`

This is an intentional adaptation to the real interface.

## 3. Design Decisions

### 3.1 Auth model

- Single static confidential OAuth client.
- Client ID from `MCP_OAUTH_CLIENT_ID`.
- Client secret from `MCP_OAUTH_CLIENT_SECRET`.
- Redirect URIs from `MCP_OAUTH_REDIRECT_URIS`.
- Supported scope set: `["mcp"]`.
- Required scopes for protected MCP endpoints: `["mcp"]`.

Rationale:

- Matches the deployment target and task constraints.
- Avoids dynamic registration and external persistence complexity.
- Keeps Claude Desktop setup predictable.

### 3.2 Storage model

- In-memory dictionaries for authorization codes, access tokens, and refresh tokens.
- Opaque token strings from `secrets.token_urlsafe(32)`.
- Expiry:
  - authorization code: 10 minutes
  - access token: 60 minutes
  - refresh token: 30 days

Rationale:

- Sufficient for Railway single-instance deployments and local testing.
- Keeps implementation self-contained.

Tradeoff:

- Tokens do not survive process restarts or horizontal scaling.
- This is acceptable for the current scope and must be documented as a caveat.

### 3.3 PKCE

- PKCE is mandatory.
- Only S256 is accepted.
- The provider validates challenge shape defensively.
- The `mcp` token handler verifies the `code_verifier` against the stored challenge.

Rationale:

- Required by the task.
- Aligns with OAuth 2.1 expectations for authorization code flow.

### 3.4 Authorization behavior

- Auto-approve authorization requests.
- If the client is valid, the redirect URI is valid, the requested scopes are valid, and the PKCE challenge is well-formed, the provider immediately issues an authorization code and redirects back to the caller.

Rationale:

- There is no end-user login UI in this server.
- This server is acting as a protected deployment of MCP tools, not as a multi-user identity product.

### 3.5 Revocation

- Enable FastMCP revocation routes with `RevocationOptions(enabled=True)`.
- Provider revocation revokes both the presented token and any sibling token pair it can identify.

Rationale:

- Standard OAuth hygiene.
- Explicitly requested in the task.

### 3.6 HTTP-only auth

- `stdio` remains unchanged.
- HTTP transports call a shared configuration helper.
- If `MCP_OAUTH_CLIENT_ID` is missing, HTTP transports run without OAuth and emit:
  - `Running HTTP transport without OAuth authentication`

Rationale:

- Preserves local CLI compatibility.
- Makes unsecured HTTP execution explicit.

### 3.7 Health endpoint

- Add `/healthz` through `FastMCP.custom_route`.
- Return a small JSON payload with `status: "ok"`.

Rationale:

- Fits Railway health-check expectations.
- Avoids introducing another ASGI wrapper around FastMCP.

## 4. Provider Interface Mapping

Provider class: `MCPOAuthProvider`

Planned responsibilities:

- `get_client(client_id)`
  - Return the static client when `client_id` matches.
  - Return `None` otherwise.

- `register_client(client_info)`
  - Raise `NotImplementedError`.
  - Dynamic registration is disabled.

- `authorize(client, params)`
  - Validate redirect URI against configured allowlist.
  - Validate requested scopes.
  - Validate PKCE challenge syntax.
  - Create `AuthorizationCode`.
  - Return redirect URL with `code` and optional `state`.

- `load_authorization_code(client, authorization_code)`
  - Return stored code if present.

- `exchange_authorization_code(client, authorization_code)`
  - Delete the one-time auth code.
  - Mint access and refresh tokens.
  - Return `OAuthToken`.

- `load_refresh_token(client, refresh_token)`
  - Return stored refresh token if present.

- `exchange_refresh_token(client, refresh_token, scopes)`
  - Rotate both access and refresh token.
  - Preserve or narrow scopes only.
  - Return `OAuthToken`.

- `load_access_token(token)`
  - Return stored access token if present and not expired.

- `revoke_token(token)`
  - Remove the provided token.
  - Remove related token pair where possible.

## 5. Environment Variables

| Variable | Required | Example | Purpose |
| --- | --- | --- | --- |
| `MCP_OAUTH_CLIENT_ID` | For OAuth-enabled HTTP | `claude-desktop` | Static OAuth client id |
| `MCP_OAUTH_CLIENT_SECRET` | For OAuth-enabled HTTP | `super-secret-value` | Static OAuth client secret |
| `MCP_OAUTH_REDIRECT_URIS` | No | `http://127.0.0.1:55432/callback,http://localhost:55432/callback` | Comma-separated redirect allowlist |
| `MCP_PUBLIC_URL` | For OAuth-enabled HTTP | `https://my-server.up.railway.app` | Public issuer base URL |
| `MCP_HOST` | No | `0.0.0.0` | Bind host for Railway/Docker |
| `MCP_PORT` | No | `8080` | Bind port for Railway |

### 5.1 Redirect URI defaults

If `MCP_OAUTH_REDIRECT_URIS` is unset, use loopback defaults:

- `http://127.0.0.1/*`
- `http://localhost/*`
- `http://[::1]/*`

Implementation detail:

- Matching is based on scheme + host being loopback and any path/port.
- If explicit values are provided, only those explicit URIs are allowed.

## 6. Railway Deployment

Railway configuration:

- Start command: `mcp-email-server streamable-http`
- `MCP_HOST=0.0.0.0`
- `MCP_PORT` provided by Railway or set explicitly
- `MCP_PUBLIC_URL=https://<service>.up.railway.app`
- Set OAuth env vars in the Railway dashboard
- Keep existing email-related environment variables configured

Published URLs:

- MCP endpoint: `https://<service>.up.railway.app/mcp`
- OAuth metadata: `https://<service>.up.railway.app/.well-known/oauth-authorization-server`
- Protected resource metadata: `https://<service>.up.railway.app/.well-known/oauth-protected-resource/mcp`
- Health check: `https://<service>.up.railway.app/healthz`

## 7. Security Considerations

- Only confidential clients with the configured shared secret are accepted.
- PKCE is mandatory and restricted to S256.
- Authorization codes are one-time use and short-lived.
- Refresh token rotation is enforced.
- Revocation is supported.
- HTTP without OAuth remains possible only when the operator omits OAuth env vars; this is deliberate for backwards compatibility and is logged loudly.
- In-memory token storage means restart invalidates all sessions.
- The server is not a multi-user identity provider; auto-approval is acceptable only because the deployment trusts the configured client and redirect URIs.

## 8. Out of Scope

- Dynamic client registration
- User login UI or consent screens
- Durable token persistence
- Multi-tenant or per-user identity
- External OAuth federation
- Token introspection
- DPoP, private_key_jwt, or public clients

## 9. Test Strategy

- Unit tests for provider env parsing, redirect validation, authorization code issuance, token exchange, refresh rotation, expiry handling, and revocation.
- Integration tests using the Starlette app returned by `FastMCP.streamable_http_app()` to cover:
  - `/.well-known/oauth-authorization-server`
  - `/authorize`
  - `/token`
  - authenticated `POST /mcp`
  - `/healthz`
- CLI/configuration tests to prove:
  - stdio path does not wire HTTP auth
  - HTTP path wires auth when env is present
  - HTTP path warns when env is absent
