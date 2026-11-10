// Run: node tests/frontend_state.cjs
// Minimal DOM regressions for submission state. No browser or network is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fixture({search = '', request = async () => ({})} = {}) {
  const elements = new Map(), groups = [], fields = [];
  class Element {
    constructor(id = '') {
      this.id = id; this.hidden = false; this.disabled = false; this.checked = false;
      this.dataset = {}; this.children = []; this.files = []; this._value = '';
    }
    get value() { return this._value; }
    set value(v) {
      v = String(v);
      // HTML datetime-local rejects timezone suffixes. Keep this behaviour in the stub.
      this._value = this.id === 'deadline' && v && !/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?$/.test(v) ? '' : v;
    }
    closest() { for (let n = this; n; n = n.parent) if (n.hidden) return n; return null; }
    addEventListener(name, fn) { this['on' + name] = fn; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    scrollIntoView() {}
    focus() {}
    reset() {}
    setAttribute(name, value) { if (name === 'hidden') this.hidden = true; else this[name] = value; }
    removeAttribute(name) { if (name === 'hidden') this.hidden = false; }
  }
  const get = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  function group(kinds, names, parent = null) {
    const node = new Element(); node.dataset.kinds = kinds; node.parent = parent; groups.push(node);
    for (const id of names) { const field = get(id); field.parent = node; fields.push(field); }
    return node;
  }
  group('event announcement', ['organiser']);
  const dated = group('event achievement', ['date']); group('event', ['fee'], dated);
  group('event', ['start_time', 'end_time', 'venue', 'category', 'audience', 'registration-required', 'registration-link']);
  group('announcement', ['deadline', 'action_link']);
  group('achievement', ['team', 'competition', 'result', 'instagram_url', 'consent_confirmed']);
  get('item-type').value = 'event'; get('app').dataset.aiConfigured = 'true';
  let ready;
  const handlers = new Map();
  const Staff = {
    api: (...args) => request(...args),
    el: (tag, attrs = {}, ...children) => Object.assign(new Element(), attrs, {children}),
    init: callback => { ready = callback; },
  };
  const context = vm.createContext({
    Staff, console, URLSearchParams, Date, Intl, location: {search},
    FormData: class { append() {} },
    URL: {createObjectURL: () => 'blob:test-poster', revokeObjectURL() {}},
    document: {getElementById: get, querySelectorAll: selector => selector === '[data-kinds]' ? groups : fields},
    window: {addEventListener: (name, fn) => handlers.set(name, fn)},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'submit.js'), 'utf8'), context);
  return {get, ready: (...args) => ready(...args), signOut: () => handlers.get('boardcast:signout')?.()};
}

function staffFixture(request, {search = '', demoSwitcher = false, stored = new Map()} = {}) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, disabled: false, textContent: '', value: '',
      addEventListener(name, fn) { this['on' + name] = fn; },
      setAttribute(name) { if (name === 'hidden') this.hidden = true; },
      removeAttribute(name) { if (name === 'hidden') this.hidden = false; },
    });
    return elements.get(id);
  };
  const window = {dispatchEvent() {}};
  const context = vm.createContext({
    window, Headers, FormData, URL, URLSearchParams, Event, location: {search}, fetch: request,
    sessionStorage: {getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key)},
    document: {getElementById: id => id === 'demo-switcher' && !demoSwitcher ? null : get(id), querySelector: () => get('login-button')},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'staff.js'), 'utf8'), context);
  return {staff: vm.runInContext('Staff', context), window, get, stored};
}

const response = (status, body) => ({status, ok: status < 400, json: async () => body});
const tick = () => new Promise(resolve => setImmediate(resolve));

async function run() {
  {
    const extraction = deferred(); let uploads = 0;
    const f = fixture({request: async route => {
      if (route === '/admin/api/uploads') return {upload_id: 'upload-' + ++uploads};
      if (route.endsWith('/extract')) return extraction.promise;
      throw new Error('Unexpected route: ' + route);
    }});
    f.get('title').value = 'Keep manual title';
    f.get('poster-file').files = [{name: 'original.png'}];
    await f.get('poster-file').onchange();
    const oldRead = f.get('read-poster').onclick();
    assert.equal(f.get('submit-item').disabled, true);
    f.get('poster-file').files = [{name: 'corrected.png'}];
    await f.get('poster-file').onchange();
    assert.equal(f.get('submit-item').disabled, false, 'replacing a poster must clear the previous reading state');
    assert.equal(f.get('read-poster').disabled, false, 'the corrected poster must be readable');
    extraction.resolve({data: {title: 'STALE EXTRACTION'}, missing: []});
    await oldRead;
    assert.equal(f.get('title').value, 'Keep manual title', 'an older extraction must not overwrite the new upload');
    console.log('PASS replacement upload during extraction');
  }
  for (const outcome of ['success', 'failure']) {
    const pendingSave = deferred(); let uploads = 0, saves = 0;
    const f = fixture({request: async (route, options = {}) => {
      if (route === '/admin/api/uploads') return {upload_id: 'upload-' + ++uploads};
      if (options.method === 'POST' && route === '/admin/api/submissions') {
        saves++;
        assert.equal(JSON.parse(options.body).upload_id, 'upload-1');
        return pendingSave.promise;
      }
      if (route === '/admin/api/club') return {rows: []};
      if (route === '/admin/api/submissions/saved-item') return {poster_url: '/poster/saved.jpg'};
      throw new Error('Unexpected route: ' + route);
    }});
    f.get('title').value = 'Save original poster';
    f.get('poster-file').files = [{name: 'original.png'}];
    await f.get('poster-file').onchange();
    const originalPreview = f.get('poster-preview').src;
    const save = f.get('submission-form').onsubmit({preventDefault() {}});
    for (const id of ['poster-file', 'remove-poster', 'item-type']) assert.equal(f.get(id).disabled, true);
    // Simulate a previously opened file picker and queued remove event, despite disabled controls.
    f.get('poster-file').files = [{name: 'replacement-too-late.png'}];
    await f.get('poster-file').onchange();
    f.get('remove-poster').onclick();
    assert.equal(uploads, 1, 'saving must not start another upload');
    assert.equal(f.get('poster-preview').src, originalPreview, 'saving must keep its selected poster');
    if (outcome === 'success') pendingSave.resolve({id: 'saved-item', status: 'pending', issues: []});
    else pendingSave.reject(new Error('Temporary save failure'));
    await save;
    assert.equal(saves, 1);
    for (const id of ['poster-file', 'remove-poster', 'submit-item']) assert.equal(f.get(id).disabled, false, id + ' must unlock after ' + outcome);
    assert.equal(f.get('read-poster').disabled, false, 'the original upload remains available');
    assert.equal(f.get('item-type').disabled, outcome === 'success', 'only a saved item keeps its type locked');
    if (outcome === 'success') assert.equal(f.get('saved').hidden, false);
    else assert.equal(f.get('notice').textContent, 'Temporary save failure');
    f.signOut();
    assert.equal(f.get('poster-file').disabled, false);
    assert.equal(f.get('item-type').disabled, false, 'account changes reset the new-submission type');
  }
  console.log('PASS poster controls recover after successful and failed saves');
  {
    const firstSave = deferred(), secondSave = deferred(); let saves = 0;
    const f = fixture({request: async (route, options = {}) => {
      if (route === '/admin/api/submissions' && options.method === 'POST') return ++saves === 1 ? firstSave.promise : secondSave.promise;
      if (route === '/admin/api/club') return {rows: []};
      if (route === '/admin/api/submissions/new-account-item') return {poster_url: null};
      throw new Error('Unexpected route: ' + route);
    }});
    f.get('title').value = 'Account A submission';
    const oldSave = f.get('submission-form').onsubmit({preventDefault() {}});
    f.signOut();
    assert.equal(f.get('poster-file').disabled, false, 'sign-out unlocks poster controls immediately');
    f.get('title').value = 'Account B submission';
    const newSave = f.get('submission-form').onsubmit({preventDefault() {}});
    firstSave.resolve({id: 'old-account-item', status: 'pending', issues: []}); await oldSave;
    assert.equal(f.get('poster-file').disabled, true, 'old completion must not unlock the new account save');
    assert.equal(f.get('submit-item').disabled, true);
    secondSave.resolve({id: 'new-account-item', status: 'pending', issues: []}); await newSave;
    assert.equal(f.get('poster-file').disabled, false);
    assert.equal(f.get('submit-item').disabled, false);
    assert.equal(saves, 2, 'a new account must create its own submission');
    console.log('PASS account switching during a pending save');
  }
  {
    const f = fixture();
    f.get('item-type').value = 'announcement'; f.get('item-type').onchange();
    assert.equal(f.get('registration-link').disabled, true);
    assert.equal(f.get('date').disabled, true);
    assert.equal(f.get('deadline').disabled, false);
    f.get('item-type').value = 'achievement'; f.get('item-type').onchange();
    assert.equal(f.get('date').disabled, false);
    assert.equal(f.get('fee').disabled, true, 'nested event-only field stays disabled inside a visible date group');
    assert.equal(f.get('deadline').disabled, true);
    f.get('item-type').value = 'event'; f.get('item-type').onchange();
    assert.equal(f.get('registration-link').disabled, false);
    assert.equal(f.get('fee').disabled, false);
    console.log('PASS hidden input enablement across content types');
  }
  for (const deadline of ['2026-11-20T09:30:00+08:00', '2026-11-20T01:30:00Z']) {
    const f = fixture({search: '?item=announcement', request: async route => route === '/admin/api/club' ? {rows: []} : {
      type: 'announcement', data: {title: 'Opportunity', organiser: 'Office', summary: 'Apply now', deadline}, issues: [],
    }});
    await f.ready({role: 'club_rep'});
    assert.equal(f.get('deadline').value.slice(0, 16), '2026-11-20T09:30', 'zoned deadlines must populate as Malaysia local time');
    assert.equal(f.get('deadline').disabled, false);
  }
  console.log('PASS timezone-aware deadline correction');
  {
    let allowed = false;
    const f = fixture({search: '?item=protected', request: async route => {
      if (route === '/admin/api/club') return {rows: []};
      if (!allowed) throw new Error('Not your club');
      return {type: 'event', data: {title: 'Allowed after account change'}, issues: []};
    }});
    await f.ready({role: 'club_rep'});
    assert.equal(f.get('submission-form').hidden, true);
    f.signOut(); allowed = true;
    await f.ready({role: 'student_affairs'});
    assert.equal(f.get('submission-form').hidden, false, 'an authorized second account must recover from the first account failure');
    console.log('PASS form recovery after account change');
  }
  for (const lateStatus of [200, 401]) {
    const late = deferred(), ready = [];
    const f = staffFixture(async (route, options) => {
      if (route === '/slow') return late.promise;
      const account = options.headers.get('Authorization').endsWith('B') ? 'b' : 'a';
      return response(200, {email: account + '@apu.edu.my', role: 'student_affairs'});
    });
    f.staff.init(me => { ready.push(me.email); });
    f.window.onSignIn({credential: 'A'}); await tick();
    const stale = f.staff.api('/slow').then(() => null, error => error);
    f.staff.signOut(); f.window.onSignIn({credential: 'B'}); await tick();
    late.resolve(response(lateStatus, lateStatus === 401 ? {detail: 'Expired old token'} : {oldAccountData: true}));
    const error = await stale;
    assert.equal(error?.stale, true, 'late responses must be discarded for either HTTP status');
    assert.equal(JSON.parse(f.stored.get('boardcast.staff')).token, 'B');
    assert.equal(f.get('signin').hidden, true, 'old 401 must not sign the new account out');
    assert.equal(f.get('who').textContent.startsWith('b@'), true);
    assert.deepEqual(ready, ['a@apu.edu.my', 'b@apu.edu.my']);
  }
  console.log('PASS stale success and unauthorized responses after account switch');
  {
    const lateLogin = deferred(), ready = [];
    const f = staffFixture(async (route, options) => options.headers.get('Authorization').endsWith('A')
      ? lateLogin.promise : response(200, {email: 'b@apu.edu.my', role: 'club_rep'}));
    f.staff.init(me => { ready.push(me.email); });
    f.window.onSignIn({credential: 'A'});
    assert.equal(f.get('login-button').disabled, true);
    f.staff.signOut();
    assert.equal(f.get('login-button').disabled, false, 'sign-out must unlock login while the old request is pending');
    f.window.onSignIn({credential: 'B'}); await tick();
    lateLogin.resolve(response(200, {email: 'a@apu.edu.my', role: 'student_affairs'})); await tick();
    assert.deepEqual(ready, ['b@apu.edu.my'], 'late old login must not invoke onReady or persist the old account');
    assert.equal(JSON.parse(f.stored.get('boardcast.staff')).token, 'B');
    console.log('PASS stale login completion and sign-out recovery');
  }
  for (const [demoRole, expectedEmail] of [
    ['club', 'clubrep.demo@mail.apu.edu.my'],
    ['student-affairs', 'studentaffairs.demo@apu.edu.my'],
  ]) {
    const stored = new Map([['boardcast.staff', JSON.stringify({token: 'OLD-STORED-TOKEN', devUser: 'old@apu.edu.my'})]]);
    const requests = [], ready = [];
    const f = staffFixture(async (route, options) => {
      requests.push(options.headers);
      return response(200, {email: options.headers.get('X-Dev-User'), role: demoRole === 'club' ? 'club_rep' : 'student_affairs'});
    }, {search: '?demo=' + demoRole, demoSwitcher: true, stored});
    f.staff.init(me => ready.push(me.email)); await tick();
    assert.deepEqual(ready, [expectedEmail]);
    assert.equal(requests[0].get('X-Dev-User'), expectedEmail, 'the explicit demo link must override a stored account');
    assert.equal(requests[0].get('Authorization'), null, 'demo selection must clear a stored token');
    assert.deepEqual(JSON.parse(stored.get('boardcast.staff')), {token: null, devUser: expectedEmail});
    f.staff.signOut(); await tick();
    assert.equal(requests.length, 1, 'sign-out must not immediately restart the query-selected demo login');
    assert.equal(stored.has('boardcast.staff'), false);
  }
  console.log('PASS explicit local role links override stored account');
  for (const [search, demoSwitcher] of [
    ['?demo=club', false],
    ['?demo=student-affairs', false],
    ['?demo=admin', true],
    ['?demo=__proto__', true],
    ['?demo=Club', true],
    ['?demo=student_affairs', true],
    ['?item=correction-id', true],
  ]) {
    const original = {token: 'PRESERVED-TOKEN', devUser: null};
    const stored = new Map([['boardcast.staff', JSON.stringify(original)]]);
    const requests = [];
    const f = staffFixture(async (route, options) => {
      requests.push(options.headers);
      return response(200, {email: 'existing-reviewer@apu.edu.my', role: 'student_affairs'});
    }, {search, demoSwitcher, stored});
    f.staff.init(() => {}); await tick();
    assert.equal(requests[0].get('Authorization'), 'Bearer PRESERVED-TOKEN', search);
    assert.equal(requests[0].get('X-Dev-User'), null, search);
    assert.deepEqual(JSON.parse(stored.get('boardcast.staff')), original);
  }
  {
    let calls = 0;
    const f = staffFixture(async () => { calls++; return response(200, {}); }, {search: '?demo=club', demoSwitcher: false});
    f.staff.init(() => {}); await tick();
    assert.equal(calls, 0, 'a production URL parameter alone must not create a demo session');
  }
  console.log('PASS invalid and nonlocal demo parameters preserve session');
  {
    const stored = new Map();
    const request = async (route, options) => {
      const email = options.headers.get('X-Dev-User');
      return response(200, {email, role: email.startsWith('studentaffairs.') ? 'student_affairs' : 'club_rep'});
    };
    const reviewer = staffFixture(request, {search: '?demo=student-affairs', demoSwitcher: true, stored});
    reviewer.staff.init(() => {}); await tick();
    const beforeOutbox = stored.get('boardcast.staff');
    // The outbox does not initialise a staff login. Sharing sessionStorage must leave the role intact.
    staffFixture(request, {search: '', demoSwitcher: true, stored});
    assert.equal(stored.get('boardcast.staff'), beforeOutbox);
    const club = staffFixture(request, {search: '?demo=club', demoSwitcher: true, stored});
    const ready = []; club.staff.init(me => ready.push(me.email)); await tick();
    assert.deepEqual(ready, ['clubrep.demo@mail.apu.edu.my']);
    const correction = staffFixture(request, {search: '?item=existing-item', demoSwitcher: true, stored});
    correction.staff.init(me => ready.push(me.email)); await tick();
    assert.deepEqual(ready, ['clubrep.demo@mail.apu.edu.my', 'clubrep.demo@mail.apu.edu.my']);
    const backToReviewer = staffFixture(request, {search: '?demo=student-affairs', demoSwitcher: true, stored});
    backToReviewer.staff.init(me => ready.push(me.email)); await tick();
    assert.equal(ready.at(-1), 'studentaffairs.demo@apu.edu.my');
    const saCorrection = staffFixture(request, {search: '?item=existing-item', demoSwitcher: true, stored});
    saCorrection.staff.init(me => ready.push(me.email)); await tick();
    assert.equal(ready.at(-1), 'studentaffairs.demo@apu.edu.my', 'a correction link must keep Student Affairs identity');
    console.log('PASS outbox return role switching and correction identity');
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
