#!/usr/bin/env python3
"""Download the models named in hub/config/models.toml. Setup step, needs internet.

Run this once on the laptop, before the pocket router:

    python scripts/download_models.py

The Whisper weights land in hub/model_files/whisper/. After that, the hub and
bench_models.py load with local_files_only and will not download.

Ollama keeps its own files. This script only checks that the configured model
is already pulled, and prints `ollama pull ...` when it is not.
"""

from __future__ import annotations

import sys

_INSTALL_HELP = (
    "Cannot import kinig_hub. From the repo root run:\n"
    "  pip install -e hub"
)


def main(argv: list[str] | None = None) -> int:
    del argv  # no flags; the model names come from models.toml
    try:
        from kinig_hub.config import load_config
        from kinig_hub.models.registry import setup_models
    except ImportError:
        print(_INSTALL_HELP, file=sys.stderr)
        return 1

    ok, lines = setup_models(load_config().models)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
