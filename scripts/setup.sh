#!/usr/bin/env bash
# Bootstrap a Linux/macOS environment for facelessfilmpjes.
set -euo pipefail

PYTHON="${PYTHON:-python3}"

echo "==> creating virtualenv (./.venv)"
$PYTHON -m venv .venv
source .venv/bin/activate

echo "==> upgrading pip"
python -m pip install --upgrade pip wheel

echo "==> installing Python requirements"
pip install -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
    cat <<EOF
[!] ffmpeg is not installed system-wide.
    Linux  : sudo apt-get install -y ffmpeg
    macOS  : brew install ffmpeg
    The project will try imageio-ffmpeg as a fallback, but installing a system
    ffmpeg is strongly recommended for better codec support.
EOF
fi

if [ ! -f config/config.yaml ]; then
    cp config/config.example.yaml config/config.yaml
    echo "==> created config/config.yaml (edit it to taste)"
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> created .env (add API keys you want to use)"
fi

echo "==> done. activate with: source .venv/bin/activate"
echo "==> run: python -m src run"
