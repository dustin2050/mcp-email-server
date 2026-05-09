from __future__ import annotations

from contextlib import asynccontextmanager
import re

import aioimaplib

from mcp_email_server.config import EmailServer
from mcp_email_server.emails._helpers import _create_ssl_context, _send_imap_id
from mcp_email_server.log import logger

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$')


class MailboxOps:
    def __init__(self, email_server: EmailServer):
        self.email_server = email_server
        self.imap_class = aioimaplib.IMAP4_SSL if email_server.use_ssl else aioimaplib.IMAP4
        self._delimiter: str | None = None

    def _imap_connect(self) -> aioimaplib.IMAP4_SSL | aioimaplib.IMAP4:
        if self.email_server.use_ssl:
            return self.imap_class(
                self.email_server.host,
                self.email_server.port,
                ssl_context=_create_ssl_context(self.email_server.verify_ssl),
            )
        return self.imap_class(self.email_server.host, self.email_server.port)

    @asynccontextmanager
    async def _login_logout(self):
        imap = self._imap_connect()
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(self.email_server.user_name, self.email_server.password.get_secret_value())
            await _send_imap_id(imap)
            yield imap
        finally:
            try:
                await imap.logout()
            except Exception as e:
                logger.info(f"Error during logout: {e}")

    def _delimiter_or_raise(self) -> str:
        if self._delimiter is None:
            raise RuntimeError("Mailbox delimiter has not been detected yet")
        return self._delimiter

    async def ensure_delimiter(self, imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4 | None = None) -> str:
        if self._delimiter is not None:
            return self._delimiter

        if imap is None:
            async with self._login_logout() as owned_imap:
                return await self.ensure_delimiter(owned_imap)

        _, lines = await imap.list('""', "*")
        for line in lines:
            if not isinstance(line, bytes):
                continue
            match = LIST_LINE_RE.match(line)
            if not match:
                continue
            delimiter_token = match.group("delimiter")
            self._delimiter = "" if delimiter_token == b"NIL" else delimiter_token[1:-1].decode("utf-8")
            return self._delimiter

        raise RuntimeError("Could not detect mailbox delimiter from IMAP LIST response")

    def to_imap_path(self, user_path: str) -> str:
        delimiter = self._delimiter_or_raise()
        if delimiter == "":
            return user_path
        parts = user_path.split("/")
        if any(part == "" for part in parts):
            raise ValueError(f"Invalid mailbox path: {user_path!r}")
        return delimiter.join(parts)

    def from_imap_path(self, server_path: str, delimiter: str) -> str:
        if delimiter == "":
            return server_path
        return server_path.replace(delimiter, "/")
