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

function fixture(request = async () => ({template: 'classic'}), libraryRequest = async () => ({themes: []})) {
  const elements = new Map(), calls = []; let pointerHit = null;
  const documentAssignments = [];
  class Element {
    constructor(value = '', id = '', tag = 'div') {
      this.id = id; this.value = value; this.tagName = tag.toUpperCase(); this.hidden = false; this.disabled = false; this.checked = false; this.textContent = ''; this._srcdoc = ''; this.attributes = new Map();
      this.children = []; this.style = {setProperty(name, value) { this[name] = value; }}; this.classes = new Set();
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
  const radios = ['classic', 'newspaper', 'spotlight', 'bulletin', 'custom'].map(value => new Element(value));
  get('newsletter-preview-frame').setAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
  get('newsletter-preview-frame').setAttribute('title', 'Selected template preview');
  const context = vm.createContext({
    console,
    Staff: {api: (route, options = {}) => { calls.push({route, ...options}); return route.startsWith('/admin/api/newsletter-themes') ? libraryRequest(route, options) : request(route, options); }},
    document: {getElementById: get, querySelectorAll: () => radios, createElement: tag => new Element('', '', tag), elementFromPoint: () => pointerHit},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'newsletter-theme-editor.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'newsletter-library.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'newsletter.js'), 'utf8'), context);
  const panel = vm.runInContext('Newsletter', context);
  const descendants = node => [node, ...node.children.flatMap(descendants)];
  const cards = () => descendants(get('newsletter-layout-canvas')).filter(node => node.getAttribute('data-story-id'));
  const card = id => cards().find(node => node.getAttribute('data-story-id') === id);
  const themeNodes = () => descendants(get('newsletter-theme-builder'));
  const themeBlock = id => themeNodes().find(node => node.getAttribute('data-theme-block') === id);
  const themeCell = key => themeNodes().find(node => node.getAttribute('data-theme-cell') === key);
  const library = vm.runInContext('ThemeLibrary', context);
  const savedCards = () => descendants(get('newsletter-theme-library')).filter(node => node.getAttribute('data-saved-theme-id'));
  const libraryNodes = () => descendants(get('newsletter-theme-save-panel'));
  return {panel, library, get, radios, calls, documentAssignments, descendants, cards, card,
    savedCards, libraryNodes,
    chooseSaved(id) { savedCards().find(node => node.getAttribute('data-saved-theme-id') === id).onclick(); },
    nameTheme(value) { const input = libraryNodes().find(node => node.id === 'newsletter-theme-name'); input.value = value; input.oninput(); },
    saveTheme(mode = 'create') { return libraryNodes().find(node => node.id === 'newsletter-theme-' + mode).onclick(); },
    themeNodes, themeBlock,
    themeRows: () => themeNodes().filter(node => node.className === 'theme-row').map(row => ({cells: descendants(row).filter(node => node.getAttribute('data-theme-cell')).map(cell => cell.children.filter(node => node.getAttribute('data-theme-block')).map(node => node.getAttribute('data-theme-block')))})),
    themeSection(name) { themeNodes().find(node => node.getAttribute('role') === 'tab' && node.textContent === name).onclick(); },
    themeAction(label) { themeNodes().find(node => node.getAttribute('aria-label') === label).onclick(); },
    themeColumns(section, row, value) { const node = themeNodes().find(node => node.getAttribute('aria-label') === 'Columns in ' + section + ' row ' + row); node.value = String(value); node.onchange(); },
    themeMove(id, destination) { const node = descendants(themeBlock(id)).find(node => node.tagName === 'SELECT'); node.value = destination; node.onchange(); },
    themeDrop(id, destination, before, pointer = false, cancel = false) {
      const event = {pointerId: 2, button: 0, clientX: 0, clientY: 0, preventDefault() {}, stopPropagation() {}, dataTransfer: {setData() {}}};
      const block = themeBlock(id), target = before ? themeBlock(before) : themeCell(destination);
      if (pointer) {
        const grip = descendants(block).find(node => node.className === 'theme-block-grip'); grip.onpointerdown(event); pointerHit = target;
        const moved = {...event, clientX: 100, clientY: 100}; grip.onpointermove(moved);
        if (cancel) grip.onpointercancel(); else grip.onpointerup(moved);
        assert.equal(grip.hasPointerCapture(2), false);
      } else { block.ondragstart(event); target.ondragover(event); target.ondrop(event); }
    },
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
const namedTheme = (id = 'theme-a', name = 'Campus Spotlight', extra = {}) => ({id, name, revision: 1,
  design: {headline_font: 'georgia', body_font: 'arial', paper_color: '#FFFFFF', text_color: '#1B1B1B', accent_color: '#0B7BB5', columns: 2, lead_image: 'left',
    header_rows: [{cells: [['name']]}, {cells: [['date', 'intro']]}], story_rows: [{cells: [['picture'], ['title', 'details']]}]}, ...extra});
const libraryMessage = f => f.libraryNodes().find(node => node.className === 'theme-save-status').textContent;
const libraryWrites = f => f.calls.filter(call => call.route.startsWith('/admin/api/newsletter-themes') && ['POST', 'PUT'].includes(call.method));
function layout(f) {
  return Object.fromEntries(f.descendants(f.get('newsletter-layout-canvas')).filter(node => node.getAttribute('data-destination'))
    .map(zone => [zone.getAttribute('data-destination'), zone.children.filter(node => node.getAttribute('data-story-id')).map(node => node.getAttribute('data-story-id'))]));
}
function assertExactlyOnce(f, expected = sampleItems.map(item => item.id)) {
  assert.deepEqual(f.cards().map(node => node.getAttribute('data-story-id')).sort(), [...expected].sort());
}

async function run() {
  {
    const f = fixture(); f.panel.setIssue(editable()); await f.panel.init();
    for (const choice of ['spotlight', 'bulletin']) {
      f.choose(choice); assert.equal(f.selected(), choice);
      assert.equal(f.get('newsletter-change-bar').hidden, false);
      assert.equal(f.get('newsletter-dirty-marker').hidden, false);
      assert.match(f.get('newsletter-change-message').textContent, /Save or discard/);
      f.get('newsletter-discard').onclick();
      assert.equal(f.selected(), 'classic');
      assert.equal(f.get('newsletter-change-bar').hidden, true);
      assert.equal(f.panel.blocksPublish(), false);
    }
    f.choose('custom'); f.choose('spotlight');
    assert.equal(f.get('newsletter-customize').checked, false, 'a built-in layout starts with its own composition');
    f.get('newsletter-discard-bottom').onclick();
    assert.equal(f.selected(), 'classic');
    console.log('PASS distinct presets, visible unsaved state and discard');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'PUT' ? pending.promise : {template:'classic'});
    f.panel.setIssue(editable()); await f.panel.init(); f.choose('spotlight');
    const saving = f.get('newsletter-save-top').onclick();
    assert.equal(f.get('newsletter-discard').disabled, true);
    f.get('newsletter-discard').onclick(); assert.equal(f.selected(), 'spotlight');
    pending.resolve({template:'spotlight', issue_id:'edition-a', applied_to_issue:true}); await saving;
    assert.equal(f.get('newsletter-change-bar').hidden, true);
    assert.equal(f.panel.blocksPublish(), false);
    console.log('PASS top save action and discard protection during save');
  }
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
  {
    const f = fixture(); await f.panel.init(); f.choose('custom');
    assert.equal(f.get('newsletter-theme-builder').hidden, false);
    assert.equal(f.get('newsletter-custom-fields').hidden, false);
    assert.equal(f.get('newsletter-customize').checked, true);
    assert.equal(f.get('newsletter-customize').disabled, true, 'Custom always has a design');
    assert.equal(f.get('newsletter-lead-image-field').hidden, true, 'the placeholder layout owns image position');
    assert.deepEqual(f.themeRows(), [{cells: [['name']]}, {cells: [['date', 'intro']]}]);
    assert.equal(f.cards().length, 0, 'theme editing works before any approved stories exist');
    f.customize(false); assert.equal(f.get('newsletter-customize').checked, true);
    f.themeDrop('date', '0:0', 'name');
    assert.deepEqual(f.themeRows(), [{cells: [['date', 'name']]}, {cells: [['intro']]}]);
    f.themeMove('intro', '0:0');
    assert.deepEqual(f.themeRows(), [{cells: [['date', 'name', 'intro']]}, {cells: [[]]}]);
    f.themeAction('Move up: Introduction');
    assert.deepEqual(f.themeRows(), [{cells: [['date', 'intro', 'name']]}, {cells: [[]]}]);
    f.themeAction('Remove empty header row 2');
    assert.deepEqual(f.themeRows(), [{cells: [['date', 'intro', 'name']]}]);
    assert.equal(f.panel.blocksPublish(), true); assert.equal(f.calls.some(call => call.method === 'PUT'), false);
    console.log('PASS Custom card enables placeholder editor with no submissions and accessible header moves');
  }
  {
    const f = fixture(); await f.panel.init(); f.choose('custom'); f.themeSection('Story layout');
    assert.deepEqual(f.themeRows(), [{cells: [['picture'], ['title', 'details']]}]);
    f.themeDrop('title', '0:0', 'picture', true);
    assert.deepEqual(f.themeRows(), [{cells: [['title', 'picture'], ['details']]}]);
    f.themeDrop('picture', '0:1', null, true);
    assert.deepEqual(f.themeRows(), [{cells: [['title'], ['details', 'picture']]}]);
    const before = f.themeRows(); f.themeDrop('title', '0:1', null, true, true); assert.deepEqual(f.themeRows(), before);
    f.themeColumns('story', 1, 1);
    assert.deepEqual(f.themeRows(), [{cells: [['title', 'details', 'picture']]}], 'merging a row retains every placeholder in reading order');
    f.themeAction('Add story row'); f.themeMove('picture', '1:0');
    f.themeAction('Move story row 2 up');
    assert.deepEqual(f.themeRows(), [{cells: [['picture']]}, {cells: [['title', 'details']]}]);
    f.themeAction('Remove empty story row 1');
    assert.equal(f.themeRows().length, 2, 'nonempty rows cannot lose their content');
    for (let i = 0; i < 8; i++) f.themeAction('Add story row');
    assert.equal(f.themeRows().length, 6, 'row count stays within the API bound');
    assert.equal(f.themeNodes().find(node => node.getAttribute('aria-label') === 'Add story row').disabled, true);
    console.log('PASS placeholder pointer dragging, cancellation, column merging and bounded row changes');
  }
  {
    const f = fixture(async (route, options) => {
      if (options.method === 'POST') return {html: 'custom theme', item_count: 5, source: 'edition'};
      if (options.method === 'PUT') { const body = JSON.parse(options.body); return {template: body.template, design: body.design, applied_to_issue: false, issue_id: null}; }
      return {template: 'classic'};
    });
    f.panel.setIssue({...designed(), ...frozen()}); await f.panel.init(); f.choose('custom');
    f.themeMove('name', '1:0'); f.themeSection('Story layout'); f.themeDrop('picture', '0:1', 'details');
    f.move('d', 'lead'); await f.preview();
    const preview = JSON.parse(f.calls.find(call => call.method === 'POST').body);
    assert.equal(preview.template, 'custom'); assert.equal(preview.issue_id, 'edition-a'); assert.equal(preview.design.lead_id, 'd');
    assert.deepEqual(preview.design.header_rows, [{cells: [[]]}, {cells: [['date', 'intro', 'name']]}]);
    assert.deepEqual(preview.design.story_rows, [{cells: [[], ['title', 'picture', 'details']]}]);
    await f.save();
    const save = JSON.parse(f.calls.find(call => call.method === 'PUT').body);
    assert.deepEqual(save.design.header_rows, preview.design.header_rows); assert.deepEqual(save.design.story_rows, preview.design.story_rows);
    for (const key of ['lead_id', 'story_order', 'column_map']) assert.equal(Object.hasOwn(save.design, key), false);
    assert.equal(f.panel.blocksPublish(), false); assert.equal(f.selected(), 'custom');
    assert.deepEqual(f.themeRows(), preview.design.story_rows, 'saving a future theme retains its reusable structure');
    f.panel.setIssue({...editable('new-custom', 'custom'), newsletter_design: save.design});
    assert.deepEqual(f.themeRows(), preview.design.story_rows); assert.equal(f.panel.blocksPublish(), false);
    console.log('PASS Custom preview/save preserves reusable placeholders while frozen story placements stay preview-only');
  }
  {
    const pending = deferred(); const f = fixture(async (route, options) => options.method === 'POST' ? pending.promise : {template: 'custom', design: null});
    await f.panel.init(); assert.equal(f.panel.blocksPublish(), false, 'a Custom default resolves safely even without stored design data');
    const work = f.preview(); f.themeMove('name', '1:0');
    pending.resolve({html: 'OLD THEME', item_count: 0}); await work;
    assert.equal(f.get('newsletter-preview-panel').hidden, true); assert.equal(f.get('newsletter-preview-frame').srcdoc, '');
    assert.equal(f.panel.blocksPublish(), true);
    f.get('newsletter-reset-design').onclick();
    assert.deepEqual(f.themeRows(), [{cells: [['name']]}, {cells: [['date', 'intro']]}]);
    assert.equal(f.get('newsletter-customize').checked, true); assert.equal(f.panel.blocksPublish(), false);
    console.log('PASS theme mutation invalidates stale previews and Custom reset keeps a valid editable theme');
  }
  {
    const pending = deferred();
    const f = fixture(async (route, options) => options.method === 'PUT' ? pending.promise : {template: 'custom', design: null});
    await f.panel.init(); f.themeMove('name', '1:0'); const before = f.themeRows(); const work = f.save();
    assert.ok(f.themeNodes().filter(node => ['SELECT', 'BUTTON'].includes(node.tagName)).every(node => node.disabled));
    f.themeMove('name', '0:0'); f.themeAction('Add header row'); assert.deepEqual(f.themeRows(), before);
    pending.reject(new Error('Try again')); await work;
    assert.deepEqual(f.themeRows(), before); assert.equal(f.panel.blocksPublish(), true);
    f.panel.reset(); f.panel.setIssue({...editable('next-account', 'custom'), newsletter_design: null}); await f.panel.init();
    assert.deepEqual(f.themeRows(), [{cells: [['name']]}, {cells: [['date', 'intro']]}]);
    console.log('PASS pending theme save locks all structural controls and account reset discards old placeholders');
  }
  {
    const badRows = [{cells: [['name', 'name', '<script>alert(1)</script>']]}];
    const f = fixture(async () => ({template: 'custom', design: custom({header_rows: badRows, story_rows: [{cells: [['title']]}]})}));
    await f.panel.init(); assert.deepEqual(f.themeRows(), [{cells: [['name']]}, {cells: [['date', 'intro']]}]);
    assert.ok(!f.themeNodes().some(node => node.textContent.includes('<script>')));
    f.themeSection('Story layout'); assert.deepEqual(f.themeRows(), [{cells: [['picture'], ['title', 'details']]}]);
    console.log('PASS invalid placeholder schemas fall back without rendering untrusted HTML or dropping content');
  }
  {
    const theme = namedTheme();
    const f = fixture(async () => ({template: 'classic'}), async () => ({themes: [theme]}));
    f.panel.setIssue(designed({newsletter_design: custom({lead_id: 'b', story_order: ['e', 'd', 'c', 'a'], column_map: {e: 2, d: 1, c: 2, a: 1}})}));
    await f.panel.init(); const prior = layout(f); f.chooseSaved(theme.id);
    assert.equal(f.savedCards()[0].getAttribute('aria-pressed'), 'true');
    assert.ok(f.radios.every(radio => !radio.checked));
    assert.equal(f.get('newsletter-customize').checked, true); assert.deepEqual(layout(f), prior);
    assert.equal(f.get('newsletter-headline-font').value, 'georgia'); assert.equal(f.panel.blocksPublish(), true);
    assert.equal(libraryWrites(f).length, 0); assert.equal(f.calls.some(call => call.route === '/admin/api/newsletter-template' && call.method === 'PUT'), false);
    f.choose('classic'); assert.equal(f.savedCards()[0].getAttribute('aria-pressed'), 'false');
    console.log('PASS saved-theme selection copies appearance, preserves story placement and waits for explicit application');
  }
  {
    const pending = deferred();
    const f = fixture(async () => ({template: 'classic'}), async (route, options) => options.method === 'POST' ? pending.promise : {themes: []});
    f.panel.setIssue(designed()); await f.panel.init(); f.choose('custom'); f.themeMove('name', '1:0'); f.move('b', 'lead'); f.nameTheme('  Spring   Campus  ');
    const before = f.themeRows(), work = f.saveTheme();
    assert.ok(f.radios.every(radio => radio.disabled)); assert.equal(f.get('newsletter-save').disabled, true);
    const payload = JSON.parse(libraryWrites(f)[0].body);
    assert.equal(payload.name, 'Spring Campus'); for (const key of ['lead_id', 'story_order', 'column_map']) assert.equal(Object.hasOwn(payload.design, key), false);
    f.change('headline-font', 'verdana'); assert.equal(f.get('newsletter-headline-font').value, 'arial');
    pending.resolve({theme: namedTheme('new-theme', payload.name, {design: payload.design})}); await work;
    assert.deepEqual(f.themeRows(), before); assert.equal(f.panel.blocksPublish(), true); assert.equal(f.get('newsletter-current').textContent, 'Newspaper');
    assert.equal(f.savedCards().length, 1); assert.equal(f.savedCards()[0].getAttribute('aria-pressed'), 'true');
    assert.match(libraryMessage(f), /saved to your themes/);
    assert.equal(f.calls.some(call => call.route === '/admin/api/newsletter-template' && call.method === 'PUT'), false);
    console.log('PASS named save strips edition placements, locks duplicate writes and leaves the edition unsaved');
  }
  {
    const theme = namedTheme(); let current = theme;
    const f = fixture(async () => ({template: 'classic'}), async (route, options) => {
      if (!options.method) return {themes: [current]};
      const data = JSON.parse(options.body); current = {...current, name: data.name, revision: current.revision + 1, design: data.design || current.design}; return {theme: current};
    });
    await f.panel.init(); f.chooseSaved(theme.id); f.change('headline-font', 'verdana'); f.nameTheme('Campus Weekly Green');
    await f.saveTheme('rename');
    const rename = JSON.parse(libraryWrites(f)[0].body);
    assert.deepEqual(rename, {name: 'Campus Weekly Green', revision: 1});
    assert.equal(current.design.headline_font, 'georgia'); assert.equal(f.get('newsletter-headline-font').value, 'verdana');
    assert.equal(f.panel.blocksPublish(), true); await f.saveTheme('update');
    const update = JSON.parse(libraryWrites(f)[1].body);
    assert.equal(update.revision, 2); assert.equal(update.design.headline_font, 'verdana');
    assert.equal(current.revision, 3); assert.equal(f.savedCards()[0].getAttribute('data-saved-theme-id'), theme.id);
    assert.equal(f.panel.blocksPublish(), true);
    console.log('PASS rename preserves saved design while update uses the new revision and never applies the edition');
  }
  {
    const theme = namedTheme(); let latest = theme, fail = true;
    const f = fixture(async () => ({template: 'classic'}), async (route, options) => {
      if (!options.method) return {themes: [latest]};
      if (fail) throw new Error('This theme was updated by another reviewer. Reload it and try again.');
      return {theme: latest};
    });
    await f.panel.init(); f.chooseSaved(theme.id); f.change('body-font', 'times'); await f.saveTheme('update');
    assert.match(libraryMessage(f), /Your design is still here/); assert.equal(f.get('newsletter-body-font').value, 'times');
    latest = {...theme, revision: 2}; await f.library.load(); await f.saveTheme('update');
    assert.equal(libraryWrites(f).length, 1, 'refreshing the list must not silently authorize overwriting a newer theme revision');
    assert.match(libraryMessage(f), /Select its card again/); f.chooseSaved(theme.id); fail = false; await f.saveTheme('update');
    assert.equal(JSON.parse(libraryWrites(f)[1].body).revision, 2);
    console.log('PASS update conflicts preserve the draft and require explicitly loading the latest theme revision');
  }
  {
    const f = fixture(async () => ({template: 'classic'})); await f.panel.init(); f.choose('custom');
    for (const value of ['   ', 'x'.repeat(61), 'Bad\u0000Name']) { f.nameTheme(value); await f.saveTheme(); }
    assert.equal(libraryWrites(f).length, 0); assert.equal(f.panel.blocksPublish(), true); assert.match(libraryMessage(f), /control characters/);
    const theme = namedTheme('safe-name', '<img src=x onerror=alert(1)>');
    const safe = fixture(async () => ({template: 'classic'}), async () => ({themes: [theme]})); await safe.panel.init();
    const title = safe.descendants(safe.savedCards()[0]).find(node => node.className === 'theme-library-title');
    assert.equal(title.textContent, theme.name); assert.equal(title.children.length, 0);
    console.log('PASS invalid names stay local and saved-theme names render as text');
  }
  {
    let fail = true; const theme = namedTheme();
    const f = fixture(async () => ({template: 'classic'}), async () => { if (fail) throw new Error('Offline'); return {themes: [theme]}; });
    await f.panel.init(); assert.equal(f.panel.blocksPublish(), false); assert.ok(f.radios.every(radio => !radio.disabled));
    const retry = f.descendants(f.get('newsletter-theme-library')).find(node => node.id === 'newsletter-themes-reload');
    assert.equal(retry.textContent, 'Retry saved themes'); fail = false; await retry.onclick(); assert.equal(f.savedCards().length, 1);
    console.log('PASS library failure leaves presets available and exposes retry');
  }
  {
    const a = namedTheme(), b = namedTheme('theme-b', 'Identical copy');
    const f = fixture(async () => ({template: 'custom', design: a.design}), async () => ({themes: [a, b]}));
    await f.panel.init(); assert.equal(f.get('newsletter-current').textContent, 'Custom'); assert.ok(f.savedCards().every(card => card.getAttribute('aria-pressed') === 'false'));
    f.chooseSaved(b.id); assert.equal(f.savedCards().find(card => card.getAttribute('data-saved-theme-id') === b.id).getAttribute('aria-pressed'), 'true');
    const unique = fixture(async () => ({template: 'custom', design: a.design}), async () => ({themes: [a]}));
    await unique.panel.init(); assert.equal(unique.get('newsletter-current').textContent, a.name); assert.equal(unique.savedCards()[0].getAttribute('aria-pressed'), 'true');
    assert.equal(unique.panel.blocksPublish(), false);
    console.log('PASS reload infers only an unambiguous named appearance and preserves explicit card choice');
  }
  {
    const pending = deferred(), theme = namedTheme(); let oldSession = true;
    const f = fixture(async () => ({template: 'classic'}), async () => oldSession ? pending.promise : {themes: []});
    await f.panel.init(); f.panel.reset(); oldSession = false; await f.panel.init(); pending.resolve({themes: [theme]}); await pending.promise; await Promise.resolve();
    assert.equal(f.savedCards().length, 0);
    const pendingWrite = deferred();
    const g = fixture(async () => ({template: 'classic'}), async (route, options) => options.method === 'POST' ? pendingWrite.promise : {themes: []});
    await g.panel.init(); g.choose('custom'); g.nameTheme('Old account'); const work = g.saveTheme(); g.panel.reset(); await g.panel.init();
    pendingWrite.resolve({theme}); await work; assert.equal(g.savedCards().length, 0); assert.equal(g.get('newsletter-theme-save-panel').hidden, true);
    console.log('PASS account changes discard late library loads and save responses');
  }
  {
    const theme = namedTheme(), stale = deferred(), pendingWrite = deferred(); let loads = 0;
    const f = fixture(async () => ({template: 'classic'}), async (route, options) => options.method === 'POST' ? pendingWrite.promise : ++loads === 1 ? {themes: []} : stale.promise);
    await f.panel.init(); const loading = f.library.load(); f.choose('custom'); f.nameTheme(theme.name); const work = f.saveTheme();
    pendingWrite.resolve({theme}); await work; stale.resolve({themes: []}); await loading;
    assert.equal(f.savedCards().length, 1); assert.equal(f.savedCards()[0].getAttribute('data-saved-theme-id'), theme.id);
    console.log('PASS a stale library reload cannot erase a newer saved theme');
  }
  {
    const pending = deferred(), theme = namedTheme();
    const f = fixture(async () => ({template: 'classic'}), async (route, options) => options.method === 'POST' ? pending.promise : {themes: []});
    f.panel.setIssue(editable('first')); await f.panel.init(); f.choose('custom'); f.nameTheme(theme.name); const work = f.saveTheme();
    f.panel.setIssue(editable('next', 'newspaper')); pending.resolve({theme}); await work;
    assert.equal(f.selected(), 'newspaper'); assert.equal(f.get('newsletter-current').textContent, 'Newspaper'); assert.equal(f.panel.blocksPublish(), false);
    assert.equal(f.savedCards().length, 1); assert.equal(f.savedCards()[0].getAttribute('aria-pressed'), 'false');
    console.log('PASS an edition switch during library save keeps its own draft and only adds the library record');
  }
  {
    const a = namedTheme(), b = namedTheme('theme-b', 'Identical copy');
    const f = fixture(async (route, options) => options.method === 'PUT'
      ? {template: 'custom', applied_to_issue: true, issue_id: 'edition-a', issue_design: JSON.parse(options.body).design, design: JSON.parse(options.body).design}
      : {template: 'classic'}, async () => ({themes: [a, b]}));
    f.panel.setIssue(editable()); await f.panel.init(); f.chooseSaved(b.id); await f.save();
    assert.equal(f.savedCards().find(card => card.getAttribute('data-saved-theme-id') === b.id).getAttribute('aria-pressed'), 'true');
    f.change('headline-font', 'verdana'); await f.save();
    assert.equal(f.panel.blocksPublish(), false); assert.equal(f.get('newsletter-current').textContent, 'Custom');
    assert.equal(f.savedCards().find(card => card.getAttribute('data-saved-theme-id') === b.id).getAttribute('aria-pressed'), 'true');
    assert.ok(f.libraryNodes().some(node => node.id === 'newsletter-theme-update'), 'applying an edited named draft must still allow updating its source theme');
    f.panel.setIssue(editable('other-edition', 'custom'));
    assert.ok(f.savedCards().every(card => card.getAttribute('aria-pressed') === 'false'));
    console.log('PASS explicit named source survives applying its draft but is cleared for a different edition');
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
