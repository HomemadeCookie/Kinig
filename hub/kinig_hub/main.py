"""Kinig hub. HTTP on hub.toml http_port (8000).

Bind address is 0.0.0.0 so a pod or phone on the LAN can connect.
Notes URLs still use public_host from hub.toml, not this bind address
and not localhost.

    python -m kinig_hub.main
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from kinig_hub.api.sessions import router as sessions_router
from kinig_hub.config import Config, hub_dir, load_config
from kinig_hub.metrics import MetricsLog
from kinig_hub.models.base import Summarizer, Transcriber
from kinig_hub.models.registry import build_summarizer, build_transcriber
from kinig_hub.pipeline.worker import Worker
from kinig_hub.storage import Storage


class Hub:
    """The objects the routes share: storage, buffers, and the one worker."""

    def __init__(
        self,
        config: Config,
        storage: Storage,
        metrics: MetricsLog,
        worker: Worker,
        *,
        reread_config: bool = True,
    ):
        self.config = config
        self.storage = storage
        self.metrics = metrics
        self.worker = worker
        self.reread_config = reread_config
        self.buffers: dict = {}
        self.stopping: set[str] = set()
        self.buffer_lock = threading.Lock()
        worker.hub = self

    def reload_config(self) -> Config:
        """Next session picks up toml edits. The running worker keeps its models.

        Tests pass a Config object and turn this off so they can set a short
        window_s without writing a second toml file.
        """
        if self.reread_config:
            self.config = load_config()
        return self.config


def create_app(
    *,
    config: Config | None = None,
    transcriber: Transcriber | None = None,
    summarizer: Summarizer | None = None,
    db_path: Path | None = None,
    metrics_path: Path | None = None,
    transcriber_name: str | None = None,
    summarizer_name: str | None = None,
    reread_config: bool = True,
) -> FastAPI:
    loaded = config if config is not None else load_config()
    if transcriber is None or summarizer is None:
        built_transcriber = build_transcriber(loaded.models)
        built_summarizer = build_summarizer(loaded.models)
        transcriber = built_transcriber
        summarizer = built_summarizer
        transcriber_name = loaded.models["transcriber"]["backend"]
        summarizer_name = loaded.models["summarizer"]["backend"]
    worker = Worker(
        transcriber,
        summarizer,
        transcriber_name=transcriber_name or "configured",
        summarizer_name=summarizer_name or "configured",
    )
    storage = Storage(db_path if db_path is not None else hub_dir() / "kinig.db")
    hub = Hub(
        loaded,
        storage,
        MetricsLog(metrics_path),
        worker,
        reread_config=reread_config,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub.worker.start()
        yield
        app.state.hub.worker.stop()

    app = FastAPI(title="Kinig hub", lifespan=lifespan)
    app.state.hub = hub
    app.include_router(sessions_router)
    return app


def main() -> None:
    config = load_config()
    application = create_app(config=config)
    # 0.0.0.0: the ESP32 and phones are other machines on the pocket router.
    uvicorn.run(application, host="0.0.0.0", port=int(config.hub["http_port"]))


if __name__ == "__main__":
    main()
