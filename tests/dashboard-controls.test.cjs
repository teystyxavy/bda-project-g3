/* Offline browser-logic tests. All network responses and sessions are fixtures. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard/dashboard.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../dashboard/index.html'),'utf8');
const CONFIG={schema_version:1,api_url:'https://example.execute-api.ap-southeast-1.amazonaws.com',
  cognito_domain:'https://example.auth.ap-southeast-1.amazoncognito.com',client_id:'publicclientid',redirect_uri:'https://example.cloudfront.net/'};
const RUN_ID='ui-aaaaaaaaaaaa-bbbbbbbbbbbb';

class Element {
  constructor() {this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.dataset={};this.options=[];this.children=[];this.rows=[];this.attributes={};}
  set innerHTML(text) {
    this.html=text;
    const body=text.match(/<tbody>([\s\S]*)<\/tbody>/)?.[1]||'';
    this.rows=Array.from(body.matchAll(/<tr>/g),()=>({firstChild:new Element()}));
  }
  get innerHTML() {return this.html||'';}
  setAttribute(name,value) {this.attributes[name]=value;}
  replaceChildren(...children) {this.children=children;this.options=children;}
  appendChild(child) {this.children.push(child);if(this.options!==this.children) this.options.push(child);}
  querySelectorAll(selector) {return selector==='tbody tr'?this.rows:selector==='[data-stage]'?this.mapNodes:[];}
  closest() {return this;}
  querySelector(selector) {return selector==='.map-status'?this.statusElement:new Element();}
}
const respond=(value,status=200)=>({ok:status>=200&&status<300,status,
  json:async()=>value,arrayBuffer:async()=>new TextEncoder().encode(JSON.stringify(value)).buffer});
const settle=async()=>{for(let i=0;i<12;i++) await new Promise(setImmediate);};

function run(status='RUNNING') {
  return {run_id:RUN_ID,status,elapsed_seconds:42,checked_at:'2026-10-06T12:00:00Z',
    selection:{subset:'HI-Small',rows:1000,dataset_version:null,train:false},
    stages:[{id:'acquire',label:'Acquire dataset and historical FX',status:'complete'},
      {id:'curate',label:'Clean transactions and compute features',status:status==='FAILED'?'failed':'running',active_service:'AWS Glue'},
      {id:'score',label:'Score transactions',status:'pending'}],
    ...(status==='SUCCEEDED'?{release_url:'https://example.cloudfront.net/releases/verified-run/index.html'}:{})};
}

function snapshot() {
  const metrics={rows:1000,k:100,positives:1,precision_at_k:0,recall_at_k:0,average_precision:0,lift_at_k:0};
  const view={metrics,flagged:0,daily:[],histogram:[],queue:[]};
  return {schema_version:1,generated_at:'2026-10-06',full_rows:1000,cohort_rows:1000,aml_scope:'full',
    aml:{test:view,validation:view,tail:view},diagnostics:{variants:[],top100_reasons:[]},
    treasury_daily:[{day:'2022-09-01',volume_usd:5,hhi:1}],
    treasury:{all:{transactions:1000,volume_usd:5,top5_share:1,bank_pairs:1,hhi:1,banks:[],currencies:[]}},
    pipeline:{run_id:'verified-run',start_date:'2022-09-01',end_date:'2022-09-01',refresh_time:'2026-10-06'}};
}

async function harness({configured=true,authenticated=true,stored={},search='',fetchHook}={}) {
  const elements=new Map(Array.from(html.matchAll(/id="([^"]+)"/g),match=>[match[1],new Element()]));
  const el=id=>elements.get(id);
  el('ingestion-flow').mapNodes=Array.from(html.matchAll(/data-stage="([^"]+)"/g),match=>{
    const node=new Element();node.dataset.stage=match[1];node.statusElement=new Element();return node;
  });
  el('ingestion-size').value='demo';el('ingestion-subset').value='HI-Small';
  el('split').value='test';el('scoring').value='rules';el('day').value='all';el('day').options=[new Element()];
  const storage=new Map(Object.entries(stored).map(([key,value])=>['is459-ingestion-'+key,JSON.stringify(value)]));
  if(authenticated) storage.set('is459-ingestion-auth',JSON.stringify({access_token:'fixture-access-token',
    expires_at:Date.now()+3600000,client_id:CONFIG.client_id}));
  const calls=[],timers=new Map(),listeners={};let timerId=0;
  const doc={hidden:false,getElementById:el,createElement:()=>new Element(),
    querySelector:()=>new Element(),querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners[name]=fn};
  const location={origin:'https://example.cloudfront.net',pathname:'/',search,hash:'',assign:url=>{location.destination=url;}};
  const context={document:doc,location,window:{},console,TextEncoder,TextDecoder,Intl,URL,URLSearchParams,Date,Number,String,Array,Math,JSON,Boolean,
    crypto:webcrypto,AbortController,Blob,btoa:binary=>Buffer.from(binary,'binary').toString('base64'),
    sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    history:{replaceState:(_state,_title,url)=>{location.cleaned=url;}},
    setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,init={})=>{
      calls.push({url,init});const custom=fetchHook?await fetchHook(url,init):null;if(custom) return custom;
      if(url==='/ingestion-config.json') return respond(configured?CONFIG:{},configured?200:404);
      if(url===CONFIG.api_url+'/options') return respond({notice:'fixture notice',subsets:[
        {value:'HI-Small',label:'HI-Small',supported:true},{value:'LI-Small',label:'LI-Small unavailable',supported:false}]});
      if(url===CONFIG.api_url+'/runs' && !init.method) return respond({runs:[]});
      return respond({message:'Unavailable'},404);
    }};
  context.window=context;
  vm.runInNewContext(source,context,{filename:'dashboard.js'});await settle();
  return {el,doc,location,storage,calls,timers,listeners,context};
}

test('ingestion tab works when results or API are absent and never starts a job',async()=>{
  const h=await harness({configured:false,authenticated:false});
  h.el('ingestion-tab').onclick();
  assert.equal(h.el('ingestion').hidden,false);
  assert.equal(h.el('aml').hidden,true);
  assert.match(h.el('ingestion-message').textContent,/not been connected/);
  assert.equal(h.el('ingestion-start').disabled,true);
  assert.equal(h.el('ingestion-signin').disabled,true);
  assert.equal(h.calls.some(call=>call.init.method==='POST'),false);
});

test('only validated subsets are enabled; demo cannot train a candidate',async()=>{
  const h=await harness();
  assert.equal(h.el('ingestion-start').disabled,false);
  assert.equal(h.el('ingestion-subset').options[1].disabled,true);
  assert.equal(h.el('ingestion-train').disabled,true);
  h.el('ingestion-size').value='full';h.el('ingestion-size').onchange();
  assert.equal(h.el('ingestion-train').disabled,false);
  h.el('ingestion-train').checked=true;h.el('ingestion-size').value='demo';h.el('ingestion-size').onchange();
  assert.equal(h.el('ingestion-train').checked,false);
});

test('lost submission response keeps stable ID and options on retry',async()=>{
  const posts=[];let fail=true;
  const h=await harness({fetchHook:async(url,init)=>{
    if(url===CONFIG.api_url+'/runs' && init.method==='POST') {
      posts.push(JSON.parse(init.body));if(fail) {fail=false;throw new TypeError('Network unavailable');}
      return respond({run_id:RUN_ID,status:'RUNNING'},202);
    }
    if(url===CONFIG.api_url+'/runs/'+RUN_ID) return respond(run());
  }});
  await h.el('ingestion-form').onsubmit({preventDefault(){}});
  assert.equal(h.el('ingestion-start').textContent,'Retry same submission');
  assert.equal(h.el('ingestion-fields').disabled,true);
  await h.el('ingestion-form').onsubmit({preventDefault(){}});
  assert.deepEqual(posts[0],posts[1]);assert.match(posts[0].request_id,/^[0-9a-f]{32}$/);
  assert.equal(h.el('ingestion-start').disabled,true);
  assert.equal(h.el('ingestion-run-state').textContent,'RUNNING');
  assert.equal(h.storage.has('is459-ingestion-pending'),false);
});

test('tracking survives reload, pauses in hidden tabs and retains stale progress',async()=>{
  let fail=false;
  const h=await harness({stored:{active:RUN_ID},fetchHook:async url=>{
    if(url===CONFIG.api_url+'/runs/'+RUN_ID) {if(fail) throw new TypeError('Disconnected');return respond(run());}
  }});
  assert.equal(h.el('ingestion-stages').children[1].dataset.state,'running');
  assert.match(h.el('ingestion-active-services').textContent,/AWS Glue/);
  assert.equal(h.el('ingestion-flow').mapNodes.find(node=>node.dataset.stage==='curate').dataset.state,'running');
  assert.ok([...h.timers.values()].some(timer=>timer.ms===10000));
  h.doc.hidden=true;h.listeners.visibilitychange();assert.equal(h.timers.size,0);
  fail=true;h.doc.hidden=false;h.listeners.visibilitychange();await settle();
  assert.equal(h.el('ingestion-stages').children[1].dataset.state,'running');
  assert.match(h.el('ingestion-message').textContent,/Last known stage is retained/);
  assert.ok([...h.timers.values()].some(timer=>timer.ms===20000));
});

test('successful publication loads immutable results into the current shell',async()=>{
  const h=await harness({stored:{active:RUN_ID},fetchHook:async url=>{
    if(url===CONFIG.api_url+'/runs/'+RUN_ID) return respond(run('SUCCEEDED'));
    if(url==='/releases/verified-run/dashboard.json') return respond(snapshot());
  }});
  assert.match(h.el('ingestion-run-note').textContent,/Verified results loaded/);
  assert.match(h.el('status').textContent,/verified-run/);
  await h.el('ingestion-results').onclick();
  assert.equal(h.el('aml').hidden,false);assert.equal(h.el('ingestion').hidden,true);
  assert.equal(h.timers.size,0);
});

test('failed run keeps existing results and does not fetch a new release',async()=>{
  const h=await harness({stored:{active:RUN_ID},fetchHook:async url=>{
    if(url===CONFIG.api_url+'/runs/'+RUN_ID) return respond({...run('FAILED'),message:'Run stopped before publication.'});
  }});
  assert.equal(h.el('ingestion-results').hidden,true);
  assert.equal(h.el('ingestion-stages').children[1].dataset.state,'failed');
  assert.equal(h.el('ingestion-flow').mapNodes.find(node=>node.dataset.stage==='curate').dataset.state,'failed');
  assert.equal(h.calls.some(call=>call.url.startsWith('/releases/')),false);
});

test('PKCE sign-in uses code flow, matching state and no client secret',async()=>{
  const h=await harness({authenticated:false});await h.el('ingestion-signin').onclick();
  const url=new URL(h.location.destination),pending=JSON.parse(h.storage.get('is459-ingestion-oauth'));
  assert.equal(url.searchParams.get('response_type'),'code');
  assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  assert.equal(url.searchParams.get('state'),pending.state);
  assert.equal(url.searchParams.has('client_secret'),false);
  assert.equal(h.calls.some(call=>call.url.includes('/oauth2/token')),false);
});

test('callback with wrong state strips code from URL and refuses token exchange',async()=>{
  const h=await harness({authenticated:false,search:'?code=privatecode&state=wrong',
    stored:{oauth:{state:'expected',client_id:CONFIG.client_id,verifier:'fixture',created_at:Date.now()}}});
  assert.match(h.el('ingestion-message').textContent,/could not be verified/);
  assert.equal(h.location.cleaned,'/');
  assert.equal(h.calls.some(call=>call.url.includes('/oauth2/token')),false);
});

test('401 expires the local session and stops automatic polling',async()=>{
  const h=await harness({stored:{active:RUN_ID},fetchHook:async url=>{
    if(url===CONFIG.api_url+'/runs/'+RUN_ID) return respond({message:'Sign in again'},401);
  }});
  assert.equal(h.storage.has('is459-ingestion-auth'),false);
  assert.equal(h.el('ingestion-start').disabled,true);
  assert.equal(h.timers.size,0);
});

const STATUS_URL='https://statusapi.execute-api.ap-southeast-1.amazonaws.com/status';
const statusHook=(statusBody,{config={schema_version:1,status_url:STATUS_URL},code=200}={})=>async url=>{
  if(url==='/status-config.json') return respond(config,config?200:404);
  if(url===STATUS_URL) return respond(statusBody,code);
};
const PUBLIC={schema_version:1,read_only:true,checked_at:'2026-10-06T12:00:00Z',
  latest:{...run('RUNNING'),run_id:'opt-full-20261006-aws1'},
  runs:[{run_id:'opt-full-20261006-aws1',status:'RUNNING',started_at:'2026-10-06T11:59:00Z',finished_at:null}]};

test('without controls, public status is read-only and only fetched while the tab is open',async()=>{
  const h=await harness({configured:false,authenticated:false,fetchHook:statusHook(PUBLIC)});
  assert.equal(h.el('ingestion-new').hidden,true);
  assert.equal(h.el('ingestion-signin').hidden,true);
  assert.equal(h.el('ingestion-connection').textContent,'Read-only status');
  h.el('ingestion-tab').onclick();await settle();
  assert.equal(h.el('ingestion').hidden,false);
  assert.equal(h.el('ingestion-run-id').textContent,'opt-full-20261006-aws1');
  assert.equal(h.el('ingestion-stages').children[1].dataset.state,'running');
  assert.equal(h.el('ingestion-flow').mapNodes.find(node=>node.dataset.stage==='curate').dataset.state,'running');
  assert.match(h.el('ingestion-active-services').textContent,/AWS Glue/);
  assert.match(h.el('ingestion-history').innerHTML,/opt-full-20261006-aws1/);
  assert.match(h.el('ingestion-message').textContent,/Read-only view/);
  const statusCalls=h.calls.filter(call=>call.url===STATUS_URL);
  assert.equal(statusCalls.every(call=>call.init.headers===undefined && !call.init.method),true);
  assert.equal(h.calls.some(call=>call.init.method==='POST'),false);
  assert.equal([...h.timers.values()].some(timer=>timer.ms===10000),true);
  // Hidden tab: pending timers fire without contacting AWS and do not reschedule.
  h.el('aml-tab').onclick();
  for(const timer of [...h.timers.values()]) await timer.fn();
  await settle();
  assert.equal(h.calls.filter(call=>call.url===STATUS_URL).length,statusCalls.length);
});

test('public status failure keeps the last stage and backs off',async()=>{
  let fail=false;
  const hook=statusHook(PUBLIC);
  const h=await harness({configured:false,authenticated:false,fetchHook:async url=>
    fail && url===STATUS_URL?respond({message:'AWS status is temporarily unavailable.'},503):hook(url)});
  h.el('ingestion-tab').onclick();await settle();
  fail=true;await h.el('ingestion-refresh-status').onclick();await settle();
  assert.match(h.el('ingestion-message').textContent,/temporarily unavailable.*Last known stage is retained/);
  assert.equal(h.el('ingestion-stages').children[1].dataset.state,'running');
  assert.equal([...h.timers.values()].some(timer=>timer.ms===20000),true);
});

test('invalid status configuration is ignored',async()=>{
  const h=await harness({configured:false,authenticated:false,
    fetchHook:statusHook(PUBLIC,{config:{schema_version:1,status_url:'https://evil.example/status'}})});
  h.el('ingestion-tab').onclick();await settle();
  assert.equal(h.calls.some(call=>call.url.includes('evil')),false);
  assert.match(h.el('ingestion-message').textContent,/not been connected/);
  assert.equal(h.el('ingestion-connection').textContent,'Setup pending');
});

test('signed-in operators keep the controls instead of the public view',async()=>{
  const h=await harness({fetchHook:statusHook(PUBLIC)});
  h.el('ingestion-tab').onclick();await settle();
  assert.equal(h.calls.some(call=>call.url===STATUS_URL),false);
  assert.equal(h.el('ingestion-new').hidden,false);
  assert.equal(h.el('ingestion-start').disabled,false);
});
