// Input and output device choice: which mic, camera and speaker the call uses.
// Pure helpers first (tested in devices.test.mjs), then a small level preview for the Settings meter.

export const STORE_KEYS = Object.freeze({ audioinput: "aeye.mic", videoinput: "aeye.camera", audiooutput: "aeye.speaker" });
const NAMES = { audioinput: "Microphone", videoinput: "Camera", audiooutput: "Speaker" };

// Before the browser grants access it lists devices with empty ids and labels.
export function devicesLocked(devices, kind) {
  const list = (devices || []).filter((d) => d && d.kind === kind);
  return list.length > 0 && list.every((d) => !d.deviceId || !d.label);
}

// Options for one list: "" means "the browser default". Chrome's own "default"/"communications" aliases are
// folded into that first entry so the same physical mic is not listed three times.
export function deviceOptions(devices, kind) {
  const list = (devices || []).filter((d) => d && d.kind === kind && d.deviceId);
  const alias = list.find((d) => d.deviceId === "default");
  const defaultLabel = alias && alias.label ? alias.label.replace(/^Default - /, "") : "";
  const out = [{ id: "", label: defaultLabel ? `Default (${defaultLabel})` : "Default" }];
  let n = 0;
  for (const d of list) {
    if (d.deviceId === "default" || d.deviceId === "communications") continue;
    n += 1;
    out.push({ id: d.deviceId, label: d.label || `${NAMES[kind] || "Device"} ${n}` });
  }
  return out;
}

// Which device to use: the saved one while it is still plugged in, else the default.
// gone is true only when the saved device really disappeared (not while the list is still locked).
export function chooseDevice(devices, kind, savedId) {
  const saved = String(savedId || "");
  if (!saved) return { id: "", gone: false };
  if (devicesLocked(devices, kind)) return { id: saved, gone: false };
  const present = (devices || []).some((d) => d && d.kind === kind && d.deviceId === saved);
  return present ? { id: saved, gone: false } : { id: "", gone: true };
}

export function deviceLabel(devices, kind, id) {
  const options = deviceOptions(devices, kind);
  const hit = options.find((o) => o.id === String(id || ""));
  return hit ? hit.label : options[0].label;
}

export function audioConstraints(base, deviceId) {
  const audio = { ...(base || {}) };
  if (deviceId) audio.deviceId = { exact: deviceId };
  return { audio };
}

export function videoConstraints(deviceId) {
  const size = { width: { ideal: 1280 }, height: { ideal: 720 } };
  return deviceId ? { deviceId: { exact: deviceId }, ...size } : { facingMode: "environment", ...size };
}

// A chosen device that is missing fails with one of these; the page then falls back to the default.
export function isMissingDevice(err) {
  const name = String((err && (err.name || err.message)) || "");
  return name === "OverconstrainedError" || name === "NotFoundError";
}

export function loadChoice(storage, kind) {
  try {
    return (storage && storage.getItem(STORE_KEYS[kind])) || "";
  } catch {
    return "";
  }
}

export function saveChoice(storage, kind, id) {
  try {
    if (!storage) return;
    if (id) storage.setItem(STORE_KEYS[kind], id);
    else storage.removeItem(STORE_KEYS[kind]);
  } catch {
    // private window or blocked storage: the choice lasts for this page only
  }
}

export function micLine(label, on) {
  const name = String(label || "").trim() || "Default";
  return on ? `Mic: ${name}` : `Mic off · ${name}`;
}

export function sinkSupported(scope = globalThis) {
  const Ctor = scope.AudioContext || scope.webkitAudioContext;
  return Boolean(Ctor && Ctor.prototype && typeof Ctor.prototype.setSinkId === "function");
}

// Settings meter while the call mic is off: opens the chosen mic, reports a 0..1 level, stops cleanly.
export function createLevelPreview({ getUserMedia, makeContext, onLevel, base }) {
  let gen = 0;
  let stream = null;
  let ctx = null;
  let timer = 0;
  let label = "";
  function stop() {
    gen += 1;
    if (timer) clearTimeout(timer);
    timer = 0;
    if (stream) for (const t of stream.getTracks()) t.stop();
    stream = null;
    if (ctx && typeof ctx.close === "function") ctx.close().catch(() => {});
    ctx = null;
    label = "";
  }
  async function start(deviceId) {
    stop();
    const token = gen;
    const got = await getUserMedia(audioConstraints(base, deviceId));
    if (token !== gen) {
      for (const t of got.getTracks()) t.stop();
      return "";
    }
    stream = got;
    const track = got.getAudioTracks()[0];
    label = (track && track.label) || "";
    ctx = makeContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(got).connect(analyser);
    const bins = new Float32Array(analyser.fftSize);
    let smooth = 0;
    const tick = () => {
      if (token !== gen) return;
      analyser.getFloatTimeDomainData(bins);
      let sum = 0;
      for (let i = 0; i < bins.length; i += 1) sum += bins[i] * bins[i];
      smooth = smooth * 0.72 + Math.sqrt(sum / bins.length) * 0.28;
      onLevel(Math.min(1, smooth / 0.12));
      timer = setTimeout(tick, 50);
    };
    tick();
    return label;
  }
  return { start, stop, active: () => Boolean(stream), label: () => label };
}
