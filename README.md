# facelessfilmpjes

Fully automated pipeline for generating **faceless short-form videos** (TikTok,
Instagram Reels, YouTube Shorts) — ideas → script → voiceover → visuals →
edited 9:16 video → subtitles → captions → upload-ready bundle.

No paid SaaS is required: every stage has a free / offline fallback so the
pipeline runs end-to-end on a laptop or VPS out of the box.

```
scheduler ─▶ idea_generator ─▶ script_generator ─▶ voice_generator
                                      │                  │
                                      ▼                  ▼
                              caption_generator     media_fetcher
                                      │                  │
                                      └──────▶ video_editor ◀── subtitle_generator
                                                       │
                                                       ▼
                                                   uploader ─▶ analytics
```

## Features

- **Idea generation** — 5–10 viral ideas per niche with hook, topic, emotion and
  angle, optionally steered by past analytics winners.
- **Script generation** — hook / body / payoff / CTA, tuned for 15–30s retention.
- **Voice synthesis** — pluggable TTS: Coqui → pyttsx3 → espeak → silent
  fallback.
- **Stock footage** — Pexels + Pixabay with on-disk cache; solid-colour PNG
  fallback when offline.
- **Video editor** — FFmpeg-based 9:16 composer with fade transitions, Ken
  Burns on stills, optional background music.
- **Subtitles** — word-level Whisper alignment when available; even split
  fallback; burned-in with configurable style.
- **Captions** — title, description, and optimised hashtag set per video.
- **Uploader** — dry-run by default, with pluggable platform hooks for TikTok /
  Reels / YouTube Shorts.
- **Analytics** — SQLite, feedback loop that surfaces winning hook patterns
  back to the idea generator.
- **A/B testing** — optional multiple script variants per idea.
- **Scheduler** — in-process (daily/hourly/custom) + cron & systemd examples.
- **Dashboard** — minimal Flask UI to preview videos and JSON analytics.
- **CLI** — `run`, `schedule`, `ideas`, `analytics`, `dashboard`.

## Install

```bash
git clone https://github.com/milantenhave/facelessfilmpjes.git
cd facelessfilmpjes
./scripts/setup.sh
source .venv/bin/activate
```

`setup.sh` creates `.venv`, installs `requirements.txt`, copies
`config/config.example.yaml` → `config/config.yaml`, and copies `.env.example`
→ `.env`. Install `ffmpeg` system-wide if you can; otherwise `imageio-ffmpeg`
is used as a fallback.

### Optional: API keys

Edit `.env`:

```env
LLM_PROVIDER=anthropic           # or openai / ollama / template
ANTHROPIC_API_KEY=sk-ant-...
PEXELS_API_KEY=...
PIXABAY_API_KEY=...
TTS_ENGINE=coqui                 # or pyttsx3 / espeak
```

If none are set, the system still works using the built-in template generator,
silent TTS, and a solid-colour background — useful for validating wiring.

## Usage

### One-shot run

```bash
python -m src run            # honours videos_per_run in config
python -m src run -n 5       # override to 5 videos
```

### Scheduled

```bash
python -m src schedule       # in-process scheduler
```

Or use cron (`scripts/cron.example`) / systemd (`scripts/systemd.example.service`).

### Just ideas

```bash
python -m src ideas -n 10
```

### Dashboard

```bash
python -m src dashboard --host 0.0.0.0 --port 8765
```

### Dump analytics

```bash
python -m src analytics --out analytics.json
```

## Output layout

```
videos/
└── 2026-04-24/
    ├── audio/        # per-video .wav voiceovers
    ├── scripts/      # per-video .txt (json dump) scripts
    ├── media/        # per-video stock-footage working dir
    ├── subtitles/    # per-video .srt files
    ├── video_01.mp4  # rendered 1080x1920 video
    └── video_01.json # upload-ready metadata (title, description, hashtags)
```

`video_01.json` example:

```json
{
  "video": "videos/2026-04-24/video_01.mp4",
  "title": "Stop scrolling. This one thing about self improvement will change your life.",
  "description": "A 25-second deep dive...\n\nFollow for one uncomfortable truth per day.",
  "hashtags": ["#selfimprovement", "#shorts", "#reels", "#fyp", "#mindset"],
  "script": { "hook": "...", "body": "...", "payoff": "...", "cta": "...", "...": "..." },
  "dry_run": true,
  "platforms": ["tiktok", "instagram", "youtube"]
}
```

## Configuration

All content and video knobs live in `config/config.yaml` (see
`config/config.example.yaml`). Highlights:

| Path | Purpose |
| ---- | ------- |
| `niches` | list of `{name, tone, emotions, weight}` — drives idea generation |
| `videos_per_run`, `ideas_per_niche` | throughput |
| `video_length_seconds` | script pacing target |
| `llm.provider` | `template` / `openai` / `anthropic` / `ollama` |
| `tts.engine` | `pyttsx3` / `coqui` / `espeak` (auto-fallback to silence) |
| `media.providers` | `[pexels, pixabay]` — in priority order |
| `video.resolution` / `fps` / `crf` | final render settings |
| `subtitles.mode` | `word` (TikTok style) or `sentence` |
| `ab_testing.enabled` | generate multiple script variants per idea |
| `analytics.feedback_loop` | surface winning patterns back into idea prompts |

## Running on a VPS

```bash
# as root
adduser --disabled-password faceless
su - faceless
git clone https://github.com/milantenhave/facelessfilmpjes.git
cd facelessfilmpjes
./scripts/setup.sh

# option 1: cron
crontab -e    # see scripts/cron.example

# option 2: systemd (recommended)
sudo cp scripts/systemd.example.service /etc/systemd/system/facelessfilmpjes.service
sudo systemctl daemon-reload
sudo systemctl enable --now facelessfilmpjes
```

## Testing

```bash
pip install pytest
pytest -q
```

The tests use the template LLM and silent TTS by default — no API keys
required. The end-to-end pipeline test is skipped if ffmpeg isn't available.

## Extending

- **New LLM provider** — implement `src.llm.base.LLMProvider.complete` and
  register it in `src/llm/factory.py`.
- **New TTS engine** — add a method on `VoiceGenerator` and include it in the
  `attempts` list in `synthesize`.
- **New upload target** — subclass `uploader.uploader.PlatformHook` and add it
  to `_HOOKS`.

## License

MIT (see `LICENSE`). Third-party media you fetch is subject to Pexels / Pixabay
terms of use.
