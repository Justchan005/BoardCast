(() => {
  const {api,el}=Staff, $=id=>document.getElementById(id);
  let itemId=new URLSearchParams(location.search).get('item'), uploadId=null, objectUrl=null, generation=0;
  let uploading=false, reading=false, saving=false, failedUpload=false, extracted={}, existingPoster=null;
  function notice(text,kind='ok') {$('notice').textContent=text;$('notice').className='alert '+kind;$('notice').hidden=!text;}
  function busy() {
    $('submit-item').disabled=uploading||reading||saving||failedUpload;
    $('read-poster').disabled=!uploadId||uploading||reading||saving||$('app').dataset.aiConfigured!=='true';
    $('poster-file').disabled=saving;$('remove-poster').disabled=saving;
    $('item-type').disabled=saving||!!itemId;
  }
  function changeType() {const type=$('item-type').value; document.querySelectorAll('[data-kinds]').forEach(e=>{e.hidden=!e.dataset.kinds.split(' ').includes(type);}); document.querySelectorAll('[data-kinds] input, [data-kinds] select').forEach(e=>{e.disabled=!!e.closest('[hidden]');}); $('confirmed').checked=false;}
  function wordCount() {const n=$('description').value.trim().split(/\s+/).filter(Boolean).length; $('description-count').textContent=n+' / 40 words';$('description-count').className=n>40?'text-danger':'muted';}
  function fill(data,description) {
    for(const key of ['title','organiser','date','start_time','end_time','venue','fee','category','audience','deadline','action_link','team','competition','result','instagram_url']) {
      let value=data[key] ?? (key==='category'?'other':'');
      if(key==='deadline' && value) {
        value=/([zZ]|[+-]\d{2}:\d{2})$/.test(value)
          ? new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Kuala_Lumpur',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)).replace(' ','T')
          : String(value).slice(0,16);
      }
      if($(key)) $(key).value=value;
    }
    $('registration-required').checked=!!data.registration?.required;$('registration-link').value=data.registration?.link||'';
    $('consent_confirmed').checked=!!data.consent_confirmed;
    $('description').value=description ?? data.summary ?? '';wordCount();$('confirmed').checked=false;
  }
  async function history() {
    const report=await api('/admin/api/club'); const box=$('history');box.replaceChildren();
    for(const row of report.rows.slice(0,6)) box.append(el('a',{class:'history-item',href:'/submit?item='+encodeURIComponent(row.id)},el('strong',{},row.title||'Untitled'),el('span',{class:'pill '+row.status},row.status),...(row.rejection_reason?[el('small',{class:'text-danger'},row.rejection_reason)]:[])));
    if(!report.rows.length)box.append(el('p',{class:'muted'},'Your first submission will appear here.'));
  }
  function showPoster(src) {$('poster-preview').src=src||'';$('poster-preview').hidden=!src;$('poster-empty').hidden=!!src;}
  async function openItem() {
    if(!itemId)return;
    const item=await api('/admin/api/submissions/'+encodeURIComponent(itemId));
    $('submission-form').hidden=false;
    $('item-type').value=item.type;$('item-type').disabled=true;changeType();extracted=item.data;
    fill(item.data,item.description || item.data.summary || '');
    $('show-poster').checked=!!item.poster_wanted||!!item.show_poster;
    existingPoster=item.poster_url;showPoster(existingPoster);
    $('form-title').textContent='Correct your submission';$('submit-item').textContent='Save and resubmit';$('new-submission').hidden=false;
    if(item.rejection_reason)notice('Student Affairs: '+item.rejection_reason,'warn');
    else if(item.issues?.length)notice(item.issues.join(' · '),'warn');
    else notice('Editing sends this submission back for review. Published editions keep their original text.','warn');
  }
  $('poster-file').onchange=async()=>{
    // A file dialog opened before saving can still deliver a change event.
    // Keep the saved payload's poster and generation until the request finishes.
    if(saving){$('poster-file').value='';return;}
    const current=++generation,file=$('poster-file').files[0];uploadId=null;failedUpload=false;reading=false;uploading=false;$('confirmed').checked=false;
    if(objectUrl)URL.revokeObjectURL(objectUrl);
    if(!file){showPoster(existingPoster);busy();return;}
    objectUrl=URL.createObjectURL(file);showPoster(objectUrl);$('remove-poster').hidden=false;uploading=true;busy();
    $('upload-status').textContent='Uploading '+file.name+'…';
    try {const body=new FormData();body.append('file',file);const result=await api('/admin/api/uploads',{method:'POST',body});
      if(current!==generation)return;uploadId=result.upload_id;$('upload-status').textContent='Poster saved. Read it with Gemini or enter the details manually.';
    }catch(e){if(current===generation){failedUpload=true;$('upload-status').textContent=e.message;notice(e.message,'bad');}}
    finally{if(current===generation){uploading=false;busy();}}
  };
  $('remove-poster').onclick=()=>{if(saving)return;generation++;uploading=false;reading=false;failedUpload=false;uploadId=null;$('poster-file').value='';if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=null;}showPoster(existingPoster);$('remove-poster').hidden=true;$('upload-status').textContent=existingPoster?'Keeping the current poster. Uncheck “Include the original poster” to hide it from students.':'Continue with a text-only submission or choose another image.';busy();};
  $('read-poster').onclick=async()=>{
    if(saving||uploading||reading||!uploadId)return;
    const current=generation,type=$('item-type').value;reading=true;busy();$('upload-status').textContent='Reading the poster with Gemini…';
    try{const result=await api('/admin/api/uploads/'+uploadId+'/extract',{method:'POST',body:JSON.stringify({type})});
      if(current!==generation||type!==$('item-type').value)return;
      extracted=result.data;const description=$('description').value.trim();fill(result.data,description||undefined);
      $('upload-status').textContent='Gemini filled the details. Compare them with the poster before confirming.';
      notice(result.missing.length?'Please complete: '+result.missing.join(', '):'Poster read. Check the extracted details before submitting.',result.missing.length?'warn':'ok');
    }catch(e){if(current===generation){$('upload-status').textContent='Manual entry is available.';notice(e.message,'warn');}}
    finally{if(current===generation){reading=false;busy();}}
  };
  function data() {
    const type=$('item-type').value, v=id=>$(id).value.trim()||null;
    if(type==='event')return {title:v('title')||'',organiser:v('organiser'),date:v('date'),start_time:v('start_time'),end_time:v('end_time'),venue:v('venue'),fee:v('fee'),category:v('category'),audience:v('audience'),registration:{required:$('registration-required').checked,link:v('registration-link')},summary:extracted.summary||null};
    if(type==='announcement')return {title:v('title')||'',organiser:v('organiser')||'',summary:v('description')||'',deadline:v('deadline'),action_link:v('action_link'),audience:extracted.audience||null};
    return {title:v('title')||'',team:v('team')||'',competition:v('competition')||'',result:v('result')||'',date:v('date'),instagram_url:v('instagram_url'),consent_confirmed:$('consent_confirmed').checked};
  }
  $('submission-form').onsubmit=async ev=>{
    ev.preventDefault();if(uploading||reading||saving||failedUpload)return;
    const payload={type:$('item-type').value,data:data(),description:$('description').value.trim(),show_poster:$('show-poster').checked,confirmed:$('confirmed').checked,upload_id:uploadId};
    const current=generation;saving=true;busy();
    try{const result=await api('/admin/api/submissions'+(itemId?'/'+encodeURIComponent(itemId):''),{method:itemId?'PATCH':'POST',body:JSON.stringify(payload)});
      if(current!==generation)return;
      itemId=result.id;$('item-type').disabled=true;$('confirmed').checked=false;$('new-submission').hidden=false;$('submit-item').textContent='Save and resubmit';
      const saved=$('saved');saved.replaceChildren(el('strong',{},result.status==='pending'?'Sent to Student Affairs.':'Saved with checks to resolve.'),el('p',{},'Submission '+result.id+' · '+result.status),...(result.issues.length?[el('ul',{},...result.issues.map(i=>el('li',{},i)))]:[]),el('a',{href:'/admin',class:'text-link'},'Open review queue'));saved.className='alert '+(result.status==='draft'?'warn':'ok');saved.hidden=false;
      if(result.poster_hidden)notice(result.poster_hidden,'warn');else notice('Your submission has been saved.');
      const item=await api('/admin/api/submissions/'+encodeURIComponent(itemId));existingPoster=item.poster_url;await history();saved.scrollIntoView({behavior:'smooth',block:'start'});
    }catch(e){if(!e.stale && current===generation)notice(e.message,'bad');}
    finally{if(current===generation){saving=false;busy();}}
  };
  $('item-type').onchange=()=>{extracted={};changeType();};$('description').addEventListener('input',wordCount);
  $('submission-form').addEventListener('input',ev=>{if(ev.target.id!=='confirmed')$('confirmed').checked=false;});
  window.addEventListener('boardcast:signout',()=>{generation++;uploadId=null;uploading=false;reading=false;saving=false;failedUpload=false;extracted={};itemId=new URLSearchParams(location.search).get('item');$('submission-form').reset();$('submission-form').hidden=false;$('item-type').disabled=!!itemId;$('saved').hidden=true;$('new-submission').hidden=true;$('submit-item').textContent=itemId?'Save and resubmit':'Submit for review';$('form-title').textContent='Make it easy to take part.';$('history').replaceChildren();if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=null;}showPoster(null);existingPoster=null;notice('');busy();});
  Staff.init(async()=>{$('app').hidden=false;$('submission-form').hidden=false;changeType();try{await history();await openItem();}catch(e){if(!e.stale){notice(e.message,'bad');$('submission-form').hidden=!!itemId;}}});
})();
