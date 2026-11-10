// Reusable newsletter structure, made from safe content placeholders.
const ThemeEditor = (() => {
  const blocks = {
    name: {label: 'Newsletter title', sample: 'Campus Weekly'},
    date: {label: 'Edition date', sample: 'Monday, 16 November · Published by BoardCast'},
    intro: {label: 'Introduction', sample: 'A short introduction to this edition goes here.'},
    title: {label: 'Story title', sample: 'Your next campus story'},
    picture: {label: 'Picture', sample: 'Club poster'},
    details: {label: 'Story details', sample: 'The approved description, event details and useful links go here.'},
  };
  const required = {header: ['name', 'date', 'intro'], story: ['title', 'picture', 'details']};
  const defaults = {
    header: [{cells: [['name']]}, {cells: [['date', 'intro']]}],
    story: [{cells: [['picture'], ['title', 'details']]}],
  };
  const fonts = {georgia: 'Georgia, "Times New Roman", serif', times: '"Times New Roman", Times, serif', arial: 'Arial, Helvetica, sans-serif', verdana: 'Verdana, Geneva, sans-serif'};
  const clone = value => JSON.parse(JSON.stringify(value));
  function normalizeRows(value, section) {
    if (!Array.isArray(value) || value.length < 1 || value.length > 6) return clone(defaults[section]);
    const seen = [];
    for (const row of value) {
      if (!row || !Array.isArray(row.cells) || ![1, 2].includes(row.cells.length)) return clone(defaults[section]);
      for (const cell of row.cells) {
        if (!Array.isArray(cell) || cell.length > 3 || cell.some(id => !required[section].includes(id))) return clone(defaults[section]);
        seen.push(...cell);
      }
    }
    if (seen.length !== 3 || new Set(seen).size !== 3) return clone(defaults[section]);
    return value.map(row => ({cells: row.cells.map(cell => [...cell])}));
  }
  let container, onChange = () => {}, settings = {}, section = 'header', disabled = false;
  let dragged = null, pointer = null, dragNodes = [], focusTargets = new Map();
  const rows = () => settings[section + '_rows'];
  const element = (tag, className, text) => {
    const node = document.createElement(tag); if (className) node.className = className;
    if (text !== undefined) node.textContent = text; return node;
  };
  function button(text, label, action, locked = disabled) {
    const node = element('button', '', text); node.type = 'button'; node.disabled = locked;
    if (label) node.setAttribute('aria-label', label);
    node.addEventListener('click', action); return node;
  }
  function cleanDrag() {
    dragged = null;
    if (pointer) {
      const active = pointer; pointer = null;
      if (active.grip.hasPointerCapture?.(active.id)) active.grip.releasePointerCapture(active.id);
    }
    for (const node of dragNodes) { node.classList.remove('dragging'); node.classList.remove('drag-over'); }
  }
  function changed(message, focusKey) {
    cleanDrag();
    // Callback updates the parent draft and synchronously re-renders this editor.
    onChange({header_rows: clone(settings.header_rows), story_rows: clone(settings.story_rows)}, message);
    if (focusKey) focusTargets.get(focusKey)?.focus();
  }
  function locate(id) {
    for (let row = 0; row < rows().length; row++) {
      for (let cell = 0; cell < rows()[row].cells.length; cell++) {
        const at = rows()[row].cells[cell].indexOf(id);
        if (at >= 0) return {row, cell, at};
      }
    }
    return null;
  }
  function move(id, destination, before = null) {
    if (disabled || !required[section].includes(id) || id === before) return;
    const [row, cell] = String(destination).split(':').map(Number), source = locate(id);
    const target = rows()[row]?.cells[cell];
    if (!source || !target || (source.row === row && source.cell === cell && !before && source.at === target.length - 1)) return;
    rows()[source.row].cells[source.cell].splice(source.at, 1);
    const at = before ? target.indexOf(before) : -1;
    target.splice(at < 0 ? target.length : at, 0, id);
    changed(blocks[id].label + ' moved.', id + ':move');
  }
  function reorder(id, direction) {
    if (disabled) return;
    const source = locate(id); if (!source) return;
    const cell = rows()[source.row].cells[source.cell], at = source.at + direction;
    if (at < 0 || at >= cell.length) return;
    [cell[source.at], cell[at]] = [cell[at], cell[source.at]];
    changed(blocks[id].label + ' reordered.', id + ':move');
  }
  function destinationAt(event) {
    const hit = document.elementFromPoint(event.clientX, event.clientY), cell = hit?.closest('[data-theme-cell]');
    if (!cell || !container.contains(cell)) return null;
    const block = hit.closest('[data-theme-block]');
    return {node: block || cell, destination: cell.getAttribute('data-theme-cell'), before: block?.getAttribute('data-theme-block') || null};
  }
  function bindGrip(grip, card, id) {
    grip.style.touchAction = 'none'; grip.draggable = false;
    grip.addEventListener('pointerdown', event => {
      if (disabled || event.button > 0 || event.isPrimary === false) return;
      event.preventDefault(); event.stopPropagation(); cleanDrag();
      pointer = {id: event.pointerId, grip, card, x: event.clientX, y: event.clientY, active: false};
      grip.setPointerCapture?.(event.pointerId);
    });
    grip.addEventListener('pointermove', event => {
      if (!pointer || pointer.id !== event.pointerId) return;
      if (!pointer.active && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 4) return;
      pointer.active = true; dragged = id; card.classList.add('dragging'); event.preventDefault();
      for (const node of dragNodes) node.classList.remove('drag-over');
      const target = destinationAt(event); if (target?.before !== id) target?.node.classList.add('drag-over');
    });
    grip.addEventListener('pointerup', event => {
      if (!pointer || pointer.id !== event.pointerId) return;
      const target = pointer.active ? destinationAt(event) : null;
      event.preventDefault(); cleanDrag(); if (target) move(id, target.destination, target.before);
    });
    grip.addEventListener('pointercancel', cleanDrag); grip.addEventListener('lostpointercapture', cleanDrag);
  }
  function dropTarget(node, destination, before = null) {
    dragNodes.push(node);
    node.addEventListener('dragover', event => {
      if (disabled || !dragged) return;
      event.preventDefault(); event.stopPropagation(); node.classList.add('drag-over');
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });
    node.addEventListener('dragleave', event => { if (!node.contains(event.relatedTarget)) node.classList.remove('drag-over'); });
    node.addEventListener('drop', event => {
      if (disabled || !dragged) return;
      event.preventDefault(); event.stopPropagation(); const id = dragged; cleanDrag(); move(id, destination, before);
    });
  }
  const cellLabel = (row, cell) => 'Row ' + (row + 1) + (rows()[row].cells.length === 1 ? ' · Full width' : cell === 0 ? ' · Left' : ' · Right');
  function blockCard(id, row, cell, at) {
    const card = element('article', 'theme-block'); card.setAttribute('data-theme-block', id); card.draggable = !disabled;
    card.addEventListener('dragstart', event => {
      if (disabled) { event.preventDefault(); return; }
      dragged = id; card.classList.add('dragging');
      if (event.dataTransfer) { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', id); }
    });
    card.addEventListener('dragend', cleanDrag); dropTarget(card, row + ':' + cell, id);
    const top = element('div', 'theme-block-heading'), grip = element('span', 'theme-block-grip', '⠿');
    grip.setAttribute('aria-hidden', 'true'); bindGrip(grip, card, id);
    top.append(grip, element('span', 'theme-block-label', blocks[id].label)); card.append(top);
    const preview = element('div', 'theme-block-preview theme-placeholder-' + id, blocks[id].sample);
    preview.style.fontFamily = fonts[['name', 'title'].includes(id) ? settings.headline_font : settings.body_font] || fonts.arial;
    card.append(preview);
    const actions = element('div', 'theme-block-actions'), select = element('select');
    select.setAttribute('aria-label', 'Move ' + blocks[id].label + ' to'); select.disabled = disabled;
    rows().forEach((value, r) => value.cells.forEach((_, c) => {
      const option = element('option', '', cellLabel(r, c)); option.value = r + ':' + c; select.append(option);
    }));
    select.value = row + ':' + cell; focusTargets.set(id + ':move', select);
    select.addEventListener('change', () => move(id, select.value)); actions.append(select);
    actions.append(button('↑', 'Move up: ' + blocks[id].label, () => reorder(id, -1), disabled || at === 0),
      button('↓', 'Move down: ' + blocks[id].label, () => reorder(id, 1), disabled || at === rows()[row].cells[cell].length - 1));
    card.append(actions); return card;
  }
  function rowCard(value, index) {
    const row = element('section', 'theme-row'); row.setAttribute('aria-label', (section === 'header' ? 'Header' : 'Story') + ' row ' + (index + 1));
    const toolbar = element('div', 'theme-row-toolbar'); toolbar.append(element('strong', '', 'Row ' + (index + 1)));
    const select = element('select'); select.setAttribute('aria-label', 'Columns in ' + section + ' row ' + (index + 1)); select.disabled = disabled;
    for (const [columns, label] of [[1, 'Full width'], [2, 'Two columns']]) { const option = element('option', '', label); option.value = String(columns); select.append(option); }
    select.value = String(value.cells.length);
    select.addEventListener('change', () => {
      if (disabled) return;
      const count = Number(select.value); if (![1, 2].includes(count) || count === value.cells.length) return;
      value.cells = count === 1 ? [value.cells.flat()] : [value.cells[0], []];
      changed('Theme row layout changed.');
    });
    toolbar.append(select);
    for (const [delta, arrow] of [[-1, '↑'], [1, '↓']]) toolbar.append(button(arrow, 'Move ' + section + ' row ' + (index + 1) + (delta < 0 ? ' up' : ' down'), () => {
      if (disabled) return; const at = index + delta; if (at < 0 || at >= rows().length) return;
      [rows()[index], rows()[at]] = [rows()[at], rows()[index]]; changed('Theme rows reordered.');
    }, disabled || (delta < 0 ? index === 0 : index === rows().length - 1)));
    const remove = button('Remove', 'Remove empty ' + section + ' row ' + (index + 1), () => {
      if (disabled || rows().length === 1 || value.cells.flat().length) return;
      rows().splice(index, 1); changed('Empty theme row removed.');
    }, disabled || rows().length === 1 || value.cells.flat().length > 0);
    remove.title = 'Move all placeholders out of a row before removing it.'; toolbar.append(remove); row.append(toolbar);
    const cells = element('div', 'theme-cells'); cells.setAttribute('data-columns', String(value.cells.length));
    value.cells.forEach((ids, cell) => {
      const zone = element('div', 'theme-cell'); zone.setAttribute('data-theme-cell', index + ':' + cell);
      zone.setAttribute('aria-label', cellLabel(index, cell));
      zone.append(element('div', 'theme-cell-label', value.cells.length === 1 ? 'Full width' : cell === 0 ? 'Left' : 'Right'));
      dropTarget(zone, index + ':' + cell);
      ids.forEach((id, at) => zone.append(blockCard(id, index, cell, at)));
      if (!ids.length) zone.append(element('p', 'theme-cell-empty', 'Drop a placeholder here'));
      cells.append(zone);
    });
    row.append(cells); return row;
  }
  function render() {
    if (!container) return;
    cleanDrag(); container.replaceChildren(); dragNodes = []; focusTargets = new Map();
    const editor = element('div', 'theme-editor');
    const heading = element('div', 'theme-editor-heading');
    heading.append(element('p', 'eyebrow', 'CUSTOM THEME'), element('h3', '', 'Build your own layout'), element('p', '', 'Move the placeholders to decide where the title, picture and details appear. Your theme is reused in every edition.'));
    editor.append(heading);
    const tabs = element('div', 'theme-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Theme sections');
    for (const [id, label] of [['header', 'Newsletter header'], ['story', 'Story layout']]) {
      const tab = button(label, null, () => { if (disabled || section === id) return; section = id; render(); focusTargets.get('tab:' + id)?.focus(); });
      tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(section === id)); tab.className = section === id ? 'is-active' : '';
      focusTargets.set('tab:' + id, tab); tabs.append(tab);
    }
    editor.append(tabs);
    const toolbar = element('div', 'theme-toolbar'); toolbar.append(element('p', '', section === 'header' ? 'Shown once at the top of the newsletter.' : 'Used for every story. A picture appears only when the club allows its poster.'));
    toolbar.append(button('+ Add row', 'Add ' + section + ' row', () => {
      if (disabled || rows().length >= 6) return;
      rows().push({cells: [[]]}); changed('Theme row added.');
    }, disabled || rows().length >= 6)); editor.append(toolbar);
    const canvas = element('div', 'theme-canvas'); canvas.setAttribute('role', 'tabpanel'); canvas.setAttribute('aria-label', section === 'header' ? 'Newsletter header placeholders' : 'Story layout placeholders');
    const safeColor = (value, fallback) => /^#[\da-f]{6}$/i.test(value || '') ? value : fallback;
    canvas.style.backgroundColor = safeColor(settings.paper_color, '#FFFFFF'); canvas.style.color = safeColor(settings.text_color, '#1B1B1B');
    canvas.style.borderColor = safeColor(settings.accent_color, '#0B7BB5');
    canvas.style.setProperty('--theme-paper', safeColor(settings.paper_color, '#FFFFFF'));
    canvas.style.setProperty('--theme-text', safeColor(settings.text_color, '#1B1B1B'));
    canvas.style.setProperty('--theme-accent', safeColor(settings.accent_color, '#0B7BB5'));
    rows().forEach((row, index) => canvas.append(rowCard(row, index))); editor.append(canvas);
    editor.append(element('p', 'theme-editor-note', 'Drag with the ⠿ handle, or use Move and the arrow buttons. Two-column rows stack from left to right on mobile.'));
    container.append(editor);
  }
  function init(node, change) { container = node; onChange = change; }
  function setDesign(design, locked = false) {
    settings = {...(design || {}), header_rows: normalizeRows(design?.header_rows, 'header'), story_rows: normalizeRows(design?.story_rows, 'story')};
    disabled = locked; render();
  }
  function reset() { cleanDrag(); section = 'header'; }
  return {init, setDesign, reset, normalizeRows};
})();
