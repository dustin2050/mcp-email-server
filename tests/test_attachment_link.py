"""Tests for signed attachment download links (get_attachment_link + /download)."""

import time

import pytest

from mcp_email_server.app import (
    _sign_attachment_token,
    _verify_attachment_token,
    get_attachment_link,
)


def _payload(**overrides):
    base = {
        "account_name": "gmx",
        "email_id": "42",
        "attachment_name": "Invoice-0025.pdf",
        "mailbox": "INBOX",
        "exp": int(time.time()) + 600,
    }
    base.update(overrides)
    return base


class TestAttachmentTokenSigning:
    def test_sign_verify_roundtrip(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "test-secret")
        payload = _payload()
        token = _sign_attachment_token(payload)
        assert _verify_attachment_token(token) == payload

    def test_tampered_body_rejected(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "test-secret")
        token = _sign_attachment_token(_payload())
        body, sig = token.split(".")
        # Flip a character in the signed body; signature must no longer match.
        tampered_char = "A" if body[-1] != "A" else "B"
        tampered = f"{body[:-1]}{tampered_char}.{sig}"
        assert _verify_attachment_token(tampered) is None

    def test_wrong_secret_rejected(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "secret-a")
        token = _sign_attachment_token(_payload())
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "secret-b")
        assert _verify_attachment_token(token) is None

    def test_expired_rejected(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "test-secret")
        token = _sign_attachment_token(_payload(exp=int(time.time()) - 1))
        assert _verify_attachment_token(token) is None

    def test_malformed_token_rejected(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "test-secret")
        assert _verify_attachment_token("") is None
        assert _verify_attachment_token("no-dot") is None
        assert _verify_attachment_token("a.b.c") is None


class TestGetAttachmentLink:
    async def test_builds_signed_url(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "test-secret")
        monkeypatch.setenv("MCP_PUBLIC_URL", "https://email-mcp.example.de")
        url = await get_attachment_link("gmx", "42", "Invoice-0025.pdf", "INBOX")
        assert url.startswith("https://email-mcp.example.de/download?token=")
        token = url.split("token=", 1)[1]
        payload = _verify_attachment_token(token)
        assert payload is not None
        assert payload["account_name"] == "gmx"
        assert payload["email_id"] == "42"
        assert payload["attachment_name"] == "Invoice-0025.pdf"
        assert payload["mailbox"] == "INBOX"
        assert payload["exp"] > time.time()

    async def test_prepends_https_scheme(self, monkeypatch):
        monkeypatch.setenv("MCP_ATTACHMENT_URL_SECRET", "test-secret")
        monkeypatch.setenv("MCP_PUBLIC_URL", "email-mcp.example.de")
        url = await get_attachment_link("gmx", "42", "Invoice-0025.pdf")
        assert url.startswith("https://email-mcp.example.de/download?token=")

    async def test_requires_public_url(self, monkeypatch):
        monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
        with pytest.raises(ValueError, match="MCP_PUBLIC_URL is not configured"):
            await get_attachment_link("gmx", "42", "Invoice-0025.pdf")
