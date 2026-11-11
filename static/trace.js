(() => {
  const {api, el} = Staff;
  const $ = id => document.getElementById(id);
  const itemId = document.querySelector('main').dataset.itemId;
  const KIND = {model:'Model', tool:'Tool', code:'Code', human:'Human', system:'System'};
  const NAMES = {
    poster_uploaded:'Poster uploaded', extract:'Gemini read the poster', not_a_poster:'Not a poster', qr_decode:'QR codes decoded',
    required_fields:'Required fields checked', manual_entry_fallback:'Fell back to manual entry', submitted:'Submitted for review',
    corrected:'Corrected and resubmitted', submission_checks:'Submission checks', prereview_started:'Pre-review agent started',
    verify_poster:'Gemini re-read the poster (second reader)', verify_against_poster:'Tool: compare with the poster',
    find_similar_submissions:'Tool: duplicate search', check_links:'Tool: link check', finish:'Tool: finish',
    prereview_rules:'Fixed rules applied to the brief', prereview_saved:'Brief saved for Student Affairs',
    prereview_unavailable:'Pre-review unavailable', approved:'Approved', rejected:'Returned to the club',
    choose_spotlight:'Gemini chose the spotlight event', published:'Published in Campus Weekly'};
  let me, polling = null, loadSeq = 0;
  function notice(text, kind = 'ok') { $('notice').textContent = text; $('notice').className = 'alert ' + kind; $('notice').hidden = !text; }
  function stepName(name) { const turn = /^prereview_turn_(\d+)$/.exec(name); return turn ? 'Agent turn ' + turn[1] + ': the model chose its next action' : (NAMES[name] || name.replace(/_/g, ' ')); }
  function tile(label, value, hint) { return el('div', {class:'trace-tile'}, el('span', {class:'trace-tile-value'}, String(value)), el('span', {class:'trace-tile-label'}, label), hint ? el('small', {class:'muted'}, hint) : null); }
  function fmt(value) { return value == null ? '' : typeof value === 'object' ? JSON.stringify(value, null, 1) : String(value); }
  function renderTotals(data) {
    const t = data.totals, box = $('totals'); box.replaceChildren();
    box.append(tile('Steps', t.steps), tile('Model calls', t.model_calls), tile('Tool calls', t.tool_calls), tile('Code checks', t.code_checks), tile('Human steps', t.human_decisions));
    box.append(tile('Tokens in / out', t.tokens_in == null ? 'Not reported' : t.tokens_in.toLocaleString() + ' / ' + t.tokens_out.toLocaleString(), t.model_ms ? (t.model_ms / 1000).toFixed(1) + ' s of model time' : null));
    box.append(tile('Estimated model cost', t.estimated_cost_usd == null ? 'Not configured' : 'US$' + t.estimated_cost_usd.toFixed(4), data.prices_configured ? 'From the configured token prices' : 'Set GEMINI_PRICE_* to estimate'));
    const e = data.engagement; box.append(tile('Student clicks', e.register + e.calendar, e.register + ' register · ' + e.calendar + ' calendar · ' + e.saved + ' reminders'));
    if (t.failed_steps) box.append(tile('Failed steps', t.failed_steps, 'Each one is shown below with what happened next'));
  }
  function renderSteps(steps) {
    const list = $('steps'); list.replaceChildren(); $('empty').hidden = steps.length > 0;
    steps.forEach((step, index) => {
      const meta = [new Date(step.ts).toLocaleString('en-GB', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit', second:'2-digit'}), step.actor, step.prompt, step.model,
        step.tokens_in == null && step.tokens_out == null ? null : (step.tokens_in || 0) + ' in / ' + (step.tokens_out || 0) + ' out tokens',
        step.ms == null ? null : step.ms + ' ms', step.issue_id ? 'edition ' + step.issue_id : null].filter(Boolean);
      const body = el('div', {class:'trace-step-body'},
        el('div', {class:'trace-step-title'}, el('span', {class:'kind kind-' + step.kind}, KIND[step.kind] || step.kind), el('strong', {}, stepName(step.name)), step.ok ? null : el('span', {class:'pill rejected'}, 'Failed')),
        el('p', {class:'trace-step-summary'}, step.summary), el('p', {class:'muted trace-step-meta'}, meta.join(' · ')));
      const entries = Object.entries(step.detail || {});
      if (entries.length) {
        const fields = el('dl', {class:'fields trace-detail'});
        for (const [key, value] of entries) fields.append(el('dt', {}, key.replace(/_/g, ' ')), el('dd', {}, typeof value === 'object' && value !== null ? el('pre', {}, fmt(value)) : fmt(value)));
        body.append(el('details', {}, el('summary', {}, 'Details'), fields));
      }
      list.append(el('li', {class:'trace-step' + (step.ok ? '' : ' trace-step-failed')}, el('span', {class:'trace-step-number', 'aria-hidden':'true'}, String(index + 1)), body));
    });
  }
  async function runAgain(button) {
    button.disabled = true; notice('The agent is running its checks. This usually takes a few seconds.');
    try { await api('/admin/api/items/' + encodeURIComponent(itemId) + '/prereview', {method:'POST'}); notice(''); }
    catch (e) { if (!e.stale) notice(e.message, 'bad'); }
    await load();
  }
  async function load() {
    const seq = ++loadSeq; clearTimeout(polling);
    const data = await api('/admin/api/items/' + encodeURIComponent(itemId) + '/trace');
    if (seq !== loadSeq) return;
    $('trace-title').textContent = data.item.title || 'Untitled submission';
    const kind = {event:'Event', announcement:'Opportunity', achievement:'Achievement'}[data.item.type] || data.item.type;
    const state = {draft:'Needs fixing', pending:'Waiting for review', approved:'Approved', published:'Published', rejected:'Returned to club'}[data.item.status] || data.item.status;
    $('trace-sub').textContent = 'Run trace · ' + kind + ' · ' + state + ' · Submitted by ' + data.item.submitted_by + ' · ID ' + data.item.id;
    renderTotals(data);
    $('brief').replaceChildren(Brief.render(data.brief, {itemId, canRun: me.role === 'student_affairs', onRun: runAgain, showTraceLink: false}));
    renderSteps(data.steps);
    if (data.brief.status === 'running') polling = setTimeout(() => load().catch(() => {}), 2500);
  }
  $('refresh').onclick = () => load().catch(e => { if (!e.stale) notice(e.message, 'bad'); });
  $('export').onclick = async () => {
    try {
      const file = await api('/admin/api/items/' + encodeURIComponent(itemId) + '/trace/export');
      const url = URL.createObjectURL(new Blob([file.markdown], {type:'text/markdown'}));
      const link = el('a', {href:url, download:file.filename}); document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { if (!e.stale) notice(e.message, 'bad'); }
  };
  window.addEventListener('boardcast:signout', () => { loadSeq++; clearTimeout(polling); });
  Staff.init(async who => { me = who; $('app').hidden = false; try { await load(); } catch (e) { if (!e.stale) notice(e.message, 'bad'); } });
})();
