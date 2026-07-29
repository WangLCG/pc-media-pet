const tokenInput = document.querySelector("#token");
const result = document.querySelector("#result");
const connectButton = document.querySelector("#connect-notify");
const alerts = document.querySelector("#alerts");
const notifyStatus = document.querySelector("#notify-status");
const streamStatus = document.querySelector("#stream-status");
const startStreamButton = document.querySelector("#start-stream");
const stopStreamButton = document.querySelector("#stop-stream");
const remoteVideo = document.querySelector("#remote-video");
let notifyPeerConnection;
let mediaPeerConnection;
let mediaSessionId;
let reconnectTimer;
let reconnectAttempt = 0;
let alertAudioContext;
const seenNotificationIds = new Set();
const maxSeenNotificationIds = 256;
const iceConfiguration = { iceServers: [], iceCandidatePoolSize: 1 };
const iceGatheringTimeoutMs = 1500;

function setButtonState(button, label, { loading = false, disabled = loading } = {}) {
  button.querySelector(".button-label").textContent = label;
  button.classList.toggle("is-loading", loading);
  button.disabled = disabled;
  button.setAttribute("aria-busy", String(loading));
}

function setStatus(statusElement, label, state) {
  statusElement.textContent = label;
  statusElement.dataset.state = state;
}

function addAlert(message) {
  alerts.querySelector(".empty-alerts")?.remove();
  const alert = document.createElement("p");
  alert.textContent = message;
  alerts.prepend(alert);
}

function rememberNotification(messageId) {
  if (seenNotificationIds.has(messageId)) return false;
  seenNotificationIds.add(messageId);
  if (seenNotificationIds.size > maxSeenNotificationIds) seenNotificationIds.delete(seenNotificationIds.values().next().value);
  return true;
}

function formatOccurrenceTime(message) {
  if (/^\d{2}:\d{2}:\d{2}$/.test(message.payload?.occurred_at_hhmmss || "")) return message.payload.occurred_at_hhmmss;
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(new Date(message.ts * 1000));
}

function prepareAlertAudio() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  alertAudioContext ??= new AudioContext();
  if (alertAudioContext.state === "suspended") void alertAudioContext.resume();
}

function playMotionAlert() {
  if (!alertAudioContext || alertAudioContext.state !== "running") return;
  const startAt = alertAudioContext.currentTime;
  for (const offset of [0, 0.24]) {
    const oscillator = alertAudioContext.createOscillator();
    const gain = alertAudioContext.createGain();
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, startAt + offset);
    gain.gain.exponentialRampToValueAtTime(0.16, startAt + offset + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + offset + 0.16);
    oscillator.connect(gain).connect(alertAudioContext.destination);
    oscillator.start(startAt + offset);
    oscillator.stop(startAt + offset + 0.17);
  }
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
    const finish = () => { window.clearTimeout(timer); peerConnection.removeEventListener("icegatheringstatechange", onStateChange); resolve(); };
    const onStateChange = () => { if (peerConnection.iceGatheringState === "complete") finish(); };
    peerConnection.addEventListener("icegatheringstatechange", onStateChange);
    timer = window.setTimeout(finish, timeoutMs);
  });
}

function scheduleReconnect() {
  if (!tokenInput.value.trim() || reconnectTimer) return;
  const delay = Math.min(30000, 1000 * (2 ** reconnectAttempt));
  reconnectAttempt += 1;
  reconnectTimer = window.setTimeout(() => { reconnectTimer = undefined; connectNotifications(); }, delay);
}

async function connectNotifications() {
  const token = tokenInput.value.trim();
  if (!token) { result.textContent = "请输入应用令牌后连接。"; return; }
  prepareAlertAudio();
  setButtonState(connectButton, "通知连接中", { loading: true });
  setStatus(notifyStatus, "连接中", "connecting");
  result.textContent = "正在建立通知通道…";

  if (notifyPeerConnection) notifyPeerConnection.close();
  const peerConnection = new RTCPeerConnection(iceConfiguration);
  notifyPeerConnection = peerConnection;
  const channel = peerConnection.createDataChannel("notify", { ordered: true });
  channel.addEventListener("open", () => {
    reconnectAttempt = 0;
    setButtonState(connectButton, "通知已连接", { disabled: false });
    setStatus(notifyStatus, "已连接", "connected");
    result.textContent = "通知通道已连接。";
  });
  channel.addEventListener("message", ({ data }) => {
    try {
      const message = JSON.parse(data);
      if (message.type === "hello") result.textContent = "通知通道已连接。";
      if (message.type === "ping") channel.send(JSON.stringify({ version: 1, type: "pong", id: `pong_${crypto.randomUUID()}`, ts: Math.floor(Date.now() / 1000), payload: { ping_id: message.id } }));
      if (["motion_detected", "sound_detected", "camera_error", "media_state"].includes(message.type)) {
        channel.send(JSON.stringify({ version: 1, type: "ack", id: `ack_${crypto.randomUUID()}`, ts: Math.floor(Date.now() / 1000), payload: { message_id: message.id, status: "received" } }));
        if (!rememberNotification(message.id)) return;
      }
      if (message.type === "motion_detected") { addAlert(`${formatOccurrenceTime(message)} 检测到${message.payload.zone}区域有移动。`); playMotionAlert(); }
      if (message.type === "sound_detected") { addAlert(`${formatOccurrenceTime(message)} 检测到较大声音（${message.payload.rms_dbfs.toFixed(1)} dBFS）。`); playMotionAlert(); }
      if (message.type === "camera_error") addAlert(`摄像头不可用：${message.payload.message}`);
      if (message.type === "media_state") result.textContent = `摄像头串流状态：${message.payload.state}。`;
    } catch { result.textContent = "收到无效的通知消息。"; }
  });
  peerConnection.addEventListener("connectionstatechange", () => {
    if (notifyPeerConnection !== peerConnection) return;
    if (["failed", "disconnected", "closed"].includes(peerConnection.connectionState)) {
      setButtonState(connectButton, "正在重新连接", { loading: true });
      setStatus(notifyStatus, "重连中", "connecting");
      result.textContent = "通知通道已断开，正在重新连接…";
      scheduleReconnect();
    }
  });

  try {
    await peerConnection.setLocalDescription(await peerConnection.createOffer());
    await waitForIceGatheringComplete(peerConnection);
    const response = await fetch("/api/notify/offer", { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ client_id: createClientId(), sdp: peerConnection.localDescription.sdp, type: "offer" }) });
    if (!response.ok) throw new Error(`Signaling failed (${response.status})`);
    await peerConnection.setRemoteDescription(await response.json());
    result.textContent = "通知通道协商完成，正在连接…";
  } catch {
    if (notifyPeerConnection !== peerConnection) return;
    setButtonState(connectButton, "正在重新连接", { loading: true });
    setStatus(notifyStatus, "重连中", "connecting");
    result.textContent = "无法连接通知通道，正在重试…";
    scheduleReconnect();
  }
}

function markStreamPlaying(peerConnection) {
  if (mediaPeerConnection !== peerConnection) return;
  setButtonState(startStreamButton, "摄像头画面播放中", { disabled: true });
  stopStreamButton.disabled = false;
  setStatus(streamStatus, "播放中", "playing");
}

async function startStream() {
  const token = tokenInput.value.trim();
  if (!token) { result.textContent = "请先输入应用令牌。"; return; }
  await stopStream(false);
  setButtonState(startStreamButton, "摄像头连接中", { loading: true });
  setStatus(streamStatus, "连接中", "connecting");
  result.textContent = "正在连接摄像头画面…";
  const peerConnection = new RTCPeerConnection(iceConfiguration);
  mediaPeerConnection = peerConnection;
  peerConnection.addTransceiver("video", { direction: "recvonly" });
  peerConnection.addEventListener("track", ({ streams }) => {
    if (mediaPeerConnection !== peerConnection) return;
    remoteVideo.srcObject = streams[0];
    remoteVideo.hidden = false;
    markStreamPlaying(peerConnection);
    result.textContent = "摄像头画面正在播放。";
  });
  peerConnection.addEventListener("connectionstatechange", () => {
    if (mediaPeerConnection !== peerConnection) return;
    if (peerConnection.connectionState === "connected") markStreamPlaying(peerConnection);
    if (peerConnection.connectionState === "failed") {
      result.textContent = "摄像头连接失败。";
      void stopStream(false);
      setStatus(streamStatus, "连接失败", "error");
    }
  });
  try {
    await peerConnection.setLocalDescription(await peerConnection.createOffer());
    await waitForIceGatheringComplete(peerConnection);
    const response = await fetch("/api/media/offer", { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ client_id: createClientId(), sdp: peerConnection.localDescription.sdp, type: "offer", video: true, audio: false }) });
    if (!response.ok) throw new Error("media offer failed");
    const answer = await response.json();
    mediaSessionId = answer.session_id;
    await peerConnection.setRemoteDescription(answer);
    stopStreamButton.disabled = false;
    if (mediaPeerConnection === peerConnection && remoteVideo.hidden) {
      result.textContent = "摄像头串流协商完成，正在连接…";
    }
  } catch {
    if (mediaPeerConnection === peerConnection) await stopStream(false);
    setStatus(streamStatus, "连接失败", "error");
    result.textContent = "无法启动摄像头画面。";
  }
}

async function stopStream(notifyServer = true) {
  const sessionId = mediaSessionId;
  mediaSessionId = undefined;
  mediaPeerConnection?.close();
  mediaPeerConnection = undefined;
  remoteVideo.srcObject = null;
  remoteVideo.hidden = true;
  setButtonState(startStreamButton, "查看摄像头", { disabled: false });
  stopStreamButton.disabled = true;
  if (notifyServer) {
    setStatus(streamStatus, "已停止", "idle");
    result.textContent = "摄像头画面已停止。";
  }
  if (notifyServer && sessionId && tokenInput.value.trim()) await fetch("/api/media/stop", { method: "POST", headers: { Authorization: `Bearer ${tokenInput.value.trim()}`, "Content-Type": "application/json" }, body: JSON.stringify({ client_id: createClientId(), session_id: sessionId }) });
}

connectButton.addEventListener("click", connectNotifications);
startStreamButton.addEventListener("click", startStream);
stopStreamButton.addEventListener("click", () => { void stopStream(); });
window.addEventListener("beforeunload", () => { notifyPeerConnection?.close(); mediaPeerConnection?.close(); });
