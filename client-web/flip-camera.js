// Touch laptops must not qualify. iPadOS uses a desktop Mac UA with multiple touch points.
export function showFlipCamera(nav, coarse, devices) {
  const mobile = /Android|iPhone|iPad|iPod/.test(nav.userAgent || "") ||
    (/Macintosh/.test(nav.userAgent || "") && nav.maxTouchPoints > 1);
  return mobile && (coarse || nav.maxTouchPoints > 0) &&
    devices.filter(d => d.kind === "videoinput").length >= 2;
}

export async function switchVideoTrack(stream, facingMode, getUserMedia, active = () => true) {
  // Mobile Safari may require releasing the old camera before opening the other one.
  for (const track of stream.getVideoTracks()) {
    track.stop();
    stream.removeTrack(track);
  }
  const next = await getUserMedia({
    video: { facingMode, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
  });
  if (!active()) {
    for (const track of next.getVideoTracks()) track.stop();
    return false;
  }
  for (const track of next.getVideoTracks()) stream.addTrack(track);
  return true;
}

export function wireFlipCamera({ video, button, active, busy, onError, onSwitch = () => {} }) {
  let facing = "environment";
  let switching = false;
  try { if (localStorage.getItem("aeye.facingMode") === "user") facing = "user"; } catch {}
  const mobile = /Android|iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
  function preview() {
    const actual = video.srcObject?.getVideoTracks?.()[0]?.getSettings?.().facingMode;
    video.style.transform = (actual || (mobile ? facing : "environment")) === "user" ? "scaleX(-1)" : "";
    button.disabled = !active() || switching;
  }
  async function refresh() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      button.hidden = !showFlipCamera(navigator, window.matchMedia("(pointer: coarse)").matches, devices);
    } catch { button.hidden = true; }
    preview();
  }
  button.addEventListener("click", async () => {
    if (switching || busy() || !active()) return;
    switching = true;
    button.disabled = true;
    const stream = video.srcObject;
    const next = facing === "environment" ? "user" : "environment";
    const stillActive = () => active() && video.srcObject === stream;
    try {
      if (await switchVideoTrack(stream, next, c => navigator.mediaDevices.getUserMedia(c), stillActive)) {
        facing = next;
        try { localStorage.setItem("aeye.facingMode", facing); } catch {}
        onSwitch();
      }
    } catch {
      // Restore the previous camera after a denied/unavailable switch.
      if (stillActive()) {
        try { await switchVideoTrack(stream, facing, c => navigator.mediaDevices.getUserMedia(c), stillActive); } catch {}
        onError("Could not switch the camera.");
      }
    } finally {
      switching = false;
      preview();
    }
  });
  video.addEventListener("loadedmetadata", refresh);
  navigator.mediaDevices?.addEventListener?.("devicechange", refresh);
  void refresh();
  return {
    constraints: base => mobile && !base.deviceId ? { width: base.width, height: base.height, facingMode: facing } : base,
    refresh,
    preview,
    busy: () => switching,
  };
}
