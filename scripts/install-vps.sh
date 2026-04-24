#!/usr/bin/env bash
# Install facelessfilmpjes on a fresh Ubuntu 24.04 VPS.
# Run as root: bash scripts/install-vps.sh
set -euo pipefail

APP_USER="${APP_USER:-faceless}"
APP_DIR="${APP_DIR:-/opt/facelessfilmpjes}"
WEB_PORT="${WEB_PORT:-8000}"

echo "==> updating apt"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ffmpeg git ca-certificates curl tzdata

echo "==> creating 2GB swap (safe for 1GB-RAM VPS)"
if ! swapon --show | grep -q '/swapfile'; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
    sysctl vm.swappiness=20 || true
    echo "vm.swappiness=20" > /etc/sysctl.d/90-faceless.conf
fi

echo "==> creating app user ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "${APP_USER}"
fi

echo "==> installing app into ${APP_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
    git clone https://github.com/milantenhave/facelessfilmpjes.git "${APP_DIR}"
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

sudo -u "${APP_USER}" bash -c "
    cd '${APP_DIR}'
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip wheel
    pip install -r requirements.txt
    mkdir -p data videos logs .cache
    [ -f .env ] || cp .env.example .env
    [ -f config/config.yaml ] || cp config/config.example.yaml config/config.yaml
    .venv/bin/python -m src init-db
    .venv/bin/python -m src seed || true
"

echo "==> installing systemd service"
cat > /etc/systemd/system/facelessfilmpjes.service <<EOF
[Unit]
Description=facelessfilmpjes web UI + worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m src web --host 127.0.0.1 --port ${WEB_PORT}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable facelessfilmpjes

cat <<EOF

============================================================
Installed. Next steps:

1. Edit secrets:
     sudo -u ${APP_USER} nano ${APP_DIR}/.env

   Set at least:
     - DASHBOARD_PASSWORD
     - SESSION_SECRET   (long random string)
     - ANTHROPIC_API_KEY   (or OPENAI_API_KEY)
     - OPENAI_API_KEY      (for TTS)
     - CREATOMATE_API_KEY
     - PEXELS_API_KEY / PIXABAY_API_KEY
     - GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET

2. Start the service:
     systemctl start facelessfilmpjes
     systemctl status facelessfilmpjes

3. From your laptop, open a tunnel:
     ssh -L ${WEB_PORT}:127.0.0.1:${WEB_PORT} root@<your-vps-ip>

   Then visit http://localhost:${WEB_PORT} in your browser.

4. In the dashboard:
     - Create a niche (or use the seeded one)
     - Create a YouTube channel → click "Connect YouTube" to OAuth
     - Add a schedule (cron)
     - Click "Run now" to test a first video

Logs:
     journalctl -u facelessfilmpjes -f
============================================================
EOF
