/* Offline tests for the BQ1 presenter view. The snapshot is a small fixture; no network is used. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard/dashboard.js'),'utf8');

class Element {
  constructor(registry,id='') {this.registry=registry;this.id=id;this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.dataset={};this.style={};this.attributes={};this.html='';
    this.classes=new Set();this.classList={add:name=>this.classes.add(name),remove:name=>this.classes.delete(name),contains:name=>this.classes.has(name)};}
  set innerHTML(text) {this.html=text;this.registry.adopt(text);}
  get innerHTML() {return this.html;}
  insertAdjacentHTML(_where,text) {this.html+=text;this.registry.adopt(text);}
  setAttribute(name,value) {this.attributes[name]=value;}
  querySelectorAll() {return [];}
  querySelector() {return new Element(this.registry);}
  closest() {return this;}
  focus() {this.registry.focused=this;}
}

function registry() {
  const elements=new Map();
  const reg={focused:null,get:id=>{if(!elements.has(id)) elements.set(id,new Element(reg,id));return elements.get(id);},has:id=>elements.has(id),
    // Elements created from markup start hidden when the markup says so, as in a browser.
    adopt:text=>{for(const match of text.matchAll(/<[a-z]+[^>]*\sid="([^"]+)"[^>]*>/g)) {const el=reg.get(match[1]);el.hidden=/\shidden(?=[\s>])/.test(match[0]);}}};
  return reg;
}

function fixture({queue=100,withNetwork=true}={}) {
  const fields=['transaction_id','timestamp','from_bank','from_account','to_bank','to_account','payment_format','payment_currency','amount_usd_asof','risk_score','model_score','model_evidence','txn_count_1h','distinct_counterparties_24h'];
  const records={}, histories={}, modelQueue=[], rulesQueue=[];
  for(let i=0;i<queue;i++) {
    const id=`HI-Small:${1000+i}`;modelQueue.push(id);
    // Rank 1's sender is in ring A (bank written with leading zeros); rank 3's receiver is in ring B.
    const from=i===0?['0012','RINGA1']:[`0${200+i}`,`S${i}`], to=i===2?['077','RINGB2']:[`0${500+i}`,`R${i}`];
    records[id]=[id,`2022-09-10T1${i%10}:00:00.000`,from[0],from[1],to[0],to[1],'ACH','US Dollar',10000+i,50,0.99-i/1000,'payment_format=ACH: +3.5000 margin; Prior-day USD amount (log): -0.2500 margin',0,4];
    const rid=`HI-Small:${5000+i}`;rulesQueue.push(rid);records[rid]=[rid,'2022-09-09T08:00:00.000',`0${900+i}`,`Q${i}`,'012','RINGA2','Cheque','Euro',500,80,0.01,'',1,1];
  }
  histories['HI-Small:1000']={observed_prior_transactions:12,scope:'Latest eight',events:[
    {transaction_id:'HI-Small:1',timestamp:'2022-09-09T08:00:00.000',to_bank:'0012',to_account:'RINGA1',payment_format:'ACH',amount_usd_asof:900},
    {transaction_id:'HI-Small:2',timestamp:'2022-09-09T12:00:00.000',to_bank:'0300',to_account:'X1',payment_format:'ACH',amount_usd_asof:1200}]};
  const metrics=(k,hits)=>({rows:862792,positives:956,k,precision_at_k:hits/k,recall_at_k:hits/956,lift_at_k:1,average_precision:hits?0.277:0.0068,hits});
  const scope={id:'all',label:'Whole evaluation period',capacity:250,
    model:{metrics_by_k:{'25':metrics(25,25),'50':metrics(50,48),'100':metrics(100,91),'250':metrics(250,175)},histogram:[{label:'0.00–0.50',transactions:862700},{label:'0.50–1.00',transactions:92}],pr_curve:[],daily_by_k:{},queue:modelQueue},
    rules:{metrics_by_k:{'25':metrics(25,0),'50':metrics(50,0),'100':metrics(100,0),'250':metrics(250,1)},histogram:[],pr_curve:[],daily_by_k:{},queue:rulesQueue}};
  const snap={schema_version:1,generated_at:'2026-11-07',cohort_rows:5078345,full_rows:5078345,
    splits:{train:['2022-09-01','2022-09-07'],validation:['2022-09-07','2022-09-09'],test:['2022-09-09','2022-09-11'],tail:['2022-09-11','2022-09-19']},
    model:{name:'hist-depth5-unweighted'},exploration:{aml:{periods:{test:{scopes:[scope]}},record_fields:fields,records,histories}}};
  if(withNetwork) snap.network={totals:{accounts:422734},ring_list:[{ring_id:'A',rank:1},{ring_id:'B',rank:7}],
    graphs:{A:{nodes:[{id:'12:RINGA1',role:'hub'},{id:'12:RINGA2',role:'counterparty'}],links:[{source:'12:RINGA2',target:'12:RINGA1'}]},
      B:{nodes:[{id:'77:RINGB2',role:'loop'}],links:[]}}};
  return snap;
}

async function harness() {
  const reg=registry(), listeners={}, host=reg.get('aml-heading-controls'), body=reg.get('body');
  reg.get('network-present');
  const doc={getElementById:id=>reg.has(id)||!/^(qp|aml-present)/.test(id)?reg.get(id):null,createElement:()=>new Element(reg),body,
    get activeElement() {return reg.get('aml-present');},
    querySelector:selector=>selector==='#aml .heading .controls'?host:new Element(reg),querySelectorAll:()=>[],
    addEventListener:(name,fn)=>{(listeners[name]||=[]).push(fn);}};
  const respond=status=>({ok:false,status,json:async()=>({}),arrayBuffer:async()=>new ArrayBuffer(0)});
  const context={document:doc,location:{origin:'https://example.cloudfront.net',pathname:'/',search:'',hash:''},window:{},console,TextEncoder,TextDecoder,Intl,URL,URLSearchParams,
    Date,Number,String,Array,Math,JSON,Boolean,Map,Set,Object,Float32Array,Uint8Array,sessionStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
    history:{replaceState:()=>{}},setTimeout:()=>0,clearTimeout:()=>{},fetch:async()=>respond(404)};
  context.window=context;
  vm.runInNewContext(source,context,{filename:'dashboard.js'});
  for(let i=0;i<12;i++) await new Promise(setImmediate);
  const run=code=>vm.runInContext(code,context);
  const load=snap=>{context.__snap=snap;run('snapshot=__snap;bq1PresenterRefresh();');};
  const key=k=>listeners.keydown.forEach(fn=>fn({key:k,target:{},preventDefault(){}}));
  return {reg,run,load,key,host};
}

test('the present button is injected into the AML heading and hidden when the release lacks queue data',async()=>{
  const h=await harness();
  h.load({schema_version:1,aml:{}});
  assert.match(h.host.innerHTML,/id="aml-present"/);
  assert.equal(h.reg.get('aml-present').hidden,true);
  h.load(fixture());
  assert.equal(h.reg.get('aml-present').hidden,false);
  assert.equal(h.reg.get('network-present').classes.has('present-cta'),true,'the BQ2 button shares the glowing style');
  h.run('openBq1Presenter()');assert.equal(h.reg.get('qp').hidden,false);
  h.load({schema_version:1,aml:{}});
  assert.equal(h.reg.get('qp').hidden,true,'a release without the data closes an open presenter');
});

test('presenter data uses only published counts and cross-checks accounts against drawn BQ2 rings',async()=>{
  const h=await harness();h.load(fixture());
  const data=h.run('bq1PresenterData(snapshot)');
  assert.deepEqual([...data.ks],['25','50','100'],'review sizes beyond the exported queue are dropped');
  assert.equal(data.rows,862792);assert.equal(data.positives,956);
  assert.equal(data.queue[0].ring,'A','leading zeros in bank codes match ring account ids');
  assert.equal(data.queue[2].ringRank,7);
  assert.equal(h.run('qpOverlap(bq1PresenterData(snapshot).queue,"100")'),2);
  assert.equal(h.run('qpOverlap(bq1PresenterData(snapshot).rulesQueue,"25")'),25,'rules queue rows paying a ring account count too');
  assert.equal(h.run('bq1PresenterData(snapshot).rings.accounts'),3);
});

test('story text states the real numbers and never claims per-payment labels',async()=>{
  const h=await harness();h.load(fixture());
  h.run('QP.data=bq1PresenterData(snapshot);QP.row=QP.data.queue[0]');
  const story=step=>h.run(`qpStoryHTML(${step},QP.data,'100',QP.row)`);
  assert.match(story(0),/862,792 payments.*956/);
  assert.match(story(0),/Test · Sep 9–10/);
  assert.match(story(3),/Rules find .*>0<.* of 100\. The model finds .*>91</);
  assert.match(story(3),/tally of each queue's published count, not particular payments/);
  assert.match(story(3),/about 0\.11/);
  assert.match(story(4),/At 100 reviews: model <em>91<\/em>, rules 0\..*\(41×\)/,'the largest review size the exported queue supports');
  assert.match(story(5),/latest 2 of 12 earlier payments/);
  assert.match(story(5),/not published; an analyst makes the call/);
  assert.match(story(6),/>2<\/em> of the model's top 100 touch an account in a BQ2 ring/);
  assert.match(story(6),/The rules' top 100: 100\./);
  h.load(fixture({withNetwork:false}));h.run('QP.data=bq1PresenterData(snapshot)');
  assert.doesNotMatch(story(6),/BQ2 ring/,'without network data the last scene does not claim a cross-check');
});

test('keyboard drives the steps, the case file shows evidence and Escape returns focus',async()=>{
  const h=await harness();h.load(fixture());
  h.run('openBq1Presenter()');
  const el=id=>h.reg.get(id), step=()=>h.run('QP.step');
  assert.equal(el('qp').hidden,false);assert.equal(step(),0);assert.equal(el('qp-back').disabled,true);
  h.key('ArrowRight');assert.equal(step(),1);assert.equal(el('qp-capacity').hidden,true);
  h.key('3');assert.equal(step(),3);assert.equal(el('qp-capacity').hidden,false);assert.equal(el('qp-readout').hidden,true);
  h.run('qpSetK("25")');assert.match(el('qp-story').innerHTML,/of 25\. The model finds .*>25</);
  h.key('4');assert.equal(step(),5);assert.equal(el('qp-readout').hidden,false);assert.equal(el('qp-timeline').hidden,false);
  assert.match(el('qp-evidence').innerHTML,/payment_format=ACH.*\+3\.50/);
  assert.match(el('qp-evidence').innerHTML,/qp-ev neg/);
  assert.match(el('qp-badge').textContent,/sender is in BQ2 ring #1/);
  assert.equal(h.run('QP.replay.pays.length'),3,'two published history events plus the flagged payment');
  assert.equal(h.run('QP.replay.pays.at(-1).flag'),true);
  h.run('qpOpenCase(QP.data.queue[2])');assert.match(el('qp-story').innerHTML,/rank #3 of/);assert.match(el('qp-badge').textContent,/receiver is in BQ2 ring #7/);
  assert.match(el('qp-story').innerHTML,/No earlier payments from this sender are published/);
  h.key('5');assert.equal(step(),6);assert.equal(el('qp-next').disabled,true);
  h.key('ArrowRight');assert.equal(step(),6);
  h.key('Escape');assert.equal(el('qp').hidden,true);assert.equal(h.reg.focused,el('aml-present'));
  h.key('ArrowLeft');assert.equal(step(),6,'keys do nothing once closed');
});

test('opening without presenter data does nothing',async()=>{
  const h=await harness();h.load(fixture({queue:10}));
  assert.equal(h.reg.get('aml-present').hidden,true);
  h.run('openBq1Presenter()');
  assert.equal(h.run('QP.open'),false);
});
