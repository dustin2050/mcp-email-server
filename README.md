# mcp-email-server

[![Release](https://img.shields.io/github/v/release/ai-zerolab/mcp-email-server)](https://img.shields.io/github/v/release/ai-zerolab/mcp-email-server)
[![Build status](https://img.shields.io/github/actions/workflow/status/ai-zerolab/mcp-email-server/main.yml?branch=main)](https://github.com/ai-zerolab/mcp-email-server/actions/workflows/main.yml?query=branch%3Amain)
[![codecov](https://codecov.io/gh/ai-zerolab/mcp-email-server/branch/main/graph/badge.svg)](https://codecov.io/gh/ai-zerolab/mcp-email-server)
[![Commit activity](https://img.shields.io/github/commit-activity/m/ai-zerolab/mcp-email-server)](https://img.shields.io/github/commit-activity/m/ai-zerolab/mcp-email-server)
[![License](https://img.shields.io/github/license/ai-zerolab/mcp-email-server)](https://img.shields.io/github/license/ai-zerolab/mcp-email-server)
[![smithery badge](https://smithery.ai/badge/@ai-zerolab/mcp-email-server)](https://smithery.ai/server/@ai-zerolab/mcp-email-server)

IMAP and SMTP via MCP Server

For local `stdio` usage, no HTTP auth is needed. For Railway or other public HTTP deployments, `mcp-email-server` now supports OAuth 2.1 for `streamable-http` and `sse` transports using a static confidential client.

- **Github repository**: <https://github.com/ai-zerolab/mcp-email-server/>
- **Documentation** <https://ai-zerolab.github.io/mcp-email-server/>

## Installation

### Manual Installation

We recommend using [uv](https://github.com/astral-sh/uv) to manage your environment.

Try `uvx mcp-email-server@latest ui` to config, and use following configuration for mcp client:

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "uvx",
      "args": ["mcp-email-server@latest", "stdio"]
    }
  }
}
```

This package is available on PyPI, so you can install it using `pip install mcp-email-server`

After that, configure your email server using the ui: `mcp-email-server ui`

### Environment Variable Configuration

You can also configure the email server using environment variables, which is particularly useful for CI/CD environments like Jenkins. zerolib-email supports both UI configuration (via TOML file) and environment variables, with environment variables taking precedence.

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "uvx",
      "args": ["mcp-email-server@latest", "stdio"],
      "env": {
        "MCP_EMAIL_SERVER_ACCOUNT_NAME": "work",
        "MCP_EMAIL_SERVER_FULL_NAME": "John Doe",
        "MCP_EMAIL_SERVER_EMAIL_ADDRESS": "john@example.com",
        "MCP_EMAIL_SERVER_USER_NAME": "john@example.com",
        "MCP_EMAIL_SERVER_PASSWORD": "your_password",
        "MCP_EMAIL_SERVER_IMAP_HOST": "imap.gmail.com",
        "MCP_EMAIL_SERVER_IMAP_PORT": "993",
        "MCP_EMAIL_SERVER_SMTP_HOST": "smtp.gmail.com",
        "MCP_EMAIL_SERVER_SMTP_PORT": "465"
      }
    }
  }
}
```

#### Available Environment Variables

| Variable                                      | Description                                            | Default       | Required |
| --------------------------------------------- | ------------------------------------------------------ | ------------- | -------- |
| `MCP_EMAIL_SERVER_ACCOUNT_NAME`               | Account identifier                                     | `"default"`   | No       |
| `MCP_EMAIL_SERVER_FULL_NAME`                  | Display name                                           | Email prefix  | No       |
| `MCP_EMAIL_SERVER_EMAIL_ADDRESS`              | Email address                                          | -             | Yes      |
| `MCP_EMAIL_SERVER_USER_NAME`                  | Login username                                         | Same as email | No       |
| `MCP_EMAIL_SERVER_PASSWORD`                   | Email password                                         | -             | Yes      |
| `MCP_EMAIL_SERVER_IMAP_HOST`                  | IMAP server host                                       | -             | Yes      |
| `MCP_EMAIL_SERVER_IMAP_PORT`                  | IMAP server port                                       | `993`         | No       |
| `MCP_EMAIL_SERVER_IMAP_SSL`                   | Enable IMAP SSL                                        | `true`        | No       |
| `MCP_EMAIL_SERVER_IMAP_VERIFY_SSL`            | Verify IMAP SSL certificates (disable for self-signed) | `true`        | No       |
| `MCP_EMAIL_SERVER_SMTP_HOST`                  | SMTP server host                                       | -             | Yes      |
| `MCP_EMAIL_SERVER_SMTP_PORT`                  | SMTP server port                                       | `465`         | No       |
| `MCP_EMAIL_SERVER_SMTP_SSL`                   | Enable SMTP SSL                                        | `true`        | No       |
| `MCP_EMAIL_SERVER_SMTP_START_SSL`             | Enable STARTTLS                                        | `false`       | No       |
| `MCP_EMAIL_SERVER_SMTP_VERIFY_SSL`            | Verify SSL certificates (disable for self-signed)      | `true`        | No       |
| `MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_DOWNLOAD` | Enable attachment download                             | `false`       | No       |
| `MCP_EMAIL_SERVER_SAVE_TO_SENT`               | Save sent emails to IMAP Sent folder                   | `true`        | No       |
| `MCP_EMAIL_SERVER_SENT_FOLDER_NAME`           | Custom Sent folder name (auto-detect if not set)       | -             | No       |

### HTTP Transport Security

HTTP transports (`sse` and `streamable-http`) validate request `Host` and `Origin` headers to protect against DNS rebinding attacks. Localhost is allowed by default. For Docker networks or reverse proxies, configure the expected service names explicitly.

| Variable                              | Description                                                      | Default           |
| ------------------------------------- | ---------------------------------------------------------------- | ----------------- |
| `MCP_HOST`                            | HTTP bind host for `streamable-http`                             | `localhost`       |
| `MCP_PORT`                            | HTTP bind port for `streamable-http`                             | `9557`            |
| `MCP_ALLOWED_HOSTS`                   | Comma-separated allowed `Host` values. Supports `host:*` ports   | Localhost hosts   |
| `MCP_ALLOWED_ORIGINS`                 | Comma-separated allowed `Origin` values. Supports `host:*` ports | Localhost origins |
| `MCP_ENABLE_DNS_REBINDING_PROTECTION` | Enable DNS rebinding protection                                  | `true`            |

Docker Compose example:

```yaml
services:
  mcp-email-server:
    image: ghcr.io/ai-zerolab/mcp-email-server:latest
    command: ["streamable-http"]
    environment:
      MCP_HOST: 0.0.0.0
      MCP_PORT: 9557
      MCP_ALLOWED_HOSTS: mcp-email-server:*,localhost:*,127.0.0.1:*
      MCP_ALLOWED_ORIGINS: http://mcp-email-server:*,http://localhost:*,http://127.0.0.1:*
```

Bare host entries such as `MCP_ALLOWED_HOSTS=mcp-email-server` also allow any port on that host. `MCP_ENABLE_DNS_REBINDING_PROTECTION=false`, `MCP_ALLOWED_HOSTS=*`, or `MCP_ALLOWED_ORIGINS=*` disables Host and Origin validation entirely. Use those options only in isolated local development environments.

IPv6 literals in allowlists should use bracketed notation, such as `[::1]:*` and `http://[::1]:*`.

### OAuth 2.1 and Railway Deployment

Use OAuth for any public HTTP deployment. `stdio` remains unchanged and does not require OAuth.

#### Required Railway Settings

Set these variables in the Railway dashboard:

| Variable | Required | Example | Notes |
| --- | --- | --- | --- |
| `MCP_OAUTH_CLIENT_ID` | Yes for HTTP OAuth | `claude-desktop` | Static OAuth client id |
| `MCP_OAUTH_CLIENT_SECRET` | Yes for HTTP OAuth | `super-secret-value` | Static OAuth client secret |
| `MCP_PUBLIC_URL` | Yes for HTTP OAuth | `https://your-app.up.railway.app` | Public base URL used as OAuth issuer |
| `MCP_OAUTH_REDIRECT_URIS` | Recommended | `http://127.0.0.1:55432/callback,http://localhost:55432/callback` | Comma-separated redirect allowlist |
| `MCP_HOST` | Yes | `0.0.0.0` | Bind host for Railway |
| `MCP_PORT` | Yes | `8080` | Bind port supplied by Railway |
| Existing email vars | Yes | see above | Keep your IMAP/SMTP configuration set |

If `MCP_OAUTH_REDIRECT_URIS` is omitted, the server allows loopback redirect URIs on `127.0.0.1`, `localhost`, and `::1`.

#### Start Command

Use this Railway start command:

```bash
mcp-email-server streamable-http
```

#### Claude Desktop Connection

In Claude Desktop or another MCP OAuth client, use:

- Server URL = `https://your-app.up.railway.app/mcp`
- OAuth `client_id` = value of `MCP_OAUTH_CLIENT_ID`
- OAuth `client_secret` = value of `MCP_OAUTH_CLIENT_SECRET`

OAuth metadata URL:

- `https://your-app.up.railway.app/.well-known/oauth-authorization-server`

Protected resource metadata URL:

- `https://your-app.up.railway.app/.well-known/oauth-protected-resource/mcp`

#### Notes

- If `MCP_OAUTH_CLIENT_ID` is not set, HTTP transports still run but log `Running HTTP transport without OAuth authentication`.
- `/healthz` is exposed for Railway health checks.
- Revocation is enabled at `/revoke`.

### Enabling Attachment Downloads

By default, downloading email attachments is disabled for security reasons. To enable this feature, you can either:

**Option 1: Environment Variable**

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "uvx",
      "args": ["mcp-email-server@latest", "stdio"],
      "env": {
        "MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_DOWNLOAD": "true"
      }
    }
  }
}
```

**Option 2: TOML Configuration**

Add `enable_attachment_download = true` to your TOML configuration file (`~/.config/zerolib/mcp_email_server/config.toml`):

```toml
enable_attachment_download = true

[[emails]]
# ... your email configuration
```

Once enabled, you can use the `download_attachment` tool to save email attachments to a specified path.

### Saving Sent Emails to IMAP Sent Folder

By default, sent emails are automatically saved to your IMAP Sent folder. This ensures that emails sent via the MCP server appear in your email client (Thunderbird, webmail, etc.).

The server auto-detects common Sent folder names: `Sent`, `INBOX.Sent`, `Sent Items`, `Sent Mail`, `[Gmail]/Sent Mail`.

**To specify a custom Sent folder name** (useful for providers with non-standard folder names):

**Option 1: Environment Variable**

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "uvx",
      "args": ["mcp-email-server@latest", "stdio"],
      "env": {
        "MCP_EMAIL_SERVER_SENT_FOLDER_NAME": "INBOX.Sent"
      }
    }
  }
}
```

**Option 2: TOML Configuration**

```toml
[[emails]]
account_name = "work"
save_to_sent = true
sent_folder_name = "INBOX.Sent"
# ... rest of your email configuration
```

**To disable saving to Sent folder**, set `MCP_EMAIL_SERVER_SAVE_TO_SENT=false` or `save_to_sent = false` in your TOML config.

### Self-Signed Certificates (e.g., ProtonMail Bridge)

If you're using a local mail server with self-signed certificates (like ProtonMail Bridge), you'll need to disable SSL certificate verification:

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "uvx",
      "args": ["mcp-email-server@latest", "stdio"],
      "env": {
        "MCP_EMAIL_SERVER_IMAP_VERIFY_SSL": "false",
        "MCP_EMAIL_SERVER_SMTP_VERIFY_SSL": "false"
      }
    }
  }
}
```

Or in TOML configuration:

```toml
[[emails]]
account_name = "protonmail"
# ... other settings ...

[emails.incoming]
verify_ssl = false

[emails.outgoing]
verify_ssl = false
```

For separate IMAP/SMTP credentials, you can also use:

- `MCP_EMAIL_SERVER_IMAP_USER_NAME` / `MCP_EMAIL_SERVER_IMAP_PASSWORD`
- `MCP_EMAIL_SERVER_SMTP_USER_NAME` / `MCP_EMAIL_SERVER_SMTP_PASSWORD`

Then you can try it in [Claude Desktop](https://claude.ai/download). If you want to intergrate it with other mcp client, run `$which mcp-email-server` for the path and configure it in your client like:

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "{{ ENTRYPOINT }}",
      "args": ["stdio"]
    }
  }
}
```

If `docker` is avaliable, you can try use docker image, but you may need to config it in your client using `tools` via `MCP`. The default config path is `~/.config/zerolib/mcp_email_server/config.toml`

```json
{
  "mcpServers": {
    "zerolib-email": {
      "command": "docker",
      "args": ["run", "-it", "ghcr.io/ai-zerolab/mcp-email-server:latest"]
    }
  }
}
```

### Installing via Smithery

To install Email Server for Claude Desktop automatically via [Smithery](https://smithery.ai/server/@ai-zerolab/mcp-email-server):

```bash
npx -y @smithery/cli install @ai-zerolab/mcp-email-server --client claude
```

## Usage

### Replying to Emails

To reply to an email with proper threading (so it appears in the same conversation in email clients):

1. First, fetch the original email to get its `message_id`:

```python
emails = await get_emails_content(account_name="work", email_ids=["123"])
original = emails.emails[0]
```

2. Send your reply using `in_reply_to` and `references`:

```python
await send_email(
    account_name="work",
    recipients=[original.sender],
    subject=f"Re: {original.subject}",
    body="Thank you for your email...",
    in_reply_to=original.message_id,
    references=original.message_id,
)
```

The `in_reply_to` parameter sets the `In-Reply-To` header, and `references` sets the `References` header. Both are used by email clients to thread conversations properly.

## MCP Tools

| Tool | Description |
| ---- | ----------- |
| `list_available_accounts` | List all configured email accounts with masked credentials. |
| `list_emails_metadata` | List email metadata without body content, now including `body_contains` and `text` full-text filters. |
| `get_emails_content` | Get the full content of one or more emails by `email_id`. |
| `send_email` | Send an email and optionally include reply-threading headers and attachments. |
| `delete_emails` | Delete one or more emails by `email_id`. |
| `download_attachment` | Download an attachment to a specified local path when attachment downloads are enabled. |
| `list_mailboxes` | List mailboxes for an account using canonical `/` separators. |
| `create_mailbox` | Create a mailbox on the IMAP server. |
| `rename_mailbox` | Rename an existing mailbox on the IMAP server. |
| `delete_mailbox` | Delete a mailbox from the IMAP server; requires `confirm=True`. |
| `get_mailbox_status` | Return message counts plus `FLAGS` and `PERMANENTFLAGS` for a mailbox. |
| `move_emails` | Move emails to another mailbox and return per-message results. |
| `copy_emails` | Copy emails to another mailbox and return per-message results. |
| `mark_emails` | Set or clear `Seen`, `Flagged`, and `Answered` flags for emails. |

## Development

This project is managed using [uv](https://github.com/ai-zerolab/uv).

Try `make install` to install the virtual environment and install the pre-commit hooks.

Use `uv run mcp-email-server` for local development.

## Releasing a new version

- Create an API Token on [PyPI](https://pypi.org/).
- Add the API Token to your projects secrets with the name `PYPI_TOKEN` by visiting [this page](https://github.com/ai-zerolab/mcp-email-server/settings/secrets/actions/new).
- Create a [new release](https://github.com/ai-zerolab/mcp-email-server/releases/new) on Github.
- Create a new tag in the form `*.*.*`.

For more details, see [here](https://fpgmaas.github.io/cookiecutter-uv/features/cicd/#how-to-trigger-a-release).
