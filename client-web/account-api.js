// Credentials stay in HttpOnly cookies. No tokens in URLs or browser storage.
export function accountAPI(fetcher = globalThis.fetch) {
  async function request(path, options = {}) {
    const response = await fetcher(path, { credentials: 'same-origin', cache: 'no-store', ...options });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || (response.status === 401 ? 'Sign in or choose Guest first.' : 'Please try again.'));
    }
    return response;
  }
  return {
    config: async () => (await request('/auth/config')).json(),
    signIn: async (kind, nonce, credential) => (await request(`/auth/${kind}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Aeye-CSRF': nonce },
      body: JSON.stringify(credential ? { credential } : {}),
    })).json(),
    me: async () => (await request('/api/me')).json(),
    export: async () => (await request('/api/me/export')).blob(),
    delete: async () => (await request('/api/me', { method: 'DELETE' })).json(),
    logout: async () => (await request('/auth/logout', { method: 'POST' })).json(),
  };
}
