import asyncio
from unittest.mock import AsyncMock, patch

import aioimaplib
import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.mailbox import EmailOps, MailboxOps
from mcp_email_server.emails.models import CopiedEmail, MarkedEmail, MovedEmail


@pytest.fixture
def email_server():
    return EmailServer(
        user_name="ops_user",
        password="ops_password",
        host="imap.example.com",
        port=993,
        use_ssl=True,
    )


class TestEmailOpsSkeleton:
    def test_init_sets_server_and_mailbox_ops(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        email_ops = EmailOps(email_server, mailbox_ops)

        assert email_ops.email_server == email_server
        assert email_ops.mailbox_ops is mailbox_ops
        assert email_ops.imap_class.__name__ == "IMAP4_SSL"

    @pytest.mark.asyncio
    async def test_login_logout_logs_in_and_out(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.logout = AsyncMock()

        with patch.object(email_ops, "_imap_connect", return_value=mock_imap):
            with patch("mcp_email_server.emails.mailbox._send_imap_id", new=AsyncMock()) as mock_send_id:
                async with email_ops._login_logout() as imap:
                    assert imap is mock_imap

        mock_send_id.assert_awaited_once_with(mock_imap)
        mock_imap.logout.assert_awaited_once()


class TestEmailOpsMove:
    @pytest.mark.asyncio
    async def test_move_emails_native_success_uses_uid_move(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(return_value=("OK", [b"move completed"]))

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            moved = await email_ops.move_emails(["101", "102"], "INBOX", "INBOX/Archive")

        assert [item.message_id for item in moved] == ["101", "102"]
        assert all(item.success is True for item in moved)
        assert all(item.method == "native" for item in moved)
        mock_imap.select.assert_awaited_once_with('"INBOX"')
        mock_imap.uid.assert_awaited_once_with("move", "101,102", '"INBOX.Archive"')

    @pytest.mark.asyncio
    async def test_move_emails_falls_back_on_bad_result(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("BAD", [b"uid move unsupported"]),
                ("OK", [b"copy 101"]),
                ("OK", [b"store 101"]),
                ("OK", [b"copy 102"]),
                ("OK", [b"store 102"]),
                ("OK", [b"expunge completed"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            moved = await email_ops.move_emails(["101", "102"], "INBOX", "INBOX/Archive")

        assert [item.method for item in moved] == ["fallback", "fallback"]
        assert all(item.success is True for item in moved)
        assert mock_imap.uid.await_args_list[1].args == ("copy", "101", '"INBOX.Archive"')
        assert mock_imap.uid.await_args_list[2].args == ("store", "101", "+FLAGS", r"(\Deleted)")
        assert mock_imap.uid.await_args_list[-1].args == ("expunge", "101,102")

    @pytest.mark.asyncio
    async def test_move_emails_marks_tentative_successes_failed_when_expunge_fails(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("NO", [b"move rejected"]),
                ("OK", [b"copy 101"]),
                ("OK", [b"store 101"]),
                ("NO", [b"expunge rejected"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            moved = await email_ops.move_emails(["101"], "INBOX", "INBOX/Archive")

        assert moved[0].success is False
        assert moved[0].method == "fallback"
        assert "UID EXPUNGE failed" in moved[0].error
        assert "remain in the mailbox flagged" in moved[0].error

    @pytest.mark.asyncio
    async def test_move_emails_fallback_continues_after_copy_no_for_first_uid(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("NO", [b"move rejected"]),
                ("NO", [b"copy 101 failed"]),
                ("OK", [b"copy 102"]),
                ("OK", [b"store 102"]),
                ("OK", [b"expunge completed"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            moved = await email_ops.move_emails(["101", "102"], "INBOX", "INBOX/Archive")

        assert moved == [
            MovedEmail(
                message_id="101",
                success=False,
                error="UID COPY failed with IMAP result NO: [b'copy 101 failed']",
                method="fallback",
            ),
            MovedEmail(message_id="102", success=True, error=None, method="fallback"),
        ]
        assert mock_imap.uid.await_args_list[1].args == ("copy", "101", '"INBOX.Archive"')
        assert mock_imap.uid.await_args_list[2].args == ("copy", "102", '"INBOX.Archive"')
        assert mock_imap.uid.await_args_list[-1].args == ("expunge", "102")

    @pytest.mark.asyncio
    async def test_move_emails_fallback_store_no_does_not_attempt_rollback(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("BAD", [b"move unsupported"]),
                ("OK", [b"copy 101"]),
                ("NO", [b"store 101 failed"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            moved = await email_ops.move_emails(["101"], "INBOX", "INBOX/Archive")

        assert moved == [
            MovedEmail(
                message_id="101",
                success=False,
                error=r"UID STORE +FLAGS (\Deleted) failed with IMAP result NO: [b'store 101 failed']",
                method="fallback",
            )
        ]
        assert len(mock_imap.uid.await_args_list) == 3
        assert mock_imap.uid.await_args_list[1].args == ("copy", "101", '"INBOX.Archive"')
        assert mock_imap.uid.await_args_list[2].args == ("store", "101", "+FLAGS", r"(\Deleted)")
        mock_imap.expunge.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_emails_fallback_records_raised_aioimaplib_exception(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("NO", [b"move rejected"]),
                aioimaplib.Abort("copy connection dropped"),
                ("OK", [b"copy 102"]),
                ("OK", [b"store 102"]),
                ("OK", [b"expunge completed"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            moved = await email_ops.move_emails(["101", "102"], "INBOX", "INBOX/Archive")

        assert moved == [
            MovedEmail(message_id="101", success=False, error="copy connection dropped", method="fallback"),
            MovedEmail(message_id="102", success=True, error=None, method="fallback"),
        ]
        assert mock_imap.uid.await_args_list[1].args == ("copy", "101", '"INBOX.Archive"')
        assert mock_imap.uid.await_args_list[2].args == ("copy", "102", '"INBOX.Archive"')
        assert mock_imap.uid.await_args_list[-1].args == ("expunge", "102")


class TestEmailOpsCopy:
    @pytest.mark.asyncio
    async def test_copy_emails_continues_on_per_uid_failures(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("OK", [b"copy 101"]),
                ("NO", [b"copy 102 failed"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            copied = await email_ops.copy_emails(["101", "102"], "INBOX", "INBOX/Archive")

        assert copied == [
            CopiedEmail(message_id="101", success=True, error=None),
            CopiedEmail(message_id="102", success=False, error="UID COPY failed with IMAP result NO: [b'copy 102 failed']"),
        ]
        mock_imap.expunge.assert_not_called()


class TestEmailOpsMark:
    @pytest.mark.asyncio
    async def test_mark_emails_rejects_all_none(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        email_ops = EmailOps(email_server, mailbox_ops)
        with pytest.raises(ValueError, match="At least one of seen, flagged, or answered must be set"):
            await email_ops.mark_emails(["101"], mailbox="INBOX")

    @pytest.mark.asyncio
    async def test_mark_emails_sends_add_and_remove_store_commands(self, email_server):
        mailbox_ops = MailboxOps(email_server)
        mailbox_ops._delimiter = "."
        email_ops = EmailOps(email_server, mailbox_ops)
        mock_imap = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b"selected"]))
        mock_imap.uid = AsyncMock(
            side_effect=[
                ("OK", [b"add flags"]),
                ("OK", [b"remove flags"]),
            ]
        )

        with patch.object(email_ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            marked = await email_ops.mark_emails(["101"], mailbox="INBOX", seen=True, flagged=False, answered=True)

        assert marked == [MarkedEmail(message_id="101", success=True, error=None)]
        assert mock_imap.uid.await_args_list[0].args == ("store", "101", "+FLAGS", r"(\Seen \Answered)")
        assert mock_imap.uid.await_args_list[1].args == ("store", "101", "-FLAGS", r"(\Flagged)")
