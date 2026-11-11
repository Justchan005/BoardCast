(() => {
  const {api, el} = Staff;
  const $ = id => document.getElementById(id);
  let me, items = [], status = 'pending', selected = null, issue = null, selectionRequest = 0;
  let publishingBusy = false;
  function updatePublishDisabled() {
    const blocked = Newsletter.blocksPublish();
    const canPublish = !!issue && ['preview','partial'].includes(issue.status);
    if ($('publish-local')) { $('publish-local').disabled = publishingBusy || !canPublish || blocked; $('publish-local').className = canPublish ? 'btn' : 'btn secondary'; }
    if ($('build-preview')) { $('build-preview').disabled = publishingBusy || blocked; $('build-preview').className = canPublish ? 'btn secondary' : 'btn'; }
    if ($('issue-next-step')) $('issue-next-step').textContent = blocked
      ? 'Save or discard your template changes below to continue.'
      : !issue ? 'Approve submissions, then build a preview to check the edition.'
      : issue.status === 'sent' ? 'This edition is already in the outbox. Build the next preview when new submissions are approved.'
      : issue.status === 'held' ? 'Delivery is paused. Build a new preview when you are ready to continue.'
      : canPublish ? 'Read the edition, then publish it to the local outbox when it is ready.'
      : 'Check the edition’s delivery status before continuing.';
  }
  // Stored values are ISO strings and ids; people read dates, times and plain status words.
  const STATUS = {draft:'Needs fixing', pending:'Waiting for review', approved:'Approved', published:'Published', rejected:'Returned to club'};
  const KIND = {event:'Event', announcement:'Opportunity', achievement:'Achievement'};
  function niceDate(value) {
    const date = new Date(String(value).slice(0, 10) + 'T12:00:00');
    return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleDateString('en-GB', {weekday:'short', day:'numeric', month:'short', year:'numeric'});
  }
  function niceTime(value) {
    const match = /^(\d{1,2}):(\d{2})/.exec(String(value));
    if (!match) return String(value);
    const hour = Number(match[1]);
    return (hour % 12 || 12) + ':' + match[2] + (hour < 12 ? ' am' : ' pm');
  }
  function whenLine(item) {
    const d = item.data, day = d.date || d.deadline;
    if (!day) return null;
    return (item.type === 'announcement' ? 'Closes ' : '') + niceDate(day).replace(/ \d{4}$/, '') + (d.start_time ? ', ' + niceTime(d.start_time) : '') + (d.venue ? ' · ' + d.venue : '');
  }
  function notice(text, kind = 'ok') { $('notice').textContent = text; $('notice').className = 'alert ' + kind; $('notice').hidden = !text; }
  async function action(button, work) {
    const isPublication = ['publish-local', 'build-preview', 'hold'].includes(button.id);
    if (isPublication) publishingBusy = true;
    button.disabled = true;
    if (isPublication) updatePublishDisabled();
    try { await work(); } catch (e) { if (!e.stale) notice(e.message, 'bad'); }
    finally {
      button.disabled = false;
      if (isPublication) publishingBusy = false;
      if (button.id === 'hold') button.disabled = !issue || ['sent','held'].includes(issue.status);
      if (isPublication) updatePublishDisabled();
    }
  }
  async function load() {
    items = (await api('/admin/api/queue')).items;
    for (const s of ['pending','draft','approved']) $('count-' + s).textContent = items.filter(i => i.status === s).length;
    renderList();
    if (me.role === 'student_affairs') await loadIssue();
    else $('issue-card').hidden = true;
  }
  function renderList(resetScroll = false) {
    const search = $('search').value.toLowerCase();
    const shown = items.filter(i => i.status === status && [i.data.title, i.club_id, i.submitted_by, i.data.organiser].join(' ').toLowerCase().includes(search));
    if (!shown.some(i => i.id === selected)) selected = shown[0]?.id || null;
    const list = $('list'); list.replaceChildren();
    for (const item of shown) {
      const button = el('button', {type:'button',class:'queue-item' + (item.id === selected ? ' selected' : ''), 'aria-pressed':String(item.id === selected), onclick:() => {
        selected = item.id;
        // Keep the focused card and scroll position while loading another submission.
        for (const card of list.querySelectorAll('.queue-item')) {
          card.classList.toggle('selected', card === button);
          card.setAttribute('aria-pressed', String(card === button));
        }
        showDetail(selected);
      }},
        el('span',{class:'queue-kind'},KIND[item.type] || item.type), el('strong',{},item.data.title || 'Untitled submission'),
        el('span',{class:'muted'},item.data.organiser || item.data.team || item.club_id || 'Campus office'),
        whenLine(item) ? el('span',{class:'queue-when'},whenLine(item)) : null,
        el('span',{class:'pill ' + item.status},item.issues?.length ? item.issues.length + (item.issues.length === 1 ? ' check to resolve' : ' checks to resolve') : STATUS[item.status] || item.status));
      list.append(button);
    }
    if (!shown.length) list.append(el('div',{class:'empty-state muted'},'No submissions in this view.'));
    if (resetScroll) list.scrollTop = 0;
    if (selected) showDetail(selected);
    else { selectionRequest++; $('detail').replaceChildren(el('div',{class:'empty-state'},el('h2',{},'All clear'),el('p',{class:'muted'},'Choose another status or wait for the next club submission.'))); }
  }
  async function showDetail(id) {
    const request = ++selectionRequest;
    $('detail').setAttribute('aria-busy','true');
    try {
      const item = await api('/admin/api/submissions/' + encodeURIComponent(id));
      if (request !== selectionRequest) return;
      renderDetail(item);
    } catch (e) { if (request === selectionRequest) notice(e.message,'bad'); }
    finally { if (request === selectionRequest) $('detail').removeAttribute('aria-busy'); }
  }
  // The AI brief loads beside the review, never in front of it: the reviewer can decide at any time.
  let briefTimer = null, onBriefLoaded = null;
  async function loadBrief(item, slot, attempt = 0) {
    const request = selectionRequest; clearTimeout(briefTimer);
    if (!['pending','draft','approved'].includes(item.status)) return;
    let brief;
    try { brief = await api('/admin/api/items/' + encodeURIComponent(item.id) + '/brief'); }
    catch (e) { return; }   // the brief is optional; the review itself is unaffected
    if (request !== selectionRequest || !slot.isConnected) return;
    const onRun = async button => {
      button.disabled = true; slot.replaceChildren(Brief.render({status:'running'}));
      try { await api('/admin/api/items/' + encodeURIComponent(item.id) + '/prereview', {method:'POST'}); }
      catch (e) { if (!e.stale) notice(e.message, 'bad'); }
      if (request === selectionRequest && slot.isConnected) loadBrief(item, slot);
    };
    slot.replaceChildren(Brief.render(brief, {itemId:item.id, canRun:me.role === 'student_affairs' && item.status === 'pending', onRun, showTraceLink:true}));
    onBriefLoaded?.(brief);
    if (brief.status === 'running' && attempt < 60) briefTimer = setTimeout(() => loadBrief(item, slot, attempt + 1), 2500);
  }
  function renderDetail(item) {
    const d = item.data, detail = $('detail'); detail.replaceChildren(); onBriefLoaded = null;
    detail.append(el('div',{class:'detail-title'},el('h2',{},d.title || 'Untitled submission'),el('span',{class:'pill ' + item.status},STATUS[item.status] || item.status)),
      el('p',{class:'muted detail-meta'},(KIND[item.type] || item.type) + ' · Submitted by ' + item.submitted_by + (item.updated_at ? ' · Updated ' + new Date(item.updated_at).toLocaleString('en-GB', {day:'numeric', month:'short', hour:'numeric', minute:'2-digit'}) : '') + ' · ID ' + item.id));
    if (item.issues?.length) detail.append(el('div',{class:'alert warn'},el('strong',{},'Resolve before approval'),el('ul',{},...item.issues.map(p => el('li',{},p)))));
    const briefSlot = el('div',{id:'brief-slot'}); detail.append(briefSlot); loadBrief(item, briefSlot);
    const source = el('div',{class:'source-pane'},el('h3',{},'Original poster'));
    if (item.poster_url) source.append(el('a',{href:item.poster_url,target:'_blank',rel:'noopener', 'aria-label':'Open original poster'},el('img',{src:item.poster_url,alt:'Source poster for ' + d.title,class:'source-poster'})));
    else source.append(el('div',{class:'poster-placeholder'},'Text-only submission'));
    source.append(el('p',{class:'muted'},item.show_poster ? 'Poster will appear in the edition.' : 'Poster will not appear in the edition.'));
    const copy = el('div',{class:'publication-pane'},el('h3',{},'What students will receive'));
    const preview = el('div',{class:'publication-copy'});
    // Server renders the same autoescaped Jinja partial used by the email and magazine.
    preview.innerHTML = item.publication_preview;
    preview.querySelectorAll('a').forEach(a => {a.target = '_blank'; a.rel = 'noopener noreferrer';});
    copy.append(preview,el('p',{class:'muted'},'This text is shared with the newsletter and web magazine. Check every fact against the source.'));
    detail.append(el('div',{class:'review-content'},source,copy));
    const fields = el('dl',{class:'fields review-fields'});
    const labels = {title:'Title',organiser:'Organiser',date:'Date',start_time:'Starts (MYT)',end_time:'Ends (MYT)',venue:'Venue',fee:'Fee',category:'Category',audience:'Audience',summary:'Poster summary',team:'Team',competition:'Competition',result:'Result',deadline:'Deadline',action_link:'Action link',instagram_url:'Instagram link',consent_confirmed:'Consent confirmed'};
    for (const [key,label] of Object.entries(labels)) if (d[key] != null && d[key] !== '') {
      const value = typeof d[key] === 'boolean' ? (d[key] ? 'Yes' : 'No')
        : ['date','deadline'].includes(key) ? niceDate(d[key]) : ['start_time','end_time'].includes(key) ? niceTime(d[key])
        : key === 'category' ? String(d[key]).charAt(0).toUpperCase() + String(d[key]).slice(1) : String(d[key]);
      const safe = ['action_link','instagram_url'].includes(key) && Staff.safeLink(value);
      fields.append(el('dt',{},label),el('dd',{},safe ? el('a',{href:safe,target:'_blank',rel:'noopener noreferrer'},value) : value));
    }
    if (d.registration) {
      fields.append(el('dt',{},'Registration required'),el('dd',{},d.registration.required ? 'Yes' : 'No'));
      if (d.registration.link) {const link = Staff.safeLink(d.registration.link); fields.append(el('dt',{},'Registration link'),el('dd',{},link ? el('a',{href:link,target:'_blank',rel:'noopener noreferrer'},d.registration.link) : d.registration.link));}
    }
    fields.append(el('dt',{},'Club description'),el('dd',{},item.description || 'Uses the poster summary shown above.'));
    detail.append(el('details',{class:'all-fields',open:''},el('summary',{},'All submitted details'),fields));
    const actions = el('div',{class:'review-actions'});
    actions.append(el('a',{class:'btn secondary',href:'/submit?item=' + encodeURIComponent(item.id)},'Correct submission'),
      el('a',{class:'btn secondary',href:'/admin/trace/' + encodeURIComponent(item.id)},'Run trace'));
    if (me.role === 'student_affairs' && item.status === 'pending') {
      const approve = el('button',{class:'btn',type:'button'},'Approve for publication');
      // Control: red flags from the AI brief must be acknowledged by a person before approval.
      // The server enforces the same rule; this box only makes it visible.
      const ackInput = el('input',{type:'checkbox',id:'ack-flags'}), ackText = el('span',{});
      const ack = el('label',{class:'check-label ack-flags',for:'ack-flags',hidden:''},ackInput,ackText);
      onBriefLoaded = brief => {
        const needed = brief.status === 'done' && brief.current && brief.recommendation === 'needs_attention';
        if (!needed) ackInput.checked = false;
        ack.hidden = !needed;
        ackText.textContent = 'I have checked what the AI pre-review raised and still want to approve.';
      };
      approve.onclick = () => action(approve,async () => {
        if (!ack.hidden && !ackInput.checked) { ackInput.focus(); throw new Error('The AI pre-review raised points to check. Tick the confirmation box above the buttons, or return the submission to the club.'); }
        let result;
        try { result = await api('/admin/api/items/' + item.id + '/decision',{method:'POST',body:JSON.stringify({decision:'approve',expected_updated_at:item.updated_at,acknowledge_flags:ackInput.checked})}); }
        catch (e) { if (!e.stale) loadBrief(item, briefSlot); throw e; }
        notice('Approved: ' + d.title + '. It is ready for the next edition.' + (result.ai_points_acknowledged ? ' Your confirmation of the pre-review points is recorded in the run trace.' : '')); await load();
      });
      const reject = el('button',{class:'btn danger',type:'button'},'Return with a reason');
      const form = el('form',{class:'reject-form stack',hidden:''},el('label',{for:'reject-reason'},'Tell the club what to correct'),el('textarea',{id:'reject-reason',required:'',maxlength:'2000',rows:'3',placeholder:'For example: confirm the venue shown on the poster.'}));
      const cancel = el('button',{class:'btn secondary',type:'button',onclick:()=>{form.hidden=true; reject.focus();}},'Cancel');
      const send = el('button',{class:'btn danger',type:'submit'},'Reject and notify club');
      form.append(el('div',{class:'row'},send,cancel));
      reject.onclick = () => {form.hidden=false; form.scrollIntoView({block:'nearest'}); form.querySelector('textarea').focus();};
      form.onsubmit = ev => {ev.preventDefault(); action(send,async () => {const result=await api('/admin/api/items/'+item.id+'/decision',{method:'POST',body:JSON.stringify({decision:'reject',note:form.querySelector('textarea').value.trim(),expected_updated_at:item.updated_at})}); notice(result.warning || 'Returned to the club with your correction reason.',result.warning?'warn':'ok'); await load();});};
      actions.append(reject,approve); detail.append(el('div',{class:'decision-bar'},ack,actions),form);
    } else detail.append(el('div',{class:'decision-bar'},actions));
  }
  async function loadIssue() {
    const res = await api('/admin/api/preview'); issue = res.issue;
    Newsletter.setIssue(issue, res);
    $('issue-card').hidden = false;
    $('issue-link').hidden = !issue;
    const headings = {sent:'Latest sent edition', held:'Edition on hold', preview:'Preview edition', partial:'Partially delivered edition'};
    $('issue-heading').textContent = issue ? (headings[issue.status] || 'Current edition') : 'Prepare your first edition';
    if ($('build-preview')) $('build-preview').textContent = issue?.status === 'sent' ? 'Build next preview' : issue ? 'Rebuild local preview' : 'Build local preview';
    if (!issue) {$('issue-status').textContent='No edition built yet.'; $('hold').disabled=true; updatePublishDisabled(); return;}
    const counts=issue.delivery_summary || {};
    const stats=Object.entries(counts).filter(([,v])=>v).map(([k,v])=>v+' '+k).join(' · ');
    const date = new Date(issue.week_start + 'T12:00:00');
    const readableDate = Number.isNaN(date.valueOf()) ? issue.week_start : date.toLocaleDateString('en-GB', {day:'numeric', month:'short', year:'numeric'});
    $('issue-status').textContent='Week of ' + readableDate + (stats?' · '+stats:'');
    $('issue-link').href=res.magazine_url;
    $('hold').disabled=['sent','held'].includes(issue.status);
    updatePublishDisabled();
    if(counts.unknown) notice('Some delivery outcomes are uncertain. Check the sender’s Sent mailbox before resolving them; these recipients will not be retried automatically.','warn');
  }
  $('hold').onclick=()=>action($('hold'),async()=>{await api('/admin/api/issues/'+issue.id+'/hold',{method:'POST'});notice('Issue held. Further deliveries are stopped.');await loadIssue();});
  $('build-preview')?.addEventListener('click',()=>action($('build-preview'),async()=>{await api('/admin/api/demo/preview',{method:'POST'});notice('Local preview built from approved submissions.');await loadIssue();}));
  $('publish-local')?.addEventListener('click',()=>action($('publish-local'),async()=>{const result=await api('/admin/api/demo/publish',{method:'POST'});notice(result.sent ? result.sent+' email(s) saved to the local outbox.' : (result.reason || 'No new email was delivered.'),result.failed?.length?'warn':'ok');await load();}));
  $('refresh').onclick=()=>action($('refresh'),load);
  $('search').oninput=()=>renderList(true);
  const tabs=[...document.querySelectorAll('[role=tab]')];
  function selectTab(button) {status=button.dataset.status; selected=null; for(const b of tabs){b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1;} $('list').setAttribute('aria-labelledby',button.id);renderList(true);}
  tabs.forEach((button,index)=>{button.onclick=()=>selectTab(button);button.onkeydown=ev=>{let next;if(ev.key==='ArrowRight')next=(index+1)%tabs.length;if(ev.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;if(ev.key==='Home')next=0;if(ev.key==='End')next=tabs.length-1;if(next!=null){ev.preventDefault();tabs[next].focus();selectTab(tabs[next]);}};});
  window.addEventListener('boardcast:signout',()=>{clearTimeout(briefTimer);selectionRequest++;items=[];selected=null;issue=null;Newsletter.reset();});
  Staff.init(async who=>{me=who;$('app').hidden=false;try{await load();if(me.role==='student_affairs')await Newsletter.init({onStateChange:updatePublishDisabled,onSaved:loadIssue});else Newsletter.reset();}catch(e){if(!e.stale)notice(e.message,'bad');}});
})();
