// Run: node tests/submission_clarity.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(request = async route => route.endsWith('/club') ? {rows: []} : {}, hostedDemo = false) {
  const elements = new Map(); let ready;
  class Element {
    constructor() {this.value='';this.checked=false;this.dataset={};this.children=[];this.attributes={};}
    addEventListener(event, handler) {this['on'+event]=handler;}
    append(...nodes) {this.children.push(...nodes);}
    replaceChildren(...nodes) {this.children=nodes;}
    setAttribute(name, value) {this.attributes[name]=value;}
    getAttribute(name) {return this.attributes[name]||null;}
    removeAttribute(name) {delete this.attributes[name];}
    focus() {this.focused=true;}
    scrollIntoView() {}
  }
  const get = id => {if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  get('item-type').value='event';
  get('app').dataset.requiredFields=JSON.stringify({event:['title','organiser','date','start_time','venue','fee'],announcement:['title','organiser','summary'],achievement:['title','team','competition','result']});
  get('description').setAttribute('aria-describedby','description-count');
  const context = vm.createContext({URLSearchParams,Date,Intl,location:{search:''},
    Staff:{api:request,init:fn=>{ready=fn;},el:(tag,attrs,...children)=>Object.assign(new Element(),attrs,{tag,children})},
    document:{body:{dataset:{hostedDemo:String(hostedDemo)}},getElementById:get,querySelectorAll:()=>[]},window:{addEventListener() {}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','static','submit.js'),'utf8'),context);
  return {get,ready:()=>ready(),input:id=>get('submission-form').oninput({target:{id}})};
}

async function run() {
  const f=fixture(); await f.ready();
  f.get('title').value='Campus showcase'; f.input('title');
  assert.equal(f.get('step-details').dataset.state,'current','Title alone must not complete the details');
  assert.equal(f.get('step-details').getAttribute('aria-current'),'step');
  assert.equal(f.get('step-poster').getAttribute('aria-current'),null);
  for(const [id,value] of Object.entries({organiser:'Club',date:'2099-11-11',start_time:'16:00',venue:'Hall',fee:'Free'}))f.get(id).value=value;
  f.input('fee');
  assert.equal(f.get('step-details').dataset.state,'done');
  assert.match(f.get('step-details').getAttribute('aria-label'),/completed/);
  assert.equal(f.get('step-confirm').getAttribute('aria-current'),'step');
  f.get('item-type').value='announcement'; f.get('item-type').onchange();
  assert.equal(f.get('step-details').dataset.state,'current','Announcement requires a description');
  f.get('description').value='Join the campus showcase.'; f.input('description');
  assert.equal(f.get('step-details').dataset.state,'done');
  f.get('description').value=Array(41).fill('word').join(' '); f.input('description');
  assert.equal(f.get('step-details').dataset.state,'current','Overlong description must remain incomplete');
  f.get('item-type').value='achievement'; f.get('item-type').onchange();
  for(const id of ['team','competition','result'])f.get(id).value='Confirmed';
  f.get('description').value=''; f.input('result');
  assert.equal(f.get('step-details').dataset.state,'current','Achievement needs consent');
  f.get('consent_confirmed').checked=true; f.input('consent_confirmed');
  assert.equal(f.get('step-details').dataset.state,'done');
  console.log('PASS progress completeness, optional poster, consent, word limit and accessible states');

  const g=fixture(async (route, options={})=> {
    if(route.endsWith('/club'))return {rows:[]};
    if(options.method==='POST')return {id:'draft',status:'draft',issues:['Missing: start_time','Missing: summary']};
    return {poster_url:null};
  });
  await g.ready();
  await g.get('submission-form').onsubmit({preventDefault(){}});
  const help=g.get('validation-help');
  assert.equal(help.hidden,false);
  const links=help.children[1].children.map(item=>item.children[0]);
  assert.equal(links[0].children[0],'Add a start time (Malaysia time).');
  assert.equal(links[0].href,'#start_time');
  links[0].onclick({preventDefault(){}});
  assert.equal(g.get('start_time').focused,true);
  assert.equal(g.get('start_time').getAttribute('aria-invalid'),'true');
  assert.equal(g.get('description').getAttribute('aria-describedby'),'description-count issue-description');
  g.input('description');
  assert.equal(g.get('description').getAttribute('aria-invalid'),null);
  assert.equal(g.get('description').getAttribute('aria-describedby'),'description-count','Correction preserves existing help');
  console.log('PASS actionable validation messages, field focus and accessible error associations');

  for (const hostedDemo of [false, true]) {
    const submitted=fixture(async (route, options={})=> {
      if(route.endsWith('/club'))return {rows:[]};
      if(options.method==='POST')return {id:'pending',status:'pending',issues:[]};
      return {poster_url:null};
    }, hostedDemo);
    await submitted.ready();
    await submitted.get('submission-form').onsubmit({preventDefault(){}});
    const next=submitted.get('saved').children.find(node=>node.tag==='ol');
    const copy=next.children.map(node=>node.children.join(' ')).join(' ');
    if(hostedDemo) {
      assert.match(copy,/Student Affairs reviews it in this demo\./);
      assert.match(copy,/After approval, build and publish a preview to the demo outbox\./);
      assert.doesNotMatch(copy,/by email|Monday 8am/);
    } else {
      assert.match(copy,/returns it with a reason by email/);
      assert.match(copy,/Monday 8am/);
    }
  }
  console.log('PASS hosted submission guidance and unchanged local delivery copy');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
