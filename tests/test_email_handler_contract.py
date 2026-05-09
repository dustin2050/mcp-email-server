import inspect

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
async def test_classic_email_handler_mailbox_contract_methods_exist_as_stubs():
    handler = ClassicEmailHandler(build_settings())

    with pytest.raises(NotImplementedError):
        await handler.list_mailboxes()
    with pytest.raises(NotImplementedError):
        await handler.create_mailbox("INBOX/Archive")
    with pytest.raises(NotImplementedError):
        await handler.rename_mailbox("INBOX/Old", "INBOX/New")
    with pytest.raises(NotImplementedError):
        await handler.delete_mailbox("INBOX/Archive", confirm=True)
    with pytest.raises(NotImplementedError):
        await handler.get_mailbox_status("INBOX")
    with pytest.raises(NotImplementedError):
        await handler.move_emails(["1"], "INBOX", "Archive")
    with pytest.raises(NotImplementedError):
        await handler.copy_emails(["1"], "INBOX", "Archive")
    with pytest.raises(NotImplementedError):
        await handler.mark_emails(["1"], mailbox="INBOX", seen=True)
