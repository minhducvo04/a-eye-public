const $ = id => document.getElementById('recipient-' + id);
let linkToken = location.hash.slice(1), replyToken = '';
history.replaceState(null, '', location.pathname);
async function post(action, data) {
  const res = await fetch('/family/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const result = await res.json();
  if (!res.ok) throw new Error(result.detail || 'This private link is unavailable.');
  return result;
}
$('open').onclick = async () => {
  $('open').disabled = true;
  try {
    const result = await post('exchange', { token: linkToken });
    linkToken = ''; replyToken = result.reply_token;
    $('note').textContent = result.text; $('form').hidden = false; $('open').hidden = true;
    $('status').textContent = 'Only your text reply goes back to the call.';
  } catch (error) { $('status').textContent = error.message; }
};
$('form').onsubmit = async event => {
  event.preventDefault();
  const button = $('form').querySelector('button'); button.disabled = true;
  try {
    await post('reply', { token: replyToken, text: $('reply').value });
    replyToken = ''; $('form').hidden = true; $('status').textContent = 'Reply sent. Thank you.';
  } catch (error) { $('status').textContent = error.message; button.disabled = false; }
};
