// Safari can disguise its platform as macOS. The explicit URL flag is the reliable entry point.
export function isHeadset(search = '', userAgent = '') {
  return new URLSearchParams(search).get('device') === 'headset' || /visionOS|Vision Pro/i.test(userAgent);
}

// Enforce the camera boundary at every local video acquisition, including Settings permissions.
export async function requestLocalMedia(mediaDevices, headset, constraints) {
  if (headset && constraints.video) throw new Error('Use your phone camera on Vision Pro.');
  return mediaDevices.getUserMedia(constraints);
}
