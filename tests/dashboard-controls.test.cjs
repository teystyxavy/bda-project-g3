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

function networkSection() {
  const group=(accounts,hits)=>({accounts,laundering_accounts:hits,laundering_rate:accounts?hits/accounts:null,lift:accounts?hits/accounts/0.01:null});
  return {question:'Which accounts collect from many and pass it on?',audience:'Network investigations team',
    parameters:{hub_thresholds:[5,10,15],headline_threshold:10,max_links:100,loop_window_days:7,min_ring_accounts:3},
    totals:{rows:1000,self_transfers:10,accounts:500,links:900,laundering_accounts:5,laundering_rate:0.01},
    thresholds:[5,10,15].map(threshold=>({threshold,headline:threshold===10,hubs:group(threshold===15?0:1,threshold===15?0:1),high_volume:group(0,0),fan_in_only:group(4,1),fan_out_only:group(6,1)})),
    loops:{three_account:{loops:1,...group(3,3)},two_account:{pairs:2,...group(4,0)}},
    rings:{rings:1,accounts:4,largest:4,coverage:0.8,laundering_accounts:4,laundering_rate:1,lift:100},
    top_rings:[{rings:1,accounts:4,laundering_rate:1,rings_with_laundering:1}],
    ring_list:[{rank:1,ring_id:'10:A',accounts:4,hubs:1,loops:1,red_flags:2,links:4,txns:9,usd:1234,first_seen:'2022-09-01T00:00:00',last_seen:'2022-09-03T00:00:00',laundering_rate:1}],
    graphs:{'10:A':{nodes:[{id:'10:H',role:'hub',fan_in:10,fan_out:10,in_usd:500,out_usd:450},{id:'10:A',role:'loop',fan_in:1,fan_out:2,in_usd:5,out_usd:6},
      {id:'10:B',role:'loop',fan_in:1,fan_out:1,in_usd:5,out_usd:5},{id:'20:R1',role:'counterparty',fan_in:1,fan_out:0,in_usd:45,out_usd:0}],
      links:[{source:'10:A',target:'10:B',txns:1,usd:5,loop:true},{source:'10:B',target:'10:A',txns:1,usd:5,loop:true},{source:'10:A',target:'10:H',txns:2,usd:9,loop:false},{source:'10:H',target:'20:R1',txns:5,usd:45,loop:false}],
      total_accounts:4,shown_accounts:4,truncated:false}},
    definitions:{hub:'Hub definition.',ranking:'Hubs first.'},verification:{status:'passed'}};
}

function snapshot() {
  const metrics={rows:1000,k:100,positives:1,precision_at_k:0,recall_at_k:0,average_precision:0,lift_at_k:0};
  const view={metrics,flagged:0,daily:[],histogram:[],queue:[]};
  return {schema_version:1,generated_at:'2026-10-06',full_rows:1000,cohort_rows:1000,aml_scope:'full',
    aml:{test:view,validation:view,tail:view},diagnostics:{variants:[],top100_reasons:[]},
    network:networkSection(),
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
    querySelector:()=>new Element(),querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners[name]=fn};
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
  vm.runInContext("snapshot={treasury_daily:[{day:'2022-09-18'}]};updateEvaluationLabels()",h.context);
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


test('mule-network tab renders hubs, loops, rings and the ring node graph from the snapshot',async()=>{
  const h=await harness();
  vm.runInContext(`snapshot=${JSON.stringify(snapshot())};selectDashboardView('network')`,h.context);
  assert.equal(h.el('network').hidden,false);assert.equal(h.el('aml').hidden,true);
  assert.equal(h.el('network-body').hidden,false);assert.equal(h.el('network-empty').hidden,true);
  assert.match(h.el('network-kpis').innerHTML,/Rings to investigate/);
  assert.match(h.el('network-kpis').innerHTML,/Mule-sized hubs \(10\+ in and out\)/);
  assert.match(h.el('threshold-table').innerHTML,/10\+ \(rings\)/);
  assert.match(h.el('ring-list').innerHTML,/10:A/);
  assert.match(h.el('network-context').innerHTML,/✓ Verified.*Glue tables matched an independent recomputation/);
  const svg=h.el('ring-graph').innerHTML;
  assert.match(svg,/<svg id="ring-svg"/);
  assert.equal((svg.match(/class="ring-node/g)||[]).length,4);
  assert.equal((svg.match(/ring-link-loop/g)||[]).length,2);
  // Direction arrows sit mid-link (no pile-up at the hub); without animation the graph is static, with no payment dots running.
  assert.match(svg,/marker-mid="url\(#ring-arrow-loop\)"/);
  assert.match(svg,/<svg id="ring-svg" class="ring-static"/);
  // One to three payment dots per link, more for links with more payments (fixture: 1, 1, 2 and 5 payments).
  assert.equal((svg.match(/data-dot="/g)||[]).length,9);
  assert.equal(h.el('ring-flow').hidden,true);assert.equal(h.el('ring-legend-flow').hidden,true);
  // The deterministic layout keeps every account inside the drawing area.
  const layout=vm.runInContext("ringLayouts['10:A']",h.context);
  for(const point of Object.values(layout)) {assert.ok(point.x>=48&&point.x<=812);assert.ok(point.y>=48&&point.y<=512);}
  // Hub rings read left to right: senders left of the hub, receivers right of it.
  assert.equal(layout['10:H'].x,430);assert.ok(layout['20:R1'].x>430);
  assert.deepEqual(vm.runInContext("JSON.stringify(flowRoles(snapshot.network.graphs['10:A']))",h.context),JSON.stringify({'10:H':'hub','10:A':'loop','10:B':'loop','20:R1':'receiver'}));
  assert.match(h.el('ring-rail').innerHTML,/data-ring="10:A"/);assert.match(h.el('ring-title').textContent,/Ring #1 · lead account 10:H/);
  assert.match(h.el('ring-kpis').innerHTML,/9<\/strong> payments/);
  assert.deepEqual(vm.runInContext("JSON.stringify(layoutRing(snapshot.network.graphs['10:A']))",h.context),JSON.stringify(layout));
  vm.runInContext("findAccount('20:r1')",h.context);
  assert.match(h.el('ring-search-result').textContent,/20:R1 is in ring #1/);
  vm.runInContext("showAccount('10:H')",h.context);
  assert.match(h.el('ring-node').innerHTML,/Hub/);assert.match(h.el('ring-node').innerHTML,/→ 20:R1/);
});

test('ring graph physics settles back on the tested layout and the hover card explains accounts and links',async()=>{
  const h=await harness({authenticated:false});
  vm.runInContext(`snapshot=${JSON.stringify(snapshot())};selectDashboardView('network')`,h.context);
  // Without animation, accounts are drawn exactly at their rest positions.
  assert.equal(vm.runInContext("JSON.stringify(Object.fromEntries(Object.entries(ringScene.pos).map(([id,p])=>[id,{x:p.x,y:p.y}])))",h.context),
    vm.runInContext("JSON.stringify(ringLayouts['10:A'])",h.context));
  // Knock every account off its rest position, then step: the springs bring them back and the energy falls to rest.
  const settled=vm.runInContext(`(()=>{const s=ringScene;for(const id of s.ids){s.pos[id].x+=60;s.pos[id].y-=40;}
    let energy=Infinity,steps=0;while(energy>=.03*s.ids.length&&steps<600){energy=stepRing(s,16.7);steps++;}
    return {steps,error:Math.max(...s.ids.map(id=>Math.hypot(s.pos[id].x-s.layout[id].x,s.pos[id].y-s.layout[id].y)))};})()`,h.context);
  assert.ok(settled.steps<600,'physics did not settle');assert.ok(settled.error<2,`accounts stopped ${settled.error}px from rest`);
  // A dragged account tows its neighbours part of the way, and only part of the way.
  const towed=vm.runInContext(`(()=>{const s=ringScene;s.drag='10:H';s.pos['10:H'].x+=100;for(let i=0;i<300;i++) stepRing(s,16.7);
    const shift=s.pos['20:R1'].x-s.layout['20:R1'].x;s.drag=null;s.pos['10:H'].x-=100;return shift;})()`,h.context);
  assert.ok(towed>20&&towed<100,`receiver moved ${towed}px`);
  // Fit keeps the ring inside the view.
  const view=vm.runInContext('fitRing(ringScene,false),ringScene.view',h.context);
  assert.ok(view.k>=.6&&view.k<=1.6);
  const node=vm.runInContext("ringTipContent(ringScene,{node:'10:H'})",h.context);
  assert.match(node,/Hub/);assert.match(node,/10 senders/);assert.match(node,/In this ring: 1 in · 1 out/);
  assert.match(vm.runInContext('ringTipContent(ringScene,{link:3})',h.context),/Money out of the hub[\s\S]*10:H → 20:R1[\s\S]*5<\/span>/);
  assert.match(vm.runInContext('ringTipContent(ringScene,{link:0})',h.context),/Part of a timed loop/);
  vm.runInContext('toggleRingFlow()',h.context);
  assert.equal(h.el('ring-flow').textContent,'Play flow');assert.equal(h.el('ring-flow').attributes['aria-pressed'],'true');
});

test('releases from before the network analysis explain that BQ2 is missing',async()=>{
  const h=await harness();
  const old=snapshot();delete old.network;
  vm.runInContext(`snapshot=${JSON.stringify(old)};selectDashboardView('network')`,h.context);
  assert.equal(h.el('network-body').hidden,true);assert.equal(h.el('network-empty').hidden,false);
  assert.match(h.el('network-empty').textContent,/before the mule-network analysis/);
});
