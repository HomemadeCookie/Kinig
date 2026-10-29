#!/usr/bin/env bash
# Build and flash the Kinig pod from any Linux laptop. One command:
#
#   bash scripts/flash_pod.sh                       # asks for Wi-Fi, finds the port, flashes
#   bash scripts/flash_pod.sh --from-nm "Magis" --from-nm "My Hotspot"
#   bash scripts/flash_pod.sh --port /dev/ttyUSB0 --monitor
#   bash scripts/flash_pod.sh --wifi                # rewrite secrets.h, then flash
#
# --from-nm copies a saved NetworkManager Wi-Fi password (asks for sudo if needed).
# Up to two networks: the pod joins whichever is in range. 2.4 GHz only.
# Wi-Fi goes in firmware/kinig_pod/secrets.h, which git ignores.
# PlatformIO is installed into firmware/.pio-venv if `pio` is not on PATH.

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
firmware="$root/firmware"
secrets="$firmware/kinig_pod/secrets.h"

port=""
monitor=0
rewrite=0
from_nm=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --monitor) monitor=1; shift ;;
    --wifi) rewrite=1; shift ;;
    --from-nm) from_nm+=("$2"); rewrite=1; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
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

# ---------------------------------------------------------------- Wi-Fi

c_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

nm_password() {
  local name="$1" value=""
  value="$(nmcli -s -g 802-11-wireless-security.psk connection show "$name" 2>/dev/null || true)"
  if [[ -z "$value" ]]; then
    value="$(sudo nmcli -s -g 802-11-wireless-security.psk connection show "$name" 2>/dev/null || true)"
  fi
  printf '%s' "$value"
}

write_secrets() {
  local ssids=() passwords=()
  if [[ ${#from_nm[@]} -gt 0 ]]; then
    for name in "${from_nm[@]:0:2}"; do
      local pass
      pass="$(nm_password "$name")"
      if [[ -z "$pass" ]]; then
        echo "No saved password for \"$name\" in NetworkManager."
        echo "Saved Wi-Fi profiles:"
        nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/ {print "  " $1}'
        exit 1
      fi
      ssids+=("$name"); passwords+=("$pass")
    done
  else
    local ssid pass
    read -r -p "Wi-Fi name the pod should join (2.4 GHz): " ssid
    read -r -s -p "Password: " pass; echo
    ssids+=("$ssid"); passwords+=("$pass")
    read -r -p "Backup Wi-Fi name, e.g. a phone hotspot (Enter to skip): " ssid
    if [[ -n "$ssid" ]]; then
      read -r -s -p "Password: " pass; echo
      ssids+=("$ssid"); passwords+=("$pass")
    fi
  fi
  umask 077
  {
    echo "#pragma once"
    echo "// Written by scripts/flash_pod.sh. Gitignored."
    echo "#define KINIG_WIFI_SSID \"$(c_escape "${ssids[0]}")\""
    echo "#define KINIG_WIFI_PASSWORD \"$(c_escape "${passwords[0]}")\""
    if [[ ${#ssids[@]} -gt 1 ]]; then
      echo "#define KINIG_WIFI_SSID_2 \"$(c_escape "${ssids[1]}")\""
      echo "#define KINIG_WIFI_PASSWORD_2 \"$(c_escape "${passwords[1]}")\""
    fi
  } > "$secrets"
  echo "Wrote firmware/kinig_pod/secrets.h for: ${ssids[*]}"
}

if [[ $rewrite -eq 1 || ! -f "$secrets" ]]; then
  write_secrets
else
  echo "Using existing firmware/kinig_pod/secrets.h (pass --wifi to change it)."
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
echo "Flashed. The pod now joins Wi-Fi, finds the hub by mDNS, and shows a pairing code."
echo "Start the hub on the hub laptop (bash scripts/run_hub.sh) and open /host."
if [[ $monitor -eq 1 ]]; then
  "$pio" device monitor --port "$port" --baud 115200
fi
