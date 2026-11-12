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
  : {status: 'sent', message: 'Gmail accepted the preview.'}) {
  const elements = new Map(), calls = [];
  function get(id) {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, disabled: false, checked: false, value: '', textContent: '', open: false,
      setAttribute(name, value) { this[name] = value; },
      addEventListener(name, callback) { this['on' + name] = callback; },
      checkValidity() { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this.value); },
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document: {getElementById: get}, window: {},
    Staff: {api: async (route, options = {}) => { calls.push({route, ...options}); return request(route, options); }},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'real-email.js'), 'utf8'), context);
  const panel = context.window.RealEmail;
  return {get, panel, calls,
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
}
run().catch(error => { console.error(error); process.exitCode = 1; });
