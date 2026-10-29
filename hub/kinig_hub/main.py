"""Kinig hub. HTTP on hub.toml http_port (8000).

Bind address is 0.0.0.0 so a pod or phone on the LAN can connect.
Notes URLs still use public_host from hub.toml, not this bind address
and not localhost.

    python -m kinig_hub.main
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from kinig_hub.api.sessions import router as sessions_router
from kinig_hub import discovery
from kinig_hub.config import Config, config_dir, hub_dir, load_config, resolved_public_host
from kinig_hub.metrics import MetricsLog
from kinig_hub.models.base import Summarizer, Transcriber
from kinig_hub.models.registry import build_summarizer, build_transcriber
from kinig_hub.pipeline.worker import Worker
from kinig_hub.pods import PodRegistry
from kinig_hub.storage import Storage

log = logging.getLogger("kinig.hub")


class Hub:
    """The objects the routes share: storage, buffers, pods, and the one worker."""

    def __init__(
        self,
        config: Config,
        storage: Storage,
        metrics: MetricsLog,
        worker: Worker,
        *,
        reread_config: bool = True,
        config_folder: Path | None = None,
    ):
        self.config = config
        self.storage = storage
        self.metrics = metrics
        self.worker = worker
        self.reread_config = reread_config
        # Tests point this at a copy of the toml files so PUT /config cannot
        # rewrite the copies that ship with the repo.
        self.config_folder = config_folder if config_folder is not None else config_dir()
        self.buffers: dict = {}
        self.stopping: set[str] = set()
        self.buffer_lock = threading.Lock()
        self.pods = PodRegistry()
        worker.hub = self

    def reload_config(self) -> Config:
        """Next session picks up toml edits. The running worker keeps its models.

        Tests pass a Config object and turn this off so they can set a short
        window_s without writing a second toml file.
        """
        if self.reread_config:
            self.config = load_config(self.config_folder)
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
    config_folder: Path | None = None,
) -> FastAPI:
    loaded = config if config is not None else load_config(config_folder)
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
        config_folder=config_folder,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub.worker.start()
        yield
        app.state.hub.worker.stop()

    app = FastAPI(title="Kinig hub", lifespan=lifespan)
    app.state.hub = hub
    app.include_router(sessions_router)
    from kinig_hub.api.config_api import router as config_router
    from kinig_hub.api.dev import router as dev_router
    from kinig_hub.api.pods import router as pods_router
    from kinig_hub.api.viewers import router as viewers_router

    app.include_router(config_router)
    app.include_router(dev_router)
    app.include_router(pods_router)
    app.include_router(viewers_router)

    @app.get("/", include_in_schema=False)
    async def home():
        return RedirectResponse("/host")

    static = hub_dir() / "kinig_hub" / "web" / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")
    return app


def https_cert_paths() -> tuple[Path, Path]:
    folder = hub_dir() / "certs"
    return folder / "dev.pem", folder / "dev-key.pem"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_config()
    host = resolved_public_host(config.hub)
    http_port = int(config.hub["http_port"])
    # 0.0.0.0: the ESP32 and phones are other machines on the pocket router.
    log.info("Notes and QR codes use http://%s:%s", host, http_port)
    log.info("Host console: http://127.0.0.1:%s/host", http_port)
    if str(config.hub.get("public_host", "")).strip().lower() == "auto":
        log.info(
            "public_host is auto. Detected LAN address %s. "
            "Set public_host in hub/config/hub.toml to override.",
            host,
        )
    application = create_app(config=config)
    if config.hub.get("dev_https"):
        cert, key = https_cert_paths()
        if not cert.is_file() or not key.is_file():
            log.error(
                "dev_https is true but %s or %s is missing. "
                "Run scripts/make_certs.sh. HTTPS will not start.",
                cert,
                key,
            )
        else:
            https_port = int(config.hub["https_port"])
            log.info("Phone dev page: https://%s:%s/dev", host, https_port)
            thread = threading.Thread(
                target=_serve_https,
                args=(application, https_port, cert, key),
                name="kinig-https",
                daemon=True,
            )
            thread.start()
    announced = discovery.advertise(host, http_port)
    try:
        uvicorn.run(application, host="0.0.0.0", port=http_port)
    finally:
        discovery.stop(announced)


def _serve_https(application: FastAPI, port: int, cert: Path, key: Path) -> None:
    # lifespan off: the HTTP server already started the worker.
    settings = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        lifespan="off",
        log_level="info",
    )
    uvicorn.Server(settings).run()


if __name__ == "__main__":
    main()
