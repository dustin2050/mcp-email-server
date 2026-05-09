import asyncio
from unittest.mock import AsyncMock, patch

from aioimaplib.aioimaplib import Response
import pytest

from mcp_email_server.config import EmailServer
from mcp_email_server.emails.mailbox import MailboxOps


@pytest.fixture
def email_server():
    return EmailServer(
        user_name='mailbox_user',
        password='mailbox_password',
        host='imap.example.com',
        port=993,
        use_ssl=True,
        verify_ssl=False,
    )


class TestMailboxOpsSkeleton:
    def test_init_sets_server_and_imap_class(self, email_server):
        ops = MailboxOps(email_server)
        assert ops.email_server == email_server
        assert ops.imap_class.__name__ == 'IMAP4_SSL'
        assert ops._delimiter is None

    def test_imap_connect_passes_ssl_context(self, email_server):
        ops = MailboxOps(email_server)

        with patch.object(ops, 'imap_class') as mock_imap_class:
            ops._imap_connect()
            mock_imap_class.assert_called_once()
            assert mock_imap_class.call_args.kwargs['ssl_context'] is not None

    @pytest.mark.asyncio
    async def test_login_logout_logs_in_sends_id_and_logs_out(self, email_server):
        ops = MailboxOps(email_server)
        mock_imap = AsyncMock()
        mock_imap._client_task = asyncio.Future()
        mock_imap._client_task.set_result(None)
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.logout = AsyncMock()

        with patch.object(ops, '_imap_connect', return_value=mock_imap):
            with patch('mcp_email_server.emails.mailbox._send_imap_id', new=AsyncMock()) as mock_send_id:
                async with ops._login_logout() as imap:
                    assert imap is mock_imap

        mock_imap.login.assert_called_once_with(
            email_server.user_name,
            email_server.password.get_secret_value(),
        )
        mock_send_id.assert_awaited_once_with(mock_imap)
        mock_imap.logout.assert_awaited_once()


class TestMailboxDelimiterTranslation:
    @pytest.mark.asyncio
    async def test_ensure_delimiter_parses_dot_and_caches(self, email_server):
        ops = MailboxOps(email_server)
        mock_imap = AsyncMock()
        mock_imap.list = AsyncMock(return_value=("OK", [b'(\\HasNoChildren) "." "INBOX"']))

        delimiter = await ops.ensure_delimiter(mock_imap)
        cached = await ops.ensure_delimiter(mock_imap)

        assert delimiter == "."
        assert cached == "."
        mock_imap.list.assert_awaited_once_with('""', "*")

    @pytest.mark.asyncio
    async def test_ensure_delimiter_parses_nil_as_flat_namespace(self, email_server):
        ops = MailboxOps(email_server)
        mock_imap = AsyncMock()
        mock_imap.list = AsyncMock(return_value=("OK", [b'(\\Noselect) NIL "Archive"']))

        delimiter = await ops.ensure_delimiter(mock_imap)

        assert delimiter == ""

    def test_to_imap_path_uses_cached_delimiter(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        assert ops.to_imap_path("INBOX/Archive/2026") == "INBOX.Archive.2026"

    def test_to_imap_path_rejects_empty_segments(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        with pytest.raises(ValueError, match="Invalid mailbox path"):
            ops.to_imap_path("INBOX//Archive")

    def test_from_imap_path_rewrites_server_delimiter_to_slash(self, email_server):
        ops = MailboxOps(email_server)
        assert ops.from_imap_path("INBOX.Archive.2026", ".") == "INBOX/Archive/2026"


class TestMailboxListing:
    @pytest.mark.asyncio
    async def test_list_mailboxes_uses_list_and_parses_paths(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.list = AsyncMock(
            return_value=(
                "OK",
                [
                    b'(\\HasNoChildren) "." "INBOX.Archive"',
                    b'(\\HasChildren \\Subscribed) "." "INBOX.Projects"',
                ],
            )
        )

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            mailboxes = await ops.list_mailboxes(pattern="INBOX/*")

        assert [mailbox.path for mailbox in mailboxes] == ["INBOX/Archive", "INBOX/Projects"]
        assert mailboxes[0].delimiter == "."
        assert mailboxes[0].flags == [r"\HasNoChildren"]
        assert mailboxes[0].subscribed is False
        assert mailboxes[1].subscribed is True
        mock_imap.list.assert_awaited_once_with('""', "INBOX.%")

    @pytest.mark.asyncio
    async def test_list_mailboxes_uses_lsub_for_subscribed_only(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.lsub = AsyncMock(
            return_value=("OK", [b'(\\Subscribed \\HasNoChildren) "." "INBOX.Newsletters"'])
        )

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            mailboxes = await ops.list_mailboxes(pattern="INBOX/*", subscribed_only=True)

        assert len(mailboxes) == 1
        assert mailboxes[0].path == "INBOX/Newsletters"
        assert mailboxes[0].subscribed is True
        mock_imap.lsub.assert_awaited_once_with('""', "INBOX.%")


class TestMailboxMutation:
    @pytest.mark.asyncio
    async def test_create_mailbox_translates_and_quotes_path(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.create = AsyncMock(return_value=Response("OK", [b"create completed"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            result = await ops.create_mailbox("INBOX/Projects/2026")

        assert result == "Successfully created mailbox 'INBOX/Projects/2026'"
        mock_imap.create.assert_awaited_once_with('"INBOX.Projects.2026"')

    @pytest.mark.asyncio
    async def test_create_mailbox_raises_on_no_response(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.create = AsyncMock(return_value=Response("NO", [b"[ALREADYEXISTS] mailbox exists"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            with pytest.raises(RuntimeError, match=r"ALREADYEXISTS.*mailbox exists"):
                await ops.create_mailbox("INBOX/Projects/2026")

    @pytest.mark.asyncio
    async def test_rename_mailbox_translates_both_paths(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.rename = AsyncMock(return_value=Response("OK", [b"rename completed"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            result = await ops.rename_mailbox("INBOX/Projects/2025", "INBOX/Projects/2026")

        assert result == "Successfully renamed mailbox 'INBOX/Projects/2025' to 'INBOX/Projects/2026'"
        mock_imap.rename.assert_awaited_once_with('"INBOX.Projects.2025"', '"INBOX.Projects.2026"')

    @pytest.mark.asyncio
    async def test_rename_mailbox_raises_on_no_response(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.rename = AsyncMock(return_value=Response("NO", [b"[NONEXISTENT] old mailbox missing"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            with pytest.raises(RuntimeError, match=r"NONEXISTENT.*old mailbox missing"):
                await ops.rename_mailbox("INBOX/Projects/2025", "INBOX/Projects/2026")

    @pytest.mark.asyncio
    async def test_delete_mailbox_requires_confirm(self, email_server):
        ops = MailboxOps(email_server)
        with pytest.raises(ValueError, match="Re-run with confirm=True"):
            await ops.delete_mailbox("INBOX/Archive")

    @pytest.mark.asyncio
    async def test_delete_mailbox_translates_and_quotes_when_confirmed(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.delete = AsyncMock(return_value=Response("OK", [b"delete completed"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            result = await ops.delete_mailbox("INBOX/Archive", confirm=True)

        assert result == "Successfully deleted mailbox 'INBOX/Archive'"
        mock_imap.delete.assert_awaited_once_with('"INBOX.Archive"')

    @pytest.mark.asyncio
    async def test_delete_mailbox_raises_on_no_response(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.delete = AsyncMock(return_value=Response("NO", [b"[INUSE] mailbox is selected"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            with pytest.raises(RuntimeError, match=r"INUSE.*mailbox is selected"):
                await ops.delete_mailbox("INBOX/Archive", confirm=True)


class TestMailboxStatus:
    @pytest.mark.asyncio
    async def test_get_mailbox_status_parses_counts_and_flags(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.examine = AsyncMock(
            return_value=Response(
                "OK",
                [
                    b"* FLAGS (\\Seen \\Answered \\Flagged)",
                    b"* OK [PERMANENTFLAGS (\\Seen \\Answered \\Flagged \\*)] Flags permitted.",
                ],
            )
        )
        mock_imap.status = AsyncMock(
            return_value=Response(
                "OK",
                [b'* STATUS "INBOX.Archive" (MESSAGES 12 RECENT 1 UIDNEXT 45 UIDVALIDITY 99 UNSEEN 3)'],
            )
        )

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            status = await ops.get_mailbox_status("INBOX/Archive")

        assert status.path == "INBOX/Archive"
        assert status.messages == 12
        assert status.recent == 1
        assert status.unseen == 3
        assert status.uid_next == 45
        assert status.uid_validity == 99
        assert status.flags == [r"\Seen", r"\Answered", r"\Flagged"]
        assert status.permanent_flags == [r"\Seen", r"\Answered", r"\Flagged", r"\*"]
        mock_imap.examine.assert_awaited_once_with('"INBOX.Archive"')
        mock_imap.status.assert_awaited_once_with('"INBOX.Archive"', "(MESSAGES RECENT UIDNEXT UIDVALIDITY UNSEEN)")

    @pytest.mark.asyncio
    async def test_get_mailbox_status_raises_on_examine_no_response(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.examine = AsyncMock(return_value=Response("NO", [b"[TRYCREATE] mailbox missing"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            with pytest.raises(RuntimeError, match=r"TRYCREATE.*mailbox missing"):
                await ops.get_mailbox_status("INBOX/Archive")

        mock_imap.status.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_mailbox_status_raises_on_status_no_response(self, email_server):
        ops = MailboxOps(email_server)
        ops._delimiter = "."
        mock_imap = AsyncMock()
        mock_imap.examine = AsyncMock(
            return_value=Response(
                "OK",
                [
                    b"* FLAGS (\\Seen)",
                    b"* OK [PERMANENTFLAGS (\\Seen \\*)] Flags permitted.",
                ],
            )
        )
        mock_imap.status = AsyncMock(return_value=Response("NO", [b"[SERVERBUG] status disallowed"]))

        with patch.object(ops, "_login_logout") as mock_login_logout:
            mock_login_logout.return_value.__aenter__.return_value = mock_imap
            mock_login_logout.return_value.__aexit__.return_value = None
            with pytest.raises(RuntimeError, match=r"SERVERBUG.*status disallowed"):
                await ops.get_mailbox_status("INBOX/Archive")
