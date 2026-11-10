// Named reusable themes. Library writes never apply a draft to an edition.
const ThemeLibrary = (() => {
  const clone = value => value == null ? null : JSON.parse(JSON.stringify(value));
  const fonts = {georgia: 'Georgia, serif', times: '"Times New Roman", serif', arial: 'Arial, sans-serif', verdana: 'Verdana, sans-serif'};
  let host, saveHost, hooks = {}, themes = [], source = null, name = '', message = '', loadError = '';
  let draft = {template: 'classic', design: null, locked: true}, initialized = false, loading = false, writing = false;
  let epoch = 0, loadSerial = 0, writeSerial = 0, selectionSerial = 0;
  const element = (tag, className, text) => {
    const node = document.createElement(tag); if (className) node.className = className;
    if (text !== undefined) node.textContent = text; return node;
  };
  const appearance = design => hooks.signature ? hooks.signature(design) : JSON.stringify(design);
  const matching = design => themes.filter(theme => appearance(theme.design) === appearance(design));
  const validTheme = value => value && typeof value.id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.id)
    && typeof value.name === 'string' && value.name.trim() && [...value.name].length <= 60
    && value.design && typeof value.design === 'object' && !Array.isArray(value.design)
    && Number.isInteger(value.revision) && value.revision > 0;
  const sortThemes = () => themes.sort((a, b) => a.name.localeCompare(b.name));
  const locked = () => draft.locked || writing;
  const sourceIsCurrent = () => source && themes.find(theme => theme.id === source.id)?.revision === source.revision;
  function notify() { render(); hooks.onStateChange?.(); }
  function reset() {
    epoch++; loadSerial++; writeSerial++; selectionSerial++;
    themes = []; source = null; name = ''; message = ''; loadError = ''; loading = writing = initialized = false;
    render();
  }
  function clearSelection() { selectionSerial++; source = null; name = ''; message = ''; }
  function syncSelection(template, design, preserveSource = false) {
    if (preserveSource && source && template === 'custom') return;
    clearSelection();
    if (template !== 'custom') return;
    const matches = matching(design);
    if (matches.length === 1) { source = clone(matches[0]); name = source.name; }
  }
  function label(template, design) {
    if (template !== 'custom') return null;
    const matches = matching(design); return matches.length === 1 ? matches[0].name : null;
  }
  async function load() {
    if (writing) return;
    const session = epoch, request = ++loadSerial;
    initialized = true; loading = true; loadError = ''; render();
    try {
      const result = await Staff.api('/admin/api/newsletter-themes');
      if (session !== epoch || request !== loadSerial) return;
      themes = (Array.isArray(result.themes) ? result.themes : []).filter(validTheme).map(clone); sortThemes();
      // A reload must not silently advance the revision used to update a selected theme.
      if (!source && !name && draft.template === 'custom') syncSelection(draft.template, draft.design);
    } catch (error) {
      if (session !== epoch || request !== loadSerial || error.stale) return;
      loadError = 'Could not load saved themes. ' + error.message;
    } finally { if (session === epoch && request === loadSerial) { loading = false; notify(); } }
  }
  function choose(theme) {
    if (locked()) return;
    selectionSerial++; source = clone(theme); name = theme.name; message = '';
    hooks.onSelect?.(clone(theme));
  }
  function cleanName(value) { return String(value || '').normalize('NFKC').trim().replace(/\s+/gu, ' '); }
  function nameProblem() {
    if (/[\u0000-\u001f\u007f-\u009f]/u.test(name)) return 'Use a theme name without control characters.';
    const value = cleanName(name);
    if (!value) return 'Enter a name for this theme.';
    if ([...value].length > 60) return 'Keep the theme name to 60 characters or fewer.';
    return null;
  }
  async function persist(mode) {
    if (locked() || draft.template !== 'custom' || !draft.design || (mode !== 'create' && !source)) return;
    const problem = nameProblem(); if (problem) { message = problem; render(); return; }
    if (mode !== 'create' && !sourceIsCurrent()) {
      message = 'This saved theme has changed. Select its card again to load the latest version, or save your draft as a new theme.'; render(); return;
    }
    const session = epoch, request = ++writeSerial, selection = selectionSerial;
    const selectedSource = clone(source), capturedDesign = clone(draft.design), draftSignature = appearance(capturedDesign);
    const payload = {name: cleanName(name)};
    if (mode !== 'rename') payload.design = capturedDesign;
    if (mode !== 'create') payload.revision = selectedSource.revision;
    const route = '/admin/api/newsletter-themes' + (mode === 'create' ? '' : '/' + encodeURIComponent(selectedSource.id));
    writing = true; message = mode === 'rename' ? 'Renaming the saved theme…' : 'Saving the theme to your library…'; notify();
    try {
      const result = await Staff.api(route, {method: mode === 'create' ? 'POST' : 'PUT', body: JSON.stringify(payload)});
      if (session !== epoch || request !== writeSerial) return;
      if (!validTheme(result.theme)) throw new Error('The saved theme could not be read. Reload the library before trying again.');
      // A GET started before this write cannot replace the newer theme list.
      loadSerial++; loading = false;
      themes = themes.filter(theme => theme.id !== result.theme.id); themes.push(clone(result.theme)); sortThemes();
      if (selection === selectionSerial && draft.template === 'custom' && appearance(draft.design) === draftSignature) {
        source = clone(result.theme); name = source.name;
      }
      message = '“' + result.theme.name + '” ' + (mode === 'rename' ? 'renamed.' : mode === 'update' ? 'updated in your saved themes.' : 'saved to your themes.')
        + ' Use “Use this template” to apply a design to your edition.';
      loadError = '';
    } catch (error) {
      if (session !== epoch || request !== writeSerial || error.stale) return;
      message = 'Could not ' + (mode === 'rename' ? 'rename' : 'save') + ' the theme. Your design is still here. ' + error.message;
    } finally { if (session === epoch && request === writeSerial) { writing = false; notify(); } }
  }
  function action(text, id, onClick, disabled = locked()) {
    const button = element('button', 'btn secondary', text); button.type = 'button'; button.id = id; button.disabled = disabled;
    button.addEventListener('click', onClick); return button;
  }
  function render() {
    if (!host || !saveHost) return;
    host.hidden = !initialized; host.replaceChildren();
    const heading = element('div', 'theme-library-heading'), words = element('div');
    words.append(element('h3', '', 'Saved themes'), element('p', '', 'Your reusable designs, ready for the next edition.'));
    heading.append(words, action(loading ? 'Loading…' : loadError ? 'Retry saved themes' : 'Reload saved themes', 'newsletter-themes-reload', load, loading || writing)); host.append(heading);
    if (loadError) { const error = element('p', 'theme-library-status', loadError); error.setAttribute('role', 'status'); host.append(error); }
    if (!themes.length) host.append(element('p', 'theme-library-empty', loading ? 'Loading saved themes…' : 'Choose Custom, make a design, then save it with a name below.'));
    else {
      const grid = element('div', 'theme-library-grid');
      for (const theme of themes) {
        const card = element('button', 'theme-library-card'); card.type = 'button'; card.disabled = locked();
        card.setAttribute('aria-label', 'Choose saved theme: ' + theme.name);
        card.setAttribute('data-saved-theme-id', theme.id);
        card.setAttribute('aria-pressed', String(draft.template === 'custom' && source?.id === theme.id));
        const swatch = element('span', 'theme-library-swatch', 'Campus Weekly'); swatch.setAttribute('aria-hidden', 'true');
        if (/^#[\da-f]{6}$/i.test(theme.design.paper_color || '')) swatch.style.backgroundColor = theme.design.paper_color;
        if (/^#[\da-f]{6}$/i.test(theme.design.text_color || '')) swatch.style.color = theme.design.text_color;
        if (/^#[\da-f]{6}$/i.test(theme.design.accent_color || '')) swatch.style.borderColor = theme.design.accent_color;
        swatch.style.fontFamily = fonts[theme.design.headline_font] || fonts.arial;
        card.append(swatch, element('span', 'theme-library-title', theme.name), element('span', 'theme-library-meta', 'Custom theme · ' + ([1, 2, 3].includes(theme.design.columns) ? theme.design.columns : 1) + ' story column' + (theme.design.columns === 1 ? '' : 's')));
        card.addEventListener('click', () => choose(theme)); grid.append(card);
      }
      host.append(grid);
    }
    saveHost.hidden = draft.template !== 'custom'; saveHost.replaceChildren();
    const saveHeading = element('div', 'theme-save-heading');
    saveHeading.append(element('h3', '', source ? 'Save this theme' : 'Keep this design for next time'), element('p', '', source ? 'Editing “' + source.name + '”. Save a copy, update its design, or rename it.' : 'Give your custom theme a name to add it to the template list.'));
    saveHost.append(saveHeading);
    const form = element('div', 'theme-save-form'), inputLabel = element('label', '', 'Theme name');
    inputLabel.setAttribute('for', 'newsletter-theme-name');
    const input = element('input'); input.id = 'newsletter-theme-name'; input.type = 'text'; input.maxLength = 60;
    input.value = name; input.placeholder = 'e.g. Campus Spotlight'; input.disabled = locked(); input.autocomplete = 'off';
    input.addEventListener('input', () => { name = input.value; }); inputLabel.append(input); form.append(inputLabel);
    const actions = element('div', 'theme-save-actions');
    actions.append(action('Save as new theme', 'newsletter-theme-create', () => persist('create')));
    if (source) actions.append(action('Update saved theme', 'newsletter-theme-update', () => persist('update')),
      action('Rename theme', 'newsletter-theme-rename', () => persist('rename')));
    form.append(actions); saveHost.append(form);
    saveHost.append(element('p', 'theme-save-note', 'Saves fonts, colours and placeholder layouts. Story selections and already-published editions stay unchanged.'));
    if (source && !sourceIsCurrent()) saveHost.append(element('p', 'theme-save-note', 'This saved theme has changed. Select its card to load the latest version before updating it.'));
    const feedback = element('p', 'theme-save-status', message); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite'); saveHost.append(feedback);
  }
  function setDraft(value) { draft = {...value, design: clone(value.design)}; render(); }
  function init(options) { host = options.host; saveHost = options.saveHost; hooks = options; }
  return {init, load, reset, setDraft, syncSelection, clearSelection, label, isSaving: () => writing, hasSelection: () => !!source};
})();
