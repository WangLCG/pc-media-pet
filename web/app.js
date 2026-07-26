const tokenInput = document.querySelector("#token");
const result = document.querySelector("#result");
const connectButton = document.querySelector("#connect-notify");
const alerts = document.querySelector("#alerts");
const startStreamButton = document.querySelector("#start-stream");
const stopStreamButton = document.querySelector("#stop-stream");
const remoteVideo = document.querySelector("#remote-video");
let notifyPeerConnection;
let mediaPeerConnection;
let mediaSessionId;
let reconnectTimer;
let reconnectAttempt = 0;
const seenNotificationIds = new Set();
const maxSeenNotificationIds = 256;
const iceConfiguration = { iceServers: [], iceCandidatePoolSize: 1 };
const iceGatheringTimeoutMs = 1500;

function rememberNotification(messageId) {
  if (seenNotificationIds.has(messageId)) return false;
  seenNotificationIds.add(messageId);
  if (seenNotificationIds.size > maxSeenNotificationIds) {
    seenNotificationIds.delete(seenNotificationIds.values().next().value);
  }
  return true;
}

function createClientId() {
  const savedId = sessionStorage.getItem("pc-media-pet-client-id");
  if (savedId) return savedId;
  const clientId = `browser-${crypto.randomUUID()}`;
  sessionStorage.setItem("pc-media-pet-client-id", clientId);
  return clientId;
}

function waitForIceGatheringComplete(peerConnection, timeoutMs = iceGatheringTimeoutMs) {
  if (peerConnection.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let timer;
    const finish = () => {
      window.clearTimeout(timer);
      peerConnection.removeEventListener("icegatheringstatechange", onStateChange);
      resolve();
    };
    const onStateChange = () => {
      if (peerConnection.iceGatheringState === "complete") finish();
    };
    peerConnection.addEventListener("icegatheringstatechange", onStateChange);
    timer = window.setTimeout(finish, timeoutMs);
  });
}

function scheduleReconnect() {
  if (!tokenInput.value.trim() || reconnectTimer) return;
  const delay = Math.min(30000, 1000 * (2 ** reconnectAttempt));
  reconnectAttempt += 1;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined;
    connectNotifications();
  }, delay);
}

async function connectNotifications() {
  const token = tokenInput.value.trim();
  if (!token) {
    result.textContent = "Enter an application token.";
    return;
  }

  if (notifyPeerConnection) notifyPeerConnection.close();
  const peerConnection = new RTCPeerConnection(iceConfiguration);
  notifyPeerConnection = peerConnection;
  const channel = peerConnection.createDataChannel("notify", { ordered: true });
  channel.addEventListener("open", () => {
    reconnectAttempt = 0;
    result.textContent = "Notifications connected.";
    connectButton.textContent = "Notifications connected";
  });
  channel.addEventListener("message", ({ data }) => {
    try {
      const message = JSON.parse(data);
      if (message.type === "hello") result.textContent = "Notifications connected.";
      if (message.type === "ping") {
        channel.send(JSON.stringify({
          version: 1,
          type: "pong",
          id: `pong_${crypto.randomUUID()}`,
          ts: Math.floor(Date.now() / 1000),
          payload: { ping_id: message.id },
        }));
      }
      if (["motion_detected", "camera_error", "media_state"].includes(message.type)) {
        channel.send(JSON.stringify({
          version: 1,
          type: "ack",
          id: `ack_${crypto.randomUUID()}`,
          ts: Math.floor(Date.now() / 1000),
          payload: { message_id: message.id, status: "received" },
        }));
        if (!rememberNotification(message.id)) return;
      }
      if (message.type === "motion_detected") {
        const alert = document.createElement("p");
        alert.textContent = `Motion detected in ${message.payload.zone}.`;
        alerts.prepend(alert);
      }
      if (message.type === "camera_error") {
        const alert = document.createElement("p");
        alert.textContent = `Camera unavailable: ${message.payload.message}`;
        alerts.prepend(alert);
      }
      if (message.type === "media_state") result.textContent = `Camera stream ${message.payload.state}.`;
    } catch {
      result.textContent = "Received an invalid notification message.";
    }
  });
  peerConnection.addEventListener("connectionstatechange", () => {
    if (notifyPeerConnection !== peerConnection) return;
    if (["failed", "disconnected", "closed"].includes(peerConnection.connectionState)) {
      result.textContent = "Notifications disconnected; reconnecting...";
      connectButton.textContent = "Connect notifications";
      scheduleReconnect();
    }
  });

  try {
    await peerConnection.setLocalDescription(await peerConnection.createOffer());
    await waitForIceGatheringComplete(peerConnection);
    const response = await fetch("/api/notify/offer", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: createClientId(), sdp: peerConnection.localDescription.sdp, type: "offer" }),
    });
    if (!response.ok) throw new Error(`Signaling failed (${response.status})`);
    const answer = await response.json();
    await peerConnection.setRemoteDescription(answer);
    result.textContent = "Connecting notifications...";
  } catch {
    if (notifyPeerConnection !== peerConnection) return;
    result.textContent = "Unable to connect notifications; retrying...";
    scheduleReconnect();
  }
}

connectButton.addEventListener("click", connectNotifications);
startStreamButton.addEventListener("click", startStream);
stopStreamButton.addEventListener("click", () => stopStream());
window.addEventListener("beforeunload", () => { notifyPeerConnection?.close(); mediaPeerConnection?.close(); });

async function startStream() {
  const token = tokenInput.value.trim();
  if (!token) return;
  await stopStream(false);
  const peerConnection = new RTCPeerConnection(iceConfiguration);
  mediaPeerConnection = peerConnection;
  peerConnection.addTransceiver("video", { direction: "recvonly" });
  peerConnection.addEventListener("track", ({ streams }) => { remoteVideo.srcObject = streams[0]; });
  peerConnection.addEventListener("connectionstatechange", () => {
    // "disconnected" is a transient ICE state. Closing the connection here
    // can stop a healthy stream after its first frame while ICE recovers.
    if (mediaPeerConnection === peerConnection && peerConnection.connectionState === "failed") {
      result.textContent = "Camera stream connection failed.";
      stopStream(false);
    }
  });
  try {
    await peerConnection.setLocalDescription(await peerConnection.createOffer());
    await waitForIceGatheringComplete(peerConnection);
    const response = await fetch("/api/media/offer", {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: createClientId(), sdp: peerConnection.localDescription.sdp, type: "offer", video: true, audio: false }),
    });
    if (!response.ok) throw new Error("media offer failed");
    const answer = await response.json();
    mediaSessionId = answer.session_id;
    await peerConnection.setRemoteDescription(answer);
    startStreamButton.disabled = true;
    stopStreamButton.disabled = false;
  } catch {
    if (mediaPeerConnection === peerConnection) await stopStream(false);
    result.textContent = "Unable to start camera stream.";
  }
}

async function stopStream(notifyServer = true) {
  const sessionId = mediaSessionId;
  mediaSessionId = undefined;
  mediaPeerConnection?.close();
  mediaPeerConnection = undefined;
  remoteVideo.srcObject = null;
  startStreamButton.disabled = false;
  stopStreamButton.disabled = true;
  if (notifyServer && sessionId && tokenInput.value.trim()) {
    await fetch("/api/media/stop", {
      method: "POST", headers: { Authorization: `Bearer ${tokenInput.value.trim()}`, "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: createClientId(), session_id: sessionId }),
    });
  }
}
