(() => {
  const {api, el} = Staff;
  const $ = id => document.getElementById(id);
  let data = null;
  function notice(text, kind = 'ok') { $('notice').textContent = text; $('notice').className = 'alert ' + kind; $('notice').hidden = !text; }
  function tile(label, value, hint) { return el('div', {class:'trace-tile'}, el('span', {class:'trace-tile-value'}, String(value)), el('span', {class:'trace-tile-label'}, label), hint ? el('small', {class:'muted'}, hint) : null); }
  function fill(id, tiles) { $(id).replaceChildren(...tiles); }
  function num(value) { return value == null ? 'Not reported' : Number(value).toLocaleString(); }
  function usd(value, digits = 4) { return value == null ? 'Not configured' : 'US$' + Number(value).toFixed(digits); }
  function row(...cells) { return el('tr', {}, ...cells.map((cell, i) => el('td', typeof cell === 'object' && cell?.num ? {class:'num'} : {}, typeof cell === 'object' && cell !== null ? cell.text : String(cell)))); }
  const n = text => ({num:true, text:String(text)});
  function render() {
    const d = data, s = d.pipeline.by_status;
    $('insights-sub').textContent = 'Every number on this page is counted in code from saved records. None is written by a model. Updated ' + new Date(d.generated_at).toLocaleString('en-GB', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'}) + '.';
    fill('pipeline', [tile('Submissions', d.pipeline.total, Object.entries(d.pipeline.by_type).map(([k, v]) => v + ' ' + k + (v === 1 ? '' : 's')).join(' · ') || 'None yet'),
      tile('Waiting for review', s.pending || 0), tile('Needs fixing by the club', s.draft || 0), tile('Approved', s.approved || 0),
      tile('Published', s.published || 0), tile('Returned to the club', s.rejected || 0)]);
    const r = d.review;
    fill('review', [tile('Decisions by Student Affairs', r.decisions, r.approved + ' approved · ' + r.rejected + ' returned'),
      tile('Median wait for a decision', r.median_minutes_to_decision == null ? 'No data yet' : r.median_minutes_to_decision < 90 ? r.median_minutes_to_decision + ' min' : (r.median_minutes_to_decision / 60).toFixed(1) + ' h', r.timed_decisions ? 'From submission to decision, over ' + r.timed_decisions + (r.timed_decisions === 1 ? ' decision' : ' decisions') : 'Recorded from the next decision onward')]);
    const o = r.outcomes;
    $('outcomes').replaceChildren(
      row('Looks ready', 'Approved', n(o.ready_approved)), row('Needs attention', 'Returned to the club', n(o.flagged_returned)),
      row('Needs attention', 'Approved after confirming the points were checked', n(o.flagged_approved_after_check)),
      row('Looks ready', 'Returned to the club (the person found something the agent did not)', n(o.ready_returned)),
      row('No current brief', 'Decided without AI advice', n(o.no_brief)));
    const a = d.agent;
    fill('agent', [tile('Briefs written', a.done, a.unavailable ? a.unavailable + ' unavailable (reviewed manually)' : 'None unavailable'),
      tile('Looks ready', a.looks_ready), tile('Needs attention', a.needs_attention),
      tile('Model overruled by fixed rules', a.overridden_by_rules, 'The model said “looks ready”; a check result said otherwise'),
      tile('Notes dropped for lack of evidence', a.points_dropped_without_evidence, 'Notes citing a tool the model never called')]);
    $('checks').replaceChildren(...a.flags_by_check.map(c => row(c.label, n(c.flagged))));
    const ai = d.ai;
    fill('ai', [tile('Gemini', d.ai_configured ? 'Connected' : 'Not configured', d.ai_configured ? 'Model: ' + d.model : 'Manual entry and manual review only'),
      tile('Model calls', ai.model_calls, ai.failed_calls ? ai.failed_calls + ' failed (each fell back to a manual step)' : 'None failed'),
      tile('Tokens in / out', ai.tokens_in == null ? 'Not reported' : num(ai.tokens_in) + ' / ' + num(ai.tokens_out), ai.model_seconds ? ai.model_seconds + ' s of model time' : null),
      tile('Estimated model cost', usd(ai.estimated_cost_usd), d.prices_configured ? 'From the configured token prices' : 'Set GEMINI_PRICE_* to estimate'),
      tile('Per submission that used AI', usd(ai.cost_per_submission_usd), ai.submissions_with_ai + (ai.submissions_with_ai === 1 ? ' submission' : ' submissions') + ' used Gemini')]);
    $('purposes').replaceChildren(...(ai.by_purpose.length ? ai.by_purpose.map(p => row(p.label, p.prompts.join(', ') || '-', n(p.calls), n(p.failed), n(num(p.tokens_in)), n(num(p.tokens_out)), n(p.avg_ms == null ? '-' : (p.avg_ms / 1000).toFixed(1) + ' s'), n(usd(p.estimated_cost_usd))))
      : [el('tr', {}, el('td', {colspan:'8', class:'muted'}, 'No Gemini call has been recorded yet.'))]));
    $('price-note').textContent = d.prices_configured ? 'Cost = tokens × the per-million-token prices set in GEMINI_PRICE_INPUT_PER_M and GEMINI_PRICE_OUTPUT_PER_M. It excludes hosting and email.'
      : 'Token counts come from the Gemini API. To show a cost, copy the prices for your model from the official pricing page into GEMINI_PRICE_INPUT_PER_M and GEMINI_PRICE_OUTPUT_PER_M.';
    const re = d.reach;
    fill('reach', [tile('Subscribed students', re.subscribers), tile('Editions delivered', re.editions_delivered, re.emails_delivered + (re.emails_delivered === 1 ? ' email' : ' emails') + ' delivered'),
      tile('Register clicks', re.register_clicks), tile('Add-to-calendar clicks', re.calendar_clicks), tile('Reminders set', re.reminders_set)]);
    calc();
    $('content').hidden = false;
  }
  function calc() {
    if (!data) return;
    const read = id => { const v = Number($(id).value); return Number.isFinite(v) && v >= 0 ? v : null; };
    const channels = read('calc-channels'), minutes = read('calc-minutes'), submit = read('calc-submit');
    const s = data.pipeline.by_status, handled = (s.approved || 0) + (s.published || 0);
    if (channels == null || minutes == null || submit == null) { $('calc-result').textContent = 'Enter a number of zero or more in each box.'; $('calc-formula').textContent = ''; return; }
    const perItem = channels * minutes - submit, hours = perItem * handled / 60, cost = data.ai.estimated_cost_usd;
    $('calc-result').textContent = handled ? (perItem >= 0 ? 'With these assumptions: about ' + hours.toFixed(1) + ' club hours saved across ' + handled + ' approved or published ' + (handled === 1 ? 'submission' : 'submissions') + '.' : 'With these assumptions BoardCast would take longer than posting by hand. Check the figures.')
      + (cost != null && hours > 0 ? ' Gemini cost for all recorded calls: ' + usd(cost) + ', or ' + usd(cost / hours) + ' per hour saved.' : '') : 'No approved or published submission yet, so there is nothing to multiply.';
    $('calc-formula').textContent = 'Formula: (channels × minutes per channel − minutes to submit once) × approved or published submissions ÷ 60. Assumption inputs are yours; the submission count and Gemini cost are counted by BoardCast.';
  }
  async function load() { data = await api('/admin/api/insights'); notice(''); render(); }
  $('calc').addEventListener('input', calc);
  $('calc').addEventListener('submit', ev => ev.preventDefault());
  $('refresh').onclick = () => load().catch(e => { if (!e.stale) notice(e.message, 'bad'); });
  window.addEventListener('boardcast:signout', () => { data = null; $('content').hidden = true; });
  Staff.init(async () => { $('app').hidden = false; try { await load(); } catch (e) { if (!e.stale) { $('content').hidden = true; notice(e.message === "You don't have permission to do this." ? 'Insights are for Student Affairs. Switch account to view them.' : e.message, 'bad'); } } });
})();
