# PC Media Pet: Tailscale + WebRTC Implementation Design

## 1. Goal

Build a Windows 10 Python application for remote pet monitoring with a USB camera.

Core requirements:

- USB camera connected to a Win10 laptop.
- Low power usage: no always-on video capture or encoding.
- Motion detection triggers local in-network notification.
- Notification uses a long-lived WebRTC DataChannel over Tailscale.
- Audio/video WebRTC sessions are independent from notification DataChannel sessions.
- Monitoring data is encrypted in transit and not saved to disk.
- Remote access is limited to the Tailscale private network.

## 2. High-Level Architecture

```text
Remote Client: Browser / Desktop App / Mobile Web
  |
  | Tailscale private network
  |
Win10 Laptop
  |-- FastAPI signaling server
  |-- Notify WebRTC PeerConnection
  |     `-- reliable DataChannel: notify
  |
  |-- Media WebRTC PeerConnection
  |     |-- VideoTrack: USB camera
  |     |-- AudioTrack: optional microphone
  |     `-- optional DataChannel: media_control
  |
  |-- Camera manager
  |-- Motion detector
  |-- Event bus
  |-- In-memory ack/retry queue
```

Two WebRTC connection types are used:

1. `notify_pc`: long-lived, DataChannel only, used for notifications and control feedback.
2. `media_pc`: short-lived, created on demand, used for audio/video streaming.

This keeps notification reliable and lightweight while allowing video encoding and camera high-FPS mode to run only when needed.

## 3. Network Model

### 3.1 Tailscale

All clients must join the same Tailnet.

The Python service should bind to one of:

- `127.0.0.1` when using `tailscale serve`.
- The Tailscale interface IP, usually `100.x.y.z`, when directly serving on the Tailnet.

Avoid:

- Router port forwarding.
- Public IP exposure.
- Tailscale Funnel for the first version.

Recommended access:

```text
https://home-laptop.<tailnet>.ts.net
```

or:

```text
http://100.x.y.z:8000
```

### 3.2 Encryption

Encryption layers:

- Tailscale WireGuard tunnel encrypts all Tailnet traffic.
- WebRTC encrypts media and DataChannel data using DTLS/SRTP.
- HTTPS/WSS can be added with `tailscale serve`.
- Application token authentication is still required.

## 4. Process Model

```text
main.py
  starts FastAPI
  starts event bus
  starts camera manager in low-power mode
  starts motion detector
  waits for notify/media signaling
```

Runtime states:

```text
IDLE
  No media viewer.
  Notify DataChannel may be connected.
  Camera samples low frequency.

MOTION_ACTIVE
  Motion detected.
  Event is sent through notify DataChannel.
  Camera may temporarily increase sampling rate for confirmation.

VIEWING
  At least one media session is active.
  Camera runs higher FPS.
  Video is encoded and sent through WebRTC.

COOLDOWN
  Media session closed or motion ended.
  Camera returns to low-power sampling.
```

## 5. Project Structure

```text
pc-media-pet/
  app/
    __init__.py
    main.py
    config.py
    auth.py
    signaling.py
    event_bus.py
    notify_channel.py
    media_session.py
    camera.py
    motion.py
    models.py
    lifecycle.py
    logging_config.py
  web/
    index.html
    app.js
    styles.css
  docs/
    tailscale-webrtc-implementation.md
  scripts/
    run.ps1
    install_service.ps1
    uninstall_service.ps1
  tests/
    test_motion.py
    test_notify_protocol.py
  .env.example
  requirements.txt
  README.md
```

## 6. Python Dependencies

```text
fastapi
uvicorn[standard]
aiortc
opencv-python
numpy
pydantic
pydantic-settings
python-dotenv
orjson
```

Optional:

```text
pywin32
psutil
```

Use `pywin32` or Windows Task Scheduler/NSSM for running the service at startup.

## 7. Configuration

`.env.example`:

```text
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TOKEN=change-me-long-random-token

CAMERA_INDEX=0
CAMERA_IDLE_WIDTH=640
CAMERA_IDLE_HEIGHT=360
CAMERA_IDLE_INTERVAL_SECONDS=2.0
CAMERA_VIEW_WIDTH=1280
CAMERA_VIEW_HEIGHT=720
CAMERA_VIEW_FPS=15

MOTION_MIN_CHANGED_AREA=1800
MOTION_CONFIRM_FRAMES=2
MOTION_COOLDOWN_SECONDS=300

NOTIFY_PING_INTERVAL_SECONDS=20
NOTIFY_MISSED_PONG_LIMIT=3
NOTIFY_ACK_TIMEOUT_SECONDS=5
NOTIFY_MAX_RETRIES=3

MEDIA_IDLE_TIMEOUT_SECONDS=120
```

## 8. Authentication

All API requests require:

```text
Authorization: Bearer <APP_TOKEN>
```

Protected endpoints:

- `POST /api/notify/offer`
- `POST /api/media/offer`
- `POST /api/media/stop`
- `GET /api/status`

The frontend stores the token only in browser memory or session storage. Avoid local storage if shared computers may be used.

## 9. HTTP API

### 9.1 Health

```http
GET /health
```

Response:

```json
{
  "ok": true
}
```

### 9.2 Status

```http
GET /api/status
Authorization: Bearer <token>
```

Response:

```json
{
  "service": "running",
  "notify_clients": 1,
  "media_sessions": 0,
  "camera_mode": "idle",
  "motion_state": "quiet",
  "uptime_seconds": 3600
}
```

### 9.3 Notify Offer

```http
POST /api/notify/offer
Authorization: Bearer <token>
Content-Type: application/json
```

Request:

```json
{
  "client_id": "phone-01",
  "sdp": "...",
  "type": "offer"
}
```

Response:

```json
{
  "sdp": "...",
  "type": "answer"
}
```

### 9.4 Media Offer

```http
POST /api/media/offer
Authorization: Bearer <token>
Content-Type: application/json
```

Request:

```json
{
  "client_id": "phone-01",
  "sdp": "...",
  "type": "offer",
  "video": true,
  "audio": false
}
```

Response:

```json
{
  "session_id": "media_20260726_001",
  "sdp": "...",
  "type": "answer"
}
```

### 9.5 Stop Media

```http
POST /api/media/stop
Authorization: Bearer <token>
Content-Type: application/json
```

Request:

```json
{
  "session_id": "media_20260726_001"
}
```

Response:

```json
{
  "ok": true
}
```

## 10. Notify DataChannel Protocol

### 10.1 Channel

Name:

```text
notify
```

Client-side creation:

```js
const channel = pc.createDataChannel("notify", {
  ordered: true
});
```

This channel is reliable and ordered by default.

### 10.2 Message Envelope

All messages are JSON.

```json
{
  "version": 1,
  "type": "motion_detected",
  "id": "evt_20260726_120001_0001",
  "ts": 1785067201,
  "payload": {}
}
```

Required fields:

- `version`: protocol version.
- `type`: message type.
- `id`: unique message id.
- `ts`: Unix timestamp in seconds.
- `payload`: type-specific object.

### 10.3 Server-to-Client Messages

#### hello

Sent when the notify DataChannel opens.

```json
{
  "version": 1,
  "type": "hello",
  "id": "msg_001",
  "ts": 1785067201,
  "payload": {
    "server_id": "home-laptop",
    "features": ["motion", "media_start", "ack_retry"]
  }
}
```

#### ping

```json
{
  "version": 1,
  "type": "ping",
  "id": "ping_001",
  "ts": 1785067201,
  "payload": {}
}
```

#### motion_detected

```json
{
  "version": 1,
  "type": "motion_detected",
  "id": "evt_20260726_120001_0001",
  "ts": 1785067201,
  "payload": {
    "zone": "default",
    "confidence": 0.82,
    "changed_area": 2410
  }
}
```

#### camera_error

```json
{
  "version": 1,
  "type": "camera_error",
  "id": "evt_20260726_120500_0002",
  "ts": 1785067500,
  "payload": {
    "code": "camera_unavailable",
    "message": "USB camera cannot be opened"
  }
}
```

#### media_state

```json
{
  "version": 1,
  "type": "media_state",
  "id": "msg_010",
  "ts": 1785067600,
  "payload": {
    "state": "started",
    "session_id": "media_20260726_001"
  }
}
```

### 10.4 Client-to-Server Messages

#### ack

```json
{
  "version": 1,
  "type": "ack",
  "id": "ack_evt_20260726_120001_0001",
  "ts": 1785067202,
  "payload": {
    "message_id": "evt_20260726_120001_0001",
    "status": "received"
  }
}
```

#### pong

```json
{
  "version": 1,
  "type": "pong",
  "id": "pong_001",
  "ts": 1785067202,
  "payload": {
    "ping_id": "ping_001"
  }
}
```

#### start_stream

```json
{
  "version": 1,
  "type": "start_stream",
  "id": "cmd_001",
  "ts": 1785067210,
  "payload": {
    "video": true,
    "audio": false
  }
}
```

The server responds with `ack` and the client then calls `POST /api/media/offer`.

#### stop_stream

```json
{
  "version": 1,
  "type": "stop_stream",
  "id": "cmd_002",
  "ts": 1785067300,
  "payload": {
    "session_id": "media_20260726_001"
  }
}
```

## 11. Notify Ack and Retry

The server keeps an in-memory pending ack map:

```text
pending_acks:
  message_id -> {
    message,
    client_id,
    sent_at,
    retry_count
  }
```

Algorithm:

```text
send event
  add to pending_acks
  wait NOTIFY_ACK_TIMEOUT_SECONDS
  if ack received:
    remove from pending_acks
  else if retry_count < NOTIFY_MAX_RETRIES:
    resend event
  else:
    mark client unhealthy
```

No event history is written to disk.

Service restart behavior:

- Pending notification events are lost.
- Active WebRTC connections are closed.
- Clients reconnect automatically.

This matches the "do not save monitoring data" requirement.

## 12. Motion Detection

First version uses frame differencing:

```text
capture frame A
wait idle interval
capture frame B
resize
convert to grayscale
Gaussian blur
absdiff(A, B)
threshold
dilate
sum changed contour area
if changed area > threshold for N frames:
  publish motion_detected event
```

False-positive controls:

- Motion confirmation count.
- Cooldown interval.
- Optional region-of-interest mask.
- Ignore very small contours.
- Optional time window, such as only monitoring when away from home.

Camera modes:

```text
idle mode:
  640x360
  1 frame every 1-3 seconds

view mode:
  1280x720
  10-15 FPS
```

## 13. Camera Manager

Responsibilities:

- Own the OpenCV `VideoCapture` object.
- Switch between idle and viewing modes.
- Share the latest frame with motion detection and media track.
- Avoid multiple modules opening the camera independently.
- Release camera resources when no longer needed.

Interface:

```python
class CameraManager:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def set_mode(self, mode: Literal["idle", "view"]) -> None: ...
    async def get_latest_frame(self) -> np.ndarray | None: ...
    async def read_frame_for_media(self) -> np.ndarray: ...
```

Implementation note:

- OpenCV capture is blocking.
- Run capture reads in a background thread or `asyncio.to_thread`.
- Use a lock around mode changes.

## 14. Media WebRTC Session

`media_session.py` owns short-lived audio/video PeerConnections.

Responsibilities:

- Create `RTCPeerConnection` for media.
- Attach camera `VideoStreamTrack`.
- Optionally attach audio later.
- Close idle or disconnected sessions.
- Tell `CameraManager` to enter `view` mode when session count is greater than zero.
- Return to `idle` mode when the last session closes.

Session lifecycle:

```text
POST /api/media/offer
  validate token
  create media_pc
  add VideoStreamTrack
  set remote offer
  create local answer
  return answer

connection closed / failed / timeout
  close media_pc
  remove session
  if no sessions remain:
    camera.set_mode("idle")
```

## 15. Frontend Flow

Initial page load:

```text
1. User opens Tailscale URL.
2. User enters token.
3. Browser creates notify PeerConnection.
4. Browser creates notify DataChannel.
5. Browser sends offer to /api/notify/offer.
6. Browser receives answer.
7. Notification channel stays open.
```

On notification:

```text
1. Browser receives motion_detected.
2. Browser immediately sends ack.
3. UI shows local alert.
4. User clicks "View".
5. Browser creates media PeerConnection.
6. Browser sends offer to /api/media/offer.
7. Browser receives remote video.
```

On page close:

```text
1. Browser closes media_pc.
2. Browser closes notify_pc.
3. Backend detects close and cleans up.
```

## 16. Reliability

Notify channel:

- Send ping every 20 seconds.
- Require pong within 3 missed intervals.
- Reconnect with exponential backoff.
- Recreate notify PeerConnection on reconnect.

Media channel:

- No automatic permanent reconnect.
- If media fails, keep notify channel alive.
- User can press "View" again.

Camera:

- If camera fails, publish `camera_error`.
- Retry camera open every 30 seconds.
- Keep service alive even when camera is disconnected.

## 17. Privacy and Storage

Hard rule:

- No video files.
- No image files.
- No historical event database.
- No snapshots saved to disk.

Allowed in memory:

- Latest frame.
- Motion comparison frame.
- Active media frames.
- Pending notification ack queue.

Logs should avoid sensitive data:

- Do not log SDP.
- Do not log tokens.
- Do not log frame data.
- Log only connection state and error codes.

## 18. Windows Deployment

Development run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tailscale Serve example:

```powershell
tailscale serve --bg 8000
```

Startup options:

1. Windows Task Scheduler.
2. NSSM wrapping `uvicorn`.
3. A small `pywin32` Windows service.

Recommended first version:

- Use Task Scheduler.
- Trigger: user logon.
- Action: run `scripts\run.ps1`.
- Restart on failure.

## 19. Milestones

### MVP 1: Local WebRTC Notify

- FastAPI starts.
- Browser creates notify DataChannel.
- Server sends `hello`, `ping`.
- Browser sends `pong`, `ack`.

### MVP 2: Motion Events

- OpenCV camera idle capture.
- Frame-difference motion detection.
- `motion_detected` event sent through notify DataChannel.
- Ack/retry implemented in memory.

### MVP 3: On-Demand Video

- Browser starts media PeerConnection.
- Python aiortc sends USB camera video.
- Media session closes cleanly.
- Camera returns to idle mode.

### MVP 4: Tailscale Deployment

- Service accessible through Tailnet.
- Token authentication enabled.
- Optional `tailscale serve` HTTPS enabled.
- Windows startup script added.

### MVP 5: Hardening

- Camera reconnect.
- Notify reconnect.
- Media timeout.
- Token rotation.
- Structured logs without sensitive data.

## 20. Key Design Decisions

1. Use Tailscale instead of public networking.
2. Use one long-lived DataChannel-only WebRTC connection for notifications.
3. Use independent short-lived WebRTC connections for audio/video.
4. Keep all event and media data in memory only.
5. Use application-level ack/retry on top of reliable DataChannel.
6. Keep the camera in low-frequency idle mode until motion or active viewing requires more work.

## 21. Recommended Implementation Order

Implement in the following order. Each phase should leave the application runnable and independently verifiable; do not begin camera/media work before the signaling, authentication, and connection-cleanup foundations are working.

### Phase 0: Project Foundation

1. Create the project layout from section 5 and add the virtual environment, `requirements.txt`, `.env.example`, and `scripts/run.ps1`.
2. Implement `config.py` with validated settings and fail fast when `APP_TOKEN` is missing or still uses the example value.
3. Implement `main.py`, application lifespan handling, `/health`, protected `/api/status`, and minimal structured logging.
4. Add a browser page that can enter a token and display service/connection status.

**Acceptance:** the service starts locally, `/health` is public, protected endpoints reject missing/invalid tokens, and no token is written to logs.

### Phase 1: Notify Signaling and DataChannel

1. Define the Pydantic request/response and DataChannel message models in `models.py`.
2. Implement `POST /api/notify/offer` and a `NotifyChannelManager` that creates, tracks, and closes one `RTCPeerConnection` per client.
3. Implement the browser notify PeerConnection and reliable ordered `notify` DataChannel.
4. On channel open, send `hello`; implement `ping`/`pong` and connection-state cleanup.
5. Add protocol tests for valid envelopes, invalid messages, and connection replacement for the same `client_id`.

**Acceptance:** a browser can establish and reconnect a notify channel, receive `hello` and `ping`, and return `pong` without resource leaks.

### Phase 2: Event Bus and Reliable Notification Delivery

1. Implement an in-process event bus with a typed `motion_detected` event.
2. Connect the event bus to the notify manager so events are broadcast only to healthy notify clients.
3. Implement the in-memory pending-ack map, `ack` processing, timeout/retry, and unhealthy-client handling exactly as defined in section 11.
4. Add browser-side immediate acknowledgements and visible notification UI.
5. Test normal acknowledgement, retry after a dropped acknowledgement, retry exhaustion, and service restart behavior.

**Acceptance:** a test event reaches the browser, is acknowledged, and is retried no more than `NOTIFY_MAX_RETRIES` when no acknowledgement arrives.

### Phase 3: Camera Ownership and Motion Detection

1. Implement `CameraManager` first, ensuring it is the only component allowed to open `cv2.VideoCapture`.
2. Implement idle-mode frame capture in a background thread, latest-frame sharing, mode-change locking, and camera error events.
3. Implement the frame-difference motion detector using frames supplied by `CameraManager`.
4. Add confirmation count, changed-area threshold, cooldown, and configuration-driven tuning.
5. Publish confirmed motion through the event bus; test the detector with synthetic/sample frames without requiring a physical camera.

**Acceptance:** with a USB camera connected, idle sampling detects sustained motion and emits one notification per cooldown window; camera failures do not stop the API service.

### Phase 4: On-Demand Media Session

1. Implement `POST /api/media/offer` and `POST /api/media/stop` with session IDs, token validation, and ownership checks.
2. Implement a `VideoStreamTrack` backed by `CameraManager`, reusing its latest frames rather than opening a second camera capture.
3. Implement `MediaSessionManager`: session tracking, PeerConnection state cleanup, idle timeout, and `media_state` notifications.
4. Switch the camera to `view` only while at least one media session is active; restore `idle` after the final session closes.
5. Add the browser View/Stop controls and verify repeated start/stop/reconnect flows.

**Acceptance:** the browser can view live USB video on demand, stopping the final session returns the camera to low-power mode, and notify remains connected throughout media failures.

### Phase 5: Tailscale and Windows Operation

1. Validate the complete flow via the Tailscale IP while binding only to the intended interface.
2. Configure `tailscale serve` if HTTPS and a stable Tailnet hostname are desired; keep Funnel disabled.
3. Add Task Scheduler installation documentation/script and verify startup, restart-on-failure, and camera recovery after logon.
4. Confirm the Tailnet ACL and application token both restrict access as intended.

**Acceptance:** an authorized remote Tailnet client receives motion alerts and starts video without public port forwarding.

### Phase 6: Hardening Before Daily Use

1. Add exponential-backoff notify reconnection in the browser and server-side stale-client cleanup.
2. Add camera reopen retries, media idle timeout, graceful shutdown, and token rotation procedure.
3. Review logs and tests to confirm SDP, tokens, frames, and event history are never persisted.
4. Add observability for only non-sensitive counters and state: client count, session count, camera mode, errors, and uptime.

**Acceptance:** disconnects, camera unplug/replug, service restart, and expired media sessions recover cleanly without storing monitoring data.

### Dependency Summary

```text
Foundation
  -> Notify signaling/DataChannel
    -> Event bus + ack/retry
      -> Camera manager + motion detection
        -> On-demand media
          -> Tailscale/Windows deployment
            -> Operational hardening
```

Audio is deliberately deferred until the video path is stable. It adds device-permission, capture, echo, and bandwidth concerns but does not change the dependency order above.
