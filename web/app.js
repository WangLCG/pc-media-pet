const tokenInput = document.querySelector("#token");
const result = document.querySelector("#result");
const connectButton = document.querySelector("#connect-notify");
let notifyPeerConnection;
let reconnectTimer;

function createClientId() {
  const savedId = sessionStorage.getItem("pc-media-pet-client-id");
  if (savedId) return savedId;
  const clientId = `browser-${crypto.randomUUID()}`;
  sessionStorage.setItem("pc-media-pet-client-id", clientId);
  return clientId;
}

function waitForIceGatheringComplete(peerConnection) {
  if (peerConnection.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    peerConnection.addEventListener("icegatheringstatechange", () => {
      if (peerConnection.iceGatheringState === "complete") resolve();
    });
  });
}

function scheduleReconnect() {
  if (!tokenInput.value.trim() || reconnectTimer) return;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined;
    connectNotifications();
  }, 2000);
}

async function connectNotifications() {
  const token = tokenInput.value.trim();
  if (!token) {
    result.textContent = "Enter an application token.";
    return;
  }

  if (notifyPeerConnection) notifyPeerConnection.close();
  const peerConnection = new RTCPeerConnection({ iceServers: [] });
  notifyPeerConnection = peerConnection;
  const channel = peerConnection.createDataChannel("notify", { ordered: true });
  channel.addEventListener("open", () => {
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
window.addEventListener("beforeunload", () => notifyPeerConnection?.close());
