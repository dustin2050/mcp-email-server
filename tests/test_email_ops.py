import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.mailbox import EmailOps, MailboxOps


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
