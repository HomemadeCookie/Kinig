#!/usr/bin/env bash
# Start the hub on 0.0.0.0 and the port in hub/config/hub.toml (8000).

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [[ -x hub/.venv/bin/python ]]; then
  exec hub/.venv/bin/python -m kinig_hub.main
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "No hub/.venv and no python3. Run scripts/setup.sh first."
  exit 1
fi

echo "hub/.venv is missing. Trying python3. Run scripts/setup.sh if this fails."
exec python3 -m kinig_hub.main
