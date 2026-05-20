import abc
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_email_server.emails.models import (
        AttachmentDownloadResponse,
        CopiedEmail,
        EmailContentBatchResponse,
        EmailMetadataPageResponse,
        MailboxInfo,
        MailboxStatusResponse,
        MarkedEmail,
        MovedEmail,
        SendEmailResponse,
    )


class EmailHandler(abc.ABC):
    @abc.abstractmethod
    async def get_emails_metadata(
        self,
        page: int = 1,
        page_size: int = 10,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        body_contains: str | None = None,
        text: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        order: str = "desc",
        mailbox: str = "INBOX",
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> "EmailMetadataPageResponse":
        """
        Get email metadata only (without body content) for better performance.

        Args:
            page: Page number (starting from 1).
            page_size: Number of emails per page.
            before: Filter emails before this datetime.
            since: Filter emails since this datetime.
            subject: Filter by subject (substring match).
            body_contains: Filter by IMAP BODY search.
            text: Filter by IMAP TEXT search.
            from_address: Filter by sender address.
            to_address: Filter by recipient address.
            order: Sort order ('asc' or 'desc').
            mailbox: Mailbox to search (default: 'INBOX').
            seen: Filter by read status (True=read, False=unread, None=all).
            flagged: Filter by flagged/starred status (True=flagged, False=unflagged, None=all).
            answered: Filter by replied status (True=replied, False=not replied, None=all).
        """

    @abc.abstractmethod
    async def get_emails_content(self, email_ids: list[str], mailbox: str = "INBOX") -> "EmailContentBatchResponse":
        """
        Get full content (including body) of multiple emails by their email IDs (IMAP UIDs)
        """

    @abc.abstractmethod
    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> "SendEmailResponse":
        """
        Send email

        Args:
            recipients: List of recipient email addresses.
            subject: Email subject.
            body: Email body content.
            cc: List of CC email addresses.
            bcc: List of BCC email addresses.
            html: Whether to send as HTML (True) or plain text (False).
            attachments: List of file paths to attach.
            in_reply_to: Message-ID of the email being replied to (for threading).
            references: Space-separated Message-IDs for the thread chain.
        """

    @abc.abstractmethod
    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """
        Delete emails by their IDs. Returns (deleted_ids, failed_ids)
        """

    @abc.abstractmethod
    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> "AttachmentDownloadResponse":
        """
        Download an email attachment and save it to the specified path.

        Args:
            email_id: The UID of the email containing the attachment.
            attachment_name: The filename of the attachment to download.
            save_path: The local path where the attachment will be saved.
            mailbox: The mailbox to search in (default: "INBOX").

        Returns:
            AttachmentDownloadResponse with download result information.
        """

    @abc.abstractmethod
    async def get_attachment_content(
        self,
        email_id: str,
        attachment_name: str,
        mailbox: str = "INBOX",
    ) -> dict:
        """Fetch an email attachment and return its raw bytes inline.

        Returns a dict with keys: ``email_id``, ``attachment_name``, ``mime_type``,
        ``size`` (int, bytes), ``data`` (bytes). No filesystem side effects.
        """

    @abc.abstractmethod
    async def list_mailboxes(self, pattern: str = "*", subscribed_only: bool = False) -> list["MailboxInfo"]:
        """List mailboxes."""

    @abc.abstractmethod
    async def create_mailbox(self, mailbox: str) -> str:
        """Create a mailbox."""

    @abc.abstractmethod
    async def rename_mailbox(self, old_mailbox: str, new_mailbox: str) -> str:
        """Rename a mailbox."""

    @abc.abstractmethod
    async def delete_mailbox(self, mailbox: str, confirm: bool = False) -> str:
        """Delete a mailbox."""

    @abc.abstractmethod
    async def get_mailbox_status(self, mailbox: str = "INBOX") -> "MailboxStatusResponse":
        """Get mailbox status."""

    @abc.abstractmethod
    async def move_emails(
        self,
        email_ids: list[str],
        source_mailbox: str,
        destination_mailbox: str,
    ) -> list["MovedEmail"]:
        """Move emails."""

    @abc.abstractmethod
    async def copy_emails(
        self,
        email_ids: list[str],
        source_mailbox: str,
        destination_mailbox: str,
    ) -> list["CopiedEmail"]:
        """Copy emails."""

    @abc.abstractmethod
    async def mark_emails(
        self,
        email_ids: list[str],
        mailbox: str = "INBOX",
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> list["MarkedEmail"]:
        """Mark emails."""
