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
    pipeline:{run_id:'verified-run',start_date:'2022-09-01',end_date:'2022-09-01',refresh_time:'2026-10-06'}};
}

async function harness({configured=true,authenticated=true,stored={},search='',hash='',configuration=CONFIG,fetchHook}={}) {
  const elements=new Map(Array.from(html.matchAll(/id="([^"]+)"/g),match=>[match[1],new Element()]));
  const el=id=>elements.get(id);
  el('ingestion-flow').mapNodes=Array.from(html.matchAll(/data-stage="([^"]+)"/g),match=>{
    const node=new Element();node.dataset.stage=match[1];node.statusElement=new Element();return node;
  });
  el('ingestion-size').value='demo';el('ingestion-subset').value='HI-Small';
  el('split').value='test';el('scoring').value='rules';
  const storage=new Map(Object.entries(stored).map(([key,value])=>['is459-ingestion-'+key,JSON.stringify(value)]));
  if(authenticated) storage.set('is459-ingestion-auth',JSON.stringify({access_token:'fixture-access-token',
    expires_at:Date.now()+3600000,client_id:configuration.client_id}));
  const calls=[],timers=new Map(),listeners={};let timerId=0;
  const doc={hidden:false,getElementById:el,createElement:()=>new Element(),
    querySelector:()=>new Element(),querySelectorAll:()=>[],
    // Like a browser, every registered listener runs (ingestion and incoming views both pause on hide).
    addEventListener:(name,fn)=>{const previous=listeners[name];listeners[name]=previous?()=>{previous();fn();}:fn;}};
  const location={origin:'https://example.cloudfront.net',pathname:'/',search,hash,assign:url=>{location.destination=url;}};
  const context={document:doc,location,window:{},console,TextEncoder,TextDecoder,Intl,URL,URLSearchParams,Date,Number,String,Array,Math,JSON,Boolean,
    crypto:webcrypto,AbortController,Blob,btoa:binary=>Buffer.from(binary,'binary').toString('base64'),
    sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    history:{replaceState:(_state,_title,url)=>{location.cleaned=url;}},
    setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,init={})=>{
      calls.push({url,init});const custom=fetchHook?await fetchHook(url,init):null;if(custom) return custom;
      if(url==='/ingestion-config.json') return respond(configured?configuration:{},configured?200:404);
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

test('custom rows preview starts no workflow and submission preserves script flags',async()=>{
  let submitted;
  const h=await harness({fetchHook:async(url,init)=>{
    if(url===CONFIG.api_url+'/runs' && init.method==='POST') {submitted=JSON.parse(init.body);return respond(run(),202);}
    if(url===CONFIG.api_url+'/runs/'+RUN_ID) return respond(run());
  }});
  h.el('ingestion-size').value='custom';h.el('ingestion-rows').value='2500';h.el('ingestion-size').onchange();
  h.el('ingestion-version').value='8';h.el('ingestion-refresh').checked=true;h.el('ingestion-rerun').checked=true;
  h.el('ingestion-preview').onclick();
  assert.match(h.el('ingestion-preview-output').textContent,/-Rows 2500 -DatasetVersion 8 -Refresh -Rerun/);
  assert.equal(h.calls.some(call=>call.init.method==='POST'),false);
  await h.el('ingestion-form').onsubmit({preventDefault(){}});
  assert.equal(submitted.rows,2500);assert.equal(submitted.dataset_version,8);
  assert.equal(submitted.refresh,true);assert.equal(submitted.rerun,true);assert.equal(submitted.train,false);
});

test('IAM helper consumes its fragment once and enables controls without AWS credentials',async()=>{
  const configuration={schema_version:1,api_url:CONFIG.api_url,auth_mode:'aws-iam-helper',client_id:'is459-g3-aws-operator',redirect_uri:CONFIG.redirect_uri};
  const h=await harness({configuration,authenticated:false,hash:'#operator_code='+'a'.repeat(43),fetchHook:async(url)=>
    url===CONFIG.api_url+'/session/exchange'?respond({access_token:'fixture-session',expires_in:3600,token_type:'Bearer'}):null});
  assert.equal(h.location.cleaned,'/');assert.equal(h.el('ingestion-start').disabled,false);
  const calls=h.calls.filter(call=>call.url.endsWith('/session/exchange'));
  assert.equal(calls.length,1);assert.deepEqual(JSON.parse(calls[0].init.body),{code:'a'.repeat(43)});
  assert.equal(h.calls.some(call=>call.url.includes('cognito')||call.url.includes('sts.')),false);
  await h.el('ingestion-signout').onclick();
  assert.equal(h.storage.has('is459-ingestion-auth'),false);
  assert.equal(h.calls.some(call=>call.url.endsWith('/session/logout')),true);
});


test('later evaluation labels follow Medium and Small snapshot dates without sparse claims',async()=>{
  const h=await harness();
  h.el('split').options=['test','validation','tail'].map(value=>Object.assign(new Element(),{value}));
  vm.runInContext("snapshot={pipeline:{end_date:'2022-09-28'}};updateEvaluationLabels()",h.context);
  assert.equal(h.el('split').options[2].textContent,'Later period · Sep 11–28');
  assert.match(h.el('evaluation-methods').textContent,/Later period: Sep 11–28/);
  // Without a pipeline end date at all, the later period falls back to the fixed default.
  vm.runInContext("snapshot={};updateEvaluationLabels()",h.context);
  assert.equal(h.el('split').options[2].textContent,'Later period · Sep 11–18');
  assert.equal(h.el('split').options[0].textContent,'Test · Sep 9–10');
});


test('evaluation labels and options follow the snapshot splits and hide a missing tail',async()=>{
  const h=await harness();
  h.el('split').options=['test','validation','tail'].map(value=>Object.assign(new Element(),{value}));
  h.el('split').value='tail';
  vm.runInContext(`snapshot={splits:{train:['2022-09-01','2022-09-07'],validation:['2022-09-07','2022-09-09'],test:['2022-09-09','2022-09-19']},pipeline:{end_date:'2022-09-18'}};updateEvaluationLabels()`,h.context);
  assert.deepEqual(h.el('split').options.slice(0,2).map(option=>option.textContent),['Test · Sep 9–18','Validation · Sep 7–8']);
  assert.deepEqual(h.el('split').options.map(option=>option.hidden),[false,false,true]);
  assert.equal(h.el('split').value,'test');
  assert.match(h.el('evaluation-methods').textContent,/Training reference: Sep 1–6; validation: Sep 7–8; test: Sep 9–18\./);
});

test('split windows are formatted across months and stretched to the last day',async()=>{
  const h=await harness();
  const text=expression=>vm.runInContext(expression,h.context);
  text(`snapshot={splits:{train:['2022-09-01','2022-09-20'],validation:['2022-09-20','2022-09-28'],test:['2022-09-28','2022-10-04']},pipeline:{end_date:'2022-10-06'}}`);
  assert.equal(text(`windowText(['2022-09-28','2022-10-04'])`),'Sep 28–Oct 3');
  assert.equal(text(`windowText(['2022-09-07','2022-09-08'])`),'Sep 7');
  assert.equal(text(`evaluationPeriodLabel('test')`),'Test · Sep 28–Oct 6');
  assert.equal(text(`evaluationPeriodLabel('validation')`),'Validation · Sep 20–27');
});

test('scoring coverage reconciles all Medium rows independently of the selected evaluation filter',async()=>{
  const h=await harness();
  vm.runInContext(`snapshot={aml_scope:'full',full_rows:31898238,pipeline:{subset:'HI-Medium',end_date:'2022-09-28'},model:{views:{validation:{metrics:{rows:3861625}},test:{metrics:{rows:3452878}},tail:{metrics:{rows:11579238}}}}};renderScoringCoverage()`,h.context);
  assert.equal(h.el('scoring-coverage').hidden,false);
  assert.equal(h.el('scoring-coverage-title').textContent,'All 31,898,238 transactions scored');
  for(const count of ['31,898,238','18,893,741','13,004,497']) assert.match(h.el('scoring-coverage-kpis').innerHTML,new RegExp(count));
  assert.match(h.el('scoring-coverage-explanation').textContent,/HI-Small and reused for HI-Medium without retraining/);
  assert.match(h.el('scoring-coverage-periods').innerHTML,/Later period · Sep 11–28/);
  assert.match(h.el('scoring-coverage-periods').innerHTML,/18,893,741 evaluated \+ 13,004,497 excluded/);
  h.el('split').value='tail';vm.runInContext('renderScoringCoverage()',h.context);
  assert.equal(h.el('scoring-coverage-title').textContent,'All 31,898,238 transactions scored');
});

test('scoring coverage hides unsupported or inconsistent populations',async()=>{
  const h=await harness();
  for(const data of [{aml_scope:'cohort',full_rows:1000},{aml_scope:'full',full_rows:1000,model:{views:{test:{metrics:{rows:2000}},validation:{metrics:{rows:0}},tail:{metrics:{rows:0}}}}}]) {
    vm.runInContext('snapshot='+JSON.stringify(data)+';renderScoringCoverage()',h.context);
    assert.equal(h.el('scoring-coverage').hidden,true);
  }
});

const STREAM_CONFIG={schema_version:1,run_id:'stream-smoke-20261006',snapshot_url:'/streaming/stream-smoke-20261006/snapshot.json'};
const streamRow=(rank,id,score)=>({rank,transaction_id:id,timestamp:'2022-09-05T14:32:00',scored_at:rank===1?null:'2026-10-06T00:20:00Z',model_score:score,amount_usd_asof:1234.5,
  payment_format:'ACH',payment_currency:'US Dollar',receiving_currency:'US Dollar',from_bank:'001',from_account:'••••BD30',to_bank:'030',to_account:'••••ABCD',
  txn_count_1h:2,txn_count_24h:5,txn_count_7d:9,distinct_counterparties_24h:3,distinct_counterparties_7d:4});
function streamSnapshot(overrides={}) {
  const now=new Date().toISOString();
  return {schema_version:1,run_id:STREAM_CONFIG.run_id,generated_at:now,state:'running',reconciled:false,consumer_enabled:true,cohort:'fixture',
    expected:884,processed:412,senders:{expected:32,started:20,complete:10},replay_position:'2022-09-05T14:32:00',max_score:.9731,top_k:100,
    queue:[streamRow(1,'HI-Small:7',.9731),streamRow(2,'HI-Small:<img src=x>',.5)],latest:[streamRow(null,'HI-Small:9',.1)],last_progress_at:now,
    progress:[{at:new Date(Date.now()-30000).toISOString(),processed:300},{at:now,processed:412}],
    model:{manifest_sha256:'f'.repeat(64),score_meaning:'ranking-score-not-calibrated-probability'},...overrides};
}
const streamHook=(body,{config=STREAM_CONFIG}={})=>async url=>{
  if(url==='/streaming-config.json') return respond(config);
  if(url===STREAM_CONFIG.snapshot_url) return typeof body==='function'?body():respond(body);
};
const snapshotCalls=h=>h.calls.filter(call=>call.url===STREAM_CONFIG.snapshot_url);

test('incoming view stays hidden and unfetched without streaming configuration',async()=>{
  const h=await harness({configured:false,authenticated:false});
  assert.equal(h.el('incoming-tab').hidden,true);
  assert.equal(h.calls.some(call=>call.url.startsWith('/streaming/')),false);
});

test('incoming view renders committed scores and polls only while open and visible',async()=>{
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot())});
  assert.equal(h.el('incoming-tab').hidden,false);
  assert.equal(snapshotCalls(h).length,0);
  h.el('incoming-tab').onclick();await settle();
  assert.equal(h.el('incoming').hidden,false);assert.equal(h.el('status').hidden,true);
  assert.equal(h.el('incoming-state').textContent,'Live · scoring');
  assert.match(h.el('incoming-kpis').innerHTML,/412 \/ 884/);
  assert.match(h.el('incoming-queue').innerHTML,/HI-Small:7/);
  assert.match(h.el('incoming-queue').innerHTML,/HI-Small:&lt;img src=x&gt;/);
  assert.doesNotMatch(h.el('incoming-queue').innerHTML,/<img/);
  assert.match(h.el('incoming-latest').innerHTML,/HI-Small:9/);
  assert.match(h.el('incoming-latest').innerHTML,/<th scope="col">Scored at<\/th>/);
  assert.match(h.el('incoming-latest').innerHTML,new RegExp(new Date('2026-10-06T00:20:00Z').toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'})));
  assert.match(h.el('incoming-progressbar').innerHTML,/width:46\.6%/);
  assert.equal(h.el('incoming-progressbar').attributes['aria-valuenow'],'412');
  assert.equal(snapshotCalls(h).every(call=>call.init.cache==='no-store' && !call.init.method && call.init.headers===undefined),true);
  assert.ok([...h.timers.values()].some(timer=>timer.ms===10000));
  const seen=snapshotCalls(h).length;
  h.doc.hidden=true;h.listeners.visibilitychange();assert.equal(h.timers.size,0);
  h.doc.hidden=false;h.listeners.visibilitychange();await settle();
  assert.equal(snapshotCalls(h).length,seen+1);
  // Leaving the view: pending timers fire without fetching and do not reschedule.
  h.el('aml-tab').onclick();
  for(const timer of [...h.timers.values()]) await timer.fn();
  await settle();
  assert.equal(snapshotCalls(h).length,seen+1);
  assert.equal(h.calls.some(call=>call.init.method==='POST'),false);
});

test('stale snapshots and failed refreshes keep the last published scores',async()=>{
  let fail=false;
  const old=streamSnapshot({generated_at:new Date(Date.now()-300000).toISOString()});
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(()=>fail?respond({},503):respond(old))});
  h.el('incoming-tab').onclick();await settle();
  assert.equal(h.el('incoming-state').textContent,'Stale');
  assert.equal(h.el('incoming-state').dataset.state,'failed');
  assert.match(h.el('incoming-message').textContent,/No successful update for 5 min/);
  fail=true;await h.el('incoming-refresh').onclick();await settle();
  assert.match(h.el('incoming-message').textContent,/request failed \(503\)\. The last published scores are retained/);
  assert.match(h.el('incoming-queue').innerHTML,/HI-Small:7/);
  assert.ok([...h.timers.values()].some(timer=>timer.ms===20000));
});

test('a completed stream is never stale and is checked less often',async()=>{
  const done=streamSnapshot({state:'completed',reconciled:true,processed:884,senders:{expected:32,started:32,complete:32},
    generated_at:new Date(Date.now()-86400000).toISOString()});
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(done)});
  h.el('incoming-tab').onclick();await settle();
  assert.equal(h.el('incoming-state').textContent,'All scored');
  assert.match(h.el('incoming-message').textContent,/All 884 incoming transactions and 32 sender histories reconcile/);
  assert.ok([...h.timers.values()].some(timer=>timer.ms===60000));
});

test('a receiver-feature run shows receiver histories and incoming activity columns',async()=>{
  const row={...streamRow(1,'HI-Small:7',.42),in_txn_count_1h:1,in_txn_count_24h:6,in_txn_count_7d:19,distinct_senders_24h:4,distinct_senders_7d:11};
  const done=streamSnapshot({state:'completed',reconciled:true,processed:884,senders:{expected:32,started:32,complete:32},
    receivers:{expected:113,preloaded:113,complete:113},queue:[row],generated_at:new Date(Date.now()-86400000).toISOString()});
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(done)});
  h.el('incoming-tab').onclick();await settle();
  assert.match(h.el('incoming-message').textContent,/All 884 incoming transactions, 32 sender histories and 113 receiver histories reconcile/);
  assert.match(h.el('incoming-kpis').innerHTML,/Complete receiver histories<\/span><strong>113 \/ 113<\/strong>/);
  assert.match(h.el('incoming-queue').innerHTML,/<th scope="col">Receiver payments 1h \/ 24h \/ 7d<\/th>/);
  assert.match(h.el('incoming-queue').innerHTML,/<td>1 \/ 6 \/ 19<\/td><td>4 \/ 11<\/td>/);
  // A sender-only snapshot keeps its original columns.
  const old=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot({}))});
  old.el('incoming-tab').onclick();await settle();
  assert.doesNotMatch(old.el('incoming-queue').innerHTML,/Receiver payments/);
  assert.doesNotMatch(old.el('incoming-kpis').innerHTML,/receiver histories/);
});

const REVIEW={cutoff:.9937289953,capacity:100,period:'2022-09-07 to 2022-09-08',source_run:'opt-full-20261006-aws1'};
const VERIFIED={transactions:884,features_exact:true,max_absolute_score_difference:2.9103830456733704e-11,verified_on:'2026-10-06',
  evidence:'planning/evidence/streaming-deployment-result-20261006.json'};

test('review cutoff and batch parity show as cards and a review column',async()=>{
  const flagged=streamSnapshot({review:{...REVIEW,flagged:1},verification:VERIFIED,
    queue:[{...streamRow(1,'HI-Small:7',.995),review_now:true},{...streamRow(2,'HI-Small:8',.5),review_now:false}]});
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(flagged)});
  h.el('incoming-tab').onclick();await settle();
  const kpis=h.el('incoming-kpis').innerHTML;
  assert.match(kpis,/Review now<\/span><strong>1 \/ 412<\/strong><span>Score ≥ 0\.9937 · batch top-100 cutoff/);
  assert.match(kpis,/Matches batch model<\/span><strong>884 \/ 884<\/strong><span>Max score difference 2\.9e-11 · recorded 2026-10-06/);
  assert.match(h.el('incoming-queue').innerHTML,/<th scope="col">Review<\/th>/);
  assert.match(h.el('incoming-queue').innerHTML,/HI-Small:7<\/td>.*<td>Review now<\/td>/);
  assert.match(h.el('incoming-queue').innerHTML,/HI-Small:8<\/td>.*<td>Below cutoff<\/td>/);
  assert.match(h.el('incoming-latest').innerHTML,/<th scope="col">Review<\/th>/);
  assert.match(h.el('incoming-queue-note').textContent,/1 reach the batch top-100 review cutoff \(score ≥ 0\.9937, set on 2022-09-07 to 2022-09-08 validation days\)/);
  // A cutoff from another model's batch days names them.
  const medium=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot({review:{...REVIEW,cutoff:.9953691363,period:'2022-09-15 to 2022-09-28',days:'HI-Medium batch',flagged:0}}))});
  medium.el('incoming-tab').onclick();await settle();
  assert.match(medium.el('incoming-queue-note').textContent,/score ≥ 0\.9954, set on 2022-09-15 to 2022-09-28 HI-Medium batch days/);
  // Nothing at the cutoff is stated plainly rather than hidden.
  const none=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot({review:{...REVIEW,flagged:0}}))});
  none.el('incoming-tab').onclick();await settle();
  assert.match(none.el('incoming-kpis').innerHTML,/<strong>0 \/ 412<\/strong>/);
  assert.match(none.el('incoming-queue-note').textContent,/None reach the batch top-100 review cutoff/);
});

test('snapshots without or with malformed review fields render as before',async()=>{
  for(const extra of [{},{review:{...REVIEW,cutoff:'0.99',flagged:1},verification:{...VERIFIED,features_exact:false}},{review:null,verification:'yes'}]) {
    const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot(extra))});
    h.el('incoming-tab').onclick();await settle();
    assert.equal(h.el('incoming-state').textContent,'Live · scoring');
    assert.match(h.el('incoming-kpis').innerHTML,/412 \/ 884/);
    assert.doesNotMatch(h.el('incoming-kpis').innerHTML,/Review now|Matches batch model/);
    assert.doesNotMatch(h.el('incoming-queue').innerHTML+h.el('incoming-latest').innerHTML,/>Review</);
    assert.doesNotMatch(h.el('incoming-queue-note').textContent,/cutoff/);
    assert.match(h.el('incoming-queue').innerHTML,/HI-Small:7/);
  }
});

test('invalid streaming configuration or snapshot is ignored',async()=>{
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot(),
    {config:{...STREAM_CONFIG,snapshot_url:'https://evil.example/snapshot.json'}})});
  assert.equal(h.el('incoming-tab').hidden,true);
  assert.equal(h.calls.some(call=>call.url.includes('evil')),false);
  const other=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot({run_id:'another-run'}))});
  other.el('incoming-tab').onclick();await settle();
  assert.match(other.el('incoming-message').textContent,/unexpected snapshot format/);
  assert.equal(other.el('incoming-queue').innerHTML,'');
});

test('a long commit history is thinned to a readable chart that keeps its first and last points',async()=>{
  // Midday UTC: 6 Oct in every common time zone, so the date label is stable.
  const start=Date.parse('2026-10-06T12:00:00Z');
  const progress=Array.from({length:240},(_,index)=>({at:new Date(start+index*4000).toISOString(),processed:index+1}));
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(streamSnapshot({progress}))});
  h.el('incoming-tab').onclick();await settle();
  const chart=h.el('incoming-progress').innerHTML;
  const marks=chart.match(/<circle/g).length;
  assert.ok(marks>=20 && marks<=25,`expected about 24 points, got ${marks}`);
  assert.match(chart,/Transactions scored · [^<]*: 1</);
  assert.match(chart,/Transactions scored · [^<]*: 240</);
  assert.match(chart,/Time scored · 6 Oct 2026/);
});

test('refresh shows when the last successful check happened, even with nothing new',async()=>{
  let release=null, fail=false;
  const done=streamSnapshot({state:'completed',reconciled:true,processed:884});
  const h=await harness({configured:false,authenticated:false,fetchHook:streamHook(()=>fail?respond({},503):
    release?new Promise(resolve=>{const go=release;release=null;go(()=>resolve(respond(done)));}):respond(done))});
  h.el('incoming-tab').onclick();await settle();
  assert.match(h.el('incoming-checked').textContent,/^Checked \d\d:\d\d:\d\d$/);
  h.el('incoming-checked').textContent='Checked earlier';
  let finish;release=resolve=>{finish=resolve;};
  const refresh=h.el('incoming-refresh').onclick();await settle();
  assert.equal(h.el('incoming-refresh').textContent,'Checking…');
  assert.equal(h.el('incoming-refresh').disabled,true);
  finish();await refresh;await settle();
  assert.equal(h.el('incoming-refresh').textContent,'Refresh now');
  assert.match(h.el('incoming-checked').textContent,/^Checked \d\d:\d\d:\d\d$/);
  const checked=h.el('incoming-checked').textContent;
  fail=true;await h.el('incoming-refresh').onclick();await settle();
  assert.equal(h.el('incoming-checked').textContent,checked);
  assert.match(h.el('incoming-message').textContent,/request failed \(503\)/);
});
