#!/usr/bin/env bash
# Make a local HTTPS certificate for phone recording on port 8443.
# Notes pages stay on HTTP port 8000. Run this again if the laptop's IP changes.
#
# Phone setup (mkcert):
#   1. On the laptop, this script runs mkcert -install once (creates a local CA).
#   2. Find the CA file: mkcert -CAROOT  (usually rootCA.pem).
#   3. Copy rootCA.pem to the phone and install it as a trusted certificate.
#      Android: Settings → Security → Install a certificate → CA certificate.
#      iPhone: AirDrop or mail the file, install the profile, then enable it
#      under Settings → General → About → Certificate Trust Settings.
#   4. Open https://<laptop-ip>:8443/dev on the phone. dev_https and dev_mode
#      must both be true in hub/config/hub.toml.

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
mkdir -p hub/certs

if ! command -v mkcert >/dev/null 2>&1; then
  echo "mkcert is not installed."
  echo "Fedora:  sudo dnf install mkcert"
  echo "Ubuntu:  sudo apt install mkcert   (or download from github.com/FiloSottile/mkcert)"
  echo "Windows: choco install mkcert    or scoop install mkcert"
  exit 1
fi

ip="${1:-}"
if [[ -z "$ip" ]]; then
  if [[ -x hub/.venv/bin/python ]]; then
    py=hub/.venv/bin/python
  else
    py=python3
  fi
  ip="$("$py" -c 'from kinig_hub.config import detect_lan_ip; print(detect_lan_ip())')"
fi

if [[ "$ip" == 127.* || -z "$ip" ]]; then
  echo "Could not detect a LAN IP (got ${ip:-nothing})."
  echo "Pass it yourself: scripts/make_certs.sh 192.168.4.2"
  exit 1
fi

echo "Creating a certificate for ${ip}, localhost, and 127.0.0.1"
mkcert -install
mkcert -cert-file hub/certs/dev.pem -key-file hub/certs/dev-key.pem "$ip" localhost 127.0.0.1

echo
echo "Certificate written to hub/certs/ (gitignored)."
echo "Install the mkcert CA on the phone, then set in hub/config/hub.toml:"
echo "  dev_mode = true"
echo "  dev_https = true"
echo "Open https://${ip}:8443/dev"
echo "The notes QR still uses http://${ip}:8000"
