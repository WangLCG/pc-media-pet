"""FastAPI entry point for the PC Media Pet service."""

import logging
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import require_app_token
from .config import get_settings
from .logging_config import configure_logging
from .models import NotifyAnswer, NotifyOffer
from .notify_channel import NotifyChannelManager

logger = logging.getLogger(__name__)
WEB_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and clean up process-level resources."""
    configure_logging()
    # Validate configuration before accepting any request.
    settings = get_settings()
    app.state.notify_manager = NotifyChannelManager(settings)
    app.state.started_at = monotonic()
    logger.info("service_started")
    try:
        yield
    finally:
        await app.state.notify_manager.close()
        logger.info("service_stopped")


app = FastAPI(title="PC Media Pet", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the minimal local status page."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, bool]:
    """Public liveness endpoint used by local process supervision."""
    return {"ok": True}


@app.get("/api/status", dependencies=[Depends(require_app_token)])
async def status() -> dict[str, str | int]:
    """Return non-sensitive service state; later phases fill live counters."""
    return {
        "service": "running",
        "notify_clients": app.state.notify_manager.client_count,
        "media_sessions": 0,
        "camera_mode": "idle",
        "motion_state": "quiet",
        "uptime_seconds": int(monotonic() - app.state.started_at),
    }


@app.post("/api/notify/offer", response_model=NotifyAnswer, dependencies=[Depends(require_app_token)])
async def notify_offer(offer: NotifyOffer) -> NotifyAnswer:
    """Accept a browser WebRTC offer for its long-lived notify channel."""
    return await app.state.notify_manager.create_answer(offer)
