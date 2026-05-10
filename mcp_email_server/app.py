from datetime import datetime
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.responses import JSONResponse

from mcp_email_server.config import (
    AccountAttributes,
    EmailSettings,
    ProviderSettings,
    get_settings,
)
from mcp_email_server.emails.dispatcher import dispatch_handler
from mcp_email_server.emails.models import (
    AttachmentDownloadResponse,
    CopiedEmail,
    EmailContentBatchResponse,
    EmailMetadataPageResponse,
    MailboxInfo,
    MailboxStatusResponse,
    MarkedEmail,
    MovedEmail,
)
from mcp_email_server.oauth import configure_fastmcp_oauth

mcp = FastMCP("email")


def configure_http_auth() -> bool:
    return configure_fastmcp_oauth(mcp)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request):
    return JSONResponse({"status": "ok"})


@mcp.resource("email://{account_name}")
async def get_account(account_name: str) -> EmailSettings | ProviderSettings | None:
    settings = get_settings()
    return settings.get_account(account_name, masked=True)


@mcp.tool(description="List all configured email accounts with masked credentials.")
async def list_available_accounts() -> list[AccountAttributes]:
    settings = get_settings()
    return [account.masked() for account in settings.get_accounts()]


@mcp.tool(description="Add a new email account configuration to the settings.")
async def add_email_account(email: EmailSettings) -> str:
    settings = get_settings()
    settings.add_email(email)
    settings.store()
    return f"Successfully added email account '{email.account_name}'"


@mcp.tool(
    description="List email metadata (email_id, subject, sender, recipients, date) without body content. Returns email_id for use with get_emails_content."
)
async def list_emails_metadata(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    page: Annotated[
        int,
        Field(default=1, description="The page number to retrieve (starting from 1)."),
    ] = 1,
    page_size: Annotated[int, Field(default=10, description="The number of emails to retrieve per page.")] = 10,
    before: Annotated[
        datetime | None,
        Field(default=None, description="Retrieve emails before this datetime (UTC)."),
    ] = None,
    since: Annotated[
        datetime | None,
        Field(default=None, description="Retrieve emails since this datetime (UTC)."),
    ] = None,
    subject: Annotated[str | None, Field(default=None, description="Filter emails by subject.")] = None,
    body_contains: Annotated[
        str | None,
        Field(default=None, description="Filter emails by body content using IMAP BODY search."),
    ] = None,
    text: Annotated[
        str | None,
        Field(default=None, description="Filter emails by full-text content using IMAP TEXT search."),
    ] = None,
    from_address: Annotated[str | None, Field(default=None, description="Filter emails by sender address.")] = None,
    to_address: Annotated[
        str | None,
        Field(default=None, description="Filter emails by recipient address."),
    ] = None,
    order: Annotated[
        Literal["asc", "desc"],
        Field(default=None, description="Order emails by field. `asc` or `desc`."),
    ] = "desc",
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to search.")] = "INBOX",
    seen: Annotated[
        bool | None,
        Field(default=None, description="Filter by read status: True=read, False=unread, None=all."),
    ] = None,
    flagged: Annotated[
        bool | None,
        Field(default=None, description="Filter by flagged/starred status: True=flagged, False=unflagged, None=all."),
    ] = None,
    answered: Annotated[
        bool | None,
        Field(default=None, description="Filter by replied status: True=replied, False=not replied, None=all."),
    ] = None,
) -> EmailMetadataPageResponse:
    handler = dispatch_handler(account_name)

    return await handler.get_emails_metadata(
        page=page,
        page_size=page_size,
        before=before,
        since=since,
        subject=subject,
        body_contains=body_contains,
        text=text,
        from_address=from_address,
        to_address=to_address,
        order=order,
        mailbox=mailbox,
        seen=seen,
        flagged=flagged,
        answered=answered,
    )


@mcp.tool(
    description="Get the full content (including body) of one or more emails by their email_id. Use list_emails_metadata first to get the email_id."
)
async def get_emails_content(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(
            description="List of email_id to retrieve (obtained from list_emails_metadata). Can be a single email_id or multiple email_ids."
        ),
    ],
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to retrieve emails from.")] = "INBOX",
) -> EmailContentBatchResponse:
    handler = dispatch_handler(account_name)
    return await handler.get_emails_content(email_ids, mailbox)


@mcp.tool(
    description="Send an email using the specified account. Supports replying to emails with proper threading when in_reply_to is provided.",
)
async def send_email(
    account_name: Annotated[str, Field(description="The name of the email account to send from.")],
    recipients: Annotated[list[str], Field(description="A list of recipient email addresses.")],
    subject: Annotated[str, Field(description="The subject of the email.")],
    body: Annotated[str, Field(description="The body of the email.")],
    cc: Annotated[
        list[str] | None,
        Field(default=None, description="A list of CC email addresses."),
    ] = None,
    bcc: Annotated[
        list[str] | None,
        Field(default=None, description="A list of BCC email addresses."),
    ] = None,
    html: Annotated[
        bool,
        Field(default=False, description="Whether to send the email as HTML (True) or plain text (False)."),
    ] = False,
    attachments: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="A list of absolute file paths to attach to the email. Supports common file types (documents, images, archives, etc.).",
        ),
    ] = None,
    in_reply_to: Annotated[
        str | None,
        Field(
            default=None,
            description="Message-ID of the email being replied to. Enables proper threading in email clients.",
        ),
    ] = None,
    references: Annotated[
        str | None,
        Field(
            default=None,
            description="Space-separated Message-IDs for the thread chain. Usually includes in_reply_to plus ancestors.",
        ),
    ] = None,
) -> str:
    handler = dispatch_handler(account_name)
    await handler.send_email(
        recipients,
        subject,
        body,
        cc,
        bcc,
        html,
        attachments,
        in_reply_to,
        references,
    )
    recipient_str = ", ".join(recipients)
    attachment_info = f" with {len(attachments)} attachment(s)" if attachments else ""
    return f"Email sent successfully to {recipient_str}{attachment_info}"


@mcp.tool(
    description="Delete one or more emails by their email_id. Use list_emails_metadata first to get the email_id."
)
async def delete_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(description="List of email_id to delete (obtained from list_emails_metadata)."),
    ],
    mailbox: Annotated[str, Field(default="INBOX", description="The mailbox to delete emails from.")] = "INBOX",
) -> str:
    handler = dispatch_handler(account_name)
    deleted_ids, failed_ids = await handler.delete_emails(email_ids, mailbox)

    result = f"Successfully deleted {len(deleted_ids)} email(s)"
    if failed_ids:
        result += f", failed to delete {len(failed_ids)} email(s): {', '.join(failed_ids)}"
    return result


@mcp.tool(
    description="Download an email attachment and save it to the specified path. This feature must be explicitly enabled in settings (enable_attachment_download=true) due to security considerations.",
)
async def download_attachment(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_id: Annotated[
        str, Field(description="The email ID (obtained from list_emails_metadata or get_emails_content).")
    ],
    attachment_name: Annotated[
        str, Field(description="The name of the attachment to download (as shown in the attachments list).")
    ],
    save_path: Annotated[str, Field(description="The absolute path where the attachment should be saved.")],
    mailbox: Annotated[str, Field(description="The mailbox to search in (default: INBOX).")] = "INBOX",
) -> AttachmentDownloadResponse:
    settings = get_settings()
    if not settings.enable_attachment_download:
        msg = (
            "Attachment download is disabled. Set 'enable_attachment_download=true' in settings to enable this feature."
        )
        raise PermissionError(msg)

    handler = dispatch_handler(account_name)
    return await handler.download_attachment(email_id, attachment_name, save_path, mailbox)


@mcp.tool(description="List available mailboxes for an account.")
async def list_mailboxes(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    pattern: Annotated[str, Field(default="*", description="Mailbox pattern in canonical user syntax.")] = "*",
    subscribed_only: Annotated[bool, Field(default=False, description="If True, use IMAP LSUB.")] = False,
) -> list[MailboxInfo]:
    handler = dispatch_handler(account_name)
    return await handler.list_mailboxes(pattern, subscribed_only)


@mcp.tool(
    description=(
        "Create a mailbox on the IMAP server. ALWAYS use '/' as the path separator, "
        "even when the underlying IMAP server uses '.' or another character — this tool "
        "auto-detects the server delimiter and translates internally. Example: pass "
        "'INBOX/Projekte/Kunden' to create 'Kunden' as a subfolder of 'Projekte' under "
        "'INBOX'. Passing 'INBOX.Foo' instead would create a flat top-level folder "
        "literally named 'INBOX.Foo', not a subfolder. Parent folders are NOT auto-created."
    )
)
async def create_mailbox(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    mailbox: Annotated[
        str,
        Field(description="New mailbox path. ALWAYS use '/' as the separator (e.g. 'INBOX/Subfolder')."),
    ],
) -> str:
    handler = dispatch_handler(account_name)
    return await handler.create_mailbox(mailbox)


@mcp.tool(
    description=(
        "Rename a mailbox on the IMAP server. Use '/' as the separator for hierarchical "
        "paths (e.g. 'INBOX/Old' -> 'INBOX/New'). The tool translates '/' to the server's "
        "actual delimiter automatically. Missing parent folders in new_mailbox are NOT "
        "auto-created."
    )
)
async def rename_mailbox(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    old_mailbox: Annotated[str, Field(description="Existing mailbox path. Use '/' as the separator.")],
    new_mailbox: Annotated[str, Field(description="New mailbox path. Use '/' as the separator.")],
) -> str:
    handler = dispatch_handler(account_name)
    return await handler.rename_mailbox(old_mailbox, new_mailbox)


@mcp.tool(
    description=(
        "Delete a mailbox from the IMAP server. Requires confirm=True. Use '/' as the "
        "path separator (e.g. 'INBOX/Subfolder'). The tool translates to the server's "
        "actual delimiter. To delete a flat folder whose literal name contains '.', "
        "pass the literal name without '/' (e.g. 'INBOX.Misnamed') — no translation "
        "happens when '/' is absent."
    )
)
async def delete_mailbox(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    mailbox: Annotated[str, Field(description="Mailbox path. Use '/' as the separator for hierarchies.")],
    confirm: Annotated[bool, Field(default=False, description="Must be True to delete the mailbox.")] = False,
) -> str:
    if not confirm:
        raise ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")
    handler = dispatch_handler(account_name)
    return await handler.delete_mailbox(mailbox, confirm)


@mcp.tool(description="Get message counts and flags for a mailbox.")
async def get_mailbox_status(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    mailbox: Annotated[str, Field(default="INBOX", description="Mailbox path in canonical user syntax.")] = "INBOX",
) -> MailboxStatusResponse:
    handler = dispatch_handler(account_name)
    return await handler.get_mailbox_status(mailbox)


@mcp.tool(description="Move emails to another mailbox and return per-message results.")
async def move_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[list[str], Field(description="Email UIDs to move.")],
    source_mailbox: Annotated[str, Field(description="Source mailbox in canonical user syntax.")],
    destination_mailbox: Annotated[str, Field(description="Destination mailbox in canonical user syntax.")],
) -> list[MovedEmail]:
    handler = dispatch_handler(account_name)
    return await handler.move_emails(email_ids, source_mailbox, destination_mailbox)


@mcp.tool(description="Copy emails to another mailbox and return per-message results.")
async def copy_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[list[str], Field(description="Email UIDs to copy.")],
    source_mailbox: Annotated[str, Field(description="Source mailbox in canonical user syntax.")],
    destination_mailbox: Annotated[str, Field(description="Destination mailbox in canonical user syntax.")],
) -> list[CopiedEmail]:
    handler = dispatch_handler(account_name)
    return await handler.copy_emails(email_ids, source_mailbox, destination_mailbox)


@mcp.tool(description="Set or clear Seen, Flagged, and Answered flags for emails.")
async def mark_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[list[str], Field(description="Email UIDs to update.")],
    mailbox: Annotated[str, Field(default="INBOX", description="Mailbox containing the emails.")] = "INBOX",
    seen: Annotated[bool | None, Field(default=None, description="True=set, False=clear, None=leave unchanged.")] = None,
    flagged: Annotated[
        bool | None, Field(default=None, description="True=set, False=clear, None=leave unchanged.")
    ] = None,
    answered: Annotated[
        bool | None, Field(default=None, description="True=set, False=clear, None=leave unchanged.")
    ] = None,
) -> list[MarkedEmail]:
    handler = dispatch_handler(account_name)
    return await handler.mark_emails(email_ids, mailbox=mailbox, seen=seen, flagged=flagged, answered=answered)
