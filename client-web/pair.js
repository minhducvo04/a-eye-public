import { drawQr } from './qr.js';
import { writeFrame } from './live.js';
import { createVoice } from './voice.js';

export function pairMode(search) {
  const query = new URLSearchParams(search);
  const mine = query.get('phone') === 'mine';
  return { camera: query.has('pair') || mine, code: query.get('pair') || '', token: query.get('token') || '',
    join: query.get('join') || '', mine };
}

export const REMEMBER_KEY = 'aeye.phone.remember';
export function loadRemember(storage) {
  try { return storage?.getItem(REMEMBER_KEY) === '1'; } catch { return false; }
}
export function saveRemember(storage, on) {
  try { storage?.setItem(REMEMBER_KEY, on ? '1' : '0'); } catch { /* private window: this page only */ }
}

// Remember this phone: what the phone does after a pair_status. live = send camera + mic; wait = idle, then try
// again without a code; stop = the old one-shot behaviour.
export function rememberStep(msg, remember) {
  if (msg.state === 'joined') return { action: 'live', text: 'Camera + mic connected' };
  if (msg.state === 'camera') return { action: 'same' };
  if (remember && msg.retry_s) {
    const text = msg.detail === 'Call ended.' ? 'Call ended. Waiting for your next call.' : msg.detail || 'Waiting for your next call.';
    return { action: 'wait', delayMs: msg.retry_s * 1000, text };
  }
  return { action: 'stop', text: pairStatus(msg, true) };
}
export function pairUrl(origin, path) {
  const url = new URL(path, origin);
  url.searchParams.delete('token');
  return url.href;
}
export function pairStatus(message, camera = false) {
  if (message.state === 'joined') return camera ? 'Camera + mic connected' : 'Phone camera + mic connected';
  if (message.state === 'camera') return message.camera_on ? 'Phone camera on' : 'Phone camera off';
  if (message.state === 'left') return camera ? 'Call ended. Request a new code to reconnect.' : 'Phone disconnected; using this device’s camera.';
  return message.detail || 'Unable to pair. Request a new code.';
}

// The laptop's mic while a phone is paired: the phone's mic replaces it, and leaving restores it.
// route is { phone, resume }; returns the next route and what to do with the local mic.
export function phoneMicRoute(route, message, localMicOn) {
  const r = route || { phone: false, resume: false };
  if (!message || message.type !== 'pair_status' || message.role !== 'call') return { route: r, action: null };
  if (message.state === 'joined' && !r.phone) {
    return { route: { phone: true, resume: Boolean(localMicOn) }, action: localMicOn ? 'stop_local' : null };
  }
  if (message.state === 'left' && r.phone) {
    return { route: { phone: false, resume: false }, action: r.resume ? 'start_local' : null };
  }
  return { route: r, action: null };
}

// Phone side: utterance chunks go out as audio_in. barge_in goes first, at voice.js's barge point (400 ms of
// speech, 700 ms in a loud room); chunks wait for it so the server's barge_in reset never drops this
// utterance's start. An utterance that ends before its barge point is sent without one.
export function createPhoneAudio(send) {
  let held = [];
  let barged = false;
  const out = (seq, pcm16_b64, final) => send({ type: 'audio_in', seq, sample_rate: 16000, pcm16_b64, final });
  return {
    chunk(pcm16_b64, seq, final) {
      if (barged) out(seq, pcm16_b64, final);
      else held.push([seq, pcm16_b64, final]);
      if (final) {
        for (const item of held) out(...item);
        held = [];
        barged = false;
      }
    },
    bargePoint() {
      if (barged) return;
      barged = true;
      send({ type: 'barge_in', utterance_id: 'phone', at_ms: 0 });
      for (const item of held) out(...item);
      held = [];
    },
  };
}

export function setupPairCall(send, sessionId) {
  const button = document.getElementById('pair-request');
  const status = document.getElementById('pair-status');
  button?.addEventListener('click', () => {
    const id = typeof sessionId === 'function' ? sessionId() : sessionId;
    if (!send({ v: 2, type: 'pair_request', session_id: id })) status.textContent = 'Start a call first.';
  });
  return message => {
    if (message.type === 'pair_status') status.textContent = pairStatus(message);
    if (message.type !== 'pair') return;
    const url = pairUrl(location.origin, message.path, location.search);
    status.textContent = `Code ${message.code} · expires in ${message.expires_in_s} seconds`;
    const link = document.getElementById('pair-link');
    link.href = url; link.textContent = url;
    const canvas = document.getElementById('pair-qr');
    canvas.hidden = false;
    try { drawQr(canvas, url); } catch (error) { canvas.hidden = true; status.textContent += ` · ${error.message}`; }
  };
}

// An isolated entry point: never initializes call UI or pack selection. One tap starts the camera and the mic;
// the mic runs voice.js's VAD and sends utterances for server speech recognition. The phone never plays audio.
// "Always use this phone as my camera": after the first tap the page stays open; whenever the same signed-in
// person starts a call, it joins without a code (server: pairing.take_remembered).
export function bootPairCamera({ makeVoice = createVoice, storage = globalThis.localStorage } = {}) {
  const mode = pairMode(location.search);
  if (!mode.camera) return false;
  let remember = mode.mine || loadRemember(storage);
  document.body.replaceChildren();
  document.body.style.cssText = 'margin:0;background:#000;color:#fff;font:17px -apple-system,system-ui,sans-serif;text-align:center';
  const video = document.createElement('video');
  video.autoplay = video.muted = video.playsInline = true;
  video.style.cssText = 'width:100%;max-height:60vh;object-fit:contain;background:#111';
  const button = document.createElement('button');
  button.textContent = 'Start'; button.style.cssText = 'margin:16px;padding:20px 48px;font-size:24px;border-radius:16px;border:0;background:#8EB6FF;color:#000';
  const meter = document.createElement('div');
  meter.setAttribute('role', 'meter'); meter.setAttribute('aria-label', 'Microphone level');
  meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
  meter.style.cssText = 'margin:8px 16px;height:28px;border-radius:14px;background:rgba(255,255,255,0.15);overflow:hidden';
  const fill = document.createElement('div');
  fill.id = 'phone-level';
  fill.style.cssText = 'height:100%;width:0;background:#8EB6FF;transition:width 60ms linear';
  meter.append(fill);
  const status = document.createElement('p'); status.setAttribute('role', 'status'); status.id = 'phone-status';
  status.style.cssText = 'margin:12px 16px;font-size:20px';
  status.textContent = remember ? 'Tap Start once. This phone then joins your calls by itself.' : 'Tap Start to use this phone as the camera and mic.';
  const label = document.createElement('label');
  label.style.cssText = 'display:flex;gap:12px;align-items:center;justify-content:center;margin:12px 16px;font-size:17px';
  const toggle = document.createElement('input');
  toggle.type = 'checkbox'; toggle.id = 'phone-remember'; toggle.setAttribute('role', 'switch'); toggle.checked = remember;
  toggle.style.cssText = 'width:24px;height:24px';
  const words = document.createElement('span');
  words.textContent = 'Always use this phone as my camera';
  label.append(toggle, words);
  const note = document.createElement('p');
  note.style.cssText = 'margin:0 16px;font-size:13px;color:rgba(255,255,255,0.6)';
  note.textContent = 'Works when this phone and your call are signed in with the same account. Keep this page open.';
  document.body.append(video, button, meter, status, label, note);
  const grab = document.createElement('canvas');
  let sessionId = globalThis.crypto.randomUUID();
  let socket, stream, timer, retry, voice, seq = 0, paired = false, terminal = false, signIn = false, usedCode = false;
  const send = msg => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ v: 2, session_id: sessionId, ...msg })); };
  const audio = createPhoneAudio(msg => { if (paired) send(msg); });
  function showLevel(level) {
    const pct = Math.round(Math.max(0, Math.min(1, Number(level) || 0)) * 100);
    fill.style.width = `${pct}%`;
    meter.setAttribute('aria-valuenow', String(pct));
  }
  function stop() {
    clearInterval(timer); clearTimeout(retry);
    stream?.getTracks().forEach(track => track.stop()); stream = null; video.srcObject = null;
    voice?.stopListening(); showLevel(0);
    button.textContent = 'Start';
  }
  // Waiting for the next call: the camera track is paused and the mic is off; nothing is sent.
  function idle(on) {
    stream?.getTracks().forEach(track => { track.enabled = !on; });
    if (on) { voice?.stopListening(); showLevel(0); }
  }
  function ensureVoice() {
    voice = voice || makeVoice({
      sttMode: 'server',
      synth: null,
      onAudioChunk: (pcm16, _seq, final) => audio.chunk(pcm16, _seq, final),
      onBargePoint: () => audio.bargePoint(),
      onLevel: showLevel,
      onMicError: () => { status.textContent = 'Mic unavailable. Camera only.'; },
    });
    return voice;
  }
  async function startMic() {
    if (ensureVoice().supported.stt && !(voice.isListening && voice.isListening())) await voice.startListening();
  }
  function connect() {
    clearTimeout(retry);
    const url = new URL('/ws2', location.href); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    if (mode.join && !usedCode) {
      url.searchParams.set('pair', mode.code);
      url.searchParams.set('join', mode.join);
    } else if (mode.token) url.searchParams.set('token', mode.token);
    const ws = new WebSocket(url);
    socket = ws;
    sessionId = globalThis.crypto.randomUUID();
    // The code is single use: after it, a remembered phone joins by its sign-in.
    const code = mode.code && !usedCode ? mode.code : '';
    ws.addEventListener('open', () => send({ type: 'hello', pack: 'general', role: 'camera', camera: true, ...(code ? { pair_code: code } : {}) }));
    ws.addEventListener('message', event => {
      if (socket !== ws) return;
      const msg = JSON.parse(event.data);
      if (msg.type === 'error') { status.textContent = msg.message; return; }
      if (msg.type !== 'pair_status') return;
      if (code) usedCode = true;
      const step = rememberStep(msg, remember);
      if (step.action === 'same') return;
      status.textContent = step.text;
      if (step.action === 'live') {
        paired = true; idle(false); button.textContent = 'Stop'; void startMic();
      } else if (step.action === 'wait') {
        paired = false; idle(true); button.textContent = 'Join my call'; button.disabled = false;
        retry = setTimeout(() => { if (remember && stream) connect(); }, step.delayMs);
      } else {
        paired = false; terminal = true; stop(); button.disabled = true;
      }
    });
    ws.addEventListener('close', event => {
      if (socket !== ws) return;
      if (event?.code === 4401) {
        terminal = true; paired = false; stop();
        signIn = true; status.textContent = 'Please sign in again';
        button.textContent = 'Reload to sign in'; button.disabled = false;
        return;
      }
      if (paired && remember && stream) {
        // Dropped mid-call: try again shortly.
        paired = false; idle(true); status.textContent = 'Reconnecting…';
        retry = setTimeout(connect, 2000);
        return;
      }
      if (!terminal && !remember) {
        status.textContent = 'Disconnected. Request a new code to reconnect.';
        terminal = true; paired = false; stop(); button.disabled = true;
      }
    });
  }
  toggle.addEventListener('change', () => {
    remember = toggle.checked;
    saveRemember(storage, remember);
    note.textContent = remember
      ? 'This phone joins your calls by itself while this page is open and you are signed in.'
      : 'Works when this phone and your call are signed in with the same account. Keep this page open.';
  });
  button.addEventListener('click', async () => {
    if (signIn) { location.reload(); return; }
    if (stream && button.textContent === 'Join my call') { connect(); return; }
    if (stream) { send({ type: 'camera', on: false }); stop(); return; }
    // Inside the tap, before any await: iOS Safari only starts audio capture from a user gesture.
    void ensureVoice().unlockAudio?.();
    button.disabled = true;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      video.srcObject = stream; await video.play(); button.textContent = 'Stop';
      terminal = false;
      if (!socket || socket.readyState > 1) connect();
      else send({ type: 'camera', on: true });
      timer = setInterval(() => {
        if (!paired || !stream || document.hidden || socket.bufferedAmount || !video.videoWidth) return;
        const shot = writeFrame(grab, video, video.videoWidth, video.videoHeight);
        if (shot) send({ type: 'frame', seq: ++seq, ts_ms: Date.now(), ...shot });
      }, 1000);
    } catch { stop(); status.textContent = 'Camera unavailable. Allow camera access and use HTTPS, then try again.'; }
    finally { button.disabled = terminal; }
  });
  window.addEventListener('pagehide', () => { stop(); socket?.close(); });
  return true;
}
