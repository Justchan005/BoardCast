// BoardCast Assistant: chat for club reps and Student Affairs on every staff page.
// The assistant only prepares actions. Every button here calls the same API as the review desk,
// which checks the role, the submission version and the AI red-flag acknowledgement again.
(() => {
  if (typeof Staff === 'undefined') return;   // a top-level const in staff.js, not a window property
  const {api, el} = Staff;
  const KEY = 'boardcast.assistant:';
  const MAX_SEND = 16;
  let me = null, info = null, state = {messages: [], open: false}, busy = false, typingTimer = null;
  let launcher, badge, panel, log, input, sendBtn, subline, dot;

  // ---------------------------------------------------------------- icons (static markup only)
  const ICONS = {
    spark: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    reset: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
    send: '<path d="M5 12h13M13 6l6 6-6 6"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 8v5M12 16.5v.5"/><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    doc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    arrow: '<path d="M9 6l6 6-6 6"/>',
    shield: '<path d="m12 3 8 3v5c0 5-4 8-8 10-4-2-8-5-8-10V6z"/><path d="m9 12 2 2 4-4"/>',
  };
  function icon(name, size = 18, stroke = 2) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', size); svg.setAttribute('height', size);
    svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', stroke);
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round'); svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = ICONS[name];
    return svg;
  }

  // ---------------------------------------------------------------- formatting
  const isAffairs = () => me?.role === 'student_affairs';
  function niceDate(value, withYear = false) {
    const d = new Date(String(value).slice(0, 10) + 'T12:00:00');
    if (Number.isNaN(d.valueOf())) return String(value);
    return d.toLocaleDateString('en-GB', {weekday: 'short', day: 'numeric', month: 'short', ...(withYear ? {year: 'numeric'} : {})});
  }
  function niceTime(value) {
    const m = /^(\d{1,2}):(\d{2})/.exec(String(value || ''));
    if (!m) return '';
    const h = Number(m[1]);
    return (h % 12 || 12) + ':' + m[2] + (h < 12 ? ' am' : ' pm');
  }
  function whenLine(row) {
    const parts = [];
    if (row.date) parts.push((row.type === 'announcement' ? 'Closes ' : '') + niceDate(row.date));
    if (row.start_time) parts.push(niceTime(row.start_time));
    if (row.venue) parts.push(row.venue);
    return parts.join(' · ');
  }
  function plural(n, word, many) { return n + ' ' + (n === 1 ? word : (many || word + 's')); }

  // Markdown-lite: **bold**, _italic_, `code`, bullet lists and in-app paths. Built as DOM nodes, never HTML.
  const PATH = /^\/(?:submit|subscribe|admin(?:\/(?:club|insights|trace\/[A-Za-z0-9_-]+))?|m\/[A-Za-z0-9_-]+|demo(?:\/outbox)?)$/;
  function inline(text) {
    const nodes = [];
    const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`|(?<![A-Za-z0-9])_[^_\n]+_(?![A-Za-z0-9])|(?<![*\w])\*[^*\n]+\*(?![*\w])|(?<![\w.\/])\/(?:submit|subscribe|admin(?:\/(?:club|insights|trace\/[A-Za-z0-9_-]+))?|m\/[A-Za-z0-9_-]+|demo(?:\/outbox)?)(?![\w\/]))/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) nodes.push(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      if (tok.startsWith('**')) nodes.push(el('strong', {}, tok.slice(2, -2)));
      else if (tok.startsWith('`')) {
        const body = tok.slice(1, -1);
        nodes.push(PATH.test(body) ? el('a', {href: body}, el('code', {}, body)) : el('code', {}, body));
      } else if (tok.startsWith('_') || tok.startsWith('*')) nodes.push(el('em', {}, tok.slice(1, -1)));
      else nodes.push(el('a', {href: tok}, tok));
      last = m.index + tok.length;
    }
    if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
    return nodes;
  }
  function markdown(text) {
    const box = el('div', {class: 'as-text'});
    let list = null, para = [];
    const flush = () => { if (para.length) { box.append(el('p', {}, ...inline(para.join(' ')))); para = []; } };
    for (const raw of String(text || '').split('\n')) {
      const line = raw.trim();
      const bullet = /^(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (bullet) {
        flush();
        const ordered = /^\d/.test(line);
        if (!list || list.ordered !== ordered) { list = el(ordered ? 'ol' : 'ul'); list.ordered = ordered; box.append(list); }
        list.append(el('li', {}, ...inline(bullet[1])));
      } else if (!line) { flush(); list = null; }
      else { list = null; para.push(line); }
    }
    flush();
    return box;
  }

  // ---------------------------------------------------------------- persistence (per viewer, per tab)
  function save() {
    if (!me) return;
    try {
      sessionStorage.setItem(KEY + me.email, JSON.stringify({messages: state.messages.slice(-40), open: state.open}));
    } catch (_) {}
  }
  function restore() {
    try { const saved = JSON.parse(sessionStorage.getItem(KEY + me.email)); if (saved?.messages) state = saved; }
    catch (_) { state = {messages: [], open: false}; }
  }
  function changed() { window.dispatchEvent(new CustomEvent('boardcast:data-changed')); refreshInfo(); }

  // ---------------------------------------------------------------- shell
  function build() {
    if (launcher) return;
    document.body.classList.add('has-assistant');
    badge = el('span', {class: 'as-launcher-badge', hidden: ''});
    launcher = el('button', {type: 'button', class: 'as-launcher', 'aria-expanded': 'false', 'aria-controls': 'as-panel',
                             onclick: () => toggle(true)},
      icon('spark', 20), el('span', {class: 'as-launcher-label'}, 'Ask BoardCast'), badge);
    dot = el('span', {class: 'as-dot'});
    subline = el('span', {}, 'Connecting…');
    input = el('textarea', {class: 'as-input', rows: '1', maxlength: '2000', placeholder: 'Ask anything about BoardCast…',
                            'aria-label': 'Message BoardCast Assistant'});
    sendBtn = el('button', {type: 'submit', class: 'as-send', 'aria-label': 'Send message', disabled: ''}, icon('send', 18, 2.2));
    const form = el('form', {class: 'as-composer'},
      el('div', {class: 'as-input-wrap'}, input, sendBtn),
      el('p', {class: 'as-foot'}, icon('shield', 13), 'Answers come from live BoardCast records. Actions always need your confirmation.'));
    form.addEventListener('submit', ev => { ev.preventDefault(); send(input.value); });
    input.addEventListener('input', () => { autosize(); sendBtn.disabled = busy || !input.value.trim(); });
    input.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send(input.value); }
    });
    log = el('div', {class: 'as-log', role: 'log', 'aria-live': 'polite', 'aria-relevant': 'additions'});
    panel = el('section', {id: 'as-panel', class: 'as-panel', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': 'as-title', hidden: ''},
      el('header', {class: 'as-head'},
        el('span', {class: 'as-avatar'}, icon('spark', 19)),
        el('div', {class: 'as-title'}, el('h2', {id: 'as-title'}, 'BoardCast Assistant'), el('div', {class: 'as-sub'}, dot, subline)),
        el('button', {type: 'button', class: 'as-icon-btn', title: 'New conversation', 'aria-label': 'Start a new conversation', onclick: reset}, icon('reset', 18)),
        el('button', {type: 'button', class: 'as-icon-btn', title: 'Close', 'aria-label': 'Close assistant', onclick: () => toggle(false)}, icon('close', 18))),
      log, form);
    panel.addEventListener('keydown', ev => { if (ev.key === 'Escape') { ev.stopPropagation(); toggle(false); } });
    document.body.append(launcher, panel);
  }
  function autosize() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 140) + 'px'; }
  function toggle(open) {
    state.open = open; save();
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', String(open));
    if (open) { renderLog(); setTimeout(() => input.focus(), 30); }
    else launcher.focus();
  }
  function reset() {
    if (busy) return;
    state.messages = []; save(); renderLog(); refreshInfo(); input.focus();
  }
  function updateHeader() {
    const role = isAffairs() ? 'Student Affairs' : 'Club rep' + (me?.club_id ? ' · ' + me.club_id : '');
    const ai = info?.ai;
    dot.className = 'as-dot' + (ai ? '' : ' quick');
    subline.textContent = (ai ? 'Gemini agent' : 'Quick answers mode') + ' · ' + role;
    const s = info?.snapshot || {};
    const count = isAffairs() ? s.waiting_for_review : (s.needs_fixing_drafts || 0) + (s.returned_to_club || 0);
    badge.hidden = !count;
    badge.textContent = count > 99 ? '99+' : String(count || '');
    launcher.setAttribute('aria-label', 'Ask BoardCast' + (count ? ' (' + count + (isAffairs() ? ' waiting for review)' : ' need your attention)') : ''));
  }
  async function refreshInfo() {
    if (!me) return;
    const who = me.email;
    try { const res = await api('/admin/api/assistant/context'); if (me?.email !== who) return; info = res; }
    catch (_) { return; }
    updateHeader();
    if (!panel.hidden && !state.messages.length) renderLog();
  }

  // ---------------------------------------------------------------- log
  function scrollEnd() { requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; }); }
  function welcome() {
    const s = info?.snapshot || {};
    const hour = new Date().getHours();
    const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
    const tiles = isAffairs()
      ? [[s.waiting_for_review, 'waiting for review', s.waiting_for_review], [s.ai_flagged, 'flagged by AI', s.ai_flagged],
         [s.approved_for_next_edition, 'approved for next edition', 0]]
      : [[s.waiting_for_review, 'waiting for review', 0], [s.needs_fixing_drafts, 'need fixing', s.needs_fixing_drafts],
         [s.returned_to_club, 'returned', s.returned_to_club]];
    return el('div', {class: 'as-welcome'},
      el('p', {class: 'as-hello'}, greet + ', ', el('span', {}, 'how can I help?')),
      el('p', {}, isAffairs()
        ? 'I can count and list submissions, explain AI pre-review flags, prepare approvals for you to confirm, and check the weekly edition.'
        : 'I can track your club’s submissions, explain why one was returned, tell you what’s required, and show how students responded.'),
      info ? el('div', {class: 'as-snapshot'}, ...tiles.map(([n, label, warn]) =>
        el('div', {class: 'as-snap' + (warn ? ' warn' : '')}, el('b', {}, String(n ?? 0)), el('span', {}, label)))) : null,
      el('p', {class: 'as-try'}, 'Try asking'),
      el('div', {class: 'as-starters'}, ...(info?.starters || []).map(text =>
        el('button', {type: 'button', class: 'as-starter', onclick: () => send(text)}, icon('arrow', 16), text))));
  }
  function renderLog() {
    log.replaceChildren();
    if (!state.messages.length) { log.append(welcome()); log.scrollTop = 0; return; }
    state.messages.forEach((m, i) => log.append(messageNode(m, i === state.messages.length - 1)));
    scrollEnd();
  }
  function messageNode(m, isLast) {
    if (m.role === 'user') return el('div', {class: 'as-msg as-user'}, el('div', {class: 'as-bubble'}, m.text));
    const body = el('div', {class: 'as-bot-body'});
    if (m.steps?.length) body.append(el('div', {class: 'as-steps', 'aria-label': 'What the assistant checked'},
      ...dedupeSteps(m.steps).map(s => el('span', {class: 'as-step' + (s.ok ? '' : ' fail')}, icon(s.ok ? 'check' : 'alert', 12, 2.6), s.label))));
    if (m.error) {
      body.append(el('div', {class: 'as-text as-error'}, m.text,
        isLast ? el('button', {type: 'button', class: 'as-btn ghost sm', onclick: retry}, 'Try again') : null));
    } else body.append(markdown(m.text));
    for (const card of m.cards || []) { const node = renderCard(card); if (node) body.append(node); }
    if (m.mode === 'quick' && !info?.ai) body.append(el('p', {class: 'as-mode-note'}, 'Quick answers mode: Gemini is not configured on this server.'));
    const wrap = el('div', {class: 'as-msg'}, el('div', {class: 'as-bot'}, el('span', {class: 'as-avatar small'}, icon('spark', 14)), body));
    if (isLast && m.suggestions?.length && !m.error) wrap.append(el('div', {class: 'as-suggest'},
      ...m.suggestions.map(text => el('button', {type: 'button', class: 'as-chip', onclick: () => send(text)}, text))));
    return wrap;
  }
  function dedupeSteps(steps) {
    const seen = new Map();
    for (const s of steps) { const prev = seen.get(s.label); seen.set(s.label, {label: s.label, ok: (prev ? prev.ok : true) && s.ok}); }
    return [...seen.values()];
  }
  function appendMessage(m) {
    log.querySelector('.as-welcome')?.remove();
    log.querySelectorAll('.as-suggest').forEach(n => n.remove());
    log.querySelectorAll('.as-error button').forEach(n => n.remove());
    const node = messageNode(m, true);
    log.append(node);
    // A new answer opens at its first line; the person reads down into its cards.
    if (m.role === 'assistant') requestAnimationFrame(() => { log.scrollTop = Math.max(0, node.offsetTop - log.offsetTop - 12); });
    else scrollEnd();
  }
  function showTyping() {
    const hints = isAffairs()
      ? ['Checking the review queue…', 'Reading the AI pre-review briefs…', 'Putting your answer together…']
      : ['Looking up your club’s submissions…', 'Checking BoardCast records…', 'Putting your answer together…'];
    const label = el('span', {}, 'Thinking…');
    const node = el('div', {class: 'as-msg as-typing-row'}, el('div', {class: 'as-bot'}, el('span', {class: 'as-avatar small'}, icon('spark', 14)),
      el('div', {class: 'as-typing', role: 'status'}, el('span', {class: 'as-dots', 'aria-hidden': 'true'}, el('i'), el('i'), el('i')), label)));
    log.append(node); scrollEnd();
    let i = 0;
    typingTimer = setInterval(() => { label.textContent = hints[i++ % hints.length]; }, 1700);
    return node;
  }

  // ---------------------------------------------------------------- talking to the API
  function setBusy(value) { busy = value; sendBtn.disabled = busy || !input.value.trim(); input.setAttribute('aria-busy', String(busy)); }
  async function send(text) {
    text = String(text || '').trim();
    if (!text || busy || !me) return;
    if (panel.hidden) toggle(true);
    input.value = ''; autosize();
    const m = {role: 'user', text: text.slice(0, 2000)};
    state.messages.push(m); save(); appendMessage(m);
    await request();
  }
  async function retry() {
    if (busy) return;
    if (state.messages.at(-1)?.error) { state.messages.pop(); save(); renderLog(); }
    await request();
  }
  async function request() {
    const who = me.email;
    setBusy(true);
    const typing = showTyping();
    const history = state.messages.filter(x => !x.error && x.text)
      .slice(-MAX_SEND).map(x => ({role: x.role, text: x.text.slice(0, 4000)}));
    let reply;
    try {
      const res = await api('/admin/api/assistant/chat', {method: 'POST', body: JSON.stringify({messages: history})});
      reply = {role: 'assistant', text: res.reply, cards: res.cards || [], steps: res.steps || [], mode: res.mode,
               suggestions: res.suggestions || []};
    } catch (e) {
      if (e.stale || me?.email !== who) return;
      reply = {role: 'assistant', error: true, text: e.message || 'Something went wrong. Please try again.'};
    } finally {
      clearInterval(typingTimer); typing.remove(); setBusy(false);
    }
    state.messages.push(reply); save(); appendMessage(reply);
    if (!reply.error && (reply.cards || []).some(c => ['stats', 'queue'].includes(c.type))) refreshInfo();
  }
  const decide = (row, decision, note, ack) => api('/admin/api/items/' + encodeURIComponent(row.id) + '/decision', {
    method: 'POST', body: JSON.stringify({decision, note: note || null, expected_updated_at: row.updated_at,
                                          acknowledge_flags: !!ack, channel: 'assistant'})});

  // ---------------------------------------------------------------- cards
  function renderCard(card) {
    try {
      return ({stats: statsCard, queue: queueCard, item: itemCard, decision: decisionCard, bulk: bulkCard,
               edition: editionCard, events: eventsCard, engagement: engagementCard}[card.type] || (() => null))(card);
    } catch (e) { console.warn('assistant card failed', card.type, e); return null; }
  }
  function head(title, extra) { return el('div', {class: 'as-card-head'}, el('h3', {}, title), extra || null); }
  function thumb(row) {
    if (row.poster_url) return el('img', {class: 'as-thumb', src: row.poster_url, alt: '', loading: 'lazy'});
    return el('span', {class: 'as-thumb', 'aria-hidden': 'true'}, icon('doc', 18, 1.6));
  }
  function aiBadge(row) {
    const b = row.brief;
    if (row.status !== 'pending') return null;
    if (!b) return el('span', {class: 'as-badge'}, 'No AI brief');
    if (b.status === 'running') return el('span', {class: 'as-badge info'}, 'AI pre-review running');
    if (b.status !== 'done') return el('span', {class: 'as-badge'}, 'AI brief unavailable');
    if (!b.current) return el('span', {class: 'as-badge'}, 'AI brief out of date');
    return b.recommendation === 'needs_attention'
      ? el('span', {class: 'as-badge warn'}, icon('alert', 12, 2.4), 'AI: ' + plural((b.flags || []).length || 1, 'point') + ' to check')
      : el('span', {class: 'as-badge ok'}, icon('check', 12, 2.6), 'AI: looks ready');
  }
  function statusBadge(row) {
    const tone = {pending: 'info', approved: 'ok', published: 'ok', rejected: 'bad', draft: 'warn'}[row.status] || '';
    return el('span', {class: 'as-badge ' + tone}, row.status_label || row.status);
  }
  function resultBanner(result) {
    const tone = result.tone || 'ok';
    return el('div', {class: 'as-result ' + tone, role: 'status'}, icon(tone === 'ok' ? 'check' : 'alert', 18, 2.4), el('span', {}, result.text));
  }

  function statsCard(card) {
    const node = el('div', {class: 'as-card'}, head(card.title, card.subtitle ? el('small', {}, card.subtitle) : null),
      el('div', {class: 'as-card-body'},
        el('div', {class: 'as-tiles'}, ...(card.tiles || []).map(t =>
          el('div', {class: 'as-tile ' + (t.tone || '')}, el('b', {}, String(t.value)), el('span', {}, t.label)))),
        card.note ? el('p', {class: 'as-note'}, card.note) : null));
    if (card.actions?.length) node.append(el('div', {class: 'as-card-actions'}, ...card.actions.map(a => a.kind === 'link'
      ? el('a', {class: 'as-btn ghost sm', href: a.href}, a.label)
      : el('button', {type: 'button', class: 'as-btn sm', onclick: () => send(a.prompt)}, a.label))));
    return node;
  }

  // One row in a list, with inline Approve / Return for Student Affairs and Correct for clubs.
  function rowNode(row, {canDecide, card}) {
    const li = el('li', {class: 'as-row'});
    li.append(el('div', {class: 'as-row-main'}, thumb(row),
      el('div', {},
        el('p', {class: 'as-row-title'}, row.title),
        el('p', {class: 'as-row-meta'}, [row.kind, row.organiser, whenLine(row)].filter(Boolean).join(' · ')),
        el('div', {class: 'as-badges'}, statusBadge(row), aiBadge(row),
          row.issues?.length ? el('span', {class: 'as-badge warn'}, plural(row.issues.length, 'problem') + ' to fix') : null),
        row.status === 'rejected' && row.rejection_reason ? el('p', {class: 'as-callout bad'}, 'Reason: ' + row.rejection_reason) : null)));
    const done = card.results?.[row.id];
    if (done) { li.append(resultBanner(done)); return li; }
    const actions = el('div', {class: 'as-row-actions'});
    if (canDecide && row.status === 'pending') {
      const open = decision => {
        li.querySelector('.as-inline')?.remove();
        const panelNode = decisionPanel(row, decision, {
          flags: decision === 'approve' ? row.brief?.flags || [] : [], problems: row.issues || [],
          onCancel: () => { panelNode.remove(); actions.hidden = false; },
          onDone: result => { card.results = {...(card.results || {}), [row.id]: result}; save(); panelNode.remove(); li.append(resultBanner(result)); },
        });
        panelNode.classList.add('as-inline');
        actions.hidden = true; li.append(panelNode);
        panelNode.querySelector('textarea, input, button')?.focus();
      };
      actions.append(el('button', {type: 'button', class: 'as-btn sm', onclick: () => open('approve')}, 'Approve'),
                     el('button', {type: 'button', class: 'as-btn danger sm', onclick: () => open('reject')}, 'Return'));
    }
    if (!isAffairs() && ['draft', 'rejected', 'pending'].includes(row.status))
      actions.append(el('a', {class: 'as-btn ghost sm', href: '/submit?item=' + encodeURIComponent(row.id)}, row.status === 'pending' ? 'Edit' : 'Correct'));
    actions.append(el('button', {type: 'button', class: 'as-btn ghost sm', onclick: () => send('Show me the details of “' + row.title + '” (id ' + row.id + ')')}, 'Details'));
    if (isAffairs()) actions.append(el('a', {class: 'as-btn ghost sm', href: '/admin?item=' + encodeURIComponent(row.id)}, 'Open in desk'));
    li.append(actions);
    return li;
  }
  function queueCard(card) {
    const rows = card.items || [];
    const node = el('div', {class: 'as-card'}, head(card.title, el('span', {class: 'as-count'}, String(card.total ?? rows.length))));
    if (!rows.length) { node.append(el('div', {class: 'as-card-body muted'}, 'Nothing here right now.')); return node; }
    node.append(el('ul', {class: 'as-rows'}, ...rows.map(row => rowNode(row, {canDecide: card.can_decide && isAffairs(), card}))));
    if ((card.total || 0) > rows.length) node.append(el('p', {class: 'as-more'}, 'Showing ' + rows.length + ' of ' + card.total + '. ',
      el('a', {href: isAffairs() ? '/admin' : '/admin/club'}, isAffairs() ? 'Open the review desk' : 'Open your club report')));
    return node;
  }

  function itemCard(card) {
    const row = card.item, d = row.data || {};
    const facts = [['When', whenLine(row) || (row.date ? niceDate(row.date, true) : '')], ['Fee', row.fee], ['Organiser', row.organiser],
                   ['Team', d.team], ['Competition', d.competition], ['Result', d.result], ['From', row.submitted_by]]
      .filter(([, v]) => v);
    const body = el('div', {class: 'as-card-body'});
    body.append(el('div', {class: 'as-item' + (row.poster_url ? '' : ' no-poster')},
      row.poster_url ? el('a', {href: row.poster_url, target: '_blank', rel: 'noopener', 'aria-label': 'Open poster'}, el('img', {src: row.poster_url, alt: 'Poster for ' + row.title})) : null,
      el('div', {}, el('p', {class: 'as-row-title'}, row.title),
        el('div', {class: 'as-badges'}, statusBadge(row), aiBadge(row)),
        el('dl', {class: 'as-dl'}, ...facts.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, String(v))])))));
    const text = row.description || d.summary;
    if (text) body.append(el('p', {class: 'as-note'}, text));
    if (row.issues?.length) body.append(el('div', {class: 'as-callout warn'}, el('strong', {}, 'Fix before approval'), el('ul', {}, ...row.issues.map(p => el('li', {}, p)))));
    if (row.status === 'rejected' && row.rejection_reason) body.append(el('div', {class: 'as-callout bad'}, el('strong', {}, 'Returned because'), row.rejection_reason));
    const b = row.brief;
    if (b?.status === 'done' && b.current) body.append(b.recommendation === 'needs_attention'
      ? el('div', {class: 'as-callout warn'}, el('strong', {}, 'AI pre-review: check these'), el('ul', {}, ...(b.flags || []).map(f => el('li', {}, f))))
      : el('div', {class: 'as-callout ok'}, el('strong', {}, 'AI pre-review: looks ready'), b.headline || ''));
    const node = el('div', {class: 'as-card'}, head(row.kind + ' · ' + (row.status_label || row.status)), body);
    const done = card.result;
    if (done) { node.append(resultBanner(done)); return node; }
    const actions = el('div', {class: 'as-card-actions'});
    if (card.can_decide && isAffairs()) {
      const open = decision => {
        const p = decisionPanel(row, decision, {flags: decision === 'approve' ? b?.flags || [] : [], problems: row.issues || [],
          onCancel: () => { p.remove(); actions.hidden = false; },
          onDone: result => { card.result = result; save(); p.remove(); node.append(resultBanner(result)); }});
        p.classList.add('as-card-body'); actions.hidden = true; node.append(p);
        p.querySelector('textarea, input, button')?.focus();
      };
      actions.append(el('button', {type: 'button', class: 'as-btn sm', onclick: () => open('approve')}, 'Approve'),
                     el('button', {type: 'button', class: 'as-btn danger sm', onclick: () => open('reject')}, 'Return'));
    }
    if (card.can_correct && (!isAffairs() || row.status !== 'pending'))
      actions.append(el('a', {class: 'as-btn ghost sm', href: '/submit?item=' + encodeURIComponent(row.id)}, 'Correct submission'));
    actions.append(el('a', {class: 'as-btn ghost sm', href: '/admin/trace/' + encodeURIComponent(row.id)}, 'Run trace'));
    node.append(actions);
    return node;
  }

  // The confirmation step shared by inline rows, item cards and decision cards.
  function decisionPanel(row, decision, {flags = [], problems = [], reason = '', onDone, onCancel}) {
    const box = el('div', {});
    const error = el('p', {class: 'as-callout bad', role: 'alert', hidden: ''});
    if (decision === 'approve' && problems.length) {
      box.append(el('div', {class: 'as-callout warn'}, el('strong', {}, 'Approval is blocked until the club fixes:'), el('ul', {}, ...problems.map(p => el('li', {}, p)))));
      if (onCancel) box.append(el('div', {class: 'row', style: 'margin-top:10px'}, el('button', {type: 'button', class: 'as-btn ghost sm', onclick: onCancel}, 'Close')));
      return box;
    }
    let ack = null, note = null;
    if (decision === 'approve' && flags.length) {
      box.append(el('div', {class: 'as-callout warn'}, el('strong', {}, 'The AI pre-review asked you to check:'), el('ul', {}, ...flags.map(f => el('li', {}, f)))));
      ack = el('input', {type: 'checkbox'});
      box.append(el('label', {class: 'as-ack'}, ack, el('span', {}, 'I have checked these points and still want to approve.')));
    }
    if (decision === 'reject') {
      note = el('textarea', {class: 'as-reason', maxlength: '2000', required: '', placeholder: 'Tell the club what to correct. This is emailed to the uploader.'});
      note.value = reason || '';
      box.append(el('label', {class: 'as-field-label'}, 'Reason for the club', note));
    }
    const go = el('button', {type: 'button', class: 'as-btn sm ' + (decision === 'approve' ? '' : 'solid-danger')},
      decision === 'approve' ? 'Approve for publication' : 'Return and notify club');
    go.onclick = async () => {
      error.hidden = true;
      if (ack && !ack.checked) { error.textContent = 'Tick the confirmation after checking the AI pre-review points.'; error.hidden = false; ack.focus(); return; }
      if (note && !note.value.trim()) { error.textContent = 'Add a reason so the club knows what to correct.'; error.hidden = false; note.focus(); return; }
      go.disabled = true; go.textContent = decision === 'approve' ? 'Approving…' : 'Returning…';
      try {
        const res = await decide(row, decision, note?.value.trim(), ack?.checked);
        onDone(decision === 'approve'
          ? {tone: 'ok', text: 'Approved: ' + row.title + '. It will be in the next Campus Weekly.' + (res.ai_points_acknowledged ? ' Your check of the AI points is recorded in the run trace.' : '')}
          : res.warning ? {tone: 'warn', text: res.warning}
          : {tone: 'ok', text: 'Returned to the club. They were emailed your reason.'});
        changed();
      } catch (e) {
        if (e.stale) return;
        error.textContent = e.message; error.hidden = false;
        go.disabled = false; go.textContent = decision === 'approve' ? 'Approve for publication' : 'Return and notify club';
      }
    };
    box.append(error, el('div', {class: 'row', style: 'margin-top:10px;gap:8px'}, go,
      onCancel ? el('button', {type: 'button', class: 'as-btn ghost sm', onclick: onCancel}, 'Cancel') : null));
    return box;
  }

  function decisionCard(card) {
    const row = card.item, approve = card.decision === 'approve';
    const node = el('div', {class: 'as-card as-confirm' + (approve ? '' : ' reject')},
      head(approve ? 'Confirm approval' : 'Confirm return to club', el('small', {}, 'Needs your OK')));
    node.append(el('div', {class: 'as-card-body'}, el('div', {class: 'as-row-main'}, thumb(row),
      el('div', {}, el('p', {class: 'as-row-title'}, row.title),
        el('p', {class: 'as-row-meta'}, [row.kind, row.organiser, whenLine(row)].filter(Boolean).join(' · ')),
        el('div', {class: 'as-badges'}, statusBadge(row), aiBadge(row))))));
    if (card.result) { node.append(resultBanner(card.result)); return node; }
    const p = decisionPanel(row, card.decision, {flags: card.flags || [], problems: card.problems || [], reason: card.reason,
      onDone: result => { card.result = result; save(); p.replaceWith(resultBanner(result)); },
      onCancel: () => { card.result = {tone: 'warn', text: 'Cancelled. Nothing was changed.'}; save(); p.replaceWith(resultBanner(card.result)); }});
    p.classList.add('as-card-body'); p.style.paddingTop = '0';
    node.append(p);
    return node;
  }

  function bulkCard(card) {
    const ready = card.ready || [], excluded = card.excluded || [];
    card.results = card.results || {};
    const node = el('div', {class: 'as-card as-confirm'}, head('Approve in one go', el('small', {}, 'Needs your OK')));
    const list = el('div', {});
    const boxes = [];
    for (const row of ready) {
      const state = card.results[row.id];
      const cb = el('input', {type: 'checkbox', 'aria-label': 'Include ' + row.title});
      cb.checked = !state; cb.disabled = !!state;
      const status = el('span', {class: 'as-row-state ' + (state ? (state.ok ? 'ok' : 'bad') : '')}, state ? (state.ok ? 'Approved' : 'Not approved') : '');
      boxes.push({row, cb, status});
      list.append(el('label', {class: 'as-check-row'}, cb, thumb(row),
        el('span', {}, el('span', {class: 'as-row-title', style: 'display:block'}, row.title),
          el('span', {class: 'as-row-meta', style: 'display:block'}, [row.organiser, whenLine(row)].filter(Boolean).join(' · ')),
          state && !state.ok ? el('span', {class: 'as-row-meta as-error', style: 'display:block'}, state.error) : null),
        status));
    }
    if (!ready.length) list.append(el('div', {class: 'as-card-body muted'}, 'Nothing is ready for a one-click approval.'));
    node.append(list);
    if (excluded.length) {
      node.append(el('div', {class: 'as-subhead'}, 'Review on their own (' + excluded.length + ')'));
      for (const row of excluded) node.append(el('div', {class: 'as-check-row as-excluded'}, el('span', {}, ''), thumb(row),
        el('span', {}, el('span', {class: 'as-row-title', style: 'display:block'}, row.title),
          el('span', {class: 'as-row-meta', style: 'display:block'}, row.excluded_because),
          el('button', {type: 'button', class: 'as-btn ghost sm', style: 'margin-top:6px', onclick: () => send('Show me the details of “' + row.title + '” (id ' + row.id + ')')}, 'Review this one'))));
    }
    const pending = boxes.filter(b => !card.results[b.row.id]);
    if (!pending.length) {
      const ok = Object.values(card.results).filter(r => r.ok).length;
      if (ready.length) node.append(resultBanner({tone: ok === ready.length ? 'ok' : 'warn', text: plural(ok, 'submission') + ' approved for the next Campus Weekly.'}));
      return node;
    }
    const go = el('button', {type: 'button', class: 'as-btn sm'});
    const label = () => { const n = pending.filter(b => b.cb.checked).length; go.textContent = 'Approve ' + plural(n, 'selected', 'selected'); go.disabled = !n; };
    pending.forEach(b => b.cb.addEventListener('change', label)); label();
    const actions = el('div', {class: 'as-card-actions', style: 'padding-top:12px;border-top:1px solid var(--as-line)'}, go,
      el('span', {class: 'as-note', style: 'margin:0;align-self:center'}, 'Each one is re-checked by the server.'));
    go.onclick = async () => {
      const chosen = pending.filter(b => b.cb.checked);
      go.disabled = true; pending.forEach(b => { b.cb.disabled = true; });
      let ok = 0;
      for (const b of chosen) {
        b.status.className = 'as-row-state busy'; b.status.textContent = 'Approving…';
        try { await decide(b.row, 'approve'); ok++; card.results[b.row.id] = {ok: true}; b.status.className = 'as-row-state ok'; b.status.textContent = 'Approved'; }
        catch (e) { if (e.stale) return; card.results[b.row.id] = {ok: false, error: e.message}; b.status.className = 'as-row-state bad'; b.status.textContent = 'Not approved'; b.status.title = e.message; }
      }
      save();
      const failed = chosen.length - ok;
      actions.replaceWith(resultBanner({tone: failed ? 'warn' : 'ok', text: plural(ok, 'submission') + ' approved for the next Campus Weekly.' +
        (failed ? ' ' + failed + ' could not be approved; open them to see why.' : '')}));
      if (ok) changed();
    };
    node.append(actions);
    return node;
  }

  function editionCard(card) {
    const issue = card.issue;
    const node = el('div', {class: 'as-card'}, head('Campus Weekly', issue ? statusBadge({status: issue.status === 'sent' ? 'published' : 'pending', status_label: {preview: 'Preview', sent: 'Sent', held: 'On hold', partial: 'Partly sent', sending: 'Sending'}[issue.status] || issue.status}) : null));
    const tiles = [];
    if (issue) tiles.push({label: 'Week of', value: niceDate(issue.week_start, false)}, {label: 'Items in edition', value: issue.items});
    tiles.push({label: 'Approved for next', value: card.approved_waiting, tone: card.approved_waiting ? 'ok' : ''});
    if (issue?.delivery?.sent) tiles.push({label: 'Emails sent', value: issue.delivery.sent});
    const body = el('div', {class: 'as-card-body'}, el('div', {class: 'as-tiles'}, ...tiles.map(t => el('div', {class: 'as-tile ' + (t.tone || '')}, el('b', {}, String(t.value)), el('span', {}, t.label)))));
    if (card.approved_titles?.length) body.append(el('p', {class: 'as-note'}, 'Next up: ' + card.approved_titles.join(' · ')));
    if (!issue) body.append(el('p', {class: 'as-note'}, 'No edition has been built yet.'));
    node.append(body);
    if (card.result) { node.append(resultBanner(card.result)); return node; }
    const actions = el('div', {class: 'as-card-actions'});
    const finish = result => { card.result = result; save(); actions.replaceWith(resultBanner(result)); changed(); };
    const fail = (button, text) => e => { if (e.stale) return; button.disabled = false; button.textContent = text; actions.append(el('p', {class: 'as-callout bad', style: 'width:100%'}, e.message)); };
    if (issue) actions.append(el('a', {class: 'as-btn ghost sm', href: issue.magazine_url, target: '_blank', rel: 'noopener'}, 'Read edition'));
    if (card.can_build) {
      const b = el('button', {type: 'button', class: 'as-btn ghost sm'}, issue?.status === 'preview' ? 'Rebuild preview' : 'Build preview');
      b.onclick = async () => { b.disabled = true; b.textContent = 'Building…';
        try { await api('/admin/api/demo/preview', {method: 'POST'}); finish({tone: 'ok', text: 'Preview built from the approved submissions. Ask me about the edition again to read or publish it.'}); }
        catch (e) { fail(b, 'Build preview')(e); } };
      actions.append(b);
    }
    if (card.can_publish) {
      const b = el('button', {type: 'button', class: 'as-btn sm'}, 'Publish to outbox');
      let armed = false;
      b.onclick = async () => {
        if (!armed) { armed = true; b.textContent = 'Confirm: publish now'; b.classList.add('solid-danger'); return; }
        b.disabled = true; b.textContent = 'Publishing…';
        try { const r = await api('/admin/api/demo/publish', {method: 'POST'}); finish(r.sent ? {tone: 'ok', text: plural(r.sent, 'email') + ' saved to the outbox.'} : {tone: 'warn', text: r.reason || 'No new email was delivered.'}); }
        catch (e) { fail(b, 'Publish to outbox')(e); }
      };
      actions.append(b);
    }
    if (card.can_hold && issue) {
      const b = el('button', {type: 'button', class: 'as-btn danger sm'}, 'Hold edition');
      let armed = false;
      b.onclick = async () => {
        if (!armed) { armed = true; b.textContent = 'Confirm: hold delivery'; b.classList.replace('danger', 'solid-danger'); return; }
        b.disabled = true; b.textContent = 'Holding…';
        try { await api('/admin/api/issues/' + encodeURIComponent(issue.id) + '/hold', {method: 'POST'}); finish({tone: 'warn', text: 'Edition held. No further emails will be sent.'}); }
        catch (e) { fail(b, 'Hold edition')(e); }
      };
      actions.append(b);
    }
    node.append(actions);
    return node;
  }

  function eventsCard(card) {
    const rows = card.items || [];
    const node = el('div', {class: 'as-card'}, head(card.title, el('span', {class: 'as-count'}, String(card.total ?? rows.length))));
    if (!rows.length) { node.append(el('div', {class: 'as-card-body muted'}, 'No approved events in this window.')); return node; }
    node.append(el('ul', {class: 'as-rows'}, ...rows.map(row => {
      const d = new Date(String(row.date).slice(0, 10) + 'T12:00:00');
      return el('li', {class: 'as-row'}, el('div', {class: 'as-row-main'},
        el('div', {class: 'as-date', 'aria-hidden': 'true'}, el('b', {}, String(d.getDate())), el('span', {}, d.toLocaleDateString('en-GB', {month: 'short'}))),
        el('div', {}, el('p', {class: 'as-row-title'}, row.title),
          el('p', {class: 'as-row-meta'}, [niceDate(row.date), niceTime(row.start_time), row.venue, row.fee].filter(Boolean).join(' · ')),
          el('p', {class: 'as-row-meta'}, row.organiser))));
    })));
    return node;
  }

  function engagementCard(card) {
    const t = card.totals || {};
    const node = el('div', {class: 'as-card'}, head(card.title),
      el('div', {class: 'as-card-body'}, el('div', {class: 'as-tiles', style: 'grid-template-columns:repeat(3,minmax(0,1fr))'},
        ...[['Register', t.register], ['Calendar', t.calendar], ['Reminders', t.saved]].map(([k, v]) =>
          el('div', {class: 'as-tile accent'}, el('b', {}, String(v ?? 0)), el('span', {}, k))))));
    const rows = (card.rows || []).filter(r => r.register + r.calendar + r.saved > 0);
    const max = Math.max(1, ...rows.map(r => r.register + r.calendar + r.saved));
    if (rows.length) node.append(el('div', {}, ...rows.map(r => el('div', {class: 'as-bar-row'},
      el('div', {class: 'as-bar-top'}, el('span', {}, r.title || 'Untitled'), el('span', {}, r.register + ' reg · ' + r.calendar + ' cal · ' + r.saved + ' rem')),
      el('div', {class: 'as-bar'}, el('i', {style: 'width:' + Math.round(100 * (r.register + r.calendar + r.saved) / max) + '%'}))))));
    else node.append(el('p', {class: 'as-note', style: 'padding:0 14px 14px;margin:0'}, 'No student clicks yet. Counts appear after Campus Weekly is sent.'));
    return node;
  }

  // ---------------------------------------------------------------- sign-in lifecycle
  function start(who) {
    if (me?.email === who.email && launcher && !launcher.hidden) return;
    me = who; info = null; state = {messages: [], open: false};
    build(); restore();
    document.body.classList.add('has-assistant');
    launcher.hidden = false; updateHeader();
    panel.hidden = !state.open; launcher.setAttribute('aria-expanded', String(state.open));
    if (state.open) renderLog();
    refreshInfo();
  }
  function stop() {
    if (me) { try { sessionStorage.removeItem(KEY + me.email); } catch (_) {} }
    me = null; info = null; state = {messages: [], open: false};
    if (launcher) { launcher.hidden = true; panel.hidden = true; log.replaceChildren(); document.body.classList.remove('has-assistant'); }
  }
  window.addEventListener('boardcast:signin', ev => start(ev.detail));
  window.addEventListener('boardcast:signout', stop);
  if (Staff.current()) start(Staff.current());
  window.BoardCastAssistant = {ask: text => send(text), open: () => launcher && toggle(true)};
})();
