#!/usr/bin/env bash
# Checks that are safe to run before a demo. Exit 1 if a demo flag is still on.

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [[ -x hub/.venv/bin/python ]]; then
  py=hub/.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  py=python3
else
  echo "python3 was not found. Run scripts/setup.sh."
  exit 1
fi

"$py" - <<'PY'
import socket
import sys

try:
    from kinig_hub.config import hub_dir, load_config, resolved_public_host
except ImportError:
    print("Cannot import kinig_hub. From the repo root run scripts/setup.sh")
    sys.exit(1)

config = load_config()
problems = []

if config.hub.get("dev_mode"):
    problems.append("dev_mode is true. Set it to false in hub/config/hub.toml for the demo.")
if config.hub.get("dev_https"):
    problems.append("dev_https is true. Set it to false in hub/config/hub.toml for the demo.")
if config.pod.get("debug_save_audio"):
    problems.append("debug_save_audio is true. Audio would be written to disk. Set it to false.")

host = resolved_public_host(config.hub)
if host.startswith("127.") or host in {"localhost", "auto", "TBD", ""}:
    problems.append(
        f"public_host resolved to {host!r}. Phones cannot open that. "
        "Set public_host in hub/config/hub.toml to this laptop's LAN IP."
    )
else:
    print(f"Notes URLs will use http://{host}:{config.hub['http_port']}")

weights = hub_dir() / "model_files" / "whisper"
if not weights.exists() or not any(weights.rglob("*")):
    problems.append("Whisper weights are missing. Run scripts/download_models.py while online.")
else:
    print(f"Whisper weights found under {weights.relative_to(hub_dir().parent)}")

try:
    from kinig_hub.models.summarizers.ollama import OllamaSummarizer

    ready, detail = OllamaSummarizer.check_ready(config.models["summarizer"]["options"])
    if ready:
        print(f"Ollama is ready ({detail})")
    else:
        problems.append(detail)
except Exception as exc:
    problems.append(f"Could not check Ollama: {exc}")

port = int(config.hub["http_port"])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    print(f"Port {port} is already in use. That is fine if the hub is already running.")
finally:
    sock.close()

try:
    import sounddevice as sd

    devices = sd.query_devices()
    inputs = [d for d in devices if d["max_input_channels"] > 0]
    if inputs:
        print(f"Microphone devices: {len(inputs)}")
    else:
        print("No microphone device was found. fake_pod.py --mic will not record.")
except Exception as exc:
    print(f"Microphone check skipped ({exc}). --mic needs PortAudio.")

if problems:
    print()
    print("Demo check failed:")
    for item in problems:
        print(f"  - {item}")
    sys.exit(1)

print("Demo check passed.")
PY
