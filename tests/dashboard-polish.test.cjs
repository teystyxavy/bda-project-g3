/* Offline tests for the shared chart engine, KPI sparklines, the pipeline map flow and the incoming run line. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard/dashboard.js'),'utf8');

class Element {
  constructor(registry,id='') {this.registry=registry;this.id=id;this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.dataset={};this.style={};this.attributes={};this.html='';this.options=[];}
  set innerHTML(text) {this.html=text;}
  get innerHTML() {return this.html;}
  setAttribute(name,value) {this.attributes[name]=value;}
  insertAdjacentElement(_where,node) {if(node.id) this.registry.set(node.id,node);}
  querySelectorAll() {return [];}
  querySelector() {return null;}
  closest() {return this;}
}

const respond=value=>({ok:true,status:200,json:async()=>value});
const settle=async()=>{for(let i=0;i<12;i++) await new Promise(setImmediate);};

function stream(extra={}) {
  return {schema_version:1,run_id:'stream-smoke-20261006',state:'completed',generated_at:'2026-11-01T11:02:08Z',processed:884,expected:884,
    senders:{expected:32,started:32,complete:32},queue:[],latest:[],consumer_enabled:false,reconciled:true,max_score:.94,replay_position:'2022-09-10T23:21:00',
    last_progress_at:'2026-10-06T12:28:20Z',progress:[{at:'2026-10-06T12:11:14Z',processed:1},{at:'2026-10-06T12:28:20Z',processed:884}],...extra};
}

async function harness({snapshot=null,frames=null}={}) {
  const elements=new Map();
  const reg={set:(id,node)=>elements.set(id,node)};
  const get=id=>{if(!elements.has(id)) elements.set(id,new Element(reg,id));return elements.get(id);};
  const timers=[];
  const doc={hidden:false,getElementById:id=>id==='incoming-run'?elements.get(id)||null:get(id),createElement:()=>new Element(reg),
    querySelector:()=>null,querySelectorAll:()=>[],addEventListener:()=>{}};
  const fetch=async url=>{
    if(url==='/streaming-config.json') return respond({schema_version:1,run_id:'stream-smoke-20261006',snapshot_url:'/streaming/stream-smoke-20261006/snapshot.json'});
    if(snapshot && url==='/streaming/stream-smoke-20261006/snapshot.json') return respond(typeof snapshot==='function'?snapshot():snapshot);
    return new Promise(()=>{});
  };
  const context={document:doc,location:{origin:'https://example.cloudfront.net',pathname:'/',search:'',hash:''},console,Intl,URL,URLSearchParams,Date,Number,String,Array,Math,JSON,Boolean,
    TextEncoder,TextDecoder,AbortController,sessionStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},history:{replaceState:()=>{}},
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:()=>{},fetch,performance:{now:()=>0}};
  if(frames) context.requestAnimationFrame=fn=>{frames.push(fn);return frames.length;};
  context.window=context;
  vm.createContext(context);vm.runInContext(source,context);
  await settle();
  return {el:get,context,run:expression=>vm.runInContext(expression,context)};
}

test('the old corridors stage lights the BQ2 node and links follow the stage states',async()=>{
  const h=await harness();
  assert.equal(h.run(`pipelineStage('corridors')`),'network');assert.equal(h.run(`pipelineStage('score')`),'score');
  const state=(from,to,states)=>h.run(`pipelineLinkState(${JSON.stringify(from)},${JSON.stringify(to)},${JSON.stringify(states)})`);
  assert.equal(state('curate','network',{curate:'complete',network:'running'}),'running');
  assert.equal(state('curate','network',{curate:'running',network:'running'}),'idle');
  assert.equal(state('acquire','curate',{acquire:'reused',curate:'complete'}),'done');
  assert.equal(state(null,'acquire',{acquire:'failed'}),'failed');
  assert.equal(state('export','publish',{export:'pending',publish:'pending'}),'idle');
  assert.equal(h.run('PIPELINE_LINKS.length'),(fs.readFileSync(path.join(__dirname,'../dashboard/index.html'),'utf8').match(/<g class="map-links"[\s\S]*?<\/g>/)[0].match(/<path /g)||[]).length);
});

test('line charts keep their dot titles, mark the review capacity and carry a readable table',async()=>{
  const h=await harness();
  h.run(`chartLines('capture-chart',[{name:'Frozen model',color:'#3fd0b8',rows:[{x:25,y:25,label:'25'},{x:100,y:91,label:'100'}]},{name:'Rules',color:'#ffb648',rows:[{x:25,y:0,label:'25'},{x:100,y:0,label:'100'}]}],
    91,null,'Payments reviewed',integer,{markers:[{x:100,y:91,color:'#3fd0b8',pulse:true,label:'Your review capacity: 100 reviews, 91 laundering'}],note:'Dots are exact.'})`);
  const html=h.el('capture-chart').innerHTML;
  assert.match(html,/viewBox="0 0 600 262"/);
  assert.equal((html.match(/class="chart-line"/g)||[]).length,2);
  assert.match(html,/<title>Frozen model · 100: 91<\/title>/);
  assert.match(html,/class="chart-pulse"[^>]*stroke="#3fd0b8"/);
  assert.match(html,/<title>Your review capacity: 100 reviews, 91 laundering<\/title>/);
  assert.match(html,/<table class="sr-only">.*<td>Rules<\/td><td>25<\/td><td>0<\/td>/);
  assert.match(html,/<p class="chart-note">Dots are exact\.<\/p>/);
  // A long curve draws as a line only: no per-point dots to tab through.
  h.run(`chartLines('pr-chart',[{name:'Frozen model',color:'#3fd0b8',rows:Array.from({length:80},(_,i)=>({x:i/79,y:1-i/79,label:'Recall '+i}))}],1,1,'Recall')`);
  assert.doesNotMatch(h.el('pr-chart').innerHTML,/class="chart-dot"/);
  h.run(`chartLines('pr-chart',[{name:'Rules',color:'#ffb648',rows:[{x:0,y:null,label:'none'}]}],1,1,'Recall')`);
  assert.equal(h.el('pr-chart').textContent,'No measurements for this selection.');
});

test('KPI sparklines appear only with at least two values and keep the card markup',async()=>{
  const h=await harness();
  h.run(`cards('aml-kpis',[['Laundering in top 100','91.0%','91 of 100',true,{values:[1,.96,.91,.7],color:'#3fd0b8',label:'Precision by size',caption:'At 25 → 250 reviews'}],['Plain','1','a',false,{values:[3]}]])`);
  const html=h.el('aml-kpis').innerHTML;
  assert.match(html,/^<div class="kpi kpi-answer"><span>Laundering in top 100<\/span><strong>91\.0%<\/strong><span>91 of 100<\/span><svg class="kpi-spark"[^>]*aria-label="Precision by size"/);
  assert.match(html,/<small class="spark-caption">At 25 → 250 reviews<\/small>/);
  assert.equal((html.match(/kpi-spark/g)||[]).length,1);
});

test('the score histogram lights the queue range and keeps exact counts one click away',async()=>{
  const h=await harness();
  h.run(`scoreColumns('scores',[{label:'0.00–0.50',transactions:862700},{label:'0.50–0.90',transactions:60},{label:'0.90–1.00',transactions:32}],1,'Top 100 starts here · score ≥ 0.7636')`);
  const html=h.el('scores').innerHTML;
  assert.equal((html.match(/score-col-queue/g)||[]).length,2);
  assert.match(html,/Top 100 starts here · score ≥ 0\.7636 →/);
  assert.match(html,/<title>Score 0\.00–0\.50: 862,700 transactions<\/title>/);
  assert.match(html,/<details class="inline-details"><summary>Exact count in each score band<\/summary><div id="scores-bars"><\/div><\/details>/);
  h.run(`scoreColumns('scores',[],-1,'')`);
  assert.equal(h.el('scores').textContent,'No observations in this period.');
});

test('count-up animates only the leading text, so markup inside a value survives',async()=>{
  const frames=[];
  const h=await harness({frames});
  h.context.window.matchMedia=()=>({matches:false});
  const text={nodeType:3,textContent:'44.3%'}, strong={textContent:'44.3%29.5×',children:[{}],firstChild:text};
  h.context.strong=strong;h.run('countUp(strong)');
  frames.shift()(450);
  assert.match(text.textContent,/^\d+\.\d%$/);assert.notEqual(text.textContent,'44.3%');
  while(frames.length) frames.shift()(5000);
  assert.equal(text.textContent,'44.3%');assert.equal(strong.textContent,'44.3%29.5×');
  // Dates and clock times are never counted.
  const date={textContent:'2022-09-10 23:21'};h.context.date=date;h.run('countUp(date)');
  assert.equal(frames.length,0);
});

test('the incoming run line names a finished test stream and never calls it live',async()=>{
  const h=await harness({snapshot:stream()});
  h.el('incoming-tab').onclick();await settle();
  const line=h.el('incoming-run');
  assert.equal(line.dataset.state,'idle');
  assert.match(line.innerHTML,/<strong>Test stream · 6 Oct 2026<\/strong>/);
  assert.match(line.innerHTML,/884 payments from 32 senders/);
  assert.match(line.innerHTML,/finished · streaming is switched off/);
  assert.doesNotMatch(line.innerHTML,/Live/);
  assert.match(h.el('incoming-kpis').innerHTML,/class="kpi-spark"/);
});

test('the run line says live only while a stream is running and fresh',async()=>{
  const fresh=await harness({snapshot:()=>stream({state:'running',processed:412,consumer_enabled:true,generated_at:new Date().toISOString()})});
  fresh.el('incoming-tab').onclick();await settle();
  assert.equal(fresh.el('incoming-run').dataset.state,'live');
  assert.match(fresh.el('incoming-run').innerHTML,/<strong>Live stream · 6 Oct 2026<\/strong>.*scoring as payments arrive/);
  const stale=await harness({snapshot:()=>stream({state:'running',processed:412,consumer_enabled:true,generated_at:new Date(Date.now()-600000).toISOString()})});
  stale.el('incoming-tab').onclick();await settle();
  assert.equal(stale.el('incoming-run').dataset.state,'idle');
  assert.match(stale.el('incoming-run').innerHTML,/no recent update/);
  assert.doesNotMatch(stale.el('incoming-run').innerHTML,/Live/);
});
