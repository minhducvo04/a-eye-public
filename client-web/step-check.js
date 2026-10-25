// Display only. This card sends no confirmations and cannot unlock the server gate.
export function createValveCard(element) {
  let owner = null;
  let visible = false;
  function show(text) {
    if (!element) return;
    visible = true;
    element.classList.remove('hidden');
    element.textContent = text;
  }
  return {
    reset() {
      owner = null;
      visible = false;
      element?.classList.add('hidden');
    },
    event(message) {
      if (message.type !== 'turn') return;
      if (message.stage === 'received') {
        owner = message.turn_id;
        if (visible) show('Show me again');
      }
      if (owner !== null && message.turn_id !== owner) return;
      if (message.detail === 'Valve: checking') {
        owner = message.turn_id;
        show('Checking the valve...');
      } else if (message.detail === 'Valve: test closed' && visible) {
        show('TEST MODE · Simulated pass, not a water-off check');
      } else if (message.detail === 'Valve: closed' && visible) {
        show('Water is off · Checked on this machine');
      } else if (message.detail === 'Valve: unsure' || (visible &&
          (message.detail === 'Stopped by code' || ['failed', 'cancelled'].includes(message.stage)))) {
        show('Show me again');
      }
    },
  };
}
