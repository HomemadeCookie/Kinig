#!/usr/bin/env bash
# Build and flash the Kinig pod from any Linux laptop. One command:
#
#   bash scripts/flash_pod.sh                       # finds the port, flashes
#   bash scripts/flash_pod.sh --port /dev/ttyUSB0 --monitor
#
# The pod hosts its own Wi-Fi (Kinig-Pod, set in firmware/kinig_pod/config.h),
# so there is no Wi-Fi password to enter.
# PlatformIO is installed into firmware/.pio-venv if `pio` is not on PATH.

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
firmware="$root/firmware"

port=""
monitor=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --monitor) monitor=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------- PlatformIO

if command -v pio >/dev/null 2>&1; then
  pio=pio
elif [[ -x "$firmware/.pio-venv/bin/pio" ]]; then
  pio="$firmware/.pio-venv/bin/pio"
else
  echo "PlatformIO not found. Installing it into firmware/.pio-venv (one time)."
  python3 -m venv "$firmware/.pio-venv"
  "$firmware/.pio-venv/bin/python" -m pip install -q -U pip platformio
  pio="$firmware/.pio-venv/bin/pio"
fi

# ---------------------------------------------------------------- port

if [[ -z "$port" ]]; then
  for candidate in /dev/ttyUSB* /dev/ttyACM*; do
    if [[ -e "$candidate" ]]; then port="$candidate"; break; fi
  done
fi
if [[ -z "$port" ]]; then
  echo "No ESP32 serial port found. Plug the pod in with a data cable."
  echo "If dmesg shows 'error -71', try another cable or USB port."
  exit 1
fi
if [[ ! -r "$port" || ! -w "$port" ]]; then
  group="$(stat -c %G "$port")"
  echo "No permission for $port. Run: sudo usermod -aG $group $USER, then log out and in."
  exit 1
fi

# ---------------------------------------------------------------- build + flash

echo "Flashing the pod on $port"
cd "$firmware"
"$pio" run -t upload --upload-port "$port"
echo
echo "Flashed. The pod now starts its own Wi-Fi, Kinig-Pod."
echo "On the hub laptop: join Kinig-Pod, run bash scripts/run_hub.sh, open /host."
if [[ $monitor -eq 1 ]]; then
  "$pio" device monitor --port "$port" --baud 115200
fi
