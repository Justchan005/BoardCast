// Run: node tests/newsletter_state.cjs
// Exercise asynchronous newsletter state without a browser, network or stored credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fixture(request = async () => ({template: 'classic'})) {
  const elements = new Map(), calls = []; let pointerHit = null;
  const documentAssignments = [];
  class Element {
    constructor(value = '', id = '', tag = 'div') {
      this.id = id; this.value = value; this.tagName = tag.toUpperCase(); this.hidden = false; this.disabled = false; this.checked = false; this.textContent = ''; this._srcdoc = ''; this.attributes = new Map();
      this.children = []; this.style = {}; this.classes = new Set();
      this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name), contains: name => this.classes.has(name)};
    }
    get srcdoc() { return this._srcdoc; }
    set srcdoc(html) { this._srcdoc = html; documentAssignments.push({node: this, html, hidden: elements.get('newsletter-preview-panel')?.hidden}); }
    addEventListener(name, handler) { this['on' + name] = handler; }
    setAttribute(name, value) { this.attributes.set(name, value); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); if (name === 'srcdoc') this._srcdoc = ''; else if (name === 'src') this.src = ''; }
    cloneNode(deep) { assert.equal(deep, false); const copy = new Element(this.value, this.id); copy.attributes = new Map(this.attributes); copy.hidden = this.hidden; return copy; }
    replaceWith(node) { assert.equal(elements.get(this.id), this); elements.set(this.id, node); this.replaced = true; }
    append(...nodes) { for (const node of nodes) node.parentNode = this; this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    focus() { this.focused = true; }
    closest(selector) { const name = selector.slice(1, -1); return this.attributes.has(name) ? this : this.parentNode?.closest(selector) || null; }
    setPointerCapture(id) { this.pointerCapture = id; }
    hasPointerCapture(id) { return this.pointerCapture === id; }
    releasePointerCapture() { this.pointerCapture = null; }
  }
  const get = id => { if (!elements.has(id)) elements.set(id, new Element('', id)); return elements.get(id); };
  const radios = ['classic', 'newspaper'].map(value => new Element(value));
  get('newsletter-preview-frame').setAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
  get('newsletter-preview-frame').setAttribute('title', 'Selected template preview');
  const context = vm.createContext({
    console,
    Staff: {api: (route, options = {}) => { calls.push({route, ...options}); return request(route, options); }},
    document: {getElementById: get, querySelectorAll: () => radios, createElement: tag => new Element('', '', tag), elementFromPoint: () => pointerHit},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'newsletter.js'), 'utf8'), context);
  const panel = vm.runInContext('Newsletter', context);
  const descendants = node => [node, ...node.children.flatMap(descendants)];
  const cards = () => descendants(get('newsletter-layout-canvas')).filter(node => node.getAttribute('data-story-id'));
  const card = id => cards().find(node => node.getAttribute('data-story-id') === id);
  return {panel, get, radios, calls, documentAssignments, descendants, cards, card,
    choose(value) { for (const radio of radios) radio.checked = radio.value === value; radios.find(r => r.value === value).onchange(); },
    selected() { return radios.find(r => r.checked)?.value; },
    customize(checked = true) { get('newsletter-customize').checked = checked; get('newsletter-customize').onchange(); },
    change(field, value) { const node = get('newsletter-' + field); node.value = value; (node.oninput || node.onchange)(); },
    move(id, destination) { const select = descendants(card(id)).find(node => node.tagName === 'SELECT'); select.value = String(destination); select.onchange(); },
    reorder(id, direction) { descendants(card(id)).find(node => node.tagName === 'BUTTON' && node.getAttribute('aria-label').startsWith(direction === 'up' ? 'Move up:' : 'Move down:')).onclick(); },
    drop(id, destination, before) {
      const event = {preventDefault() {}, stopPropagation() {}, dataTransfer: {setData() {}}};
      card(id).ondragstart(event);
      const target = before ? card(before) : descendants(get('newsletter-layout-canvas')).find(node => node.getAttribute('data-destination') === String(destination));
      target.ondragover(event); target.ondrop(event);
    },
    pointerDrop(id, destination, before, cancel = false) {
      const grip = descendants(card(id)).find(node => node.className === 'editor-grip');
      const event = {pointerId: 1, button: 0, clientX: 0, clientY: 0, preventDefault() {}, stopPropagation() {}};
      grip.onpointerdown(event);
      pointerHit = before ? card(before) : descendants(get('newsletter-layout-canvas')).find(node => node.getAttribute('data-destination') === String(destination));
      const moved = {...event, clientX: 100, clientY: 100}; grip.onpointermove(moved);
      if (cancel) grip.onpointercancel(); else grip.onpointerup(moved);
      assert.equal(grip.hasPointerCapture(1), false);
    },
    preview: () => get('newsletter-preview').onclick(), save: () => get('newsletter-save').onclick(),
  };
}

const editable = (id = 'edition-a', layout = 'classic') => ({id, status: 'preview', layout_editable: true, newsletter_template: layout});
const frozen = (id = 'edition-a', layout = 'classic') => ({id, status: 'sent', layout_editable: false, newsletter_template: layout});
const sampleItems = ['a', 'b', 'c', 'd', 'e'].map((id, index) => ({id, title: 'Story ' + id, type: index % 2 ? 'announcement' : 'event', poster_url: '/uploads/' + id + '.png'}));
const custom = (extra = {}) => ({headline_font: 'georgia', body_font: 'georgia', paper_color: '#F7F3E8', text_color: '#23231F', accent_color: '#9D3229', columns: 2, lead_image: 'left', lead_id: null, story_order: [], column_map: {}, ...extra});
const designed = (extra = {}) => ({...editable('edition-a', 'newspaper'), layout_items: sampleItems, layout_lead_id: 'a', newsletter_design: null, ...extra});
function layout(f) {
  return Object.fromEntries(f.descendants(f.get('newsletter-layout-canvas')).filter(node => node.getAttribute('data-destination'))
    .map(zone => [zone.getAttribute('data-destination'), zone.children.filter(node => node.getAttribute('data-story-id')).map(node => node.getAttribute('data-story-id'))]));
}
function assertExactlyOnce(f, expected = sampleItems.map(item => item.id)) {
  assert.deepEqual(f.cards().map(node => node.getAttribute('data-story-id')).sort(), [...expected].sort());
}

async function run() {
  {
    const f = fixture(); let notifications = 0;
    f.panel.setIssue(editable());
    await f.panel.init({onStateChange: () => notifications++});
    assert.equal(f.selected(), 'classic');
    assert.equal(f.panel.blocksPublish(), false);
    f.choose('newspaper');
    assert.equal(f.panel.blocksPublish(), true, 'an unsaved choice must block building and sending');
    assert.equal(f.get('newsletter-current').textContent, 'Classic', 'summary must show the saved baseline');
    assert.equal(f.calls.filter(c => c.method === 'PUT').length, 0, 'selecting a radio must never autosave');
    f.panel.setIssue(editable());
    assert.equal(f.selected(), 'newspaper', 'same-edition refresh preserves an unsaved choice');
    assert.equal(f.panel.blocksPublish(), true);
    f.choose('classic');
    assert.equal(f.panel.blocksPublish(), false);
    assert.ok(notifications > 0);
    console.log('PASS explicit selection, publish guard and same-issue refresh');
  }
  {
    const pending = deferred(); let saved = 0;
    const f = fixture(async (route, options) => options.method === 'PUT' ? pending.promise : {template: 'classic'});
    f.panel.setIssue(editable());
    await f.panel.init({onSaved: async () => { saved++; f.panel.setIssue(editable('edition-a', 'newspaper')); }});
    f.choose('newspaper'); const saving = f.save();
    assert.equal(f.panel.blocksPublish(), true);
    assert.ok(f.radios.every(r => r.disabled));
    f.choose('classic');
    assert.equal(f.selected(), 'newspaper', 'even a queued change event must not mutate a pending save');
    await f.save();
    assert.equal(f.calls.filter(c => c.method === 'PUT').length, 1);
    assert.deepEqual(JSON.parse(f.calls.find(c => c.method === 'PUT').body), {template: 'newspaper', design: null, issue_id: 'edition-a'});
    pending.resolve({template: 'newspaper', issue_id: 'edition-a', applied_to_issue: true}); await saving;
    assert.equal(saved, 1);
    assert.equal(f.get('newsletter-current').textContent, 'Newspaper');
    assert.equal(f.panel.blocksPublish(), false);
    assert.ok(f.radios.every(r => !r.disabled));
    console.log('PASS save payload, duplicate guard and saved-edition refresh');
  }
  {
    const f = fixture(async (route, options) => {
      if (options.method === 'PUT') throw new Error('Delivery already started');
      return {template: 'classic'};
    });
    f.panel.setIssue(editable()); await f.panel.init(); f.choose('newspaper'); await f.save();
    assert.equal(f.selected(), 'newspaper');
    assert.equal(f.get('newsletter-current').textContent, 'Classic');
    assert.equal(f.get('newsletter-save').disabled, false);
    assert.equal(f.panel.blocksPublish(), true);
    assert.match(f.get('newsletter-status').textContent, /still unsaved/);
    f.panel.setIssue(frozen());
    assert.equal(f.selected(), 'classic');
    assert.equal(f.panel.blocksPublish(), false);
    assert.match(f.get('newsletter-status').textContent, /can no longer change/);
    console.log('PASS failed save and frozen-edition transition');
  }
  {
    const f = fixture(async (route, options) => options.method === 'PUT'
      ? {template: 'newspaper', issue_id: null, applied_to_issue: false} : {template: 'classic'});
    f.panel.setIssue(frozen()); await f.panel.init(); f.choose('newspaper');
    assert.equal(f.panel.blocksPublish(), true, 'future-default changes must also block a new preview build');
    await f.save();
    assert.deepEqual(JSON.parse(f.calls.find(c => c.method === 'PUT').body), {template: 'newspaper', design: null});
    assert.match(f.get('newsletter-scope').textContent, /edition uses Classic/);
    assert.match(f.get('newsletter-status').textContent, /future editions/);
    assert.equal(f.panel.blocksPublish(), false);
    f.panel.setIssue(editable('new-edition', 'newspaper'));
    assert.equal(f.get('newsletter-status').textContent, 'Newspaper is saved for this edition.',
      'building a new preview must replace the earlier future-only save status');
    f.panel.setIssue(null);
    assert.equal(f.get('newsletter-status').textContent, 'Newspaper is saved for future editions.',
      'removing the current edition must not retain a current-edition status');
    console.log('PASS frozen editions change future defaults only');
  }
  {
    const old = deferred(), fresh = deferred(); let previews = 0;
    const f = fixture(async (route, options) => options.method === 'POST'
      ? (++previews === 1 ? old.promise : fresh.promise) : {template: 'classic'});
    f.panel.setIssue(editable()); await f.panel.init();
    const first = f.preview(); f.choose('newspaper'); const second = f.preview();
    fresh.resolve({html: '<p>Newspaper current preview</p>', item_count: 2, source: 'edition'}); await second;
    old.resolve({html: '<p>STALE classic preview</p>', item_count: 9, source: 'edition'}); await first;
    assert.equal(f.get('newsletter-preview-frame').srcdoc, '<p>Newspaper current preview</p>');
    assert.match(f.get('newsletter-preview-meta').textContent, /Newspaper.*2 items/);
    assert.equal(f.get('newsletter-preview-panel').hidden, false);
    assert.equal(f.panel.blocksPublish(), true, 'preview never saves the choice');
    assert.equal(f.calls.some(c => c.method === 'PUT'), false);
    f.choose('classic'); assert.equal(f.get('newsletter-preview-panel').hidden, true);
    console.log('PASS out-of-order previews and explicit-save requirement');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'POST' ? pending.promise : {template: 'classic'});
    f.panel.setIssue(editable()); await f.panel.init(); f.choose('newspaper');
    const preview = f.preview(); f.panel.setIssue(editable('edition-b'));
    assert.equal(f.selected(), 'classic');
    assert.match(f.get('newsletter-status').textContent, /unsaved choice was cleared/);
    pending.resolve({html: 'OLD EDITION', item_count: 1, source: 'edition'}); await preview;
    assert.equal(f.get('newsletter-preview-panel').hidden, true);
    assert.equal(f.get('newsletter-preview-frame').srcdoc, '');
    assert.equal(f.get('newsletter-preview').disabled, false);
    console.log('PASS new edition invalidates old preview and selection');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'POST' ? pending.promise : {template: 'classic'});
    f.panel.setIssue(editable()); await f.panel.init();
    const assignments = f.documentAssignments;
    const previousFrame = f.get('newsletter-preview-frame');
    const preview = f.preview();
    assert.equal(f.get('newsletter-preview-panel').hidden, false, 'the iframe must stay in layout while loading');
    pending.resolve({html: '<html><head><style>@media(max-width:600px){p{display:block}}</style></head><body><p>Visible newspaper</p></body></html>', item_count: 1, source: 'edition'});
    await preview;
    assert.equal(assignments.filter(entry => entry.html).length, 1);
    assert.ok(assignments.filter(entry => entry.html).every(entry => !entry.hidden),
      'returned HTML must only be assigned after the preview panel is visible');
    assert.notEqual(f.get('newsletter-preview-frame'), previousFrame, 'success replaces the old browsing context');
    assert.equal(previousFrame.replaced, true);
    assert.equal(f.get('newsletter-preview-frame').getAttribute('sandbox'), 'allow-popups allow-popups-to-escape-sandbox');
    assert.equal(f.get('newsletter-preview-frame').getAttribute('title'), 'Selected template preview');
    assert.match(f.get('newsletter-preview-frame').srcdoc, /Visible newspaper/);
    assert.equal(f.get('newsletter-preview-panel').hidden, false);
    console.log('PASS fresh visible iframe receives HTML with sandbox retained');
  }
  {
    const f = fixture(async (route, options) => {
      if (options.method === 'POST') throw new Error('Preview unavailable');
      return {template: 'classic'};
    });
    await f.panel.init(); await f.preview();
    assert.equal(f.get('newsletter-preview').disabled, false);
    assert.equal(f.get('newsletter-preview-panel').hidden, true);
    assert.match(f.get('newsletter-status').textContent, /Preview unavailable/);
    console.log('PASS preview failure unlocks retry');
  }
  for (const operation of ['init', 'preview', 'save']) {
    const pending = deferred(); let hold = operation === 'init', saved = 0;
    const f = fixture(async (route, options) => hold ? pending.promise : {template: 'classic'});
    let work;
    f.panel.setIssue(editable());
    if (operation === 'init') work = f.panel.init();
    else {
      await f.panel.init({onSaved: async () => saved++}); hold = true;
      if (operation === 'preview') work = f.preview();
      else { f.choose('newspaper'); work = f.save(); }
    }
    f.panel.reset(); hold = false;
    f.panel.setIssue(editable('other-account', 'classic')); await f.panel.init();
    pending.resolve(operation === 'preview' ? {html: 'OLD ACCOUNT', item_count: 1, source: 'edition'}
      : {template: 'newspaper', issue_id: 'edition-a', applied_to_issue: true});
    await work;
    assert.equal(f.selected(), 'classic', operation + ' must not restore the old account choice');
    assert.equal(f.get('newsletter-current').textContent, 'Classic');
    assert.equal(f.get('newsletter-preview-panel').hidden, true);
    assert.equal(f.panel.blocksPublish(), false);
    assert.equal(saved, 0, 'old save completion must not refresh the new account');
  }
  console.log('PASS account reset discards late init, preview and save responses');
  {
    let first = true;
    const f = fixture(async (route, options) => {
      if (first) { first = false; throw new Error('Temporary settings failure'); }
      return {template: 'classic', applied_to_issue: false, issue_id: null};
    });
    await f.panel.init();
    assert.equal(f.get('newsletter-current').textContent, 'Not loaded');
    assert.equal(f.panel.blocksPublish(), true);
    assert.equal(f.get('newsletter-save').disabled, false, 'explicit save recovers an unknown default');
    await f.save();
    assert.equal(f.panel.blocksPublish(), false);
    assert.equal(f.get('newsletter-current').textContent, 'Classic');
    console.log('PASS failed settings load has an explicit recovery path');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'PUT' ? pending.promise : {template: 'classic'});
    f.panel.setIssue(editable()); await f.panel.init(); f.choose('newspaper'); const save = f.save();
    f.panel.setIssue(editable('newer-edition', 'classic'));
    pending.resolve({template: 'newspaper', issue_id: 'edition-a', applied_to_issue: true}); await save;
    assert.equal(f.selected(), 'classic', 'an old-edition save must not restyle the newer preview');
    assert.equal(f.get('newsletter-current').textContent, 'Classic');
    assert.equal(f.panel.blocksPublish(), false);
    console.log('PASS changed edition during pending save retains its actual layout');
  }
  {
    const f = fixture(async () => ({template: 'newspaper', design: null, design_defaults: {newspaper: custom({accent_color: '#123456'})}, items: sampleItems, lead_id: 'c'}));
    await f.panel.init(); f.customize();
    assert.equal(f.get('newsletter-accent-color').value, '#123456', 'defaults come from the server, not duplicated assumptions');
    assert.equal(f.get('newsletter-custom-fields').hidden, false);
    assert.equal(f.panel.blocksPublish(), true);
    assert.deepEqual(layout(f).lead, ['c'], 'the actual default spotlight leads');
    assertExactlyOnce(f);
    f.change('headline-font', 'verdana'); f.change('body-font', 'times');
    f.change('paper-color', '#aabbcc'); f.change('accent-color-value', '#ff0000');
    assert.equal(f.get('newsletter-paper-color-value').value, '#AABBCC');
    assert.equal(f.get('newsletter-accent-color').value, '#FF0000');
    f.change('accent-color-value', 'url(javascript:bad)');
    assert.equal(f.get('newsletter-accent-color-value').value, 'url(javascript:bad)', 'incomplete typing waits for blur before restoring');
    f.get('newsletter-accent-color-value').onchange();
    assert.equal(f.get('newsletter-accent-color-value').value, '#FF0000');
    assert.match(f.get('newsletter-status').textContent, /six-digit hex colour/);
    assert.equal(f.calls.some(call => call.method === 'PUT'), false);
    f.get('newsletter-reset-design').onclick();
    assert.equal(f.get('newsletter-customize').checked, false);
    assert.equal(f.get('newsletter-custom-fields').hidden, true);
    assert.equal(f.panel.blocksPublish(), false, 'returning to the saved preset is clean');
    console.log('PASS server defaults, custom typography/colours, validated hex and preset reset');
  }
  {
    const f = fixture(); f.panel.setIssue(designed()); await f.panel.init(); f.customize();
    assert.deepEqual(layout(f), {lead: ['a'], 1: ['b', 'd'], 2: ['c', 'e']});
    f.drop('b', 'lead');
    assert.deepEqual(layout(f), {lead: ['b'], 1: ['a', 'd'], 2: ['c', 'e']}, 'a dropped lead swaps into the original story position');
    f.drop('e', 1, 'd');
    assert.deepEqual(layout(f), {lead: ['b'], 1: ['a', 'e', 'd'], 2: ['c']});
    f.reorder('e', 'up');
    assert.deepEqual(layout(f), {lead: ['b'], 1: ['e', 'a', 'd'], 2: ['c']});
    assert.equal(f.descendants(f.card('e')).find(node => node.tagName === 'SELECT').focused, true,
      'keyboard focus stays with the story when its up button becomes disabled');
    f.move('a', 2);
    assert.deepEqual(layout(f), {lead: ['b'], 1: ['e', 'd'], 2: ['c', 'a']}, 'Move menus append to the selected column');
    f.drop('b', 2, 'c');
    assert.deepEqual(layout(f), {lead: ['c'], 1: ['e', 'd'], 2: ['b', 'a']}, 'moving the lead to a story promotes that story without duplication');
    assertExactlyOnce(f); assert.equal(f.panel.blocksPublish(), true);
    console.log('PASS drag/drop lead swap, insert-before, column append and accessible story moves');
  }
  {
    const f = fixture(); f.panel.setIssue(designed({newsletter_design: custom({columns: 3, lead_id: 'b', story_order: ['d', 'e', 'c', 'a'], column_map: {d: 3, e: 2, c: 1, a: 3}})}));
    await f.panel.init();
    assert.equal(f.panel.blocksPublish(), false);
    assert.deepEqual(layout(f), {lead: ['b'], 1: ['c'], 2: ['e'], 3: ['d', 'a']});
    f.change('columns', '2');
    assert.deepEqual(layout(f), {lead: ['b'], 1: ['c'], 2: ['d', 'e', 'a']});
    f.change('columns', '1'); assert.deepEqual(layout(f), {lead: ['b'], 1: ['d', 'e', 'c', 'a']});
    assertExactlyOnce(f);
    f.panel.setIssue(designed({newsletter_design: custom()}));
    assert.equal(f.get('newsletter-columns').value, '1', 'same-edition reload preserves unsaved column choices');
    assert.equal(f.panel.blocksPublish(), true);
    f.panel.setIssue(designed({id: 'edition-b', newsletter_design: custom({columns: 3})}));
    assert.equal(f.get('newsletter-columns').value, '3'); assert.equal(f.panel.blocksPublish(), false);
    console.log('PASS saved placements, column reduction and edition refresh/reset');
  }
  {
    const f = fixture(async (route, options) => {
      if (options.method === 'POST') return {html: 'candidate', item_count: 5, source: 'edition'};
      if (options.method === 'PUT') { const body = JSON.parse(options.body); return {template: body.template, design: body.design, applied_to_issue: false, issue_id: null}; }
      return {template: 'newspaper', design: custom()};
    });
    f.panel.setIssue({...designed(), ...frozen('edition-a', 'newspaper')}); await f.panel.init();
    f.move('d', 'lead');
    assert.match(f.get('newsletter-status').textContent, /candidate preview only.*resets the arrangement/);
    assert.equal(f.panel.blocksPublish(), true, 'preview-only arrangement changes still require an explicit save/reset');
    f.change('lead-image', 'right'); await f.preview();
    const candidate = JSON.parse(f.calls.find(call => call.method === 'POST').body);
    assert.equal(candidate.issue_id, 'edition-a'); assert.equal(candidate.design.lead_id, 'd');
    assert.equal(candidate.design.lead_image, 'right'); assert.match(f.get('newsletter-layout-help').textContent, /candidate preview only/);
    await f.save();
    const payload = JSON.parse(f.calls.find(call => call.method === 'PUT').body);
    assert.equal(Object.hasOwn(payload, 'issue_id'), false);
    for (const key of ['lead_id', 'story_order', 'column_map']) assert.equal(Object.hasOwn(payload.design, key), false, key + ' must not become a future default');
    assert.equal(payload.design.lead_image, 'right'); assert.equal(f.panel.blocksPublish(), false);
    assert.deepEqual(layout(f).lead, ['a']); assert.match(f.get('newsletter-status').textContent, /arrangement has been reset/);
    console.log('PASS frozen candidate preview keeps edition ID while future save strips placements and clears dirty state');
  }
  {
    let savedDesign;
    const f = fixture(async (route, options) => {
      if (options.method === 'PUT') { const body = JSON.parse(options.body); savedDesign = body.design; return {template: body.template, design: {...custom(), accent_color: '#ABCDEF'}, issue_design: body.design, applied_to_issue: true, issue_id: body.issue_id}; }
      return {template: 'classic'};
    });
    f.panel.setIssue(designed());
    await f.panel.init({onSaved: async () => f.panel.setIssue(designed({newsletter_design: savedDesign}))});
    f.customize(); f.move('e', 'lead'); f.change('accent-color', '#abcdef'); await f.save();
    const payload = JSON.parse(f.calls.find(call => call.method === 'PUT').body);
    assert.equal(payload.issue_id, 'edition-a'); assert.equal(payload.design.lead_id, 'e'); assert.equal(payload.design.accent_color, '#ABCDEF');
    assert.equal(f.panel.blocksPublish(), false); assertExactlyOnce(f); assert.deepEqual(layout(f).lead, ['e']);
    f.customize(false); await f.save();
    assert.equal(JSON.parse(f.calls.filter(call => call.method === 'PUT').at(-1).body).design, null, 'disabling custom explicitly resets a saved design');
    console.log('PASS editable custom save persists arrangement and explicit null resets preset');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'POST' ? pending.promise : {template: 'newspaper'});
    f.panel.setIssue(designed()); await f.panel.init(); f.customize(); const work = f.preview();
    f.change('paper-color', '#123456');
    pending.resolve({html: 'OLD COLOUR', item_count: 5, source: 'edition'}); await work;
    assert.equal(f.get('newsletter-preview-frame').srcdoc, ''); assert.equal(f.get('newsletter-preview-panel').hidden, true);
    assert.equal(f.get('newsletter-preview').disabled, false); assert.equal(f.panel.blocksPublish(), true);
    console.log('PASS design mutations invalidate in-flight previews');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'PUT' ? pending.promise : {template: 'newspaper'});
    f.panel.setIssue(designed()); await f.panel.init(); f.customize(); f.change('body-font', 'verdana'); const work = f.save();
    for (const id of ['newsletter-customize', 'newsletter-headline-font', 'newsletter-paper-color', 'newsletter-paper-color-value', 'newsletter-columns', 'newsletter-lead-image']) assert.equal(f.get(id).disabled, true);
    const before = layout(f); f.move('e', 'lead'); f.change('body-font', 'times');
    assert.deepEqual(layout(f), before); assert.equal(f.get('newsletter-body-font').value, 'verdana');
    pending.reject(new Error('Edition is sending')); await work;
    assert.equal(f.panel.blocksPublish(), true); assert.equal(f.get('newsletter-save').disabled, false); assert.equal(f.get('newsletter-body-font').disabled, false);
    assert.match(f.get('newsletter-status').textContent, /still unsaved/);
    console.log('PASS pending design save locks controls and failed save retains the candidate');
  }
  {
    const f = fixture(); f.panel.setIssue(designed({layout_items: [], layout_lead_id: null})); await f.panel.init(); f.customize();
    assert.equal(f.cards().length, 0); assert.match(f.get('newsletter-layout-canvas').children[0].textContent, /Approved stories/);
    const malicious = {id: 'only', title: '<img src=x onerror=alert(1)>', type: 'announcement', poster_url: 'javascript:alert(1)'};
    f.panel.setIssue(designed({id: 'single', layout_items: [malicious, malicious], layout_lead_id: 'only', newsletter_design: custom()}));
    assertExactlyOnce(f, ['only']);
    assert.equal(f.descendants(f.card('only')).filter(node => node.tagName === 'IMG').length, 0);
    assert.ok(f.descendants(f.card('only')).some(node => node.textContent === malicious.title), 'titles are text nodes, never HTML');
    assert.ok(f.descendants(f.card('only')).filter(node => ['SELECT', 'BUTTON'].includes(node.tagName)).every(node => node.disabled));
    f.drop('only', 1); assertExactlyOnce(f, ['only']); assert.deepEqual(layout(f).lead, ['only']);
    console.log('PASS empty/single layouts, duplicate exclusion and text-safe story cards');
  }
  {
    const f = fixture(); f.panel.setIssue(designed()); await f.panel.init(); f.customize();
    f.pointerDrop('d', 'lead');
    assert.deepEqual(layout(f), {lead: ['d'], 1: ['b', 'a'], 2: ['c', 'e']});
    f.pointerDrop('e', 1, 'b');
    assert.deepEqual(layout(f), {lead: ['d'], 1: ['e', 'b', 'a'], 2: ['c']});
    const before = layout(f); f.pointerDrop('c', 1, null, true);
    assert.deepEqual(layout(f), before, 'cancelling pointer drag never changes the design');
    assertExactlyOnce(f);
    console.log('PASS pointer grip fallback moves lead/columns and cancels without data loss');
  }
  {
    const f = fixture(async () => ({template: 'newspaper', items: sampleItems.slice(0, 2), lead_id: 'a'}));
    await f.panel.init(); f.customize(); f.change('headline-font', 'verdana');
    f.panel.setIssue(null, {layout_items: sampleItems, layout_lead_id: 'a'});
    assertExactlyOnce(f); assert.equal(f.get('newsletter-headline-font').value, 'verdana');
    assert.equal(f.panel.blocksPublish(), true);
    assert.match(f.get('newsletter-status').textContent, /still unsaved/);
    f.move('e', 'lead');
    assert.match(f.get('newsletter-status').textContent, /candidate preview only.*future editions/);
    assert.equal(f.panel.blocksPublish(), true);
    console.log('PASS no-edition refresh adds approved stories without losing unsaved typography');
  }
  {
    const f = fixture(async (route, options) => options.method === 'POST' ? {html: 'fresh', item_count: 5} : {template: 'newspaper'});
    f.panel.setIssue(designed({newsletter_design: custom({lead_id: 'e'})})); await f.panel.init();
    assert.deepEqual(layout(f).lead, ['e']); f.customize(false); f.customize();
    assert.deepEqual(layout(f).lead, ['a'], 'reset uses the preset lead, not the old customized lead');
    await f.preview();
    const payload = JSON.parse(f.calls.find(call => call.method === 'POST').body);
    assert.equal(payload.design.lead_id, 'a');
    assert.deepEqual(payload.design.story_order, ['b', 'd', 'c', 'e']);
    assert.deepEqual(payload.design.column_map, {b: 1, c: 2, d: 1, e: 2});
    console.log('PASS enabling custom mode materializes the exact visible preset arrangement');
  }
  {
    const pending = deferred(); const f = fixture(() => pending.promise);
    const work = f.panel.init();
    f.panel.setIssue(null, {layout_items: sampleItems, layout_lead_id: 'a'});
    pending.resolve({template: 'newspaper', design: custom(), items: sampleItems.slice(0, 1), lead_id: 'a'});
    await work;
    assertExactlyOnce(f); assert.equal(f.panel.blocksPublish(), false);
    console.log('PASS late settings metadata cannot overwrite a newer approved-story refresh');
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
