"""FastAPI entry point for the PC Media Pet service."""

import logging
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import require_app_token
from .camera import CameraManager
from .config import get_settings
from .event_bus import EventBus
from .logging_config import configure_logging
from .models import MediaAnswer, MediaOffer, MediaStopRequest, NotifyAnswer, NotifyOffer
from .media_session import MediaSessionManager
from .motion import MotionDetector
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
    app.state.event_bus = EventBus()
    app.state.event_bus.subscribe_motion(app.state.notify_manager.publish_motion)
    app.state.event_bus.subscribe_camera_error(app.state.notify_manager.publish_camera_error)
    app.state.camera_manager = CameraManager(settings, app.state.event_bus.publish_camera_error)
    app.state.motion_detector = MotionDetector(app.state.camera_manager, app.state.event_bus, settings)
    app.state.media_manager = MediaSessionManager(settings, app.state.camera_manager, app.state.notify_manager.publish_media_state)
    await app.state.camera_manager.start()
    await app.state.motion_detector.start()
    app.state.started_at = monotonic()
    logger.info("service_started")
    try:
        yield
    finally:
        await app.state.motion_detector.stop()
        await app.state.media_manager.close()
        await app.state.camera_manager.stop()
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
        "media_sessions": app.state.media_manager.session_count,
        "camera_mode": app.state.camera_manager.mode,
        "motion_state": app.state.motion_detector.state,
        "uptime_seconds": int(monotonic() - app.state.started_at),
    }


@app.post("/api/notify/offer", response_model=NotifyAnswer, dependencies=[Depends(require_app_token)])
async def notify_offer(offer: NotifyOffer) -> NotifyAnswer:
    """Accept a browser WebRTC offer for its long-lived notify channel."""
    try:
        return await app.state.notify_manager.create_answer(offer)
    except (ValueError, AssertionError, TimeoutError) as error:
        raise HTTPException(status_code=422, detail="Invalid WebRTC notify offer") from error


@app.post("/api/media/offer", response_model=MediaAnswer, dependencies=[Depends(require_app_token)])
async def media_offer(offer: MediaOffer) -> MediaAnswer:
    """Create a short-lived video PeerConnection for the requesting browser."""
    try:
        return await app.state.media_manager.create_answer(offer)
    except (ValueError, AssertionError, TimeoutError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/media/stop", dependencies=[Depends(require_app_token)])
async def media_stop(request: MediaStopRequest) -> dict[str, bool]:
    """Stop a media session only when the caller owns it."""
    try:
        return {"ok": await app.state.media_manager.stop_session(request.session_id, request.client_id)}
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
