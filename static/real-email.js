// Explicit one-recipient Gmail previews. Ordinary local publishing stays in the outbox.
window.RealEmail = (() => {
  const $ = id => document.getElementById(id);
  let active = false, epoch = 0, connectionRequest = 0;
  let issue = null, publicationBusy = false, blocked = false;
  let connection = null, checking = false, sending = false, onStateChange = () => {};
  const receipts = new Map();
  const recipient = () => $('real-email-recipient').value.trim().toLowerCase();
  const editionKey = () => issue ? issue.id + '|' + (issue.email_revision || '') : '';
  const fingerprint = () => editionKey() + '|' + recipient();
  const eligible = () => !!issue?.email_revision && ['preview', 'sent'].includes(issue.status);
  function notice(message = '', kind = '') {
    const target = $('real-email-result');
    if (!target) return;
    target.textContent = message; target.hidden = !message;
    target.className = 'real-email-result' + (kind ? ' ' + kind : '');
  }
  function validRecipient() {
    const input = $('real-email-recipient'), value = recipient();
    return !!value && !/[\r\n,;<>\s]/.test(value) && input.checkValidity();
  }
  function render() {
    if (!$('real-email-panel')) return;
    $('real-email-panel').hidden = !active;
    const canSend = active && connection?.configured && !checking && !sending &&
      !publicationBusy && !blocked && eligible();
    const previous = receipts.get(fingerprint());
    const alreadyHandled = previous && ['sent', 'sending', 'unknown'].includes(previous.status);
    $('real-email-send').disabled = !canSend || !validRecipient() || !$('real-email-confirm').checked || !!alreadyHandled;
    $('real-email-send').textContent = sending ? 'Sending to Gmail…' : 'Send real email';
    $('real-email-refresh').disabled = !active || checking || sending;
    $('real-email-refresh').textContent = checking ? 'Checking…' : 'Refresh connection';
    $('real-email-recipient').disabled = sending;
    $('real-email-confirm').disabled = !canSend || !!alreadyHandled;
    $('real-email-form').setAttribute('aria-busy', String(sending));
    const configured = connection?.configured;
    $('real-email-connection-label').textContent = checking ? 'Checking sender' : configured ? 'Gmail configured' : 'Set up Gmail';
    $('real-email-connection').textContent = checking ? 'Checking the sender configuration…' :
      connection?.message || 'Connect a Gmail account to send a preview outside this computer.';
    $('real-email-selected').textContent = issue
      ? (issue.status === 'sent' ? 'Copy of sent edition' : 'Selected edition') + ' · ' +
        (issue.editor_plan?.subject_line || 'Campus Weekly') + ' · Week of ' + issue.week_start
      : 'No edition selected. Build a local preview first.';
    $('real-email-requirement').textContent = !eligible()
      ? 'Build an eligible preview, or select the latest sent edition, before sending.'
      : blocked ? 'Save or discard your template changes before sending this edition.'
      : publicationBusy ? 'Wait for the publication action to finish.'
      : alreadyHandled ? previous.message
      : !configured ? 'Complete Gmail setup, then refresh the connection here.'
      : 'Enter one recipient and tick the confirmation to send this edition.';
  }
  async function refresh() {
    if (!active || checking || sending) return;
    const currentEpoch = epoch, request = ++connectionRequest;
    checking = true; connection = null; render();
    try {
      const result = await Staff.api('/admin/api/real-email/status');
      if (currentEpoch !== epoch || request !== connectionRequest) return;
      connection = result;
    } catch (error) {
      if (currentEpoch !== epoch || request !== connectionRequest || error.stale) return;
      connection = {configured: false, message: 'Could not check Gmail. Refresh the connection to try again.'};
      notice(error.message, 'bad');
    } finally {
      if (currentEpoch === epoch && request === connectionRequest) { checking = false; render(); }
    }
  }
  async function send(event) {
    event.preventDefault();
    // These checks also protect programmatic submits while another action is running.
    render();
    if ($('real-email-send').disabled) return;
    const currentEpoch = epoch, key = fingerprint(), selectedEdition = editionKey();
    const payload = {recipient: recipient(), confirmed: true, expected_revision: issue.email_revision};
    const issueId = issue.id;
    sending = true; notice('Sending this preview to Gmail…'); render(); onStateChange();
    try {
      const result = await Staff.api('/admin/api/issues/' + encodeURIComponent(issueId) + '/email-preview',
        {method: 'POST', body: JSON.stringify(payload)});
      if (currentEpoch !== epoch) return;
      const fallback = result.status === 'sent'
        ? 'Gmail accepted the preview for ' + payload.recipient + '. Check the recipient’s inbox or spam folder.'
        : result.status === 'unknown' ? 'The outcome is uncertain. Check the sender’s Sent folder before trying again.'
        : result.status === 'sending' ? 'This preview is already being sent. Check the sender’s Sent folder.'
        : 'Gmail did not accept this preview. Check the sender configuration before retrying.';
      receipts.set(key, {...result, message: result.message || fallback});
      if (selectedEdition !== editionKey()) return;
      notice(result.message || fallback, result.status === 'sent' ? 'ok' : result.status === 'failed' ? 'bad' : 'warn');
      $('real-email-confirm').checked = false;
    } catch (error) {
      if (currentEpoch !== epoch || selectedEdition !== editionKey() || error.stale) return;
      notice(error.message + ' No automatic retry was made. If the connection was interrupted, check the sender’s Sent folder before retrying.', 'bad');
      $('real-email-confirm').checked = false;
    } finally {
      if (currentEpoch === epoch) { sending = false; render(); onStateChange(); }
    }
  }
  function update(state) {
    const previous = editionKey();
    issue = state.issue || null;
    publicationBusy = !!state.busy; blocked = !!state.blocked;
    if (previous !== editionKey()) {
      if ($('real-email-confirm')) $('real-email-confirm').checked = false;
      notice();
    }
    render();
  }
  function reset() {
    epoch++; connectionRequest++;
    active = false; issue = null; connection = null; checking = false; sending = false;
    publicationBusy = false; blocked = false; receipts.clear(); onStateChange = () => {};
    if (!$('real-email-panel')) return;
    $('real-email-panel').open = false;
    $('real-email-recipient').value = '';
    $('real-email-confirm').checked = false;
    notice(); render();
  }
  async function init(who, callback = () => {}) {
    reset();
    if (!$('real-email-panel') || who?.role !== 'student_affairs') return;
    active = true; onStateChange = callback; render();
    await refresh();
  }
  if ($('real-email-panel')) {
    $('real-email-form').addEventListener('submit', send);
    $('real-email-refresh').addEventListener('click', refresh);
    $('real-email-recipient').addEventListener('input', () => {
      $('real-email-confirm').checked = false; notice(); render();
    });
    $('real-email-confirm').addEventListener('change', render);
  }
  return {init, update, reset, isSending: () => sending};
})();
