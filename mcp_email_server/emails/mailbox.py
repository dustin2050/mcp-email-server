from __future__ import annotations

import base64
import re
from contextlib import asynccontextmanager

import aioimaplib

from mcp_email_server.config import EmailServer
from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
from mcp_email_server.emails.models import CopiedEmail, MailboxInfo, MailboxStatusResponse, MarkedEmail, MovedEmail
from mcp_email_server.log import logger

LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$')
FLAGS_LINE_RE = re.compile(rb'^\* FLAGS \((?P<flags>[^)]*)\)$')
PERMANENT_FLAGS_LINE_RE = re.compile(rb'^\* OK \[PERMANENTFLAGS \((?P<flags>[^)]*)\)\]')
EXISTS_LINE_RE = re.compile(rb'^\* (?P<count>\d+) EXISTS$')
RECENT_LINE_RE = re.compile(rb'^\* (?P<count>\d+) RECENT$')
UNSEEN_LINE_RE = re.compile(rb'^\* OK \[UNSEEN (?P<count>\d+)\]')
UIDNEXT_LINE_RE = re.compile(rb'^\* OK \[UIDNEXT (?P<count>\d+)\]')
UIDVALIDITY_LINE_RE = re.compile(rb'^\* OK \[UIDVALIDITY (?P<count>\d+)\]')


def _decode_imap_line(line: bytes | str | object) -> str:
    if isinstance(line, bytes):
        return line.decode("utf-8", errors="replace")
    return str(line)


def _decode_imap_utf7(s: str) -> str:
    """Decode IMAP modified UTF-7 (RFC 3501 §5.1.3) to a Unicode string.

    Differs from RFC 2152 UTF-7: '&' replaces '+' as the shift character,
    '/' is replaced with ',' inside the base64 alphabet, and '&-' is the
    literal '&'. Without this, GMX returns folder names like 'Entw&APw-rfe'
    (= 'Entwürfe') verbatim and the client sees garbage.
    """
    result = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "&":
            end = s.find("-", i + 1)
            if end == -1:
                result.append(s[i:])
                break
            if end == i + 1:
                result.append("&")  # &- → &
            else:
                b64 = s[i + 1 : end].replace(",", "/")
                b64 += "=" * (-len(b64) % 4)
                try:
                    result.append(base64.b64decode(b64).decode("utf-16-be"))
                except Exception:
                    # Malformed encoded run — surface verbatim rather than crash.
                    result.append(s[i : end + 1])
            i = end + 1
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


def _encode_imap_utf7(s: str) -> str:
    """Encode a Unicode string to IMAP modified UTF-7 (RFC 3501 §5.1.3)."""
    out: list[str] = []
    buf: list[str] = []

    def _flush() -> None:
        if buf:
            text = "".join(buf)
            encoded = (
                base64.b64encode(text.encode("utf-16-be"))
                .decode("ascii")
                .rstrip("=")
                .replace("/", ",")
            )
            out.append(f"&{encoded}-")
            buf.clear()

    for c in s:
        if c == "&":
            _flush()
            out.append("&-")
        elif 0x20 <= ord(c) <= 0x7E:
            _flush()
            out.append(c)
        else:
            buf.append(c)
    _flush()
    return "".join(out)


def _raise_for_imap_response(
    response: aioimaplib.aioimaplib.Response | tuple[str, list[bytes]],
    operation: str,
    mailbox: str,
) -> list[bytes]:
    result = response.result if hasattr(response, "result") else response[0]
    lines = response.lines if hasattr(response, "lines") else response[1]
    if result != "OK":
        detail = " ".join(_decode_imap_line(line) for line in lines)
        raise RuntimeError(f"IMAP {operation} failed for {mailbox!r}: {detail}")
    return lines

class _ImapSession:
    def __init__(self, email_server: EmailServer):
        self.email_server = email_server
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


class MailboxOps(_ImapSession):
    def __init__(self, email_server: EmailServer):
        super().__init__(email_server)
        self._delimiter: str | None = None

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
            return _encode_imap_utf7(user_path)
        parts = user_path.split("/")
        if any(part == "" for part in parts):
            raise ValueError(f"Invalid mailbox path: {user_path!r}")
        return delimiter.join(_encode_imap_utf7(p) for p in parts)

    def from_imap_path(self, server_path: str, delimiter: str) -> str:
        decoded = _decode_imap_utf7(server_path)
        if delimiter == "":
            return decoded
        return decoded.replace(delimiter, "/")

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
            response = await imap.create(_quote_mailbox(self.to_imap_path(mailbox)))
            _raise_for_imap_response(response, "CREATE", mailbox)
            return f"Successfully created mailbox '{mailbox}'"

    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            response = await imap.rename(
                _quote_mailbox(self.to_imap_path(old_mailbox)),
                _quote_mailbox(self.to_imap_path(new_mailbox)),
            )
            _raise_for_imap_response(response, "RENAME", old_mailbox)
            return f"Successfully renamed mailbox '{old_mailbox}' to '{new_mailbox}'"

    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
        if not confirm:
            raise ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            response = await imap.delete(_quote_mailbox(self.to_imap_path(mailbox)))
            _raise_for_imap_response(response, "DELETE", mailbox)
            return f"Successfully deleted mailbox '{mailbox}'"

    async def get_mailbox_status(self, mailbox: str = "INBOX") -> MailboxStatusResponse:
        async with self._login_logout() as imap:
            await self.ensure_delimiter(imap)
            quoted_mailbox = _quote_mailbox(self.to_imap_path(mailbox))

            examine_lines = _raise_for_imap_response(await imap.examine(quoted_mailbox), "EXAMINE", mailbox)

            messages = 0
            recent = 0
            unseen: int | None = None
            uid_next: int | None = None
            uid_validity: int | None = None
            flags: list[str] = []
            permanent_flags: list[str] = []
            for line in examine_lines:
                if not isinstance(line, bytes):
                    continue
                exists_match = EXISTS_LINE_RE.search(line)
                if exists_match:
                    messages = int(exists_match.group("count"))
                    continue
                recent_match = RECENT_LINE_RE.search(line)
                if recent_match:
                    recent = int(recent_match.group("count"))
                    continue
                unseen_match = UNSEEN_LINE_RE.search(line)
                if unseen_match:
                    unseen = int(unseen_match.group("count"))
                    continue
                uid_next_match = UIDNEXT_LINE_RE.search(line)
                if uid_next_match:
                    uid_next = int(uid_next_match.group("count"))
                    continue
                uid_validity_match = UIDVALIDITY_LINE_RE.search(line)
                if uid_validity_match:
                    uid_validity = int(uid_validity_match.group("count"))
                    continue
                flags_match = FLAGS_LINE_RE.search(line)
                if flags_match:
                    flags = [flag.decode("utf-8") for flag in flags_match.group("flags").split()]
                    continue
                permanent_flags_match = PERMANENT_FLAGS_LINE_RE.search(line)
                if permanent_flags_match:
                    permanent_flags = [
                        flag.decode("utf-8")
                        for flag in permanent_flags_match.group("flags").split()
                    ]

            return MailboxStatusResponse(
                path=mailbox,
                messages=messages,
                recent=recent,
                unseen=unseen,
                uid_next=uid_next,
                uid_validity=uid_validity,
                flags=flags,
                permanent_flags=permanent_flags,
            )


class EmailOps(_ImapSession):
    def __init__(self, email_server: EmailServer, mailbox_ops: MailboxOps):
        super().__init__(email_server)
        self.mailbox_ops = mailbox_ops

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
            if result == "OK":
                return [
                    MovedEmail(message_id=email_id, success=True, error=None, method="native")
                    for email_id in email_ids
                ]
            if result not in {"BAD", "NO"}:
                raise RuntimeError(f"UID MOVE failed with IMAP result {result}: {lines!r}")

            results: list[MovedEmail] = []
            flagged_for_expunge: list[str] = []
            for email_id in email_ids:
                try:
                    copy_result, copy_lines = await imap.uid("copy", email_id, destination)
                    if copy_result != "OK":
                        results.append(
                            MovedEmail(
                                message_id=email_id,
                                success=False,
                                error=f"UID COPY failed with IMAP result {copy_result}: {copy_lines!r}",
                                method="fallback",
                            )
                        )
                        continue

                    store_result, store_lines = await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                    if store_result != "OK":
                        results.append(
                            MovedEmail(
                                message_id=email_id,
                                success=False,
                                error=f"UID STORE +FLAGS (\\Deleted) failed with IMAP result {store_result}: {store_lines!r}",
                                method="fallback",
                            )
                        )
                        continue

                    flagged_for_expunge.append(email_id)
                    results.append(MovedEmail(message_id=email_id, success=True, error=None, method="fallback"))
                except Exception as e:
                    results.append(
                        MovedEmail(
                            message_id=email_id,
                            success=False,
                            error=str(e),
                            method="fallback",
                        )
                    )

            if flagged_for_expunge:
                # UID EXPUNGE only purges the IDs we flagged. A bare EXPUNGE could
                # purge other \Deleted-flagged messages from concurrent sessions.
                try:
                    expunge_result, _expunge_lines = await imap.uid(
                        "expunge", ",".join(flagged_for_expunge)
                    )
                except Exception as e:
                    expunge_result = "BAD"
                    _expunge_lines = [str(e).encode()]
                if expunge_result != "OK":
                    expunge_error = (
                        "COPY succeeded and source was flagged \\Deleted, but UID EXPUNGE failed; "
                        "source messages remain in the mailbox flagged \\Deleted. "
                        f"Detail: {_expunge_lines!r}"
                    )
                    adjusted_results: list[MovedEmail] = []
                    for item in results:
                        if item.message_id in flagged_for_expunge and item.success:
                            adjusted_results.append(
                                MovedEmail(
                                    message_id=item.message_id,
                                    success=False,
                                    error=expunge_error,
                                    method="fallback",
                                )
                            )
                        else:
                            adjusted_results.append(item)
                    return adjusted_results

            return results

    async def copy_emails(
        self,
        email_ids: list[str],
        source_mailbox: str,
        destination_mailbox: str,
    ) -> list[CopiedEmail]:
        async with self._login_logout() as imap:
            await self.mailbox_ops.ensure_delimiter(imap)
            await imap.select(_quote_mailbox(self.mailbox_ops.to_imap_path(source_mailbox)))
            destination = _quote_mailbox(self.mailbox_ops.to_imap_path(destination_mailbox))
            results: list[CopiedEmail] = []
            for email_id in email_ids:
                try:
                    result, lines = await imap.uid("copy", email_id, destination)
                    if result == "OK":
                        results.append(CopiedEmail(message_id=email_id, success=True, error=None))
                    else:
                        results.append(
                            CopiedEmail(
                                message_id=email_id,
                                success=False,
                                error=f"UID COPY failed with IMAP result {result}: {lines!r}",
                            )
                        )
                except Exception as e:
                    results.append(CopiedEmail(message_id=email_id, success=False, error=str(e)))
            return results

    async def mark_emails(
        self,
        email_ids: list[str],
        mailbox: str = "INBOX",
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> list[MarkedEmail]:
        add_flags: list[str] = []
        remove_flags: list[str] = []

        flag_updates = [
            (seen, r"\Seen"),
            (flagged, r"\Flagged"),
            (answered, r"\Answered"),
        ]
        for value, flag in flag_updates:
            if value is True:
                add_flags.append(flag)
            elif value is False:
                remove_flags.append(flag)

        if not add_flags and not remove_flags:
            raise ValueError("At least one of seen, flagged, or answered must be set")

        async with self._login_logout() as imap:
            await self.mailbox_ops.ensure_delimiter(imap)
            await imap.select(_quote_mailbox(self.mailbox_ops.to_imap_path(mailbox)))
            results: list[MarkedEmail] = []
            for email_id in email_ids:
                try:
                    if add_flags:
                        add_result, add_lines = await imap.uid(
                            "store",
                            email_id,
                            "+FLAGS",
                            f"({' '.join(add_flags)})",
                        )
                        if add_result != "OK":
                            raise RuntimeError(f"UID STORE +FLAGS failed with IMAP result {add_result}: {add_lines!r}")
                    if remove_flags:
                        remove_result, remove_lines = await imap.uid(
                            "store",
                            email_id,
                            "-FLAGS",
                            f"({' '.join(remove_flags)})",
                        )
                        if remove_result != "OK":
                            raise RuntimeError(f"UID STORE -FLAGS failed with IMAP result {remove_result}: {remove_lines!r}")
                    results.append(MarkedEmail(message_id=email_id, success=True, error=None))
                except Exception as e:
                    results.append(MarkedEmail(message_id=email_id, success=False, error=str(e)))
            return results
