// Kept only in this page: a shared-token caller's private, server-issued call capability.
let pending = null;
let current = '';
let generation = 0;

export async function callOwner(search = globalThis.location?.search || '', doFetch = globalThis.fetch) {
  const token = new URLSearchParams(search).get('token');
  if (!token) return '';
  if (!pending) {
    const started = generation;
    pending = doFetch('/api/call-owner', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
      .then(async response => {
        if (!response.ok) throw new Error('Could not create private call');
        const body = await response.json();
        if (typeof body.owner !== 'string') throw new Error('Missing private call owner');
        if (started === generation) current = body.owner;
        return body.owner;
      }).catch(error => { if (started === generation) pending = null; throw error; });
  }
  return pending;
}

export function resetCallOwner() {
  generation++;
  pending = null;
  current = '';
}

export function ownerQuery() {
  return current ? `&owner=${encodeURIComponent(current)}` : '';
}

export async function ownerHeaders(search, doFetch) {
  const owner = await callOwner(search, doFetch);
  return owner ? { 'X-Aeye-Owner': owner } : {};
}
