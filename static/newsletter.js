// Student Affairs design selection. The server owns permissions and edition state.
const Newsletter = (() => {
  const $ = id => document.getElementById(id);
  const radios = [...document.querySelectorAll('input[name="newsletter-template"]')];
  const labels = {classic: 'Classic', newspaper: 'Newspaper', spotlight: 'Spotlight', bulletin: 'Bulletin', custom: 'Custom'};
  const fontIds = ['georgia', 'times', 'arial', 'verdana'];
  const colorFields = ['paper_color', 'text_color', 'accent_color'];
  const fields = ['headline_font', 'body_font', ...colorFields, 'columns', 'lead_image'];
  const control = field => $('newsletter-' + field.replaceAll('_', '-'));
  const presetDefaults = {
    classic: {headline_font: 'arial', body_font: 'arial', paper_color: '#FFFFFF', text_color: '#1B1B1B', accent_color: '#0B7BB5', columns: 1, lead_image: 'left'},
    newspaper: {headline_font: 'georgia', body_font: 'georgia', paper_color: '#F7F3E8', text_color: '#23231F', accent_color: '#9D3229', columns: 2, lead_image: 'left'},
    spotlight: {headline_font: 'georgia', body_font: 'arial', paper_color: '#FFFFFF', text_color: '#1B1B1B', accent_color: '#1F4FD8', columns: 2, lead_image: 'left'},
    bulletin: {headline_font: 'arial', body_font: 'arial', paper_color: '#FFFFFF', text_color: '#1B1B1B', accent_color: '#1F4FD8', columns: 1, lead_image: 'left'},
    custom: {headline_font: 'arial', body_font: 'arial', paper_color: '#FFFFFF', text_color: '#1B1B1B', accent_color: '#0B7BB5', columns: 1, lead_image: 'left'},
  };
  let defaults = {...presetDefaults};
  let issue = null, preference = 'classic', selected = 'classic', preferenceKnown = false;
  let preferenceDesign = null, design = null, approvedItems = [], approvedLead = null, approvedRevision = 0;
  let initialized = false, loading = false, saving = false, previewing = false;
  let epoch = 0, contextVersion = 0, previewSerial = 0, saveSerial = 0, draggedId = null;
  let onStateChange = () => {}, onSaved = async () => {};
  let focusTargets = new Map(), dragElements = [], pointerDrag = null;
  const template = value => Object.hasOwn(labels, value) ? value : 'classic';
  const editable = () => !!issue && issue.layout_editable === true;
  const baseline = () => editable() ? template(issue.newsletter_template) : preference;
  const baselineDesign = () => editable() ? issue.newsletter_design ?? null : preferenceDesign;
  const busy = () => !initialized || loading || saving || ThemeLibrary.isSaving();
  const clone = value => value == null ? null : JSON.parse(JSON.stringify(value));
  function items() {
    const seen = new Set();
    return (issue ? issue.layout_items || [] : approvedItems).filter(item => {
      if (!item || typeof item.id !== 'string' || seen.has(item.id)) return false;
      seen.add(item.id); return true;
    });
  }
  function normalized(value, preset = selected) {
    if (!value && preset !== 'custom') return null;
    value = value || defaults.custom;
    const base = defaults[preset] || presetDefaults[preset], result = {};
    for (const field of ['headline_font', 'body_font']) result[field] = fontIds.includes(value[field]) ? value[field] : base[field];
    for (const field of colorFields) result[field] = /^#[\da-f]{6}$/i.test(value[field] || '') ? value[field].toUpperCase() : base[field];
    result.columns = [1, 2, 3].includes(Number(value.columns)) ? Number(value.columns) : base.columns;
    result.lead_image = ['left', 'right'].includes(value.lead_image) ? value.lead_image : base.lead_image;
    for (const section of ['header', 'story']) {
      const key = section + '_rows';
      if (preset === 'custom' || value[key] != null) result[key] = ThemeEditor.normalizeRows(value[key], section);
    }
    const ids = new Set(items().map(item => item.id));
    result.lead_id = ids.has(value.lead_id) ? value.lead_id : null;
    result.story_order = [...new Set(Array.isArray(value.story_order) ? value.story_order : [])].filter(id => ids.has(id));
    result.column_map = {};
    for (const id of [...ids].sort()) {
      const column = value.column_map?.[id];
      if ([1, 2, 3].includes(column)) result.column_map[id] = Math.min(column, result.columns);
    }
    return result;
  }
  const signature = (value, preset = selected) => JSON.stringify(normalized(value, preset));
  const dirty = () => !preferenceKnown || selected !== baseline() || signature(design) !== signature(baselineDesign(), baseline());
  function blocksPublish() { return initialized && (loading || saving || ThemeLibrary.isSaving() || dirty()); }
  function discard() {
    if (busy()) return;
    contextVersion++; clearPreview(); cleanDrag(); restoreBaseline();
    status('Changes discarded. ' + savedStatus()); render();
  }
  function status(message) { $('newsletter-status').textContent = message; }
  function restoreBaseline() { selected = baseline(); design = selected === 'custom' ? normalized(baselineDesign()) : clone(baselineDesign()); ThemeLibrary.syncSelection(selected, design, true); }
  function designLabel(preset, value) { return ThemeLibrary.label(preset, value) || labels[preset]; }
  function savedStatus() { return designLabel(baseline(), baselineDesign()) + ' is saved' + (editable() ? ' for this edition.' : ' for future editions.'); }
  function clearFrame() { const frame = $('newsletter-preview-frame'); frame.removeAttribute('src'); frame.srcdoc = ''; }
  function clearPreview() {
    previewSerial++; previewing = false;
    $('newsletter-preview-panel').hidden = true; clearFrame(); $('newsletter-preview-meta').textContent = '';
  }
  function arrangement() {
    const list = items(), ids = list.map(item => item.id), settings = normalized(design || defaults[selected]);
    const defaultLead = issue ? issue.layout_lead_id : approvedLead;
    const lead = ids.includes(settings.lead_id) ? settings.lead_id : ids.includes(defaultLead) ? defaultLead : ids[0] ?? null;
    const order = [...settings.story_order, ...ids].filter((id, index, all) => id !== lead && all.indexOf(id) === index);
    const columns = Array.from({length: settings.columns}, () => []);
    // Explicit assignments remain stable; new stories go into the shortest column.
    for (const id of order) if (settings.column_map[id]) columns[settings.column_map[id] - 1].push(id);
    for (const id of order) if (!settings.column_map[id]) {
      const shortest = Math.min(...columns.map(column => column.length));
      columns.find(column => column.length === shortest).push(id);
    }
    return {lead, columns};
  }
  function changed(message, focusKey, previewOnlyArrangement = false) {
    contextVersion++; clearPreview();
    status(dirty() ? previewOnlyArrangement
      ? 'Story arrangement is for this candidate preview only. Saving keeps appearance settings for future editions and resets the arrangement.'
      : message + ' Choose “Use this template” to save.' : savedStatus());
    render();
    if (focusKey) {
      const target = focusTargets.get(focusKey);
      (target?.disabled ? focusTargets.get(focusKey.replace(/:(up|down)$/, ':move')) : target)?.focus();
    }
  }
  function saveArrangement(layout) {
    design = normalized(design || defaults[selected]);
    design.lead_id = layout.lead; design.story_order = layout.columns.flat(); design.column_map = {};
    layout.columns.forEach((column, index) => column.forEach(id => { design.column_map[id] = index + 1; }));
  }
  function move(id, destination, before = null, focusKey) {
    if (busy() || !design || !items().some(item => item.id === id)) return;
    const layout = arrangement(), from = layout.columns.findIndex(column => column.includes(id));
    if (destination === 'lead') {
      if (id === layout.lead) return;
      const at = layout.columns[from].indexOf(id);
      layout.columns[from].splice(at, 1, ...(layout.lead ? [layout.lead] : []));
      layout.lead = id;
    } else {
      const target = Number(destination) - 1;
      if (!layout.columns[target] || before === id) return;
      if (id === layout.lead) {
        // Moving the lead into a column promotes its destination story, or the
        // first available supporting story, so no article disappears or repeats.
        const replacement = before && layout.columns[target].includes(before) ? before : layout.columns[target][0] || layout.columns.flat()[0];
        if (!replacement) return;
        layout.lead = replacement;
        const replacementColumn = layout.columns.find(column => column.includes(replacement));
        const replacementIndex = replacementColumn.indexOf(replacement);
        replacementColumn.splice(replacementIndex, 1);
        if (before === replacement) before = layout.columns[target][replacementIndex] || null;
      } else layout.columns[from].splice(layout.columns[from].indexOf(id), 1);
      const at = before ? layout.columns[target].indexOf(before) : -1;
      layout.columns[target].splice(at < 0 ? layout.columns[target].length : at, 0, id);
    }
    saveArrangement(layout); changed('Story arrangement changed.', focusKey, !editable());
  }
  function reorder(id, direction, focusKey) {
    if (busy() || !design) return;
    const layout = arrangement(), column = layout.columns.find(value => value.includes(id));
    if (!column) return;
    const index = column.indexOf(id), target = index + direction;
    if (target < 0 || target >= column.length) return;
    [column[index], column[target]] = [column[target], column[index]];
    saveArrangement(layout); changed('Story order changed.', focusKey, !editable());
  }
  function element(tag, className, text) {
    const node = document.createElement(tag); if (className) node.className = className;
    if (text !== undefined) node.textContent = text; return node;
  }
  function cleanDrag() {
    draggedId = null;
    if (pointerDrag) {
      const active = pointerDrag; pointerDrag = null;
      if (active.grip.hasPointerCapture?.(active.pointerId)) active.grip.releasePointerCapture(active.pointerId);
    }
    for (const node of dragElements) { node.classList.remove('drag-over'); node.classList.remove('dragging'); }
  }
  function pointerDestination(event) {
    const hit = document.elementFromPoint(event.clientX, event.clientY);
    const zone = hit?.closest('[data-destination]');
    if (!zone || !$('newsletter-layout-canvas').contains(zone)) return null;
    const card = hit.closest('[data-story-id]');
    return {node: card || zone, destination: zone.getAttribute('data-destination'), before: card?.getAttribute('data-story-id') || null};
  }
  function pointerGrip(grip, card, id) {
    grip.style.touchAction = 'none'; grip.draggable = false;
    grip.addEventListener('pointerdown', event => {
      if (busy() || event.button > 0 || event.isPrimary === false) return;
      event.preventDefault(); event.stopPropagation(); cleanDrag();
      pointerDrag = {id, grip, card, pointerId: event.pointerId, x: event.clientX, y: event.clientY, active: false};
      grip.setPointerCapture?.(event.pointerId);
    });
    grip.addEventListener('pointermove', event => {
      if (!pointerDrag || pointerDrag.pointerId !== event.pointerId) return;
      if (!pointerDrag.active && Math.hypot(event.clientX - pointerDrag.x, event.clientY - pointerDrag.y) < 4) return;
      pointerDrag.active = true; draggedId = id; card.classList.add('dragging'); event.preventDefault();
      for (const node of dragElements) node.classList.remove('drag-over');
      const target = pointerDestination(event);
      if (target?.before !== id) target?.node.classList.add('drag-over');
    });
    grip.addEventListener('pointerup', event => {
      if (!pointerDrag || pointerDrag.pointerId !== event.pointerId) return;
      const active = pointerDrag.active, target = active ? pointerDestination(event) : null;
      event.preventDefault(); cleanDrag();
      if (target) move(id, target.destination, target.before, id + ':move');
    });
    grip.addEventListener('pointercancel', cleanDrag);
    grip.addEventListener('lostpointercapture', cleanDrag);
  }
  function dropTarget(node, destination, before = null) {
    dragElements.push(node);
    node.addEventListener('dragover', event => {
      if (busy() || !draggedId) return;
      event.preventDefault(); event.stopPropagation(); node.classList.add('drag-over');
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });
    node.addEventListener('dragleave', event => { if (!node.contains(event.relatedTarget)) node.classList.remove('drag-over'); });
    node.addEventListener('drop', event => {
      if (busy() || !draggedId) return;
      event.preventDefault(); event.stopPropagation(); const id = draggedId; cleanDrag(); move(id, destination, before);
    });
  }
  function storyCard(item, destination, index, total) {
    const card = element('article', 'editor-story');
    card.setAttribute('data-story-id', item.id); card.draggable = !busy();
    card.addEventListener('dragstart', event => {
      if (busy()) { event.preventDefault(); return; }
      draggedId = item.id; card.classList.add('dragging');
      if (event.dataTransfer) { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', item.id); }
    });
    card.addEventListener('dragend', cleanDrag); dropTarget(card, destination, item.id);
    const grip = element('span', 'editor-grip', '⠿'); grip.setAttribute('aria-hidden', 'true');
    pointerGrip(grip, card, item.id); card.append(grip);
    // Poster paths are server-generated upload/HTTPS locations; never interpolate HTML.
    if (typeof item.poster_url === 'string' && (/^https?:\/\//i.test(item.poster_url) || /^\/(?!\/)/.test(item.poster_url))) {
      const photo = element('img', 'editor-story-photo'); photo.src = item.poster_url; photo.alt = ''; photo.loading = 'lazy'; card.append(photo);
    }
    const copy = element('div', 'editor-story-copy');
    copy.append(element('span', 'editor-story-type', String(item.type || 'Story')), element('p', 'editor-story-title', String(item.title || 'Untitled submission')));
    card.append(copy);
    const actions = element('div', 'editor-story-actions'), select = element('select');
    select.setAttribute('aria-label', 'Move ' + (item.title || 'story') + ' to');
    for (const value of ['lead', ...Array.from({length: Number(control('columns').value)}, (_, i) => String(i + 1))]) {
      const option = element('option', '', value === 'lead' ? 'Lead story' : 'Column ' + value); option.value = value; select.append(option);
    }
    select.value = String(destination); select.disabled = busy() || items().length < 2;
    const moveKey = item.id + ':move'; focusTargets.set(moveKey, select);
    select.addEventListener('change', () => move(item.id, select.value, null, moveKey)); actions.append(select);
    for (const [direction, label, delta] of [['up', 'Move up', -1], ['down', 'Move down', 1]]) {
      const button = element('button', '', direction === 'up' ? '↑' : '↓'); button.type = 'button';
      button.setAttribute('aria-label', label + ': ' + (item.title || 'story'));
      button.disabled = busy() || destination === 'lead' || (delta < 0 ? index === 0 : index === total - 1);
      const key = item.id + ':' + direction; focusTargets.set(key, button);
      button.addEventListener('click', () => reorder(item.id, delta, key)); actions.append(button);
    }
    card.append(actions); return card;
  }
  function renderCanvas() {
    const canvas = $('newsletter-layout-canvas'); canvas.replaceChildren(); focusTargets = new Map(); dragElements = [];
    if (!design) return;
    const list = items(), byId = new Map(list.map(item => [item.id, item]));
    if (!list.length) { canvas.append(element('p', 'muted', 'Approved stories will appear here. You can save fonts, colours and columns now.')); return; }
    const layout = arrangement();
    function zone(title, destination, ids, className = '') {
      const node = element('section', 'editor-zone' + className); node.setAttribute('aria-label', title);
      node.setAttribute('data-destination', String(destination));
      node.append(element('h4', 'editor-zone-header', title)); dropTarget(node, destination);
      ids.forEach((id, index) => node.append(storyCard(byId.get(id), destination, index, ids.length)));
      if (!ids.length) node.append(element('p', 'editor-zone-empty', 'Drop a story here, or use its Move menu.'));
      return node;
    }
    canvas.append(zone('Lead story', 'lead', layout.lead ? [layout.lead] : [], ' editor-zone-lead'));
    const columns = element('div', 'editor-columns');
    columns.style.gridTemplateColumns = 'repeat(' + layout.columns.length + ', minmax(0, 1fr))';
    columns.setAttribute('data-columns', String(layout.columns.length));
    layout.columns.forEach((ids, index) => columns.append(zone('Column ' + (index + 1), index + 1, ids)));
    canvas.append(columns);
  }
  function render() {
    $('newsletter-designer').hidden = !initialized;
    for (const radio of radios) { radio.checked = radio.value === selected && !(selected === 'custom' && ThemeLibrary.hasSelection()); radio.disabled = busy(); }
    $('newsletter-current').textContent = editable() ? designLabel(baseline(), baselineDesign()) : preferenceKnown ? designLabel(preference, preferenceDesign) : 'Not loaded';
    $('newsletter-scope').textContent = editable() ? 'Applies to this preview edition and future editions.'
      : issue ? 'This edition uses ' + labels[template(issue.newsletter_template)] + ' and its layout is fixed. Your choice applies to future editions only.'
      : 'Applies to future editions. Build a preview after saving your choice.';
    $('newsletter-customize').checked = !!design; $('newsletter-customize').disabled = busy() || selected === 'custom';
    $('newsletter-custom-fields').hidden = !design;
    const values = normalized(design || defaults[selected]);
    for (const field of fields) { control(field).value = String(values[field]); control(field).disabled = busy() || !design; }
    for (const field of colorFields) { const hex = $(control(field).id + '-value'); hex.value = values[field]; hex.textContent = values[field]; hex.disabled = busy() || !design; }
    $('newsletter-reset-design').disabled = busy() || !design;
    $('newsletter-theme-builder').hidden = selected !== 'custom';
    $('newsletter-lead-image-field').hidden = ['custom', 'spotlight', 'bulletin'].includes(selected);
    $('newsletter-columns-field').hidden = selected === 'bulletin';
    ThemeEditor.setDesign(values, busy() || !design);
    $('newsletter-layout-help').textContent = editable()
      ? 'Drag stories between the lead and columns, or use each story’s Move menu and arrow buttons. Save to apply this arrangement to the preview edition.'
      : 'Story arrangement is for this candidate preview only. Saving keeps fonts, colours and column settings for future editions, then resets the arrangement. Existing editions stay unchanged.';
    renderCanvas();
    $('newsletter-save').disabled = busy() || !dirty(); $('newsletter-preview').disabled = busy() || previewing;
    const hasChanges = initialized && !loading && dirty();
    $('newsletter-change-bar').hidden = !hasChanges;
    $('newsletter-dirty-marker').hidden = !hasChanges;
    $('newsletter-change-message').textContent = saving ? 'Saving template changes…'
      : 'Unsaved template changes. Save or discard them before building or publishing an edition.';
    $('newsletter-save-top').disabled = busy() || !dirty();
    $('newsletter-discard').disabled = busy() || !dirty();
    $('newsletter-discard-bottom').disabled = busy() || !dirty();
    ThemeLibrary.setDraft({template: selected, design: futureDesign(normalized(design)), locked: busy()});
    onStateChange();
  }
  function setIssue(value, source = null) {
    const oldIssue = issue, hadDirtyChoice = selected !== baseline() || signature(design) !== signature(baselineDesign(), baseline());
    const wasEditable = editable();
    const oldLayout = JSON.stringify([oldIssue?.newsletter_template, oldIssue?.newsletter_design, oldIssue?.layout_items, oldIssue?.layout_lead_id]);
    let changedApproved = false;
    if (!value && Array.isArray(source?.layout_items)) {
      changedApproved = JSON.stringify([approvedItems, approvedLead]) !== JSON.stringify([source.layout_items, source.layout_lead_id ?? null]);
      approvedItems = clone(source.layout_items); approvedLead = source.layout_lead_id ?? null; approvedRevision++;
    }
    issue = value ? clone(value) : null;
    const changedIssue = (oldIssue?.id ?? null) !== (issue?.id ?? null), changedLock = wasEditable !== editable();
    const changedLayout = oldLayout !== JSON.stringify([issue?.newsletter_template, issue?.newsletter_design, issue?.layout_items, issue?.layout_lead_id]);
    if (changedIssue || changedLock || changedLayout || changedApproved) { contextVersion++; clearPreview(); cleanDrag(); }
    if (!initialized || changedIssue || changedLock) {
      ThemeLibrary.clearSelection();
      restoreBaseline();
      if (initialized && changedIssue && hadDirtyChoice) status((issue ? 'A different edition was loaded.' : 'There is no current edition.') + ' The previous unsaved choice was cleared.');
      else if (initialized && changedLock && !editable() && issue) status('This edition can no longer change layout. Choose a template for future editions.');
      else if (initialized && (changedIssue || changedLock)) status(preferenceKnown ? savedStatus() : loading ? 'Loading the saved template…' : 'The saved template has not been loaded.');
    } else if (!hadDirtyChoice) restoreBaseline();
    else if (changedLayout || changedApproved) status(dirty() ? 'The saved layout or story list changed. Your design is still unsaved.' : savedStatus());
    render();
  }
  async function init(options = {}) {
    const loginEpoch = ++epoch, itemsRevision = approvedRevision;
    onStateChange = options.onStateChange || (() => {}); onSaved = options.onSaved || (async () => {});
    initialized = true; loading = true; saving = false; preferenceKnown = false;
    ThemeLibrary.reset();
    saveSerial++; clearPreview(); restoreBaseline(); status('Loading the saved template…'); render();
    void ThemeLibrary.load();
    try {
      const result = await Staff.api('/admin/api/newsletter-template'); if (loginEpoch !== epoch) return;
      defaults = {...presetDefaults, ...result.design_defaults};
      preference = template(result.template); preferenceDesign = clone(result.design ?? null); preferenceKnown = true;
      if (itemsRevision === approvedRevision) { approvedItems = Array.isArray(result.items) ? result.items : []; approvedLead = result.lead_id ?? null; }
      restoreBaseline(); status(savedStatus());
    } catch (error) {
      if (loginEpoch !== epoch || error.stale) return;
      status('Could not load the saved template. Choose a template and save to continue. ' + error.message);
    } finally { if (loginEpoch === epoch) { loading = false; render(); } }
  }
  async function preview() {
    if (busy() || previewing) return;
    const loginEpoch = epoch, request = ++previewSerial, version = contextVersion;
    const choice = selected, payload = {template: choice, design: normalized(design)};
    if (issue) payload.issue_id = issue.id;
    previewing = true; $('newsletter-preview-panel').hidden = false; clearFrame();
    $('newsletter-preview-meta').textContent = 'Preparing the ' + labels[choice] + ' preview…'; status('Preparing the ' + labels[choice] + ' preview…'); render();
    try {
      const result = await Staff.api('/admin/api/newsletter-template/preview', {method: 'POST', body: JSON.stringify(payload)});
      if (loginEpoch !== epoch || request !== previewSerial || version !== contextVersion || choice !== selected) return;
      $('newsletter-preview-panel').hidden = false;
      // A fresh visible browsing context prevents embedded WebKit from retaining
      // a zero-layout document. Its sandbox, title and styling remain intact.
      const previous = $('newsletter-preview-frame'), frame = previous.cloneNode(false);
      frame.removeAttribute('srcdoc'); previous.replaceWith(frame); frame.srcdoc = result.html;
      const count = Number(result.item_count) || 0;
      $('newsletter-preview-meta').textContent = 'Email preview · ' + labels[choice] + (design ? ' · Custom design' : '') + ' · ' + count + (count === 1 ? ' item' : ' items') + (result.source === 'edition' ? ' from this edition. The “Read online” link opens the saved web edition.' : ' from approved submissions');
      status(dirty() ? 'Preview only. Choose “Use this template” to save your selection.' : 'Preview of the saved template.');
    } catch (error) {
      if (loginEpoch === epoch && request === previewSerial && !error.stale) { $('newsletter-preview-panel').hidden = true; clearFrame(); $('newsletter-preview-meta').textContent = ''; status('Could not load the preview. ' + error.message); }
    } finally { if (loginEpoch === epoch && request === previewSerial) { previewing = false; render(); } }
  }
  function futureDesign(value) {
    if (!value) return null;
    const result = {...value}; delete result.lead_id; delete result.story_order; delete result.column_map; return result;
  }
  async function save() {
    if (busy() || !dirty()) return;
    const loginEpoch = epoch, request = ++saveSerial, version = contextVersion, wasEditable = editable();
    const choice = selected, fullDesign = normalized(design), payload = {template: choice, design: wasEditable ? fullDesign : futureDesign(fullDesign)};
    if (wasEditable) payload.issue_id = issue.id;
    const hadArrangement = !!fullDesign && (!!fullDesign.lead_id || fullDesign.story_order.length > 0 || Object.keys(fullDesign.column_map).length > 0);
    let committed = false; saving = true; clearPreview(); cleanDrag(); status('Saving ' + labels[choice] + '…'); render();
    try {
      const result = await Staff.api('/admin/api/newsletter-template', {method: 'PUT', body: JSON.stringify(payload)});
      if (loginEpoch !== epoch || request !== saveSerial) return;
      committed = true; preference = template(result.template); preferenceKnown = true;
      preferenceDesign = clone(Object.hasOwn(result, 'design') ? result.design : futureDesign(fullDesign));
      if (version === contextVersion && result.applied_to_issue && issue?.id === result.issue_id) issue = {...issue, newsletter_template: preference, newsletter_design: clone(Object.hasOwn(result, 'issue_design') ? result.issue_design : fullDesign)};
      restoreBaseline();
      status(labels[preference] + (result.applied_to_issue ? ' saved for the preview edition and future editions.' : ' saved for future editions. This edition is unchanged.') + (!wasEditable && hadArrangement ? ' The preview-only story arrangement has been reset.' : ''));
      render(); await onSaved(result);
    } catch (error) {
      if (loginEpoch !== epoch || request !== saveSerial || error.stale) return;
      status(committed ? 'Template saved, but the edition could not refresh. Refresh the page. ' + error.message : 'Could not save the template. Your selection is still unsaved. ' + error.message);
    } finally { if (loginEpoch === epoch && request === saveSerial) { saving = false; render(); } }
  }
  function reset() {
    epoch++; contextVersion++; saveSerial++; initialized = loading = saving = false;
    issue = null; preference = selected = 'classic'; preferenceKnown = false; preferenceDesign = design = null;
    approvedItems = []; approvedLead = null; approvedRevision++; defaults = {...presetDefaults};
    clearPreview(); cleanDrag(); ThemeEditor.reset(); ThemeLibrary.reset(); status(''); render(); onStateChange = () => {}; onSaved = async () => {};
  }
  for (const radio of radios) radio.addEventListener('change', () => {
    if (!radio.checked) return;
    if (busy()) { render(); return; }
    ThemeLibrary.clearSelection();
    selected = template(radio.value);
    if (selected === 'custom') design = normalized(design || defaults.custom);
    else design = selected === baseline() ? clone(baselineDesign()) : null;
    changed(labels[selected] + ' selected.');
  });
  $('newsletter-customize').addEventListener('change', () => {
    if (busy() || selected === 'custom') { render(); return; }
    design = $('newsletter-customize').checked ? normalized(defaults[selected]) : null;
    if (design) saveArrangement(arrangement());
    changed(design ? 'Custom design enabled.' : 'Preset design selected.');
  });
  $('newsletter-reset-design').addEventListener('click', () => {
    if (busy()) return;
    ThemeLibrary.clearSelection();
    design = selected === 'custom' ? normalized(defaults.custom) : null;
    ThemeEditor.reset(); changed(selected === 'custom' ? 'Custom theme reset to its starting layout.' : 'Preset design selected.');
  });
  for (const field of fields) {
    const node = control(field);
    node.addEventListener(colorFields.includes(field) ? 'input' : 'change', () => {
      if (busy() || !design) { render(); return; }
      const value = field === 'columns' ? Number(node.value) : node.value;
      design = normalized({...design, [field]: value}); changed('Custom design changed.');
    });
  }
  for (const field of colorFields) {
    const hex = $(control(field).id + '-value');
    const updateHex = final => {
      if (busy() || !design) { render(); return; }
      if (!/^#[\da-f]{6}$/i.test(hex.value)) {
        if (final) { render(); status('Use a six-digit hex colour, such as #9D3229. The previous colour has been kept.'); }
        return;
      }
      if (hex.value.toUpperCase() === normalized(design)[field]) return;
      design = normalized({...design, [field]: hex.value}); changed('Colour changed.');
    };
    hex.addEventListener('input', () => updateHex(false));
    hex.addEventListener('change', () => updateHex(true));
  }
  ThemeEditor.init($('newsletter-theme-builder'), (rows, message) => {
    if (busy() || selected !== 'custom' || !design) { render(); return; }
    design = normalized({...design, ...rows}); changed(message);
  });
  ThemeLibrary.init({host: $('newsletter-theme-library'), saveHost: $('newsletter-theme-save-panel'),
    signature: value => JSON.stringify(futureDesign(normalized(value, 'custom'))),
    onStateChange: () => render(),
    onSelect: theme => {
      if (busy()) return;
      const previous = normalized(design || defaults[selected]);
      selected = 'custom';
      design = normalized({...theme.design, lead_id: previous.lead_id, story_order: previous.story_order, column_map: previous.column_map});
      changed('“' + theme.name + '” selected.');
    },
  });
  $('newsletter-preview').addEventListener('click', preview); $('newsletter-save').addEventListener('click', save);
  $('newsletter-save-top').addEventListener('click', save);
  $('newsletter-discard').addEventListener('click', discard);
  $('newsletter-discard-bottom').addEventListener('click', discard);
  return {init, setIssue, reset, blocksPublish};
})();
