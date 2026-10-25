// Separate HTTP wake channel: it deliberately has no camera callback or camera command.
export function triggerClient({ request, session, available, listening, wake, timer = setTimeout }) {
  let stopped = false;
  let ack = '';
  async function poll() {
    if (stopped) return;
    try {
      if (available()) {
        const response = await request('/api/trigger/client', {
          session_id: session(), ack, listening: listening(),
        });
        if (response.wake && response.wake.id !== ack && !stopped && available()) {
          ack = response.wake.id; // Do not repeat speech when an ACK response is lost.
          await wake(response.wake.line);
        }
      }
    } catch { /* Offline or permission denied: no false acknowledgement of listening. */ }
    if (!stopped) timer(poll, 1000);
  }
  void poll();
  return () => { stopped = true; };
}
