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
  assert.match(html,/<div class="sr-only"><table>.*<td>Rules<\/td><td>25<\/td><td>0<\/td>/);
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

test('the stream replay lays out the ranked queue and counts from the commit history',async()=>{
  const h=await harness();
  h.context.progress=[{at:'2026-10-06T12:11:14Z',processed:1},{at:'2026-10-06T12:20:00Z',processed:400},{at:'2026-10-06T12:28:20Z',processed:884}];
  assert.equal(h.run(`streamCountAt(progress,Date.parse('2026-10-06T12:00:00Z'))`),0);
  assert.equal(h.run(`streamCountAt(progress,Date.parse('2026-10-06T12:25:00Z'))`),400);
  assert.equal(h.run(`streamCountAt(progress,Date.parse('2026-10-06T12:28:20Z'))`),884);
  h.context.data={queue:Array.from({length:100},(_,i)=>({rank:i+1,scored_at:'2026-10-06T12:15:00Z'}))};
  const view=JSON.parse(h.run('JSON.stringify(streamReplayHTML(data,1200))'));
  assert.equal((view.html.match(/class="qx-cell sr-slot/g)||[]).length,100);assert.equal(view.cols,20);
  assert.match(view.html,/qx-urgent" data-index="0"/);assert.match(view.html,/qx-high" data-index="99"/);
  assert.equal(JSON.parse(h.run('JSON.stringify(streamReplayHTML(data,390))')).cols,10);
});

const TRAIL_EVENTS=[{timestamp:'2022-09-10T13:18:00.000',to_bank:'0023',to_account:'SELF',payment_format:'ACH',amount_usd_asof:13290},
  {timestamp:'2022-09-01T15:08:00.000',to_bank:'024840',to_account:'A',payment_format:'ACH',amount_usd_asof:12300},
  {timestamp:'2022-09-01T16:00:00.000',to_bank:'008771',to_account:'C',payment_format:'Reinvestment',amount_usd_asof:1000}];
const TRAIL_ROW={rank:4,transaction_id:'HI-Small:9',timestamp:'2022-09-10T16:50:00.000',from_bank:'23',from_account:'SELF',to_bank:'008771',to_account:'C',
  payment_format:'ACH',payment_currency:'US Dollar',amount_usd_asof:18530,model_score:.9959,distinct_counterparties_24h:4,txn_count_1h:0,
  model_evidence:'payment_format=ACH: +3.0703 margin; Prior-week distinct senders (log): +3.0434 margin; Hour (UTC): -0.4000 margin'};

test('the case trail says what the sender did, in time order, and names payments back to itself',async()=>{
  const h=await harness();
  h.context.events=TRAIL_EVENTS;h.context.row=TRAIL_ROW;
  const trail=JSON.parse(h.run('JSON.stringify(caseTrail(events,row))'));
  assert.equal(trail.summary,"Before this payment, the sender made 3 payments to 3 receivers, $26.59K in total. This payment goes to a receiver it paid 1 time before. 1 of them went back to the sender's own account.");
  assert.deepEqual(trail.payments.map(payment=>payment.timestamp.slice(0,16)),['2022-09-01T15:08','2022-09-01T16:00','2022-09-10T13:18','2022-09-10T16:50']);
  assert.equal(trail.payments.at(-1).current,true);
  assert.deepEqual(trail.receivers.map(item=>[item.key,item.count,item.current,item.self]),[['24840:A',1,false,false],['8771:C',2,true,false],['23:SELF',1,false,true]]);
  assert.equal(JSON.parse(h.run('JSON.stringify(caseTrail([],row))')).summary,'No earlier outgoing payments from this sender are in the exported history.');
});

test('the case graph draws the sender, its receivers and a loop for payments to itself',async()=>{
  const h=await harness();
  h.context.events=TRAIL_EVENTS;h.context.row=TRAIL_ROW;
  const svg=h.run('caseGraphHTML(caseTrail(events,row),row,900,430)');
  assert.equal((svg.match(/class="case-node/g)||[]).length,2);
  assert.equal((svg.match(/class="case-node case-node-current"/g)||[]).length,1);
  assert.match(svg,/PAYS ITSELF · 1 payment/);assert.match(svg,/SENDER 23:SELF/);
  assert.equal((svg.match(/class="case-edge[ "]/g)||[]).length,3);
  // Every link starts at the sender.
  const layout=JSON.parse(h.run('JSON.stringify((({sender,nodes})=>({sender,paths:[...nodes.values()].map(node=>node.path)}))(caseLayout(caseTrail(events,row).receivers,900,430)))'));
  assert.ok(layout.paths.every(path=>path.startsWith(`M${layout.sender.x.toFixed(1)} ${layout.sender.y.toFixed(1)}`)));
});

test('the case readout shows rank, facts and why it ranked high, with no laundering label',async()=>{
  const h=await harness();
  h.context.row=TRAIL_ROW;h.context.rows=[TRAIL_ROW,{...TRAIL_ROW,rank:1,amount_usd_asof:20000}];
  const html=h.run(`caseReadoutHTML(row,rows,true,{ring_id:'r6',rank:6})`);
  assert.match(html,/<span class="priority priority-urgent rank-chip">#4 · Urgent<\/span>/);
  assert.match(html,/#2 largest amount/);assert.match(html,/data-ring="r6">In BQ2 ring #6 →/);
  assert.match(html,/<small>Ranking score<\/small>0\.9959/);
  assert.equal((html.match(/<div class="(neg)?"><span>/g)||[]).length,3);assert.match(html,/class="neg"><span>Hour \(UTC\)<\/span><b>-0\.40<\/b>/);
  assert.doesNotMatch(html,/is_laundering|laundering label|labelled/i);
  assert.equal(h.run(`rankChip({rank:26})`),'<span class="priority priority-high rank-chip">#26 · High</span>');
});

test('incoming links count the stream by evaluation day and never match masked accounts',async()=>{
  const h=await harness();
  h.context.data={queue:[{transaction_id:'HI-Small:1',timestamp:'2022-09-02T10:00:00'},{transaction_id:'HI-Small:2',timestamp:'2022-09-07T10:00:00'},{transaction_id:'HI-Small:3',timestamp:'2022-09-10T10:00:00'}]};
  h.context.batch={full_rows:5078345,splits:{train:['2022-09-01','2022-09-07'],validation:['2022-09-07','2022-09-09'],test:['2022-09-09','2022-09-11']},
    exploration:{aml:{periods:{test:{scopes:[{model:{queue:['HI-Small:3']},rules:{queue:[]}}]}}}}};
  const html=h.run('streamLinksHTML(data,batch)');
  assert.match(html,/Training reference · Sep 1–6 · <b>1<\/b>/);assert.match(html,/Validation · Sep 7–8 · <b>1<\/b>/);assert.match(html,/Test · Sep 9–10 · <b>1<\/b>/);
  assert.match(html,/1 of them also appear in the batch queues exported for review/);
  assert.match(html,/over all 5,078,345 payments/);assert.match(html,/not matched to rings here/);
  assert.equal((html.match(/data-view="(aml|network|ingestion)"/g)||[]).length,3);
  assert.match(h.run('streamLinksHTML(data,null)'),/Load the batch release to compare dates/);
});

test('an incoming case uses only that sender\'s earlier published payments and no self loop',async()=>{
  const h=await harness();
  const pay=(id,time,to,from='••••9010')=>({transaction_id:id,timestamp:time,from_bank:'021745',from_account:from,to_bank:'0232259',to_account:to,amount_usd_asof:1000,payment_format:'ACH'});
  h.context.data={queue:[pay('A','2022-09-02T16:04:00','••••D3C0'),pay('B','2022-09-05T06:30:00','••••D3C0'),pay('C','2022-09-01T06:41:00','••••9010')],
    latest:[pay('D','2022-09-01T07:00:00','••••AAAA','••••1111'),pay('C','2022-09-01T06:41:00','••••9010')]};
  const trail=JSON.parse(h.run(`JSON.stringify(streamTrail(data,data.queue[1]))`));
  assert.deepEqual(trail.payments.map(payment=>payment.transaction_id),['C','A','B']);
  assert.ok(trail.receivers.every(item=>!item.self));
  assert.match(trail.summary,/^Among the payments this stream publishes, the sender made 2 payments to 2 receivers/);
  assert.match(JSON.parse(h.run(`JSON.stringify(streamTrail(data,data.queue[2]))`)).summary,/^None of this sender's earlier payments/);
});

// A HI-Large-shaped closed club: hubs that all pay one another, senders that each pay every hub once,
// one sender tied to a single hub and one joined by a $2 payment.
function clubRing(hubCount=5,senderCount=44) {
  const hubs=Array.from({length:hubCount},(_,i)=>`1:H${i}`), nodes=[], links=[];
  const node=(id,role)=>({id,role,fan_in:3,fan_out:3,in_usd:1000,out_usd:1000,touches_laundering:false});
  hubs.forEach(id=>nodes.push(node(id,'hub')));
  for(const a of hubs) for(const b of hubs) if(a!==b) links.push({source:a,target:b,txns:1,usd:500,loop:false});
  for(let i=0;i<senderCount;i++) {const id=`2:S${i}`;nodes.push(node(id,'counterparty'));hubs.forEach(hub=>links.push({source:id,target:hub,txns:1,usd:200+i,loop:false}));}
  nodes.push(node('3:ONE','counterparty'));links.push({source:'3:ONE',target:hubs[0],txns:14,usd:5000,loop:false});
  nodes.push(node('4:TINY','counterparty'));links.push({source:'4:TINY',target:hubs[1],txns:1,usd:2,loop:false});
  return {nodes,links,total_accounts:nodes.length,shown_accounts:nodes.length,truncated:false,loops:[],
    payments:links.map((link,i)=>[i,i,link.usd,0]),payments_total:links.length,payment_start:'2022-09-01T00:00:00'};
}

test('club view draws the senders that pay every hub as one group and hides hub-to-hub links until hover',async()=>{
  const h=await harness();
  h.context.ring=clubRing();
  const view=JSON.parse(h.run('JSON.stringify(focusGraph(ring))'));
  assert.deepEqual(view.club.groups,[{id:'group:sender',dir:'sender',count:44}]);
  assert.equal(view.club.closed,true);assert.equal(view.club.trivial,1);assert.equal(view.club.hubLinks,20);
  // 5 hubs + the single-hub sender + the group; the $2 account is left out.
  assert.deepEqual(view.nodes.map(node=>node.id).sort(),['1:H0','1:H1','1:H2','1:H3','1:H4','3:ONE','group:sender']);
  const group=view.links.filter(link=>link.source==='group:sender');
  assert.equal(group.length,5);assert.ok(group.every(link=>link.members===44&&link.txns===44&&!link.club));
  assert.equal(view.links.filter(link=>link.club).length,20);
  // Every original link maps to a merged one, except the trivial account's.
  assert.equal(view.linkMap.length,h.context.ring.links.length);assert.equal(view.linkMap.filter(i=>i<0).length,1);
  // The presenter replays every payment but the trivial one on the merged links.
  h.run('snapshot={network:{ring_list:[{rank:1,ring_id:"1:H0",hubs:5,loops:0,usd:1,laundering_rate:0,accounts:51}],graphs:{"1:H0":ring},thresholds:[{threshold:10,headline:true,hubs:{accounts:5},high_volume:{accounts:0}}],totals:{},rings:{},parameters:{loop_window_days:7,headline_threshold:10}}}');
  const pres=JSON.parse(h.run('JSON.stringify(presenterData(snapshot.network).rings[0])'));
  assert.equal(pres.pays.length,h.context.ring.links.length-1);assert.equal(pres.grouped,44);
  assert.ok(pres.pays.every(pay=>pay[0]>=0&&pay[0]<pres.links.length));
  assert.equal(pres.links.filter(link=>link[4]).length,20);
});

test('the presenter story names the lead ring, the held-back group, what the ranking misses and the club\'s real payment count',async()=>{
  const h=await harness();
  const ids=Array.from({length:11},(_,i)=>`1:R${i}`);
  h.context.club=clubRing();h.context.plain=clubRing(1,3);
  h.run(`snapshot={network:{ring_list:${JSON.stringify(ids.map((id,i)=>({rank:i+1,ring_id:id,hubs:i===1?1:5,loops:0,usd:1,laundering_rate:0.02,accounts:51})))},
    graphs:Object.fromEntries(${JSON.stringify(ids)}.map((id,i)=>[id,i===1?plain:club])),
    thresholds:[{threshold:10,headline:true,hubs:{accounts:5,laundering_rate:0.81},high_volume:{accounts:0}}],
    totals:{rows:1000,accounts:100,links:50,laundering_rate:0.115},rings:{rings:9732,accounts:41208,laundering_rate:0.32,lift:2.8,coverage:0.059},
    component_review:{components:1,accounts:26370},parameters:{loop_window_days:7,headline_threshold:10}}}`);
  h.run('story=(step)=>{const data=presenterData(snapshot.network);return presStoryHTML(step,data,data.lead,0);}');
  // Ring #1 is a closed club, so the story leads with the first open ring and names that rank, not #1.
  assert.equal(h.run('presenterData(snapshot.network).lead.rank'),2);
  const two=h.run('story(2)');
  assert.match(two,/Let's zoom in on one\./);assert.doesNotMatch(two,/sits in the middle/);
  assert.match(two,/1 oversized connected group \(26,370 accounts\) is held back/);
  // "Does it hold up?" gives the ring and hub rates against the base rate.
  const five=h.run('story(5)');
  assert.match(five,/Inside the rings it's/);assert.match(five,/among hubs/);assert.doesNotMatch(five,/not yet reliable/);
  // A club ring counts its real hub-to-hub payments and does not claim to be a star of separate hubs.
  h.run('clubStory=presStoryHTML(3,presenterData(snapshot.network),presenterData(snapshot.network).rings[0],0)');
  assert.doesNotMatch(h.run('clubStory'),/Each hub is its own star/);
  assert.match(h.run('presStoryHTML(4,presenterData(snapshot.network),presenterData(snapshot.network).rings[0],0)'),/about 1\.0 payments per pair/);
  assert.doesNotMatch(h.run('presStoryHTML(4,presenterData(snapshot.network),presenterData(snapshot.network).rings[0],0)'),/spread over months/);
});

test('a ring without a shared sender pool keeps the ordinary focus view',async()=>{
  const h=await harness();
  h.context.ring=clubRing(1,44);
  assert.equal(h.run('clubGraph(ring)'),null);
  assert.equal(h.run('focusGraph(ring).club'),undefined);
});
