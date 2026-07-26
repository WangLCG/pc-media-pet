"""Short-lived WebRTC media sessions backed by the shared camera manager."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from .camera import CameraManager
from .config import Settings
from .models import MediaAnswer, MediaOffer, MediaStateEvent

logger = logging.getLogger(__name__)


class CameraVideoTrack(VideoStreamTrack):
    """Produce WebRTC frames from CameraManager without opening another device."""

    def __init__(self, camera: CameraManager, fps: int) -> None:
        super().__init__()
        self._camera = camera
        self._frame_interval = 1 / fps
        self._next_frame_at = 0.0
        self._pts = 0

    async def recv(self) -> VideoFrame:
        now = time.monotonic()
        if self._next_frame_at > now:
            await asyncio.sleep(self._next_frame_at - now)
        self._next_frame_at = max(self._next_frame_at + self._frame_interval, time.monotonic())
        frame = await self._camera.read_frame_for_media()
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        self._pts += int(90_000 * self._frame_interval)
        video_frame.pts = self._pts
        video_frame.time_base = Fraction(1, 90_000)
        return video_frame


@dataclass
class MediaSession:
    session_id: str
    client_id: str
    peer_connection: RTCPeerConnection
    timeout_task: asyncio.Task[None] | None = None


class MediaSessionManager:
    """Own short-lived media PeerConnections and camera view-mode transitions."""

    def __init__(self, settings: Settings, camera: CameraManager, publish_state=None) -> None:
        self._settings = settings
        self._camera = camera
        self._sessions: dict[str, MediaSession] = {}
        self._lock = asyncio.Lock()
        self._publish_state = publish_state

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    async def create_answer(self, offer: MediaOffer) -> MediaAnswer:
        if not offer.video or offer.audio:
            raise ValueError("Only video media sessions are supported")
        peer_connection = RTCPeerConnection()
        session_id = f"media_{uuid.uuid4().hex}"
        session = MediaSession(session_id, offer.client_id, peer_connection)

        @peer_connection.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if peer_connection.connectionState in {"closed", "failed", "disconnected"}:
                await self.close_session(session_id, expected=peer_connection)

        async with self._lock:
            self._sessions[session_id] = session
            first_session = len(self._sessions) == 1
        if first_session:
            await self._camera.set_mode("view")

        try:
            peer_connection.addTrack(CameraVideoTrack(self._camera, self._settings.camera_view_fps))
            await peer_connection.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
            await peer_connection.setLocalDescription(await peer_connection.createAnswer())
            await self._wait_for_ice_complete(peer_connection)
            local_description = peer_connection.localDescription
            if local_description is None:
                raise RuntimeError("Media answer was not created")
            # This is a connection-establishment deadline, not a maximum stream
            # duration. Active viewers must remain connected until they stop.
            session.timeout_task = asyncio.create_task(
                self._expire_unconnected_session(session),
                name=f"media-connect-timeout-{session_id}",
            )
            await self._publish(MediaStateEvent(state="started", session_id=session_id))
            return MediaAnswer(session_id=session_id, sdp=local_description.sdp, type=local_description.type)
        except Exception:
            await self.close_session(session_id, expected=peer_connection)
            raise

    async def stop_session(self, session_id: str, client_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if session.client_id != client_id:
                raise PermissionError("Media session is owned by another client")
        await self.close_session(session_id, expected=session.peer_connection)
        return True

    async def close_session(self, session_id: str, expected: RTCPeerConnection | None = None) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or (expected is not None and session.peer_connection is not expected):
                return
            self._sessions.pop(session_id)
            became_idle = not self._sessions
        if session.timeout_task is not None and session.timeout_task is not asyncio.current_task():
            session.timeout_task.cancel()
        await session.peer_connection.close()
        if became_idle:
            await self._camera.set_mode("idle")
        await self._publish(MediaStateEvent(state="stopped", session_id=session_id))
        logger.info("media_session_closed", extra={"session_id": session_id})

    async def close(self) -> None:
        await asyncio.gather(*(self.close_session(session_id) for session_id in list(self._sessions)), return_exceptions=True)

    async def _expire_unconnected_session(self, session: MediaSession) -> None:
        try:
            await asyncio.sleep(self._settings.media_idle_timeout_seconds)
            if session.peer_connection.connectionState in {"new", "connecting"}:
                await self.close_session(session.session_id, expected=session.peer_connection)
        except asyncio.CancelledError:
            raise

    async def _publish(self, event: MediaStateEvent) -> None:
        if self._publish_state is not None:
            await self._publish_state(event)

    @staticmethod
    async def _wait_for_ice_complete(peer_connection: RTCPeerConnection) -> None:
        if peer_connection.iceGatheringState == "complete":
            return
        completed = asyncio.Event()

        @peer_connection.on("icegatheringstatechange")
        def on_ice_gathering_state_change() -> None:
            if peer_connection.iceGatheringState == "complete":
                completed.set()

        await asyncio.wait_for(completed.wait(), timeout=10)
