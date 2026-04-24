# facelessfilmpjes

Volledig autonome **multi-channel, multi-niche faceless video factory** voor TikTok
en YouTube Shorts.

```
┌───────────────────────────────────────────────────────────┐
│  FastAPI web UI (http://localhost:8000)                   │
│  ─ Channels / niches / schedules / jobs / live SSE status │
│  ─ YouTube OAuth connect flow per channel                 │
└────────────────────────────┬──────────────────────────────┘
                             │
                  ┌──────────┴──────────┐
                  │  SQLite state       │  channels, niches,
                  │                     │  schedules, jobs,
                  │                     │  oauth tokens
                  └──────────┬──────────┘
                             │
           ┌─────────────────┴─────────────────┐
           │   APScheduler + worker thread     │
           └─────────────────┬─────────────────┘
                             │
  ┌──────────────────────────┴──────────────────────────────┐
  │ Pipeline (runs one job, emits live status events)       │
  │                                                         │
  │  Idea        → Anthropic / OpenAI                       │
  │  Script      → Anthropic / OpenAI                       │
  │  Voice       → OpenAI TTS HD (or local fallback)        │
  │  Media       → Pexels / Pixabay (free)                  │
  │  Alignment   → Whisper (optional)                       │
  │  Render      → Creatomate (word-by-word captions)       │
  │  Caption     → Anthropic / OpenAI                       │
  │  Upload      → YouTube Data API v3 (Shorts)             │
  └─────────────────────────────────────────────────────────┘
```

## Features

- **Multi-channel** — elk channel heeft z'n eigen niche, voice, kleuren, font, upload-defaults
- **Multi-niche** — herbruikbare content-strategieën met custom LLM-instructies per niche
- **Live dashboard** — Server-Sent Events streamen job status (rendering/uploading/done) real-time
- **YouTube OAuth** — "Connect YouTube" knop per channel; refresh tokens automatisch beheerd
- **Creatomate rendering** — professionele 9:16 output met word-by-word animated captions, Ken Burns zoom, muziek
- **OpenAI TTS HD** — natuurlijke stem, instelbaar per channel (nova / onyx / shimmer / etc.)
- **Cron schedules** — per channel meerdere slots per dag
- **Auto-cleanup** vriendelijk voor kleine VPSen (1GB RAM, 10GB disk werkt)
- **Wachtwoord-beveiligde UI** — voor zolang je via SSH-tunnel draait, veilig genoeg
- **TikTok-ready hook** — ontwerp ondersteunt meerdere platforms; TikTok upload is fase 2

## Stack & kosten (indicatief)

| Dienst | Gebruik | ~Kosten |
|---|---|---|
| Anthropic Claude (of OpenAI) | scripts, ideeën, captions | €3-8/mnd |
| OpenAI TTS HD | voice-over | €5-15/mnd |
| Creatomate | video rendering | €8-25/mnd |
| Pexels / Pixabay | stock footage | gratis |
| Google Cloud | YouTube Data API v3 | gratis (10k units/dag) |
| VPS (1 GB / 1 core) | orchestratie + UI | €5/mnd |
| **Totaal bij 1-3 kanalen** | | **~€25-60/mnd** |

## Install op Ubuntu VPS (24.04)

Als root op een schone VPS:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/milantenhave/facelessfilmpjes/main/scripts/install-vps.sh)
```

Of manueel:

```bash
git clone https://github.com/milantenhave/facelessfilmpjes.git /opt/facelessfilmpjes
cd /opt/facelessfilmpjes
bash scripts/install-vps.sh
```

Dit doet:

1. Installeert Python, ffmpeg, git
2. Maakt een 2 GB swapfile aan (essentieel op 1 GB RAM)
3. Maakt een `faceless` user aan
4. Installeert de app in `/opt/facelessfilmpjes` + venv
5. Zet systemd service `facelessfilmpjes` op (auto-start bij reboot)
6. Initialiseert de SQLite DB

## Configureer `.env`

```bash
sudo -u faceless nano /opt/facelessfilmpjes/.env
```

Minimaal nodig:

```env
DASHBOARD_PASSWORD=een-stevig-wachtwoord
SESSION_SECRET=$(openssl rand -hex 32)
PUBLIC_BASE_URL=http://localhost:8000

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

TTS_ENGINE=openai
OPENAI_API_KEY=sk-...
OPENAI_TTS_VOICE=nova

CREATOMATE_API_KEY=...

PEXELS_API_KEY=...
PIXABAY_API_KEY=...

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

Start:

```bash
systemctl start facelessfilmpjes
journalctl -u facelessfilmpjes -f    # volg logs
```

## Verbinden vanaf je laptop (SSH tunnel)

```bash
ssh -L 8000:127.0.0.1:8000 root@jouw-vps-ip
```

Open je browser op **http://localhost:8000**, login met je DASHBOARD_PASSWORD.

## Google Cloud project opzetten (eenmalig)

1. Ga naar https://console.cloud.google.com/
2. Nieuw project → enable **YouTube Data API v3**
3. OAuth consent screen: type "External", publish status "Testing" volstaat
4. Credentials → **OAuth client ID** → type "Web application"
5. **Authorized redirect URI**: `http://localhost:8000/oauth/youtube/callback`
   - Als je later een domein gebruikt: voeg `https://jouw-domein.nl/oauth/youtube/callback` ook toe
6. Client ID + Client Secret in `.env` plakken
7. Bij OAuth consent: voeg jouw Google accounts toe als test users
8. Restart service: `systemctl restart facelessfilmpjes`

## In de dashboard

1. **Niches** tab → maak 1+ niche aan (bijv. "finance_tips", "deep_facts")
2. **Channels** tab → nieuw channel → kies platform=youtube, kies niche, voice, kleur
3. Klik **"Connect YouTube"** → Google login → terug op dashboard = connected ✓
4. **Schedules** tab → voor elk channel cron toevoegen (bv. `0 9,15,20 * * *` = 3x/dag)
5. Klik **"Run now"** op een channel om direct een video te produceren en testen

De worker pakt automatisch jobs op, je ziet de status live op `/dashboard` en `/jobs`.

## Lokaal dev draaien

```bash
./scripts/setup.sh                       # venv + deps
source .venv/bin/activate
python -m src init-db
python -m src seed
python -m src web --reload               # http://localhost:8000
```

## Architectuur op een rijtje

- `src/web/` — FastAPI app, Jinja2 templates, HTMX, SSE
- `src/db/` — SQLAlchemy models (Channel, Niche, Schedule, Job, OAuthToken)
- `src/worker/` — APScheduler + serial worker thread
- `src/llm/` — abstract provider (Anthropic / OpenAI / Ollama / template fallback)
- `src/voice_generator/` — OpenAI TTS HD + local fallback
- `src/media_fetcher/` — Pexels + Pixabay (met remote URL voor cloud renderer)
- `src/subtitle_generator/` — Whisper alignment of even split
- `src/video_editor/` — **CreatomateRenderer** (default) + lokale FFmpeg renderer
- `src/uploader/` — YouTube Data API v3 + OAuth flow
- `src/events.py` — in-process pub/sub voor live status via SSE

## Fase 2 (nog te doen)

- **TikTok Content Posting API** — zelfde OAuth-patroon, nieuwe `src/uploader/tiktok.py`.
  Vereist dat jij eerst een TikTok Developer app + app review doet.
- **Analytics feedback loop** — per-video metrics uit YouTube Analytics API terugvoeden
  naar idea generation.
- **Thumbnail generator** (YouTube-specifiek).
- **A/B testing UI** — variant winners promoten naar default.

## Troubleshooting

| Probleem | Oplossing |
|---|---|
| `OAuth error: redirect_uri_mismatch` | Voeg exact jouw `PUBLIC_BASE_URL + /oauth/youtube/callback` toe in Google Cloud credentials |
| Rendering faalt met "Creatomate key not set" | Zet `CREATOMATE_API_KEY` in `.env`, restart service |
| Job blijft op `pending` hangen | Check `journalctl -u facelessfilmpjes -f` — meestal ontbreekt een API key |
| Out of memory op 1GB VPS | Zorg dat `scripts/install-vps.sh` de swapfile heeft aangemaakt |
| Video's vullen de disk | Voeg cron toe: `0 3 * * * find /opt/facelessfilmpjes/videos -mtime +7 -delete` |

## License

MIT.
