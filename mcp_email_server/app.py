import base64
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent
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
    SendEmailResponse,
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
    description=(
        "Send an email using the specified account. Supports replying to emails with "
        "proper threading when in_reply_to is provided. The response includes a "
        "sent_copy field indicating whether an archive copy was stored in the IMAP "
        "Sent folder (or 'disabled' / 'failed' with a reason). SMTP delivery is "
        "independent — if the response is returned at all, the outgoing server "
        "accepted the message."
    ),
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
) -> SendEmailResponse:
    handler = dispatch_handler(account_name)
    return await handler.send_email(
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
    description=(
        "Save an email attachment to a filesystem path on the machine running the server. "
        "This is ONLY useful for local stdio deployments where the server and the client "
        "share a filesystem. For remote/HTTP clients (Claude.ai, Claude Desktop) this tool "
        "cannot help — the file lands on the server's disk, out of the client's reach — so "
        "use `get_attachment` instead, which returns the content inline. Disabled by default "
        "and must be explicitly enabled with enable_attachment_download=true."
    ),
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
            "Attachment download is disabled (enable_attachment_download=false). "
            "Note that download_attachment only writes to the server's local disk and is "
            "meant for local stdio deployments; for remote clients it cannot deliver the "
            "file. To read, view, or hand an attachment to the user, call the "
            "`get_attachment` tool instead — it needs no configuration and returns images, "
            "PDF text (with OCR fallback), and other files inline. Only enable this tool "
            "(enable_attachment_download=true) if you specifically need to save to a local path."
        )
        raise PermissionError(msg)

    handler = dispatch_handler(account_name)
    return await handler.download_attachment(email_id, attachment_name, save_path, mailbox)


# Cap inline attachment payload to keep MCP responses transport-friendly.
# Anything above is rejected with a hint to use download_attachment instead.
_ATTACHMENT_MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 MiB


def _extract_pdf_text(data: bytes) -> str | None:
    """Extract text from a PDF blob. Returns None if extraction yields nothing
    (e.g. scanned PDF) or if pypdf raises."""
    from io import BytesIO

    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        chunks = []
        for i, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                chunks.append(f"--- page {i} ---\n{page_text}")
        text = "\n\n".join(chunks).strip()
        return text or None
    except Exception:
        return None


_OCR_LANG = "deu+eng"
_OCR_MAX_PAGES = 50
_OCR_DPI = 200


def _ocr_pdf(data: bytes) -> str | None:
    """OCR a PDF by rendering pages to images and running tesseract.
    Returns None on failure (e.g. tesseract not installed) or empty result."""
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError:
        return None
    try:
        pdf = pdfium.PdfDocument(data)
        n = min(len(pdf), _OCR_MAX_PAGES)
        chunks = []
        for i in range(n):
            page = pdf[i]
            bitmap = page.render(scale=_OCR_DPI / 72)
            pil_image = bitmap.to_pil()
            page_text = pytesseract.image_to_string(pil_image, lang=_OCR_LANG).strip()
            if page_text:
                chunks.append(f"--- page {i + 1} (OCR) ---\n{page_text}")
        return "\n\n".join(chunks).strip() or None
    except Exception:
        return None


def _ocr_image(data: bytes) -> str | None:
    """OCR an image blob via tesseract."""
    try:
        from io import BytesIO

        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(BytesIO(data))
        text = pytesseract.image_to_string(img, lang=_OCR_LANG).strip()
        return text or None
    except Exception:
        return None


def _decode_text_attachment(data: bytes, mime: str) -> str | None:
    """Decode a text/* attachment. Returns None on decode failure."""
    # Try common encodings; charset is often missing from mail attachments.
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


@mcp.tool(
    description=(
        "Retrieve an email attachment and return its content inline. This is the "
        "primary, always-available way to download, read, or view an attachment — "
        "prefer it over download_attachment for any client that is not running the "
        "server locally (Claude.ai, Claude Desktop, and other remote/HTTP clients). "
        "By default "
        "(mode='auto') the server picks the best representation: images come back "
        "as image content blocks (viewable in the chat), PDFs are returned as "
        "extracted plain text (falls back to OCR via tesseract for scanned PDFs), "
        "text files as decoded text, other binaries as embedded resource blobs. "
        "Use mode='text' to force text extraction (PDF text layer first, then OCR), "
        "mode='ocr' to force OCR (for PDFs and images, useful for scanned docs), "
        "mode='raw' to force the raw base64 blob. For saving to a local filesystem "
        "path, use `download_attachment` instead. "
        f"Payload cap: {_ATTACHMENT_MAX_INLINE_BYTES // (1024 * 1024)} MiB. "
        f"OCR languages: {_OCR_LANG}."
    ),
)
async def get_attachment(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_id: Annotated[
        str, Field(description="The email ID (obtained from list_emails_metadata or get_emails_content).")
    ],
    attachment_name: Annotated[
        str, Field(description="The name of the attachment to fetch (as shown in the attachments list).")
    ],
    mailbox: Annotated[str, Field(description="The mailbox to search in (default: INBOX).")] = "INBOX",
    mode: Annotated[
        Literal["auto", "text", "ocr", "raw"],
        Field(
            description=(
                "How to return the attachment. 'auto' (default): server picks the best "
                "representation per MIME type; PDFs use embedded text layer with OCR "
                "fallback for scans. 'text': force text extraction (PDF text layer first, "
                "then OCR; text/* decoded). 'ocr': force tesseract OCR (PDFs and images). "
                "'raw': skip server-side text/OCR extraction and return the raw "
                "base64 bytes (images as ImageContent so they remain viewable; "
                "everything else as EmbeddedResource)."
            )
        ),
    ] = "auto",
) -> list[ImageContent | EmbeddedResource | TextContent]:
    handler = dispatch_handler(account_name)
    result = await handler.get_attachment_content(email_id, attachment_name, mailbox)

    data: bytes = result["data"]
    mime: str = result["mime_type"] or "application/octet-stream"
    size: int = result["size"]

    if size > _ATTACHMENT_MAX_INLINE_BYTES:
        limit_mib = _ATTACHMENT_MAX_INLINE_BYTES // (1024 * 1024)
        actual_mib = size / (1024 * 1024)
        msg = (
            f"Attachment '{attachment_name}' is {actual_mib:.1f} MiB which exceeds the "
            f"inline cap of {limit_mib} MiB. Use download_attachment to save it to disk."
        )
        raise ValueError(msg)

    summary = TextContent(
        type="text",
        text=f"Attachment '{attachment_name}' ({mime}, {size} bytes) from email {email_id}.",
    )

    # --- mode='ocr': force tesseract on PDFs / images ---
    if mode == "ocr":
        if mime == "application/pdf":
            ocr_text = _ocr_pdf(data)
        elif mime.startswith("image/"):
            ocr_text = _ocr_image(data)
        else:
            msg = (
                f"mode='ocr' is only supported for application/pdf and image/* MIME types, "
                f"got '{mime}'."
            )
            raise ValueError(msg)
        if ocr_text is None:
            msg = f"OCR produced no text for '{attachment_name}' (tesseract missing, or empty result)."
            raise ValueError(msg)
        header = TextContent(
            type="text",
            text=f"OCR text from '{attachment_name}' ({len(ocr_text)} chars, lang={_OCR_LANG}):",
        )
        return [header, TextContent(type="text", text=ocr_text)]

    # --- mode='text': prefer text layer, fall back to OCR, error if both fail ---
    if mode == "text":
        extracted: str | None = None
        if mime == "application/pdf":
            extracted = _extract_pdf_text(data)
            if extracted is None:
                extracted = _ocr_pdf(data)
            if extracted is None:
                msg = (
                    f"Could not extract text from PDF '{attachment_name}' — neither text "
                    "layer nor OCR yielded anything."
                )
                raise ValueError(msg)
        elif mime.startswith("image/"):
            extracted = _ocr_image(data)
            if extracted is None:
                msg = f"OCR yielded no text for image '{attachment_name}'."
                raise ValueError(msg)
        elif mime.startswith("text/"):
            extracted = _decode_text_attachment(data, mime)
            if extracted is None:
                msg = f"Could not decode text attachment '{attachment_name}' (unknown encoding)."
                raise ValueError(msg)
        else:
            msg = (
                f"mode='text' is only supported for application/pdf, image/*, and text/* "
                f"MIME types, got '{mime}'."
            )
            raise ValueError(msg)
        return [summary, TextContent(type="text", text=extracted)]

    # --- mode='auto': pick best representation per MIME ---
    if mode == "auto":
        if mime == "application/pdf":
            extracted = _extract_pdf_text(data)
            source = "text layer"
            if not extracted:
                extracted = _ocr_pdf(data)
                source = "OCR"
            if extracted:
                header = TextContent(
                    type="text",
                    text=(
                        f"Extracted text from PDF '{attachment_name}' via {source} "
                        f"({len(extracted)} chars from {size} bytes):"
                    ),
                )
                return [header, TextContent(type="text", text=extracted)]
            # Fall through to blob if both fail
            note = TextContent(
                type="text",
                text=(
                    f"PDF '{attachment_name}' yielded no text via extraction or OCR. "
                    "Returning raw blob."
                ),
            )
            b64 = base64.b64encode(data).decode("ascii")
            uri = f"attachment://{account_name}/{quote(mailbox, safe='')}/{email_id}/{quote(attachment_name, safe='')}"
            blob = BlobResourceContents(uri=uri, mimeType=mime, blob=b64)
            return [note, EmbeddedResource(type="resource", resource=blob)]
        if mime.startswith("image/"):
            b64 = base64.b64encode(data).decode("ascii")
            return [summary, ImageContent(type="image", data=b64, mimeType=mime)]
        if mime.startswith("text/"):
            decoded = _decode_text_attachment(data, mime)
            if decoded is not None:
                return [summary, TextContent(type="text", text=decoded)]
            # Fall through to blob on decode failure

    # --- mode='raw' or unhandled MIME in auto ---
    b64 = base64.b64encode(data).decode("ascii")
    uri = f"attachment://{account_name}/{quote(mailbox, safe='')}/{email_id}/{quote(attachment_name, safe='')}"
    blob = BlobResourceContents(uri=uri, mimeType=mime, blob=b64)
    if mime.startswith("image/"):
        # raw mode for image — still return as ImageContent so client can show it
        return [summary, ImageContent(type="image", data=b64, mimeType=mime)]
    return [summary, EmbeddedResource(type="resource", resource=blob)]


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
