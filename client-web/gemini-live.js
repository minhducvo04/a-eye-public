// Live and General share the Live talker; consent and Private mode still gate transport.
export function liveSettings(mode, google, privateMode) {
  return {
    selected: ['gemini-live', 'general'].includes(mode),
    allowed: ['gemini-live', 'general'].includes(mode) && Boolean(google) && !privateMode,
    notice: 'During a Gemini Live call, your microphone audio and downscaled camera video go to Google. Memory stays local; only requested tool result text goes to Google.',
  };
}
