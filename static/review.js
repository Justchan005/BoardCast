(() => {
  const {api, el} = Staff;
  const $ = id => document.getElementById(id);
  let me, items = [], status = 'pending', selected = null, issue = null, selectionRequest = 0;
  function updatePublishDisabled() {
    if ($('publish-local')) $('publish-local').disabled = !issue || !['preview','partial'].includes(issue.status) || Newsletter.blocksPublish();
    if ($('build-preview')) $('build-preview').disabled = Newsletter.blocksPublish();
  }
  function notice(text, kind = 'ok') { $('notice').textContent = text; $('notice').className = 'alert ' + kind; $('notice').hidden = !text; }
  async function action(button, work) {
    button.disabled = true;
    try { await work(); } catch (e) { if (!e.stale) notice(e.message, 'bad'); }
    finally {
      button.disabled = false;
      if (button.id === 'hold') button.disabled = !issue || ['sent','held'].includes(issue.status);
      if (button.id === 'publish-local' || button.id === 'build-preview') updatePublishDisabled();
    }
  }
  async function load() {
    items = (await api('/admin/api/queue')).items;
    for (const s of ['pending','draft','approved']) $('count-' + s).textContent = items.filter(i => i.status === s).length;
    renderList();
    if (me.role === 'student_affairs') await loadIssue();
    else $('issue-card').hidden = true;
  }
  function renderList() {
    const search = $('search').value.toLowerCase();
    const shown = items.filter(i => i.status === status && [i.data.title, i.club_id, i.submitted_by, i.data.organiser].join(' ').toLowerCase().includes(search));
    if (!shown.some(i => i.id === selected)) selected = shown[0]?.id || null;
    const list = $('list'); list.replaceChildren();
    for (const item of shown) {
      const button = el('button', {type:'button',class:'queue-item' + (item.id === selected ? ' selected' : ''), 'aria-pressed':String(item.id === selected), onclick:() => {selected = item.id; renderList();}},
        el('span',{class:'queue-kind'},item.type), el('strong',{},item.data.title || 'Untitled submission'),
        el('span',{class:'muted'},item.data.organiser || item.data.team || item.club_id || 'Campus office'),
        el('span',{class:'pill ' + item.status},item.issues?.length ? item.issues.length + ' checks to resolve' : item.status));
      list.append(button);
    }
    if (!shown.length) list.append(el('div',{class:'empty-state muted'},'No submissions in this view.'));
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
  function renderDetail(item) {
    const d = item.data, detail = $('detail'); detail.replaceChildren();
    detail.append(el('div',{class:'detail-title'},el('div',{},el('span',{class:'eyebrow'},'SUBMISSION ' + item.id),el('h2',{},d.title || 'Untitled submission')),el('span',{class:'pill ' + item.status},item.status)),
      el('p',{class:'muted'},'Submitted by ' + item.submitted_by + (item.updated_at ? ' · Updated ' + new Date(item.updated_at).toLocaleString() : '')));
    if (item.issues?.length) detail.append(el('div',{class:'alert warn'},el('strong',{},'Resolve before approval'),el('ul',{},...item.issues.map(p => el('li',{},p)))));
    const source = el('div',{class:'source-pane'},el('h3',{},'Original poster'));
    if (item.poster_url) source.append(el('a',{href:item.poster_url,target:'_blank',rel:'noopener', 'aria-label':'Open original poster'},el('img',{src:item.poster_url,alt:'Source poster for ' + d.title,class:'source-poster'})));
    else source.append(el('div',{class:'poster-placeholder'},'Text-only submission'));
    source.append(el('p',{class:'muted'},item.show_poster ? 'Poster will appear in the edition.' : 'Poster will not appear in the edition.'));
    const copy = el('div',{class:'publication-pane'},el('span',{class:'eyebrow'},'STUDENT-FACING COPY'),el('h3',{},'What students will receive'));
    const preview = el('div',{class:'publication-copy'});
    // Server renders the same autoescaped Jinja partial used by the email and magazine.
    preview.innerHTML = item.publication_preview;
    preview.querySelectorAll('a').forEach(a => {a.target = '_blank'; a.rel = 'noopener noreferrer';});
    copy.append(preview,el('p',{class:'muted'},'This text is shared with the newsletter and web magazine. Check every fact against the source.'));
    detail.append(el('div',{class:'review-content'},source,copy));
    const fields = el('dl',{class:'fields review-fields'});
    const labels = {title:'Title',organiser:'Organiser',date:'Date',start_time:'Starts (MYT)',end_time:'Ends (MYT)',venue:'Venue',fee:'Fee',category:'Category',audience:'Audience',summary:'Poster summary',team:'Team',competition:'Competition',result:'Result',deadline:'Deadline',action_link:'Action link',instagram_url:'Instagram link',consent_confirmed:'Consent confirmed'};
    for (const [key,label] of Object.entries(labels)) if (d[key] != null && d[key] !== '') {
      const value = typeof d[key] === 'boolean' ? (d[key] ? 'Yes' : 'No') : String(d[key]);
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
    actions.append(el('a',{class:'btn secondary',href:'/submit?item=' + encodeURIComponent(item.id)},'Correct submission'));
    if (me.role === 'student_affairs' && item.status === 'pending') {
      const approve = el('button',{class:'btn',type:'button'},'Approve for publication');
      approve.onclick = () => action(approve,async () => {
        await api('/admin/api/items/' + item.id + '/decision',{method:'POST',body:JSON.stringify({decision:'approve',expected_updated_at:item.updated_at})});
        notice('Approved: ' + d.title + '. It is ready for the next edition.'); await load();
      });
      const reject = el('button',{class:'btn danger',type:'button'},'Return with a reason');
      const form = el('form',{class:'reject-form stack',hidden:''},el('label',{for:'reject-reason'},'Tell the club what to correct'),el('textarea',{id:'reject-reason',required:'',maxlength:'2000',rows:'3',placeholder:'For example: confirm the venue shown on the poster.'}));
      const cancel = el('button',{class:'btn secondary',type:'button',onclick:()=>{form.hidden=true; reject.focus();}},'Cancel');
      const send = el('button',{class:'btn danger',type:'submit'},'Reject and notify club');
      form.append(el('div',{class:'row'},send,cancel));
      reject.onclick = () => {form.hidden=false; form.querySelector('textarea').focus();};
      form.onsubmit = ev => {ev.preventDefault(); action(send,async () => {const result=await api('/admin/api/items/'+item.id+'/decision',{method:'POST',body:JSON.stringify({decision:'reject',note:form.querySelector('textarea').value.trim(),expected_updated_at:item.updated_at})}); notice(result.warning || 'Returned to the club with your correction reason.',result.warning?'warn':'ok'); await load();});};
      actions.append(reject,approve); detail.append(actions,form);
    } else detail.append(actions);
  }
  async function loadIssue() {
    const res = await api('/admin/api/preview'); issue = res.issue;
    Newsletter.setIssue(issue, res);
    $('issue-card').hidden = false;
    $('issue-link').hidden = !issue;
    if (!issue) {$('issue-status').textContent='No edition built yet. Approve submissions first.'; $('hold').disabled=true; if($('publish-local')) $('publish-local').disabled=true; return;}
    const counts=issue.delivery_summary || {};
    const stats=Object.entries(counts).filter(([,v])=>v).map(([k,v])=>v+' '+k).join(' · ');
    $('issue-status').textContent=issue.week_start+' · '+issue.status+(stats?' · '+stats:'');
    $('issue-link').href=res.magazine_url;
    $('hold').disabled=['sent','held'].includes(issue.status);
    updatePublishDisabled();
    if(counts.unknown) notice('Some delivery outcomes are uncertain. Check the sender’s Sent mailbox before resolving them; these recipients will not be retried automatically.','warn');
  }
  $('hold').onclick=()=>action($('hold'),async()=>{await api('/admin/api/issues/'+issue.id+'/hold',{method:'POST'});notice('Issue held. Further deliveries are stopped.');await loadIssue();});
  $('build-preview')?.addEventListener('click',()=>action($('build-preview'),async()=>{await api('/admin/api/demo/preview',{method:'POST'});notice('Local preview built from approved submissions.');await loadIssue();}));
  $('publish-local')?.addEventListener('click',()=>action($('publish-local'),async()=>{const result=await api('/admin/api/demo/publish',{method:'POST'});notice(result.sent ? result.sent+' email(s) saved to the local outbox.' : (result.reason || 'No new email was delivered.'),result.failed?.length?'warn':'ok');await load();}));
  $('refresh').onclick=()=>action($('refresh'),load);
  $('search').oninput=renderList;
  const tabs=[...document.querySelectorAll('[role=tab]')];
  function selectTab(button) {status=button.dataset.status; selected=null; for(const b of tabs){b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1;} $('list').setAttribute('aria-labelledby',button.id);renderList();}
  tabs.forEach((button,index)=>{button.onclick=()=>selectTab(button);button.onkeydown=ev=>{let next;if(ev.key==='ArrowRight')next=(index+1)%tabs.length;if(ev.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;if(ev.key==='Home')next=0;if(ev.key==='End')next=tabs.length-1;if(next!=null){ev.preventDefault();tabs[next].focus();selectTab(tabs[next]);}};});
  window.addEventListener('boardcast:signout',()=>{selectionRequest++;items=[];selected=null;issue=null;Newsletter.reset();});
  Staff.init(async who=>{me=who;$('app').hidden=false;try{await load();if(me.role==='student_affairs')await Newsletter.init({onStateChange:updatePublishDisabled,onSaved:loadIssue});else Newsletter.reset();}catch(e){if(!e.stale)notice(e.message,'bad');}});
})();
