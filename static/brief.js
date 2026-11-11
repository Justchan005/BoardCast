// The AI pre-review brief card, shared by the review desk and the run trace page.
// The brief is advice for Student Affairs: it never changes a submission's status.
const Brief = (() => {
  const {el} = Staff;
  const OUTCOME = {ok:'No issue', flag:'Check this', error:'Could not run', skipped:'Not run'};
  function list(cls, rows) { return el('ul', {class: cls}, ...rows); }
  // options: {itemId, canRun, onRun(button), showTraceLink}
  function render(brief, options = {}) {
    const card = el('section', {class: 'brief-card', 'aria-label': 'AI pre-review brief'});
    const head = el('div', {class: 'brief-head'}, el('h3', {class: 'brief-title'}, 'AI pre-review', el('span', {class: 'brief-advice'}, 'Advice only')));
    card.append(head);
    const run = label => {
      if (!options.canRun || !brief.ai_configured) return null;
      const button = el('button', {class: 'btn secondary brief-run', type: 'button'}, label);
      button.onclick = () => options.onRun(button);
      return button;
    };
    const status = brief.status;
    if (status === 'none') {
      card.classList.add('brief-quiet');
      card.append(el('p', {class: 'brief-headline'}, brief.ai_configured
        ? 'No pre-review yet for this submission.'
        : 'AI pre-review is off: no Gemini credential is set on this server. Review this submission manually.'));
      const button = run('Run pre-review'); if (button) card.append(button);
      return card;
    }
    if (status === 'running') {
      card.classList.add('brief-running');
      card.append(el('p', {class: 'brief-headline', role: 'status'}, el('span', {class: 'brief-spinner', 'aria-hidden': 'true'}),
        'The agent is checking this submission. You can review it yourself in the meantime.'));
      return card;
    }
    if (status === 'unavailable') {
      card.classList.add('brief-warn');
      head.append(el('span', {class: 'pill draft'}, 'Unavailable'));
      card.append(el('p', {class: 'brief-headline'}, 'No AI brief for this submission. Review it manually.'),
        el('p', {class: 'muted'}, brief.reason || 'The agent could not complete its checks.'));
      const button = run('Try again'); if (button) card.append(button);
      return card;
    }
    const ready = brief.recommendation === 'looks_ready';
    card.classList.add(ready ? 'brief-ok' : 'brief-warn');
    head.append(el('span', {class: 'pill ' + (ready ? 'approved' : 'draft')}, ready ? 'Looks ready' : 'Needs attention'));
    if (!brief.current) card.append(el('p', {class: 'alert warn'}, 'This brief was written for an earlier version of the submission. Run it again before relying on it.'));
    card.append(el('p', {class: 'brief-headline'}, brief.headline));
    if (brief.flags?.length) card.append(el('h4', {}, 'Found by the checks'), list('brief-flags', brief.flags.map(text => el('li', {}, text))));
    if (brief.checks?.length) card.append(el('h4', {}, 'Checks the agent chose to run'), list('brief-checks', brief.checks.map(check =>
      el('li', {class: 'check-' + check.outcome}, el('span', {class: 'check-outcome'}, OUTCOME[check.outcome] || check.outcome),
        el('span', {}, el('strong', {}, check.label), ' ', check.summary)))));
    if (brief.points?.length) card.append(el('h4', {}, 'Agent notes'), list('brief-points', brief.points.map(point =>
      el('li', {class: 'point-' + point.severity}, point.text))));
    if (brief.overridden_by_rules) card.append(el('p', {class: 'muted brief-rule'},
      'The model recommended “looks ready”. Fixed rules changed it to “needs attention” because of the points above.'));
    const foot = el('p', {class: 'muted brief-foot'}, 'You decide: the agent cannot approve, reject or edit. ',
      [brief.prompt, brief.model, brief.turns ? brief.turns + (brief.turns === 1 ? ' model turn' : ' model turns') : null].filter(Boolean).join(' · '));
    if (options.showTraceLink && options.itemId) foot.append(' · ', el('a', {href: '/admin/trace/' + encodeURIComponent(options.itemId)}, 'View run trace'));
    card.append(foot);
    const again = run(brief.current ? 'Run again' : 'Run pre-review again'); if (again) card.append(again);
    return card;
  }
  return {render};
})();
