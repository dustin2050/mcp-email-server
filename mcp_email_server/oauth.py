from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import AnyHttpUrl, AnyUrl, Field, TypeAdapter

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
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


class MCPOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, config: OAuthRuntimeConfig):
        self.config = config
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

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id == self.client.client_id:
            return self._auth_client
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        raise NotImplementedError

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        raise NotImplementedError

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        raise NotImplementedError

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        raise NotImplementedError

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raise NotImplementedError

    async def load_access_token(self, token: str) -> AccessToken | None:
        raise NotImplementedError

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        raise NotImplementedError
