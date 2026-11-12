// Run: node tests/real_email_state.cjs
// Exercise real-email UI safeguards with a fake API: no network or email sending.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const issue = (revision = 'rev-1', status = 'preview') => ({
  id: 'edition-1', status, email_revision: revision, week_start: '2026-11-13',
  editor_plan: {subject_line: 'Campus Weekly'},
});
const officer = {role: 'student_affairs', email: 'staff@apu.edu.my'};
function fixture(request = async route => route.endsWith('/status')
  ? {configured: true, message: 'Gmail is configured.'}
  : {status: 'sent', message: 'Gmail accepted the preview.'}, hostedDemo = false) {
  const elements = new Map(), calls = [], popups = [];
  function get(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, disabled: false, checked: false, value: '', textContent: '', open: false,
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; },
      addEventListener(name, callback) { this['on' + name] = callback; },
      checkValidity() { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this.value); },
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    URL, document: {body: {dataset: {hostedDemo: String(hostedDemo)}}, getElementById: get}, window: {open: (...args) => popups.push(args)},
    Staff: {api: async (route, options = {}) => { calls.push({route, ...options}); return request(route, options); }},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'real-email.js'), 'utf8'), context);
  const panel = context.window.RealEmail;
  return {get, panel, calls, popups,
    async ready() { await panel.init(officer); panel.update({issue: issue()}); },
    address(value) { get('real-email-recipient').value = value; get('real-email-recipient').oninput(); },
    confirm() { get('real-email-confirm').checked = true; get('real-email-confirm').onchange(); },
    submit() { return get('real-email-form').onsubmit({preventDefault() {}}); },
  };
}

async function run() {
  {
    const f = fixture(async () => ({configured: false, message: 'Set up Gmail.'}));
    await f.ready(); f.address('reader@example.com'); f.confirm(); await f.submit();
    assert.equal(f.get('real-email-send').disabled, true);
    assert.equal(f.calls.length, 1);
    assert.match(f.get('real-email-requirement').textContent, /Complete Gmail setup/);
    console.log('PASS missing sender blocks real sending');
  }
  {
    const f = fixture(); await f.panel.init(officer);
    f.address('reader@example.com'); f.confirm(); await f.submit();
    assert.equal(f.calls.length, 1, 'no edition must not send');
    for (const nextIssue of [issue('', 'preview'), issue('rev-1', 'held'), issue('rev-1', 'sending')]) {
      f.panel.update({issue: nextIssue}); f.confirm(); await f.submit();
      assert.equal(f.get('real-email-send').disabled, true);
    }
    f.panel.update({issue: issue()}); f.confirm(); assert.equal(f.get('real-email-send').disabled, false);
    f.panel.update({issue: issue(), blocked: true}); await f.submit();
    f.panel.update({issue: issue(), busy: true}); await f.submit();
    assert.equal(f.calls.length, 1, 'unsaved templates and publication actions must block sends');
    console.log('PASS edition status, template and publication guards');
  }
  {
    const f = fixture(); await f.ready();
    for (const value of ['first@example.com,second@example.com', 'first@example.com;second@example.com', 'Name <first@example.com>', 'first@example.com\r\nBcc:second@example.com']) {
      f.address(value); f.confirm(); await f.submit();
      assert.equal(f.get('real-email-send').disabled, true);
    }
    assert.equal(f.calls.length, 1);
    f.address('first@example.com'); f.confirm();
    assert.equal(f.get('real-email-send').disabled, false);
    f.address('second@example.com');
    assert.equal(f.get('real-email-confirm').checked, false);
    f.confirm(); f.panel.update({issue: issue('rev-2')});
    assert.equal(f.get('real-email-confirm').checked, false);
    console.log('PASS single recipient and renewed confirmation for changed address or edition');
  }
  {
    const pending = deferred(); let changes = 0;
    const f = fixture(async route => route.endsWith('/status') ? {configured: true} : pending.promise);
    await f.panel.init(officer, () => changes++); f.panel.update({issue: issue()});
    f.address('Reader@Example.com'); f.confirm(); const sent = f.submit(); await f.submit();
    assert.equal(f.calls.length, 2, 'double clicks must produce one POST');
    assert.equal(f.panel.isSending(), true);
    assert.equal(f.get('real-email-recipient').disabled, true);
    assert.deepEqual(JSON.parse(f.calls[1].body), {recipient: 'reader@example.com', confirmed: true, expected_revision: 'rev-1'});
    assert.equal(f.calls[1].route, '/admin/api/issues/edition-1/email-preview');
    pending.resolve({status: 'sent', message: 'Gmail accepted the preview.'}); await sent;
    assert.equal(f.panel.isSending(), false); assert.equal(changes, 2);
    f.confirm(); await f.submit(); assert.equal(f.calls.length, 2, 'accepted same preview should stay deduplicated');
    assert.match(f.get('real-email-result').textContent, /accepted/);
    assert.doesNotMatch(f.get('real-email-result').textContent, /delivered/);
    console.log('PASS send payload, double-click prevention and accepted receipt state');
  }
  {
    const f = fixture(async route => route.endsWith('/status') ? {configured: true}
      : {status: 'unknown', message: 'Check the sender’s Sent folder.'});
    await f.ready(); f.address('reader@example.com'); f.confirm(); await f.submit();
    f.confirm(); await f.submit();
    assert.equal(f.calls.length, 2);
    assert.equal(f.get('real-email-send').disabled, true);
    assert.match(f.get('real-email-result').textContent, /Sent folder/);
    console.log('PASS uncertain results are never automatically retried');
  }
  {
    const f = fixture(async route => route.endsWith('/status') ? {configured: true}
      : {status: 'failed', message: 'Reconnect Gmail before retrying.'});
    await f.ready(); f.address('reader@example.com'); f.confirm(); await f.submit();
    assert.equal(f.get('real-email-confirm').checked, false);
    assert.equal(f.calls.length, 2);
    f.confirm(); await f.submit(); assert.equal(f.calls.length, 3, 'known failure requires a new explicit action');
    console.log('PASS known failure allows explicit retry with fresh confirmation');
  }
  {
    const pending = deferred();
    const f = fixture(async route => route.endsWith('/status') ? {configured: true} : pending.promise);
    await f.ready(); f.address('reader@example.com'); f.confirm(); const sent = f.submit();
    f.panel.reset(); pending.resolve({status: 'sent', message: 'Should not appear after sign-out'}); await sent;
    assert.equal(f.get('real-email-panel').hidden, true);
    assert.equal(f.get('real-email-recipient').value, '');
    assert.equal(f.get('real-email-result').textContent, '');
    assert.equal(f.get('real-email-send').disabled, true);
    console.log('PASS sign-out invalidates late send responses');
  }
  {
    const pending = deferred(); const f = fixture(() => pending.promise);
    const started = f.panel.init(officer); await f.panel.init({role: 'club_rep'});
    pending.resolve({configured: true, message: 'Old staff status'}); await started;
    assert.equal(f.get('real-email-panel').hidden, true);
    assert.notEqual(f.get('real-email-connection').textContent, 'Old staff status');
    assert.equal(f.calls.length, 1);
    console.log('PASS late connection response cannot leak into a Club session');
  }
  {
    for (const fails of [false, true]) {
      const pending = deferred();
      const f = fixture(async route => route.endsWith('/status') ? {configured: true} : pending.promise);
      await f.ready(); f.address('reader@example.com'); f.confirm(); const sent = f.submit();
      f.panel.update({issue: issue('rev-2')});
      if (fails) pending.reject(new Error('Old edition error'));
      else pending.resolve({status: 'sent', message: 'Old edition success'});
      await sent;
      assert.equal(f.get('real-email-result').textContent, '');
      assert.equal(f.get('real-email-confirm').checked, false);
      assert.equal(f.panel.isSending(), false);
    }
    console.log('PASS late outcomes do not overwrite a newer edition');
  }
  {
    const f = fixture(async () => ({configured: false, owner_required: true, hosted: true, message: 'Owner access required.'}), true);
    await f.ready(); f.address('other@example.com'); f.confirm(); await f.submit();
    assert.equal(f.get('real-email-unlock').hidden, false);
    assert.equal(f.get('real-email-recipient').value, '');
    assert.equal(f.get('real-email-recipient').disabled, true);
    assert.equal(f.get('real-email-recipient').readOnly, true);
    assert.equal(f.get('real-email-confirm').disabled, true);
    assert.equal(f.get('real-email-send').textContent, 'Send test email');
    assert.equal(f.get('real-email-send').disabled, true);
    assert.equal(f.get('real-email-open-inbox').hidden, true);
    assert.equal(f.calls.length, 1, 'owner-locked submits must not reach the API');
    for (const response of [
      {configured: true, hosted: true},
      {configured: true, hosted: true, owner_required: false},
      {configured: false, hosted: true, owner_required: false},
    ]) {
      const unavailable = fixture(async () => response, true);
      await unavailable.ready(); unavailable.confirm(); await unavailable.submit();
      assert.equal(unavailable.calls.length, 1, 'explicit owner unlock and configured fixed recipient are required');
      assert.equal(unavailable.get('real-email-send').disabled, true);
      assert.equal(unavailable.get('real-email-open-inbox').hidden, true);
      if(response.owner_required === false) assert.equal(unavailable.get('real-email-unlock').hidden, true);
    }
    console.log('PASS hosted owner lock and unavailable or incomplete configuration fail closed');
  }
  {
    let unlocked = false;
    const inbox = 'https://mail.google.com/mail/u/0/#inbox';
    const f = fixture(async route => route.endsWith('/status')
      ? unlocked ? {configured: true, hosted: true, owner_required: false, fixed_recipient: 'Owner@Example.com', inbox_url: inbox}
        : {configured: false, hosted: true, owner_required: true}
      : {status: 'sent', message: 'Gmail accepted this preview.'}, true);
    await f.ready(); assert.equal(f.calls.length, 1);
    unlocked = true; await f.get('real-email-refresh').onclick();
    assert.equal(f.calls.length, 2, 'unlocking and refreshing must never automatically send');
    assert.equal(f.get('real-email-recipient').value, 'owner@example.com');
    assert.equal(f.get('real-email-recipient').readOnly, true);
    assert.equal(f.get('real-email-unlock').hidden, true);
    assert.equal(f.get('real-email-open-inbox').hidden, true, 'Gmail link appears only after an accepted send');
    f.address('tampered@example.com');
    assert.equal(f.get('real-email-recipient').value, 'owner@example.com');
    f.confirm(); await f.submit();
    assert.deepEqual(JSON.parse(f.calls[2].body), {recipient: 'owner@example.com', confirmed: true, expected_revision: 'rev-1'});
    assert.equal(f.get('real-email-open-inbox').hidden, false);
    assert.equal(f.get('real-email-open-inbox').href, inbox);
    assert.equal(f.popups.length, 0, 'successful sends must not open a browser popup');
    f.confirm(); await f.submit();
    assert.equal(f.calls.length, 3, 'same accepted hosted test must not be resent');
    unlocked = false; await f.get('real-email-refresh').onclick();
    assert.equal(f.get('real-email-recipient').value, '');
    assert.equal(f.get('real-email-confirm').checked, false);
    assert.equal(f.get('real-email-result').textContent, '');
    assert.equal(f.get('real-email-open-inbox').hidden, true);
    assert.equal(f.get('real-email-send').disabled, true);
    f.panel.update({issue: issue('rev-2')});
    assert.equal(f.get('real-email-open-inbox').hidden, true);
    assert.equal(f.get('real-email-open-inbox').href, undefined);
    f.panel.reset();
    assert.equal(f.get('real-email-recipient').value, '');
    assert.equal(f.get('real-email-unlock').hidden, true);
    console.log('PASS hosted fixed recipient, explicit confirmation, accepted Gmail link and no automatic send or popup');
  }
  {
    for(const outcome of ['unknown', 'failed']) {
      const f = fixture(async route => route.endsWith('/status')
        ? {configured: true, hosted: true, owner_required: false, fixed_recipient: 'owner@example.com', inbox_url: 'https://mail.google.com/mail/u/0/#inbox'}
        : {status: outcome}, true);
      await f.ready(); f.confirm(); await f.submit();
      assert.equal(f.get('real-email-open-inbox').hidden, true);
      assert.equal(f.calls.length, 2);
    }
    const f = fixture(async route => route.endsWith('/status')
      ? {configured: true, hosted: true, owner_required: false, fixed_recipient: 'owner@example.com', inbox_url: 'https://mail.google.com.attacker.example/mail/'}
      : {status: 'sent'}, true);
    await f.ready(); f.confirm(); await f.submit();
    assert.equal(f.get('real-email-open-inbox').hidden, true);
    assert.equal(f.get('real-email-open-inbox').href, undefined);
    console.log('PASS unsuccessful outcomes and non-Gmail URLs never expose an inbox link');
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
