from __future__ import annotations

from contextlib import asynccontextmanager
import re

import aioimaplib

from mcp_email_server.config import EmailServer
from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
from mcp_email_server.emails.models import MailboxInfo, MailboxStatusResponse, MovedEmail
from mcp_email_server.log import logger

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$')
STATUS_LINE_RE = re.compile(rb'^\* STATUS (?P<mailbox>"(?:[^"\\]|\\.)*"|[^ ]+) \((?P<items>[^)]*)\)$')
FLAGS_LINE_RE = re.compile(rb'^\* FLAGS \((?P<flags>[^)]*)\)$')
PERMANENT_FLAGS_LINE_RE = re.compile(rb'^\* OK \[PERMANENTFLAGS \((?P<flags>[^)]*)\)\]')


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

    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list[MailboxInfo]:
        async with self._login_logout() as imap:
            delimiter = await self.ensure_delimiter(imap)
            imap_pattern = pattern.replace("*", "%")
            if delimiter != "":
                imap_pattern = imap_pattern.replace("/", delimiter)
            if subscribed_only:
                _, lines = await imap.lsub('""', imap_pattern)
            else:
                _, lines = await imap.list('""', imap_pattern)

            mailboxes: list[MailboxInfo] = []
            for line in lines:
                if not isinstance(line, bytes):
                    continue
                match = LIST_LINE_RE.match(line)
                if not match:
                    continue

                flags_bytes = match.group("flags")
                flags = [flag.decode("utf-8") for flag in flags_bytes.split()] if flags_bytes else []
                delimiter_token = match.group("delimiter")
                line_delimiter = "" if delimiter_token == b"NIL" else delimiter_token[1:-1].decode("utf-8")
                name_token = match.group("name").strip()
                if name_token.startswith(b'"') and name_token.endswith(b'"'):
                    server_name = name_token[1:-1].decode("utf-8")
                else:
                    server_name = name_token.decode("utf-8")

                mailboxes.append(
                    MailboxInfo(
                        path=self.from_imap_path(server_name, line_delimiter or delimiter),
                        delimiter=line_delimiter,
                        flags=flags,
                        subscribed=subscribed_only or r"\Subscribed" in flags,
                    )
                )

            return mailboxes

    async def create_mailbox(self, mailbox: str) -> str:
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            await imap.create(_quote_mailbox(self.to_imap_path(mailbox)))
            return f"Successfully created mailbox '{mailbox}'"

    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            await imap.rename(
                _quote_mailbox(self.to_imap_path(old_mailbox)),
                _quote_mailbox(self.to_imap_path(new_mailbox)),
            )
            return f"Successfully renamed mailbox '{old_mailbox}' to '{new_mailbox}'"

    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
        if not confirm:
            raise ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            await imap.delete(_quote_mailbox(self.to_imap_path(mailbox)))
            return f"Successfully deleted mailbox '{mailbox}'"

    async def get_mailbox_status(self, mailbox: str = "INBOX") -> MailboxStatusResponse:
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            quoted_mailbox = _quote_mailbox(self.to_imap_path(mailbox))

            _, examine_lines = await imap.examine(quoted_mailbox)
            _, status_lines = await imap.status(quoted_mailbox, "(MESSAGES RECENT UIDNEXT UIDVALIDITY UNSEEN)")

            flags: list[str] = []
            permanent_flags: list[str] = []
            for line in examine_lines:
                if not isinstance(line, bytes):
                    continue
                flags_match = FLAGS_LINE_RE.search(line)
                if flags_match:
                    flags = [flag.decode("utf-8") for flag in flags_match.group("flags").split()]
                permanent_flags_match = PERMANENT_FLAGS_LINE_RE.search(line)
                if permanent_flags_match:
                    permanent_flags = [
                        flag.decode("utf-8")
                        for flag in permanent_flags_match.group("flags").split()
                    ]

            counts: dict[str, int] = {}
            for line in status_lines:
                if not isinstance(line, bytes):
                    continue
                status_match = STATUS_LINE_RE.search(line)
                if not status_match:
                    continue
                items = status_match.group("items").decode("utf-8").split()
                if len(items) % 2 != 0:
                    raise RuntimeError(f"Could not parse STATUS response line: {line!r}")
                counts = {
                    items[index].lower(): int(items[index + 1])
                    for index in range(0, len(items), 2)
                }
                break

            if not counts:
                raise RuntimeError(f"Could not parse STATUS response lines: {status_lines!r}")

            return MailboxStatusResponse(
                path=mailbox,
                messages=counts["messages"],
                recent=counts["recent"],
                unseen=counts.get("unseen"),
                uid_next=counts.get("uidnext"),
                uid_validity=counts.get("uidvalidity"),
                flags=flags,
                permanent_flags=permanent_flags,
            )


class EmailOps:
    def __init__(self, email_server: EmailServer, mailbox_ops: MailboxOps):
        self.email_server = email_server
        self.mailbox_ops = mailbox_ops
        self.imap_class = aioimaplib.IMAP4_SSL if email_server.use_ssl else aioimaplib.IMAP4

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

    async def move_emails(
        self,
        email_ids: list[str],
        source_mailbox: str,
        destination_mailbox: str,
    ) -> list[MovedEmail]:
        async with self._login_logout() as imap:
            await self.mailbox_ops.ensure_delimiter(imap)
            await imap.select(_quote_mailbox(self.mailbox_ops.to_imap_path(source_mailbox)))
            uid_set = ",".join(email_ids)
            destination = _quote_mailbox(self.mailbox_ops.to_imap_path(destination_mailbox))
            result, lines = await imap.uid("move", uid_set, destination)
            if result != "OK":
                raise RuntimeError(f"UID MOVE failed with IMAP result {result}: {lines!r}")
            return [
                MovedEmail(message_id=email_id, success=True, error=None, method="native")
                for email_id in email_ids
            ]
