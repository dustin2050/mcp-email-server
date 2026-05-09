import inspect
from unittest.mock import AsyncMock, patch

import pytest

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails import EmailHandler
from mcp_email_server.emails.classic import ClassicEmailHandler


def build_settings() -> EmailSettings:
    return EmailSettings(
        account_name="contract",
        full_name="Contract Tester",
        email_address="contract@example.com",
        incoming=EmailServer(
            user_name="contract",
            password="secret",
            host="imap.example.com",
            port=993,
            use_ssl=True,
        ),
        outgoing=EmailServer(
            user_name="contract",
            password="secret",
            host="smtp.example.com",
            port=465,
            use_ssl=True,
        ),
    )


def test_email_handler_metadata_signature_exposes_body_contains_and_text():
    params = inspect.signature(EmailHandler.get_emails_metadata).parameters
    assert "body_contains" in params
    assert params["body_contains"].default is None
    assert "text" in params
    assert params["text"].default is None


def test_classic_email_handler_still_instantiates_after_contract_extension():
    handler = ClassicEmailHandler(build_settings())
    assert isinstance(handler, EmailHandler)


@pytest.mark.asyncio
async def test_classic_email_handler_mailbox_contract_methods_delegate_to_ops():
    handler = ClassicEmailHandler(build_settings())

    with patch.object(handler.mailbox_ops, "list_mailboxes", AsyncMock(return_value=[])) as mock_list:
        assert await handler.list_mailboxes() == []
        mock_list.assert_awaited_once_with("*", False)
    with patch.object(handler.mailbox_ops, "create_mailbox", AsyncMock(return_value="created")) as mock_create:
        assert await handler.create_mailbox("INBOX/Archive") == "created"
        mock_create.assert_awaited_once_with("INBOX/Archive")
    with patch.object(handler.mailbox_ops, "rename_mailbox", AsyncMock(return_value="renamed")) as mock_rename:
        assert await handler.rename_mailbox("INBOX/Old", "INBOX/New") == "renamed"
        mock_rename.assert_awaited_once_with("INBOX/Old", "INBOX/New")
    with patch.object(handler.mailbox_ops, "delete_mailbox", AsyncMock(return_value="deleted")) as mock_delete:
        assert await handler.delete_mailbox("INBOX/Archive", confirm=True) == "deleted"
        mock_delete.assert_awaited_once_with("INBOX/Archive", True)
    with patch.object(handler.mailbox_ops, "get_mailbox_status", AsyncMock(return_value="status")) as mock_status:
        assert await handler.get_mailbox_status("INBOX") == "status"
        mock_status.assert_awaited_once_with("INBOX")
    with patch.object(handler.email_ops, "move_emails", AsyncMock(return_value=[])) as mock_move:
        assert await handler.move_emails(["1"], "INBOX", "Archive") == []
        mock_move.assert_awaited_once_with(["1"], "INBOX", "Archive")
    with patch.object(handler.email_ops, "copy_emails", AsyncMock(return_value=[])) as mock_copy:
        assert await handler.copy_emails(["1"], "INBOX", "Archive") == []
        mock_copy.assert_awaited_once_with(["1"], "INBOX", "Archive")
    with patch.object(handler.email_ops, "mark_emails", AsyncMock(return_value=[])) as mock_mark:
        assert await handler.mark_emails(["1"], mailbox="INBOX", seen=True) == []
        mock_mark.assert_awaited_once_with(["1"], mailbox="INBOX", seen=True, flagged=None, answered=None)
