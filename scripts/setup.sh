#!/usr/bin/env bash
# Create the hub virtualenv, install Kinig, and download models.
# Linux only. Windows steps are in the README.
# Run from anywhere: bash scripts/setup.sh

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found."
  echo "Fedora:  sudo dnf install python3 python3-pip portaudio-devel"
  echo "Ubuntu:  sudo apt install python3 python3-venv python3-pip portaudio19-dev"
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required. This machine has:"
  python3 --version || true
  exit 1
fi

if ! python3 -c 'import venv' >/dev/null 2>&1; then
  echo "The venv module is missing."
  echo "Ubuntu:  sudo apt install python3-venv"
  echo "Fedora:  sudo dnf install python3"
  exit 1
fi

echo "Creating hub/.venv"
python3 -m venv hub/.venv
# shellcheck disable=SC1091
source hub/.venv/bin/activate

python -m pip install -U pip
if ! python -m pip install -e "hub[dev]"; then
  echo "pip install failed. On Linux, PortAudio headers are required for the microphone."
  echo "Fedora:  sudo dnf install portaudio-devel python3-devel gcc"
  echo "Ubuntu:  sudo apt install portaudio19-dev python3-dev gcc"
  exit 1
fi

echo "Downloading the Whisper weights and checking Ollama (needs a network)"
if ! python scripts/download_models.py; then
  echo "Model setup did not finish. Whisper weights or the Ollama model are missing."
  echo "Fix the message above, then run: hub/.venv/bin/python scripts/download_models.py"
  exit 1
fi

echo
echo "Setup finished. Next:"
echo "  scripts/run_hub.sh"
echo "  hub/.venv/bin/python scripts/fake_pod.py --file path/to/meeting.wav"
echo "The dev page stays off until dev_mode = true in hub/config/hub.toml."
