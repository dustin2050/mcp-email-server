# MCP-Host Hetzner — Runbook

Hetzner-Server, der mehrere MCP-Server hinter einem gemeinsamen Reverse-Proxy
(Caddy mit Auto-HTTPS) betreibt. Jeder MCP läuft als eigener Docker-Compose-Stack
unter `/opt/mcp/<name>/`.

---

## 1. Schnellübersicht

| Item | Wert |
|---|---|
| Host / IP | `46.224.172.80` |
| OS | Ubuntu 26.04 LTS |
| Resources | 2 vCPU · 3.7 GiB RAM · 1.5 GiB Swap · 38 GB Disk |
| Reverse Proxy | Caddy 2 (Container `caddy`) |
| Shared Docker Network | `mcp_net` (external) |
| Firewall | UFW: 22 / 80 / 443 |
| Auto-Updates | `unattended-upgrades` (security) |
| SSH-Schutz | `fail2ban` |

### Aktuell deployte MCPs

| Subdomain | Pfad | Container | Internal Port | Image-Größe |
|---|---|---|---|---|
| `email-mcp.not4everybody.de` | `/opt/mcp/email/` | `mcp-email` | 9557 | ~1.05 GB (inkl. tesseract OCR) |

---

## 2. Verzeichnislayout

```
/opt/mcp/
├── README.md                  ← dieses Runbook
├── _proxy/                    ← Caddy-Stack (TLS-Termination + Routing)
│   ├── docker-compose.yml
│   └── Caddyfile              ← ein Block pro Subdomain
└── <mcp-name>/                ← ein Verzeichnis pro MCP
    ├── docker-compose.yml
    ├── .env                   ← Secrets, chmod 600
    └── src/                   ← Git-Clone des MCP-Repos (falls Build aus Source)
```

Konvention: **ein Container pro MCP, eindeutiger Container-Name = `mcp-<kurzname>`.**
Caddy adressiert Container über diesen Namen im `mcp_net`-Network.

---

## 3. Häufige Operationen

Alle Kommandos als `root` (oder via `sudo`).

### Status aller MCPs

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

### Logs eines MCP

```bash
cd /opt/mcp/<name> && docker compose logs -f --tail=100
```

### Neustart eines MCP

```bash
cd /opt/mcp/<name> && docker compose restart
```

### MCP-Code aktualisieren (Build aus Source)

```bash
cd /opt/mcp/<name>/src && git pull
cd /opt/mcp/<name> && docker compose build && docker compose up -d
```

### Caddy-Konfig neu laden (ohne Downtime)

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

### Caddy-Logs (TLS-Probleme, Access)

```bash
docker logs -f caddy
```

### Disk / RAM / Swap prüfen

```bash
df -h /; free -h; docker system df
```

### Aufräumen alter Docker-Images

```bash
docker image prune -af   # entfernt nicht referenzierte Images
docker system prune -af  # zusätzlich Build-Cache und unused volumes (Vorsicht!)
```

---

## 4. Neuen MCP hinzufügen

Rezept in 5 Schritten:

### 4.1 DNS

A-Record `<name>-mcp.not4everybody.de` → `46.224.172.80` setzen,
auf Propagation warten (`getent hosts <name>-mcp.not4everybody.de`).

### 4.2 Verzeichnis + Compose

```bash
mkdir -p /opt/mcp/<name>
cd /opt/mcp/<name>
```

Lege `docker-compose.yml` an. Beispiel-Skelett:

```yaml
services:
  mcp-<name>:
    image: <image-tag>       # oder build: { context: ./src }
    container_name: mcp-<name>
    restart: unless-stopped
    env_file:
      - .env
    expose:
      - "<internal-port>"
    networks:
      - mcp_net

networks:
  mcp_net:
    external: true
```

**Wichtig:**
- Kein `ports:`-Mapping nach außen — Caddy terminiert TLS und proxied intern.
- `expose:` reicht (macht Port nur innerhalb des Docker-Netzwerks sichtbar).
- Container muss auf `0.0.0.0:<internal-port>` lauschen, nicht nur localhost.

### 4.3 Secrets

```bash
touch /opt/mcp/<name>/.env
chmod 600 /opt/mcp/<name>/.env
# dann Variablen reinschreiben (siehe Abschnitt 6)
```

### 4.4 Caddyfile erweitern

In `/opt/mcp/_proxy/Caddyfile` Block anhängen:

```caddyfile
<name>-mcp.not4everybody.de {
    encode zstd gzip
    reverse_proxy mcp-<name>:<internal-port>
}
```

### 4.5 Starten + Caddy reloaden

```bash
cd /opt/mcp/<name> && docker compose up -d
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Smoketest:

```bash
curl -I https://<name>-mcp.not4everybody.de/
docker exec caddy wget -qO- http://mcp-<name>:<internal-port>/healthz
```

---

## 5. MCP entfernen

```bash
cd /opt/mcp/<name> && docker compose down -v
rm -rf /opt/mcp/<name>
# Caddyfile-Block entfernen, dann:
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

DNS-Record kann bestehen bleiben oder entfernt werden — wenn Caddy keinen
Block dafür hat, antwortet er nicht.

---

## 6. Email-MCP (`/opt/mcp/email/`)

### Quelle

Geklontes Fork-Repo: `https://github.com/dustin2050/mcp-email-server.git`
unter `/opt/mcp/email/src/`. Build via `Dockerfile` aus dem Repo
(uv-basiertes Python 3.12 Image + tesseract-ocr + Sprachdaten deu/eng).

### Update

```bash
cd /opt/mcp/email/src && git pull
cd /opt/mcp/email && docker compose build && docker compose up -d
```

### Variablen (`/opt/mcp/email/.env`)

| Variable | Zweck |
|---|---|
| `MCP_EMAIL_SERVER_ACCOUNT_NAME` | logischer Account-Name (`gmx`) |
| `MCP_EMAIL_SERVER_EMAIL_ADDRESS` | Mail-Adresse |
| `MCP_EMAIL_SERVER_FULL_NAME` | Display-Name |
| `MCP_EMAIL_SERVER_IMAP_HOST/PORT/SSL` | IMAP-Endpoint |
| `MCP_EMAIL_SERVER_USER_NAME` | Login-User |
| `MCP_EMAIL_SERVER_PASSWORD` | App-Passwort (GMX) |
| `MCP_EMAIL_SERVER_SMTP_HOST/PORT/SSL/START_SSL` | SMTP-Endpoint |
| `MCP_EMAIL_SERVER_SAVE_TO_SENT` | gesendete Mails in IMAP-Sent ablegen |
| `MCP_HOST` / `MCP_PORT` | Listen-Bind im Container (`0.0.0.0:9557`) |
| `MCP_PUBLIC_URL` | für OAuth-Issuer & Redirect-Generierung (`https://email-mcp.not4everybody.de`) |
| `MCP_OAUTH_CLIENT_ID` | OAuth-Client-ID (`claude-desktop`) |
| `MCP_OAUTH_CLIENT_SECRET` | OAuth-Client-Secret |
| `MCP_ENABLE_DNS_REBINDING_PROTECTION` | `false` (Caddy proxied) |

### Attachment-Handling

Zwei Tools für Anhänge:

| Tool | Wann sinnvoll | Output |
|---|---|---|
| `get_attachment` | **Standard-Fall** für Remote-Clients (Claude.ai / Claude Desktop) | MCP-Content-Block (inline im JSON-Response) |
| `download_attachment` | Nur lokaler `stdio`-Modus (MCP läuft auf derselben Maschine wie Client) | Speichert Datei auf Container-Disk, gibt Pfad zurück. Gated hinter `enable_attachment_download=true` |

**`get_attachment` Modi** (Parameter `mode`, Default `auto`):

| Mode | Verhalten |
|---|---|
| `auto` | Server entscheidet pro MIME: PDF → Text-Layer, sonst OCR-Fallback; Bilder → `ImageContent`; `text/*` → dekodiert; Rest → Blob |
| `text` | Erzwingt Text-Output (PDF-Layer → OCR → Fehler; Bilder → OCR; `text/*` → dekodiert) |
| `ocr` | Erzwingt tesseract-OCR auf PDFs und Bildern |
| `raw` | Immer Base64-Blob (`EmbeddedResource`), Bilder als `ImageContent` |

**OCR-Eigenschaften** (im Code als Konstanten `_OCR_LANG`, `_OCR_MAX_PAGES`, `_OCR_DPI` in `mcp_email_server/app.py`):

- Sprachen: `deu+eng`
- Cap: 50 Seiten pro PDF
- Rendering-DPI: 200
- Stack: `pypdf` (Text-Layer) → `pypdfium2` (Page-Rastern) → `pytesseract` → `tesseract` (System-Binary)

**Größenlimits:** `get_attachment` cap = 20 MiB Roh-Anhang. Größere Anhänge erfordern `download_attachment`.

**Wenn OCR-Qualität schlecht ist:** DPI hochziehen (z. B. 300) oder zusätzliche Sprachpakete im Dockerfile installieren (`tesseract-ocr-fra`, `tesseract-ocr-ita`, …).

### Endpoints

| Pfad | Funktion |
|---|---|
| `/mcp/` | Streamable-HTTP-Endpoint (Claude.ai-Connector) |
| `/.well-known/oauth-authorization-server` | OAuth-Discovery |
| `/authorize`, `/token`, `/revoke` | OAuth-Endpoints |
| `/healthz` | Health-Check |

### Claude.ai-Connector verbinden

Connector-URL: `https://email-mcp.not4everybody.de/mcp`

Claude.ai zieht die OAuth-Discovery automatisch, führt dich durch den
Authorization-Code-Flow und speichert das Refresh-Token.

---

## 7. TLS / Caddy

- Auto-HTTPS via Let's Encrypt (`tls-alpn-01`-Challenge).
- Zertifikate persistieren im Docker-Volume `proxy_caddy_data`.
- ACME-Account-E-Mail: `admin@not4everybody.de` (im `Caddyfile` änderbar).
- Renewal läuft automatisch ~30 Tage vor Ablauf.
- Nach Caddyfile-Änderung **immer** `docker exec caddy caddy reload …` —
  ein `restart` würde alle MCPs kurz offline nehmen.

---

## 8. Sicherheit

- **Firewall (UFW):** nur 22/80/443 inbound.
- **fail2ban:** Standard-Jail für SSH aktiv.
- **unattended-upgrades:** Security-Patches automatisch.
- **Secrets:** `.env`-Dateien mit `chmod 600`, nicht in Git committen.
- **SSH:** aktuell Passwort-Login als root. **Empfehlung:** SSH-Key
  hinterlegen und `PasswordAuthentication no` setzen.

---

## 9. Backup-Empfehlung

Was sich zu sichern lohnt:

```
/opt/mcp/                     # gesamte Config, Compose-Files, .env
/var/lib/docker/volumes/      # Caddy-Cert-Storage, ggf. MCP-State-Volumes
```

Beispiel (täglicher Tarball nach `/root/backup/`):

```bash
mkdir -p /root/backup
tar -czf /root/backup/mcp-$(date +%F).tar.gz \
  /opt/mcp \
  /var/lib/docker/volumes/proxy_caddy_data
```

Via Cron in `/etc/cron.daily/` oder Hetzner-Snapshot-Funktion.

---

## 10. Troubleshooting

| Symptom | Diagnose | Fix |
|---|---|---|
| 502 von Caddy | `docker logs caddy` — sieht "no such host" → Container-Name stimmt nicht mit Caddyfile überein | Container-Name oder `reverse_proxy`-Target korrigieren |
| 502 dauerhaft | `docker ps` — Container down | `docker compose logs` im MCP-Verzeichnis lesen |
| TLS schlägt fehl | Caddy-Log "tls-alpn-01 failed" | DNS-A-Record prüfen, Port 80/443 frei? UFW prüfen |
| OAuth-Discovery 404 | `MCP_PUBLIC_URL` falsch | `.env` korrigieren, `docker compose up -d` |
| Connector verbindet nicht | `/mcp` antwortet 401 | normal — Claude.ai muss OAuth-Flow durchlaufen |
| Container OOM-Kill | `dmesg \| grep -i oom` | RAM-fressenden MCP isolieren, Swap erhöhen, ggf. Server upgraden |
| Disk voll | `docker system df` | `docker image prune -af`, alte Logs rotieren |

---

## 11. Historie

- **2026-05-19** — Initialer Setup, Migration `mcp-email-server` von Railway
  zu Hetzner. Server gehärtet (UFW, fail2ban, Swap, unattended-upgrades),
  Docker + Compose installiert, Caddy-Reverse-Proxy mit Auto-HTTPS,
  Email-MCP als erster Tenant unter `email-mcp.not4everybody.de`.
- **2026-05-20** — Anhang-Handling für Remote-Clients ergänzt:
  - Neues Tool `get_attachment` liefert Anhänge inline als MCP-Content-Block
    (statt nur auf Container-Disk wie `download_attachment`).
  - PDF-Text-Extraction via `pypdf`.
  - OCR-Fallback für gescannte PDFs und Bilder via `tesseract` (deu+eng),
    `pypdfium2` für Page-Rastern. Image-Größe dadurch von ~700 MB auf ~1.05 GB.
  - Modes: `auto` (Default), `text`, `ocr`, `raw`.
