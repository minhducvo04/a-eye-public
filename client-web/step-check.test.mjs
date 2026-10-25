import test from 'node:test';
import assert from 'node:assert/strict';
import { createValveCard } from './step-check.js';

function fixture() {
  const classes = new Set(['hidden']);
  const element = { textContent: '', classList: {
    add: x => classes.add(x), remove: x => classes.delete(x),
  } };
  return { element, classes, card: createValveCard(element) };
}
const turn = (detail, turn_id = 'a', stage = 'thinking') => ({ type: 'turn', detail, turn_id, stage });

test('valve card follows checker results, not speech or client confirmation', () => {
  const { element, classes, card } = fixture();
  card.event({ type: 'say', text: 'Water is off' });
  assert.equal(classes.has('hidden'), true);
  card.event(turn('Valve: checking'));
  assert.equal(element.textContent, 'Checking the valve...');
  card.event(turn('Valve: closed'));
  assert.match(element.textContent, /^Water is off/);
  card.event(turn('Valve: unsure'));
  assert.equal(element.textContent, 'Show me again');
  card.reset();
  assert.equal(classes.has('hidden'), true);
});

test('new turns, cancellation and hazards remove stale success; old results cannot restore it', () => {
  const { element, card } = fixture();
  card.event(turn('Valve: checking'));
  card.event(turn('Valve: closed'));
  card.event(turn('', 'b', 'received'));
  card.event(turn('Valve: closed', 'a'));
  assert.equal(element.textContent, 'Show me again');
  card.event(turn('Valve: checking', 'b'));
  card.event(turn('', 'b', 'cancelled'));
  assert.equal(element.textContent, 'Show me again');
  card.event(turn('Stopped by code', 'b'));
  assert.equal(element.textContent, 'Show me again');
});

test('presenter pass is visibly synthetic and never claims water is off', () => {
  const { element, card } = fixture();
  card.event(turn('Valve: checking'));
  card.event(turn('Valve: test closed'));
  assert.match(element.textContent, /TEST MODE/);
  assert.doesNotMatch(element.textContent, /Water is off/);
  card.event(turn('', 'b', 'received'));
  assert.equal(element.textContent, 'Show me again');
});
