// Run: node tests/hosted_review_state.cjs
// Exercise the real review controller in both demo modes without browser/network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(hostedDemo) {
  const elements = new Map(), calls = [], events = new Map();
  let onReady, current = null;
  class Element {
    constructor() { this.value = ''; this.hidden = false; this.disabled = false; this.textContent = ''; this.dataset = {}; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    addEventListener(name, handler) { this['on' + name] = handler; }
    replaceChildren(...children) { this.children = children; }
    append(...children) { this.children = [...(this.children || []), ...children]; }
  }
  const get = id => { if (!elements.has(id)) elements.set(id, Object.assign(new Element(), {id})); return elements.get(id); };
  const context = vm.createContext({
    document: {body: {dataset: {hostedDemo: String(hostedDemo)}}, getElementById: get, querySelectorAll: () => []},
    window: {addEventListener: (name, callback) => events.set(name, callback)},
    Date, clearTimeout, setTimeout,
    Newsletter: {blocksPublish: () => false, setIssue() {}, reset() {}, init: async () => {}},
    Staff: {
      init: callback => { onReady = callback; },
      el: (tag, attrs = {}, ...children) => Object.assign(new Element(), attrs, {children}),
      api: async (route, opts = {}) => {
        calls.push({route, method: opts.method || 'GET'});
        if (route === '/admin/api/queue') return {items: []};
        if (route === '/admin/api/preview') return {issue: current, magazine_url: '/m/test-edition'};
        if (route === '/admin/api/demo/preview') {
          current = {id: 'test-edition', status: 'preview', week_start: '2026-11-13'};
          return {issue_id: current.id};
        }
        if (route === '/admin/api/demo/publish') { current.status = 'sent'; return {sent: 1}; }
        throw new Error('Unexpected API route ' + route);
      },
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'review.js'), 'utf8'), context);
  return {get, calls, events, ready: () => onReady({role: 'student_affairs', email: 'studentaffairs.demo@apu.edu.my'})};
}

async function run() {
  for (const hosted of [false, true]) {
    const f = fixture(hosted), place = hosted ? 'demo' : 'local';
    await f.ready();
    assert.equal(f.get('build-preview').textContent, 'Build ' + place + ' preview');
    assert.equal(f.get('publish-local').disabled, true);
    await f.get('build-preview').onclick();
    assert.equal(f.get('notice').textContent, (hosted ? 'Demo' : 'Local') + ' preview built from approved submissions.');
    assert.equal(f.get('build-preview').textContent, 'Rebuild ' + place + ' preview');
    assert.match(f.get('issue-next-step').textContent, new RegExp('publish it to the ' + place + ' outbox'));
    assert.equal(f.get('publish-local').disabled, false);
    await f.get('publish-local').onclick();
    assert.equal(f.get('notice').textContent, '1 email(s) saved to the ' + place + ' outbox.');
    assert.equal(f.get('build-preview').textContent, 'Build next preview');
    assert.equal(f.get('publish-local').disabled, true);
    assert.equal(f.calls.filter(c => c.route === '/admin/api/demo/publish').length, 1);
    assert.equal(f.calls.some(c => c.route.includes('real-email') || c.route.includes('email-preview')), false);
    f.events.get('boardcast:signout')(); // No RealEmail module is loaded in a hosted demo.
    console.log('PASS ' + (hosted ? 'hosted' : 'local') + ' preview and outbox publishing labels, state and routes');
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
