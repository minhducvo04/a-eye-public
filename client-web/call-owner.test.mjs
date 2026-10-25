import test from 'node:test';
import assert from 'node:assert/strict';
import { callOwner, ownerHeaders, ownerQuery, resetCallOwner } from './call-owner.js';

test('one private owner across HTTP and socket, rotated for each call', async () => {
  resetCallOwner();
  let issued = 0;
  const fetch = async (path, init) => {
    assert.equal(path, '/api/call-owner');
    assert.equal(init.headers.Authorization, 'Bearer shared');
    return { ok: true, json: async () => ({ owner: `private-${++issued}` }) };
  };
  const [a, b] = await Promise.all([callOwner('?token=shared', fetch), callOwner('?token=shared', fetch)]);
  assert.equal(a, b);
  assert.equal(issued, 1);
  assert.equal((await ownerHeaders('?token=shared', fetch))['X-Aeye-Owner'], a);
  assert.equal(new URLSearchParams(ownerQuery().slice(1)).get('owner'), a);
  resetCallOwner();
  assert.notEqual(await callOwner('?token=shared', fetch), a);
  assert.deepEqual(await ownerHeaders('', fetch), {});
});

test('an old in-flight owner request cannot replace a new call owner', async () => {
  resetCallOwner();
  let complete;
  const old = callOwner('?token=shared', () => new Promise(resolve => { complete = resolve; }));
  resetCallOwner();
  await callOwner('?token=shared', async () => ({ ok: true, json: async () => ({ owner: 'new' }) }));
  complete({ ok: true, json: async () => ({ owner: 'old' }) });
  await old;
  assert.equal(ownerQuery(), '&owner=new');
});
