import { wireFlipCamera } from "./flip-camera.js";
// Safari call page. Speaks the v1 WebSocket contract. Frames stay in memory only
// for the JPEG we are about to send; nothing is written to storage or disk.

import {
  OVERLAY_STALE_MS,
  drawOverlayCover,
  reduceOverlay,
  sharpCanvasSize,
  visibleOverlay,
} from "./overlay.js";
import { buildFrameMessage, createFrameLoop, frameOrientation, writeFrame } from "./live.js";

const BOARD_ID = "demo-b650";
const MAX_PHOTO_WIDTH = 640;

const video = document.getElementById("camera");
const canvas = document.getElementById("overlay");
const statusDot = document.getElementById("status");
const stepState = document.getElementById("step-state");
const stepItem = document.getElementById("step-item");
const stepHeader = document.getElementById("step-header");
const captionEl = document.getElementById("caption");
const verdictEl = document.getElementById("verdict");
const liveStatsEl = document.getElementById("live-stats");
const startBtn = document.getElementById("start-camera");
const pauseBtn = document.getElementById("pause-live");
const grab = document.createElement("canvas");

const sessionId = randomId();
let socket = null;
let seq = 0;
let overlayState = { seq: -1, overlay: null, atMs: 0 };
let staleTimer = 0;
let cameraOn = false;
let starting = false;
let shownFps = 0;
let shownRtt = null;

const loop = createFrameLoop({
  now: () => Date.now(),
  cameraOn: () => cameraOn,
  hidden: () => document.hidden,
  socketOpen: () => Boolean(socket && socket.readyState === WebSocket.OPEN),
  bufferedAmount: () => (socket ? socket.bufferedAmount : 0),
  captureAndSend: (now) => captureAndSend(now),
  onStats: (stats) => {
    shownFps = stats.fps;
    shownRtt = stats.rttMs;
    renderStats();
  },
});

function randomId() {
  if (globalThis.crypto && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function wsUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws?token=${encodeURIComponent(new URLSearchParams(location.search).get("token") || "")}`;
}

export function speak(text) {
  const synth = globalThis.speechSynthesis;
  if (!text || !synth || typeof SpeechSynthesisUtterance !== "function") return;
  const utter = new SpeechSynthesisUtterance(text);
  synth.cancel();
  synth.speak(utter);
}

function socketLabel() {
  if (!socket) return "closed";
  if (socket.readyState === WebSocket.OPEN) return "open";
  if (socket.readyState === WebSocket.CONNECTING) return "connecting";
  if (socket.readyState === WebSocket.CLOSING) return "closing";
  return "closed";
}

function renderStats() {
  const rtt = shownRtt == null ? "—" : `${shownRtt} ms`;
  liveStatsEl.textContent = `${shownFps} fps · ${rtt} · ${socketLabel()}`;
}

function setStatus(on) {
  statusDot.classList.toggle("on", on);
  statusDot.classList.toggle("off", !on);
  statusDot.setAttribute("aria-label", on ? "socket connected" : "socket disconnected");
  renderStats();
}

function setCaption(text, isError) {
  captionEl.textContent = text || "";
  captionEl.classList.toggle("bad", Boolean(isError));
}

function send(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function nextSeq() {
  seq += 1;
  return seq;
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  socket = new WebSocket(wsUrl());
  renderStats();
  socket.addEventListener("open", () => {
    setStatus(true);
    send({
      v: 1,
      type: "hello",
      session_id: sessionId,
      board_id: BOARD_ID,
      mode: "live",
      client: "web",
    });
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    loop.noteServer(message);
    onServer(message);
  });
  socket.addEventListener("close", () => {
    setStatus(false);
    socket = null;
    window.setTimeout(connect, 2000);
  });
  socket.addEventListener("error", () => {
    setStatus(false);
  });
}

function armOverlayExpiry() {
  window.clearTimeout(staleTimer);
  const wait = overlayState.atMs + OVERLAY_STALE_MS - Date.now();
  staleTimer = window.setTimeout(() => {
    if (visibleOverlay(overlayState, Date.now())) {
      armOverlayExpiry();
      return;
    }
    paint();
  }, Math.max(0, wait));
}

function onServer(message) {
  if (!message || typeof message !== "object") return;
  if (message.type === "overlay") {
    const next = reduceOverlay(overlayState, message, Date.now());
    if (next === overlayState) return;
    overlayState = next;
    armOverlayExpiry();
    paint();
    return;
  }
  if (message.type === "say") {
    setCaption(message.text, false);
    speak(message.text);
    return;
  }
  if (message.type === "step") {
    stepState.textContent = message.state || "—";
    stepItem.textContent = message.item ? String(message.item).replaceAll("_", " ") : "—";
    stepHeader.textContent = message.target_header || "—";
    return;
  }
  if (message.type === "verdict") {
    showVerdict(message);
    return;
  }
  if (message.type === "error") {
    setCaption(message.message || "Something went wrong.", true);
  }
}

function showVerdict(message) {
  verdictEl.classList.remove("hidden", "pass", "fail", "abstain");
  if (message.abstain) {
    verdictEl.textContent = "Can't tell";
    verdictEl.classList.add("abstain");
    return;
  }
  const pct = Math.round(Number(message.conf) * 100);
  const reason = message.reason ? `${message.reason} ` : "";
  if (message.pass) {
    verdictEl.textContent = `${reason}${pct}%`;
    verdictEl.classList.add("pass");
  } else {
    verdictEl.textContent = `${reason}${pct}%`;
    verdictEl.classList.add("fail");
  }
}

function paint() {
  const cssW = video.clientWidth || canvas.clientWidth;
  const cssH = video.clientHeight || canvas.clientHeight;
  if (!cssW || !cssH) return;
  const bitmap = sharpCanvasSize(cssW, cssH, window.devicePixelRatio || 1);
  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(bitmap.ratio, 0, 0, bitmap.ratio, 0, 0);
  const srcW = video.videoWidth || (lastPhotoSize && lastPhotoSize.w);
  const srcH = video.videoHeight || (lastPhotoSize && lastPhotoSize.h);
  // Stale boxes are cleared only in live mode; a photo's boxes stay until the next result.
  const overlay = cameraOn ? visibleOverlay(overlayState, Date.now()) : overlayState && overlayState.overlay;
  if (!srcW || !srcH) {
    ctx.clearRect(0, 0, cssW, cssH);
    return;
  }
  // The JPEG is a uniform scale of this frame, so 0..1 matches object-fit: cover.
  drawOverlayCover(ctx, overlay, srcW, srcH, cssW, cssH);
}

function rasterizePhoto(source, srcW, srcH) {
  const scale = srcW > MAX_PHOTO_WIDTH ? MAX_PHOTO_WIDTH / srcW : 1;
  const w = Math.max(1, Math.round(srcW * scale));
  const h = Math.max(1, Math.round(srcH * scale));
  const offscreen = document.createElement("canvas");
  offscreen.width = w;
  offscreen.height = h;
  const ctx = offscreen.getContext("2d");
  ctx.drawImage(source, 0, 0, w, h);
  const dataUrl = offscreen.toDataURL("image/jpeg", 0.7);
  const comma = dataUrl.indexOf(",");
  return { w, h, jpeg_b64: dataUrl.slice(comma + 1) };
}

function captureAndSend(now) {
  if (!cameraOn || loop.isPaused() || document.hidden) return null;
  if (!socket || socket.readyState !== WebSocket.OPEN) return null;
  if (socket.bufferedAmount > 0) return null;
  if (!video.videoWidth || !video.videoHeight) return null;
  const shot = writeFrame(grab, video, video.videoWidth, video.videoHeight);
  if (!shot || !shot.jpeg_b64) return null;
  const sentSeq = nextSeq();
  const orientationType = globalThis.screen && screen.orientation ? screen.orientation.type : "";
  const ok = send(buildFrameMessage({
    sessionId,
    seq: sentSeq,
    tsMs: now,
    w: shot.w,
    h: shot.h,
    orientation: frameOrientation(orientationType, window.innerWidth, window.innerHeight),
    jpeg_b64: shot.jpeg_b64,
  }));
  if (!ok) return null;
  return { seq: sentSeq, tsMs: now };
}

// Size of the last photo sent, so its overlay can be drawn when no camera is running.
let lastPhotoSize = null;

function sendPhoto(shot) {
  lastPhotoSize = { w: shot.w, h: shot.h };
  const ok = send({
    v: 1,
    type: "photo",
    session_id: sessionId,
    seq: nextSeq(),
    w: shot.w,
    h: shot.h,
    jpeg_b64: shot.jpeg_b64,
  });
  if (!ok) setCaption("Not connected.", true);
}

function confirm(what) {
  const ok = send({
    v: 1,
    type: "confirm",
    session_id: sessionId,
    what,
    value: true,
  });
  if (!ok) setCaption("Not connected.", true);
}

function stopCamera() {
  const stream = video.srcObject;
  if (stream && typeof stream.getTracks === "function") {
    for (const track of stream.getTracks()) track.stop();
  }
  video.srcObject = null;
  cameraOn = false;
}

const flipCamera = wireFlipCamera({
  video, button: document.getElementById("flip-camera"),
  active: () => cameraOn, busy: () => starting,
  onError: message => setCaption(message, true),
});

async function startCamera() {
  if (starting || cameraOn) return;
  if (!window.isSecureContext) {
    setCaption("Camera needs HTTPS or localhost. Use Upload photo.", false);
    return;
  }
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    setCaption("No camera in this browser. Use Upload photo.", false);
    return;
  }
  starting = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: flipCamera.constraints({ facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } }),
      audio: false,
    });
    video.srcObject = stream;
    video.playsInline = true;
    video.muted = true;
    await video.play();
    cameraOn = true;
    void flipCamera.refresh();
    startBtn.disabled = true;
    pauseBtn.disabled = false;
    loop.start();
    setCaption("", false);
  } catch (err) {
    stopCamera();
    const name = err && err.name;
    if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
      setCaption("Camera blocked. Allow camera access, or use Upload photo.", false);
    } else {
      setCaption("Camera unavailable. Use Upload photo.", false);
    }
  } finally {
    starting = false;
  }
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("image"));
    image.src = url;
  });
}

async function onUpload(event) {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const url = URL.createObjectURL(file);
  try {
    const image = await loadImage(url);
    sendPhoto(rasterizePhoto(image, image.naturalWidth, image.naturalHeight));
  } catch {
    setCaption("Could not read that photo.", true);
  } finally {
    URL.revokeObjectURL(url);
  }
}

startBtn.addEventListener("click", () => {
  startCamera();
});
pauseBtn.addEventListener("click", () => {
  const paused = loop.togglePaused();
  pauseBtn.textContent = paused ? "Resume" : "Pause";
});
document.getElementById("power-off").addEventListener("click", () => confirm("power_off"));
document.getElementById("check-again").addEventListener("click", () => confirm("check_again"));
document.getElementById("send-photo").addEventListener("click", () => {
  if (!video.videoWidth) {
    setCaption("No camera frame. Use Upload photo.", true);
    return;
  }
  sendPhoto(rasterizePhoto(video, video.videoWidth, video.videoHeight));
});
document.getElementById("upload").addEventListener("change", (event) => {
  onUpload(event);
});

document.addEventListener("visibilitychange", () => {
  loop.setHidden(document.hidden);
});

window.addEventListener("resize", paint);
video.addEventListener("loadedmetadata", paint);
connect();
paint();
renderStats();
