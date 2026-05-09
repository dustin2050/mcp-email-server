# Mailbox Management — Design Spec

**Datum:** 2026-05-09
**Repo:** `mcp-email-server` (Fork: `dustin2050/mcp-email-server`)
**Ziel:** Den MCP-Server um Folder-Management (anlegen, umbenennen, löschen, listen, status) und erweiterte E-Mail-Operationen (move, copy, mark) erweitern; `list_emails_metadata` um Volltextsuche ergänzen.

## Sektion 0: Zusammenfassung & getroffene Entscheidungen

**Scope (vom User bestätigt):** 9 neue/erweiterte Tools — `list_mailboxes`, `create_mailbox`, `rename_mailbox`, `delete_mailbox`, `get_mailbox_status`, `move_emails`, `copy_emails`, `mark_emails`, plus `body`/`text`-Filter auf `list_emails_metadata`.

**API-Entscheidungen (vom User bestätigt):**
- **Folder-Pfad-API:** Voller Pfad mit `/` als Universal-Separator. Server-Delimiter wird automatisch via IMAP `LIST '""' '""'` erkannt und intern übersetzt.
- **delete_mailbox-Safety:** `confirm: bool = False`-Param am Tool. Ohne `confirm=True` → `ValueError`.
- **mark_emails-API:** Narrow, benannte Booleans (`seen`, `flagged`, `answered`). `None` = unverändert lassen.
- **search_emails:** Kein eigenes Tool. Stattdessen werden zwei neue Filter `body_contains` (IMAP `BODY`) und `text` (IMAP `TEXT`) zu `list_emails_metadata` hinzugefügt. (Der Param heißt `body_contains` statt `body`, um Verwechslung mit Body-Retrieval zu vermeiden — siehe Sektion 2.9.)

**Naming-Konsistenz mit der bestehenden API:** Alle neuen Tools nutzen `account_name` (nicht `account_id`), `email_ids` (nicht `message_ids`) und `mailbox`/`source_mailbox`/`destination_mailbox` (nicht `path`/`folder`). Damit ist die Tool-Surface kohärent zu `list_emails_metadata`, `delete_emails` etc.

## Sektion 1: Architektur & Modul-Layout

**Approach B (vom User bestätigt):** Mailbox-Operationen in eigene Helfer-Klassen auslagern, statt `classic.py` (bereits 1182 Zeilen) weiter wachsen zu lassen.

**Neue/geänderte Dateien:**

```
mcp_email_server/
  app.py                    # +9 neue MCP-Tools, +2 neue Params auf list_emails_metadata
  emails/
    __init__.py             # EmailHandler abstract: +9 neue async-Methoden
    classic.py              # ClassicEmailHandler delegiert an MailboxOps/EmailOps
    mailbox.py              # NEU: MailboxOps (Folder + Delimiter), EmailOps (move/copy/mark)
    models.py               # +MailboxInfo, MailboxStatusResponse, MovedEmail, CopiedEmail, MarkedEmail
tests/
  test_mailbox_ops.py       # NEU
  test_email_ops.py         # NEU
  test_mcp_tools.py         # erweitert
```

**Klassen-Verantwortlichkeiten:**
- `MailboxOps` — IMAP-Folder-Verwaltung, einziger Owner der Delimiter-Detection und Pfad-Übersetzung. Nutzt `EmailClient` für Connect/Login.
- `EmailOps` — Per-E-Mail-Operationen jenseits von `delete_emails` (move, copy, mark). Bekommt `MailboxOps` injiziert für Pfad-Übersetzung.
- `ClassicEmailHandler` — bekommt `self.mailbox_ops` und `self.email_ops`; die neuen `EmailHandler`-Methoden delegieren ohne Eigenlogik.

Die Detail-Sektionen 2–4 unten finalisieren Tool-Signaturen, Implementierungs-Edge-Cases (Delimiter-Detection, MOVE-Fallback, Pfadübersetzung, Fehlermodell) und Teststrategie. Diese wurden von Codex auf Bitte des Users entschieden.

---

## Sektion 2: Finalisierte MCP Tool Signaturen

### Gemeinsame Response-Modelle

Die neuen MCP-Tools sollen bestehende `pydantic.BaseModel`-Konventionen aus `mcp_email_server/emails/models.py` fortführen. Für die neuen Mailbox- und Bulk-Operationen werden folgende Modelle ergänzt:

```python
from typing import Literal

from pydantic import BaseModel


class MailboxInfo(BaseModel):
    path: str
    delimiter: str
    flags: list[str]
    subscribed: bool


class MailboxStatusResponse(BaseModel):
    path: str
    messages: int
    recent: int
    unseen: int | None = None
    uid_next: int | None = None
    uid_validity: int | None = None
    flags: list[str]
    permanent_flags: list[str]


class MovedEmail(BaseModel):
    message_id: str
    success: bool
    error: str | None = None
    method: Literal["native", "fallback"]


class CopiedEmail(BaseModel):
    message_id: str
    success: bool
    error: str | None = None


class MarkedEmail(BaseModel):
    message_id: str
    success: bool
    error: str | None = None
```

Rationale:
- `MailboxInfo.path` ist immer die kanonische User-Ansicht mit `/` als Separator. Der rohe Server-Delimiter wird separat in `delimiter` zurückgegeben.
- `MovedEmail.method` ist verpflichtend, damit der Aufrufer sofort sieht, ob `UID MOVE` nativ oder der Fallback `COPY+STORE+EXPUNGE` verwendet wurde.
- Für `copy_emails` und `mark_emails` ist kein Wrapper-Response nötig; die zentrale Semantik ist das per-ID-Ergebnis.

### 1. `list_mailboxes`

```python
@mcp.tool(description="List available mailboxes for an account.")
async def list_mailboxes(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    pattern: Annotated[
        str,
        Field(
            default="*",
            description="Mailbox pattern in canonical user syntax. `/` is always treated as the mailbox separator."
        ),
    ] = "*",
    subscribed_only: Annotated[
        bool,
        Field(default=False, description="If True, list only subscribed mailboxes via IMAP LSUB."),
    ] = False,
) -> list[MailboxInfo]:
```

Kurze Tool-Beschreibung:
`"List available mailboxes for an account."`

Rationale:
- `pattern` bleibt ein String mit IMAP-Wildcards `*` und `%`; nur der Separator wird in User-Eingaben immer als `/` interpretiert.
- `subscribed_only=True` mappt direkt auf `LSUB`, weil das semantisch präziser ist als ein `LIST` plus clientseitiges Filtern.

### 2. `create_mailbox`

```python
@mcp.tool(description="Create a mailbox on the IMAP server. Use '/' as the path separator (e.g. 'INBOX/Projekte/Kunden'). Parent folders are NOT auto-created.")
async def create_mailbox(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    mailbox: Annotated[str, Field(description="New mailbox path in canonical user syntax (use '/' as separator).")],
) -> str:
```

Kurze Tool-Beschreibung:
`"Create a mailbox on the IMAP server."`

Rationale:
- Rückgabewert ist ein einfacher Success-String; Fehler werden nicht in das Payload eingebettet, sondern als Exception propagiert.
- Keine implizite Parent-Erzeugung. Das Tool führt exakt eine IMAP-`CREATE`-Operation gegen den übersetzten Zielpfad aus.

### 3. `rename_mailbox`

```python
@mcp.tool(description="Rename a mailbox on the IMAP server. Use '/' as the path separator. Missing parent folders in the new path are NOT auto-created.")
async def rename_mailbox(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    old_mailbox: Annotated[str, Field(description="Existing mailbox path in canonical user syntax (use '/' as separator).")],
    new_mailbox: Annotated[str, Field(description="New mailbox path in canonical user syntax (use '/' as separator).")],
) -> str:
```

Kurze Tool-Beschreibung:
`"Rename a mailbox on the IMAP server."`

Rationale:
- `rename_mailbox` erzeugt fehlende Elternordner ausdrücklich nicht. Wenn der Parent von `new_mailbox` serverseitig fehlt, schlägt `RENAME` fehl und die Exception wird unverändert hochgereicht.
- Das hält die Operation deterministisch und verhindert implizite Seiteneffekte in der Ordnerhierarchie.

### 4. `delete_mailbox`

```python
@mcp.tool(description="Delete a mailbox from the IMAP server. Requires confirm=True. Use '/' as the path separator.")
async def delete_mailbox(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    mailbox: Annotated[str, Field(description="Mailbox path in canonical user syntax (use '/' as separator).")],
    confirm: Annotated[
        bool,
        Field(default=False, description="Must be True to perform the destructive delete operation."),
    ] = False,
) -> str:
```

Kurze Tool-Beschreibung:
`"Delete a mailbox from the IMAP server. Requires confirm=True."`

Rationale:
- `confirm=False` ist kein Soft-Warning, sondern ein sofortiger `ValueError` mit klarer Handlungsanweisung.
- Die Bestätigung im Tool-Call selbst ist die einzige Schutzschicht; es gibt keine zusätzliche interaktive Nachfrage.

### 5. `get_mailbox_status`

```python
@mcp.tool(description="Get message counts and flags for a mailbox. Use '/' as the path separator.")
async def get_mailbox_status(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    mailbox: Annotated[str, Field(default="INBOX", description="Mailbox path in canonical user syntax (use '/' as separator).")] = "INBOX",
) -> MailboxStatusResponse:
```

Kurze Tool-Beschreibung:
`"Get message counts and flags for a mailbox."`

Rationale:
- Das Tool liefert Counts plus `FLAGS`/`PERMANENTFLAGS` in einem strukturierten Modell statt als Freitext.
- Mailbox-Existenzfehler und Protokollfehler werden als Exceptions behandelt, nicht als partielles Statusmodell.

### 6. `move_emails`

```python
@mcp.tool(description="Move emails to another mailbox and return per-message results.")
async def move_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(description="List of email_id (IMAP UID) to move (obtained from list_emails_metadata)."),
    ],
    source_mailbox: Annotated[str, Field(description="Source mailbox in canonical user syntax (use '/' as separator).")],
    destination_mailbox: Annotated[str, Field(description="Destination mailbox in canonical user syntax (use '/' as separator).")],
) -> list[MovedEmail]:
```

Kurze Tool-Beschreibung:
`"Move emails to another mailbox and return per-message results."`

Rationale:
- Die Rückgabe ist immer ein `list[MovedEmail]`, nie ein Success-String. Die Operation ist bewusst per-ID sichtbar, weil Fallback-Moves partiell fehlschlagen können.
- `method` ist pro Element verpflichtend, um `UID MOVE` gegen `COPY+STORE+EXPUNGE` transparent zu machen.

### 7. `copy_emails`

```python
@mcp.tool(description="Copy emails to another mailbox and return per-message results.")
async def copy_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(description="List of email_id (IMAP UID) to copy (obtained from list_emails_metadata)."),
    ],
    source_mailbox: Annotated[str, Field(description="Source mailbox in canonical user syntax (use '/' as separator).")],
    destination_mailbox: Annotated[str, Field(description="Destination mailbox in canonical user syntax (use '/' as separator).")],
) -> list[CopiedEmail]:
```

Kurze Tool-Beschreibung:
`"Copy emails to another mailbox and return per-message results."`

Rationale:
- Kein `expunge`-Parameter. Copy ist explizit nicht-destruktiv; ein Expunge-Schalter würde die API semantisch verwässern.
- Copy wird per ID ausgeführt, damit das Resultat exakt zum partiellen Fehlerbild passt.

### 8. `mark_emails`

```python
@mcp.tool(description="Set or clear Seen, Flagged, and Answered flags for emails.")
async def mark_emails(
    account_name: Annotated[str, Field(description="The name of the email account.")],
    email_ids: Annotated[
        list[str],
        Field(description="List of email_id (IMAP UID) to update (obtained from list_emails_metadata)."),
    ],
    mailbox: Annotated[str, Field(default="INBOX", description="Mailbox containing the messages, in canonical user syntax (use '/' as separator).")] = "INBOX",
    seen: Annotated[
        bool | None,
        Field(default=None, description="Set True to add \\Seen, False to remove it, None to leave unchanged."),
    ] = None,
    flagged: Annotated[
        bool | None,
        Field(default=None, description="Set True to add \\Flagged, False to remove it, None to leave unchanged."),
    ] = None,
    answered: Annotated[
        bool | None,
        Field(default=None, description="Set True to add \\Answered, False to remove it, None to leave unchanged."),
    ] = None,
) -> list[MarkedEmail]:
```

Kurze Tool-Beschreibung:
`"Set or clear Seen, Flagged, and Answered flags for emails."`

Rationale:
- `None` bedeutet unverändert; `True` setzt das Flag, `False` entfernt es.
- Wenn alle drei Parameter `None` sind, wird vor dem IMAP-Zugriff ein `ValueError` ausgelöst. Ein stilles No-op-Tool ist hier nicht sinnvoll.

### 9. `list_emails_metadata`

Die bestehende Signatur wird erweitert, aber nicht umbenannt. `account_name` bleibt aus Kompatibilitätsgründen unverändert.

```python
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
```

Kurze Tool-Beschreibung:
Bestehende Description beibehalten.

Rationale:
- `body_contains` wird bewusst nicht `body` genannt, damit kein Konflikt mit tatsächlichem Body-Retrieval entsteht.
- `text` spiegelt den IMAP-Operator `TEXT` direkt wider.
- Die bestehende öffentliche API bleibt stabil; nur zwei optionale Filter werden ergänzt.

## Sektion 3: Implementierungsdetails

### (a) Delimiter Detection

`MailboxOps` ist für die Delimiter-Erkennung und Pfadübersetzung verantwortlich. Die Erkennung passiert lazy beim ersten Zugriff und wird auf der Instanz gecacht.

Konkretes Verhalten:

1. `MailboxOps` hält ein Feld `_delimiter: str | None = None`.
2. Beim ersten Bedarf ruft `MailboxOps.ensure_delimiter()` genau einmal `LIST "" "*"` auf dem bereits authentifizierten IMAP-Handle auf.
3. Aus der ersten echten `LIST`-Response-Zeile wird der Delimiter extrahiert.
4. Wenn der Server als Delimiter `NIL` meldet, wird intern `""` gecacht.
5. Danach wird `LIST` für Delimiter-Erkennung auf derselben `MailboxOps`-Instanz nie erneut ausgeführt.

Parsing-Regel:

```python
LIST_LINE_RE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|"[^"]*")\s+(?P<name>.+)$'
)
```

Verarbeitung:
- Erste `bytes`-Zeile nehmen, die auf das Regex passt.
- `delimiter_token == b"NIL"` bedeutet flacher Namespace; `delimiter = ""`.
- Sonst Token entquoten, also etwa `"."` zu `"."` oder `"/"` zu `"/"`.
- Wenn keine parsebare Zeile vorliegt, wird `RuntimeError("Could not detect mailbox delimiter from IMAP LIST response")` ausgelöst.

Namespace-Regel:
- Rohe Server-Präfixe werden nicht transformiert und nicht abgeschnitten.
- Beispiel: Meldet der Server Mailboxen wie `INBOX.Archive`, dann ist der kanonische User-Pfad `INBOX/Archive`.
- Der führende Segmentname `INBOX` bleibt Teil des User-Pfads; das System versucht nicht, ein besonderes Namespace-Mapping zu erraten.

NIL-Regel:
- Bei `delimiter == ""` wird jeder User-Pfad als einzelner Mailbox-Name behandelt.
- Es gibt dann keine Segmentzerlegung auf `/`; der komplette String wird als Name verwendet.

### (b) MOVE-Strategie

`EmailOps.move_emails()` verwendet strikt diese Reihenfolge:

1. Source-Mailbox selektieren.
2. Zielpfad mit `MailboxOps.to_imap_path()` übersetzen und mit `_quote_mailbox()` quoten.
3. Zuerst `UID MOVE` gegen die komplette UID-Liste versuchen.
4. Nur wenn der MOVE-Versuch mit IMAP-Status `BAD` oder `NO` endet, auf Fallback umschalten.
5. Bei Verbindungsfehlern, Timeouts oder sonstigen Exceptions kein automatischer Fallback; diese Fehler werden direkt hochgereicht.

Native MOVE:
- Befehl: `UID MOVE <uid-set> "<destination>"`
- Bei `OK` werden alle angefragten IDs als erfolgreich mit `method="native"` zurückgegeben.
- Es gibt keinen Versuch, native Teilerfolge zu rekonstruieren. Native MOVE wird als atomare Erfolgs- oder Fallback-Entscheidung behandelt.

Fallback MOVE:
- Pro UID einzeln:
  1. `UID COPY <uid> "<destination>"`
  2. bei Erfolg `UID STORE <uid> +FLAGS (\Deleted)`
- Erfolgreiche Kandidaten werden gesammelt.
- Nach der Schleife genau ein `EXPUNGE`, wenn mindestens eine UID im Source-Folder auf `\Deleted` gesetzt wurde.

Partielle Fehleroberfläche:
- Wenn `COPY` fehlschlägt, `MovedEmail(message_id=<uid>, success=False, error=<reason>, method="fallback")`.
- Wenn `STORE +FLAGS (\Deleted)` fehlschlägt, ebenfalls `success=False`, `method="fallback"`. Es gibt keinen Rollback der bereits erfolgten Kopie.
- Wenn das abschließende `EXPUNGE` fehlschlägt, werden alle bis dahin nur tentativ erfolgreichen Fallback-Moves als fehlgeschlagen markiert:
  `error="COPY succeeded and source was flagged \\Deleted, but EXPUNGE failed; no rollback performed."`
- Es wird nie versucht, kopierte Nachrichten wieder zu löschen oder Flags zurückzunehmen.

Das ist absichtlich identisch zur geforderten Semantik: Fallback ist best-effort, partial success ist sichtbar, Rollback findet nie statt.

### (c) Pfadübersetzung

Pfadübersetzung ist ausschließlich Aufgabe von `MailboxOps`.

Kanonische User-Regeln:
- User-Eingaben verwenden immer `/` als Separator.
- Es gibt keinen Escape-Mechanismus für ein literales `/` in einem Mailbox-Namen.
- Diese Einschränkung ist dokumentierte Produktgrenze, kein Sonderfall zur Laufzeit.

Übersetzung nach IMAP:

```python
def to_imap_path(self, user_path: str) -> str:
    delimiter = self._delimiter_or_raise()
    if delimiter == "":
        return user_path
    parts = user_path.split("/")
    if any(part == "" for part in parts):
        raise ValueError(f"Invalid mailbox path: {user_path!r}")
    return delimiter.join(parts)
```

Wichtige Regeln:
- Vor dem Senden an IMAP wird der resultierende Mailbox-Name immer mit `_quote_mailbox()` gequotet.
- Für Pattern in `list_mailboxes` bleibt `*` oder `%` unverändert; nur die Separator-Zeichen werden umgeschrieben.
- Bei Delimiter `""` wird nicht gesplittet. `Projects/2026` bleibt dann genau `Projects/2026`.

Übersetzung zurück in User-Syntax:

```python
def from_imap_path(self, server_path: str, delimiter: str) -> str:
    if delimiter == "":
        return server_path
    return server_path.replace(delimiter, "/")
```

Dokumentierte Limitation:
- Ein echter Server-Mailbox-Name, der ein literales `/` enthält, ist über diese API nicht adressierbar, weil `/` immer Separator bedeutet.
- Das ist bewusst akzeptiert. Es wird keine Escape-Syntax, kein Backslash-Protokoll und keine heuristische Sonderbehandlung eingeführt.

### (d) Fehlermodell

Das Fehlermodell wird bewusst in zwei Klassen getrennt:

Single-target Mailbox-Operationen, die Exceptions werfen:
- `list_mailboxes`
- `create_mailbox`
- `rename_mailbox`
- `delete_mailbox`
- `get_mailbox_status`

Multi-ID Email-Operationen mit strukturiertem Partial-Success-Result:
- `move_emails` -> `list[MovedEmail]`
- `copy_emails` -> `list[CopiedEmail]`
- `mark_emails` -> `list[MarkedEmail]`

Konkrete Regeln:
- `delete_mailbox(confirm=False)` wirft sofort `ValueError(f"Refusing to delete mailbox '{mailbox}'. Re-run with confirm=True.")`.
- `rename_mailbox` wirft serverseitige `NO/BAD`-Fehler ungefiltert nach oben; fehlende Elternordner werden nicht abgefangen oder automatisch erzeugt.
- `mark_emails` validiert vor dem IMAP-Zugriff, dass mindestens eines von `seen`, `flagged`, `answered` nicht `None` ist.
- `copy_emails` und `mark_emails` fahren bei Fehlern pro UID fort, analog zum bestehenden `delete_emails`-Muster in `classic.py`.

Ausrichtung auf bestehendes Verhalten:
- `delete_emails` in `classic.py` arbeitet bereits als Best-Effort-Schleife mit Success/Failure-Aggregation.
- Die neuen Bulk-Tools übernehmen genau dieses Continue-on-error-Verhalten, aber mit typisierten per-ID-Objekten statt Tuple oder Freitext.
- Mailbox-Operationen bleiben dagegen strikt exception-basiert, weil ein partial result für einen einzelnen Zielpfad keinen Mehrwert liefert.

### (e) Architektur

Neue Implementierungseinheit:
- Datei: `mcp_email_server/emails/mailbox.py`

Darin liegen genau zwei operative Klassen:

1. `MailboxOps`
2. `EmailOps`

`MailboxOps` Verantwortlichkeiten:
- IMAP-Verbindung öffnen, authentifizieren und sauber ausloggen für Mailbox-Operationen
- Delimiter-Erkennung und Cache
- `to_imap_path()` / `from_imap_path()`
- `list_mailboxes`
- `create_mailbox`
- `rename_mailbox`
- `delete_mailbox`
- `get_mailbox_status`

`EmailOps` Verantwortlichkeiten:
- IMAP-Verbindung öffnen, authentifizieren und sauber ausloggen für Bulk-Message-Operationen
- Source-Mailbox selektieren
- `move_emails`
- `copy_emails`
- `mark_emails`

Zusammenarbeit:
- `EmailOps` erhält im Konstruktor eine `MailboxOps`-Instanz und benutzt deren gecachten Delimiter sowie deren Pfadübersetzung.
- Dadurch gibt es genau eine Stelle für Namespace- und Delimiter-Logik.

`ClassicEmailHandler`:
- behält `EmailClient` für bestehende Funktionen wie Metadaten, Content, Delete, Download und Send.
- ergänzt zwei neue Delegate-Felder:

```python
self.mailbox_ops = MailboxOps(email_settings.incoming)
self.email_ops = EmailOps(email_settings.incoming, self.mailbox_ops)
```

- neue Handler-Methoden delegieren direkt an diese Objekte.

`EmailHandler` in `mcp_email_server/emails/__init__.py` wird erweitert um:
- `list_mailboxes`
- `create_mailbox`
- `rename_mailbox`
- `delete_mailbox`
- `get_mailbox_status`
- `move_emails`
- `copy_emails`
- `mark_emails`

Zusätzlich wird die bestehende abstrakte Signatur von `get_emails_metadata()` erweitert um:
- `body_contains: str | None = None`
- `text: str | None = None`

`ClassicEmailHandler.get_emails_metadata()` und die bestehenden Pfade in `EmailClient` werden entsprechend durchverdrahtet:
- `body_contains` wird an `_build_search_criteria(body=body_contains, ...)` gereicht
- `text` wird an `_build_search_criteria(text=text, ...)` gereicht

Es wird kein weiterer Abstraktionslayer eingeführt. `MailboxOps` und `EmailOps` sind die vollständige neue Schicht.

## Sektion 4: Teststrategie

### Grundsatz

Die Teststrategie bleibt konsistent mit dem existierenden Projekt:
- keine echten Server
- Mocking auf `aioimaplib`-Ebene
- klare Assertions auf IMAP-Befehle
- Assertions auf konkrete Pydantic-Modelle oder Strings

Es werden ausdrücklich keine Integrationstests gegen reale IMAP-Server ergänzt.

### `tests/test_mailbox_ops.py`

Neue Unit-Test-Datei für `MailboxOps`.

Struktur:
- eine Testklasse pro Hauptbereich
- `setUp()` erstellt `EmailServer`, `MailboxOps` und einen vollständig gemockten IMAP-Client
- `imap._client_task`, `wait_hello_from_server`, `login`, `logout` und die relevanten IMAP-Kommandos werden dort vorbereitet

Empfohlene Klassen:
- `TestMailboxOpsDelimiterDetection`
- `TestMailboxOpsListing`
- `TestMailboxOpsMutation`
- `TestMailboxOpsStatus`

Pflichtfälle:

`TestMailboxOpsDelimiterDetection`
- normaler Delimiter `"."` wird aus der ersten `LIST`-Zeile geparst und gecacht
- `NIL` wird als `""` gecacht
- Prefix-Namespace-Fall, z. B. `INBOX.Archive`, wird nicht gestrippt; `from_imap_path()` ergibt `INBOX/Archive`
- wiederholter Zugriff nutzt den Cache und sendet `LIST "" "*"` nur einmal

`TestMailboxOpsListing`
- `list_mailboxes(..., subscribed_only=False)` sendet `LIST`
- `list_mailboxes(..., subscribed_only=True)` sendet `LSUB`
- Rückgabe ist `list[MailboxInfo]`
- Assertions auf `path`, `delimiter`, `flags`, `subscribed`
- Pattern mit `/` wird korrekt in den Server-Delimiter übersetzt

`TestMailboxOpsMutation`
- `create_mailbox` sendet `CREATE "<translated path>"`
- `rename_mailbox` sendet `RENAME "<old>" "<new>"`
- Parent-Missing-Fall bei `rename_mailbox` propagiert den IMAP-Fehler
- `delete_mailbox(confirm=False)` wirft sofort ohne IMAP-Call
- `delete_mailbox(confirm=True)` sendet `DELETE "<translated path>"`

`TestMailboxOpsStatus`
- `get_mailbox_status` verwendet `EXAMINE` plus `STATUS`
- Counts werden korrekt aus der `STATUS`-Antwort geparst
- `FLAGS` und `PERMANENTFLAGS` werden aus der `EXAMINE`-Antwort übernommen

### `tests/test_email_ops.py`

Neue Unit-Test-Datei für `EmailOps`.

Struktur:
- eine Testklasse pro Hauptbereich
- `setUp()` erstellt `EmailServer`, `MailboxOps`, `EmailOps` und einen gemockten IMAP-Client
- Delimiter-Cache wird im Test entweder explizit vorbelegt oder über eine gemockte `LIST`-Antwort aufgebaut

Empfohlene Klassen:
- `TestEmailOpsMove`
- `TestEmailOpsCopy`
- `TestEmailOpsMark`

Pflichtfälle:

`TestEmailOpsMove`
- nativer MOVE-Erfolg:
  - `uid("MOVE", uid_set, quoted_destination)` wird gesendet
  - Rückgabe enthält pro UID `MovedEmail(..., success=True, method="native")`
- Fallback-Pfad:
  - erster MOVE-Versuch liefert `BAD` oder `NO`
  - danach pro UID `UID COPY` und `UID STORE +FLAGS (\Deleted)`
  - abschließend genau ein `EXPUNGE`
  - Rückgabe pro UID `method="fallback"`
- partielle Fehler:
  - Copy-Fehler für einzelne UID
  - Store-Fehler für einzelne UID
  - Expunge-Fehler nach mehreren tentativen Erfolgen
  - keine Rollback-Assertion; stattdessen Assertion auf Fehlertext im Resultat

`TestEmailOpsCopy`
- pro UID `UID COPY`
- vollständiger Erfolg
- partieller Fehler bei einer UID bei fortgesetzter Verarbeitung der restlichen UIDs
- keine `EXPUNGE`-Aufrufe
- Rückgabe ist `list[CopiedEmail]`

`TestEmailOpsMark`
- jede Flag-Kombination abdecken:
  - nur `seen=True`
  - nur `seen=False`
  - nur `flagged=True`
  - nur `flagged=False`
  - nur `answered=True`
  - nur `answered=False`
  - gemischte Kombinationen wie `seen=True, flagged=False, answered=True`
- Assertion auf die tatsächlich gesendeten `UID STORE`-Kommandos
- partielle Fehler pro UID
- Validierungsfehler wenn alle Flag-Argumente `None` sind

### Erweiterung von `tests/test_mcp_tools.py`

`tests/test_mcp_tools.py` wird im bestehenden Mocking-Stil erweitert:
- `patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler)`
- `AsyncMock` für Handler-Methoden
- Assertions auf Call-Args und Rückgabetypen

Neue MCP-Tool-Tests:
- `test_list_mailboxes`
- `test_list_mailboxes_subscribed_only`
- `test_create_mailbox`
- `test_rename_mailbox`
- `test_delete_mailbox_requires_confirm`
- `test_delete_mailbox_confirmed`
- `test_get_mailbox_status`
- `test_move_emails`
- `test_copy_emails`
- `test_mark_emails`

Zusätzliche Anpassung am bestehenden Metadaten-Tool:
- bestehende `list_emails_metadata`-Tests werden erweitert, damit `body_contains` und `text` an `handler.get_emails_metadata(...)` durchgereicht werden
- mindestens ein Test soll explizit die neuen Parameter setzen und die exakten Keyword-Args prüfen

Assertions in `test_mcp_tools.py`:
- Single-target-Tools prüfen Success-Strings oder Exceptions
- Bulk-Tools prüfen konkrete Listen aus `MovedEmail`, `CopiedEmail`, `MarkedEmail`
- Fehler bei `delete_mailbox(confirm=False)` werden als direkte Exception auf Tool-Ebene geprüft

### Nicht-Ziele

Diese Testserie soll nicht abdecken:
- provider-spezifische End-to-End-Interoperabilität
- echte Server-Namespace-Besonderheiten außerhalb gemockter LIST-Responses
- Performance- oder Lasttests

Das Ziel ist deterministische Verifikation der IMAP-Befehlsfolge, der Pfadübersetzung und des Fehlervertrags.
