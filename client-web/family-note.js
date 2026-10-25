// No cloud composer, media fields, cross-call memory, or default recipient.
export function confirmation(text) {
  return String(text || '').trim().toLowerCase().replace(/[.!?]+$/, '').trim() === 'send' ? 'send' : 'cancel';
}

export function createApproval(post) {
  let draft = null, heard = false;
  return {
    start(value) { draft = value; heard = false; },
    pending() { return Boolean(draft); },
    async heard() {
      const current = draft;
      if (!current) return;
      await post('heard', { id: current.id });
      if (draft === current) heard = true;
    },
    async confirm(text) {
      if (!draft) return false;
      const current = draft;
      draft = null;
      const answer = heard ? confirmation(text) : 'cancel';
      heard = false;
      await post('send', { id: current.id, confirmation: answer });
      return answer === 'send';
    },
    clear() { draft = null; heard = false; },
  };
}

export function setupFamily({ session, request, speak }) {
  const root = document.getElementById('family-panel');
  const $ = id => document.getElementById('family-' + id);
  let currentSession = session(), tasks = {}, seen = new Set(), busy = false;
  let draftText = '', generation = 0, hasContacts = false;
  const post = async (action, data) => {
    const result = await request('/api/family/' + action + '?session_id=' + encodeURIComponent(session()), data);
    if (result.delivery_link) showLink(result.delivery_link);
    return result;
  };
  const approval = createApproval(post);
  const report = error => { $('status').textContent = error.message; };
  const safely = fn => async () => { try { await fn(); } catch (error) { report(error); } };
  function showLink(path) {
    $('link').href = new URL(path, location.origin).href;
    $('link').hidden = false;
    $('status').textContent = 'Note ready for your chosen person. Share this private link directly; it opens once and expires in 24 hours.';
    $('send').disabled = true;
    approval.clear();
  }
  function fillDetails() {
    $('detail').replaceChildren();
    (tasks[$('task').value]?.details || []).forEach((text, index) => {
      $('detail').add(new Option(text, String(index)));
    });
  }
  function contacts(rows) {
    hasContacts = rows.length > 0;
    $('sharing').hidden = !hasContacts;
    $('draft').disabled = !hasContacts;
    $('heading').textContent = hasContacts ? 'Want to show someone?' : 'People';
    if (!hasContacts) {
      approval.clear(); generation++;
      $('read').disabled = true; $('send').disabled = true;
      $('preview').textContent = ''; $('status').textContent = '';
      $('link').hidden = true;
    }
    const chosen = $('contact').value;
    $('contact').replaceChildren(new Option('Choose a person', ''));
    rows.forEach(c => $('contact').add(new Option(`${c.name} (${c.relation})`, c.id)));
    if (rows.some(c => c.id === chosen)) $('contact').value = chosen;
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    const sid = session();
    if (sid !== currentSession) {
      currentSession = sid; generation++; approval.clear(); seen = new Set();
      $('preview').textContent = ''; $('reply').textContent = ''; $('link').hidden = true;
      $('send').disabled = true; $('read').disabled = true; root.open = false;
      contacts([]);
    }
    try {
      const data = await request('/api/family?session_id=' + encodeURIComponent(sid));
      if (sid !== session()) return;
      contacts(data.contacts);
      if (!Object.keys(tasks).length) {
        tasks = data.tasks;
        for (const [id, task] of Object.entries(tasks)) $('task').add(new Option(task.fixed, id));
        fillDetails();
      }
      const pending = data.latest;
      if (pending?.status === 'sent' && approval.pending()) {
        const contact = data.contacts.find(c => c.id === pending.contact);
        if (contact?.delivery_link) showLink(contact.delivery_link);
      } else if (pending?.status === 'cancelled' && approval.pending()) {
        approval.clear(); $('send').disabled = true; $('status').textContent = 'Cancelled. Nothing sent.';
      }
      for (const reply of data.replies) {
        if (seen.has(reply.id)) continue;
        seen.add(reply.id);
        $('reply').textContent = `${reply.name}: ${reply.text}`;
        $('replay').hidden = false;
        root.open = false;
        try {
          await speak(`${reply.name} replied. ${reply.text}`);
          $('status').textContent = 'Reply read aloud.';
        }
        catch (error) { report(error); }
      }
    } catch { /* No call yet: the ordinary call UI owns connection status. */ }
    finally { busy = false; }
  }
  $('task').onchange = fillDetails;
  $('add').onclick = safely(async () => {
    await post('contact', { name: $('name').value, relation: $('relation').value, consent: $('consent').checked });
    $('name').value = ''; $('relation').value = ''; $('consent').checked = false;
    await refresh();
    $('status').textContent = 'Contact added. Choose them before drafting.';
  });
  $('remove').onclick = safely(async () => {
    await post('remove', { contact: $('contact').value });
    approval.clear(); generation++; $('link').hidden = true; $('send').disabled = true;
    $('preview').textContent = ''; await refresh();
  });
  $('draft').onclick = safely(async () => {
    if (!$('complete').checked) throw new Error('Confirm these facts describe the task you just completed.');
    generation++;
    const draft = await post('draft', { contact: $('contact').value,
      log: { task: $('task').value, detail: Number($('detail').value) } });
    approval.start(draft); draftText = draft.text;
    $('preview').textContent = draftText;
    $('send').disabled = true; $('read').disabled = false; $('link').hidden = true;
    $('status').textContent = 'Read the note aloud, then say send or tap Send. Anything else cancels.';
    await read();
  });
  async function read() {
    if (!approval.pending()) return;
    const gen = ++generation;
    $('send').disabled = true;
    await speak(draftText);
    if (gen !== generation || !approval.pending()) return;
    await approval.heard();
    $('send').disabled = false;
    $('status').textContent = 'Say send using the call microphone, or tap Send. Anything else cancels.';
  }
  $('read').onclick = safely(read);
  $('send').onclick = safely(async () => { generation++; await approval.confirm('send'); });
  $('cancel').onclick = safely(async () => {
    generation++;
    try { await approval.confirm('cancel'); } finally {
      $('send').disabled = true; $('status').textContent = 'Cancelled. Nothing sent.';
    }
  });
  $('contact').onchange = () => { if (approval.pending()) void $('cancel').onclick(); };
  $('replay').onclick = safely(() => speak($('reply').textContent));
  const timer = window.setInterval(refresh, 2000);
  window.addEventListener('pagehide', () => { window.clearInterval(timer); approval.clear(); });
  return {
    complete(task) {
      if (!hasContacts || session() !== currentSession) return;
      root.open = true;
      if (tasks[task]) { $('task').value = task; fillDetails(); }
      $('complete').checked = false;
      $('status').textContent = 'Want to show someone? Choose your person and confirm only what you actually did.';
    },
    consume(text, source) {
      if (!approval.pending()) return false;
      generation++;
      const spoken = source === 'browser_stt' || source === 'server_stt';
      void approval.confirm(spoken ? confirmation(text) : 'cancel').catch(() => {
        $('send').disabled = true; $('status').textContent = 'Cancelled. Nothing sent.';
      });
      return true;
    },
  };
}
