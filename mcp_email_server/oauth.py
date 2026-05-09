from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from pydantic import AnyHttpUrl, AnyUrl, Field, TypeAdapter

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    ProviderTokenVerifier,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

OAUTH_SCOPE = "mcp"
DEFAULT_LOOPBACK_REDIRECT_URIS = [
    "http://127.0.0.1/callback",
    "http://localhost/callback",
    "http://[::1]/callback",
]

ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
AUTHORIZATION_CODE_TTL_SECONDS = 10 * 60
PKCE_CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


@dataclass(frozen=True)
class OAuthRuntimeConfig:
    client_id: str
    client_secret: str
    public_url: AnyHttpUrl
    redirect_uris: list[AnyUrl]
    allow_loopback_redirects: bool


class StaticOAuthClientInformation(OAuthClientInformationFull):
    allow_loopback_redirects: bool = Field(default=False, exclude=True)

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and self.allow_loopback_redirects and _is_loopback_redirect_uri(redirect_uri):
            return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_loopback_redirect_uri(redirect_uri: AnyUrl) -> bool:
    return redirect_uri.scheme == "http" and redirect_uri.host in {"127.0.0.1", "localhost", "::1"}


def _parse_redirect_uris(value: str | None) -> tuple[list[AnyUrl], bool]:
    if value:
        return TypeAdapter(list[AnyUrl]).validate_python(_split_csv(value)), False
    return TypeAdapter(list[AnyUrl]).validate_python(DEFAULT_LOOPBACK_REDIRECT_URIS), True


def _normalize_url(url: AnyUrl) -> str:
    return str(url)


def build_oauth_runtime_config_from_env(env: dict[str, str] | None = None) -> OAuthRuntimeConfig | None:
    environ = env or os.environ
    client_id = environ.get("MCP_OAUTH_CLIENT_ID")
    client_secret = environ.get("MCP_OAUTH_CLIENT_SECRET")
    public_url = environ.get("MCP_PUBLIC_URL")

    if not client_id and not client_secret and not public_url:
        return None

    missing = [
        env_name
        for env_name, value in (
            ("MCP_OAUTH_CLIENT_ID", client_id),
            ("MCP_OAUTH_CLIENT_SECRET", client_secret),
            ("MCP_PUBLIC_URL", public_url),
        )
        if not value
    ]
    if missing:
        msg = f"Missing required OAuth environment variables: {', '.join(missing)}"
        raise ValueError(msg)

    redirect_uris, allow_loopback_redirects = _parse_redirect_uris(environ.get("MCP_OAUTH_REDIRECT_URIS"))
    parsed_public_url = TypeAdapter(AnyHttpUrl).validate_python(public_url)

    return OAuthRuntimeConfig(
        client_id=client_id,
        client_secret=client_secret,
        public_url=parsed_public_url,
        redirect_uris=redirect_uris,
        allow_loopback_redirects=allow_loopback_redirects,
    )


def build_auth_settings(config: OAuthRuntimeConfig, streamable_http_path: str = "/mcp") -> AuthSettings:
    public_url = str(config.public_url).rstrip("/")
    return AuthSettings(
        issuer_url=TypeAdapter(AnyHttpUrl).validate_python(public_url),
        resource_server_url=TypeAdapter(AnyHttpUrl).validate_python(f"{public_url}{streamable_http_path}"),
        client_registration_options=ClientRegistrationOptions(
            enabled=False,
            valid_scopes=[OAUTH_SCOPE],
            default_scopes=[OAUTH_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[OAUTH_SCOPE],
    )


def configure_fastmcp_oauth(server: Any, env: dict[str, str] | None = None) -> bool:
    config = build_oauth_runtime_config_from_env(env)
    if config is None:
        server.settings.auth = None
        server._auth_server_provider = None
        server._token_verifier = None
        return False

    provider = MCPOAuthProvider(config)
    server.settings.auth = build_auth_settings(config, server.settings.streamable_http_path)
    server._auth_server_provider = provider
    server._token_verifier = ProviderTokenVerifier(provider)
    return True


class MCPOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, config: OAuthRuntimeConfig):
        self.config = config
        self.access_token_model = AccessToken
        self.refresh_token_model = RefreshToken
        self.authorization_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.access_to_refresh_tokens: dict[str, str] = {}
        self.refresh_to_access_tokens: dict[str, str] = {}
        self.refresh_token_resources: dict[str, str | None] = {}
        self.client = OAuthClientInformationFull(
            client_id=config.client_id,
            client_secret=config.client_secret,
            redirect_uris=config.redirect_uris,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=OAUTH_SCOPE,
            token_endpoint_auth_method="client_secret_post",
        )
        self._auth_client = StaticOAuthClientInformation(
            **self.client.model_dump(),
            allow_loopback_redirects=config.allow_loopback_redirects,
        )

    def _is_redirect_uri_allowed(self, redirect_uri: AnyUrl) -> bool:
        if self.config.allow_loopback_redirects:
            return _is_loopback_redirect_uri(redirect_uri)
        return _normalize_url(redirect_uri) in {_normalize_url(item) for item in self.config.redirect_uris}

    def _build_oauth_token(
        self,
        client_id: str,
        scopes: list[str],
        resource: str | None = None,
    ) -> OAuthToken:
        now = int(time.time())
        access_token_value = secrets.token_urlsafe(32)
        refresh_token_value = secrets.token_urlsafe(32)
        access_token = self.access_token_model(
            token=access_token_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource,
        )
        refresh_token = self.refresh_token_model(
            token=refresh_token_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
        )

        self.access_tokens[access_token_value] = access_token
        self.refresh_tokens[refresh_token_value] = refresh_token
        self.access_to_refresh_tokens[access_token_value] = refresh_token_value
        self.refresh_to_access_tokens[refresh_token_value] = access_token_value
        self.refresh_token_resources[refresh_token_value] = resource

        return OAuthToken(
            access_token=access_token_value,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh_token_value,
        )

    def _revoke_token_pair(
        self,
        access_token_value: str | None = None,
        refresh_token_value: str | None = None,
    ) -> None:
        if access_token_value is None and refresh_token_value is not None:
            access_token_value = self.refresh_to_access_tokens.get(refresh_token_value)
        if refresh_token_value is None and access_token_value is not None:
            refresh_token_value = self.access_to_refresh_tokens.get(access_token_value)

        if access_token_value is not None:
            self.access_tokens.pop(access_token_value, None)
            self.access_to_refresh_tokens.pop(access_token_value, None)
        if refresh_token_value is not None:
            self.refresh_tokens.pop(refresh_token_value, None)
            self.refresh_to_access_tokens.pop(refresh_token_value, None)
            self.refresh_token_resources.pop(refresh_token_value, None)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id == self.client.client_id:
            return self._auth_client
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not self._is_redirect_uri_allowed(params.redirect_uri):
            raise AuthorizeError(
                error="invalid_request",
                error_description="redirect URI is not allowed for this OAuth client",
            )

        scopes = params.scopes or [OAUTH_SCOPE]
        invalid_scopes = [scope for scope in scopes if scope != OAUTH_SCOPE]
        if invalid_scopes:
            raise AuthorizeError(
                error="invalid_scope",
                error_description=f"requested scope is not allowed: {', '.join(invalid_scopes)}",
            )

        if not PKCE_CODE_CHALLENGE_RE.fullmatch(params.code_challenge):
            raise AuthorizeError(
                error="invalid_request",
                error_description="PKCE code_challenge must be 43-128 unpadded base64url characters",
            )

        code = secrets.token_urlsafe(32)
        self.authorization_codes[code] = AuthorizationCode(
            code=code,
            scopes=scopes,
            expires_at=time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self.authorization_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        self.authorization_codes.pop(authorization_code.code, None)
        return self._build_oauth_token(
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        token = self.refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        if token.expires_at is not None and token.expires_at < time.time():
            self._revoke_token_pair(refresh_token_value=refresh_token)
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        resource = self.refresh_token_resources.get(refresh_token.token)
        self._revoke_token_pair(refresh_token_value=refresh_token.token)
        return self._build_oauth_token(
            client_id=client.client_id or "",
            scopes=scopes,
            resource=resource,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self.access_tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at is not None and access_token.expires_at < time.time():
            self._revoke_token_pair(access_token_value=token)
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._revoke_token_pair(access_token_value=token.token)
            return
        self._revoke_token_pair(refresh_token_value=token.token)
