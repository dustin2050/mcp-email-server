from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class EmailMetadata(BaseModel):
    """Email metadata"""

    email_id: str
    message_id: str | None = None  # RFC 5322 Message-ID header for reply threading
    subject: str
    sender: str
    recipients: list[str]  # Recipient list
    date: datetime
    attachments: list[str]

    @classmethod
    def from_email(cls, email: dict[str, Any]):
        return cls(
            email_id=email["email_id"],
            message_id=email.get("message_id"),
            subject=email["subject"],
            sender=email["from"],
            recipients=email.get("to", []),
            date=email["date"],
            attachments=email["attachments"],
        )


class EmailMetadataPageResponse(BaseModel):
    """Paged email metadata response"""

    page: int
    page_size: int
    before: datetime | None
    since: datetime | None
    subject: str | None
    emails: list[EmailMetadata]
    total: int


class EmailBodyResponse(EmailMetadata):
    """Single email body response - extends EmailMetadata with body content"""

    body: str


class EmailContentBatchResponse(BaseModel):
    """Batch email content response for multiple emails"""

    emails: list[EmailBodyResponse]
    requested_count: int
    retrieved_count: int
    failed_ids: list[str]


class AttachmentDownloadResponse(BaseModel):
    """Attachment download response"""

    email_id: str
    attachment_name: str
    mime_type: str
    size: int
    saved_path: str


class SendEmailResponse(BaseModel):
    """Result of sending an email. SMTP delivery is independent of sent_copy:
    if this response is returned at all, the recipient's MTA accepted the
    message. sent_copy reflects only the IMAP Sent-folder archive."""

    recipients: list[str]
    attachments_count: int = 0
    sent_copy: Literal["saved", "disabled", "failed"]
    sent_copy_folder: str | None = None
    sent_copy_error: str | None = None


class MailboxInfo(BaseModel):
    """Mailbox information"""

    path: str
    delimiter: str
    flags: list[str]
    subscribed: bool


class MailboxStatusResponse(BaseModel):
    """Mailbox status response"""

    path: str
    messages: int
    recent: int
    unseen: int | None = None
    uid_next: int | None = None
    uid_validity: int | None = None
    flags: list[str]
    permanent_flags: list[str]


class MovedEmail(BaseModel):
    """Moved email result"""

    message_id: str
    success: bool
    error: str | None = None
    method: Literal["native", "fallback"]


class CopiedEmail(BaseModel):
    """Copied email result"""

    message_id: str
    success: bool
    error: str | None = None


class MarkedEmail(BaseModel):
    """Marked email result"""

    message_id: str
    success: bool
    error: str | None = None
