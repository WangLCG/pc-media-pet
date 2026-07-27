"""Long-lived WebRTC DataChannel management for notifications."""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcdatachannel import RTCDataChannel

from .config import Settings
from .models import AckPayload, CameraErrorEvent, MediaStateEvent, MotionDetectedEvent, NotifyAnswer, NotifyEnvelope, NotifyOffer, PongPayload, SoundDetectedEvent

logger = logging.getLogger(__name__)
CHINA_STANDARD_TIME = timezone(timedelta(hours=8), name="UTC+08:00")


@dataclass
class NotifyClient:
    """Resources owned by one browser's notify connection."""

    client_id: str
    peer_connection: RTCPeerConnection
    channel: RTCDataChannel | None = None
    ping_task: asyncio.Task[None] | None = None
    channel_open_task: asyncio.Task[None] | None = None
    awaiting_ping_id: str | None = None
    missed_pongs: int = 0


@dataclass
class PendingAck:
    """One client-specific reliable notification waiting for acknowledgement."""

    client_id: str
    message: NotifyEnvelope
    peer_connection: RTCPeerConnection
    retry_count: int = 0
    retry_task: asyncio.Task[None] | None = None


class NotifyChannelManager:
    """Create, monitor, and clean up one notify connection per client ID."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: dict[str, NotifyClient] = {}
        self._pending_acks: dict[tuple[str, str], PendingAck] = {}
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        """Return the number of active notify PeerConnections."""
        return len(self._clients)

    @property
    def pending_ack_count(self) -> int:
        """Return outstanding in-memory acknowledgements for operational status/tests."""
        return len(self._pending_acks)

    async def publish_motion(self, event: MotionDetectedEvent) -> None:
        """Send a motion event to all currently healthy notify clients."""
        payload = event.model_dump(exclude={"id", "ts"})
        # Keep the machine-readable Unix timestamp in the envelope and add the
        # operator-facing China Standard Time occurrence time for the UI.
        payload["occurred_at_hhmmss"] = datetime.fromtimestamp(event.ts, tz=CHINA_STANDARD_TIME).strftime("%H:%M:%S")
        message = NotifyEnvelope(
            version=1,
            type="motion_detected",
            id=event.id,
            ts=event.ts,
            payload=payload,
        )
        async with self._lock:
            clients = tuple(self._clients.values())
        for client in clients:
            if not self._is_healthy(client):
                continue
            self._send_reliable(client, message)

    async def publish_sound(self, event: SoundDetectedEvent) -> None:
        """Send a loud-sound event; the payload contains measurements, never audio."""
        payload = event.model_dump(exclude={"id", "ts"})
        payload["occurred_at_hhmmss"] = datetime.fromtimestamp(event.ts, tz=CHINA_STANDARD_TIME).strftime("%H:%M:%S")
        message = NotifyEnvelope(version=1, type="sound_detected", id=event.id, ts=event.ts, payload=payload)
        async with self._lock:
            clients = tuple(self._clients.values())
        for client in clients:
            if self._is_healthy(client):
                self._send_reliable(client, message)

    async def publish_camera_error(self, event: CameraErrorEvent) -> None:
        """Send a non-sensitive camera failure to healthy notify clients."""
        message = NotifyEnvelope(
            version=1,
            type="camera_error",
            id=event.id,
            ts=event.ts,
            payload=event.model_dump(exclude={"id", "ts"}),
        )
        async with self._lock:
            clients = tuple(self._clients.values())
        for client in clients:
            if self._is_healthy(client):
                self._send_reliable(client, message)

    async def publish_media_state(self, event: MediaStateEvent) -> None:
        """Reliably tell healthy clients when a media session starts or stops."""
        message = NotifyEnvelope(version=1, type="media_state", id=event.id, ts=event.ts, payload=event.model_dump(exclude={"id", "ts"}))
        async with self._lock:
            clients = tuple(self._clients.values())
        for client in clients:
            if self._is_healthy(client):
                self._send_reliable(client, message)

    async def create_answer(self, offer: NotifyOffer) -> NotifyAnswer:
        """Replace an existing client connection and return an SDP answer."""
        peer_connection = RTCPeerConnection()
        client = NotifyClient(client_id=offer.client_id, peer_connection=peer_connection)

        @peer_connection.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            if channel.label != "notify" or not channel.ordered:
                logger.warning("notify_channel_rejected", extra={"client_id": offer.client_id})
                channel.close()
                asyncio.create_task(self.close_client(offer.client_id, expected=peer_connection))
                return
            client.channel = channel
            self._bind_channel(client, channel)

        @peer_connection.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if peer_connection.connectionState in {"closed", "failed", "disconnected"}:
                await self.close_client(offer.client_id, expected=peer_connection)

        previous_client = await self._replace_client(client)
        if previous_client is not None:
            await self._close_client_resources(previous_client)
        client.channel_open_task = asyncio.create_task(
            self._expire_if_channel_does_not_open(client),
            name=f"notify-channel-open-{client.client_id}",
        )

        try:
            await peer_connection.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
            await peer_connection.setLocalDescription(await peer_connection.createAnswer())
            await self._wait_for_ice_complete(peer_connection)
            if not await self._is_current(client):
                raise RuntimeError("Notify connection was replaced during negotiation")
            local_description = peer_connection.localDescription
            if local_description is None:
                raise RuntimeError("Notify answer was not created")
            return NotifyAnswer(sdp=local_description.sdp, type=local_description.type)
        except Exception:
            await self.close_client(offer.client_id, expected=peer_connection)
            raise

    async def _replace_client(self, client: NotifyClient) -> NotifyClient | None:
        """Atomically make a client current and return the connection it replaced."""
        async with self._lock:
            previous_client = self._clients.get(client.client_id)
            self._clients[client.client_id] = client
            return previous_client

    async def _is_current(self, expected_client: NotifyClient) -> bool:
        """Return whether a negotiating client still owns its client ID."""
        async with self._lock:
            return self._clients.get(expected_client.client_id) is expected_client

    def _bind_channel(self, client: NotifyClient, channel: RTCDataChannel) -> None:
        @channel.on("open")
        def on_open() -> None:
            logger.info("notify_channel_open", extra={"client_id": client.client_id})
            if client.channel_open_task is not None:
                client.channel_open_task.cancel()
                client.channel_open_task = None
            self._send(client, self._message("hello", {"server_id": "pc-media-pet", "features": ["ping_pong"]}))
            self._send_ping(client)
            client.ping_task = asyncio.create_task(self._ping_loop(client), name=f"notify-ping-{client.client_id}")

        @channel.on("message")
        def on_message(message: str | bytes) -> None:
            self._handle_message(client, message)

        @channel.on("close")
        def on_close() -> None:
            asyncio.create_task(self.close_client(client.client_id, expected=client.peer_connection))

    def _handle_message(self, client: NotifyClient, message: str | bytes) -> None:
        if not isinstance(message, str):
            logger.warning("notify_message_rejected", extra={"client_id": client.client_id, "reason": "not_text"})
            return
        try:
            envelope = NotifyEnvelope.model_validate_json(message)
            if envelope.type == "pong":
                pong = PongPayload.model_validate(envelope.payload)
            elif envelope.type == "ack":
                ack = AckPayload.model_validate(envelope.payload)
            else:
                raise ValueError("unexpected message type")
        except (ValueError, json.JSONDecodeError):
            logger.warning("notify_message_rejected", extra={"client_id": client.client_id, "reason": "invalid"})
            return
        if envelope.type == "ack":
            self._acknowledge(client.client_id, ack.message_id)
            return
        if pong.ping_id != client.awaiting_ping_id:
            logger.warning("notify_pong_rejected", extra={"client_id": client.client_id, "reason": "unexpected_ping_id"})
            return
        client.awaiting_ping_id = None
        client.missed_pongs = 0
        logger.debug("notify_pong_received", extra={"client_id": client.client_id})

    async def _ping_loop(self, client: NotifyClient) -> None:
        try:
            while True:
                await asyncio.sleep(self._settings.notify_ping_interval_seconds)
                if client.awaiting_ping_id is not None:
                    client.missed_pongs += 1
                    if client.missed_pongs >= self._settings.notify_missed_pong_limit:
                        logger.warning("notify_client_unhealthy", extra={"client_id": client.client_id})
                        await self.close_client(client.client_id, expected=client.peer_connection)
                        return
                self._send_ping(client)
        except asyncio.CancelledError:
            raise

    def _send_ping(self, client: NotifyClient) -> None:
        """Send a ping and remember its ID until the matching pong arrives."""
        ping = self._message("ping", {})
        client.awaiting_ping_id = ping.id
        self._send(client, ping)

    def _send_reliable(self, client: NotifyClient, message: NotifyEnvelope) -> None:
        """Send an event and retain it only until acknowledged or retry exhaustion."""
        key = (client.client_id, message.id)
        if key in self._pending_acks or not self._is_healthy(client):
            return
        self._send(client, message)
        pending = PendingAck(client.client_id, message, client.peer_connection)
        self._pending_acks[key] = pending
        pending.retry_task = asyncio.create_task(self._retry_until_acknowledged(pending), name=f"notify-ack-{client.client_id}-{message.id}")

    async def _retry_until_acknowledged(self, pending: PendingAck) -> None:
        key = (pending.client_id, pending.message.id)
        try:
            while True:
                await asyncio.sleep(self._settings.notify_ack_timeout_seconds)
                if self._pending_acks.get(key) is not pending:
                    return
                if pending.retry_count >= self._settings.notify_max_retries:
                    self._pending_acks.pop(key, None)
                    logger.warning("notify_ack_retry_exhausted", extra={"client_id": pending.client_id})
                    await self.close_client(pending.client_id, expected=pending.peer_connection)
                    return
                async with self._lock:
                    client = self._clients.get(pending.client_id)
                if client is None or client.peer_connection is not pending.peer_connection or not self._is_healthy(client):
                    self._pending_acks.pop(key, None)
                    return
                pending.retry_count += 1
                self._send(client, pending.message)
        except asyncio.CancelledError:
            raise

    def _acknowledge(self, client_id: str, message_id: str) -> None:
        pending = self._pending_acks.pop((client_id, message_id), None)
        if pending is not None and pending.retry_task is not None:
            pending.retry_task.cancel()

    @staticmethod
    def _is_healthy(client: NotifyClient) -> bool:
        return client.channel is not None and client.channel.readyState == "open"

    async def _expire_if_channel_does_not_open(self, client: NotifyClient) -> None:
        """Release a negotiated connection if its required notify channel never opens."""
        try:
            await asyncio.sleep(self._settings.notify_channel_open_timeout_seconds)
            if not await self._is_current(client):
                return
            if client.channel is None or client.channel.readyState != "open":
                logger.warning("notify_channel_open_timeout", extra={"client_id": client.client_id})
                await self.close_client(client.client_id, expected=client.peer_connection)
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _message(message_type: str, payload: dict[str, object]) -> NotifyEnvelope:
        return NotifyEnvelope(
            version=1,
            type=message_type,
            id=f"msg_{uuid.uuid4().hex}",
            ts=int(time.time()),
            payload=payload,
        )

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

    @staticmethod
    def _send(client: NotifyClient, message: NotifyEnvelope) -> None:
        if client.channel is not None and client.channel.readyState == "open":
            client.channel.send(message.model_dump_json())

    async def close_client(self, client_id: str, expected: RTCPeerConnection | None = None) -> None:
        """Close a client only if it still owns the current connection slot."""
        async with self._lock:
            client = self._clients.get(client_id)
            if client is None or (expected is not None and client.peer_connection is not expected):
                return
            self._clients.pop(client_id)
        self._clear_pending_acks(client_id)
        await self._close_client_resources(client)

    def _clear_pending_acks(self, client_id: str) -> None:
        """Discard non-persistent events for a disconnected client."""
        current_task = asyncio.current_task()
        for key, pending in tuple(self._pending_acks.items()):
            if key[0] == client_id:
                self._pending_acks.pop(key, None)
                if pending.retry_task is not None and pending.retry_task is not current_task:
                    pending.retry_task.cancel()

    @staticmethod
    async def _close_client_resources(client: NotifyClient) -> None:
        """Release a detached client's task and PeerConnection."""
        current_task = asyncio.current_task()
        for task in (client.ping_task, client.channel_open_task):
            if task is not None and task is not current_task:
                task.cancel()
        await client.peer_connection.close()
        logger.info("notify_client_closed", extra={"client_id": client.client_id})

    async def close(self) -> None:
        """Close all active notify connections during application shutdown."""
        await asyncio.gather(*(self.close_client(client_id) for client_id in list(self._clients)), return_exceptions=True)
