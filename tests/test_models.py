from datetime import datetime, timezone

from mcp_email_server.emails.models import (
    CopiedEmail,
    EmailBodyResponse,
    EmailMetadata,
    EmailMetadataPageResponse,
    MailboxInfo,
    MailboxStatusResponse,
    MarkedEmail,
    MovedEmail,
)


class TestEmailMetadata:
    def test_init(self):
        """Test initialization with valid data."""
        email_data = EmailMetadata(
            email_id="123",
            message_id=None,
            subject="Test Subject",
            sender="test@example.com",
            recipients=["recipient@example.com"],
            date=datetime.now(timezone.utc),
            attachments=["file1.txt", "file2.pdf"],
        )

        assert email_data.subject == "Test Subject"
        assert email_data.sender == "test@example.com"
        assert email_data.recipients == ["recipient@example.com"]
        assert isinstance(email_data.date, datetime)
        assert email_data.attachments == ["file1.txt", "file2.pdf"]

    def test_from_email(self):
        """Test from_email class method."""
        now = datetime.now(timezone.utc)
        email_dict = {
            "email_id": "123",
            "subject": "Test Subject",
            "from": "test@example.com",
            "to": ["recipient@example.com"],
            "date": now,
            "attachments": ["file1.txt", "file2.pdf"],
        }

        email_data = EmailMetadata.from_email(email_dict)

        assert email_data.subject == "Test Subject"
        assert email_data.sender == "test@example.com"
        assert email_data.recipients == ["recipient@example.com"]
        assert email_data.date == now
        assert email_data.attachments == ["file1.txt", "file2.pdf"]

    def test_from_email_with_message_id(self):
        """Test from_email extracts message_id when present."""
        now = datetime.now(timezone.utc)
        email_dict = {
            "email_id": "123",
            "message_id": "<test@example.com>",
            "subject": "Test Subject",
            "from": "test@example.com",
            "to": ["recipient@example.com"],
            "date": now,
            "attachments": [],
        }
        email_data = EmailMetadata.from_email(email_dict)
        assert email_data.message_id == "<test@example.com>"

    def test_from_email_without_message_id(self):
        """Test from_email handles missing message_id."""
        now = datetime.now(timezone.utc)
        email_dict = {
            "email_id": "123",
            "subject": "Test Subject",
            "from": "test@example.com",
            "to": ["recipient@example.com"],
            "date": now,
            "attachments": [],
        }
        email_data = EmailMetadata.from_email(email_dict)
        assert email_data.message_id is None


class TestEmailMetadataPageResponse:
    def test_init(self):
        """Test initialization with valid data."""
        now = datetime.now(timezone.utc)
        email_data = EmailMetadata(
            email_id="123",
            message_id=None,
            subject="Test Subject",
            sender="test@example.com",
            recipients=["recipient@example.com"],
            date=now,
            attachments=[],
        )

        response = EmailMetadataPageResponse(
            page=1,
            page_size=10,
            before=now,
            since=None,
            subject="Test",
            emails=[email_data],
            total=1,
        )

        assert response.page == 1
        assert response.page_size == 10
        assert response.before == now
        assert response.since is None
        assert response.subject == "Test"
        assert len(response.emails) == 1
        assert response.emails[0] == email_data
        assert response.total == 1

    def test_empty_emails(self):
        """Test with empty email list."""
        response = EmailMetadataPageResponse(
            page=1,
            page_size=10,
            before=None,
            since=None,
            subject=None,
            emails=[],
            total=0,
        )

        assert response.page == 1
        assert response.page_size == 10
        assert len(response.emails) == 0
        assert response.total == 0


def test_email_metadata_includes_message_id():
    """Test that EmailMetadata includes message_id field."""
    metadata = EmailMetadata(
        email_id="123",
        message_id="<abc123@example.com>",
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime.now(timezone.utc),
        attachments=[],
    )
    assert metadata.message_id == "<abc123@example.com>"


def test_email_metadata_message_id_optional():
    """Test that message_id can be None."""
    metadata = EmailMetadata(
        email_id="123",
        message_id=None,
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime.now(timezone.utc),
        attachments=[],
    )
    assert metadata.message_id is None


def test_email_body_response_includes_message_id():
    """Test that EmailBodyResponse includes message_id field."""
    response = EmailBodyResponse(
        email_id="123",
        message_id="<abc123@example.com>",
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime.now(timezone.utc),
        body="Test body",
        attachments=[],
    )
    assert response.message_id == "<abc123@example.com>"


def test_mailbox_info_model():
    mailbox = MailboxInfo(
        path="INBOX/Archive",
        delimiter=".",
        flags=[r"\HasNoChildren"],
        subscribed=True,
    )
    assert mailbox.path == "INBOX/Archive"
    assert mailbox.delimiter == "."
    assert mailbox.flags == [r"\HasNoChildren"]
    assert mailbox.subscribed is True


def test_mailbox_status_response_model():
    status = MailboxStatusResponse(
        path="INBOX/Archive",
        messages=12,
        recent=1,
        unseen=3,
        uid_next=45,
        uid_validity=99,
        flags=[r"\Seen", r"\Answered"],
        permanent_flags=[r"\Seen", r"\Answered", r"\*"],
    )
    assert status.path == "INBOX/Archive"
    assert status.messages == 12
    assert status.recent == 1
    assert status.unseen == 3
    assert status.uid_next == 45
    assert status.uid_validity == 99


def test_moved_email_model():
    moved = MovedEmail(message_id="101", success=True, error=None, method="native")
    assert moved.message_id == "101"
    assert moved.success is True
    assert moved.error is None
    assert moved.method == "native"


def test_copied_email_model():
    copied = CopiedEmail(message_id="202", success=False, error="copy failed")
    assert copied.message_id == "202"
    assert copied.success is False
    assert copied.error == "copy failed"


def test_marked_email_model():
    marked = MarkedEmail(message_id="303", success=True, error=None)
    assert marked.message_id == "303"
    assert marked.success is True
    assert marked.error is None
