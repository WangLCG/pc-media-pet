import asyncio
import json

import pytest

from app.config import Settings
from app.event_bus import EventBus
from app.models import CameraErrorEvent, MotionDetectedEvent, NotifyOffer
from app.notify_channel import NotifyChannelManager
from tests.test_notify_known_gaps import ImmediatePeerConnection, OpenDataChannel


def settings(**overrides) -> Settings:
    return Settings(app_token="test-token-that-is-long-enough", **overrides)


@pytest.mark.asyncio
async def test_event_bus_delivers_typed_motion_event():
    event_bus = EventBus()
    received = []

    async def receive(event: MotionDetectedEvent) -> None:
        received.append(event)

    event_bus.subscribe_motion(receive)
    event = MotionDetectedEvent(confidence=0.82, changed_area=2410)
    await event_bus.publish_motion(event)

    assert received == [event]


@pytest.mark.asyncio
async def test_event_bus_delivers_typed_camera_error_event():
    event_bus = EventBus()
    received = []

    async def receive(event: CameraErrorEvent) -> None:
        received.append(event)

    event_bus.subscribe_camera_error(receive)
    event = CameraErrorEvent(code="camera_unavailable", message="USB camera cannot be opened")
    await event_bus.publish_camera_error(event)

    assert received == [event]


async def connected_manager(monkeypatch, **setting_overrides):
    peer_connection = ImmediatePeerConnection()
    monkeypatch.setattr("app.notify_channel.RTCPeerConnection", lambda: peer_connection)
    manager = NotifyChannelManager(settings(**setting_overrides))
    offer = NotifyOffer(client_id="browser-01", sdp="offer-sdp", type="offer")
    await manager.create_answer(offer)
    client = manager._clients[offer.client_id]
    channel = OpenDataChannel()
    client.channel = channel
    manager._bind_channel(client, channel)
    channel.handlers["open"]()
    return manager, peer_connection, channel


@pytest.mark.asyncio
async def test_motion_notification_is_removed_after_ack(monkeypatch):
    manager, _, channel = await connected_manager(monkeypatch)
    try:
        event = MotionDetectedEvent(id="evt_motion_01", confidence=0.82, changed_area=2410)
        await manager.publish_motion(event)
        motion_message = json.loads(channel.messages[-1])

        assert motion_message["type"] == "motion_detected"
        assert manager.pending_ack_count == 1

        channel.handlers["message"](json.dumps({
            "version": 1,
            "type": "ack",
            "id": "ack_motion_01",
            "ts": event.ts + 1,
            "payload": {"message_id": event.id, "status": "received"},
        }))
        await asyncio.sleep(0)
        assert manager.pending_ack_count == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_motion_notification_retries_then_closes_unhealthy_client(monkeypatch):
    manager, peer_connection, channel = await connected_manager(
        monkeypatch,
        notify_ack_timeout_seconds=0.01,
        notify_max_retries=1,
    )
    try:
        await manager.publish_motion(MotionDetectedEvent(id="evt_motion_02", confidence=0.75, changed_area=1900))
        for _ in range(20):
            if manager.pending_ack_count == 0:
                break
            await asyncio.sleep(0.01)

        motion_messages = [json.loads(message) for message in channel.messages if json.loads(message)["type"] == "motion_detected"]
        assert len(motion_messages) == 2
        assert manager.pending_ack_count == 0
        assert manager.client_count == 0
        assert peer_connection.closed is True
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_camera_error_notification_is_reliably_acknowledged(monkeypatch):
    manager, _, channel = await connected_manager(monkeypatch)
    try:
        event = CameraErrorEvent(id="evt_camera_01", code="camera_unavailable", message="USB camera cannot be opened")
        await manager.publish_camera_error(event)
        message = json.loads(channel.messages[-1])

        assert message["type"] == "camera_error"
        assert message["payload"] == {"code": "camera_unavailable", "message": "USB camera cannot be opened"}
        channel.handlers["message"](json.dumps({
            "version": 1,
            "type": "ack",
            "id": "ack_camera_01",
            "ts": event.ts + 1,
            "payload": {"message_id": event.id, "status": "received"},
        }))
        await asyncio.sleep(0)
        assert manager.pending_ack_count == 0
    finally:
        await manager.close()
