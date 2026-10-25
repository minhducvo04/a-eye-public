import { accountAPI } from './account-api.js';

const api = accountAPI();
const status = document.getElementById('account-status');
const showError = error => { status.textContent = error.message; };

async function startSignIn() {
  const config = await api.config();
  const guest = document.getElementById('guest');
  if (!config.enabled) {
    status.textContent = 'Accounts are not configured on this server yet. Existing demo access still works.';
    return;
  }
  if (location.protocol !== 'https:') {
    status.textContent = 'Open this site over HTTPS to use secure account cookies.';
    return;
  }
  guest.disabled = false;
  guest.addEventListener('click', async () => {
    guest.disabled = true;
    try {
      await api.signIn('guest', config.nonce);
      location.assign('/agent.html');
    } catch (error) { showError(error); guest.disabled = false; }
  });
  if (!config.google_client_id) return;
  const load = document.getElementById('load-google');
  load.hidden = false;
  load.addEventListener('click', () => {
    load.disabled = true;
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.onload = () => {
      google.accounts.id.initialize({
        client_id: config.google_client_id, nonce: config.nonce, auto_select: false,
        callback: async result => {
          try {
            await api.signIn('google', config.nonce, result.credential);
            location.assign('/agent.html');
          } catch (error) { showError(error); }
        },
      });
      google.accounts.id.renderButton(document.getElementById('google-button'), { theme: 'outline', size: 'large' });
      load.hidden = true;
    };
    script.onerror = () => { status.textContent = 'Google is unavailable. You can still use Guest.'; load.disabled = false; };
    document.head.append(script);
  });
}

async function startMyData() {
  const data = await api.me();
  status.textContent = data.account.kind === 'guest'
    ? 'Guest data expires after 24 hours. Viewing, exporting and deleting are always free.'
    : 'Viewing, exporting and deleting your data are always free.';
  document.getElementById('stored-data').textContent = JSON.stringify(data, null, 2);
  const actions = document.getElementById('data-actions');
  actions.hidden = false;
  document.getElementById('export-data').addEventListener('click', async () => {
    try {
      const url = URL.createObjectURL(await api.export());
      const link = document.createElement('a');
      link.href = url; link.download = 'a-eye-my-data.zip'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = 'Your export is downloaded. It excludes media and embeddings.';
    } catch (error) { showError(error); }
  });
  document.getElementById('delete-data').addEventListener('click', async () => {
    if (!confirm('Delete all your A-eye data and end your active calls? This cannot be undone.')) return;
    try {
      const result = await api.delete();
      actions.hidden = true;
      document.getElementById('stored-data').textContent = '';
      status.textContent = result.cloud_pending
        ? 'Local data deleted. Cloud deletion is pending and will retry automatically. You are signed out.'
        : 'Your data is deleted. You are signed out.';
    } catch (error) { showError(error); }
  });
  document.getElementById('sign-out').addEventListener('click', async () => {
    try { await api.logout(); location.assign('/signin.html'); } catch (error) { showError(error); }
  });
}

(document.getElementById('guest') ? startSignIn() : startMyData()).catch(showError);
