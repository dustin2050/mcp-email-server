# Mailbox Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IMAP folder management (list/create/rename/delete/status), email move/copy/mark, and full-text body/text search to the mcp-email-server MCP tools.

**Architecture:** New `mcp_email_server/emails/mailbox.py` module with `MailboxOps` (folder management + delimiter detection) and `EmailOps` (move/copy/mark) helper classes. `ClassicEmailHandler` delegates to these. `EmailHandler` abstract base extended. Tool surface in `app.py` adds 9 new tools and 2 new params on `list_emails_metadata`. See spec at `docs/superpowers/specs/2026-05-09-mailbox-management-design.md`.

**Tech Stack:** Python 3.10+, FastMCP, aioimaplib, pydantic v2, pytest with `pytest-asyncio`, mocking via `unittest.mock.AsyncMock`.

---

## Verified Repo Decisions

1. Q1: `pyproject.toml:20-31` declares `aioimaplib>=2.0.1`, and `uv.lock:20-27` pins `aioimaplib` to `2.0.1`. Inspecting the pinned wheel source shows native UID MOVE support already exists in `aioimaplib/aioimaplib.py:550-577` and `747-853`: `IMAP4.uid()` dispatches `MOVE` to `self.move(..., by_uid=True)`, and `move()` is implemented. Use `await imap.uid("move", uid_set, _quote_mailbox(destination_imap_path))`. Wire-level reference: `Command.__repr__` plus `Protocol.send` in `aioimaplib/aioimaplib.py:164-168` and `409-415` serialize the raw command as `b"{tag} UID MOVE {uid_set} \"{destination}\"\r\n"`.
2. Q2: `classic.py` currently parses `aioimaplib` responses by iterating `response.lines` and applying `re.search(..., item)` on `bytes`, then decoding capture groups; see `_fetch_dates_chunk()` at `classic.py:375-385` and `_batch_fetch_headers()` at `classic.py:433-454`. Task 9 below uses the same bytes-plus-regex style and includes the exact STATUS parser code.
3. Q3: the request text is stale here: there is no existing LIST/LSUB regex in `classic.py:889-899`; the current code uses `folder_str.split('"')` while finding the `\Sent` folder. The plan resolves that mismatch explicitly by introducing `LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$')` in `mailbox.py` and using it for all new LIST/LSUB parsing.
4. Q4: `pytest-asyncio` is already present in `pyproject.toml:41-53`, and `pytest.ini:1-4` already sets `asyncio_mode = auto`. No pytest config changes are needed anywhere in this plan.
5. Q5: do not import `_quote_mailbox` from `classic.py` into `mailbox.py`. Once `classic.py` imports `MailboxOps`/`EmailOps`, that would create a circular import. Move `_quote_mailbox` into a new shared `mcp_email_server/emails/_helpers.py`, then import it from both `classic.py` and `mailbox.py`.
6. Q6: apply the same decision consistently to `_send_imap_id` and `_create_ssl_context`. Move all three helpers into `mcp_email_server/emails/_helpers.py`, re-import them in `classic.py`, and keep `_create_smtp_ssl_context = _create_ssl_context` in `classic.py` so the existing `tests/test_email_client.py` import remains valid.

## Task 1: Add Pydantic mailbox/email operation models

**Files:**
- Modify `mcp_email_server/emails/models.py` after line 65.
- Modify `tests/test_models.py` imports at lines 3-7 and append after line 170.

- [ ] Step 1. Write the failing model tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_models.py
@@
-from mcp_email_server.emails.models import (
-    EmailBodyResponse,
-    EmailMetadata,
-    EmailMetadataPageResponse,
-)
+from mcp_email_server.emails.models import (
+    CopiedEmail,
+    EmailBodyResponse,
+    EmailMetadata,
+    EmailMetadataPageResponse,
+    MailboxInfo,
+    MailboxStatusResponse,
+    MarkedEmail,
+    MovedEmail,
+)
@@
 def test_email_body_response_includes_message_id():
     """Test that EmailBodyResponse includes message_id field."""
     response = EmailBodyResponse(
         email_id="123",
         message_id="<abc123@example.com>",
@@
     )
     assert response.message_id == "<abc123@example.com>"
+
+
+def test_mailbox_info_model():
+    mailbox = MailboxInfo(
+        path="INBOX/Archive",
+        delimiter=".",
+        flags=[r"\\HasNoChildren"],
+        subscribed=True,
+    )
+    assert mailbox.path == "INBOX/Archive"
+    assert mailbox.delimiter == "."
+    assert mailbox.flags == [r"\\HasNoChildren"]
+    assert mailbox.subscribed is True
+
+
+def test_mailbox_status_response_model():
+    status = MailboxStatusResponse(
+        path="INBOX/Archive",
+        messages=12,
+        recent=1,
+        unseen=3,
+        uid_next=45,
+        uid_validity=99,
+        flags=[r"\\Seen", r"\\Answered"],
+        permanent_flags=[r"\\Seen", r"\\Answered", r"\\*"],
+    )
+    assert status.path == "INBOX/Archive"
+    assert status.messages == 12
+    assert status.recent == 1
+    assert status.unseen == 3
+    assert status.uid_next == 45
+    assert status.uid_validity == 99
+
+
+def test_moved_email_model():
+    moved = MovedEmail(message_id="101", success=True, error=None, method="native")
+    assert moved.message_id == "101"
+    assert moved.success is True
+    assert moved.error is None
+    assert moved.method == "native"
+
+
+def test_copied_email_model():
+    copied = CopiedEmail(message_id="202", success=False, error="copy failed")
+    assert copied.message_id == "202"
+    assert copied.success is False
+    assert copied.error == "copy failed"
+
+
+def test_marked_email_model():
+    marked = MarkedEmail(message_id="303", success=True, error=None)
+    assert marked.message_id == "303"
+    assert marked.success is True
+    assert marked.error is None
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the new model classes do not exist yet.
```bash
uv run pytest tests/test_models.py -q
```
Expected: import errors for `MailboxInfo`, `MailboxStatusResponse`, `MovedEmail`, `CopiedEmail`, and `MarkedEmail`.

- [ ] Step 3. Implement the models in `mcp_email_server/emails/models.py`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/models.py
@@
-from datetime import datetime
-from typing import Any
+from datetime import datetime
+from typing import Any, Literal
@@
 class AttachmentDownloadResponse(BaseModel):
     """Attachment download response"""
@@
     attachment_name: str
     mime_type: str
     size: int
     saved_path: str
+
+
+class MailboxInfo(BaseModel):
+    path: str
+    delimiter: str
+    flags: list[str]
+    subscribed: bool
+
+
+class MailboxStatusResponse(BaseModel):
+    path: str
+    messages: int
+    recent: int
+    unseen: int | None = None
+    uid_next: int | None = None
+    uid_validity: int | None = None
+    flags: list[str]
+    permanent_flags: list[str]
+
+
+class MovedEmail(BaseModel):
+    message_id: str
+    success: bool
+    error: str | None = None
+    method: Literal["native", "fallback"]
+
+
+class CopiedEmail(BaseModel):
+    message_id: str
+    success: bool
+    error: str | None = None
+
+
+class MarkedEmail(BaseModel):
+    message_id: str
+    success: bool
+    error: str | None = None
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_models.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/models.py tests/test_models.py
git commit -F - <<'EOF'
feat: add mailbox operation response models

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 2: Extend `EmailHandler` with the mailbox-management contract

**Files:**
- Modify `mcp_email_server/emails/__init__.py` lines 1-107.
- Modify `mcp_email_server/emails/classic.py` imports at lines 23-29 and append temporary stubs after line 1182.
- Create `tests/test_email_handler_contract.py`.

- [ ] Step 1. Write failing contract tests for the abstract base and temporary Classic handler compatibility.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Add File: tests/test_email_handler_contract.py
+import inspect
+
+import pytest
+
+from mcp_email_server.config import EmailServer, EmailSettings
+from mcp_email_server.emails import EmailHandler
+from mcp_email_server.emails.classic import ClassicEmailHandler
+
+
+def build_settings() -> EmailSettings:
+    return EmailSettings(
+        account_name="contract",
+        full_name="Contract Tester",
+        email_address="contract@example.com",
+        incoming=EmailServer(
+            user_name="contract",
+            password="secret",
+            host="imap.example.com",
+            port=993,
+            use_ssl=True,
+        ),
+        outgoing=EmailServer(
+            user_name="contract",
+            password="secret",
+            host="smtp.example.com",
+            port=465,
+            use_ssl=True,
+        ),
+    )
+
+
+def test_email_handler_metadata_signature_exposes_body_contains_and_text():
+    params = inspect.signature(EmailHandler.get_emails_metadata).parameters
+    assert "body_contains" in params
+    assert params["body_contains"].default is None
+    assert "text" in params
+    assert params["text"].default is None
+
+
+def test_classic_email_handler_still_instantiates_after_contract_extension():
+    handler = ClassicEmailHandler(build_settings())
+    assert isinstance(handler, EmailHandler)
+
+
+@pytest.mark.asyncio
+async def test_classic_email_handler_mailbox_contract_methods_exist_as_stubs():
+    handler = ClassicEmailHandler(build_settings())
+
+    with pytest.raises(NotImplementedError):
+        await handler.list_mailboxes()
+    with pytest.raises(NotImplementedError):
+        await handler.create_mailbox("INBOX/Archive")
+    with pytest.raises(NotImplementedError):
+        await handler.rename_mailbox("INBOX/Old", "INBOX/New")
+    with pytest.raises(NotImplementedError):
+        await handler.delete_mailbox("INBOX/Archive", confirm=True)
+    with pytest.raises(NotImplementedError):
+        await handler.get_mailbox_status("INBOX")
+    with pytest.raises(NotImplementedError):
+        await handler.move_emails(["1"], "INBOX", "Archive")
+    with pytest.raises(NotImplementedError):
+        await handler.copy_emails(["1"], "INBOX", "Archive")
+    with pytest.raises(NotImplementedError):
+        await handler.mark_emails(["1"], mailbox="INBOX", seen=True)
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the new abstract methods and metadata args are not declared yet.
```bash
uv run pytest tests/test_email_handler_contract.py -q
```
Expected: signature assertion failure for `body_contains`/`text` and `AttributeError` on the new handler methods.

- [ ] Step 3. Extend `EmailHandler` and add temporary Classic handler stubs so the class remains instantiable until Task 15 wires the real delegates.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/__init__.py
@@
 if TYPE_CHECKING:
     from mcp_email_server.emails.models import (
         AttachmentDownloadResponse,
+        CopiedEmail,
         EmailContentBatchResponse,
         EmailMetadataPageResponse,
+        MailboxInfo,
+        MailboxStatusResponse,
+        MarkedEmail,
+        MovedEmail,
     )
@@
     async def get_emails_metadata(
         self,
         page: int = 1,
         page_size: int = 10,
         before: datetime | None = None,
         since: datetime | None = None,
         subject: str | None = None,
+        body_contains: str | None = None,
+        text: str | None = None,
         from_address: str | None = None,
         to_address: str | None = None,
         order: str = "desc",
         mailbox: str = "INBOX",
         seen: bool | None = None,
@@
             before: Filter emails before this datetime.
             since: Filter emails since this datetime.
             subject: Filter by subject (substring match).
+            body_contains: Filter by IMAP BODY search.
+            text: Filter by IMAP TEXT search.
             from_address: Filter by sender address.
             to_address: Filter by recipient address.
             order: Sort order ('asc' or 'desc').
             mailbox: Mailbox to search (default: 'INBOX').
             seen: Filter by read status (True=read, False=unread, None=all).
             flagged: Filter by flagged/starred status (True=flagged, False=unflagged, None=all).
             answered: Filter by replied status (True=replied, False=not replied, None=all).
         """
+
+    @abc.abstractmethod
+    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list["MailboxInfo"]:
+        """List mailboxes."""
+
+    @abc.abstractmethod
+    async def create_mailbox(self, mailbox: str) -> str:
+        """Create a mailbox."""
+
+    @abc.abstractmethod
+    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
+        """Rename a mailbox."""
+
+    @abc.abstractmethod
+    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
+        """Delete a mailbox."""
+
+    @abc.abstractmethod
+    async def get_mailbox_status(self, mailbox: str = "INBOX") -> "MailboxStatusResponse":
+        """Get mailbox status."""
+
+    @abc.abstractmethod
+    async def move_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list["MovedEmail"]:
+        """Move emails."""
+
+    @abc.abstractmethod
+    async def copy_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list["CopiedEmail"]:
+        """Copy emails."""
+
+    @abc.abstractmethod
+    async def mark_emails(
+        self,
+        email_ids: list[str],
+        mailbox: str = "INBOX",
+        seen: bool | None = None,
+        flagged: bool | None = None,
+        answered: bool | None = None,
+    ) -> list["MarkedEmail"]:
+        """Mark emails."""
*** Update File: mcp_email_server/emails/classic.py
@@
 from mcp_email_server.emails.models import (
     AttachmentDownloadResponse,
+    CopiedEmail,
     EmailBodyResponse,
     EmailContentBatchResponse,
     EmailMetadata,
     EmailMetadataPageResponse,
+    MailboxInfo,
+    MailboxStatusResponse,
+    MarkedEmail,
+    MovedEmail,
 )
@@
     async def download_attachment(
         self,
         email_id: str,
         attachment_name: str,
         save_path: str,
@@
         return AttachmentDownloadResponse(
             email_id=result["email_id"],
             attachment_name=result["attachment_name"],
             mime_type=result["mime_type"],
             size=result["size"],
             saved_path=result["saved_path"],
         )
+
+    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list[MailboxInfo]:
+        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+
+    async def create_mailbox(self, mailbox: str) -> str:
+        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+
+    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
+        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+
+    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
+        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+
+    async def get_mailbox_status(self, mailbox: str = "INBOX") -> MailboxStatusResponse:
+        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+
+    async def move_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list[MovedEmail]:
+        raise NotImplementedError("Task 15 wires EmailOps into ClassicEmailHandler")
+
+    async def copy_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list[CopiedEmail]:
+        raise NotImplementedError("Task 15 wires EmailOps into ClassicEmailHandler")
+
+    async def mark_emails(
+        self,
+        email_ids: list[str],
+        mailbox: str = "INBOX",
+        seen: bool | None = None,
+        flagged: bool | None = None,
+        answered: bool | None = None,
+    ) -> list[MarkedEmail]:
+        raise NotImplementedError("Task 15 wires EmailOps into ClassicEmailHandler")
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_handler_contract.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/__init__.py mcp_email_server/emails/classic.py tests/test_email_handler_contract.py
git commit -F - <<'EOF'
refactor: extend email handler mailbox contract

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 3: Create `mailbox.py` skeleton and move shared IMAP helpers

**Files:**
- Create `mcp_email_server/emails/_helpers.py`.
- Create `mcp_email_server/emails/mailbox.py`.
- Modify `mcp_email_server/emails/classic.py` imports and helper definitions at lines 1-97.
- Create `tests/test_mailbox_ops.py`.

- [ ] Step 1. Write failing skeleton tests for `MailboxOps.__init__`, `_imap_connect()`, and `_login_logout()`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Add File: tests/test_mailbox_ops.py
+import asyncio
+from unittest.mock import AsyncMock, patch
+
+import pytest
+
+from mcp_email_server.config import EmailServer
+from mcp_email_server.emails.mailbox import MailboxOps
+
+
+@pytest.fixture
+def email_server():
+    return EmailServer(
+        user_name="mailbox_user",
+        password="mailbox_password",
+        host="imap.example.com",
+        port=993,
+        use_ssl=True,
+        verify_ssl=False,
+    )
+
+
+class TestMailboxOpsSkeleton:
+    def test_init_sets_server_and_imap_class(self, email_server):
+        ops = MailboxOps(email_server)
+        assert ops.email_server == email_server
+        assert ops.imap_class.__name__ == "IMAP4_SSL"
+        assert ops._delimiter is None
+
+    def test_imap_connect_passes_ssl_context(self, email_server):
+        ops = MailboxOps(email_server)
+
+        with patch.object(ops, "imap_class") as mock_imap_class:
+            ops._imap_connect()
+            mock_imap_class.assert_called_once()
+            assert mock_imap_class.call_args.kwargs["ssl_context"] is not None
+
+    @pytest.mark.asyncio
+    async def test_login_logout_logs_in_sends_id_and_logs_out(self, email_server):
+        ops = MailboxOps(email_server)
+        mock_imap = AsyncMock()
+        mock_imap._client_task = asyncio.Future()
+        mock_imap._client_task.set_result(None)
+        mock_imap.wait_hello_from_server = AsyncMock()
+        mock_imap.login = AsyncMock()
+        mock_imap.logout = AsyncMock()
+
+        with patch.object(ops, "_imap_connect", return_value=mock_imap):
+            with patch("mcp_email_server.emails.mailbox._send_imap_id", new=AsyncMock()) as mock_send_id:
+                async with ops._login_logout() as imap:
+                    assert imap is mock_imap
+
+        mock_imap.login.assert_called_once_with(
+            email_server.user_name,
+            email_server.password.get_secret_value(),
+        )
+        mock_send_id.assert_awaited_once_with(mock_imap)
+        mock_imap.logout.assert_awaited_once()
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `mailbox.py` and the shared helper module do not exist yet.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: import failure for `mcp_email_server.emails.mailbox`.

- [ ] Step 3. Move `_quote_mailbox`, `_send_imap_id`, and `_create_ssl_context` into `emails/_helpers.py`, re-import them from `classic.py`, keep `_create_smtp_ssl_context = _create_ssl_context` in `classic.py`, and add the `MailboxOps` skeleton in `mailbox.py`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Add File: mcp_email_server/emails/_helpers.py
+import ssl
+
+import aioimaplib
+
+from mcp_email_server.log import logger
+
+
+def _quote_mailbox(mailbox: str) -> str:
+    escaped = mailbox.replace("\\", "\\\\").replace('"', r"\"")
+    return f'"{escaped}"'
+
+
+async def _send_imap_id(imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL) -> None:
+    try:
+        response = await imap.id(name="mcp-email-server", version="1.0.0")
+        if response.result != "OK":
+            await imap.protocol.execute(
+                aioimaplib.Command(
+                    "ID",
+                    imap.protocol.new_tag(),
+                    '("name" "mcp-email-server" "version" "1.0.0")',
+                )
+            )
+    except Exception as e:
+        logger.warning(f"IMAP ID command failed: {e!s}")
+
+
+def _create_ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
+    if verify_ssl:
+        return None
+    ctx = ssl.create_default_context()
+    ctx.check_hostname = False
+    ctx.verify_mode = ssl.CERT_NONE
+    return ctx
*** Add File: mcp_email_server/emails/mailbox.py
+from __future__ import annotations
+
+from contextlib import asynccontextmanager
+
+import aioimaplib
+
+from mcp_email_server.config import EmailServer
+from mcp_email_server.emails._helpers import _create_ssl_context, _send_imap_id
+from mcp_email_server.log import logger
+
+
+class MailboxOps:
+    def __init__(self, email_server: EmailServer):
+        self.email_server = email_server
+        self.imap_class = aioimaplib.IMAP4_SSL if email_server.use_ssl else aioimaplib.IMAP4
+        self._delimiter: str | None = None
+
+    def _imap_connect(self) -> aioimaplib.IMAP4_SSL | aioimaplib.IMAP4:
+        if self.email_server.use_ssl:
+            return self.imap_class(
+                self.email_server.host,
+                self.email_server.port,
+                ssl_context=_create_ssl_context(self.email_server.verify_ssl),
+            )
+        return self.imap_class(self.email_server.host, self.email_server.port)
+
+    @asynccontextmanager
+    async def _login_logout(self):
+        imap = self._imap_connect()
+        try:
+            await imap._client_task
+            await imap.wait_hello_from_server()
+            await imap.login(self.email_server.user_name, self.email_server.password.get_secret_value())
+            await _send_imap_id(imap)
+            yield imap
+        finally:
+            try:
+                await imap.logout()
+            except Exception as e:
+                logger.info(f"Error during logout: {e}")
*** Update File: mcp_email_server/emails/classic.py
@@
-import ssl
 import time
@@
-from mcp_email_server.emails.models import (
+from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
+from mcp_email_server.emails.models import (
     AttachmentDownloadResponse,
     CopiedEmail,
     EmailBodyResponse,
@@
-# Maximum body length before truncation (characters)
-MAX_BODY_LENGTH = 20000
-
-
-def _quote_mailbox(mailbox: str) -> str:
-    """Quote mailbox name for IMAP compatibility.
-
-    Some IMAP servers (notably Proton Mail Bridge) require mailbox names
-    to be quoted. This is valid per RFC 3501 and works with all IMAP servers.
-
-    Per RFC 3501 Section 9 (Formal Syntax), quoted strings must escape
-    backslashes and double-quote characters with a preceding backslash.
-
-    See: https://github.com/ai-zerolab/mcp-email-server/issues/87
-    See: https://www.rfc-editor.org/rfc/rfc3501#section-9
-    """
-    # Per RFC 3501, literal double-quote characters in a quoted string must
-    # be escaped with a backslash. Backslashes themselves must also be escaped.
-    escaped = mailbox.replace("\\", "\\\\").replace('"', r"\"")
-    return f'"{escaped}"'
-
-
-async def _send_imap_id(imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL) -> None:
-    """Send IMAP ID command with fallback for strict servers like 163.com.
-
-    aioimaplib's id() method sends ID command with spaces between parentheses
-    and content (e.g., 'ID ( "name" "value" )'), which some strict IMAP servers
-    like 163.com reject with 'BAD Parse command error'.
-
-    This function first tries the standard id() method, and if it fails,
-    falls back to sending a raw command with correct format.
-
-    See: https://github.com/ai-zerolab/mcp-email-server/issues/85
-    """
-    try:
-        response = await imap.id(name="mcp-email-server", version="1.0.0")
-        if response.result != "OK":
-            # Fallback for strict servers (e.g., 163.com)
-            # Send raw command with correct parenthesis format
-            await imap.protocol.execute(
-                aioimaplib.Command(
-                    "ID",
-                    imap.protocol.new_tag(),
-                    '("name" "mcp-email-server" "version" "1.0.0")',
-                )
-            )
-    except Exception as e:
-        logger.warning(f"IMAP ID command failed: {e!s}")
-
-
-def _create_ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
-    """Create SSL context for SMTP/IMAP connections.
-
-    Returns None for default verification, or permissive context
-    for self-signed certificates when verify_ssl=False.
-    """
-    if verify_ssl:
-        return None
-    ctx = ssl.create_default_context()
-    ctx.check_hostname = False
-    ctx.verify_mode = ssl.CERT_NONE
-    return ctx
+# Maximum body length before truncation (characters)
+MAX_BODY_LENGTH = 20000
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/_helpers.py mcp_email_server/emails/mailbox.py mcp_email_server/emails/classic.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
refactor: extract imap helpers and add mailbox ops skeleton

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 4: Implement delimiter detection and path translation

**Files:**
- Modify `mcp_email_server/emails/mailbox.py` created in Task 3.
- Modify `tests/test_mailbox_ops.py` and append delimiter/path tests after line 43.

- [ ] Step 1. Add failing delimiter and path-translation tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mailbox_ops.py
@@
 class TestMailboxOpsSkeleton:
@@
         mock_send_id.assert_awaited_once_with(mock_imap)
         mock_imap.logout.assert_awaited_once()
+
+
+class TestMailboxDelimiterTranslation:
+    @pytest.mark.asyncio
+    async def test_ensure_delimiter_parses_dot_and_caches(self, email_server):
+        ops = MailboxOps(email_server)
+        mock_imap = AsyncMock()
+        mock_imap.list = AsyncMock(return_value=("OK", [b'(\\HasNoChildren) "." "INBOX"']))
+
+        delimiter = await ops.ensure_delimiter(mock_imap)
+        cached = await ops.ensure_delimiter(mock_imap)
+
+        assert delimiter == "."
+        assert cached == "."
+        mock_imap.list.assert_awaited_once_with('""', "*")
+
+    @pytest.mark.asyncio
+    async def test_ensure_delimiter_parses_nil_as_flat_namespace(self, email_server):
+        ops = MailboxOps(email_server)
+        mock_imap = AsyncMock()
+        mock_imap.list = AsyncMock(return_value=("OK", [b'(\\Noselect) NIL "Archive"']))
+
+        delimiter = await ops.ensure_delimiter(mock_imap)
+
+        assert delimiter == ""
+
+    def test_to_imap_path_uses_cached_delimiter(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        assert ops.to_imap_path("INBOX/Archive/2026") == "INBOX.Archive.2026"
+
+    def test_to_imap_path_rejects_empty_segments(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        with pytest.raises(ValueError, match="Invalid mailbox path"):
+            ops.to_imap_path("INBOX//Archive")
+
+    def test_from_imap_path_rewrites_server_delimiter_to_slash(self, email_server):
+        ops = MailboxOps(email_server)
+        assert ops.from_imap_path("INBOX.Archive.2026", ".") == "INBOX/Archive/2026"
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the delimiter methods do not exist yet.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: `AttributeError` for `ensure_delimiter`, `to_imap_path`, and `from_imap_path`.

- [ ] Step 3. Implement `LIST_LINE_RE`, `ensure_delimiter()`, `to_imap_path()`, `from_imap_path()`, and the private delimiter guard.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 from __future__ import annotations
 
 from contextlib import asynccontextmanager
+import re
 
 import aioimaplib
@@
 from mcp_email_server.log import logger
 
+LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$')
 
 class MailboxOps:
@@
             except Exception as e:
                 logger.info(f"Error during logout: {e}")
+
+    def _delimiter_or_raise(self) -> str:
+        if self._delimiter is None:
+            raise RuntimeError("Mailbox delimiter has not been detected yet")
+        return self._delimiter
+
+    async def ensure_delimiter(self, imap: aioimaplib.IMAP4_SSL | aioimaplib.IMAP4 | None = None) -> str:
+        if self._delimiter is not None:
+            return self._delimiter
+
+        if imap is None:
+            async with self._login_logout() as owned_imap:
+                return await self.ensure_delimiter(owned_imap)
+
+        _, lines = await imap.list('""', "*")
+        for line in lines:
+            if not isinstance(line, bytes):
+                continue
+            match = LIST_LINE_RE.match(line)
+            if not match:
+                continue
+            delimiter_token = match.group("delimiter")
+            self._delimiter = "" if delimiter_token == b"NIL" else delimiter_token[1:-1].decode("utf-8")
+            return self._delimiter
+
+        raise RuntimeError("Could not detect mailbox delimiter from IMAP LIST response")
+
+    def to_imap_path(self, user_path: str) -> str:
+        delimiter = self._delimiter_or_raise()
+        if delimiter == "":
+            return user_path
+        parts = user_path.split("/")
+        if any(part == "" for part in parts):
+            raise ValueError(f"Invalid mailbox path: {user_path!r}")
+        return delimiter.join(parts)
+
+    def from_imap_path(self, server_path: str, delimiter: str) -> str:
+        if delimiter == "":
+            return server_path
+        return server_path.replace(delimiter, "/")
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
feat: add mailbox delimiter translation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 5: Implement `MailboxOps.list_mailboxes(pattern, subscribed_only)`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_mailbox_ops.py` and append listing tests after the delimiter block.

- [ ] Step 1. Add failing LIST/LSUB parsing tests using the exact `LIST_LINE_RE` introduced for this feature.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mailbox_ops.py
@@
 class TestMailboxDelimiterTranslation:
@@
     def test_from_imap_path_rewrites_server_delimiter_to_slash(self, email_server):
         ops = MailboxOps(email_server)
         assert ops.from_imap_path("INBOX.Archive.2026", ".") == "INBOX/Archive/2026"
+
+
+class TestMailboxListing:
+    @pytest.mark.asyncio
+    async def test_list_mailboxes_uses_list_and_parses_paths(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        mock_imap = AsyncMock()
+        mock_imap.list = AsyncMock(
+            return_value=(
+                "OK",
+                [
+                    b'(\\HasNoChildren) "." "INBOX.Archive"',
+                    b'(\\HasChildren \\Subscribed) "." "INBOX.Projects"',
+                ],
+            )
+        )
+
+        with patch.object(ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            mailboxes = await ops.list_mailboxes(pattern="INBOX/*")
+
+        assert [mailbox.path for mailbox in mailboxes] == ["INBOX/Archive", "INBOX/Projects"]
+        assert mailboxes[0].delimiter == "."
+        assert mailboxes[0].flags == [r"\HasNoChildren"]
+        assert mailboxes[0].subscribed is False
+        assert mailboxes[1].subscribed is True
+        mock_imap.list.assert_awaited_once_with('""', "INBOX.%")
+
+    @pytest.mark.asyncio
+    async def test_list_mailboxes_uses_lsub_for_subscribed_only(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        mock_imap = AsyncMock()
+        mock_imap.lsub = AsyncMock(
+            return_value=("OK", [b'(\\Subscribed \\HasNoChildren) "." "INBOX.Newsletters"'])
+        )
+
+        with patch.object(ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            mailboxes = await ops.list_mailboxes(pattern="INBOX/*", subscribed_only=True)
+
+        assert len(mailboxes) == 1
+        assert mailboxes[0].path == "INBOX/Newsletters"
+        assert mailboxes[0].subscribed is True
+        mock_imap.lsub.assert_awaited_once_with('""', "INBOX.%")
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `list_mailboxes()` is not implemented yet.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: `AttributeError` for `list_mailboxes`.

- [ ] Step 3. Implement `list_mailboxes()` with the new `LIST_LINE_RE` parser. This is the concrete LIST/LSUB parser replacing the stale “existing regex” assumption from Q3.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 from mcp_email_server.config import EmailServer
 from mcp_email_server.emails._helpers import _create_ssl_context, _send_imap_id
+from mcp_email_server.emails.models import MailboxInfo
 from mcp_email_server.log import logger
@@
     def from_imap_path(self, server_path: str, delimiter: str) -> str:
         if delimiter == "":
             return server_path
         return server_path.replace(delimiter, "/")
+
+    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list[MailboxInfo]:
+        async with self._login_logout() as imap:
+            delimiter = await self.ensure_delimiter(imap)
+            imap_pattern = pattern if delimiter == "" else pattern.replace("/", delimiter)
+            if subscribed_only:
+                _, lines = await imap.lsub('""', imap_pattern)
+            else:
+                _, lines = await imap.list('""', imap_pattern)
+
+            mailboxes: list[MailboxInfo] = []
+            for line in lines:
+                if not isinstance(line, bytes):
+                    continue
+                match = LIST_LINE_RE.match(line)
+                if not match:
+                    continue
+
+                flags_bytes = match.group("flags")
+                flags = [flag.decode("utf-8") for flag in flags_bytes.split()] if flags_bytes else []
+                delimiter_token = match.group("delimiter")
+                line_delimiter = "" if delimiter_token == b"NIL" else delimiter_token[1:-1].decode("utf-8")
+                name_token = match.group("name").strip()
+                if name_token.startswith(b'"') and name_token.endswith(b'"'):
+                    server_name = name_token[1:-1].decode("utf-8")
+                else:
+                    server_name = name_token.decode("utf-8")
+
+                mailboxes.append(
+                    MailboxInfo(
+                        path=self.from_imap_path(server_name, line_delimiter or delimiter),
+                        delimiter=line_delimiter,
+                        flags=flags,
+                        subscribed=subscribed_only or r"\Subscribed" in flags,
+                    )
+                )
+
+            return mailboxes
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
feat: add mailbox listing support

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 6: Implement `MailboxOps.create_mailbox(mailbox)`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_mailbox_ops.py` and append create-mailbox coverage.

- [ ] Step 1. Add the failing create-mailbox test.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mailbox_ops.py
@@
 class TestMailboxListing:
@@
         assert len(mailboxes) == 1
         assert mailboxes[0].path == "INBOX/Newsletters"
         assert mailboxes[0].subscribed is True
         mock_imap.lsub.assert_awaited_once_with('""', "INBOX.%")
+
+
+class TestMailboxMutation:
+    @pytest.mark.asyncio
+    async def test_create_mailbox_translates_and_quotes_path(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        mock_imap = AsyncMock()
+        mock_imap.create = AsyncMock(return_value=("OK", [b"create completed"]))
+
+        with patch.object(ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            result = await ops.create_mailbox("INBOX/Projects/2026")
+
+        assert result == "Successfully created mailbox 'INBOX/Projects/2026'"
+        mock_imap.create.assert_awaited_once_with('"INBOX.Projects.2026"')
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `create_mailbox()` is missing.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: `AttributeError` for `create_mailbox`.

- [ ] Step 3. Implement `create_mailbox()`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 from mcp_email_server.config import EmailServer
-from mcp_email_server.emails._helpers import _create_ssl_context, _send_imap_id
+from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
@@
     async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list[MailboxInfo]:
@@
 
             return mailboxes
+
+    async def create_mailbox(self, mailbox: str) -> str:
+        async with self._login_logout() as imap:
+            await self.ensure_delimiter(imap)
+            await imap.create(_quote_mailbox(self.to_imap_path(mailbox)))
+            return f"Successfully created mailbox '{mailbox}'"
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
feat: add mailbox creation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 7: Implement `MailboxOps.rename_mailbox(old_mailbox, new_mailbox)`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_mailbox_ops.py` and append rename coverage.

- [ ] Step 1. Add the failing rename tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mailbox_ops.py
@@
 class TestMailboxMutation:
@@
         assert result == "Successfully created mailbox 'INBOX/Projects/2026'"
         mock_imap.create.assert_awaited_once_with('"INBOX.Projects.2026"')
+
+    @pytest.mark.asyncio
+    async def test_rename_mailbox_translates_both_paths(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        mock_imap = AsyncMock()
+        mock_imap.rename = AsyncMock(return_value=("OK", [b"rename completed"]))
+
+        with patch.object(ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            result = await ops.rename_mailbox("INBOX/Projects/2025", "INBOX/Projects/2026")
+
+        assert result == "Successfully renamed mailbox 'INBOX/Projects/2025' to 'INBOX/Projects/2026'"
+        mock_imap.rename.assert_awaited_once_with('"INBOX.Projects.2025"', '"INBOX.Projects.2026"')
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `rename_mailbox()` is missing.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: `AttributeError` for `rename_mailbox`.

- [ ] Step 3. Implement `rename_mailbox()`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
     async def create_mailbox(self, mailbox: str) -> str:
         async with self._login_logout() as imap:
             await self.ensure_delimiter(imap)
             await imap.create(_quote_mailbox(self.to_imap_path(mailbox)))
             return f"Successfully created mailbox '{mailbox}'"
+
+    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
+        async with self._login_logout() as imap:
+            await self.ensure_delimiter(imap)
+            await imap.rename(
+                _quote_mailbox(self.to_imap_path(old_mailbox)),
+                _quote_mailbox(self.to_imap_path(new_mailbox)),
+            )
+            return f"Successfully renamed mailbox '{old_mailbox}' to '{new_mailbox}'"
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
feat: add mailbox rename support

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 8: Implement `MailboxOps.delete_mailbox(mailbox, confirm)`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_mailbox_ops.py` and append delete coverage.

- [ ] Step 1. Add failing safe-delete tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mailbox_ops.py
@@
 class TestMailboxMutation:
@@
         assert result == "Successfully renamed mailbox 'INBOX/Projects/2025' to 'INBOX/Projects/2026'"
         mock_imap.rename.assert_awaited_once_with('"INBOX.Projects.2025"', '"INBOX.Projects.2026"')
+
+    @pytest.mark.asyncio
+    async def test_delete_mailbox_requires_confirm(self, email_server):
+        ops = MailboxOps(email_server)
+        with pytest.raises(ValueError, match="Re-run with confirm=True"):
+            await ops.delete_mailbox("INBOX/Archive")
+
+    @pytest.mark.asyncio
+    async def test_delete_mailbox_translates_and_quotes_when_confirmed(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        mock_imap = AsyncMock()
+        mock_imap.delete = AsyncMock(return_value=("OK", [b"delete completed"]))
+
+        with patch.object(ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            result = await ops.delete_mailbox("INBOX/Archive", confirm=True)
+
+        assert result == "Successfully deleted mailbox 'INBOX/Archive'"
+        mock_imap.delete.assert_awaited_once_with('"INBOX.Archive"')
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `delete_mailbox()` is missing.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: `AttributeError` for `delete_mailbox`.

- [ ] Step 3. Implement `delete_mailbox()` with the required confirmation guard.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
     async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
         async with self._login_logout() as imap:
             await self.ensure_delimiter(imap)
             await imap.rename(
                 _quote_mailbox(self.to_imap_path(old_mailbox)),
                 _quote_mailbox(self.to_imap_path(new_mailbox)),
             )
             return f"Successfully renamed mailbox '{old_mailbox}' to '{new_mailbox}'"
+
+    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
+        if not confirm:
+            raise ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")
+        async with self._login_logout() as imap:
+            await self.ensure_delimiter(imap)
+            await imap.delete(_quote_mailbox(self.to_imap_path(mailbox)))
+            return f"Successfully deleted mailbox '{mailbox}'"
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
feat: add safe mailbox deletion

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 9: Implement `MailboxOps.get_mailbox_status(mailbox)`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_mailbox_ops.py` and append status coverage.

- [ ] Step 1. Add failing EXAMINE+STATUS tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mailbox_ops.py
@@
 class TestMailboxMutation:
@@
         assert result == "Successfully deleted mailbox 'INBOX/Archive'"
         mock_imap.delete.assert_awaited_once_with('"INBOX.Archive"')
+
+
+class TestMailboxStatus:
+    @pytest.mark.asyncio
+    async def test_get_mailbox_status_parses_counts_and_flags(self, email_server):
+        ops = MailboxOps(email_server)
+        ops._delimiter = "."
+        mock_imap = AsyncMock()
+        mock_imap.examine = AsyncMock(
+            return_value=(
+                "OK",
+                [
+                    b"* FLAGS (\\Seen \\Answered \\Flagged)",
+                    b"* OK [PERMANENTFLAGS (\\Seen \\Answered \\Flagged \\*)] Flags permitted.",
+                ],
+            )
+        )
+        mock_imap.status = AsyncMock(
+            return_value=(
+                "OK",
+                [b'* STATUS "INBOX.Archive" (MESSAGES 12 RECENT 1 UIDNEXT 45 UIDVALIDITY 99 UNSEEN 3)'],
+            )
+        )
+
+        with patch.object(ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            status = await ops.get_mailbox_status("INBOX/Archive")
+
+        assert status.path == "INBOX/Archive"
+        assert status.messages == 12
+        assert status.recent == 1
+        assert status.unseen == 3
+        assert status.uid_next == 45
+        assert status.uid_validity == 99
+        assert status.flags == [r"\Seen", r"\Answered", r"\Flagged"]
+        assert status.permanent_flags == [r"\Seen", r"\Answered", r"\Flagged", r"\*"]
+        mock_imap.examine.assert_awaited_once_with('"INBOX.Archive"')
+        mock_imap.status.assert_awaited_once_with('"INBOX.Archive"', "(MESSAGES RECENT UIDNEXT UIDVALIDITY UNSEEN)")
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `get_mailbox_status()` is missing.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```
Expected: `AttributeError` for `get_mailbox_status`.

- [ ] Step 3. Implement `get_mailbox_status()` using the same bytes-plus-regex response parsing style already used in `classic.py`. This is the concrete STATUS parser required by Q2.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 from mcp_email_server.config import EmailServer
 from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
-from mcp_email_server.emails.models import MailboxInfo
+from mcp_email_server.emails.models import MailboxInfo, MailboxStatusResponse
 from mcp_email_server.log import logger
 
 LIST_LINE_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$')
+STATUS_LINE_RE = re.compile(rb'^\* STATUS (?P<mailbox>"(?:[^"\\]|\\.)*"|[^ ]+) \((?P<items>[^)]*)\)$')
+FLAGS_LINE_RE = re.compile(rb'^\* FLAGS \((?P<flags>[^)]*)\)$')
+PERMANENT_FLAGS_LINE_RE = re.compile(rb'^\* OK \[PERMANENTFLAGS \((?P<flags>[^)]*)\)\]')
@@
     async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
         if not confirm:
             raise ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")
         async with self._login_logout() as imap:
             await self.ensure_delimiter(imap)
             await imap.delete(_quote_mailbox(self.to_imap_path(mailbox)))
             return f"Successfully deleted mailbox '{mailbox}'"
+
+    async def get_mailbox_status(self, mailbox: str = "INBOX") -> MailboxStatusResponse:
+        async with self._login_logout() as imap:
+            await self.ensure_delimiter(imap)
+            quoted_mailbox = _quote_mailbox(self.to_imap_path(mailbox))
+
+            _, examine_lines = await imap.examine(quoted_mailbox)
+            _, status_lines = await imap.status(quoted_mailbox, "(MESSAGES RECENT UIDNEXT UIDVALIDITY UNSEEN)")
+
+            flags: list[str] = []
+            permanent_flags: list[str] = []
+            for line in examine_lines:
+                if not isinstance(line, bytes):
+                    continue
+                flags_match = FLAGS_LINE_RE.search(line)
+                if flags_match:
+                    flags = [flag.decode("utf-8") for flag in flags_match.group("flags").split()]
+                permanent_flags_match = PERMANENT_FLAGS_LINE_RE.search(line)
+                if permanent_flags_match:
+                    permanent_flags = [
+                        flag.decode("utf-8")
+                        for flag in permanent_flags_match.group("flags").split()
+                    ]
+
+            counts: dict[str, int] = {}
+            for line in status_lines:
+                if not isinstance(line, bytes):
+                    continue
+                status_match = STATUS_LINE_RE.search(line)
+                if not status_match:
+                    continue
+                items = status_match.group("items").decode("utf-8").split()
+                if len(items) % 2 != 0:
+                    raise RuntimeError(f"Could not parse STATUS response line: {line!r}")
+                counts = {
+                    items[index].lower(): int(items[index + 1])
+                    for index in range(0, len(items), 2)
+                }
+                break
+
+            if not counts:
+                raise RuntimeError(f"Could not parse STATUS response lines: {status_lines!r}")
+
+            return MailboxStatusResponse(
+                path=mailbox,
+                messages=counts["messages"],
+                recent=counts["recent"],
+                unseen=counts.get("unseen"),
+                uid_next=counts.get("uidnext"),
+                uid_validity=counts.get("uidvalidity"),
+                flags=flags,
+                permanent_flags=permanent_flags,
+            )
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mailbox_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_mailbox_ops.py
git commit -F - <<'EOF'
feat: add mailbox status queries

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 10: Create `EmailOps` skeleton

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Create `tests/test_email_ops.py`.

- [ ] Step 1. Write failing constructor/context-manager tests for `EmailOps`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Add File: tests/test_email_ops.py
+import asyncio
+from unittest.mock import AsyncMock, patch
+
+import pytest
+
+from mcp_email_server.config import EmailServer
+from mcp_email_server.emails.mailbox import EmailOps, MailboxOps
+
+
+@pytest.fixture
+def email_server():
+    return EmailServer(
+        user_name="ops_user",
+        password="ops_password",
+        host="imap.example.com",
+        port=993,
+        use_ssl=True,
+    )
+
+
+class TestEmailOpsSkeleton:
+    def test_init_sets_server_and_mailbox_ops(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        email_ops = EmailOps(email_server, mailbox_ops)
+
+        assert email_ops.email_server == email_server
+        assert email_ops.mailbox_ops is mailbox_ops
+        assert email_ops.imap_class.__name__ == "IMAP4_SSL"
+
+    @pytest.mark.asyncio
+    async def test_login_logout_logs_in_and_out(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        email_ops = EmailOps(email_server, mailbox_ops)
+        mock_imap = AsyncMock()
+        mock_imap._client_task = asyncio.Future()
+        mock_imap._client_task.set_result(None)
+        mock_imap.wait_hello_from_server = AsyncMock()
+        mock_imap.login = AsyncMock()
+        mock_imap.logout = AsyncMock()
+
+        with patch.object(email_ops, "_imap_connect", return_value=mock_imap):
+            with patch("mcp_email_server.emails.mailbox._send_imap_id", new=AsyncMock()) as mock_send_id:
+                async with email_ops._login_logout() as imap:
+                    assert imap is mock_imap
+
+        mock_send_id.assert_awaited_once_with(mock_imap)
+        mock_imap.logout.assert_awaited_once()
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `EmailOps` does not exist yet.
```bash
uv run pytest tests/test_email_ops.py -q
```
Expected: import failure or `AttributeError` for `EmailOps`.

- [ ] Step 3. Add the `EmailOps` skeleton with constructor, `_imap_connect()`, and `_login_logout()`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 class MailboxOps:
@@
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
+
+
+class EmailOps:
+    def __init__(self, email_server: EmailServer, mailbox_ops: MailboxOps):
+        self.email_server = email_server
+        self.mailbox_ops = mailbox_ops
+        self.imap_class = aioimaplib.IMAP4_SSL if email_server.use_ssl else aioimaplib.IMAP4
+
+    def _imap_connect(self) -> aioimaplib.IMAP4_SSL | aioimaplib.IMAP4:
+        if self.email_server.use_ssl:
+            return self.imap_class(
+                self.email_server.host,
+                self.email_server.port,
+                ssl_context=_create_ssl_context(self.email_server.verify_ssl),
+            )
+        return self.imap_class(self.email_server.host, self.email_server.port)
+
+    @asynccontextmanager
+    async def _login_logout(self):
+        imap = self._imap_connect()
+        try:
+            await imap._client_task
+            await imap.wait_hello_from_server()
+            await imap.login(self.email_server.user_name, self.email_server.password.get_secret_value())
+            await _send_imap_id(imap)
+            yield imap
+        finally:
+            try:
+                await imap.logout()
+            except Exception as e:
+                logger.info(f"Error during logout: {e}")
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_email_ops.py
git commit -F - <<'EOF'
feat: add email ops skeleton

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 11: Implement native `EmailOps.move_emails()`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_email_ops.py` and append native-move coverage.

- [ ] Step 1. Add the failing native MOVE success test using the verified `aioimaplib 2.0.1` call shape from Q1.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_email_ops.py
@@
 class TestEmailOpsSkeleton:
@@
         mock_send_id.assert_awaited_once_with(mock_imap)
         mock_imap.logout.assert_awaited_once()
+
+
+class TestEmailOpsMove:
+    @pytest.mark.asyncio
+    async def test_move_emails_native_success_uses_uid_move(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        mailbox_ops._delimiter = "."
+        email_ops = EmailOps(email_server, mailbox_ops)
+        mock_imap = AsyncMock()
+        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
+        mock_imap.uid = AsyncMock(return_value=("OK", [b"move completed"]))
+
+        with patch.object(email_ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            moved = await email_ops.move_emails(["101", "102"], "INBOX", "INBOX/Archive")
+
+        assert [item.message_id for item in moved] == ["101", "102"]
+        assert all(item.success is True for item in moved)
+        assert all(item.method == "native" for item in moved)
+        mock_imap.select.assert_awaited_once_with('"INBOX"')
+        mock_imap.uid.assert_awaited_once_with("move", "101,102", '"INBOX.Archive"')
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `move_emails()` does not exist yet.
```bash
uv run pytest tests/test_email_ops.py -q
```
Expected: `AttributeError` for `move_emails`.

- [ ] Step 3. Implement the native MOVE path. This uses the verified pinned-library API: `await imap.uid("move", uid_set, quoted_destination)`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 from mcp_email_server.config import EmailServer
 from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
-from mcp_email_server.emails.models import MailboxInfo, MailboxStatusResponse
+from mcp_email_server.emails.models import MailboxInfo, MailboxStatusResponse, MovedEmail
@@
 class EmailOps:
@@
             try:
                 await imap.logout()
             except Exception as e:
                 logger.info(f"Error during logout: {e}")
+
+    async def move_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list[MovedEmail]:
+        async with self._login_logout() as imap:
+            await self.mailbox_ops.ensure_delimiter(imap)
+            await imap.select(_quote_mailbox(self.mailbox_ops.to_imap_path(source_mailbox)))
+            uid_set = ",".join(email_ids)
+            destination = _quote_mailbox(self.mailbox_ops.to_imap_path(destination_mailbox))
+            result, lines = await imap.uid("move", uid_set, destination)
+            if result != "OK":
+                raise RuntimeError(f"UID MOVE failed with IMAP result {result}: {lines!r}")
+            return [
+                MovedEmail(message_id=email_id, success=True, error=None, method="native")
+                for email_id in email_ids
+            ]
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_email_ops.py
git commit -F - <<'EOF'
feat: add native email move support

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 12: Implement the COPY+STORE+EXPUNGE fallback in `EmailOps.move_emails()`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_email_ops.py` and append fallback/partial-success coverage.

- [ ] Step 1. Add failing BAD/NO fallback and partial-success tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_email_ops.py
@@
 class TestEmailOpsMove:
@@
         assert all(item.method == "native" for item in moved)
         mock_imap.select.assert_awaited_once_with('"INBOX"')
         mock_imap.uid.assert_awaited_once_with("move", "101,102", '"INBOX.Archive"')
+
+    @pytest.mark.asyncio
+    async def test_move_emails_falls_back_on_bad_result(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        mailbox_ops._delimiter = "."
+        email_ops = EmailOps(email_server, mailbox_ops)
+        mock_imap = AsyncMock()
+        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
+        mock_imap.uid = AsyncMock(
+            side_effect=[
+                ("BAD", [b"uid move unsupported"]),
+                ("OK", [b"copy 101"]),
+                ("OK", [b"store 101"]),
+                ("OK", [b"copy 102"]),
+                ("OK", [b"store 102"]),
+            ]
+        )
+        mock_imap.expunge = AsyncMock(return_value=("OK", [b"expunge completed"]))
+
+        with patch.object(email_ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            moved = await email_ops.move_emails(["101", "102"], "INBOX", "INBOX/Archive")
+
+        assert [item.method for item in moved] == ["fallback", "fallback"]
+        assert all(item.success is True for item in moved)
+        assert mock_imap.uid.await_args_list[1].args == ("copy", "101", '"INBOX.Archive"')
+        assert mock_imap.uid.await_args_list[2].args == ("store", "101", "+FLAGS", r"(\Deleted)")
+        mock_imap.expunge.assert_awaited_once()
+
+    @pytest.mark.asyncio
+    async def test_move_emails_marks_tentative_successes_failed_when_expunge_fails(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        mailbox_ops._delimiter = "."
+        email_ops = EmailOps(email_server, mailbox_ops)
+        mock_imap = AsyncMock()
+        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
+        mock_imap.uid = AsyncMock(
+            side_effect=[
+                ("NO", [b"move rejected"]),
+                ("OK", [b"copy 101"]),
+                ("OK", [b"store 101"]),
+            ]
+        )
+        mock_imap.expunge = AsyncMock(return_value=("NO", [b"expunge rejected"]))
+
+        with patch.object(email_ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            moved = await email_ops.move_emails(["101"], "INBOX", "INBOX/Archive")
+
+        assert moved[0].success is False
+        assert moved[0].method == "fallback"
+        assert moved[0].error == "COPY succeeded and source was flagged \\Deleted, but EXPUNGE failed; no rollback performed."
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the native-only implementation does not fallback yet.
```bash
uv run pytest tests/test_email_ops.py -q
```
Expected: runtime failure on `BAD`/`NO` native MOVE results.

- [ ] Step 3. Replace the native-only failure path with the verified best-effort fallback: per-UID `UID COPY`, then `UID STORE +FLAGS (\Deleted)`, then one `EXPUNGE`, with visible partial-success results and the exact EXPUNGE-failure message required by the spec.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
 class EmailOps:
@@
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
-            if result != "OK":
-                raise RuntimeError(f"UID MOVE failed with IMAP result {result}: {lines!r}")
-            return [
-                MovedEmail(message_id=email_id, success=True, error=None, method="native")
-                for email_id in email_ids
-            ]
+            if result == "OK":
+                return [
+                    MovedEmail(message_id=email_id, success=True, error=None, method="native")
+                    for email_id in email_ids
+                ]
+            if result not in {"BAD", "NO"}:
+                raise RuntimeError(f"UID MOVE failed with IMAP result {result}: {lines!r}")
+
+            results: list[MovedEmail] = []
+            flagged_for_expunge: list[str] = []
+            for email_id in email_ids:
+                try:
+                    copy_result, copy_lines = await imap.uid("copy", email_id, destination)
+                    if copy_result != "OK":
+                        results.append(
+                            MovedEmail(
+                                message_id=email_id,
+                                success=False,
+                                error=f"UID COPY failed with IMAP result {copy_result}: {copy_lines!r}",
+                                method="fallback",
+                            )
+                        )
+                        continue
+
+                    store_result, store_lines = await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
+                    if store_result != "OK":
+                        results.append(
+                            MovedEmail(
+                                message_id=email_id,
+                                success=False,
+                                error=f"UID STORE +FLAGS (\\Deleted) failed with IMAP result {store_result}: {store_lines!r}",
+                                method="fallback",
+                            )
+                        )
+                        continue
+
+                    flagged_for_expunge.append(email_id)
+                    results.append(MovedEmail(message_id=email_id, success=True, error=None, method="fallback"))
+                except Exception as e:
+                    results.append(
+                        MovedEmail(
+                            message_id=email_id,
+                            success=False,
+                            error=str(e),
+                            method="fallback",
+                        )
+                    )
+
+            if flagged_for_expunge:
+                expunge_result, expunge_lines = await imap.expunge()
+                if expunge_result != "OK":
+                    expunge_error = "COPY succeeded and source was flagged \\Deleted, but EXPUNGE failed; no rollback performed."
+                    adjusted_results: list[MovedEmail] = []
+                    for item in results:
+                        if item.message_id in flagged_for_expunge and item.success:
+                            adjusted_results.append(
+                                MovedEmail(
+                                    message_id=item.message_id,
+                                    success=False,
+                                    error=expunge_error,
+                                    method="fallback",
+                                )
+                            )
+                        else:
+                            adjusted_results.append(item)
+                    return adjusted_results
+
+            return results
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_email_ops.py
git commit -F - <<'EOF'
feat: add email move fallback path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 13: Implement `EmailOps.copy_emails()`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_email_ops.py` and append copy coverage.

- [ ] Step 1. Add failing per-UID copy tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_email_ops.py
@@
 from mcp_email_server.config import EmailServer
 from mcp_email_server.emails.mailbox import EmailOps, MailboxOps
+from mcp_email_server.emails.models import CopiedEmail
@@
 class TestEmailOpsMove:
@@
         assert moved[0].success is False
         assert moved[0].method == "fallback"
         assert moved[0].error == "COPY succeeded and source was flagged \\Deleted, but EXPUNGE failed; no rollback performed."
+
+
+class TestEmailOpsCopy:
+    @pytest.mark.asyncio
+    async def test_copy_emails_continues_on_per_uid_failures(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        mailbox_ops._delimiter = "."
+        email_ops = EmailOps(email_server, mailbox_ops)
+        mock_imap = AsyncMock()
+        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
+        mock_imap.uid = AsyncMock(
+            side_effect=[
+                ("OK", [b"copy 101"]),
+                ("NO", [b"copy 102 failed"]),
+            ]
+        )
+
+        with patch.object(email_ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            copied = await email_ops.copy_emails(["101", "102"], "INBOX", "INBOX/Archive")
+
+        assert copied == [
+            CopiedEmail(message_id="101", success=True, error=None),
+            CopiedEmail(message_id="102", success=False, error="UID COPY failed with IMAP result NO: [b'copy 102 failed']"),
+        ]
+        mock_imap.expunge.assert_not_called()
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `copy_emails()` is missing.
```bash
uv run pytest tests/test_email_ops.py -q
```
Expected: `AttributeError` for `copy_emails`.

- [ ] Step 3. Implement `copy_emails()`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
-from mcp_email_server.emails.models import MailboxInfo, MailboxStatusResponse, MovedEmail
+from mcp_email_server.emails.models import CopiedEmail, MailboxInfo, MailboxStatusResponse, MovedEmail
@@
 class EmailOps:
@@
             if flagged_for_expunge:
                 expunge_result, expunge_lines = await imap.expunge()
                 if expunge_result != "OK":
@@
                     return adjusted_results
 
             return results
+
+    async def copy_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list[CopiedEmail]:
+        async with self._login_logout() as imap:
+            await self.mailbox_ops.ensure_delimiter(imap)
+            await imap.select(_quote_mailbox(self.mailbox_ops.to_imap_path(source_mailbox)))
+            destination = _quote_mailbox(self.mailbox_ops.to_imap_path(destination_mailbox))
+            results: list[CopiedEmail] = []
+            for email_id in email_ids:
+                try:
+                    result, lines = await imap.uid("copy", email_id, destination)
+                    if result == "OK":
+                        results.append(CopiedEmail(message_id=email_id, success=True, error=None))
+                    else:
+                        results.append(
+                            CopiedEmail(
+                                message_id=email_id,
+                                success=False,
+                                error=f"UID COPY failed with IMAP result {result}: {lines!r}",
+                            )
+                        )
+                except Exception as e:
+                    results.append(CopiedEmail(message_id=email_id, success=False, error=str(e)))
+            return results
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_email_ops.py
git commit -F - <<'EOF'
feat: add email copy support

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 14: Implement `EmailOps.mark_emails(seen, flagged, answered)`

**Files:**
- Modify `mcp_email_server/emails/mailbox.py`.
- Modify `tests/test_email_ops.py` and append mark coverage.

- [ ] Step 1. Add failing mark tests, including the all-None validation error.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_email_ops.py
@@
 from mcp_email_server.emails.mailbox import EmailOps, MailboxOps
-from mcp_email_server.emails.models import CopiedEmail
+from mcp_email_server.emails.models import CopiedEmail, MarkedEmail
@@
 class TestEmailOpsCopy:
@@
         assert copied == [
             CopiedEmail(message_id="101", success=True, error=None),
             CopiedEmail(message_id="102", success=False, error="UID COPY failed with IMAP result NO: [b'copy 102 failed']"),
         ]
         mock_imap.expunge.assert_not_called()
+
+
+class TestEmailOpsMark:
+    @pytest.mark.asyncio
+    async def test_mark_emails_rejects_all_none(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        email_ops = EmailOps(email_server, mailbox_ops)
+        with pytest.raises(ValueError, match="At least one of seen, flagged, or answered must be set"):
+            await email_ops.mark_emails(["101"], mailbox="INBOX")
+
+    @pytest.mark.asyncio
+    async def test_mark_emails_sends_add_and_remove_store_commands(self, email_server):
+        mailbox_ops = MailboxOps(email_server)
+        mailbox_ops._delimiter = "."
+        email_ops = EmailOps(email_server, mailbox_ops)
+        mock_imap = AsyncMock()
+        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
+        mock_imap.uid = AsyncMock(
+            side_effect=[
+                ("OK", [b"add flags"]),
+                ("OK", [b"remove flags"]),
+            ]
+        )
+
+        with patch.object(email_ops, "_login_logout") as mock_login_logout:
+            mock_login_logout.return_value.__aenter__.return_value = mock_imap
+            mock_login_logout.return_value.__aexit__.return_value = None
+            marked = await email_ops.mark_emails(["101"], mailbox="INBOX", seen=True, flagged=False, answered=True)
+
+        assert marked == [MarkedEmail(message_id="101", success=True, error=None)]
+        assert mock_imap.uid.await_args_list[0].args == ("store", "101", "+FLAGS", r"(\Seen \Answered)")
+        assert mock_imap.uid.await_args_list[1].args == ("store", "101", "-FLAGS", r"(\Flagged)")
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `mark_emails()` is missing.
```bash
uv run pytest tests/test_email_ops.py -q
```
Expected: `AttributeError` for `mark_emails`.

- [ ] Step 3. Implement `mark_emails()` with explicit add/remove flag groups and the required all-None validation.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/mailbox.py
@@
-from mcp_email_server.emails.models import CopiedEmail, MailboxInfo, MailboxStatusResponse, MovedEmail
+from mcp_email_server.emails.models import CopiedEmail, MailboxInfo, MailboxStatusResponse, MarkedEmail, MovedEmail
@@
 class EmailOps:
@@
     async def copy_emails(
         self,
         email_ids: list[str],
         source_mailbox: str,
         destination_mailbox: str,
     ) -> list[CopiedEmail]:
@@
                 except Exception as e:
                     results.append(CopiedEmail(message_id=email_id, success=False, error=str(e)))
             return results
+
+    async def mark_emails(
+        self,
+        email_ids: list[str],
+        mailbox: str = "INBOX",
+        seen: bool | None = None,
+        flagged: bool | None = None,
+        answered: bool | None = None,
+    ) -> list[MarkedEmail]:
+        add_flags: list[str] = []
+        remove_flags: list[str] = []
+
+        flag_updates = [
+            (seen, r"\Seen"),
+            (flagged, r"\Flagged"),
+            (answered, r"\Answered"),
+        ]
+        for value, flag in flag_updates:
+            if value is True:
+                add_flags.append(flag)
+            elif value is False:
+                remove_flags.append(flag)
+
+        if not add_flags and not remove_flags:
+            raise ValueError("At least one of seen, flagged, or answered must be set")
+
+        async with self._login_logout() as imap:
+            await self.mailbox_ops.ensure_delimiter(imap)
+            await imap.select(_quote_mailbox(self.mailbox_ops.to_imap_path(mailbox)))
+            results: list[MarkedEmail] = []
+            for email_id in email_ids:
+                try:
+                    if add_flags:
+                        add_result, add_lines = await imap.uid(
+                            "store",
+                            email_id,
+                            "+FLAGS",
+                            f"({' '.join(add_flags)})",
+                        )
+                        if add_result != "OK":
+                            raise RuntimeError(f"UID STORE +FLAGS failed with IMAP result {add_result}: {add_lines!r}")
+                    if remove_flags:
+                        remove_result, remove_lines = await imap.uid(
+                            "store",
+                            email_id,
+                            "-FLAGS",
+                            f"({' '.join(remove_flags)})",
+                        )
+                        if remove_result != "OK":
+                            raise RuntimeError(f"UID STORE -FLAGS failed with IMAP result {remove_result}: {remove_lines!r}")
+                    results.append(MarkedEmail(message_id=email_id, success=True, error=None))
+                except Exception as e:
+                    results.append(MarkedEmail(message_id=email_id, success=False, error=str(e)))
+            return results
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_ops.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/mailbox.py tests/test_email_ops.py
git commit -F - <<'EOF'
feat: add email flag updates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 15: Wire `ClassicEmailHandler` to `MailboxOps` and `EmailOps`

**Files:**
- Modify `mcp_email_server/emails/classic.py` constructor at lines 1029-1038, `get_emails_metadata()` at lines 1040-1090 later, and replace the temporary stubs added in Task 2.
- Modify `tests/test_classic_handler.py` imports at lines 6-14 and append delegate tests after line 306.

- [ ] Step 1. Add failing Classic handler delegate tests.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_classic_handler.py
@@
 from mcp_email_server.emails.classic import ClassicEmailHandler, EmailClient
+from mcp_email_server.emails.models import CopiedEmail, MailboxInfo, MailboxStatusResponse, MarkedEmail, MovedEmail
@@
     async def test_download_attachment(self, classic_handler, tmp_path):
@@
             mock_download.assert_called_once_with("123", "document.pdf", save_path, "INBOX")
+
+    @pytest.mark.asyncio
+    async def test_list_mailboxes_delegates_to_mailbox_ops(self, classic_handler):
+        mock_list = AsyncMock(return_value=[MailboxInfo(path="INBOX/Archive", delimiter=".", flags=[], subscribed=False)])
+        with patch.object(classic_handler.mailbox_ops, "list_mailboxes", mock_list):
+            result = await classic_handler.list_mailboxes(pattern="INBOX/*")
+        assert result[0].path == "INBOX/Archive"
+        mock_list.assert_awaited_once_with("INBOX/*", False)
+
+    @pytest.mark.asyncio
+    async def test_get_mailbox_status_delegates_to_mailbox_ops(self, classic_handler):
+        mock_status = AsyncMock(
+            return_value=MailboxStatusResponse(
+                path="INBOX",
+                messages=10,
+                recent=1,
+                unseen=2,
+                uid_next=11,
+                uid_validity=99,
+                flags=[r"\Seen"],
+                permanent_flags=[r"\Seen", r"\*"],
+            )
+        )
+        with patch.object(classic_handler.mailbox_ops, "get_mailbox_status", mock_status):
+            result = await classic_handler.get_mailbox_status("INBOX")
+        assert result.messages == 10
+        mock_status.assert_awaited_once_with("INBOX")
+
+    @pytest.mark.asyncio
+    async def test_move_copy_and_mark_delegate_to_email_ops(self, classic_handler):
+        mock_move = AsyncMock(return_value=[MovedEmail(message_id="1", success=True, error=None, method="native")])
+        mock_copy = AsyncMock(return_value=[CopiedEmail(message_id="1", success=True, error=None)])
+        mock_mark = AsyncMock(return_value=[MarkedEmail(message_id="1", success=True, error=None)])
+
+        with patch.object(classic_handler.email_ops, "move_emails", mock_move):
+            moved = await classic_handler.move_emails(["1"], "INBOX", "Archive")
+        with patch.object(classic_handler.email_ops, "copy_emails", mock_copy):
+            copied = await classic_handler.copy_emails(["1"], "INBOX", "Archive")
+        with patch.object(classic_handler.email_ops, "mark_emails", mock_mark):
+            marked = await classic_handler.mark_emails(["1"], mailbox="INBOX", seen=True)
+
+        assert moved[0].method == "native"
+        assert copied[0].success is True
+        assert marked[0].success is True
+        mock_move.assert_awaited_once_with(["1"], "INBOX", "Archive")
+        mock_copy.assert_awaited_once_with(["1"], "INBOX", "Archive")
+        mock_mark.assert_awaited_once_with(["1"], mailbox="INBOX", seen=True, flagged=None, answered=None)
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because `ClassicEmailHandler` does not have `mailbox_ops`/`email_ops` yet and still raises stubs.
```bash
uv run pytest tests/test_classic_handler.py -q
```
Expected: `AttributeError` on `mailbox_ops`/`email_ops` or `NotImplementedError` from the stub methods.

- [ ] Step 3. Instantiate `MailboxOps` and `EmailOps` in the constructor and replace the temporary stubs with direct delegates.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/classic.py
@@
 from mcp_email_server.config import EmailServer, EmailSettings
 from mcp_email_server.emails import EmailHandler
 from mcp_email_server.emails._helpers import _create_ssl_context, _quote_mailbox, _send_imap_id
+from mcp_email_server.emails.mailbox import EmailOps, MailboxOps
 from mcp_email_server.emails.models import (
@@
 class ClassicEmailHandler(EmailHandler):
     def __init__(self, email_settings: EmailSettings):
         self.email_settings = email_settings
         self.incoming_client = EmailClient(email_settings.incoming)
         self.outgoing_client = EmailClient(
             email_settings.outgoing,
             sender=f"{email_settings.full_name} <{email_settings.email_address}>",
         )
+        self.mailbox_ops = MailboxOps(email_settings.incoming)
+        self.email_ops = EmailOps(email_settings.incoming, self.mailbox_ops)
         self.save_to_sent = email_settings.save_to_sent
         self.sent_folder_name = email_settings.sent_folder_name
@@
-    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list[MailboxInfo]:
-        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list[MailboxInfo]:
+        return await self.mailbox_ops.list_mailboxes(pattern, subscribed_only)
@@
-    async def create_mailbox(self, mailbox: str) -> str:
-        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+    async def create_mailbox(self, mailbox: str) -> str:
+        return await self.mailbox_ops.create_mailbox(mailbox)
@@
-    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
-        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
+        return await self.mailbox_ops.rename_mailbox(old_mailbox, new_mailbox)
@@
-    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
-        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
+        return await self.mailbox_ops.delete_mailbox(mailbox, confirm)
@@
-    async def get_mailbox_status(self, mailbox: str = "INBOX") -> MailboxStatusResponse:
-        raise NotImplementedError("Task 15 wires MailboxOps into ClassicEmailHandler")
+    async def get_mailbox_status(self, mailbox: str = "INBOX") -> MailboxStatusResponse:
+        return await self.mailbox_ops.get_mailbox_status(mailbox)
@@
-    async def move_emails(
-        self,
-        email_ids: list[str],
-        source_mailbox: str,
-        destination_mailbox: str,
-    ) -> list[MovedEmail]:
-        raise NotImplementedError("Task 15 wires EmailOps into ClassicEmailHandler")
+    async def move_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list[MovedEmail]:
+        return await self.email_ops.move_emails(email_ids, source_mailbox, destination_mailbox)
@@
-    async def copy_emails(
-        self,
-        email_ids: list[str],
-        source_mailbox: str,
-        destination_mailbox: str,
-    ) -> list[CopiedEmail]:
-        raise NotImplementedError("Task 15 wires EmailOps into ClassicEmailHandler")
+    async def copy_emails(
+        self,
+        email_ids: list[str],
+        source_mailbox: str,
+        destination_mailbox: str,
+    ) -> list[CopiedEmail]:
+        return await self.email_ops.copy_emails(email_ids, source_mailbox, destination_mailbox)
@@
-    async def mark_emails(
-        self,
-        email_ids: list[str],
-        mailbox: str = "INBOX",
-        seen: bool | None = None,
-        flagged: bool | None = None,
-        answered: bool | None = None,
-    ) -> list[MarkedEmail]:
-        raise NotImplementedError("Task 15 wires EmailOps into ClassicEmailHandler")
+    async def mark_emails(
+        self,
+        email_ids: list[str],
+        mailbox: str = "INBOX",
+        seen: bool | None = None,
+        flagged: bool | None = None,
+        answered: bool | None = None,
+    ) -> list[MarkedEmail]:
+        return await self.email_ops.mark_emails(
+            email_ids,
+            mailbox=mailbox,
+            seen=seen,
+            flagged=flagged,
+            answered=answered,
+        )
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_classic_handler.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/classic.py tests/test_classic_handler.py
git commit -F - <<'EOF'
feat: wire classic handler mailbox delegates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 16: Extend `EmailClient` metadata search threading for `body_contains` and `text`

**Files:**
- Modify `mcp_email_server/emails/classic.py` at `_build_search_criteria()` lines 284-322 only if needed, plus `get_email_count()` lines 458-499 and `get_emails_metadata_stream()` lines 501-585.
- Modify `tests/test_email_client.py` and append metadata-search forwarding tests after line 280.

**Note:** `_build_search_criteria()` already supports `body` and `text` at `classic.py:303-306`, so this task is strictly about threading new public parameters through `get_email_count()` and `get_emails_metadata_stream()`. No pytest config changes are needed because `pytest.ini` already sets `asyncio_mode = auto`.

- [ ] Step 1. Add failing tests proving `body_contains` and `text` reach `uid_search()`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_email_client.py
@@
     async def test_get_emails_stream(self, email_client):
@@
                     mock_fetch_dates.assert_called_once_with(mock_imap, [b"1", b"2", b"3"])
                     # Headers fetched for page UIDs in sorted order (desc by date)
                     mock_fetch_headers.assert_called_once_with(mock_imap, ["3", "2", "1"])
+
+    @pytest.mark.asyncio
+    async def test_get_emails_stream_passes_body_contains_and_text_to_uid_search(self, email_client):
+        mock_imap = AsyncMock()
+        mock_imap._client_task = asyncio.Future()
+        mock_imap._client_task.set_result(None)
+        mock_imap.wait_hello_from_server = AsyncMock()
+        mock_imap.login = AsyncMock()
+        mock_imap.select = AsyncMock()
+        mock_imap.uid_search = AsyncMock(return_value=(None, [b"1"]))
+        mock_imap.logout = AsyncMock()
+
+        with patch.object(email_client, "imap_class", return_value=mock_imap):
+            with patch.object(email_client, "_batch_fetch_dates", return_value={"1": datetime(2024, 1, 1, tzinfo=timezone.utc)}):
+                with patch.object(
+                    email_client,
+                    "_batch_fetch_headers",
+                    return_value={"1": {"email_id": "1", "subject": "Subject", "from": "a@test.com", "to": [], "date": datetime(2024, 1, 1, tzinfo=timezone.utc), "attachments": []}},
+                ):
+                    emails = []
+                    async for email_data in email_client.get_emails_metadata_stream(
+                        body_contains="invoice",
+                        text="follow up",
+                    ):
+                        emails.append(email_data)
+
+        assert len(emails) == 1
+        mock_imap.uid_search.assert_called_once_with("BODY", "invoice", "TEXT", '"follow up"')
+
+    @pytest.mark.asyncio
+    async def test_get_email_count_passes_body_contains_and_text_to_uid_search(self, email_client):
+        mock_imap = AsyncMock()
+        mock_imap._client_task = asyncio.Future()
+        mock_imap._client_task.set_result(None)
+        mock_imap.wait_hello_from_server = AsyncMock()
+        mock_imap.login = AsyncMock()
+        mock_imap.select = AsyncMock()
+        mock_imap.uid_search = AsyncMock(return_value=(None, [b"1 2"]))
+        mock_imap.logout = AsyncMock()
+
+        with patch.object(email_client, "imap_class", return_value=mock_imap):
+            count = await email_client.get_email_count(body_contains="invoice", text="follow up")
+
+        assert count == 2
+        mock_imap.uid_search.assert_called_once_with("BODY", "invoice", "TEXT", '"follow up"')
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the public methods do not accept `body_contains` yet.
```bash
uv run pytest tests/test_email_client.py -q
```
Expected: `TypeError` for unexpected keyword argument `body_contains`.

- [ ] Step 3. Thread `body_contains` and `text` through `get_email_count()` and `get_emails_metadata_stream()` to the existing `_build_search_criteria(body=..., text=...)`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/classic.py
@@
     async def get_email_count(
         self,
         before: datetime | None = None,
         since: datetime | None = None,
         subject: str | None = None,
+        body_contains: str | None = None,
+        text: str | None = None,
         from_address: str | None = None,
         to_address: str | None = None,
         mailbox: str = "INBOX",
         seen: bool | None = None,
         flagged: bool | None = None,
@@
             search_criteria = self._build_search_criteria(
                 before,
                 since,
                 subject,
+                body=body_contains,
+                text=text,
                 from_address=from_address,
                 to_address=to_address,
                 seen=seen,
                 flagged=flagged,
                 answered=answered,
@@
     async def get_emails_metadata_stream(
         self,
         page: int = 1,
         page_size: int = 10,
         before: datetime | None = None,
         since: datetime | None = None,
         subject: str | None = None,
+        body_contains: str | None = None,
+        text: str | None = None,
         from_address: str | None = None,
         to_address: str | None = None,
         order: str = "desc",
         mailbox: str = "INBOX",
         seen: bool | None = None,
@@
             search_criteria = self._build_search_criteria(
                 before,
                 since,
                 subject,
+                body=body_contains,
+                text=text,
                 from_address=from_address,
                 to_address=to_address,
                 seen=seen,
                 flagged=flagged,
                 answered=answered,
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_email_client.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/classic.py tests/test_email_client.py
git commit -F - <<'EOF'
feat: thread metadata body and text search filters

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 17: Update `ClassicEmailHandler.get_emails_metadata()` for `body_contains` and `text`

**Files:**
- Modify `mcp_email_server/emails/classic.py` at `ClassicEmailHandler.get_emails_metadata()` lines 1040-1090.
- Modify `tests/test_classic_handler.py` and extend the existing metadata assertions around lines 59-167.

- [ ] Step 1. Add failing handler-forwarding tests for `body_contains` and `text`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_classic_handler.py
@@
     async def test_get_emails_with_mailbox(self, classic_handler):
@@
                 mock_count.assert_called_once_with(
                     None,
                     None,
                     None,
+                    body_contains=None,
+                    text=None,
                     from_address=None,
                     to_address=None,
                     mailbox="Sent",
                     seen=None,
                     flagged=None,
                     answered=None,
                 )
+
+    @pytest.mark.asyncio
+    async def test_get_emails_metadata_forwards_body_contains_and_text(self, classic_handler):
+        now = datetime.now(timezone.utc)
+        mock_stream = AsyncMock()
+        mock_stream.__aiter__.return_value = []
+        mock_count = AsyncMock(return_value=0)
+
+        with patch.object(classic_handler.incoming_client, "get_emails_metadata_stream", return_value=mock_stream):
+            with patch.object(classic_handler.incoming_client, "get_email_count", mock_count):
+                await classic_handler.get_emails_metadata(
+                    body_contains="invoice",
+                    text="follow up",
+                )
+
+        classic_handler.incoming_client.get_emails_metadata_stream.assert_called_once_with(
+            1,
+            10,
+            None,
+            None,
+            None,
+            "invoice",
+            "follow up",
+            None,
+            None,
+            "desc",
+            "INBOX",
+            None,
+            None,
+            None,
+        )
+        mock_count.assert_called_once_with(
+            None,
+            None,
+            None,
+            body_contains="invoice",
+            text="follow up",
+            from_address=None,
+            to_address=None,
+            mailbox="INBOX",
+            seen=None,
+            flagged=None,
+            answered=None,
+        )
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the handler signature and delegation do not expose the new args yet.
```bash
uv run pytest tests/test_classic_handler.py -q
```
Expected: assertion mismatch or `TypeError` on `body_contains`.

- [ ] Step 3. Update `ClassicEmailHandler.get_emails_metadata()` to accept and forward `body_contains` and `text`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/emails/classic.py
@@
     async def get_emails_metadata(
         self,
         page: int = 1,
         page_size: int = 10,
         before: datetime | None = None,
         since: datetime | None = None,
         subject: str | None = None,
+        body_contains: str | None = None,
+        text: str | None = None,
         from_address: str | None = None,
         to_address: str | None = None,
         order: str = "desc",
         mailbox: str = "INBOX",
         seen: bool | None = None,
@@
         async for email_data in self.incoming_client.get_emails_metadata_stream(
             page,
             page_size,
             before,
             since,
             subject,
+            body_contains,
+            text,
             from_address,
             to_address,
             order,
             mailbox,
             seen,
@@
         total = await self.incoming_client.get_email_count(
             before,
             since,
             subject,
+            body_contains=body_contains,
+            text=text,
             from_address=from_address,
             to_address=to_address,
             mailbox=mailbox,
             seen=seen,
             flagged=flagged,
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_classic_handler.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/emails/classic.py tests/test_classic_handler.py
git commit -F - <<'EOF'
feat: expose metadata search filters in classic handler

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 18: Add the 8 new MCP tool definitions in `app.py`

**Files:**
- Modify `mcp_email_server/app.py` imports at lines 1-18 and append new tool functions after line 221.
- Modify `tests/test_mcp_tools.py` imports at lines 6-22 and append new tool tests after the existing suite.

- [ ] Step 1. Add failing MCP tool tests for the 8 new mailbox-management operations.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mcp_tools.py
@@
 from mcp_email_server.app import (
     add_email_account,
+    copy_emails,
+    create_mailbox,
     delete_emails,
+    delete_mailbox,
     download_attachment,
+    get_mailbox_status,
     get_emails_content,
     list_available_accounts,
+    list_mailboxes,
     list_emails_metadata,
+    mark_emails,
+    move_emails,
+    rename_mailbox,
     send_email,
 )
@@
 from mcp_email_server.emails.models import (
     AttachmentDownloadResponse,
+    CopiedEmail,
     EmailBodyResponse,
     EmailContentBatchResponse,
     EmailMetadata,
     EmailMetadataPageResponse,
+    MailboxInfo,
+    MailboxStatusResponse,
+    MarkedEmail,
+    MovedEmail,
 )
@@
 class TestMcpTools:
@@
     async def test_delete_emails_with_mailbox(self):
         """Test delete_emails MCP tool with custom mailbox."""
@@
             mock_handler.delete_emails.assert_called_once_with(["12345"], "Trash")
+
+    @pytest.mark.asyncio
+    async def test_list_mailboxes(self):
+        mock_handler = AsyncMock()
+        mock_handler.list_mailboxes.return_value = [
+            MailboxInfo(path="INBOX/Archive", delimiter=".", flags=[r"\HasNoChildren"], subscribed=False)
+        ]
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await list_mailboxes(account_name="test_account", pattern="INBOX/*")
+        assert result[0].path == "INBOX/Archive"
+        mock_handler.list_mailboxes.assert_called_once_with("INBOX/*", False)
+
+    @pytest.mark.asyncio
+    async def test_create_mailbox(self):
+        mock_handler = AsyncMock()
+        mock_handler.create_mailbox.return_value = "Successfully created mailbox 'INBOX/Archive'"
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await create_mailbox(account_name="test_account", mailbox="INBOX/Archive")
+        assert result == "Successfully created mailbox 'INBOX/Archive'"
+        mock_handler.create_mailbox.assert_called_once_with("INBOX/Archive")
+
+    @pytest.mark.asyncio
+    async def test_rename_mailbox(self):
+        mock_handler = AsyncMock()
+        mock_handler.rename_mailbox.return_value = "Successfully renamed mailbox 'INBOX/Old' to 'INBOX/New'"
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await rename_mailbox(account_name="test_account", old_mailbox="INBOX/Old", new_mailbox="INBOX/New")
+        assert "INBOX/New" in result
+        mock_handler.rename_mailbox.assert_called_once_with("INBOX/Old", "INBOX/New")
+
+    @pytest.mark.asyncio
+    async def test_delete_mailbox_requires_confirm(self):
+        with pytest.raises(ValueError, match="confirm=True"):
+            await delete_mailbox(account_name="test_account", mailbox="INBOX/Archive")
+
+    @pytest.mark.asyncio
+    async def test_delete_mailbox_confirmed(self):
+        mock_handler = AsyncMock()
+        mock_handler.delete_mailbox.return_value = "Successfully deleted mailbox 'INBOX/Archive'"
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await delete_mailbox(account_name="test_account", mailbox="INBOX/Archive", confirm=True)
+        assert result == "Successfully deleted mailbox 'INBOX/Archive'"
+        mock_handler.delete_mailbox.assert_called_once_with("INBOX/Archive", True)
+
+    @pytest.mark.asyncio
+    async def test_get_mailbox_status(self):
+        mock_handler = AsyncMock()
+        mock_handler.get_mailbox_status.return_value = MailboxStatusResponse(
+            path="INBOX",
+            messages=5,
+            recent=1,
+            unseen=2,
+            uid_next=6,
+            uid_validity=9,
+            flags=[r"\Seen"],
+            permanent_flags=[r"\Seen", r"\*"],
+        )
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await get_mailbox_status(account_name="test_account", mailbox="INBOX")
+        assert result.messages == 5
+        mock_handler.get_mailbox_status.assert_called_once_with("INBOX")
+
+    @pytest.mark.asyncio
+    async def test_move_emails(self):
+        mock_handler = AsyncMock()
+        mock_handler.move_emails.return_value = [MovedEmail(message_id="1", success=True, error=None, method="native")]
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await move_emails(
+                account_name="test_account",
+                email_ids=["1"],
+                source_mailbox="INBOX",
+                destination_mailbox="Archive",
+            )
+        assert result[0].method == "native"
+        mock_handler.move_emails.assert_called_once_with(["1"], "INBOX", "Archive")
+
+    @pytest.mark.asyncio
+    async def test_copy_emails(self):
+        mock_handler = AsyncMock()
+        mock_handler.copy_emails.return_value = [CopiedEmail(message_id="1", success=True, error=None)]
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await copy_emails(
+                account_name="test_account",
+                email_ids=["1"],
+                source_mailbox="INBOX",
+                destination_mailbox="Archive",
+            )
+        assert result[0].success is True
+        mock_handler.copy_emails.assert_called_once_with(["1"], "INBOX", "Archive")
+
+    @pytest.mark.asyncio
+    async def test_mark_emails(self):
+        mock_handler = AsyncMock()
+        mock_handler.mark_emails.return_value = [MarkedEmail(message_id="1", success=True, error=None)]
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await mark_emails(account_name="test_account", email_ids=["1"], mailbox="INBOX", seen=True)
+        assert result[0].success is True
+        mock_handler.mark_emails.assert_called_once_with(["1"], mailbox="INBOX", seen=True, flagged=None, answered=None)
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the tools do not exist yet.
```bash
uv run pytest tests/test_mcp_tools.py -q
```
Expected: import failures for the new tool functions.

- [ ] Step 3. Add the 8 new MCP tools to `app.py`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/app.py
@@
 from mcp_email_server.emails.models import (
     AttachmentDownloadResponse,
+    CopiedEmail,
     EmailContentBatchResponse,
     EmailMetadataPageResponse,
+    MailboxInfo,
+    MailboxStatusResponse,
+    MarkedEmail,
+    MovedEmail,
 )
@@
 async def download_attachment(
@@
 
     handler = dispatch_handler(account_name)
     return await handler.download_attachment(email_id, attachment_name, save_path, mailbox)
+
+
+@mcp.tool(description="List available mailboxes for an account.")
+async def list_mailboxes(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    pattern: Annotated[str, Field(default="*", description="Mailbox pattern in canonical user syntax.")] = "*",
+    subscribed_only: Annotated[bool, Field(default=False, description="If True, use IMAP LSUB.")] = False,
+) -> list[MailboxInfo]:
+    handler = dispatch_handler(account_name)
+    return await handler.list_mailboxes(pattern, subscribed_only)
+
+
+@mcp.tool(description="Create a mailbox on the IMAP server.")
+async def create_mailbox(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    mailbox: Annotated[str, Field(description="Mailbox path in canonical user syntax.")],
+) -> str:
+    handler = dispatch_handler(account_name)
+    return await handler.create_mailbox(mailbox)
+
+
+@mcp.tool(description="Rename a mailbox on the IMAP server.")
+async def rename_mailbox(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    old_mailbox: Annotated[str, Field(description="Existing mailbox path in canonical user syntax.")],
+    new_mailbox: Annotated[str, Field(description="New mailbox path in canonical user syntax.")],
+) -> str:
+    handler = dispatch_handler(account_name)
+    return await handler.rename_mailbox(old_mailbox, new_mailbox)
+
+
+@mcp.tool(description="Delete a mailbox from the IMAP server. Requires confirm=True.")
+async def delete_mailbox(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    mailbox: Annotated[str, Field(description="Mailbox path in canonical user syntax.")],
+    confirm: Annotated[bool, Field(default=False, description="Must be True to delete the mailbox.")] = False,
+) -> str:
+    if not confirm:
+        raise ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")
+    handler = dispatch_handler(account_name)
+    return await handler.delete_mailbox(mailbox, confirm)
+
+
+@mcp.tool(description="Get message counts and flags for a mailbox.")
+async def get_mailbox_status(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    mailbox: Annotated[str, Field(default="INBOX", description="Mailbox path in canonical user syntax.")] = "INBOX",
+) -> MailboxStatusResponse:
+    handler = dispatch_handler(account_name)
+    return await handler.get_mailbox_status(mailbox)
+
+
+@mcp.tool(description="Move emails to another mailbox and return per-message results.")
+async def move_emails(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    email_ids: Annotated[list[str], Field(description="Email UIDs to move.")],
+    source_mailbox: Annotated[str, Field(description="Source mailbox in canonical user syntax.")],
+    destination_mailbox: Annotated[str, Field(description="Destination mailbox in canonical user syntax.")],
+) -> list[MovedEmail]:
+    handler = dispatch_handler(account_name)
+    return await handler.move_emails(email_ids, source_mailbox, destination_mailbox)
+
+
+@mcp.tool(description="Copy emails to another mailbox and return per-message results.")
+async def copy_emails(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    email_ids: Annotated[list[str], Field(description="Email UIDs to copy.")],
+    source_mailbox: Annotated[str, Field(description="Source mailbox in canonical user syntax.")],
+    destination_mailbox: Annotated[str, Field(description="Destination mailbox in canonical user syntax.")],
+) -> list[CopiedEmail]:
+    handler = dispatch_handler(account_name)
+    return await handler.copy_emails(email_ids, source_mailbox, destination_mailbox)
+
+
+@mcp.tool(description="Set or clear Seen, Flagged, and Answered flags for emails.")
+async def mark_emails(
+    account_name: Annotated[str, Field(description="The name of the email account.")],
+    email_ids: Annotated[list[str], Field(description="Email UIDs to update.")],
+    mailbox: Annotated[str, Field(default="INBOX", description="Mailbox containing the emails.")] = "INBOX",
+    seen: Annotated[bool | None, Field(default=None, description="True=set, False=clear, None=leave unchanged.")] = None,
+    flagged: Annotated[bool | None, Field(default=None, description="True=set, False=clear, None=leave unchanged.")] = None,
+    answered: Annotated[bool | None, Field(default=None, description="True=set, False=clear, None=leave unchanged.")] = None,
+) -> list[MarkedEmail]:
+    handler = dispatch_handler(account_name)
+    return await handler.mark_emails(email_ids, mailbox=mailbox, seen=seen, flagged=flagged, answered=answered)
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mcp_tools.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/app.py tests/test_mcp_tools.py
git commit -F - <<'EOF'
feat: add mailbox management mcp tools

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 19: Add `body_contains` and `text` to the `list_emails_metadata` MCP tool

**Files:**
- Modify `mcp_email_server/app.py` at `list_emails_metadata()` lines 43-100.
- Modify `tests/test_mcp_tools.py` and extend the existing metadata tests around lines 110-223.

- [ ] Step 1. Add failing `list_emails_metadata` MCP tests for the new params.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mcp_tools.py
@@
     async def test_list_emails_metadata_with_mailbox(self):
@@
             mock_handler.get_emails_metadata.assert_called_once_with(
                 page=1,
                 page_size=10,
                 before=None,
                 since=None,
                 subject=None,
+                body_contains=None,
+                text=None,
                 from_address=None,
                 to_address=None,
                 order="desc",
                 mailbox="Sent",
                 seen=None,
                 flagged=None,
                 answered=None,
             )
+
+    @pytest.mark.asyncio
+    async def test_list_emails_metadata_with_body_and_text_filters(self):
+        now = datetime.now(timezone.utc)
+        email_metadata_page = EmailMetadataPageResponse(
+            page=1,
+            page_size=10,
+            before=None,
+            since=None,
+            subject=None,
+            emails=[],
+            total=0,
+        )
+        mock_handler = AsyncMock()
+        mock_handler.get_emails_metadata.return_value = email_metadata_page
+
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            await list_emails_metadata(
+                account_name="test_account",
+                body_contains="invoice",
+                text="follow up",
+            )
+
+        mock_handler.get_emails_metadata.assert_called_once_with(
+            page=1,
+            page_size=10,
+            before=None,
+            since=None,
+            subject=None,
+            body_contains="invoice",
+            text="follow up",
+            from_address=None,
+            to_address=None,
+            order="desc",
+            mailbox="INBOX",
+            seen=None,
+            flagged=None,
+            answered=None,
+        )
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the MCP tool signature does not expose the new args yet.
```bash
uv run pytest tests/test_mcp_tools.py -q
```
Expected: `TypeError` for unexpected keyword argument `body_contains`.

- [ ] Step 3. Extend the MCP tool signature and forward `body_contains` and `text` to `handler.get_emails_metadata(...)`.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: mcp_email_server/app.py
@@
 async def list_emails_metadata(
     account_name: Annotated[str, Field(description="The name of the email account.")],
@@
     since: Annotated[
         datetime | None,
         Field(default=None, description="Retrieve emails since this datetime (UTC)."),
     ] = None,
     subject: Annotated[str | None, Field(default=None, description="Filter emails by subject.")] = None,
+    body_contains: Annotated[
+        str | None,
+        Field(default=None, description="Filter emails by body content using IMAP BODY search."),
+    ] = None,
+    text: Annotated[
+        str | None,
+        Field(default=None, description="Filter emails by full-text content using IMAP TEXT search."),
+    ] = None,
     from_address: Annotated[str | None, Field(default=None, description="Filter emails by sender address.")] = None,
@@
         page_size=page_size,
         before=before,
         since=since,
         subject=subject,
+        body_contains=body_contains,
+        text=text,
         from_address=from_address,
         to_address=to_address,
         order=order,
         mailbox=mailbox,
         seen=seen,
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mcp_tools.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add mcp_email_server/app.py tests/test_mcp_tools.py
git commit -F - <<'EOF'
feat: add metadata full-text mcp params

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 20: Add smoke tests covering the full new MCP tool surface

**Files:**
- Modify `tests/test_mcp_tools.py` and append a parameterized smoke matrix after the direct tool tests.

- [ ] Step 1. Add a failing smoke-matrix test that expects all new mailbox-management tools to be callable through the mocked handler surface.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mcp_tools.py
@@
 class TestMcpTools:
@@
     async def test_mark_emails(self):
         mock_handler = AsyncMock()
         mock_handler.mark_emails.return_value = [MarkedEmail(message_id="1", success=True, error=None)]
         with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
             result = await mark_emails(account_name="test_account", email_ids=["1"], mailbox="INBOX", seen=True)
         assert result[0].success is True
         mock_handler.mark_emails.assert_called_once_with(["1"], mailbox="INBOX", seen=True, flagged=None, answered=None)
+
+    @pytest.mark.asyncio
+    @pytest.mark.parametrize(
+        ("tool", "kwargs", "handler_method", "handler_return"),
+        [
+            (list_mailboxes, {"account_name": "test_account"}, "list_mailboxes", [MailboxInfo(path="INBOX", delimiter=".", flags=[], subscribed=False)]),
+            (create_mailbox, {"account_name": "test_account", "mailbox": "INBOX/Archive"}, "create_mailbox", "Successfully created mailbox 'INBOX/Archive'"),
+            (rename_mailbox, {"account_name": "test_account", "old_mailbox": "INBOX/Old", "new_mailbox": "INBOX/New"}, "rename_mailbox", "Successfully renamed mailbox 'INBOX/Old' to 'INBOX/New'"),
+            (get_mailbox_status, {"account_name": "test_account"}, "get_mailbox_status", MailboxStatusResponse(path="INBOX", messages=1, recent=0, unseen=0, uid_next=2, uid_validity=9, flags=[], permanent_flags=[])),
+            (move_emails, {"account_name": "test_account", "email_ids": ["1"], "source_mailbox": "INBOX", "destination_mailbox": "Archive"}, "move_emails", [MovedEmail(message_id="1", success=True, error=None, method="native")]),
+            (copy_emails, {"account_name": "test_account", "email_ids": ["1"], "source_mailbox": "INBOX", "destination_mailbox": "Archive"}, "copy_emails", [CopiedEmail(message_id="1", success=True, error=None)]),
+            (mark_emails, {"account_name": "test_account", "email_ids": ["1"], "mailbox": "INBOX", "seen": True}, "mark_emails", [MarkedEmail(message_id="1", success=True, error=None)]),
+        ],
+    )
+    async def test_mailbox_management_tool_smoke_matrix(self, tool, kwargs, handler_method, handler_return):
+        mock_handler = AsyncMock()
+        getattr(mock_handler, handler_method).return_value = handler_return
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await tool(**kwargs)
+        assert result == handler_return
*** End Patch
PATCH
```

- [ ] Step 2. Run the targeted tests and expect failure because the new smoke matrix is stricter than the current direct coverage.
```bash
uv run pytest tests/test_mcp_tools.py -q
```
Expected: at least one call-shape mismatch if any tool is not forwarding exactly as expected.

- [ ] Step 3. Implement the minimal smoke-test harness adjustment by normalizing the direct `list_mailboxes()` default-argument call so the parameterized matrix exercises the same shape as the production tool surface.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: tests/test_mcp_tools.py
@@
     async def test_list_mailboxes(self):
         mock_handler = AsyncMock()
         mock_handler.list_mailboxes.return_value = [
             MailboxInfo(path="INBOX/Archive", delimiter=".", flags=[r"\HasNoChildren"], subscribed=False)
         ]
         with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
             result = await list_mailboxes(account_name="test_account", pattern="INBOX/*")
         assert result[0].path == "INBOX/Archive"
         mock_handler.list_mailboxes.assert_called_once_with("INBOX/*", False)
+
+    @pytest.mark.asyncio
+    async def test_list_mailboxes_defaults(self):
+        mock_handler = AsyncMock()
+        mock_handler.list_mailboxes.return_value = []
+        with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
+            result = await list_mailboxes(account_name="test_account")
+        assert result == []
+        mock_handler.list_mailboxes.assert_called_once_with("*", False)
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the targeted tests and expect PASS.
```bash
uv run pytest tests/test_mcp_tools.py -q
```

- [ ] Step 5. Commit the task.
```bash
git add tests/test_mcp_tools.py
git commit -F - <<'EOF'
test: add mailbox management mcp smoke coverage

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```

## Task 21: Add a README tools table for the 9-tool surface

**Files:**
- Modify `README.md` after line 283.

**Note:** there is no existing tools table in `README.md`; the request text is stale on that point. The README does use pipe-table markdown style already at lines 66-96, so this task creates a new `## MCP Tools` table in that same style instead of pretending an existing tools table is present.

- [ ] Step 1. Write a failing README assertion script for the new tool table.
```bash
cat <<'EOF' >/tmp/check_readme_tools_table.py
from pathlib import Path

readme = Path("README.md").read_text()

assert "## MCP Tools" in readme
assert "| Tool | Description |" in readme
for tool_name in [
    "list_available_accounts",
    "list_emails_metadata",
    "get_emails_content",
    "send_email",
    "delete_emails",
    "download_attachment",
    "list_mailboxes",
    "create_mailbox",
    "rename_mailbox",
    "delete_mailbox",
    "get_mailbox_status",
    "move_emails",
    "copy_emails",
    "mark_emails",
]:
    assert f"`{tool_name}`" in readme
EOF
```

- [ ] Step 2. Run the README assertion and expect failure because the README has no tools table yet.
```bash
python /tmp/check_readme_tools_table.py
```
Expected: `AssertionError` for missing `## MCP Tools`.

- [ ] Step 3. Add a new README tools table using the repo’s existing pipe-table style.
```bash
apply_patch <<'PATCH'
*** Begin Patch
*** Update File: README.md
@@
 The `in_reply_to` parameter sets the `In-Reply-To` header, and `references` sets the `References` header. Both are used by email clients to thread conversations properly.
+
+## MCP Tools
+
+| Tool | Description |
+| ---- | ----------- |
+| `list_available_accounts` | List all configured email accounts with masked credentials. |
+| `list_emails_metadata` | List email metadata without body content, now including `body_contains` and `text` full-text filters. |
+| `get_emails_content` | Get the full content of one or more emails by `email_id`. |
+| `send_email` | Send an email and optionally include reply-threading headers and attachments. |
+| `delete_emails` | Delete one or more emails by `email_id`. |
+| `download_attachment` | Download an attachment to a specified local path when attachment downloads are enabled. |
+| `list_mailboxes` | List mailboxes for an account using canonical `/` separators. |
+| `create_mailbox` | Create a mailbox on the IMAP server. |
+| `rename_mailbox` | Rename an existing mailbox on the IMAP server. |
+| `delete_mailbox` | Delete a mailbox from the IMAP server; requires `confirm=True`. |
+| `get_mailbox_status` | Return message counts plus `FLAGS` and `PERMANENTFLAGS` for a mailbox. |
+| `move_emails` | Move emails to another mailbox and return per-message results. |
+| `copy_emails` | Copy emails to another mailbox and return per-message results. |
+| `mark_emails` | Set or clear `Seen`, `Flagged`, and `Answered` flags for emails. |
 
 ## Development
*** End Patch
PATCH
```

- [ ] Step 4. Re-run the README assertion and expect PASS.
```bash
python /tmp/check_readme_tools_table.py
```

- [ ] Step 5. Commit the task.
```bash
git add README.md
git commit -F - <<'EOF'
docs: add mailbox management tools table

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
```
