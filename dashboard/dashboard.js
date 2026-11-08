'use strict';
let snapshot;
let activeView='aml';
let snapshotLoad=0;
const element = id => document.getElementById(id);
const integer = value => Number(value).toLocaleString('en-US');
// Older snapshots may not retain receiver history; missing values are not zero.
const historyCount = value => Number.isSafeInteger(value) && value >= 0 ? integer(value) : 'Unavailable';
const receiverPayments = row => ['in_txn_count_1h','in_txn_count_24h','in_txn_count_7d'].map(key=>historyCount(row[key])).join(' / ');
const receiverSenders = row => ['distinct_senders_24h','distinct_senders_7d'].map(key=>historyCount(row[key])).join(' / ');
const receiverFacts = row => `<span><small>Receiver payments, past 1h / 24h / 7d</small>${receiverPayments(row)}</span><span><small>Receiver distinct senders, past 24h / 7d</small>${receiverSenders(row)}</span>`;
const percent = value => value == null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`;
const compact = value => new Intl.NumberFormat('en-US', {notation:'compact',maximumFractionDigits:2}).format(value);
const dollars = value => `$${compact(value)}`;
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

function selectDashboardView(view) {
  activeView=view;
  for(const name of ['aml','incoming','network','ingestion']) {
    element(name).hidden=name!==view || (['aml','network'].includes(name) && !snapshot);
    element(`${name}-tab`).setAttribute('aria-pressed',String(name===view));
  }
  const live=['ingestion','incoming'].includes(view);
  element('status').hidden=live;element('methods').hidden=live;
  if(view==='network' && snapshot) renderNetwork();
  if(view==='ingestion') startJourney();else stopJourney();
  replaySection(view);
}
for(const view of ['aml','incoming','network','ingestion']) element(`${view}-tab`).onclick=()=>selectDashboardView(view);

// ---- Shared motion: count-up numbers, charts drawn at their real width, a replay whenever a tab opens ----
const reducedMotion = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
const animating = () => typeof requestAnimationFrame==='function' && !reducedMotion();
// Counts every number in a text up from `from` (same decimals and separators). Dates and clock times stay as they are.
// Only the element's own leading text moves, so markup inside it (a <small> unit, say) is left alone.
const counting=new WeakSet();
function countUp(node,from=0,duration=900) {
  const target=node?.children?.length?(node.firstChild?.nodeType===3?node.firstChild:null):node;
  const text=target?.textContent;
  if(!text || !animating() || /\d[-:]\d/.test(text) || counting.has(target)) return;
  const numbers=[...text.matchAll(/\d[\d,]*(?:\.\d+)?/g)];
  if(!numbers.length) return;
  const parts=numbers.map(match=>({value:Number(match[0].replace(/,/g,'')),decimals:(match[0].split('.')[1]||'').length,commas:match[0].includes(',')}));
  const render=u=>{let index=0;return text.replace(/\d[\d,]*(?:\.\d+)?/g,()=>{const part=parts[index++], value=from+(part.value-from)*u;
    return part.commas?value.toLocaleString('en-US',{minimumFractionDigits:part.decimals,maximumFractionDigits:part.decimals}):value.toFixed(part.decimals);});};
  const start=performance.now();counting.add(target);
  const frame=now=>{try {
    const u=Math.min(1,(now-start)/duration), eased=1-Math.pow(1-u,3);
    target.textContent=u<1?render(eased):text;
    if(u<1) requestAnimationFrame(frame);else counting.delete(target);
  } catch {target.textContent=text;counting.delete(target);}};
  requestAnimationFrame(frame);
}
// Charts remember how they were drawn so a tab switch or resize redraws them at the width they are shown.
const chartCalls=new Map();
function redrawCharts(scope) {
  for(const [target,[draw,args]] of chartCalls) {
    const host=element(target);
    if(host && (!scope || scope.contains?.(host)) && host.offsetParent!==null) draw(...args);
  }
}
// Opening a tab replays its numbers and redraws its charts, so every view arrives the same way.
function replaySection(view) {
  const section=element(view);
  if(!section?.querySelectorAll || section.hidden) return;
  redrawCharts(section);
  section.querySelectorAll('.kpi strong, .bar-value, .signal-row strong').forEach(node=>countUp(node));
}
let resizeTimer=null;
window.addEventListener?.('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>redrawCharts(element(activeView)),180);});
// Chart width in CSS pixels: the host's width when it is on screen, else a sensible default.
const chartWidth = host => Math.round(Math.min(1100,Math.max(300,host?.clientWidth||600)));

// The trust strip under each tab's heading; verified adds the ✓ Verified chip.
function trustStrip(target,text,verified) {element(target).className=`trust-strip${verified?' verified':''}`;element(target).textContent=text;}
// A small trend line for a KPI card: values in order, drawn with the chart glow.
function sparkline(values,color='#3fd0b8',label='',caption='') {
  const points=values.filter(Number.isFinite);
  if(points.length<2) return '';
  const low=Math.min(...points), high=Math.max(...points), span=high-low||1;
  const xy=points.map((value,index)=>[3+index/(points.length-1)*254,30-(value-low)/span*26]);
  const line=xy.map(([x,y],index)=>`${index?'L':'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  return `<svg class="kpi-spark" viewBox="0 0 260 34" preserveAspectRatio="none" role="img" aria-label="${escapeText(label)}"><path d="${line} L257 34 L3 34 Z" fill="${color}" opacity=".12"/><path class="spark-line" d="${line}" pathLength="1" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" style="filter:drop-shadow(0 0 4px ${color})"/></svg>${caption?`<small class="spark-caption">${escapeText(caption)}</small>`:''}`;
}
// items: [label, value, detail, answer?, spark?]; spark is {values, color, label}.
function cards(target, items) {
  const host=element(target), previous=new Map();
  host.querySelectorAll?.('.kpi')?.forEach(card=>previous.set(card.firstChild?.textContent,card.querySelector('strong')?.textContent));
  host.innerHTML = items.map(([label,value,detail,answer,spark]) => `<div class="kpi${answer?' kpi-answer':''}"><span>${escapeText(label)}</span><strong>${escapeText(value)}</strong><span>${escapeText(detail)}</span>${spark?sparkline(spark.values,spark.color,spark.label,spark.caption):''}</div>`).join('');
  // Count up on first show and when a value changes, not on every quiet refresh.
  host.querySelectorAll?.('.kpi')?.forEach(card=>{const strong=card.querySelector('strong'), before=previous.get(card.firstChild?.textContent);
    if(before!==strong?.textContent) countUp(strong,before&&!/\d[-:]\d/.test(before)?Number(before.match(/\d[\d,]*(?:\.\d+)?/)?.[0].replace(/,/g,''))||0:0);});
}

function table(target, headings, rows) {
  element(target).innerHTML = `<table><thead><tr>${headings.map(label=>`<th scope="col">${escapeText(label)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(value=>`<td>${escapeText(value)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// scale 'log' sizes bars by log10(1 + value) so one huge bucket cannot hide the rest; color(row, index) tints each bar;
// mark {index, text} draws a labelled divider above that row.
function bars(target, rows, label, value, formatter=integer, {scale='linear', color, mark}={}) {
  if (!rows.length) { element(target).textContent='No observations in this period.'; return; }
  const size = scale==='log' ? number=>Math.log10(1+Math.max(0,number)) : number=>number;
  const maximum = Math.max(...rows.map(row=>size(row[value])),scale==='log'?1e-9:1);
  element(target).innerHTML = `<div class="bars">${rows.map((row,index)=>`${mark&&mark.index===index?`<p class="bar-mark">${escapeText(mark.text)}</p>`:''}<div class="bar-row" title="${escapeText(label(row))}: ${escapeText(formatter(row[value]))}"><span class="bar-label">${escapeText(label(row))}</span><div class="bar-track"><div class="bar-fill" style="width:${size(row[value])/maximum*100}%${color?`;background:${color(row,index)}`:''}"></div></div><span class="bar-value">${escapeText(formatter(row[value]))}</span></div>`).join('')}</div>`;
  element(target).querySelectorAll?.('.bar-value')?.forEach(node=>countUp(node));
}

// Score histogram as one row of heat columns: height on a log scale, colour deepening with the score, the review cut-off
// marked. The exact counts sit in a details list (id `${target}-bars`) under the chart.
function scoreColumns(target,histogram,cutIndex,cutText) {
  const host=element(target);
  chartCalls.set(target,[scoreColumns,[target,histogram,cutIndex,cutText]]);
  if(!histogram.length) {host.textContent='No observations in this period.';return;}
  const width=chartWidth(host), narrow=width<520, height=narrow?190:210, left=8, right=8, top=34, bottom=30;
  const w=width-left-right, h=height-top-bottom, gap=narrow?2:4, column=w/histogram.length;
  const top10=Math.log10(1+Math.max(...histogram.map(row=>row.transactions)));
  const n=value=>value.toFixed(1), every=Math.ceil(histogram.length/Math.max(2,Math.floor(w/64)));
  const columns=histogram.map((row,index)=>{
    const tall=Math.max(2,Math.log10(1+row.transactions)/top10*h), x=left+index*column, color=heatFill(index/Math.max(1,histogram.length-1));
    return `<g class="score-col${index>=cutIndex&&cutIndex>=0?' score-col-queue':''}" style="--i:${index}"><rect class="score-hit" x="${n(x)}" y="${top}" width="${n(column)}" height="${h}" fill="transparent"/><rect x="${n(x+gap/2)}" y="${n(top+h-tall)}" width="${n(column-gap)}" height="${n(tall)}" rx="3" fill="${color}"${index>=cutIndex&&cutIndex>=0?` style="filter:drop-shadow(0 0 6px ${color})"`:''}/><title>Score ${escapeText(row.label)}: ${escapeText(integer(row.transactions))} transactions</title></g>`;}).join('');
  const labels=histogram.map((row,index)=>index%every===0||index===histogram.length-1?`<text class="chart-axis" x="${n(left+index*column+column/2)}" y="${height-10}" text-anchor="middle">${escapeText(row.label.split('–')[0])}</text>`:'').join('');
  const cutX=left+cutIndex*column;
  const cut=cutIndex>=0?`<rect class="score-queue-zone" x="${n(cutX)}" y="${top-6}" width="${n(left+w-cutX)}" height="${h+6}" rx="6"/><line class="score-cut" x1="${n(cutX)}" x2="${n(cutX)}" y1="${top-24}" y2="${top+h}"/><text class="score-cut-label" x="${n(cutX+(cutX>width*.6?-8:8))}" y="${top-12}" text-anchor="${cutX>width*.6?'end':'start'}">${escapeText(cutText)} →</text>`:'';
  host.innerHTML=`<div class="chart-box"><svg class="analysis-chart score-columns" viewBox="0 0 ${width} ${height}" role="img" aria-label="Score distribution on a log scale; exact counts are listed below the chart.">${cut}${columns}${labels}</svg><div class="ring-tip chart-tip" role="tooltip" hidden></div></div><details class="inline-details"><summary>Exact count in each score band</summary><div id="${target}-bars"></div></details>`;
  const svg=host.querySelector?.('svg'), tip=host.querySelector?.('.chart-tip');
  if(!svg?.addEventListener || !tip) return;
  svg.querySelectorAll('.score-col').forEach((group,index)=>{
    group.addEventListener('pointerenter',()=>{try {
      const row=histogram[index], scale=svg.getBoundingClientRect().width/width;
      tip.innerHTML=`<span class="tip-title">Score ${escapeText(row.label)}</span><span class="chart-tip-row" style="--role:${heatFill(index/Math.max(1,histogram.length-1))}"><span class="tip-role">Transactions</span><b>${escapeText(integer(row.transactions))}</b></span>${index>=cutIndex&&cutIndex>=0?'<span class="tip-foot">Inside the review queue’s score range</span>':''}`;
      tip.hidden=false;const x=(left+index*column+column/2)*scale;
      tip.style.left=`${Math.max(4,Math.min(svg.getBoundingClientRect().width-tip.offsetWidth-4,x-tip.offsetWidth/2))}px`;tip.style.top=`${Math.max(0,top*scale-tip.offsetHeight-4)}px`;
    } catch {tip.hidden=true;}});
    group.addEventListener('pointerleave',()=>{tip.hidden=true;});
  });
}

function periodNames() {
  return snapshot?.splits ? Object.keys(snapshot.splits).filter(name=>name!=='train') : ['validation','test','tail'];
}
function isoDay(milliseconds) {return new Date(milliseconds).toISOString().slice(0,10);}
function monthDay(iso) {
  return new Date(iso+'T00:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
}
// Text for a half-open [start, end) ISO window, e.g. Sep 9–18 or Sep 28–Oct 3.
function windowText(window) {
  const last=isoDay(Date.parse(window[1]+'T00:00:00Z')-86400000), first=monthDay(window[0]);
  if(last===window[0]) return first;
  return `${first}–${window[0].slice(5,7)===last.slice(5,7)?Number(last.slice(-2)):monthDay(last)}`;
}
// The last evaluation window runs to the run's final day, as in batch.evaluation_periods.
function periodWindow(period) {
  const window=[...snapshot.splits[period]], end=snapshot?.pipeline?.end_date;
  if(period===periodNames().at(-1) && end) {
    const next=isoDay(Date.parse(end+'T00:00:00Z')+86400000);
    if(next>window[1]) window[1]=next;
  }
  return window;
}
function evaluationPeriodLabel(period) {
  if(snapshot?.splits?.[period]) return `${{validation:'Validation',test:'Test',tail:'Later period'}[period]||period} · ${windowText(periodWindow(period))}`;
  if(period==='test') return 'Test · Sep 9–10';
  if(period==='validation') return 'Validation · Sep 7–8';
  const end=snapshot?.pipeline?.end_date || '2022-09-18';
  const date=new Date(end+'T00:00:00Z');
  const finish=date.getUTCMonth()===8 ? String(date.getUTCDate()) : date.toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
  return `Later period · Sep 11–${finish}`;
}
function updateEvaluationLabels() {
  const names=periodNames();
  const control=element('split'), previous=control.value;
  control.replaceChildren(...names.map(name=>{
    const option=document.createElement('option');
    option.value=name;option.textContent=evaluationPeriodLabel(name);
    return option;
  }));
  control.value=names.includes(previous)?previous:names.includes('test')?'test':names[0];
  control.disabled=names.length<2;
  control.title=names.length<2?'This release contains one evaluation period.':'';
  if(snapshot?.splits) {
    const train=windowText(snapshot.splits.train);
    element('evaluation-methods').textContent=`Training reference: ${train}; ${names.map(name=>`${name}: ${windowText(periodWindow(name))}`).join('; ')}. Evaluation views show separate chronological populations. Rolling history before ${monthDay(snapshot.pipeline?.start_date || snapshot.splits.train[0])} is unavailable.`;
    element('model-methods').textContent=element('model-methods').textContent.replace(/trained on [^;]*;/,`trained on ${train};`);
    if(snapshot.splits.validation) element('validation-window').textContent=element('validation-window').textContent.replace(/^.*?only\./,`${windowText(periodWindow('validation'))} only.`);
    return;
  }
  element('evaluation-methods').textContent=`Training reference: Sep 1–6; validation: Sep 7–8; test: Sep 9–10; ${evaluationPeriodLabel('tail').replace(' · ', ': ')}. Evaluation views show separate chronological populations. Rolling history before Sep 1 is unavailable.`;
}

function renderScoringCoverage() {
  const periods=periodNames(), trainText=snapshot?.splits?windowText(snapshot.splits.train):'Sep 1–6';
  const counts=periods.map(period=>snapshot?.model?.views?.[period]?.metrics?.rows);
  const total=snapshot?.full_rows;
  const evaluated=counts.reduce((sum,count)=>sum+count,0);
  const valid=['full','selected-population'].includes(snapshot?.aml_scope) && Number.isSafeInteger(total) &&
    counts.every(count=>Number.isSafeInteger(count) && count>=0) && evaluated<=total;
  element('scoring-coverage').hidden=!valid;
  if(!valid) return;
  const reference=total-evaluated;
  if(snapshot.population) {
    const p=snapshot.population,n=snapshot.network?.population;
    element('scoring-coverage-title').textContent=`All ${integer(total)} selected transactions scored`;
    element('scoring-coverage-summary').textContent=`BQ1: ${p.source_subset} · ${p.start_date} to ${p.end_date}, inclusive. BQ2: ${integer(n?.rows || snapshot.source_index_rows)} full-source transactions.`;
    cards('scoring-coverage-kpis',[['Transactions scored',integer(total),'BQ1 selected dates'],['Transactions in evaluation',integer(evaluated),'Frozen model · no retraining'],['BQ2 transactions',integer(n?.rows || snapshot.source_index_rows),'Complete source population']]);
    element('scoring-coverage-explanation').textContent=snapshot.model.evaluation_scope || 'Frozen-model evaluation; training and validation dates are excluded.';
    table('scoring-coverage-periods',['Date period','Transactions','Scope'],periods.map((period,index)=>[evaluationPeriodLabel(period),integer(counts[index]),'BQ1 evaluation only']));
    element('test-date-breakdown').hidden=true;
    return;
  }
  element('scoring-coverage-title').textContent=`All ${integer(total)} transactions scored`;
  element('scoring-coverage-summary').textContent='The frozen AML model scored every transaction in this dataset. The evaluation dropdown shows separate later-date populations, so its counts are smaller than the full scoring population.';
  cards('scoring-coverage-kpis',[
    ['Transactions scored',integer(total),'All transaction dates in this run'],
    ['Transactions in evaluation',integer(evaluated),snapshot.splits?`${monthDay(snapshot.splits[periods[0]][0])} onward · across all ${periods.length} periods`:'Sep 7 onward · across all three periods'],
    ['Scored, excluded from evaluation',integer(reference),`${trainText} · training-reference dates`]]);
  const trainingSubset=snapshot.model.training_subset || 'HI-Small';
  const medium=snapshot.pipeline?.subset!==undefined && snapshot.pipeline.subset!==trainingSubset && !snapshot.pipeline.train;
  element('scoring-coverage-explanation').textContent=`Why exclude ${trainText}?`+' The original model used these dates for training; the dashboard preserves that training-reference window and reports later dates separately for evaluation. These earlier rows still received scores.'+
    (medium?` The model was trained on ${trainingSubset} and reused for ${snapshot.pipeline.subset} without retraining. This is transfer evaluation; the synthetic populations may overlap, so it is not independent evidence of generalisation.`:' Training-reference scores are not included in the reported evaluation metrics.');
  const beforeTraining=snapshot.pipeline?.start_date && snapshot.splits && snapshot.pipeline.start_date<snapshot.splits.train[0];
  if(beforeTraining) {
    element('scoring-coverage-explanation').textContent=`The model training window is ${trainText}. All dates before validation are excluded from evaluation, including earlier source dates that were not training inputs. BQ2 covers all ${integer(total)} source transactions. Neither test slice retunes the model.`;
    cards('scoring-coverage-kpis',[['Transactions scored',integer(total),'All transaction dates in this run'],['Transactions in evaluation',integer(evaluated),'Validation and test periods'],['Outside evaluation',integer(reference),'Earlier source and training dates']]);
  }
  const breakdown=snapshot.model.test_date_breakdown;
  element('test-date-breakdown').hidden=!breakdown;
  if(breakdown) table('test-date-breakdown-table',['Test slice','Transactions','Precision@100','Average precision'],Object.entries(breakdown).map(([name,value])=>[name==='unseen_dates'?'Dates absent from development':'Previously reported test dates',integer(value.metrics.rows),percent(value.metrics.precision_at_k),value.metrics.average_precision===null?'—':Number(value.metrics.average_precision).toFixed(4)]));
  table('scoring-coverage-periods',['Date period','Transactions scored','Evaluation coverage'],[
    [beforeTraining?`${monthDay(snapshot.pipeline.start_date)}–${monthDay(isoDay(Date.parse(snapshot.splits[periods[0]][0]+'T00:00:00Z')-86400000))}`:trainText,integer(reference),beforeTraining?'Excluded · dates before validation':'Excluded · training-reference dates'],
    ...periods.map((period,index)=>[evaluationPeriodLabel(period),integer(counts[index]),'Included']),
    ['Total',integer(total),`${integer(evaluated)} evaluated + ${integer(reference)} excluded`]]);
}

function isModelQueue() {return Boolean(snapshot.model) && element('scoring').value==='model';}
function currentQueue() {return isModelQueue()?snapshot.model.views[element('split').value]:snapshot.aml[element('split').value];}

function renderAmlLegacy() {
  const rules=snapshot.aml[element('split').value], current=currentQueue(), metrics=current.metrics, model=isModelQueue();
  cards('aml-kpis', [[snapshot.aml_scope==='full'?'Transactions':'Cohort transactions',integer(metrics.rows),'Selected period'],model?['Review capacity',integer(metrics.k),'Top-ranked queue · no model threshold']:['Rule-flagged transactions',integer(rules.flagged),'Rule score ≥60'],[`Precision@${metrics.k}`,percent(metrics.precision_at_k),'Label-positive fraction of top queue'],[`Recall@${metrics.k}`,percent(metrics.recall_at_k),`${integer(metrics.positives)} labelled positives in period`]]);
  const note=`Exploratory ${model?'frozen model':'rule baseline'} · average precision ${metrics.average_precision==null?'unavailable':metrics.average_precision.toFixed(4)} · lift@${metrics.k} ${metrics.lift_at_k==null?'unavailable':metrics.lift_at_k.toFixed(2)+'×'}.`;
  element('aml-note').textContent=note+(element('split').value==='tail'?' Later-period evaluation; reported separately from the main test period.':' Positive labels evaluate the queue; scores are not probabilities.');
  if (snapshot.model) {
    table('comparison',['Scorer','Positive labels in top queue','Precision@100','Recall@100','Average precision'],
      [['Rule baseline',rules.metrics],['Frozen model · experimental',snapshot.model.views[element('split').value].metrics]].map(([name,m])=>[name,`${Math.round(m.precision_at_k*m.k)} / ${m.k}`,percent(m.precision_at_k),percent(m.recall_at_k),m.average_precision==null?'Unavailable':m.average_precision.toFixed(4)]));
  }
  bars('alerts', rules.daily, row=>row.day, 'alerts');
  bars('scores', rules.histogram, row=>`Rule score ${row.risk_score}`, 'transactions');
  element('queue-title').textContent=model?'Frozen-model investigation queue':'Rule-baseline investigation queue';
  element('queue-note').textContent=`Top ${metrics.k} in this period; ties use timestamp then source row. ${model?'Model score 0–1 is uncalibrated; evidence explains model margins.':'Rule score 0–100 is a priority.'} Select a row for evidence.`;
  table('queue',['Transaction','Time',model?'Model score (0–1)':'Rule score (0–100)','Payment','Prior-day USD',model?'Model margin evidence':'Rule reasons'],current.queue.map(row=>[row.transaction_id,row.timestamp.replace('T',' ').slice(0,16),model?Number(row.model_score).toFixed(4):row.risk_score,row.payment_format,dollars(row.amount_usd_asof),model?row.model_evidence:row.risk_reasons||'No rule triggered']));
  element('queue').querySelectorAll('tbody tr').forEach((row,index)=>{
    const button=document.createElement('button'); button.textContent=current.queue[index].transaction_id;
    button.addEventListener('click',()=>showDetail(current.queue[index])); row.firstChild.replaceChildren(button);
  });
  element('detail').hidden=true;
}

function showDetailLegacy(row) {
  const model=isModelQueue();
  element('detail').hidden=false;
  element('detail').innerHTML=`<h3>${escapeText(row.transaction_id)}</h3><p>Sender ${escapeText(row.from_bank)} / ${escapeText(row.from_account)} → receiver ${escapeText(row.to_bank)} / ${escapeText(row.to_account)}</p><p>${escapeText(model?row.model_evidence:row.risk_reasons||'No rule triggered')}. Prior hour: ${integer(row.txn_count_1h)} transactions. Prior 24 hours: ${integer(row.distinct_counterparties_24h)} distinct counterparties. Original payment currency: ${escapeText(row.payment_currency)}.</p>${model?`<p>Top three signed model-margin contributions; positive values push the score up, negative values push it down. Bias margin ${Number(row.explanation_bias_margin).toFixed(4)}; full total margin ${Number(row.explanation_total_margin).toFixed(4)} includes all features. This is not causal evidence or a calibrated probability.</p>`:''}<button id="close-detail">Close detail</button>`;
  element('close-detail').onclick=()=>{element('detail').hidden=true;};
}

function download(rows,name) {
  if (!rows.length) return;
  const keys=Object.keys(rows[0]);
  const cell=value=>'"'+String(value??'').replace(/"/g,'""')+'"';
  const csv=[keys.map(cell).join(','),...rows.map(row=>keys.map(key=>cell(row[key])).join(','))].join('\r\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a'); link.href=url; link.download=name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}

async function start(snapshotSource=null) {
  const loadId=++snapshotLoad;
  try {
    let source=snapshotSource||'dashboard.json', pointer=null;
    if(!snapshotSource && !location.pathname.includes('/releases/')) {
      const current=await fetch('current.json',{cache:'no-store'});
      if(current.ok) {
        pointer=await current.json();
        if(pointer.schema_version!==1 || !/^releases\/[a-z0-9][a-z0-9-]{2,35}\/dashboard\.json$/.test(pointer.snapshot)
          || !/^[0-9a-f]{64}$/.test(pointer.sha256)) throw new Error('Invalid release pointer');
        source=pointer.snapshot;
      } else if(![403,404].includes(current.status)) throw new Error(`Release request failed (${current.status})`);
    }
    const response=await fetch(source,{cache:'no-store'});
    if(!response.ok) throw new Error(`Data request failed (${response.status})`);
    const bytes=await response.arrayBuffer();
    if(pointer) {
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');
      if(hash!==pointer.sha256) throw new Error('Snapshot integrity check failed');
    }
    if(loadId!==snapshotLoad) return false;
    snapshot=JSON.parse(new TextDecoder().decode(bytes));
    if(snapshot.schema_version!==1) throw new Error('Unsupported snapshot version');
    updateEvaluationLabels();renderScoringCoverage();
    const fullAml = snapshot.aml_scope === 'full';
    element('status').textContent=`Snapshot generated ${snapshot.generated_at.slice(0,10)} · AML ${fullAml ? 'full population' : 'cohort'} ${integer(snapshot.cohort_rows)} rows · Networks ${integer(snapshot.full_rows)} rows`;
    if (fullAml) {
      element('aml-scope').textContent='Retrospective full-population queue. Rule scores are priorities, not probabilities.';
      element('score-scope').textContent='All transactions in the selected period.';
      element('method-scope').textContent='Both views cover the full HI-Small population. Features come from the verified AWS Glue run; rule scoring and snapshot export run locally.';
    }
    if (snapshot.model) {
      element('scoring-control').hidden=false;element('model-comparison').hidden=false;element('model-methods').hidden=false;
      element('aml-scope').textContent='Retrospective full-population queues. Compare frozen-model ranking with the rule baseline; neither score is a probability.';
      if(snapshot.model.inference_source==='aws-glue') {
        element('method-scope').textContent='Features and model scores come from verified AWS Glue runs. Rule scoring, explanation reconstruction and dashboard export run locally. Mule networks use the full population.';
      }
      element('scoring').onchange=renderAml;
    }
    if(snapshot.pipeline) {
      const p=snapshot.pipeline;
      element('status').textContent=`Run ${p.run_id} · ${integer(snapshot.full_rows)} transactions · ${p.start_date} to ${p.end_date} · Dataset version ${p.dataset_version??'unrecorded local copy'} · Refreshed ${String(p.refresh_time).slice(0,16).replace("T"," ")} UTC`;
      const scope=p.source_scope==='smoke-prefix' ? 'Demonstration sample' : `Selected ${p.subset || 'HI-Small'} population`;
      element('method-scope').textContent=`${scope}. Both views use the same verified transaction run. ${snapshot.model?.evaluation_scope || 'Optional training creates a candidate for separate review.'}`;
      document.querySelector('footer').textContent='Group 3 · Verified batch analysis';
    }
    element('split').onchange=renderAml;
    element('export-queue').onclick=()=>download(currentQueue().queue,`queue-${isModelQueue()?'model':'rules'}-${element('split').value}.csv`);
    table('variants',['Variant','Precision@100','Recall@100','Average precision'],snapshot.diagnostics.variants.map(row=>[row.variant,percent(row.precision_at_k),percent(row.recall_at_k),row.average_precision==null?'Unavailable':row.average_precision.toFixed(4)]));
    table('reasons',['Score','Reasons','Reviewed','Positives','False positives'],snapshot.diagnostics.top100_reasons.map(row=>[row.risk_score,row.risk_reasons,row.reviewed,row.positives,row.false_positives]));
    element('provenance').textContent=JSON.stringify({generated_at:snapshot.generated_at,pipeline:snapshot.pipeline,sources:snapshot.sources,provenance:snapshot.provenance,model:snapshot.model?{name:snapshot.model.name,provenance:snapshot.model.provenance}:undefined},null,2);
    bq1PresenterRefresh();
    selectDashboardView(activeView);renderAml();return true;
  } catch(error) {if(loadId===snapshotLoad) element('status').textContent=`Unable to load dashboard: ${error.message}. Serve this folder over HTTP and rebuild the snapshot if needed.`;return false;}
}
start();

// ---- Pipeline map: money-coloured dots flow along each link into a running stage; finished links stay lit ----
// The deployed status API may still name the BQ2 stage by its old id.
const PIPELINE_ALIAS={corridors:'network'};
const pipelineStage = id => PIPELINE_ALIAS[id]||id;
// The SVG's links in document order, as [from stage, to stage]; null is the Kaggle + FX source.
const PIPELINE_LINKS=[[null,'acquire'],['acquire','curate'],['curate','score'],['curate','network'],['curate','baseline'],
  ['score','export'],['network','export'],['baseline','export'],['baseline','train'],['train','export'],['export','publish']];
function pipelineLinkState(from,to,states) {
  const done=stage=>['complete','reused'].includes(states[stage]);
  if(states[to]==='running' && (from===null || done(from))) return 'running';
  if((from===null || done(from)) && (done(to) || states[to]==='running')) return 'done';
  if(['failed','stopped'].includes(states[to])) return 'failed';
  return 'idle';
}
function paintPipelineFlow(states) {
  const svg=element('ingestion-flow'), paths=svg?.querySelectorAll?.('.map-links path');
  if(!paths?.length) return;
  svg.querySelector('.map-flow')?.remove();
  const layer=document.createElementNS('http://www.w3.org/2000/svg','g');layer.setAttribute('class','map-flow');
  paths.forEach((path,index)=>{
    const [from,to]=PIPELINE_LINKS[index]||[], state=from===undefined?'idle':pipelineLinkState(from,to,states);
    path.id=`pipeline-link-${index}`;path.dataset.flow=state;
    if(state!=='running' || reducedMotion()) return;
    for(let dot=0;dot<3;dot++) layer.insertAdjacentHTML('beforeend',`<circle class="flow-dot" r="3.5"><animateMotion dur="1.8s" begin="${(-dot*0.6).toFixed(1)}s" repeatCount="indefinite" rotate="auto"><mpath href="#pipeline-link-${index}"/></animateMotion></circle>`);
  });
  svg.querySelector('.map-links').after(layer);
}

// ---- Pipeline map: each AWS service shows its official AWS Architecture Icon and colour ----
// Icons are the team's crops of the AWS Architecture Icons in its draw.io diagrams (artifacts/deck/img/aws), inlined
// so the page makes no extra requests. The Kaggle + FX source has no AWS icon, so it keeps a plain database glyph.
const SERVICES={
  lambda:{name:'AWS Lambda',color:'#ed7100',icon:'data:image/png;base64,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'},
  glue:{name:'AWS Glue',color:'#8c4fff',icon:'data:image/png;base64,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'},
  s3:{name:'Amazon S3',color:'#7aa116',icon:'data:image/png;base64,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'},
  cloudfront:{name:'Amazon CloudFront',color:'#8c4fff',icon:'data:image/png;base64,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'},
  sagemaker:{name:'Amazon SageMaker',color:'#01a88d',icon:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAE4AAABOCAIAAAAByLdKAAAaGklEQVR4nOVcCXhVxb2fmTPnnHtvdpKQhRASIHvCFmLYAwQUqlUrWlRc2qpdtL7XUrVawdYF21etC7Y+q7W1RbHlK6IVcAHBAJKwRLbskJAQIAvZt3vPMjPvm5mbyyUsTVhe+9kB8t2cTObMf/777/8fYMC7z2HVQRgDX9EBAaDE6lm8FPeaBv/2q0sqH7YFAMAAIYzQV5yrCAEA+L//kIHAf8xAV2JRJtQB+n3+p/Nh//wrZzXQZV5P7FNBiDFAic1fABF/ej4CxHOMEGWUEoIABBDy07kCBKPLthLj+4YQIAiJ6QGAJgYNY5RQ20QQwbMJEJ/FQQDbcIeojrjAEGJ5GKUYIQCh99j+3UhljAHImUmJTQx3XnTi5jm3HFhw19tTr0sLiSCGmxIiCRAEC4mFEAFIbIPZ1nXxqdvzby1b+K2Xcq4Owtg2PJy94DKzF4JVz2LtEkIITiRUILQpBYYnNTx6WcbU2xLSEGcLH92W+dbRkhUlhU29nUDVpWPj820LUJodHrM8a9oNcUm+9Yrbmh49sG1zfRXQdIwUsTHmU+SLohBQ22JLfnoJXD1DAj1OpDyQkbslf/GSxHQEYUFz/cP7Cw52nApStQeTJ+2Yv+TupEkIANv0MMpsjzvGGfi73GsKr7lT0rm2vmr5wR29lpk9LOqjvEUv5y4IUi4ze+HFcFVYVZ8EAgCvG5n87LiZWaERAIBjfV2/LN312pEDwDJczsBvJWYsz5wa7QgAAOxqaXiqtHBL07F7Rmc+kn5VvCsYAPBlW9OKsqL3jlUBYk2MjHtlcv70iBEAgL1tTY9dDvbCfq4OmVSpZlwCiQ0IyQ6PWZY17UbBGZOSP1QfeqZkZ0N3B9B0FSkWsYFlZEXE/jhl8l2jMxUIKWN1vV2JgSEAgDbT83RJ4W+rvrQtE2o6X9MyVaw+mDzpicypIapuU/rq4X3LDm7v9rgVzUH5y7lbEkbripLK+D+EEKWUWUaQI+AXWdN+mDxJQwoAgDJW2dWWvuGPgFGsaoTvqv9QLAswMnV4/LPjZ80aHocg7LOtt2vLXqzYW9He5FNgvjhAhBFgWRMiYl+YNHdO1MhLZy+8CF1lkFsgYhqM0XtTJn8xf8nS1BwNKYWtDW/VlEAIbcZ0rEIFcxMl6ASMEUoRxorqKGw6tmDb2n3tTZSxFyuLv1fwXkVXm6I7+S9SKndFGIUQKbpjf1vD/K1rfrLv807TmHw+7R3iQIOlk0sOZMTOix61Ze7iN666Ois0ot30PFWyc+bHqzY0HBXCxWi/hHtlTHzg4QRgSNMNQrosi3OV2AirGtYoPxSu9l4OiNOhlClYJwC8UFI4+7O/bm2qxwj9V0r2lvzb5sWNJUYf4ycyZGrRYOdByGxz5vD4rfm3zomKZ4z9/vCBaZ++8/P9BYSYoZoO+RDvPluRxANK+f4UsUWutFzmqTiLAZP5E8FeiDl7G/3Z+3HeopW5CwMUzMRqV4pUQOkIVyCEsMcyr9/+/vcL11d0teq6E0Dkk0A52f+8T3/2Z92ZHwYMn5JTxhRVAxAK9r67tbleQejBlEl/m3495vynV4RUJkIFk1LGWKvhXn+yGmkOJNXyrE1L8fX9HdLwyr/w1Tz2IoRYJtL0/W1N8z/764rSIkLp3KiRLlVn9MoIMBSvh/0jAOte038mkfLlDDJKbEoIpYQNUamkujJK703JXjPjxo9mL3oocyoCQFM1QOmnjXUIQg8h3CwMUYAxuKjBjQnjYj1wo94fs4fTc6dHjlhzrHJ19SGkaoOklXF/A6llPjNpzuMZU+TDBTGJUyNiFxW8Bww3FYd7cVEiBhc9xAslxyhjNucg5dZWPF0YkzgnKr6+t2v14f081xkcYzmdtj0mbPgjaTmUsX+cONJpmrcnpN0Ul7R6xg2Hu9uTg8J8wvX/SKqfvGlIwQiFqDoXKsq1uts2bUr7iO3NZgY3uPGlJCkoVIGow/R8Y/sHwNM7adj9acHht41K9Zt2MREiBpc2KGMAKbW9ne8fP3K4u51IFeLuhKNzMr+RxzGo1QBfraq7gzIWiLXnJ86u7e1MDAjBCG1rPl7T0xHnCpoXPYpnjEPPZvEg50nOoDPBFO7uAQCqtqmxbtPJo/z1SJGEedGWC+AP53kPVJSjXa3/OFl9U1zST1Iny6f72puvLVjb09s1Oz5lXvQoKepyA4M3TngwVAIIeZhKaY9tQa541E1sLmwCK+D6yf0gj4SFqYKEEoPKKBj4C7B84A87DbDhMnBiEN5dtLFpfN51I8Y4kPLBieonD+3osU2kahpSuHlmrNMyuCeDSERig0oA8D8lEkFEATMMt1N3zhk+0qY0VHWMD4ncf6oeYE1BChVBktQeCAAx3COCw5MDwxgAuqL4gmFBh5c+gxKeFfDjE2z3BRXSryKlx7Lu3/3Jw7oTQdjt7uVgtaLZtlsV2I2GlJtGJq2tKWWIKNy8c+L/acaDzkfkQExkZOrOq+98OC0HIxSmO7blL14xaa5TUYhtyCUQhNyRmuYdY8cXzr8tIzQCQbi/7ZQIzSUdfEWBmLHkwDCEdY/hlpzxx9lkcAsRQljttcxu01BUHSmYR5EI1fd1dVtmAFb/Pv36d2bemBYaOQDNuYAJROcLjPieiE0sI3tYzLq8mz7M+8aEsMge23rjyIF97c1BqvazjCnb5t0+ZXg8z3UoJYYnzhm4bvaiVVOvHekKbjc9D+zd/FbNIajp3FbxNyFgWyfcPQpC3xqduXPh3dfFp1DL9OFsHGqEEAt75hUThCDiMkV5gM8gVg+2N8/atHr9yWoAwO0JabuuvmNl7oIoh8s23Dy0Rmc5er8xMF9lQGQwjAHLiAkMXZYx9b6x41SRkX5w/PCyA9tL2pqCHa4V42fenzRRBC72ixXFK0p23hif/Mvxs0a6ggAAHzcc/UnxlrK2Rqg5hV56lYkSMjIg+MnMaXeOzsACqVldW/5MaWF5WyPAKkQKswxACUAKwBpPMM60OlLQmGUCrN6dmLE0JXtcaCQA4Eh3xzOlRatqS6ltAVXzqsygUnPGM9Pvjh23LHPqCGcgAKCopWFFWdH6Y5UcMVM1QgiwrXlxY1+aNCcjhIMsJ9w9cmar4X7i0BevVn4JAPNP0E9vlBBAOXbxxLjp148YK3G2Px8teapk5ynDfXN8anJQWHlX64cna2xCAEJn7LsfzaKMMst0OpzfTsz0oTl7Wxt/enDbloZapGB/IT4vqfwHljkjOmH7vFsBAHW9Xb8q2/X6kYOUmEjVIYAy85LTgnTns+Nn3jtmnEPo0qeNdZKZSHdCmWf7mwrheYSkQmIZAzhT0dXW4O6ZExUv525vPn799vc7jL4B+z4T8SHAMjIjYpem5twan+LEal1vV8qGPxrUFvo/kNSBuspdCHffKmWsxzbnbl3zWtluCoGmObl1FXRK3VdUvYfYD36x/pWqfZSxnS0NCz9fW9bZgh0uJmb6S5GA7b0GlgKmqDoA4M9V+6ZseueBvZsb3T2pwcPmRMVTxko7Wnp5Yhz3dNZ04M0Nz9yh2IBNKUQIO1wlHae+s23dzw7ukNxShCAMyixJn0CE7bYpbXD3qrpTQci0DCIKEyI8EEgKoypSIELtpgdB2GK4AWMIawJt8W5IpjXcaHHb05+8s9OZt9u2Xy3bdXXB2vLOVpvS144cyFz7ylOlRZSxBTEJmu6k56HWh+ZoWIOK0mZ6ZNI/QL0vRKoEBXzJmkNRLMsgpjFpWPTq6dc/kDSB2ab08lCkzkxUXBhj0txTyUwv2AIBsaMcro15i+bGJhJPHyW2mOaVQ8kZ1RFwqPlEaWcrgjDaEZAYNjzBFcwAa3T3ccCxX8/9BdhHseAKT1ul0b5wGoDP+dSbdvKgxJwYEbs0LeeWkSm6ouQNj1tdV9Fu9EFhk/2P+ZynSCh5MnP6wtjE2cPj/ny09MWKvVXtzUDTT1tXIc8QoY2NR2+OT75pZNLXRox2SIN/8gizTYU3L3iRGsq4qgukRkiW70AHN/A5n8rfxhCtnvK1G+PG6gqftqulYeXhfT2WCYTSM8aBUk4Pd3un4xWfyeWnreA1xyoyQsJnRI74ftKE6+PGLj+4462jpbxIp2BpXXnCgNW/VB9KDgz9UUq2A+E+23qz+tCL5Xsg1iignDxKbdsESOHyrChIUbn4DDG5wRcgNRCri0XqVNTSsLLqy78dq6SWB6gcMQOMGwCLEkbsENUBAHBhzCgBvBLH4yHunCGDUNlysnr7qRP3jcl6LD03zhX0Zu6CH4ydcFvhhiNdrdK68mkAEgAe21fwRs2heFdwTU/nsa4W7mnlkRE7SHM8mDElJTis0d37x+pDlZ2nINbOxkAuhlQmvhLA9rY0vuxHJNad0tAhBInhCdCdy6YsfDBpIoRwWviI5eNmPH1wO4WKgjGh/NR5BKM6LEZfLdu18WTN8sxpt8enTg6PXhyfsmLf54qi2v0RC1cyjGu62mo6WwBUFJVj+WITJMIR8PHsm7OHRcm93Ttm3JzP/nawrRFhlV0ubKnXtq4p+Pu7h/dTwHA/No0AFGGgOy8msWDebY+m5zoxbjc9Loyfypr+ft7NMY4AYhrcVsnzksbW4artbr+naOPetiYiVE76R/kir7kGAClYwTpSFAHoMB4cE/un6bnZw6I6TM+qo2UVXW3DNMfzE/O48Zcmil0aqUx85ZEnY4rukETymFGUiQMU/Mvs/M/yF2cPi6KMvVCxJ2vDm7+p2AsAuCFu7Edzb5kQHmN7+tBprwA4kxWMscqdqp9N8r3RG1GJ7NwX+HM1VnBSYChh9I9HS+/a+KcVwg+lBYVbEBLb8trzwcEc6JxP5WnLjfqCO65ThMyJTdw679ZH03MVCA92tMzdsuYnezafcPc+VPzZA3s3d5rG+NDIgnm3fjslm3JcT3o64WdFTdWiZAB5F9ocRIBYjZ5eBaLJw6JuSr9Kwi4hmv7KpLlRzkAZ5XO046JJHTDknriHIPaM4SNzhkX32dbz5btnbHq74ORRRQDCCKuvlu26tuC9w93twar+h6uumTl8JLB52UImg9Rwp4ZGjgkM9QLogxoMQLS6rpwyNisybu2cW74WOxpBGKRqP0ye2F+zhZR3mYnZF+QuuvCr/LtOuP5g/OuKPX+qKbl227qHd2/qpQTrOg++ZSzhcH3RWHvdtnUN7h4IQE54NCA2T6YZI4b7jqQJW+fcMiqA11QHaTm5VqvatoajN2x/v7DlZIfpqepq+6/iLffu+uREX8/YoNC3piz4Yt5t+fEpUlguvCw+6xhF6CZrTYy5ie3tOpFHhhSDkO8UbeSa5nDybFO4BC7hDNiAQlWv6mpvcPdGOwK4DUPI4KmM/VzO/IfScgAAFiUqVAYJgkmvC1V9fX3lRw1HwzRHt2UapgGo/VlT3fKMqXclZuSER2/Kv+1wd5sPxADnGecO96X91JGSGRJBTQ+1bZ4jC5o4FIg1QEiggm9JzFARYpTIYJB7UkY1hQOlPL5BiJqeGIdrXd4iSefmprqKLr6nwZcgvD4c64SxFk+vQTnCwu15T8c9RR9N+XTVO7XlJrWTg4cRRnF/bAwGQyrnKkKthhtB6MLq1rmLX512bVpoOMc1bBshRIhlG31XjxizI//WNdO//lhaLqDkNDQkIFrJsx7bmhyTsCV/8Y1xPC99vnzP1Z+tafb0DbUNi0NHwvgjAUdyfeHipQJGi5uPr6mvPGW4TUIUiLpsyxc2/3MBlgq5u7Xh3t2fLE3JTg+J+EHShDsS0t+uLfttVXFZa1N6ePTP0qcsSUiTziAIY4Tw6XyC8cOT7mTJqNTnJuSFqFq76fnBnk1/qzkEVV0X8a23PDm44dNA0RnldXgAKV8fmfzj1Mm+FHdr07FH9hVYxD47xT03qTKwZBC8WVm8uq7izoT0B5ImjAuNlARvOFGdHz0q0uECAHzWWLf84I7CpjooQH0v7MaV05aBTt5wXt7f1978naKN+1tOimYGbmm87xn6kMgrJfb82DGPZUzxEbmlqe6liuIPTxzhQTXmmfY57dM5AkNvmq873cR6vWLPqtqyOxLSfpg0cVxo5K0JaQCA2p7Op0qL/lRzEBCCNKeMbLzIgLtn6oixcQJhAgD85WjZ0uLNrZ4+rDttbiTPm2R5kfSzkKQz2cDRqREBIR/OXqQjhTK2tenYy5XFH56oBsSGms7xTXbeEvM5SJUWlWfnvC2BE/xGRfHb1YdemDzvntFZh7vb533+94aOU1B3IhXzgIYHQIhQYtvWd1JzfjV+Vpjm6LOtxw/seKl8F1AUReO9KhegU5KBIOIoh0RGAeRozZl5tnhAh+kOyIBFyPeKN//p8H5AKScS8514s+WhZTb9CbEkWHc43b3d+9ubVYSaPH0NfV2KwyWkkUoghViGU9WeyZm/VJQeyjvbvr3ro12NtYru5OCLKOZf2L9zDbQMqGqBqtZtGcS2oShV+m9c8p0n9OLbrU3HAASq7rA5ziF7DYbiV89JMOcJQk6Fmx9edEKK3L30BJDRjLDhv89dMC0iFgCw7vjh7+76pMXdw4/jHKWKcwye9Frm/BFjf545NTEgpLSrdWXVl+vrqyBW/X9d1qR8Re1ArPGmmEEQOdiajQ/F8L1Gnm7/6vy7N3IXTImIpYw+fvCLX5UWcZHWdHnSAyWqvx4LKOUphJALYpmzYkZ/MnuRnBzrCpwzfOQN29/fWF+pqPrZ4JI3j5XndjnLUxcc3NER+8mSwucm5C07tOODo6VQ45mQZPvZ8yUItCQhbXJYlMa9As9XTWJnhkZCCPe1N71Vte+6+NT5MQkPJE3YeOLI0Do7rnh9VcEfN9RsbznR6+lTdJesO5zPPMhAPy04PC04fMCPCKOfNNSt3PVxH0T50aMidKcAVog/qPuvJBUImiBUejnkxXtBfHj8AFxfTpf1m+2n6j9vOh4g0lcEYK9tzotOmBoRe2dCWsjCb82NikcQ1vV283LOAKjhdMHlypDKvF+963t7G2RZtb+cBWV9wc+YyZKTdDI+88urjAD840TN87s/Ba4g0XsEgW39OSJ285xbEgJDfpA0AQDQbnqeLSsauA1ZBO3vuhtqNxoe1Czv0tL9UGIasD+hOcNbnrZavCjGIXIO8GHBdg7T+KyUS8HY4eJxhWAy1J3V3W3XbnvvxymTY50BFd3tfzhyoLy9GaqaVxeE21EQsm2ryza8OKMfGy4bqVCkCw6FZw5jA0NvGJX2QV05hwPFVvwDHW94CBExPUGa64Wr5ha1NrxZuVfRHeRMl2PzvI/7bfE9RQou6zh1X+EGgDGwTKBgpPJM2NtuILTENg2Hqt81KgMAoCFFgaJ4NWhq8WAm8Q1hXNhystsy41xB78+8YfXI5KdLdla0NwNVU0RZXf5RIEf6ienOix29Mjt/XGjknQnpnzbW1vd0DEDJBwwu8AqGCuTgk+6UoY/8kWCmDYidFzv65Ulzx4fxctbetsZjvZ28Tjk4OsEgARcuRVjb0Xxs1qZ3Pm2olWXcL65ecn9qDmKMWIasPnGhtU1KrKUZ0zbNuWVcaGS3ZT5+YHuzuwf57cm/NcL7SHhp4WT5E79eYs432+OOcwX9ZcbXt8z95viwSMLY/1btW7j1712mAfpxycspwEyc+v62pgUFa+8bk/XfydnpIeG/y5l39+jMh/cXbDtRDVSN2FZqaOSLk/MXxCQCAA51tNy7+5PdjbVAeFreoCGLAKLQgmTQS+3+bKo/1j+jW55XEr6flvNYRq7sfufgw4EdhY21QOVVqSGh3niQ87gt5fg115/Xy/e8X39kxfgZ94zOuio8etPsm/9QffDnB3fkx6eszM4fLlK831Xte/zAtk7DjXl4KNpBLA9Ude5gGAtRdV6cs02MsX+52dtNI7rlCSHZEbH/M3F2vkjWTrp7ni4pfI3H90RxuLylk6F1tq8aQu/+GWVc28qLHf3chDwOlwFQ09MxyhWiIFjd3fHw/oJ1tWUAYyyKPbyhndqzY0Yvz5o2KzIOI9RtmW/Xlq2sLK5oEw3tCm9ol/6J64tpBDlcv8ia/sPkibJb/vUjB35TvqeqvQlqvOfFq8aDoxNe9DUFL5wmTCK1DF3T7xsz7qms6WGagzJqUnr7zg3rqopRQChWFNO2gGVkhMc+mp57u7x8w0Q3gYt3E7SZnufKd79c+aXb6BPXFJDo8qeLR2c+nj5F3u/Y3968/NAX6+vKAcJYVc8QgcENePGkegnma4hUmADLnBAx4qG0nCUJ6QCAFqNvVW3ZEwd29Lh7gl3BP0rN/lFKdpjm8F0++by5/u6E9B+nTk4SvZG7Wxtfqix+91gFMD0Z4TErxs+UN2/aTeOFij2/Lt9jmh5Fd/QX+4ZafwOXTKofbiAvjQBKvpGYsWLczLTgYbJ4t+Fk9TfjUyVzKrvaf1m2y9uEomBgGVFBYc+Mm3FXYoYU0fUnanacOv5oRm6o6B344PiRn+0vKGtrErcwxH2NofTYXWZSz75DRbiOOZ7InH5/0gQXVuXPZQPLMyWFTT0dQHOcvigmr+lExD6SlvvNUSm+9Uo6Wp4tLXq3roxjtGf2yFzcgJeNVB/BfveMJobHPJE1PS8yruBU/bID20vbGoGiqli1RR1Atn/IAIjaJmD0GwnpT2dNj3MG/aW29OlDO0/1dsoeGWF+hiyxV5jUAf7QMoCiRDgCWtw9vGFW5WXfgW2AvtgdMG5yXYFOBTf3dAJFwYoqK/GXwsyzSb3kJO70kt4gmELe50MBB+MRdzaCOX6XaXzzRRzGcwBFd3RbRrfpUVSdAWaLcsFlodN/XD5S5RD7kwilDAbl9Z/zCqGIw0QwIK63nOvqw78rqQOSuUEU3fzB+yF1rAx1IPAfM3B/VekrO2D/B8z/gxNxMRF8hQflhWYc5AhwqLx6+a/ezpUaCABD4fHM/wH2/hbnrtsuWQAAAABJRU5ErkJggg=='},
  stepfunctions:{name:'AWS Step Functions',color:'#e7157b',icon:'data:image/png;base64,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'},
  source:{name:'Kaggle + historical FX',color:'#20beff',glyph:'<ellipse cx="10" cy="5" rx="6" ry="2.2"/><path d="M4 5v10c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2V5M4 10c0 1.2 2.7 2.2 6 2.2s6-1 6-2.2"/>'}};
// Which services each map stage runs on (the publish box stacks Lambda, S3 and CloudFront).
const STAGE_SERVICES={acquire:['lambda'],curate:['glue'],score:['glue'],network:['glue'],baseline:['glue'],train:['sagemaker'],export:['glue'],publish:['lambda','s3','cloudfront']};
const serviceTile=(id,size=28)=>{const service=SERVICES[id];
  if(service.icon) return `<g class="svc-tile" data-service="${id}"><image href="${service.icon}" width="${size}" height="${size}" style="clip-path:inset(0 round ${size/5}px);filter:drop-shadow(0 2px 6px rgba(0,0,0,.45))"/></g>`;
  return `<g class="svc-tile" data-service="${id}"><rect width="${size}" height="${size}" rx="${size/4}" style="fill:${service.color};stroke:none;animation:none;filter:drop-shadow(0 2px 6px rgba(0,0,0,.45))"/><g transform="translate(${(size-20*size/28)/2} ${(size-20*size/28)/2}) scale(${size/28})" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${service.glyph}</g></g>`;};
const serviceChip=id=>`<span class="svc-chip" style="--svc:${SERVICES[id].color}"><svg viewBox="0 0 28 28" aria-hidden="true">${serviceTile(id)}</svg>${escapeText(SERVICES[id].name)}</span>`;
function decoratePipelineMap() {
  const svg=element('ingestion-flow');
  if(typeof svg?.querySelector!=='function' || svg.querySelector('.svc-tile')) return;
  svg.querySelectorAll('[data-stage]').forEach(node=>{
    const services=STAGE_SERVICES[node.dataset.stage]||[], box=node.querySelector('rect'), width=Number(box?.getAttribute('width'))||190, height=Number(box?.getAttribute('height'))||100;
    const lines=[...node.querySelectorAll('.map-service')];
    node.insertAdjacentHTML('afterbegin',`<rect class="svc-accent" x="0" y="10" width="4" height="${height-20}" rx="2" style="fill:${SERVICES[services[0]]?.color||'#2a4459'};stroke:none;animation:none;filter:none"/>`);
    services.forEach((id,i)=>{const line=lines[i];
      // Beside the service name; a smaller tile in the corner when the name would run under it. The map can be hidden
      // while this runs, so the name's width is estimated from its length (15px semibold) rather than measured.
      const crowded=(line?.textContent.length||0)*8.4+14>width-44, y=line?Number(line.getAttribute('y'))-19:10;
      node.insertAdjacentHTML('beforeend',crowded?`<g transform="translate(${width-26} 7)">${serviceTile(id,20)}</g>`:`<g transform="translate(${width-38} ${y})">${serviceTile(id)}</g>`);});
  });
  const source=svg.querySelector('.map-source');
  if(source) source.insertAdjacentHTML('beforebegin',`<g transform="translate(${Number(source.getAttribute('x'))-100} ${Number(source.getAttribute('y'))-18})">${serviceTile('source',24)}</g>`);
  const orchestrator=document.querySelector('.map-orchestrator strong');
  orchestrator?.insertAdjacentHTML('beforebegin',`<svg class="svc-inline" viewBox="0 0 28 28" aria-hidden="true">${serviceTile('stepfunctions')}</svg>`);
  document.querySelector('.map-legend')?.insertAdjacentHTML('afterend',`<div class="svc-legend" aria-label="AWS services in the diagram">${['source','stepfunctions','lambda','glue','s3','sagemaker','cloudfront'].map(serviceChip).join('')}</div>`);
}
decoratePipelineMap();

// ---- Pipeline map: a packet of data travels the whole journey, Kaggle to CloudFront, on a loop ----
// Fractions of one cycle: each link carries the packet in its window (same order as PIPELINE_LINKS), and each stage
// lights up as the packet reaches it. Box colours keep showing the selected run; the packets show how data moves.
const JOURNEY_MS=9000;
const JOURNEY_LINKS=[[0,.1],[.13,.24],[.28,.42],[.28,.42],[.28,.42],[.47,.6],[.47,.58],[.47,.58],[.45,.51],[.53,.62],[.67,.79]];
const JOURNEY_STAGES={acquire:[.1,.17],curate:[.24,.31],score:[.42,.5],network:[.42,.5],baseline:[.42,.48],train:[.51,.56],export:[.6,.69],publish:[.79,.97]};
let journey=null;
function startJourney() {
  if(journey || !animating()) return;
  const svg=element('ingestion-flow'), paths=svg?.querySelectorAll?.('.map-links path');
  if(!paths?.length || typeof paths[0].getTotalLength!=='function') return;
  const layer=document.createElementNS('http://www.w3.org/2000/svg','g');layer.setAttribute('class','map-journey');layer.setAttribute('aria-hidden','true');
  const links=[...paths].map((path,index)=>{
    const dots=[0,1,2,3].map(trail=>{const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');
      dot.setAttribute('class','journey-dot');dot.setAttribute('r',String(5.5-trail*1.1));dot.setAttribute('opacity','0');layer.appendChild(dot);return dot;});
    return {path,length:path.getTotalLength(),dots,window:JOURNEY_LINKS[index]||[0,0],optional:path.getAttribute('stroke-dasharray')!=null};
  });
  svg.querySelector('.map-links').after(layer);
  const legend=document.querySelector('.map-legend');
  if(legend && !legend.querySelector('.journey-key')) legend.insertAdjacentHTML('beforeend','<span class="journey-key">Moving dots: how data travels through the pipeline</span>');
  const nodes=new Map([...svg.querySelectorAll('[data-stage]')].map(node=>[node.dataset.stage,node]));
  const start=performance.now();
  const frame=now=>{
    if(!journey) return;
    try {
      const u=((now-start)%JOURNEY_MS)/JOURNEY_MS;
      for(const link of links) {
        const [from,to]=link.window;
        link.dots.forEach((dot,trail)=>{
          const v=(u-from-trail*.011)/(to-from);
          if(v<0||v>1) {dot.setAttribute('opacity','0');return;}
          const point=link.path.getPointAtLength(v*link.length);
          dot.setAttribute('cx',point.x.toFixed(1));dot.setAttribute('cy',point.y.toFixed(1));
          dot.setAttribute('opacity',String((1-trail*.24)*(link.optional?.55:1)));
        });
      }
      for(const [stage,[from,to]] of Object.entries(JOURNEY_STAGES)) nodes.get(stage)?.classList.toggle('journey-hit',u>=from&&u<=to);
    } catch {}
    journey.raf=requestAnimationFrame(frame);
  };
  journey={layer,nodes,raf:requestAnimationFrame(frame)};
}
function stopJourney() {
  if(!journey) return;
  cancelAnimationFrame(journey.raf);journey.layer.remove();
  journey.nodes.forEach(node=>node.classList.remove('journey-hit'));journey=null;
}
document.addEventListener?.('visibilitychange',()=>{if(document.hidden) stopJourney();else if(activeView==='ingestion') startJourney();});

// Ingestion is independent of snapshot loading: controls work before the first release.
(function ingestionControls() {
  const KEY='is459-ingestion-';
  const stageLabels={pending:'Waiting',running:'In progress',complete:'Complete',reused:'Reused',skipped:'Skipped',failed:'Failed',stopped:'Stopped'};
  let config=null, auth=null, timer=null, polling=false, historyBusy=false, submitting=false;
  let currentRun=null, lastStatus=null, failures=0, loadedRelease=null;
  // Public read-only status: shown when no operator is signed in. It never starts or changes runs.
  let statusUrl=null, statusTimer=null, statusBusy=false;
  const read=name=>{try{return JSON.parse(sessionStorage.getItem(KEY+name)||'null');}catch{return null;}};
  const save=(name,value)=>value===null?sessionStorage.removeItem(KEY+name):sessionStorage.setItem(KEY+name,JSON.stringify(value));
  const message=text=>{element('ingestion-message').textContent=text;};
  const signedIn=()=>Boolean(auth && auth.client_id===config?.client_id && auth.expires_at>Date.now()+5000);
  const stopPolling=()=>{clearTimeout(timer);timer=null;clearTimeout(statusTimer);statusTimer=null;};
  const readOnly=()=>Boolean(statusUrl) && !signedIn();
  const random=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
  const base64url=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  const date=value=>value?new Date(value).toLocaleString():'—';
  const elapsed=seconds=>`${Math.floor(seconds/60)}m ${Math.floor(seconds%60)}s`;

  function updateControls() {
    const online=Boolean(config), login=signedIn(), pending=Boolean(read('pending'));
    element('ingestion-signin').disabled=!online || submitting;
    // Without deployed controls, show status only: no disabled form that reads as broken.
    element('ingestion-signin').hidden=login || !online;
    element('ingestion-new').hidden=!online;
    element('ingestion-layout').dataset.mode=online?'':'read-only';
    element('ingestion-signout').hidden=!login;
    element('ingestion-signout').disabled=submitting;
    element('ingestion-fields').disabled=!login || submitting || pending || lastStatus?.status==='RUNNING';
    const medium=element('ingestion-subset').value==='HI-Medium';
    const fullOption=Array.from(element('ingestion-size').options).find(option=>option.value==='full');
    if(fullOption) {fullOption.disabled=false;fullOption.textContent=medium?'Full dataset · about 32 million transactions':'Full dataset · about 5.1 million transactions';}
    element('ingestion-rows').max=medium?'32000000':'5100000';
    const full=element('ingestion-size').value==='full';
    element('ingestion-rows-field').hidden=element('ingestion-size').value!=='custom';
    element('ingestion-preview').disabled=!login || submitting || pending;
    element('ingestion-train').disabled=!full || medium;
    if(!full || medium) element('ingestion-train').checked=false;
    element('ingestion-train-help').textContent=medium?'HI-Medium uses the existing model; candidate training is unavailable.':'Available for full HI-Small runs. Requires separate review before promotion.';
    element('ingestion-start').disabled=!login || submitting || lastStatus?.status==='RUNNING';
    element('ingestion-start').textContent=submitting?'Submitting…':pending?'Retry same submission':'Start ingestion';
    element('ingestion-history-refresh').disabled=readOnly()?statusBusy:!login || historyBusy;
    element('ingestion-refresh-status').disabled=readOnly()?statusBusy:!login || !currentRun || polling;
    element('ingestion-connection').textContent=login?'Signed in':readOnly()?'Read-only status':!online?'Setup pending':'Sign-in required';
    element('ingestion-connection').dataset.state=login?'running':'';
  }

  async function request(path, init={}) {
    if(!signedIn()) {auth=null;save('auth',null);stopPolling();updateControls();throw new Error('Session expired. Sign in again to continue tracking.');}
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(config.api_url+path,{...init,cache:'no-store',signal:controller.signal,
        headers:{'Authorization':`Bearer ${auth.access_token}`,...(init.body?{'Content-Type':'application/json'}:{})}});
      const body=await response.json();
      if(response.status===401) {auth=null;save('auth',null);stopPolling();updateControls();}
      if(!response.ok) {const error=new Error(body.message||`Request failed (${response.status})`);error.status=response.status;throw error;}
      return body;
    } finally {clearTimeout(timeout);}
  }

  async function signIn() {
    if(config.auth_mode==='aws-iam-helper') {
      element('ingestion-aws-signin').hidden=false;
      message('Run the local AWS sign-in helper. It verifies g1team3 and opens this dashboard with a single-use sign-in link.');return;
    }
    try {
      const verifier=base64url(crypto.getRandomValues(new Uint8Array(32))), state=random();
      save('oauth',{verifier,state,created_at:Date.now(),client_id:config.client_id});
      const params=new URLSearchParams({client_id:config.client_id,response_type:'code',redirect_uri:config.redirect_uri,
        scope:'openid ingestion/control',state,code_challenge_method:'S256',
        code_challenge:base64url(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier)))});
      location.assign(`${config.cognito_domain}/oauth2/authorize?${params}`);
    } catch {message('Unable to open sign-in. Allow session storage and use a secure browser connection.');}
  }

  async function finishSignIn() {
    if(config.auth_mode==='aws-iam-helper') {
      const fragment=new URLSearchParams(location.hash.slice(1)), code=fragment.get('operator_code');
      if(!code) return;
      history.replaceState(null,'',location.pathname+location.search);
      selectDashboardView('ingestion');
      const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
      let response;
      try {response=await fetch(config.api_url+'/session/exchange',{method:'POST',signal:controller.signal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});}
      finally {clearTimeout(timeout);}
      if(!response.ok) throw new Error('Sign-in link is invalid or expired. Run the AWS sign-in helper again.');
      const tokens=await response.json();
      if(typeof tokens.access_token!=='string'||!Number.isFinite(tokens.expires_in)||tokens.expires_in>3600)
        throw new Error('Unexpected sign-in response.');
      auth={access_token:tokens.access_token,expires_at:Date.now()+tokens.expires_in*1000,client_id:config.client_id};
      save('auth',auth);element('ingestion-aws-signin').hidden=true;return;
    }
    const params=new URLSearchParams(location.search);
    if(!params.has('code') && !params.has('error')) return;
    selectDashboardView('ingestion');
    const pending=read('oauth');save('oauth',null);
    // Remove authorization codes and provider errors before any other navigation.
    for(const key of ['code','state','error','error_description']) params.delete(key);
    history.replaceState(null,'',location.pathname+(params.size?'?'+params:'')+location.hash);
    if(!pending || pending.state!==callbackParams.get('state') || pending.client_id!==config.client_id || Date.now()-pending.created_at>600000)
      throw new Error('Sign-in response could not be verified. Start sign-in again.');
    if(callbackParams.has('error')) throw new Error('Sign-in was not completed. You can try again.');
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
    let response;
    try {response=await fetch(config.cognito_domain+'/oauth2/token',{method:'POST',signal:controller.signal,
      headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'authorization_code',
        client_id:config.client_id,code:callbackParams.get('code'),redirect_uri:config.redirect_uri,code_verifier:pending.verifier})});}
    finally {clearTimeout(timeout);}
    if(!response.ok) throw new Error('Sign-in exchange failed. Start sign-in again.');
    const tokens=await response.json();
    if(typeof tokens.access_token!=='string' || !Number.isFinite(tokens.expires_in) || tokens.token_type?.toLowerCase()!=='bearer')
      throw new Error('Unexpected sign-in response.');
    // No refresh tokens retained. Sign in again when this short session expires.
    auth={access_token:tokens.access_token,expires_at:Date.now()+tokens.expires_in*1000,client_id:config.client_id};
    save('auth',auth);
  }

  function renderRun(run) {
    lastStatus=run;
    element('ingestion-empty').hidden=true;element('ingestion-run').hidden=false;
    element('ingestion-run-id').textContent=run.run_id;
    element('pipeline-orchestrator-state').textContent=run.status.replaceAll('_',' ');
    const activeServices=run.stages.filter(stage=>stage.status==='running' && stage.active_service);
    element('ingestion-active-services').textContent=activeServices.length?
      'Current AWS service: '+activeServices.map(stage=>`${stage.active_service} — ${stage.label}`).join(' · '):
      run.status==='RUNNING'?'AWS Step Functions — coordinating the next workflow stage.':
      run.status==='SUCCEEDED'?'Run complete. The verified release is available through Amazon CloudFront.':'No active workflow phase reported. The last recorded progress is shown below.';
    element('ingestion-flow').querySelectorAll('[data-stage]').forEach(node=>{
      const stage=run.stages.find(item=>pipelineStage(item.id)===node.dataset.stage);
      node.dataset.state=stage?.status||'pending';
      node.querySelector('.map-status').textContent=stageLabels[stage?.status]||'Waiting';
    });
    paintPipelineFlow(Object.fromEntries(run.stages.map(stage=>[pipelineStage(stage.id),stage.status])));
    element('ingestion-run-state').textContent=run.status.replaceAll('_',' ');
    element('ingestion-run-state').dataset.state=run.status==='RUNNING'?'running':run.status==='SUCCEEDED'?'':'failed';
    element('ingestion-elapsed').textContent=elapsed(run.elapsed_seconds);
    const s=run.selection;
    element('ingestion-run-selection').textContent=`${s.subset} · ${s.rows?integer(s.rows)+'-row demo':'full dataset'} · version ${s.dataset_version??'latest requested'}${s.train?' · candidate training':''}`;
    const list=element('ingestion-stages');list.replaceChildren();
    for(const [index,stage] of run.stages.entries()) {
      const li=document.createElement('li');li.dataset.state=stage.status;
      for(const [className,text] of [['stage-dot',stage.status==='complete'?'✓':stage.status==='failed'?'!':String(index+1)],
        ['stage-label',stage.label],['stage-state',stageLabels[stage.status]||stage.status]]) {
        const span=document.createElement('span');span.className=className;span.textContent=text;li.appendChild(span);
      }
      // The AWS service behind the step, in the map's colours.
      const services=STAGE_SERVICES[pipelineStage(stage.id)];
      if(services) li.querySelector?.('.stage-state')?.insertAdjacentHTML?.('beforebegin',`<span class="stage-svcs">${services.map(serviceChip).join('')}</span>`);
      list.appendChild(li);
    }
    element('ingestion-updated').textContent=`Last checked ${date(run.checked_at)} · updates every 10 seconds`;
    element('ingestion-run-note').textContent=run.message || (run.cache_hit?'Verified release reused; batch processing was skipped.':run.status==='RUNNING'?'You can leave this page and resume tracking later.':'Verification finished.');
    element('ingestion-results').hidden=run.status!=='SUCCEEDED' || !run.release_url;
    updateControls();
  }

  function resultPath(run) {
    const url=new URL(run.release_url,location.origin);
    if(url.origin!==location.origin || !/^\/releases\/[a-z0-9][a-z0-9-]{2,35}\/index\.html$/.test(url.pathname))
      throw new Error('The result link is outside this dashboard.');
    return url.pathname.replace(/index\.html$/,'dashboard.json');
  }

  async function loadResults(run) {
    if(loadedRelease===run.release_url) return true;
    const ok=await start(resultPath(run));
    if(ok) {loadedRelease=run.release_url;element('ingestion-run-note').textContent='Verified results loaded. Open BQ1 · Review queue or BQ2 · Mule networks to explore this run.';}
    else element('ingestion-run-note').textContent='Run succeeded. Results are not available here yet; select View verified results to try again.';
    return ok;
  }

  async function poll() {
    stopPolling();
    if(polling || !currentRun || !signedIn() || document.hidden) return;
    polling=true;updateControls();const id=currentRun;
    try {
      const run=await request('/runs/'+encodeURIComponent(id));
      if(id!==currentRun) return;
      failures=0;renderRun(run);message('Connected. Progress reflects the latest AWS workflow status.');
      if(run.status==='SUCCEEDED') {await loadResults(run);await refreshHistory();}
      else if(run.status!=='RUNNING') await refreshHistory();
    } catch(error) {
      failures++;message(`Progress update unavailable: ${error.name==='AbortError'?'request timed out':error.message} Last known stage is retained.`);
      element('ingestion-updated').textContent='Status may be out of date. Refresh to reconnect.';
    } finally {
      polling=false;updateControls();
      if(currentRun && signedIn() && !document.hidden && (id!==currentRun || !lastStatus || lastStatus.status==='RUNNING' || failures))
        timer=setTimeout(poll,Math.min(60000,10000*Math.pow(2,Math.min(failures,3))));
    }
  }

  async function track(runId) {
    if(!/^ui-[0-9a-f]{12}-[0-9a-f]{12}$/.test(runId)) throw new Error('Invalid run identifier.');
    currentRun=runId;save('active',runId);lastStatus=null;failures=0;stopPolling();
    selectDashboardView('ingestion');await poll();
  }

  async function refreshHistory() {
    if(historyBusy || !signedIn()) return;
    historyBusy=true;updateControls();
    try {
      const data=await request('/runs');
      table('ingestion-history',['Run','Status','Started','Finished'],data.runs.map(run=>[run.run_id,run.status.replaceAll('_',' '),date(run.started_at),date(run.finished_at)]));
      if(!data.runs.length) element('ingestion-history').textContent='No recent dashboard runs for this account.';
      else element('ingestion-history').querySelectorAll('tbody tr').forEach((row,index)=>{
        const button=document.createElement('button');button.textContent=data.runs[index].run_id;
        button.onclick=()=>track(data.runs[index].run_id).catch(error=>message(error.message));row.firstChild.replaceChildren(button);
      });
    } catch(error) {message(`Unable to load recent runs: ${error.message}`);}
    finally {historyBusy=false;updateControls();}
  }

  async function loadStatusConfig() {
    try {
      const response=await fetch('/status-config.json',{cache:'no-store'});
      if(!response.ok) return false;
      const value=await response.json();
      if(value.schema_version!==1 || !/^https:\/\/[a-z0-9]+\.execute-api\.ap-southeast-1\.amazonaws\.com\/status$/.test(value.status_url)) return false;
      statusUrl=value.status_url;return true;
    } catch {return false;}
  }

  async function publicPoll() {
    clearTimeout(statusTimer);statusTimer=null;
    if(!readOnly() || statusBusy || document.hidden || element('ingestion').hidden) return;
    statusBusy=true;updateControls();
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(statusUrl,{cache:'no-store',signal:controller.signal});
      const data=await response.json();
      if(!response.ok) throw new Error(data.message||`Request failed (${response.status})`);
      failures=0;
      if(data.latest) renderRun(data.latest);
      else {element('ingestion-empty-title').textContent='No pipeline runs yet';element('ingestion-empty-text').textContent='The next AWS pipeline run appears here.';}
      table('ingestion-history',['Run','Status','Started','Finished'],data.runs.map(run=>[run.run_id,run.status.replaceAll('_',' '),date(run.started_at),date(run.finished_at)]));
      if(!data.runs.length) element('ingestion-history').textContent='No pipeline runs recorded yet.';
      message(`Read-only view of the AWS pipeline, checked ${date(data.checked_at)}.${config?' Sign in with your team account to start a run.':''}`);
    } catch(error) {
      failures++;message(`AWS status unavailable: ${error.name==='AbortError'?'request timed out.':error.message} Last known stage is retained.`);
      element('ingestion-updated').textContent='Status may be out of date. Refresh to reconnect.';
    } finally {
      clearTimeout(timeout);statusBusy=false;updateControls();
      if(readOnly() && !document.hidden && !element('ingestion').hidden)
        statusTimer=setTimeout(publicPoll,Math.min(60000,10000*Math.pow(2,Math.min(failures,3))));
    }
  }

  function enterReadOnly() {
    element('ingestion-title').textContent=config?'Is today’s data in, processed and verified?':'Follow every stage in AWS.';
    element('ingestion-intro').textContent='Watch the latest pipeline run move from acquisition to verified results. Status comes straight from AWS Step Functions.';
    element('ingestion-empty-title').textContent='Checking AWS';
    element('ingestion-empty-text').textContent='The latest pipeline run appears here.';
    element('ingestion-history-title').textContent='Recent pipeline runs';
    element('ingestion-history-text').textContent='The 10 most recent AWS pipeline runs, however they were started.';
    element('ingestion-history').textContent='Loading recent runs…';
    message('Loading AWS pipeline status…');
    const show=element('ingestion-tab').onclick;
    element('ingestion-tab').onclick=()=>{show();publicPoll();};
    updateControls();publicPoll();
  }

  async function submit(event) {
    event.preventDefault();if(submitting || !signedIn() || lastStatus?.status==='RUNNING') return;
    let pending;
    try {pending=read('pending') || {request_id:random(),...selectedRunOptions()};}
    catch(error) {message(error.message);return;}
    save('pending',pending);submitting=true;updateControls();
    try {
      const receipt=await request('/runs',{method:'POST',body:JSON.stringify(pending)});
      save('pending',null);message('Ingestion submitted. Connecting to live progress…');
      await track(receipt.run_id);await refreshHistory();
    } catch(error) {
      // Definitive validation/conflict responses permit changing the selection.
      // Transport/server failures retain the original ID and options for a safe retry.
      if(error.status>=400 && error.status<500 && error.status!==401) save('pending',null);
      message(`${error.name==='AbortError'?'Submission response timed out.':error.message} ${read('pending')?'Retry uses the same submission ID and options.':'Check your selection or recent runs.'}`);
    } finally {submitting=false;updateControls();}
  }

  function selectedRunOptions() {
    const mode=element('ingestion-size').value;
    const rows=mode==='full'?null:mode==='demo'?1000:Number(element('ingestion-rows').value);
    const version=element('ingestion-version').value===''?null:Number(element('ingestion-version').value);
    const maximum=element('ingestion-subset').value==='HI-Medium'?32000000:5100000;
    if(rows!==null && (!Number.isInteger(rows)||rows<1||rows>maximum)) throw new Error(`Choose a transaction limit from 1 to ${integer(maximum)}.`);
    if(version!==null && (!Number.isInteger(version)||version<1||version>100000)) throw new Error('Choose a positive whole-number dataset version.');
    const subset=element('ingestion-subset').value;
    if(!['HI-Small','HI-Medium'].includes(subset)) throw new Error('Cloud ingestion supports HI-Small and HI-Medium only.');
    if(subset==='HI-Medium' && element('ingestion-train').checked) throw new Error('HI-Medium uses the existing model and does not support candidate training.');
    return {subset,rows,dataset_version:version,refresh:element('ingestion-refresh').checked,
      rerun:element('ingestion-rerun').checked,train:mode==='full'&&element('ingestion-train').checked};
  }
  element('ingestion-preview').onclick=()=>{
    try {
      const options=selectedRunOptions();
      let command='.\\scripts\\ingestion\\ingest.ps1 -NoWait';
      if(options.subset!=='HI-Small') command+=` -Subset ${options.subset}`;
      if(options.rows!==null) command+=` -Rows ${options.rows}`;
      if(options.dataset_version!==null) command+=` -DatasetVersion ${options.dataset_version}`;
      for(const [key,flag] of [['refresh','Refresh'],['rerun','Rerun'],['train','Train']]) if(options[key]) command+=` -${flag}`;
      element('ingestion-preview-output').textContent=JSON.stringify(options,null,2)+'\n\nEquivalent script configuration:\n'+command;
      element('ingestion-preview-panel').hidden=false;
    } catch(error) {message(error.message);}
  };

  const callbackParams=new URLSearchParams(location.search);
  async function initialise() {
    try {
      const response=await fetch('/ingestion-config.json',{cache:'no-store'});
      if([403,404].includes(response.status)) {
        if(await loadStatusConfig()) enterReadOnly();
        else message('Live AWS status and ingestion controls have not been connected to this dashboard yet.');
        return;
      }
      if(!response.ok) throw new Error('Unable to read ingestion configuration.');
      const value=await response.json();
      if(value.schema_version!==1 || !/^https:\/\/[a-z0-9]+\.execute-api\.ap-southeast-1\.amazonaws\.com$/.test(value.api_url)
        || (value.auth_mode!=='aws-iam-helper' && !/^https:\/\/[a-z0-9-]+\.auth\.ap-southeast-1\.amazoncognito\.com$/.test(value.cognito_domain))
        || !/^[a-z0-9-]{1,128}$/.test(value.client_id) || new URL(value.redirect_uri).origin!==location.origin)
        throw new Error('Invalid ingestion configuration.');
      config=value;auth=read('auth');await finishSignIn();updateControls();
      if(signedIn()) {
        const supported=await request('/options');
        element('ingestion-subset').replaceChildren();
        for(const subset of supported.subsets) {const option=document.createElement('option');option.value=subset.value;option.textContent=subset.label;option.disabled=!subset.supported;element('ingestion-subset').appendChild(option);}
        message(supported.notice);await refreshHistory();
        const active=read('active');if(active) await track(active);
      } else if(await loadStatusConfig()) enterReadOnly();
      else message('Sign in with your team account to start ingestion and view your runs.');
    } catch(error) {message(error.message || 'Ingestion setup could not be loaded.');}
    finally {updateControls();}
  }
  element('ingestion-signin').onclick=signIn;
  element('ingestion-signout').onclick=async()=>{
    if(config.auth_mode==='aws-iam-helper') {
      try {await request('/session/logout',{method:'POST'});}
      catch {message('Local session cleared. If server sign-out was unavailable, the session expires within one hour.');}
      stopPolling();auth=null;lastStatus=null;save('auth',null);save('pending',null);save('active',null);updateControls();
      if(await loadStatusConfig()) enterReadOnly();return;
    }
    stopPolling();auth=null;save('auth',null);save('pending',null);save('oauth',null);updateControls();
    location.assign(config.cognito_domain+'/logout?'+new URLSearchParams({client_id:config.client_id,logout_uri:config.redirect_uri}));
  };
  element('ingestion-form').onsubmit=submit;element('ingestion-size').onchange=updateControls;
  element('ingestion-subset').onchange=updateControls;
  element('ingestion-refresh-status').onclick=()=>readOnly()?publicPoll():poll();
  element('ingestion-history-refresh').onclick=()=>readOnly()?publicPoll():refreshHistory();
  element('ingestion-results').onclick=async()=>{
    try {if(await loadResults(lastStatus)) selectDashboardView('aml');}catch(error){message(error.message);}
  };
  document.addEventListener('visibilitychange',()=>{if(document.hidden) stopPolling();else if(currentRun && signedIn()) poll();else if(readOnly()) publicPoll();});
  initialise();
})();

// Incoming AML: committed streaming scores published by infra/streaming's publisher.
// The tab appears only when streaming-config.json is published; batch views never depend on it.
(function incomingScores() {
  const states={waiting:'Waiting for events',running:'Live · scoring',paused:'Paused',completed:'All scored',failed:'Consumer failure'};
  let config=null, latest=null, timer=null, busy=false, failures=0;
  const visible=()=>Boolean(config) && !document.hidden && !element('incoming').hidden;
  const stop=()=>{clearTimeout(timer);timer=null;};
  const age=data=>Math.max(0,Math.round((Date.now()-Date.parse(data.generated_at))/1000));
  const ago=seconds=>seconds<90?`${seconds} s ago`:`${Math.round(seconds/60)} min ago`;
  // A completed replay does not go stale by aging; anything else does after two minutes.
  const stale=data=>data.state!=='completed' && age(data)>120;
  const eventTime=value=>value?value.replace('T',' ').slice(0,16):'Not started';
  const clock=value=>new Date(value).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const parties=row=>`${row.from_bank} ${row.from_account} → ${row.to_bank} ${row.to_account}`;
  // Optional snapshot fields: older snapshots lack them, so each card or column appears only when well formed.
  const review=data=>{const value=data.review;
    return value && Number.isFinite(value.cutoff) && Number.isSafeInteger(value.capacity) && Number.isSafeInteger(value.flagged)?value:null;};
  const receivers=data=>{const value=data.receivers;
    return value && Number.isSafeInteger(value.expected) && Number.isSafeInteger(value.complete)?value:null;};
  const parity=data=>{const value=data.verification;
    return value && value.features_exact===true && Number.isSafeInteger(value.transactions) &&
      Number.isFinite(value.max_absolute_score_difference) && typeof value.verified_on==='string'?value:null;};
  element('incoming-tab').hidden=true;

  function valid(data) {
    return data?.schema_version===1 && data.run_id===config.run_id && Object.hasOwn(states,data.state) &&
      typeof data.generated_at==='string' && Number.isSafeInteger(data.processed) && Number.isSafeInteger(data.expected) &&
      Array.isArray(data.queue) && Array.isArray(data.latest) && Array.isArray(data.progress) && data.progress.length>0;
  }

  // One incoming transaction's evidence: who paid whom, its score and the sender's rolling history.
  function incomingDetail(row,cutoff) {
    const panel=element('incoming-detail');panel.hidden=false;
    panel.innerHTML=`<div class="heading"><h3>${rankChip(row)}Transaction ${escapeText(row.transaction_id)}</h3><button id="incoming-close" type="button">Close evidence</button></div><div class="transfer-path"><span>Sender<br><strong>${escapeText(row.from_bank)} / ${escapeText(row.from_account)}</strong></span><span aria-hidden="true">→</span><span>Receiver<br><strong>${escapeText(row.to_bank)} / ${escapeText(row.to_account)}</strong></span></div><div class="evidence-facts"><span><small>Event time</small>${escapeText(eventTime(row.timestamp))}</span><span><small>Scored at</small>${row.scored_at?escapeText(clock(row.scored_at)):'—'}</span><span><small>Ranking score</small>${Number(row.model_score).toFixed(4)}${cutoff?` · ${row.review_now===true?'Review now':'below the batch cutoff'}`:''}</span><span><small>Payment</small>${escapeText(row.payment_format)} / ${escapeText(row.payment_currency)}</span><span><small>Prior-day USD valuation</small>${dollars(row.amount_usd_asof)}</span><span><small>Sender payments, past 1h / 24h / 7d</small>${integer(row.txn_count_1h)} / ${integer(row.txn_count_24h)} / ${integer(row.txn_count_7d)}</span><span><small>Sender counterparties, past 24h / 7d</small>${integer(row.distinct_counterparties_24h)} / ${integer(row.distinct_counterparties_7d)}</span>${receiverFacts(row)}</div><p class="field-help">The rolling history counts activity before this payment. Receiver history is unavailable where it was not retained in the snapshot. Scores rank review priority; they are not probabilities.</p>`;
    element('incoming-close').onclick=()=>{panel.hidden=true;};
    panel.scrollIntoView?.({behavior:'smooth',block:'start'});
  }

  // One line saying which stream this is and whether anything is still arriving. "Live" only while it runs and is fresh.
  function streamRun(data,old) {
    let line=element('incoming-run');
    if(!line) {
      if(typeof document.createElement!=='function') return;
      line=document.createElement('p');line.id='incoming-run';line.className='stream-run';
      element('incoming-message').insertAdjacentElement?.('beforebegin',line);
      if(!element('incoming-run')) return;
    }
    const first=data.progress[0]?.at, last=data.last_progress_at||data.progress.at(-1)?.at;
    const day=first?new Date(first).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'}):'';
    const span=first&&last?`${clock(first).slice(0,5)}–${clock(last).slice(0,5)}`:'';
    const live=data.state==='running'&&!old, off=data.consumer_enabled===false;
    line.dataset.state=live?'live':data.state==='failed'?'failed':'idle';
    line.innerHTML=`<span class="stream-dot" aria-hidden="true"></span><strong>${live?'Live stream':data.state==='completed'?'Test stream':'Stream'}${day?` · ${escapeText(day)}`:''}</strong>`+
      `<span>${escapeText(span)}</span><span>${integer(data.processed)} payments from ${integer(data.senders?.expected??0)} senders</span>`+
      `<span>${live?'scoring as payments arrive':{completed:'finished',paused:'paused',waiting:'waiting for the first payment',failed:'consumer failure',running:'no recent update'}[data.state]}${off&&!live?' · streaming is switched off':''}</span>`+
      `<span class="stream-published">Published ${escapeText(new Date(data.generated_at).toLocaleDateString('en-GB',{day:'numeric',month:'short'}))}</span>`;
  }

  function render(data) {
    const old=stale(data), share=data.expected?data.processed/data.expected:0;
    element('incoming-state').textContent=old?'Stale':states[data.state];
    element('incoming-state').dataset.state=old||data.state==='failed'?'failed':data.state==='running'?'running':'';
    element('incoming-message').className=!old&&data.state==='completed'&&data.reconciled!==false?'trust-strip verified':'note';
    element('incoming-message').textContent=old?`No successful update for ${Math.round(age(data)/60)} min. The last published scores are shown.`:{
      waiting:'Waiting for the first incoming transaction to be scored.',
      running:`Updated ${ago(age(data))}. Committed scores appear here within about 15 seconds; this page checks every 10 seconds.`,
      paused:'The Kinesis consumer is disabled, so nothing new is arriving. Scores committed so far are shown.',
      completed:`All ${integer(data.expected)} incoming transactions${receivers(data)?`, ${integer(data.senders.expected)} sender histories and ${integer(data.receivers.expected)} receiver histories`:` and ${integer(data.senders.expected)} sender histories`} reconcile with the reviewed input.`,
      failed:'The streaming consumer reported record failures. Committed scores are shown; later events may be held back until the failure is resolved.'}[data.state];
    const cutoff=review(data), verified=parity(data);
    const answer=cutoff?'review':verified?'parity':'scored';
    const step=Math.ceil(data.progress.length/24), points=data.progress.filter((point,index,all)=>index%step===0||index===all.length-1);
    streamRun(data,old);
    cards('incoming-kpis',[['Transactions scored',`${integer(data.processed)} / ${integer(data.expected)}`,`${(share*100).toFixed(1)}% of expected transactions`,answer==='scored',
      points.length>1?{values:points.map(point=>point.processed),color:'#3fd0b8',label:'Running total of scored transactions',caption:`${clock(points[0].at)} → ${clock(points.at(-1).at)}`}:null],
      ['Complete sender histories',`${integer(data.senders.complete)} / ${integer(data.senders.expected)}`,'Rolling features need each sender’s full history'],
      ...(receivers(data)?[['Complete receiver histories',`${integer(data.receivers.complete)} / ${integer(data.receivers.expected)}`,'Payments from other senders preloaded; replayed ones added as scored']]:[]),
      ['Highest ranking score',data.max_score==null?'—':Number(data.max_score).toFixed(4),'Uncalibrated · not a probability'],
      ['Latest event time',eventTime(data.replay_position),'Historical event time · September 2022'],
      ...(cutoff?[['Review now',`${integer(cutoff.flagged)} / ${integer(data.processed)}`,`Score ≥ ${cutoff.cutoff.toFixed(4)} · batch top-${integer(cutoff.capacity)} cutoff`,true]]:[]),
      ...(verified?[['Matches batch model',`${integer(verified.transactions)} / ${integer(verified.transactions)}`,
        `Max score difference ${verified.max_absolute_score_difference.toExponential(1)} · recorded ${verified.verified_on}`,answer==='parity']]:[])]);
    const bar=element('incoming-progressbar');
    bar.setAttribute('aria-valuenow',String(data.processed));bar.setAttribute('aria-valuemax',String(data.expected));
    bar.innerHTML=`<i style="width:${(share*100).toFixed(1)}%"></i>`;bar.hidden=data.state==='completed';
    streamLinks(data);
    if(data.queue.length) {
      streamReplay(data,row=>incomingDetail(row,cutoff));
      // The same case explorer as the review queue; rebuilt only when the published queue changes.
      const key=`${data.generated_at}|${data.queue.length}`;
      if(incomingStream?.key!==key) {incomingStream={key,data,open:row=>incomingDetail(row,cutoff)};incomingCases.render(data.queue);}
      else incomingStream.open=row=>incomingDetail(row,cutoff);
    }
    // Up to ~24 points (thinned above) keeps the shared chart's axis labels readable; first and last are always kept.
    chartLines('incoming-progress',[{name:'Transactions scored',color:'#3fd0b8',rows:points.map(point=>({x:Date.parse(point.at),y:point.processed,
      label:clock(point.at)}))}],Math.max(1,data.expected),null,`Time scored · ${new Date(points[0].at).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})}`,integer);
    // Review column only when the snapshot carries the batch cutoff.
    const flag=row=>cutoff?[row.review_now===true?'Review now':'Below cutoff']:[];
    table('incoming-latest',['Event time','Scored at','Transaction','Sender → receiver','Ranking score',...(cutoff?['Review']:[]),'Payment','Prior-day USD'],
      data.latest.map(row=>[eventTime(row.timestamp),row.scored_at?clock(row.scored_at):'—',row.transaction_id,parties(row),Number(row.model_score).toFixed(4),...flag(row),row.payment_format,dollars(row.amount_usd_asof)]));
    if(!data.latest.length) element('incoming-latest').textContent='No transactions scored yet.';
    const reviewNote=cutoff?` ${cutoff.flagged?`${integer(cutoff.flagged)} reach`:'None reach'} the batch top-${integer(cutoff.capacity)} review cutoff (score ≥ ${cutoff.cutoff.toFixed(4)}, taken from the batch covering ${cutoff.period}).`:'';
    element('incoming-queue-note').textContent=`Top ${integer(data.queue.length)} of ${integer(data.processed)} transactions scored so far; ties use event time then source row. Scores rank review priority and are not probabilities.${reviewNote}`;
    table('incoming-queue',['Rank','Priority','Transaction','Event time','Sender → receiver','Ranking score',...(cutoff?['Review']:[]),'Payment / currency','Prior-day USD','Receiver payments 1h / 24h / 7d','Receiver distinct senders 24h / 7d'],
      data.queue.map(row=>[row.rank,tierFor(row.rank).label,row.transaction_id,eventTime(row.timestamp),parties(row),Number(row.model_score).toFixed(4),...flag(row),`${row.payment_format} / ${row.payment_currency}`,dollars(row.amount_usd_asof),receiverPayments(row),receiverSenders(row)]));
    element('incoming-queue').querySelectorAll?.('tbody tr').forEach((tr,index)=>{const row=data.queue[index];if(!row||!tr.children) return;
      const button=document.createElement('button');button.textContent=row.transaction_id;button.title='Show evidence';
      button.onclick=event=>{event?.stopPropagation?.();incomingDetail(row,cutoff);};tr.onclick=()=>incomingDetail(row,cutoff);
      tr.classList.add(`tier-row-${tierFor(row.rank).id}`);tr.children[1].replaceChildren(priorityChip(row.rank));tr.children[2].replaceChildren(button);});
    element('incoming-detail').hidden=true;
    if(!data.queue.length) element('incoming-queue').textContent='The queue fills as transactions are scored.';
    element('incoming-provenance').textContent=JSON.stringify({run_id:data.run_id,generated_at:data.generated_at,last_progress_at:data.last_progress_at,
      reconciled:data.reconciled,consumer_enabled:data.consumer_enabled,cohort:data.cohort,model:data.model},null,2);
  }

  async function poll() {
    stop();
    if(busy || !visible()) return;
    busy=true;element('incoming-refresh').disabled=true;element('incoming-refresh').textContent='Checking…';
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(config.snapshot_url,{cache:'no-store',signal:controller.signal});
      if(!response.ok) throw new Error([403,404].includes(response.status)?'no snapshot has been published yet':`request failed (${response.status})`);
      const data=await response.json();
      if(!valid(data)) throw new Error('unexpected snapshot format');
      failures=0;latest=data;render(data);
      // Visible proof of each successful check, even when a finished replay has nothing new.
      element('incoming-checked').textContent=`Checked ${clock(Date.now())}`;
    } catch(error) {
      failures++;
      element('incoming-message').className='note';
      element('incoming-message').textContent=`Incoming scores unavailable: ${error.name==='AbortError'?'request timed out':error.message}.${latest?' The last published scores are retained.':''}`;
      if(latest) {element('incoming-state').textContent='Stale';element('incoming-state').dataset.state='failed';}
    } finally {
      clearTimeout(timeout);busy=false;element('incoming-refresh').disabled=false;element('incoming-refresh').textContent='Refresh now';
      // Poll only while visible; back off on failure; a completed replay only needs an occasional check.
      if(visible()) timer=setTimeout(poll,failures?Math.min(60000,10000*Math.pow(2,Math.min(failures,3))):latest?.state==='completed'?60000:10000);
    }
  }

  async function initialise() {
    try {
      const response=await fetch('/streaming-config.json',{cache:'no-store'});
      if(!response.ok) return;
      const value=await response.json();
      if(value.schema_version!==1 || !/^[a-z0-9][a-z0-9-]{2,49}$/.test(value.run_id) || value.snapshot_url!==`/streaming/${value.run_id}/snapshot.json`) return;
      config=value;element('incoming-tab').hidden=false;
    } catch {}
  }
  element('incoming-tab').onclick=()=>{selectDashboardView('incoming');poll();};
  element('incoming-refresh').onclick=poll;
  element('incoming-export').onclick=()=>{if(latest) download(latest.queue,`incoming-${latest.run_id}.csv`);};
  document.addEventListener('visibilitychange',()=>{if(document.hidden) stop();else poll();});
  initialise();
})();


let amlMode='investigation', displayedQueue=[], explorationPeriod=null, explorationReady=false;
function projectionRecord(id) {
  const data=snapshot.exploration.aml;
  return Object.fromEntries(data.record_fields.map((field,index)=>[field,data.records[id][index]]));
}
function selectedPopulation() {
  return snapshot.exploration.aml.periods[element('split').value].scopes.find(item=>item.id===element('aml-population').value)
    || snapshot.exploration.aml.periods[element('split').value].scopes[0];
}
function selectedCapacity() {return element('review-capacity').value||'100';}
// Review priority by queue rank, matching the review capacities (25, 100, 250): rank-based, so no score
// threshold is invented and labels are never needed to assign a tier.
const TIERS=[{id:'urgent',label:'Urgent',first:1,last:25,color:'#ff6f5e'},{id:'high',label:'High',first:26,last:100,color:'#ffb648'},{id:'review',label:'Review',first:101,last:Infinity,color:'#cfae7a'}];
function tierFor(rank) {return TIERS.find(tier=>rank<=tier.last);}
// Labelled laundering inside each tier of the top `capacity`, from the cumulative hits at each tier boundary.
function tierHits(metrics,capacity) {
  return TIERS.map(tier=>{const last=Math.min(tier.last,capacity), upper=metrics[last], lower=tier.first>1?metrics[tier.first-1]:{hits:0};
    return upper&&lower&&last>=tier.first?{...tier,last,reviewed:last-tier.first+1,hits:upper.hits-lower.hits}:null;}).filter(Boolean)
    .map(tier=>({...tier,rate:tier.hits/tier.reviewed}));
}
// The histogram bucket ('low–high' label) holding score; the last bucket includes its upper edge.
function bucketFor(histogram,score) {
  const index=histogram.findIndex(row=>{const [low,high]=row.label.split('–').map(Number);return score>=low&&score<high;});
  return index>=0?index:histogram.length-1;
}
// "#4 · Urgent" before an evidence title; empty for rows without a rank.
function rankChip(row) {
  if(!Number.isFinite(row?.rank)) return '';
  const tier=tierFor(row.rank);
  return `<span class="priority priority-${tier.id} rank-chip">#${integer(row.rank)} · ${escapeText(tier.label)}</span>`;
}
// The opened row is selected in the case explorer and lit in the queue table.
function markSelectedRank(row) {
  if(reviewCases.rows.some(item=>item.transaction_id===row.transaction_id)) {if(reviewCases.selected!==row.rank) reviewCases.select(row.rank,{replay:false});return;}
  element('queue')?.querySelectorAll?.('tbody tr')?.forEach(tr=>tr.classList?.toggle('row-selected',Number(tr.dataset?.rank)===row.rank));
}
function priorityChip(rank) {
  const tier=tierFor(rank), chip=document.createElement('span');
  chip.className=`priority priority-${tier.id}`;chip.textContent=tier.label;
  return chip;
}
// On the night background a faint red reads as brown, so the scale runs from slate to the explorer's hub coral.
const heatFill = level => {const t=Math.max(0,Math.min(1,level||0)), mix=(from,to)=>Math.round(from+(to-from)*t);
  return `rgb(${mix(61,255)},${mix(84,111)},${mix(104,94)})`;};
function initialiseExploration() {
  if(explorationReady) return;
  explorationReady=true;
  element('aml-population').onchange=()=>{clearQueueFilters();renderAml();};
  element('review-capacity').onchange=renderAml;
  element('investigation-mode').onclick=()=>{amlMode='investigation';renderAml();};
  element('evaluation-mode').onclick=()=>{amlMode='evaluation';renderAml();};
  element('aml-reset').onclick=()=>{element('aml-population').value='all';element('review-capacity').value='100';clearQueueFilters();renderAml();};
  for(const id of ['queue-search','queue-minimum']) element(id).oninput=renderQueue;
  for(const id of ['queue-payment','queue-currency','queue-sort']) element(id).onchange=renderQueue;
}
function clearQueueFilters() {
  for(const id of ['queue-search','queue-minimum','queue-payment','queue-currency']) element(id).value='';
  element('queue-sort').value='rank';
}
function selectOptions(id,values,label=value=>value) {
  const control=element(id), previous=control.value;
  control.innerHTML=values.map(value=>`<option value="${escapeText(value)}">${escapeText(label(value))}</option>`).join('');
  control.value=values.includes(previous)?previous:values[0];
}
// Line chart drawn at its on-screen width: glowing lines over gradient fills, drawn in each time it is shown.
// Hover, or focus it and press ←/→, for a crosshair readout; a hidden table carries the same numbers for screen readers.
// options.markers: [{x, y, color, label, pulse}] extra points such as the chosen review size; options.note: a line under the chart.
function chartLines(target,series,maxY=1,maxX=null,xLabel='Date',yFormatter=percent,options={}) {
  const host=element(target);
  chartCalls.set(target,[chartLines,[target,series,maxY,maxX,xLabel,yFormatter,options]]);
  const valid=series.flatMap(item=>item.rows).filter(row=>row.y!=null && Number.isFinite(row.y));
  if(!valid.length) {host.textContent='No measurements for this selection.';return;}
  const width=chartWidth(host), narrow=width<520, height=narrow?232:262;
  const left=narrow?50:64, right=18, top=16, w=width-left-right, h=height-top-50, recall=xLabel==='Recall';
  const minX=recall?0:Math.min(...valid.map(row=>row.x)), endX=maxX??Math.max(...valid.map(row=>row.x));
  const x=row=>left+(row.x-minX)/Math.max(endX-minX,1e-9)*w, y=row=>top+h-row.y/Math.max(maxY,1e-9)*h;
  const id=`chart-${target}`, n=value=>value.toFixed(1);
  const drawn=series.map(item=>({...item,points:item.rows.filter(row=>row.y!=null && Number.isFinite(row.y)).map(row=>({...row,px:x(row),py:y(row)}))}));
  const grid=[0,.25,.5,.75,1].map(f=>`<line class="chart-grid${f?'':' chart-base'}" x1="${left}" x2="${left+w}" y1="${n(top+h-f*h)}" y2="${n(top+h-f*h)}"/>${f%.5===0?`<text class="chart-axis" x="${left-9}" y="${n(top+h-f*h+4)}" text-anchor="end">${escapeText(yFormatter(f*maxY))}</text>`:''}`).join('');
  const defs=drawn.map((item,i)=>`<linearGradient id="${id}-fill${i}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${item.color}" stop-opacity="${i?.13:.3}"/><stop offset="1" stop-color="${item.color}" stop-opacity="0"/></linearGradient>`).join('');
  const lines=drawn.map((item,i)=>{
    if(!item.points.length) return '';
    const d=item.points.map((p,index)=>`${index?'L':'M'}${n(p.px)} ${n(p.py)}`).join(' ');
    const area=item.points.length>1?`<path class="chart-area" d="${d} L${n(item.points.at(-1).px)} ${top+h} L${n(item.points[0].px)} ${top+h} Z" fill="url(#${id}-fill${i})"/>`:'';
    const dots=item.points.length<=30?item.points.map(p=>`<circle class="chart-dot" cx="${n(p.px)}" cy="${n(p.py)}" r="3" fill="${item.color}"><title>${escapeText(item.name)} · ${escapeText(p.label)}: ${escapeText(yFormatter(p.y))}</title></circle>`).join(''):'';
    return `${area}<path class="chart-line" d="${d}" pathLength="1" stroke="${item.color}" style="filter:drop-shadow(0 0 5px ${item.color})"/>${dots}`;}).join('');
  const markers=(options.markers||[]).map(mark=>{const px=x(mark), py=y(mark);
    return `<g class="chart-marker">${mark.pulse?`<circle class="chart-pulse" cx="${n(px)}" cy="${n(py)}" r="6" stroke="${mark.color}"/>`:''}<circle cx="${n(px)}" cy="${n(py)}" r="${mark.pulse?6:4}" fill="${mark.color}" stroke="#09131e" stroke-width="2"><title>${escapeText(mark.label)}</title></circle></g>`;}).join('');
  // X labels: as many as fit, always the first and last.
  const rows=series[0].rows, every=Math.ceil(rows.length/Math.max(2,Math.floor(w/(narrow?78:96))));
  const sample=rows.filter((row,index)=>index===0||index===rows.length-1||index%every===0&&rows.length-1-index>=every/2);
  const ticks=recall?[0,.25,.5,.75,1].map(f=>`<text class="chart-axis" x="${n(left+f*w)}" y="${top+h+20}" text-anchor="middle">${percent(f)}</text>`).join(''):
    sample.map(row=>`<text class="chart-axis" x="${n(x(row))}" y="${top+h+20}" text-anchor="middle">${escapeText(row.label)}</text>`).join('');
  const data=drawn.flatMap(item=>item.points.map(p=>`<tr><td>${escapeText(item.name)}</td><td>${escapeText(p.label)}</td><td>${escapeText(yFormatter(p.y))}</td></tr>`)).slice(0,400).join('');
  host.innerHTML=`<div class="chart-legend">${series.map(item=>`<span><i style="background:${item.color};box-shadow:0 0 8px ${item.color}"></i>${escapeText(item.name)}</span>`).join('')}</div><div class="chart-box"><svg class="analysis-chart" viewBox="0 0 ${width} ${height}" tabindex="0" role="img" aria-label="${escapeText(xLabel)} comparison. Use the left and right arrow keys to read values."><defs>${defs}</defs>${grid}${lines}${markers}<line class="chart-cross" x1="0" x2="0" y1="${top}" y2="${top+h}" visibility="hidden"/><g class="chart-hover"></g>${ticks}<text class="chart-axis chart-title" x="${n(left+w/2)}" y="${height-6}" text-anchor="middle">${escapeText(xLabel)}</text></svg><div class="ring-tip chart-tip" role="tooltip" hidden></div></div><div class="sr-only"><table><caption>${escapeText(xLabel)} values</caption><thead><tr><th scope="col">Series</th><th scope="col">${escapeText(xLabel)}</th><th scope="col">Value</th></tr></thead><tbody>${data}</tbody></table></div>${options.note?`<p class="chart-note">${escapeText(options.note)}</p>`:''}`;
  bindChartHover(host,drawn,{left,w,width,recall,xLabel,yFormatter});
}
// Crosshair readout: the nearest point of every series to the pointer, or to the key-stepped position.
function bindChartHover(host,drawn,{left,w,width,recall,xLabel,yFormatter}) {
  const svg=host.querySelector?.('svg'), tip=host.querySelector?.('.chart-tip');
  if(!svg?.addEventListener || !tip) return;
  const cross=svg.querySelector('.chart-cross'), hover=svg.querySelector('.chart-hover');
  const anchors=[...new Map(drawn.flatMap(item=>item.points).sort((a,b)=>a.px-b.px).map(p=>[p.px.toFixed(1),p])).values()];
  let index=-1;
  const scale=()=>svg.getBoundingClientRect().width/width;
  const hide=()=>{tip.hidden=true;cross.setAttribute('visibility','hidden');hover.innerHTML='';};
  const show=i=>{try {
    index=Math.max(0,Math.min(anchors.length-1,i));const anchor=anchors[index];
    const near=drawn.filter(item=>item.points.length).map(item=>({item,p:item.points.reduce((best,p)=>Math.abs(p.px-anchor.px)<Math.abs(best.px-anchor.px)?p:best)}));
    cross.setAttribute('x1',anchor.px);cross.setAttribute('x2',anchor.px);cross.setAttribute('visibility','visible');
    hover.innerHTML=near.map(({item,p})=>`<circle cx="${p.px}" cy="${p.py}" r="5.5" fill="${item.color}" stroke="#09131e" stroke-width="2" style="filter:drop-shadow(0 0 6px ${item.color})"/>`).join('');
    tip.innerHTML=`<span class="tip-title">${escapeText(recall?anchor.label:`${xLabel}: ${anchor.label}`)}</span>${near.map(({item,p})=>`<span class="chart-tip-row" style="--role:${item.color}"><span class="tip-role">${escapeText(item.name)}</span><b>${escapeText(yFormatter(p.y))}</b></span>`).join('')}`;
    tip.hidden=false;
    const k=scale(), boxWidth=svg.getBoundingClientRect().width, tipWidth=tip.offsetWidth, px=anchor.px*k;
    tip.style.left=`${Math.max(4,Math.min(boxWidth-tipWidth-4,px+(px>boxWidth*.6?-tipWidth-14:14)))}px`;
    tip.style.top=`${Math.max(4,Math.min(...near.map(({p})=>p.py))*k-tip.offsetHeight-12)}px`;
  } catch {hide();}};
  svg.addEventListener('pointermove',event=>{
    const px=(event.clientX-svg.getBoundingClientRect().left)/scale();
    if(px<left-12||px>left+w+12) {hide();return;}
    let best=0;anchors.forEach((p,i)=>{if(Math.abs(p.px-px)<Math.abs(anchors[best].px-px)) best=i;});
    if(best!==index||tip.hidden) show(best);});
  svg.addEventListener('pointerleave',()=>{hide();index=-1;});svg.addEventListener('blur',hide);
  svg.addEventListener('focus',()=>show(index<0?anchors.length-1:index));
  svg.addEventListener('keydown',event=>{
    const step={ArrowLeft:-1,ArrowRight:1}[event.key];
    if(event.key==='Escape') {hide();return;}
    if(event.key==='Home'||event.key==='End') {event.preventDefault();show(event.key==='Home'?0:anchors.length-1);return;}
    if(step) {event.preventDefault();show(index+step);}});
}
function renderAml() {
  if(!snapshot?.exploration) {
    const alerts=element('alerts').closest('article'), scores=element('scores').closest('article');
    alerts.querySelector('h3').textContent='Rule alerts through time';
    alerts.querySelector('p').textContent='Rule score ≥60; this chart uses the rule baseline.';
    scores.querySelector('h3').textContent='Rule score distribution';
    scores.querySelector('p').textContent='Rule baseline across the selected period.';
    for(const id of ['aml-explore-controls','aml-context','aml-mode','queue-controls','queue-coverage','evaluation-charts']) element(id).hidden=true;
    element('investigation-panel').hidden=false;element('validation-diagnostics').hidden=false;
    element('model-comparison').hidden=!snapshot?.model;element('aml-note').hidden=false;element('capture-card').hidden=true;
    renderAmlLegacy();return;
  }
  initialiseExploration();
  for(const id of ['aml-explore-controls','aml-context','aml-mode','queue-controls','queue-coverage']) element(id).hidden=false;
  const period=element('split').value, scopes=snapshot.exploration.aml.periods[period].scopes;
  const previous=element('aml-population').value;
  element('aml-population').innerHTML=scopes.map(scope=>`<option value="${escapeText(scope.id)}">${escapeText(scope.label)}</option>`).join('');
  element('aml-population').value=scopes.some(item=>item.id===previous)?previous:'all';
  if(explorationPeriod!==period) {clearQueueFilters();explorationPeriod=period;}
  const population=selectedPopulation(), model=isModelQueue(), scorer=model?'model':'rules';
  element('review-capacity').querySelectorAll('option').forEach(option=>{option.disabled=Number(option.value)>population.capacity;});
  if(Number(selectedCapacity())>population.capacity) element('review-capacity').value='100';
  const k=selectedCapacity(), data=population[scorer], metrics=data.metrics_by_k[k];
  const other=population[model?'rules':'model'].metrics_by_k[k], otherName=model?'rules':'model';
  // The published review sizes, smallest first: they feed the card sparklines and the chart markers.
  const sizes=Object.keys(data.metrics_by_k).map(Number).sort((a,b)=>a-b);
  cards('aml-kpis',[['Transactions in population',integer(metrics.rows),`${population.label} · ${evaluationPeriodLabel(period)}`],['Ranking quality · average precision',metrics.average_precision==null?'Unavailable':metrics.average_precision.toFixed(4),`${otherName} ${other.average_precision==null?'unavailable':other.average_precision.toFixed(4)} · whole ranking, not one cut-off`],
    [`Laundering in top ${integer(metrics.k)}`,percent(metrics.precision_at_k),`${integer(metrics.hits)} of ${integer(metrics.k)} · ${otherName} ${percent(other.precision_at_k)}`,true,
      {values:sizes.map(size=>data.metrics_by_k[size].precision_at_k),color:'#3fd0b8',label:`Share that is laundering at ${sizes.join(', ')} reviews`,caption:`At ${sizes[0]} → ${sizes.at(-1)} reviews`}],
    ['Share of all laundering caught',percent(metrics.recall_at_k),`of ${integer(metrics.positives)} laundering cases · ${otherName} ${percent(other.recall_at_k)}`,false,
      {values:sizes.map(size=>data.metrics_by_k[size].recall_at_k),color:'#4fa8ff',label:`Share of all laundering caught at ${sizes.join(', ')} reviews`,caption:`At ${sizes[0]} → ${sizes.at(-1)} reviews`}]]);
  const capture=['model','rules'].map((kind,i)=>({name:kind==='model'?'Frozen model':'Rules',color:i?'#ffb648':'#3fd0b8',
    rows:Object.entries(population[kind].metrics_by_k).map(([capacity,m])=>({x:Number(capacity),y:m.hits,label:capacity}))}));
  element('capture-card').hidden=amlMode==='evaluation';
  element('capture-note').textContent=`Laundering found as the queue grows from 25 to ${integer(population.capacity)} reviews. Labels only score the ranking.`;
  chartLines('capture-chart',capture,Math.max(1,...capture.flatMap(series=>series.rows.map(row=>row.y))),null,'Payments reviewed',value=>integer(Math.round(value)),
    {markers:[{x:Number(k),y:metrics.hits,color:model?'#3fd0b8':'#ffb648',pulse:true,label:`Your review capacity: ${k} reviews, ${integer(metrics.hits)} laundering`}]});
  const pipeline=snapshot.pipeline||{};
  const demo=pipeline.source_scope==='smoke-prefix';
  trustStrip('aml-context',`${demo?'Demo sample':'Full source, reconciled before publishing'} · ${population.label} · ${evaluationPeriodLabel(period)} · ${model?'Frozen model':'Rule baseline'} ${snapshot.model.provenance.model_sha256.slice(0,12)} · Dataset ${pipeline.dataset_version??'unversioned'} · Published ${String(pipeline.refresh_time||snapshot.generated_at).slice(0,10)}`,!demo);
  element('aml-filter-note').textContent=snapshot.exploration.aml.filter_policy;
  element('aml-note').textContent=`Average precision ${metrics.average_precision==null?'Unavailable':metrics.average_precision.toFixed(4)} · lift@${metrics.k} ${metrics.lift_at_k==null?'Unavailable':metrics.lift_at_k.toFixed(2)+'×'}. Scores rank review priority; they are not probabilities.`+(period==='tail'?' Later-period evaluation; compare separately from the main test period.':' Retrospective evaluation; labels are absent from investigation records.');
  element('investigation-panel').hidden=amlMode!=='investigation';
  element('model-comparison').hidden=amlMode!=='evaluation';
  element('evaluation-charts').hidden=amlMode!=='evaluation';
  element('aml-note').hidden=amlMode!=='evaluation';
  element('validation-diagnostics').hidden=amlMode!=='evaluation';
  renderScoringCoverage();if(amlMode!=='evaluation') element('scoring-coverage').hidden=true;
  element('investigation-mode').setAttribute('aria-pressed',String(amlMode==='investigation'));
  element('evaluation-mode').setAttribute('aria-pressed',String(amlMode==='evaluation'));
  element('model-comparison').querySelector('p').textContent=`Same selected population, ${period} period and review capacity ${k}. Precision = positive labels / reviewed; recall = captured positives / all positives.`;
  table('comparison',['Scorer','Label-positive in queue',`Precision@${k}`,`Recall@${k}`,'Lift','Average precision'],['model','rules'].map(kind=>{
    const m=population[kind].metrics_by_k[k];return [kind==='model'?'Frozen model':'Rules',`${m.hits} / ${m.k}`,percent(m.precision_at_k),percent(m.recall_at_k),m.lift_at_k==null?'Unavailable':m.lift_at_k.toFixed(2)+'×',m.average_precision==null?'Unavailable':m.average_precision.toFixed(4)];}));
  const alertArticle=element('alerts').closest('article'), scoreArticle=element('scores').closest('article');
  alertArticle.querySelector('h3').textContent='Laundering found per priority tier';
  alertArticle.querySelector('p').textContent=`Share of each tier of the top ${k} that is laundering. It thins out further down the queue, which is why ranks 1–25 are Urgent. Labels only score the ranking.`;
  bars('alerts',tierHits(data.metrics_by_k,Number(k)),tier=>`${tier.label} · ranks ${integer(tier.first)}–${integer(tier.last)} · ${integer(tier.hits)} of ${integer(tier.reviewed)}`,'rate',percent,{color:tier=>tier.color});
  scoreArticle.querySelector('h3').textContent=model?'Model score distribution':'Rule score distribution';
  // Log widths so the large low-score bucket does not hide the rest; higher-score buckets are drawn deeper red.
  const cutoff=data.queue.length?projectionRecord(data.queue[Math.min(Number(k),data.queue.length)-1]):null, cutScore=cutoff&&Number(model?cutoff.model_score:cutoff.risk_score);
  const cutIndex=Number.isFinite(cutScore)?bucketFor(data.histogram,cutScore):-1, edges=row=>row.label.split('–');
  const scored=data.histogram.reduce((sum,row)=>sum+row.transactions,0), first=data.histogram[0];
  const high=cutIndex>=0?data.histogram.slice(cutIndex).reduce((sum,row)=>sum+row.transactions,0):null;
  // Headline first: how decisive the scores are, then how to read the bars.
  scoreArticle.querySelector('p').textContent=(first&&scored?`${percent(first.transactions/scored)} of ${integer(scored)} transactions score below ${edges(first)[1]}`+
    (high==null?'. ':`; only ${integer(high)} score ${edges(data.histogram[cutIndex])[0]} or higher. `):'')+
    'Column height uses a log scale so the small high-score groups stay visible; hover a column, or open the list below, for exact counts. Queue filters do not change this chart.';
  scoreColumns('scores',data.histogram,cutIndex,`Top ${k} starts here · score ≥ ${model?cutScore?.toFixed(4):integer(cutScore)}`);
  bars('scores-bars',data.histogram,row=>row.label,'transactions',integer,{scale:'log',color:(row,index)=>heatFill(index/Math.max(1,data.histogram.length-1)),
    mark:cutIndex>=0?{index:cutIndex,text:`Your top ${k} starts here · score ≥ ${model?cutScore.toFixed(4):integer(cutScore)} ↓`}:undefined});
  chartLines('pr-chart',['model','rules'].map((kind,i)=>({name:kind==='model'?'Frozen model':'Rules',color:i?'#ffb648':'#3fd0b8',rows:population[kind].pr_curve.map(row=>({x:row.recall,y:row.precision,label:`Recall ${percent(row.recall)}`}))})),1,1,'Recall',percent,
    {markers:['model','rules'].flatMap((kind,i)=>sizes.map(size=>{const m=population[kind].metrics_by_k[size];
      return {x:m.recall_at_k,y:m.precision_at_k,color:i?'#ffb648':'#3fd0b8',pulse:String(size)===k&&kind===scorer,label:`${kind==='model'?'Frozen model':'Rules'} · top ${size}: precision ${percent(m.precision_at_k)}, recall ${percent(m.recall_at_k)}`};})),
     note:`Dots are the exact top-${sizes.join('/')} queues; the pulsing one is your review capacity. The curve is published at fewer thresholds, so a straight segment can pass below a dot.`});
  chartLines('capacity-chart',['model','rules'].map((kind,i)=>({name:kind==='model'?'Frozen model':'Rules',color:i?'#ffb648':'#3fd0b8',rows:Object.entries(population[kind].metrics_by_k).map(([capacity,m])=>({x:Number(capacity),y:m.hits,label:capacity}))})),Math.max(1,...['model','rules'].flatMap(kind=>Object.values(population[kind].metrics_by_k).map(m=>m.hits))),null,'Transactions reviewed',integer,
    {markers:[{x:Number(k),y:metrics.hits,color:model?'#3fd0b8':'#ffb648',pulse:true,label:`Your review capacity: ${k} reviews, ${integer(metrics.hits)} laundering`}]});
  const allRows=data.queue.map(projectionRecord);
  selectOptions('queue-payment',['',...new Set(allRows.map(row=>row.payment_format))],value=>value||'All formats');
  selectOptions('queue-currency',['',...new Set(allRows.map(row=>row.payment_currency))],value=>value||'All currencies');
  element('detail').hidden=true;renderQueue();
  renderCaseExplorer();
}
function renderQueue() {
  if(!snapshot?.exploration) return;
  const population=selectedPopulation(), model=isModelQueue(), k=selectedCapacity();
  const data=population[model?'model':'rules'];
  const search=element('queue-search').value.trim().toLowerCase(), minimum=Number(element('queue-minimum').value||0);
  const rows=data.queue.slice(0,Number(k)).map((id,index)=>({...projectionRecord(id),rank:index+1})).filter(row=>
    (!search||[row.transaction_id,row.from_bank,row.from_account,row.to_bank,row.to_account].some(value=>String(value).toLowerCase().includes(search))) &&
    (!element('queue-payment').value||row.payment_format===element('queue-payment').value) &&
    (!element('queue-currency').value||row.payment_currency===element('queue-currency').value) && row.amount_usd_asof>=minimum);
  if(element('queue-sort').value==='amount') rows.sort((a,b)=>b.amount_usd_asof-a.amount_usd_asof||a.rank-b.rank);
  if(element('queue-sort').value==='time') rows.sort((a,b)=>a.timestamp.localeCompare(b.timestamp)||a.rank-b.rank);
  displayedQueue=rows;
  element('queue-title').textContent=model?'Frozen-model investigation queue':'Rule-baseline investigation queue';
  element('queue-note').textContent=`Top ${k} from every transaction in ${population.label.toLowerCase()}. Click any row to see why it was ranked.`;
  element('queue-coverage').textContent=`Showing ${rows.length} of ${Math.min(Number(k),data.queue.length)} ranked rows. Search, payment, currency and amount controls narrow this exported queue; they do not search all transactions or recompute population metrics. Population presets above do.`;
  table('queue',['Rank','Priority','Transaction','Event time','Sender → receiver','Ranking score','Payment / currency','Prior-day USD','Receiver payments 1h / 24h / 7d','Receiver distinct senders 24h / 7d','Evidence'],rows.map(row=>[row.rank,tierFor(row.rank).label,row.transaction_id,row.timestamp.replace('T',' ').slice(0,16),`${row.from_bank} → ${row.to_bank}`,model?Number(row.model_score).toFixed(4):row.risk_score,`${row.payment_format} / ${row.payment_currency}`,dollars(row.amount_usd_asof),receiverPayments(row),receiverSenders(row),model?(row.model_evidence||'Inspect behaviour signals'):row.risk_reasons||'No rule triggered']));
  element('queue').querySelectorAll('tbody tr').forEach((row,index)=>{const button=document.createElement('button');button.textContent=rows[index].transaction_id;button.title='Show evidence';
    // The whole row opens the evidence; the button keeps it reachable by keyboard.
    button.onclick=event=>{event?.stopPropagation?.();showDetail(rows[index]);};row.onclick=()=>showDetail(rows[index]);
    row.dataset.rank=String(rows[index].rank);row.classList.toggle('row-selected',rows[index].rank===reviewCases.selected);
    row.classList.add(`tier-row-${tierFor(rows[index].rank).id}`);row.children[1].replaceChildren(priorityChip(rows[index].rank));row.children[2].replaceChildren(button);
    const evidence=model&&rows[index].model_evidence, split=evidence?evidence.indexOf('; '):-1;
    if(split>0) row.children[8].innerHTML=`<b>${escapeText(evidence.slice(0,split))}</b>${escapeText(evidence.slice(split))}`;});
  element('export-queue').onclick=()=>download(displayedQueue,`queue-${model?'model':'rules'}-${element('split').value}-${k}.csv`);
}
function showDetail(row) {
  if(!snapshot?.exploration) {showDetailLegacy(row);return;}
  const history=snapshot.exploration.aml.histories[row.transaction_id], model=isModelQueue();
  const parts=(row.model_evidence||'').split('; ').filter(Boolean).map(part=>{const colon=part.lastIndexOf(':');return {label:part.slice(0,colon),value:Number(part.slice(colon+1).replace('margin','').trim())};}).filter(part=>Number.isFinite(part.value));
  const maximum=Math.max(.01,...parts.map(part=>Math.abs(part.value)));
  element('detail').hidden=false;
  markSelectedRank(row);
  element('detail').innerHTML=`<div class="heading"><h3>${rankChip(row)}Transaction ${escapeText(row.transaction_id)}</h3><button id="close-detail">Close evidence</button></div><div class="transfer-path"><span>Sender<br><strong>${escapeText(row.from_bank)} / ${escapeText(row.from_account)}</strong></span><span aria-hidden="true">→</span><span>Receiver<br><strong>${escapeText(row.to_bank)} / ${escapeText(row.to_account)}</strong></span></div>${ringLinks(row)}<div class="evidence-facts"><span><small>Event time</small>${escapeText(row.timestamp.replace('T',' '))}</span><span><small>Original payment</small>${row.amount_paid==null?'Amount not retained in this export':escapeText(integer(row.amount_paid))} ${escapeText(row.payment_currency)}</span><span><small>Prior-day USD valuation</small>${dollars(row.amount_usd_asof)}</span><span><small>Payment format</small>${escapeText(row.payment_format)}</span><span><small>Prior hour</small>${integer(row.txn_count_1h)} outgoing transactions</span><span><small>Prior 24 hours</small>${integer(row.distinct_counterparties_24h)} counterparties</span>${receiverFacts(row)}</div><h3>${model?'Signed model contributions':'Rule evidence'}</h3>${model?(parts.length?`<div class="contribution-list">${parts.map(part=>`<div><span>${escapeText(part.label)}</span><div class="signed-track"><i style="${part.value>=0?'left:50%':'right:50%'};width:${Math.abs(part.value)/maximum*48}%;background:${part.value>=0?'#3fd0b8':'#ffb648'}"></i></div><strong>${part.value>=0?'+':''}${part.value.toFixed(4)}</strong></div>`).join('')}</div><p class="field-help">Displayed contributions sum to ${parts.reduce((sum,p)=>sum+p.value,0).toFixed(4)}; bias ${Number(row.explanation_bias_margin).toFixed(4)}; remaining features ${(Number(row.explanation_total_margin)-Number(row.explanation_bias_margin)-parts.reduce((sum,p)=>sum+p.value,0)).toFixed(4)}; full margin ${Number(row.explanation_total_margin).toFixed(4)}. Positive contributions push the ranking score up. These are model explanations, not causal proof or probabilities.</p>`:'<p>Signed explanations are exported for the whole-period top 250. This filtered-population record shows observed behaviour signals; an exact model contribution is unavailable.</p>'):`<p>${escapeText(row.risk_reasons||'No rule triggered')}</p>`}<h3>Account activity preceding this transaction</h3>${history?`<p>${integer(history.observed_prior_transactions)} preceding outgoing transactions observed since the dataset began. Latest ${history.events.length} shown; no later activity is included.</p><div id="account-timeline" class="table-wrap"></div>`:'<p>Account history is exported for the whole-period model/rule top 250. This record is outside that bounded history projection.</p>'}`;
  element('detail').scrollIntoView?.({behavior:'smooth',block:'start'});
  element('detail').querySelectorAll?.('.ring-link').forEach(button=>{button.onclick=()=>openRing(button.dataset.ring,button.dataset.account);});
  if(history) table('account-timeline',['Event time','Receiver bank / account','Payment','Prior-day USD'],history.events.map(event=>[event.timestamp.replace('T',' ').slice(0,16),`${event.to_bank} / ${event.to_account}`,event.payment_format,dollars(event.amount_usd_asof)]));
  element('close-detail').onclick=()=>{element('detail').hidden=true;};
}

// ---- BQ1 case explorer: the presenter's case file inline, laid out like the BQ2 ring explorer ----
// Left: the ranked queue as a rail. Centre: the selected payment's sender, the receivers it paid and a replay of each
// earlier payment flying along its link, then this one. Right: the case readout. No laundering labels are used.
// Dataset times carry no zone: read them as written, the way the history table shows them.
const instant = timestamp => Date.parse(String(timestamp).slice(0,19)+'Z');
const trailTime = value => new Date(value).toISOString().slice(0,16).replace('T',' ');
const trailDay = value => new Date(value).toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
// The sender's trail: earlier payments in time order then this one, the receivers they reached and a one-line summary.
function caseTrail(events,row,{lead='Before this payment, the sender made',none='No earlier outgoing payments from this sender are in the exported history.',self:detectSelf=true}={}) {
  const earlier=[...events].filter(event=>Number.isFinite(instant(event.timestamp))).sort((a,b)=>a.timestamp.localeCompare(b.timestamp));
  const payments=earlier.map(event=>({...event,current:false})).concat([{...row,current:true}]);
  const own=accountKey(row.from_bank,row.from_account), keyOf=payment=>accountKey(payment.to_bank,payment.to_account), receivers=new Map();
  for(const payment of payments) {
    const key=keyOf(payment), item=receivers.get(key)||{key,bank:payment.to_bank,account:payment.to_account,count:0,usd:0,current:false,self:detectSelf&&key===own};
    item.count++;item.usd+=Number(payment.amount_usd_asof)||0;item.current||=payment.current;receivers.set(key,item);
  }
  const before=earlier.filter(event=>keyOf(event)===keyOf(row)).length, total=earlier.reduce((sum,event)=>sum+(Number(event.amount_usd_asof)||0),0);
  const toSelf=detectSelf?earlier.filter(event=>keyOf(event)===own).length:0, distinct=new Set(earlier.map(keyOf)).size;
  let summary=earlier.length?`${lead} ${plural(earlier.length,'payment')} to ${plural(distinct,'receiver')}, ${dollars(total)} in total. This payment goes to ${before?`a receiver it paid ${plural(before,'time')} before`:'a receiver it had not paid in the payments shown'}.`
    :none;
  if(toSelf) summary+=` ${toSelf===earlier.length?'All of them':`${integer(toSelf)} of them`} went back to the sender's own account.`;
  return {payments,receivers:[...receivers.values()],summary};
}
// Node positions for a trail in a w × h stage: sender on the left, other receivers on an arc to the right,
// the sender's own account drawn as a loop above it. Returned paths run from the sender to each receiver.
function caseLayout(receivers,width,height) {
  const narrow=width<600, sx=narrow?Math.max(68,width*.2):width*.2, sy=height*.56, others=receivers.filter(item=>!item.self);
  const top=46, bottom=height-40, most=Math.max(1,...receivers.map(item=>item.usd));
  const nodes=new Map(), loop=receivers.find(item=>item.self);
  others.forEach((item,i)=>{
    const y=others.length===1?sy:top+(i+.5)/others.length*(bottom-top), arc=Math.pow((y-sy)/(height/2),2);
    const x=width*(narrow?.62:.66)-arc*width*.07, r=4+9*Math.sqrt(item.usd/most);
    nodes.set(item.key,{...item,x,y,r,path:`M${sx.toFixed(1)} ${sy.toFixed(1)} Q${(sx+(x-sx)*.55).toFixed(1)} ${sy.toFixed(1)} ${x.toFixed(1)} ${y.toFixed(1)}`});
  });
  if(loop) {const R=Math.min(narrow?32:46,height*.13);nodes.set(loop.key,{...loop,x:sx,y:sy-2*R,r:0,R,
    path:`M${sx.toFixed(1)} ${sy.toFixed(1)} A${R} ${R} 0 1 0 ${(sx-.1).toFixed(1)} ${sy.toFixed(1)}`});}
  return {sender:{x:sx,y:sy},nodes,most};
}
function caseGraphHTML(trail,row,width,height) {
  const {sender,nodes}=caseLayout(trail.receivers,width,height), narrow=width<600, n=value=>value.toFixed(1);
  const most=Math.max(1,...trail.receivers.map(item=>item.count));
  const short=item=>{const label=`${String(item.bank).replace(/^0+(?=.)/,'')}:${item.account}`;return narrow&&label.length>12?`…${label.slice(-7)}`:label;};
  const edges=[...nodes.values()].map(node=>`<path class="case-edge${node.current?' case-edge-current':''}${node.self?' case-edge-self':''}" d="${node.path}" stroke-width="${(1.2+3*node.count/most).toFixed(1)}"/><path class="case-flow${node.current?' case-flow-current':''}" d="${node.path}"/>`).join('');
  const receivers=[...nodes.values()].filter(node=>!node.self).map(node=>`<g class="case-node${node.current?' case-node-current':''}" data-key="${escapeText(node.key)}" tabindex="-1">${node.current?`<circle class="ring-halo" cx="${n(node.x)}" cy="${n(node.y)}" r="${n(node.r+3)}" style="stroke:var(--coral)"/>`:''}<circle class="case-core" cx="${n(node.x)}" cy="${n(node.y)}" r="${n(node.r)}"/><text class="case-label" x="${n(node.x+node.r+9)}" y="${n(node.y+4)}">${escapeText(short(node))}</text></g>`).join('');
  const self=[...nodes.values()].find(node=>node.self);
  const loop=self?`<g class="case-loop${self.current?' case-node-current':''}" data-key="${escapeText(self.key)}"><text class="case-tag" x="${n(self.x)}" y="${n(self.y-12)}" text-anchor="middle">PAYS ITSELF ${narrow?`×${integer(self.count)}`:`· ${escapeText(plural(self.count,'payment'))}`}</text></g>`:'';
  const senderNode=`<g class="case-sender" data-key="sender"><circle class="case-core" cx="${n(sender.x)}" cy="${n(sender.y)}" r="11"/><text class="case-label" x="${n(sender.x)}" y="${n(sender.y+30)}" text-anchor="middle">SENDER ${escapeText(narrow?`…${String(row.from_account).slice(-5)}`:accountKey(row.from_bank,row.from_account))}</text></g>`;
  return `<svg class="case-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeText(trail.summary)}"><g class="case-edges">${edges}</g>${loop}<g class="case-packets"></g>${receivers}${senderNode}</svg>`;
}

// One case explorer component, used by the review queue and the incoming stream. `p` prefixes its element ids.
// config: place(html) inserts the frame; note(rows); rail(row) → {flags, small}; trail(row) → {trail, summary};
// readout(row, rows) → html; bind(row, readout) wires readout buttons; evidence(row); selected(row) runs after a selection.
const CASE_REPLAY_MS=6500, CASE_FLIGHT_MS=900;
function caseExplorer(p,config) {
  const self={rows:[],selected:null,scene:null};
  const el=name=>element(`${p}-${name}`);
  self.mount=()=>{
    if(el('explorer')) return true;
    config.place(`<article id="${p}-explorer" class="ring-explorer case-explorer" aria-labelledby="${p}-explorer-title"><div class="heading"><div><h3 id="${p}-explorer-title">${escapeText(config.title)}</h3><p id="${p}-list-note"></p></div><div class="controls"><label class="ring-picker-label">Case<select id="${p}-picker"></select></label></div></div>
<div class="ring-layout"><nav id="${p}-rail" class="ring-rail" aria-label="Ranked cases"></nav>
<div class="ring-canvas"><div class="ring-toolbar"><div><h4 id="${p}-title"></h4><p id="${p}-summary"></p></div><div class="ring-actions"><button id="${p}-evidence" type="button">Open full evidence</button></div></div>
<div class="ring-stage case-stage"><div id="${p}-graph" class="case-graph"></div><div id="${p}-tip" class="ring-tip" role="tooltip" hidden></div>
<div class="case-timeline"><button type="button" id="${p}-play" class="sr-play" aria-label="Replay the sender's payments">▶</button><div class="case-clock" id="${p}-clock"></div><div class="case-track" id="${p}-track" role="slider" tabindex="0" aria-label="Replay time" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100"><div id="${p}-ticks"></div><b id="${p}-head"></b></div></div>
<div class="chart-legend ring-legend"><span><i class="case-key-sender"></i>Sender</span><span><i class="legend-receiver"></i>Receiver it paid</span><span><i class="legend-hub"></i>This payment's receiver</span>${config.selfLoop===false?'':'<span><b class="legend-line legend-loop-line"></b>Pays itself</span>'}<span><b class="legend-flow"></b>Payments moving</span></div></div>
<p class="field-help">${escapeText(config.help)}</p></div>
<aside id="${p}-readout" class="case-readout" aria-live="polite"></aside></div></article>`);
    if(!el('explorer')) return false;
    el('picker').onchange=()=>self.select(Number(el('picker').value));
    el('evidence').onclick=()=>{const row=self.rows.find(item=>item.rank===self.selected);if(row) config.evidence(row);};
    return true;
  };
  // Rebuilds the rail for these rows and keeps the selected case if it is still listed.
  self.render=rows=>{
    if(!self.mount()) return;
    self.rows=rows;
    el('list-note').textContent=config.note(rows);
    el('rail').innerHTML=`<ol>${rows.map(row=>{const tier=tierFor(row.rank), meta=config.rail(row);
      return `<li><button type="button" data-rank="${row.rank}"><span class="rail-rank">#${row.rank}</span><span class="rail-main"><strong>${escapeText(dollars(row.amount_usd_asof))} · ${escapeText(row.payment_format)}</strong><span><b class="flag flag-${tier.id}">${escapeText(tier.label)}</b>${meta.flags||''}</span><small>${escapeText(meta.small)}</small></span></button></li>`;}).join('')}</ol>`;
    el('rail').querySelectorAll?.('[data-rank]').forEach(button=>{button.onclick=()=>self.select(Number(button.dataset.rank));});
    if(rows.length>10) {
      const rail=el('rail'), more=document.createElement('button');more.type='button';more.className='rail-more';
      const label=()=>{more.textContent=rail.classList.contains('rail-open')?'Show top 10 only':`Show all ${rows.length} cases`;};
      more.onclick=()=>{rail.classList.toggle('rail-open');label();};label();rail.appendChild(more);
    }
    el('picker').innerHTML=rows.map(row=>`<option value="${row.rank}">#${row.rank} · ${escapeText(dollars(row.amount_usd_asof))} · ${escapeText(row.payment_format)}</option>`).join('');
    self.select(rows.some(row=>row.rank===self.selected)?self.selected:rows[0]?.rank,{replay:false});
  };
  self.select=(rank,{replay=true}={})=>{
    const row=self.rows.find(item=>item.rank===rank);
    if(!row) return;
    self.selected=rank;el('picker').value=String(rank);
    el('rail').querySelectorAll?.('[data-rank]').forEach(button=>button.setAttribute('aria-current',String(Number(button.dataset.rank)===rank)));
    const {trail,summary}=config.trail(row);
    el('title').textContent=`Case #${integer(rank)} · ${row.transaction_id}`;
    el('summary').textContent=summary;
    el('readout').innerHTML=config.readout(row,self.rows);
    config.bind?.(row,el('readout'));
    config.selected?.(row);
    self.draw(row,trail,replay);
  };
  // The case graph and its replay. The replay clock runs 0 → 1 over CASE_REPLAY_MS; each payment flies along its link
  // at its place in time, so bursts of same-minute payments leave together.
  self.draw=(row,trail,replay)=>{
    const host=el('graph');
    if(!host) return;
    chartCalls.set(`${p}-graph`,[(r,t)=>self.draw(r,t,false),[row,trail]]);
    if(self.scene?.raf) cancelAnimationFrame(self.scene.raf);
    const width=chartWidth(host), height=width<600?380:430;
    host.innerHTML=caseGraphHTML(trail,row,width,height);
    const svg=host.querySelector?.('svg');
    if(!svg?.querySelector) return;
    const {nodes}=caseLayout(trail.receivers,width,height), paths=new Map();
    svg.querySelectorAll('.case-edge').forEach((path,i)=>paths.set([...nodes.keys()][i],path));
    const times=trail.payments.map(payment=>instant(payment.timestamp)), start=Math.min(...times), end=Math.max(...times);
    const at=trail.payments.map((payment,i)=>end>start?(times[i]-start)/(end-start):i/Math.max(1,trail.payments.length-1));
    const layer=svg.querySelector('.case-packets'), packets=trail.payments.map(payment=>{
      const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('class',`case-packet${payment.current?' case-packet-current':''}`);
      dot.setAttribute('r',payment.current?'6':'4.5');dot.setAttribute('opacity','0');layer.appendChild(dot);return dot;});
    el('ticks').innerHTML=trail.payments.map((payment,i)=>`<i class="${payment.current?'now':''}" style="left:${(at[i]*100).toFixed(2)}%" title="${escapeText(payment.current?'This payment':'Earlier payment')} · ${escapeText(trailTime(times[i]))} · ${escapeText(dollars(payment.amount_usd_asof))}"></i>`).join('');
    const scene={u:1,playing:false,raf:null};self.scene=scene;
    const set=u=>{
      scene.u=Math.max(0,Math.min(1,u));const now=scene.u*CASE_REPLAY_MS;
      trail.payments.forEach((payment,i)=>{
        const v=(now-at[i]*(CASE_REPLAY_MS-CASE_FLIGHT_MS))/CASE_FLIGHT_MS, dot=packets[i], path=paths.get(accountKey(payment.to_bank,payment.to_account));
        if(!path||v<0||(v>1.25&&!payment.current)) {dot.setAttribute('opacity','0');return;}
        const point=path.getPointAtLength(Math.min(1,v)*path.getTotalLength());
        dot.setAttribute('cx',point.x.toFixed(1));dot.setAttribute('cy',point.y.toFixed(1));dot.setAttribute('opacity',v>1&&!payment.current?String(Math.max(0,1-(v-1)*4)):'1');
      });
      const clock=start+scene.u*(end-start);
      el('clock').innerHTML=`${escapeText(trailDay(clock))} · ${escapeText(trailTime(clock).slice(11))}<small>dataset time</small>`;
      el('head').style.left=`${(scene.u*100).toFixed(2)}%`;el('track').setAttribute('aria-valuenow',String(Math.round(scene.u*100)));
    };
    const pause=()=>{scene.playing=false;if(scene.raf) cancelAnimationFrame(scene.raf);scene.raf=null;el('play').textContent='▶';};
    const play=()=>{
      if(!animating()) {set(1);return;}
      if(scene.u>=1) set(0);
      scene.playing=true;el('play').textContent='❚❚';let last=performance.now();
      const frame=now=>{try {
        if(!scene.playing||self.scene!==scene) return;
        set(scene.u+(now-last)/CASE_REPLAY_MS);last=now;
        if(scene.u>=1) {pause();return;}
        scene.raf=requestAnimationFrame(frame);
      } catch {pause();}};
      scene.raf=requestAnimationFrame(frame);
    };
    el('play').onclick=()=>scene.playing?pause():play();
    const seek=event=>{const box=el('track').getBoundingClientRect();pause();set((event.clientX-box.left)/box.width);};
    el('track').onpointerdown=event=>{seek(event);el('track').onpointermove=move=>{if(move.buttons) seek(move);};};
    el('track').onkeydown=event=>{const step={ArrowLeft:-.05,ArrowRight:.05,Home:-1,End:1}[event.key];if(step!=null) {event.preventDefault();pause();set(scene.u+step);}};
    // Hover any account for its numbers.
    const tip=el('tip');
    svg.addEventListener('pointerover',event=>{try {
      const target=event.target.closest?.('[data-key]');if(!target) return;
      const key=target.dataset.key, node=nodes.get(key);
      tip.innerHTML=key==='sender'?`<span class="tip-role" style="--role:#4fa8ff">Sender</span><span class="tip-title">${escapeText(accountKey(row.from_bank,row.from_account))}</span><span class="tip-foot">${escapeText(plural(trail.payments.length-1,'earlier payment'))} shown, then this one</span>`
        :`<span class="tip-role" style="--role:${node.current?'#ff6f5e':'#ffb648'}">${node.self?'Sender’s own account':node.current?'This payment’s receiver':'Receiver'}</span><span class="tip-title">${escapeText(key)}</span><span class="tip-grid"><span><small>Payments</small>${integer(node.count)}</span><span><small>USD</small>${escapeText(dollars(node.usd))}</span></span>`;
      tip.hidden=false;
      const box=target.getBoundingClientRect(), frame=host.getBoundingClientRect();
      tip.style.left=`${Math.max(6,Math.min(frame.width-tip.offsetWidth-6,box.left-frame.left+box.width/2-tip.offsetWidth/2))}px`;
      tip.style.top=`${Math.max(6,box.top-frame.top-tip.offsetHeight-10)}px`;
    } catch {tip.hidden=true;}});
    svg.addEventListener('pointerout',event=>{if(!event.relatedTarget?.closest?.('[data-key]')) tip.hidden=true;});
    set(1);
    if(replay&&animating()) {set(0);play();}
  };
  return self;
}
function evidenceParts(row) {
  return (row.model_evidence||'').split('; ').filter(Boolean).map(part=>{const colon=part.lastIndexOf(':');
    return {label:part.slice(0,colon),value:Number(part.slice(colon+1).replace('margin','').trim())};}).filter(part=>Number.isFinite(part.value));
}
// The case readout beside the graph, as in the presenter: chips, the payment's facts and why it ranked high.
function caseReadoutHTML(row,rows,model,ring) {
  const largest=1+rows.filter(item=>item.amount_usd_asof>row.amount_usd_asof).length, parts=model?evidenceParts(row):[];
  const top=Math.max(.01,...parts.map(part=>Math.abs(part.value)));
  return `<div class="case-chips">${rankChip(row)}${largest<=Math.min(25,rows.length)?`<span class="flag flag-pay">#${integer(largest)} largest amount</span>`:''}${ring?`<button type="button" class="ring-link case-ring" data-ring="${escapeText(ring.ring_id)}">In BQ2 ring #${integer(ring.rank)} →</button>`:''}</div>
<p class="account-id">${escapeText(row.transaction_id)}</p>
<div class="ring-facts"><span><small>Amount (prior-day USD)</small>${escapeText(dollars(row.amount_usd_asof))}</span><span><small>Ranking score</small>${escapeText(model?Number(row.model_score).toFixed(4):String(row.risk_score))}</span><span><small>Payment · currency</small>${escapeText(row.payment_format)} · ${escapeText(row.payment_currency)}</span><span><small>Event time</small>${escapeText(row.timestamp.replace('T',' ').slice(0,16))}</span><span><small>Counterparties, past 24h</small>${integer(row.distinct_counterparties_24h)}</span><span><small>Payments, past hour</small>${integer(row.txn_count_1h)}</span></div>
<h4>Why it ranked high</h4>${parts.length?`<div class="case-why">${parts.map(part=>`<div class="${part.value<0?'neg':''}"><span>${escapeText(part.label)}</span><b>${part.value>=0?'+':''}${part.value.toFixed(2)}</b><i style="--w:${(Math.abs(part.value)/top*100).toFixed(0)}%"></i></div>`).join('')}</div><p class="field-help">Signed model contributions to the ranking margin; not probabilities or proof.</p>`
    :`<p>${escapeText(model?'Model contributions are exported for the whole-period top 250 only.':row.risk_reasons||'No rule triggered')}</p>`}`;
}
// The incoming readout: what the stream publishes for the payment, including the sender history the model saw.
function incomingReadoutHTML(row,rows) {
  const largest=1+rows.filter(item=>item.amount_usd_asof>row.amount_usd_asof).length;
  const scored=row.scored_at?new Date(row.scored_at).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
  return `<div class="case-chips">${rankChip(row)}${row.review_now===true?'<span class="flag flag-urgent">Review now · batch cut-off</span>':''}${largest<=Math.min(25,rows.length)?`<span class="flag flag-pay">#${integer(largest)} largest amount</span>`:''}</div>
<p class="account-id">${escapeText(row.transaction_id)}</p>
<div class="ring-facts"><span><small>Amount (prior-day USD)</small>${escapeText(dollars(row.amount_usd_asof))}</span><span><small>Ranking score</small>${Number(row.model_score).toFixed(4)}</span><span><small>Payment · currency</small>${escapeText(row.payment_format)} · ${escapeText(row.payment_currency)}</span><span><small>Event time</small>${escapeText(row.timestamp.replace('T',' ').slice(0,16))}</span><span><small>Scored at (this device)</small>${escapeText(scored)}</span><span><small>Counterparties, 24h / 7d</small>${integer(row.distinct_counterparties_24h)} / ${integer(row.distinct_counterparties_7d)}</span></div>
<h4>What the model saw of the sender</h4><div class="case-history">${[['Past hour',row.txn_count_1h],['Past 24 hours',row.txn_count_24h],['Past 7 days',row.txn_count_7d]].map(([label,count])=>`<span><b>${integer(count)}</b><small>${label}</small></span>`).join('')}</div>
<p class="field-help">Earlier payments the Lambda rebuilt from DynamoDB before scoring this one; the payment never counts itself.</p>`;
}

// The review queue's explorer: the batch queue for the selected scorer, period, population and capacity.
const reviewRing=row=>ringOf(row.from_bank,row.from_account)||ringOf(row.to_bank,row.to_account);
const reviewCases=caseExplorer('case',{
  title:'Case explorer',
  help:'Bigger receivers took more money; thicker links carry more payments. Press play or drag the timeline to replay the sender\'s earlier payments in time order, then this one.',
  place:html=>element('investigation-panel')?.insertAdjacentHTML?.('beforebegin',html),
  note:rows=>`The ${isModelQueue()?'frozen model':'rule baseline'}'s top ${integer(rows.length)}, in rank order. Pick one to replay its sender's history.`,
  rail:row=>{const linked=reviewRing(row);return {flags:linked?`<b class="flag flag-loop">BQ2 ring #${integer(linked.rank)}</b>`:'',
    small:`${isModelQueue()?`score ${Number(row.model_score).toFixed(4)}`:`rule score ${row.risk_score}`} · ${trailDay(instant(row.timestamp))} ${row.timestamp.slice(11,16)}`};},
  trail:row=>{const history=snapshot.exploration.aml.histories[row.transaction_id], trail=caseTrail(history?.events||[],row);
    return {trail,summary:history?`${trail.summary}${history.observed_prior_transactions>history.events.length?` Latest ${integer(history.events.length)} of ${integer(history.observed_prior_transactions)} earlier payments shown.`:''}`
      :'The sender history is exported for the whole-period top 250 only, so this case shows the payment alone.'};},
  readout:(row,rows)=>caseReadoutHTML(row,rows,isModelQueue(),reviewRing(row)),
  bind:(row,readout)=>{const ring=reviewRing(row);readout.querySelector?.('.case-ring')?.addEventListener?.('click',()=>openRing(ring.ring_id,ringOf(row.from_bank,row.from_account)?accountKey(row.from_bank,row.from_account):accountKey(row.to_bank,row.to_account)));},
  evidence:row=>showDetail(row),
  selected:row=>element('queue')?.querySelectorAll?.('tbody tr')?.forEach(tr=>tr.classList?.toggle('row-selected',Number(tr.dataset?.rank)===row.rank))});
function renderCaseExplorer() {
  if(!snapshot?.exploration || !reviewCases.mount()) return;
  element('case-explorer').hidden=amlMode!=='investigation';
  const population=selectedPopulation(), data=population[isModelQueue()?'model':'rules'];
  reviewCases.render(data.queue.slice(0,Number(selectedCapacity())).map((id,index)=>({...projectionRecord(id),rank:index+1})));
}

// The incoming stream's explorer. It only knows the payments the stream publishes (the ranked queue and the latest
// arrivals), and account numbers are masked, so a sender's trail is drawn from those payments and has no "pays itself" loop.
let incomingStream=null;
function streamTrail(data,row) {
  const sender=`${row.from_bank}|${row.from_account}`, seen=new Set([row.transaction_id]);
  const earlier=[...data.queue,...data.latest].filter(item=>`${item.from_bank}|${item.from_account}`===sender && item.timestamp<row.timestamp && !seen.has(item.transaction_id) && seen.add(item.transaction_id));
  return caseTrail(earlier,row,{lead:'Among the payments this stream publishes, the sender made',none:'None of this sender\'s earlier payments are among the ones this stream publishes (its ranked top 100 and latest arrivals).',self:false});
}
const incomingCases=caseExplorer('icase',{
  title:'Case explorer',selfLoop:false,
  help:'The same view as BQ1 · Review queue. Account numbers are masked in the stream, so receivers are told apart by bank and the last characters of the account.',
  place:html=>(element('stream-replay')||element('incoming-progressbar'))?.insertAdjacentHTML?.('afterend',html),
  note:rows=>`The stream's top ${integer(rows.length)} by ranking score. Pick one to replay what this stream published of its sender.`,
  rail:row=>({flags:row.review_now===true?'<b class="flag flag-urgent">Review now</b>':'',small:`score ${Number(row.model_score).toFixed(4)} · ${trailDay(instant(row.timestamp))} ${row.timestamp.slice(11,16)}`}),
  trail:row=>{const trail=streamTrail(incomingStream.data,row);return {trail,summary:trail.summary};},
  readout:(row,rows)=>incomingReadoutHTML(row,rows),
  evidence:row=>incomingStream.open(row)});

// Incoming → the other tabs: how this stream relates to the review queue, mule networks and the batch pipeline.
// Every number comes from the two snapshots; account numbers are masked in the stream, so nothing is matched by account.
function streamLinksHTML(data,batch) {
  const names={train:'Training reference',validation:'Validation',test:'Test',tail:'Later period'}, colors={train:'#5f7a8e',validation:'#4fa8ff',test:'#3fd0b8',tail:'#ffb648'};
  const splits=batch?.splits, periods=splits?Object.keys(splits):[];
  const counts=periods.map(name=>data.queue.filter(row=>row.timestamp.slice(0,10)>=splits[name][0]&&row.timestamp.slice(0,10)<splits[name][1]).length);
  const queued=new Set(Object.values(batch?.exploration?.aml?.periods||{}).flatMap(period=>period.scopes.flatMap(scope=>[...scope.model.queue,...scope.rules.queue])));
  const inBatch=data.queue.filter(row=>queued.has(row.transaction_id)).length;
  const mix=splits?`<div class="period-bar" role="img" aria-label="${escapeText(periods.map((name,i)=>`${names[name]||name}: ${counts[i]}`).join(', '))}">${periods.map((name,i)=>counts[i]?`<i style="flex:${counts[i]};background:${colors[name]||'#a9bccb'}"></i>`:'').join('')}</div><p class="period-key">${periods.map((name,i)=>counts[i]?`<span><i style="background:${colors[name]||'#a9bccb'}"></i>${escapeText(names[name]||name)} · ${escapeText(windowText(splits[name]))} · <b>${integer(counts[i])}</b></span>`:'').join('')}</p>`:'';
  return `<div class="link-card"><div class="link-head"><span class="tab-tag">BQ1</span><h4>Review queue</h4></div><p>Scored by the same frozen model and sender features as the batch review queue, one payment at a time. The stream's top ${integer(data.queue.length)} fall on these evaluation days:</p>${mix}<p>${splits?`${inBatch?`${integer(inBatch)} of them also appear`:'None of them appear'} in the batch queues exported for review, so the two queues rank different payments.`:'Load the batch release to compare dates with the review queue.'}</p><button type="button" data-view="aml">Open the review queue →</button></div>
<div class="link-card"><div class="link-head"><span class="tab-tag">BQ2</span><h4>Mule networks</h4></div><p>Rings come from the verified batch run${batch?.full_rows?` over all ${integer(batch.full_rows)} payments`:''}. The stream masks account numbers, so its payments are not matched to rings here. The stream keeps each sender's own history; a ring needs who paid whom across all accounts.</p><button type="button" data-view="network">Open mule networks →</button></div>
<div class="link-card"><div class="link-head"><span class="tab-tag">Pipeline</span><h4>Where the scores come from</h4></div><p>Not part of the batch pipeline on the Ingestion tab. Payments go into Amazon Kinesis, keyed by sender so each sender stays in order; a Lambda rebuilds the sender's recent history, scores with the frozen model and commits once to DynamoDB; a second Lambda publishes this page through CloudFront.</p><button type="button" data-view="ingestion">Open the batch pipeline →</button></div>`;
}
function streamLinks(data) {
  if(!element('incoming-links')) {
    if(typeof element('incoming-kpis')?.insertAdjacentHTML!=='function') return;
    element('incoming-kpis').insertAdjacentHTML('afterend','<section id="incoming-links" class="link-strip" aria-label="How this stream connects to the other tabs"></section>');
  }
  const host=element('incoming-links');
  if(!host) return;
  host.innerHTML=streamLinksHTML(data,snapshot);
  host.querySelectorAll?.('[data-view]').forEach(button=>{button.onclick=()=>{selectDashboardView(button.dataset.view);scrollTo?.({top:0,behavior:'smooth'});};});
}

// ---- BQ1 incoming: replay the stream and watch the final ranked queue fill in as each payment is scored ----
// Each slot is a payment in the snapshot's ranked queue, at its final rank; it lights when its score was committed.
// The counter follows the published commit history. No labels are involved.
const SR_MS=12000;
let streamView=null;
// The scored count at time t, from the commit history (the last commit at or before t).
function streamCountAt(progress,t) {
  let count=0;
  for(const point of progress) {if(Date.parse(point.at)<=t) count=point.processed;else break;}
  return count;
}
function streamReplayHTML(data,width=900) {
  const rows=data.queue, cols=width<520?10:rows.length<=50?10:20, cell=Math.min(36,Math.floor(Math.min(width*.66,760)/cols));
  const slots=rows.map((row,i)=>{const x=(i%cols)*cell, y=Math.floor(i/cols)*cell, size=cell-6, tier=tierFor(row.rank);
    return `<g class="qx-cell sr-slot qx-${tier.id}" data-index="${i}" style="--i:0"><rect x="${x+3}" y="${y+3}" width="${size}" height="${size}" rx="${Math.max(2,size*.18).toFixed(1)}"/>${cell>=26?`<text class="qx-rank" x="${x+cell/2}" y="${y+cell/2+4}" text-anchor="middle">${row.rank}</text>`:''}</g>`;}).join('');
  const gridWidth=cols*cell, gridHeight=Math.ceil(rows.length/cols)*cell;
  return {cols,html:`<svg class="qx-svg sr-grid" viewBox="0 0 ${gridWidth} ${gridHeight}" style="max-width:${gridWidth}px" tabindex="0" role="img" aria-label="The ranked incoming queue, lit as each payment was scored. Use the arrow keys to move and Enter to open evidence.">${slots}</svg>`};
}
function streamReplay(data,open) {
  const anchor=element('incoming-progressbar');
  if(!element('stream-replay')) {
    if(typeof anchor?.insertAdjacentHTML!=='function') return;
    document.querySelector?.('#incoming > .heading')?.insertAdjacentHTML?.('beforeend','<button id="incoming-play" class="present-button present-cta section-cta" type="button">▶ Replay the stream</button>');
    if(element('incoming-play')) element('incoming-play').onclick=()=>{element('stream-replay')?.scrollIntoView?.({behavior:'smooth',block:'start'});streamView?.replay?.();};
    anchor.insertAdjacentHTML('afterend',`<article id="stream-replay" class="queue-explorer" aria-labelledby="sr-title"><div class="heading"><div><h3 id="sr-title">Stream replay</h3><p class="qx-note">Press play to replay the stream: each square is a payment in the ranked queue, at its final rank, and it lights up when its score was committed. Hover for details; click to open its evidence.</p></div></div><div class="ring-stage qx-stage-frame"><div class="sr-controls"><button type="button" id="sr-play" class="sr-play" aria-label="Play the stream replay">▶</button><input id="sr-scrub" type="range" min="0" max="1000" value="1000" aria-label="Stream time"><span id="sr-clock" class="sr-clock"></span></div><div class="sr-body"><div class="sr-side"><small>Scored so far</small><strong id="sr-count">0</strong><small id="sr-of"></small><small class="sr-placed-label">Ranked queue placed</small><strong id="sr-placed" class="sr-placed">0</strong><svg id="sr-spark" class="sr-spark" viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true"></svg></div><div id="sr-stage" class="qx-stage sr-stage"></div></div></div></article>`);
  }
  const host=element('sr-stage');
  if(!host) return;
  const key=`${data.generated_at}|${data.processed}|${data.queue.length}`;
  if(streamView?.key===key) return;
  const first=!streamView;
  if(streamView?.raf) cancelAnimationFrame(streamView.raf);
  const start=Date.parse(data.progress[0].at), end=Date.parse(data.progress.at(-1).at), times=data.queue.map(row=>Date.parse(row.scored_at||end));
  const view=streamReplayHTML(data,chartWidth(element('stream-replay')));
  host.innerHTML=`${view.html}<div id="sr-tip" class="ring-tip qx-tip" role="tooltip" hidden></div>`;
  const svg=host.querySelector('svg'), slots=[...svg.querySelectorAll('.sr-slot')], tip=element('sr-tip');
  // The commit history as a small area chart with a playhead.
  const xs=t=>(t-start)/Math.max(end-start,1)*200, ys=count=>58-count/Math.max(data.expected,1)*54;
  const line=data.progress.map((point,i)=>`${i?'L':'M'}${xs(Date.parse(point.at)).toFixed(1)} ${ys(point.processed).toFixed(1)}`).join(' ');
  element('sr-spark').innerHTML=`<path d="${line} L200 60 L0 60 Z" fill="rgba(63,208,184,.14)"/><path d="${line}" fill="none" stroke="#3fd0b8" stroke-width="1.6" vector-effect="non-scaling-stroke"/><line id="sr-head" x1="0" x2="0" y1="0" y2="60" stroke="#dff4ff" stroke-width="1.4" vector-effect="non-scaling-stroke"/>`;
  element('sr-of').textContent=`of ${integer(data.expected)} payments`;
  const state={key,t:end,playing:false,raf:null};streamView=state;
  const set=t=>{
    state.t=Math.max(start,Math.min(end,t));
    let placed=0;slots.forEach((slot,i)=>{const on=times[i]<=state.t;slot.classList.toggle('sr-on',on);if(on) placed++;});
    element('sr-count').textContent=integer(streamCountAt(data.progress,state.t));
    element('sr-placed').textContent=`${integer(placed)} / ${integer(slots.length)}`;
    element('sr-clock').textContent=new Date(state.t).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    element('sr-scrub').value=String(Math.round((state.t-start)/Math.max(end-start,1)*1000));
    const head=element('sr-head');head?.setAttribute('x1',xs(state.t));head?.setAttribute('x2',xs(state.t));
  };
  const pause=()=>{state.playing=false;if(state.raf) cancelAnimationFrame(state.raf);state.raf=null;element('sr-play').textContent='▶';element('sr-play').setAttribute('aria-label','Play the stream replay');};
  const play=()=>{
    if(!animating()) {set(end);return;}
    if(state.t>=end) set(start);
    state.playing=true;element('sr-play').textContent='❚❚';element('sr-play').setAttribute('aria-label','Pause the stream replay');
    let last=performance.now();
    const frame=now=>{try {
      if(!state.playing || streamView!==state) return;
      set(state.t+(now-last)/SR_MS*(end-start));last=now;
      if(state.t>=end) {pause();return;}
      state.raf=requestAnimationFrame(frame);
    } catch {pause();}};
    state.raf=requestAnimationFrame(frame);
  };
  element('sr-play').onclick=()=>state.playing?pause():play();
  state.replay=()=>{pause();set(start);play();};
  element('sr-scrub').oninput=event=>{pause();set(start+Number(event.target.value)/1000*(end-start));};
  // Hover, keyboard and click, as in the review-queue explorer.
  let current=-1;
  const show=index=>{try {
    current=Math.max(0,Math.min(slots.length-1,index));const row=data.queue[current], slot=slots[current], tier=tierFor(row.rank);
    slots.forEach(item=>item.classList.toggle('qx-focus',item===slot));
    tip.innerHTML=`<span class="tip-role" style="--role:${tier.color}">#${integer(row.rank)} · ${escapeText(tier.label)}</span><span class="tip-title">${escapeText(row.transaction_id)}</span><span class="tip-grid"><span><small>Ranking score</small>${Number(row.model_score).toFixed(4)}</span><span><small>Prior-day USD</small>${escapeText(dollars(row.amount_usd_asof))}</span></span><span class="tip-foot">Scored ${row.scored_at?escapeText(new Date(row.scored_at).toLocaleTimeString('en-GB')):'—'} · ${escapeText(row.payment_format)} · ${escapeText(row.payment_currency)}</span>`;
    tip.hidden=false;
    const box=slot.getBoundingClientRect(), frame=host.getBoundingClientRect();
    tip.style.left=`${Math.max(4,Math.min(frame.width-tip.offsetWidth-4,box.left-frame.left+box.width/2-tip.offsetWidth/2))}px`;
    tip.style.top=`${Math.max(4,box.top-frame.top-tip.offsetHeight-8)}px`;
  } catch {tip.hidden=true;}};
  const hide=()=>{tip.hidden=true;slots.forEach(item=>item.classList.remove('qx-focus'));};
  svg.addEventListener('pointermove',event=>{const slot=event.target.closest?.('[data-index]');if(slot) {if(+slot.dataset.index!==current||tip.hidden) show(+slot.dataset.index);} else hide();});
  svg.addEventListener('pointerleave',()=>{hide();current=-1;});
  svg.addEventListener('click',event=>{const slot=event.target.closest?.('[data-index]');if(slot) {slots.forEach(item=>item.classList.toggle('qx-selected',item===slot));open(data.queue[+slot.dataset.index]);}});
  svg.addEventListener('focus',()=>show(current<0?0:current));svg.addEventListener('blur',hide);
  svg.addEventListener('keydown',event=>{const step={ArrowLeft:-1,ArrowRight:1,ArrowUp:-view.cols,ArrowDown:view.cols}[event.key];
    if(step) {event.preventDefault();show(current+step);}
    else if(event.key==='Enter'&&current>=0) {event.preventDefault();slots.forEach(item=>item.classList.toggle('qx-selected',item===slots[current]));open(data.queue[current]);}
    else if(event.key==='Escape') hide();});
  // First show: replay from the start once; afterwards keep the end state as new data arrives.
  set(end);
  if(first && animating()) {set(start);play();}
}

// ---- BQ2: mule networks (hubs, loops, rings) and the ring node graph ----
let networkReady=false, selectedRing=null, selectedAccount=null;
const ringLayouts={}, ringLayoutShape={};
// Wide canvas on desktop; a taller, narrower one on phones so nodes stay legible.
const graphShape = () => (window.innerWidth||1200)<760 ? {width:520,height:620,margin:40} : {width:860,height:560,margin:48};
let GRAPH=graphShape();
// Display roles: counterparties are split by the direction of their money within the ring.
const ROLE={hub:{label:'Hub',color:'#b3402f',radius:14},loop:{label:'Loop account',color:'#7c5cb0',radius:9},
  sender:{label:'Sends money in',color:'#2b6c9a',radius:6},receiver:{label:'Receives money out',color:'#c98a1b',radius:6},
  both:{label:'Both directions',color:'#6b7f8e',radius:6}};
const share = value => value==null ? 'Unavailable' : `${(value*100).toFixed(1)}%`;
const times = value => value==null ? 'Unavailable' : `${value.toFixed(1)}×`;
const plural = (count,word) => `${integer(count)} ${word}${count===1?'':'s'}`;
const shortAccount = id => {const [bank,account='']=String(id).split(':');return account.length>9?`${bank}:…${account.slice(-5)}`:String(id);};
const dateRange = (first,last) => `${monthDay(first.slice(0,10))} → ${monthDay(last.slice(0,10))}`;
const ringRow = id => snapshot.network.ring_list.find(item=>item.ring_id===id);
// The account investigators start from: the busiest hub, else the busiest loop account.
const leadAccount = id => snapshot.network.graphs[id].nodes[0]?.id || id;

// Within-ring direction of each account: hub/loop, or a counterparty that only sends, only receives, or both.
// Ring account ids drop the bank's leading zeros (network.account_key), so BQ1 rows are keyed the same way.
const accountKey=(bank,account)=>`${String(bank).replace(/^0+/,'')||'0'}:${account}`;
let ringIndexFor=null, ringIndex=new Map();
// The best-ranked listed ring that draws this account, or null (only the listed rings' accounts are known here).
function ringOf(bank,account) {
  const net=snapshot?.network;
  if(!net?.graphs||!net.ring_list) return null;
  if(ringIndexFor!==net) {
    ringIndexFor=net;ringIndex=new Map();
    for(const row of net.ring_list) for(const node of net.graphs[row.ring_id]?.nodes||[]) if(!ringIndex.has(node.id)) ringIndex.set(node.id,row);
  }
  return ringIndex.get(accountKey(bank,account))||null;
}
// BQ1 -> BQ2: buttons naming the mule ring the sender or receiver belongs to.
function ringLinks(row) {
  const links=[['Sender',row.from_bank,row.from_account],['Receiver',row.to_bank,row.to_account]].map(([who,bank,account])=>{const ring=ringOf(bank,account);
    return ring?`<button type="button" class="ring-link" data-ring="${escapeText(ring.ring_id)}" data-account="${escapeText(accountKey(bank,account))}">${who} is in mule ring #${integer(ring.rank)} →</button>`:'';}).join('');
  return links?`<div class="ring-links">${links}</div>`:'';
}
function openRing(id,account) {
  selectDashboardView('network');selectRing(id,account);
  element('ring-explorer').scrollIntoView?.({behavior:'smooth',block:'start'});
}
// One ring as one case: each drawn account, its role and its payments inside the ring. Laundering labels are left out.
function ringCase(id) {
  const row=ringRow(id), graph=snapshot.network.graphs[id], roles=flowRoles(graph);
  const total=(links,key)=>links.reduce((sum,link)=>sum+(Number(link[key])||0),0), money=value=>Math.round(value*100)/100;
  return graph.nodes.map(node=>{const sent=graph.links.filter(link=>link.source===node.id), received=graph.links.filter(link=>link.target===node.id);
    return {ring_rank:row.rank,ring_id:id,account:node.id,role:roles[node.id],payments_in_ring_received:total(received,'txns'),payments_in_ring_sent:total(sent,'txns'),
      usd_in_ring_received:money(total(received,'usd')),usd_in_ring_sent:money(total(sent,'usd')),distinct_senders_all_data:node.fan_in,distinct_receivers_all_data:node.fan_out,
      usd_received_all_data:money(node.in_usd),usd_sent_all_data:money(node.out_usd),accounts_in_file:`${graph.shown_accounts} of ${graph.total_accounts}`};})
    .sort((a,b)=>(b.role==='hub')-(a.role==='hub')||(b.payments_in_ring_received+b.payments_in_ring_sent)-(a.payments_in_ring_received+a.payments_in_ring_sent));
}

function flowRoles(graph) {
  const sends=new Set(graph.links.map(link=>link.source)), receives=new Set(graph.links.map(link=>link.target));
  return Object.fromEntries(graph.nodes.map(node=>[node.id,node.role!=='counterparty'?node.role:
    sends.has(node.id)&&!receives.has(node.id)?'sender':receives.has(node.id)&&!sends.has(node.id)?'receiver':'both']));
}

function renderNetwork() {
  const data=snapshot?.network;
  element('network-body').hidden=!data;element('network-empty').hidden=Boolean(data);
  if(!data) {
    trustStrip('network-context',`${integer(snapshot?.full_rows||0)} transactions in this release.`,false);
    element('network-empty').textContent='This release was published before the mule-network analysis. Run the pipeline again to publish BQ2 network results.';
    return;
  }
  const t=data.totals, p=data.parameters, headline=data.thresholds.find(row=>row.headline)||data.thresholds[0];
  const three=data.loops.three_account, rings=data.rings;
  element('network-question').textContent=data.question;
  const checked=data.verification?.status==='passed', run=snapshot.pipeline;
  // Releases with pipeline metadata lead with the dataset and its dates, as on the review queue.
  const scope=run?`${run.subset?`${run.subset} · `:''}${run.start_date} to ${run.end_date} · ${integer(t.rows)} transactions · `:'';
  const review=data.component_review, reviewNote=review?.components?` · ${integer(review.components)} oversized connected groups (${integer(review.accounts)} accounts) withheld from investigation rings; separate network review required. Full-population totals retained.`:'';
  trustStrip('network-context',`${scope}${checked?'Glue tables matched an independent recomputation · ':''}${integer(t.accounts)} accounts · ${integer(t.links)} account links after merging repeat payments · ${integer(t.self_transfers)} self-transfers excluded${reviewNote}`,checked&&!review?.components);
  cards('network-kpis',[
    ['Rings to investigate',integer(rings.rings),`${integer(rings.accounts)} accounts · largest ${integer(rings.largest)}`,false,
      Array.isArray(data.ring_sizes)&&data.ring_sizes.length>1?{values:data.ring_sizes,color:'#ff6f5e',label:'Accounts in each ring, in rank order',caption:`Ring size by rank, #1 → #${integer(data.ring_sizes.length)}`}:null],
    ['Ring accounts touching laundering',share(rings.laundering_rate),`${times(rings.lift)} the ${share(t.laundering_rate)} base rate`,true],
    [`Mule-sized hubs (${p.headline_threshold}+ in and out)`,integer(headline.hubs.accounts),`${share(headline.hubs.laundering_rate)} touch laundering · ${integer(headline.high_volume.accounts)} high-volume listed separately`],
    ['Three-account loops',integer(three.loops),`${integer(three.accounts)} accounts · ${share(three.laundering_rate)} touch laundering`]]);
  table('threshold-table',['Cut-off','Hubs','Hubs · laundering','High-volume · laundering','Fan-in only','Fan-out only'],
    data.thresholds.map(row=>[`${row.threshold}+${row.headline?' (rings)':''}`,integer(row.hubs.accounts),`${integer(row.hubs.laundering_accounts)} · ${share(row.hubs.laundering_rate)}`,
      `${integer(row.high_volume.accounts)} · ${share(row.high_volume.laundering_rate)}`,`${integer(row.fan_in_only.accounts)} · ${share(row.fan_in_only.laundering_rate)}`,`${integer(row.fan_out_only.accounts)} · ${share(row.fan_out_only.laundering_rate)}`]));
  element('threshold-note').textContent=`A hub has at least this many distinct senders and receivers, and at most ${p.max_links} links in total. The cut-off is our choice, so three are shown. High-volume accounts pass the cut-off but have more than ${p.max_links} links.`;
  const signals=[
    ['All accounts (base rate)',{accounts:t.accounts,laundering_accounts:t.laundering_accounts,laundering_rate:t.laundering_rate,lift:1},'base'],
    [`Fan-out ${p.headline_threshold}+ only`,headline.fan_out_only],[`Fan-in ${p.headline_threshold}+ only`,headline.fan_in_only],
    ['Two-account back-and-forth',data.loops.two_account],['Three-account loops',three],
    ['Ring accounts',{accounts:rings.accounts,laundering_accounts:rings.laundering_accounts,laundering_rate:rings.laundering_rate,lift:rings.lift},'ring'],
    [`Mule-sized hubs (${p.headline_threshold}+)`,headline.hubs,'ring']];
  signals.sort((a,b)=>(a[2]==='base'?-1:0)-(b[2]==='base'?-1:0)||(a[1].laundering_rate??0)-(b[1].laundering_rate??0));
  const top=Math.max(...signals.map(([,row])=>row.laundering_rate||0),.01);
  element('signal-bars').innerHTML=`<div class="signal-bars">${signals.map(([label,row,kind])=>`<div class="signal-row${kind?` signal-${kind}`:''}"><span>${escapeText(label)}<small>${escapeText(integer(row.accounts))} accounts</small></span><div class="bar-track"><div class="bar-fill" style="width:${(row.laundering_rate||0)/top*100}%${kind==='base'?'':`;background:${heatFill(row.laundering_rate)}`}"></div></div><strong>${escapeText(share(row.laundering_rate))}<small>${kind==='base'?'base':escapeText(times(row.lift))}</small></strong></div>`).join('')}</div>`;
  table('signal-table',['Signal','Accounts','Touching laundering','Rate','Against base'],signals.map(([label,row])=>[label,integer(row.accounts),integer(row.laundering_accounts),share(row.laundering_rate),times(row.lift)]));
  table('top-rings',['Rings reviewed','Accounts','Touching laundering','Rings with any'],data.top_rings.map(row=>[`Top ${integer(row.rings)}`,integer(row.accounts),share(row.laundering_rate),`${integer(row.rings_with_laundering)} of ${integer(row.rings)}`]));
  element('network-terms').innerHTML=Object.entries(data.definitions).map(([term,text])=>`<dt>${escapeText(term.replace('_',' '))}</dt><dd>${escapeText(text)}</dd>`).join('');
  element('network-verification').textContent=data.verification?JSON.stringify(data.verification,null,2):'';
  renderRingList(data);
  element('network-present').hidden=!presenterAvailable(data);
  if(!networkReady) {
    networkReady=true;
    bindPresenter();
    element('ring-picker').onchange=()=>selectRing(element('ring-picker').value);
    element('ring-search').oninput=()=>findAccount(element('ring-search').value);
    element('ring-export').onclick=()=>{if(selectedRing) download(ringCase(selectedRing),`ring-${ringRow(selectedRing).rank}-case.csv`);};
    element('ring-reset').onclick=()=>{if(selectedRing){delete ringLayouts[selectedRing];delete ringLayouts[selectedRing+'|focus'];stopRingScene();ringScene=null;renderRing();}};
    element('ring-flow').onclick=toggleRingFlow;
    element('ring-zoom-in').onclick=()=>zoomRing(1.3);element('ring-zoom-out').onclick=()=>zoomRing(1/1.3);element('ring-fit').onclick=()=>ringScene&&fitRing(ringScene);
    // Animate only while the graph is on screen.
    if(typeof IntersectionObserver==='function') new IntersectionObserver(entries=>{ringVisible=entries.some(entry=>entry.isIntersecting);if(ringVisible&&ringScene) wakeRing(ringScene);}).observe(element('ring-graph'));
    element('export-rings').onclick=()=>download(snapshot.network.ring_list,'mule-network-rings.csv');
  }
  const ids=data.ring_list.map(row=>row.ring_id);
  element('ring-explorer').hidden=!ids.length;
  if(!ids.length) {element('ring-list').textContent='No rings were found in this run. Small demonstration runs usually contain too few payments for hubs or loops.';return;}
  selectOptions('ring-picker',ids,id=>{const row=ringRow(id);return `#${row.rank} · ${shortAccount(leadAccount(id))} · ${row.accounts} accounts`;});
  selectRing(ids.includes(selectedRing)?selectedRing:ids[0]);
}

function renderRingList(data) {
  element('ring-list-note').textContent=`Top ${data.ring_list.length} of ${integer(data.rings.rings)} rings. ${data.definitions.ranking}`;
  element('ring-rail').innerHTML=`<ol>${data.ring_list.map(row=>`<li><button type="button" data-ring="${escapeText(row.ring_id)}" title="Ring ${escapeText(row.ring_id)}"><span class="rail-rank">#${row.rank}</span><span class="rail-main"><strong>${escapeText(shortAccount(leadAccount(row.ring_id)))}</strong><span>${row.hubs?`<b class="flag flag-hub">${plural(row.hubs,'hub')}</b>`:''}${row.loops?`<b class="flag flag-loop">${plural(row.loops,'loop')}</b>`:''}<b class="flag flag-pay" title="Rings rank by hubs, then loops, then payments">${plural(row.txns,'payment')}</b></span><small>${integer(row.accounts)} accounts · ${dollars(row.usd)}</small></span></button></li>`).join('')}</ol>`;
  element('ring-rail').querySelectorAll?.('[data-ring]').forEach(button=>{button.onclick=()=>selectRing(button.dataset.ring);});
  // The rail lists the top 10 (plus the selected ring) and grows on request, so it never needs its own scroll box.
  if(data.ring_list.length>10&&typeof document.createElement==='function') {
    const rail=element('ring-rail'), more=document.createElement('button');
    more.type='button';more.className='rail-more';
    const label=()=>{more.textContent=rail.classList.contains('rail-open')?'Show top 10 only':`Show all ${data.ring_list.length} rings`;};
    more.onclick=()=>{rail.classList.toggle('rail-open');label();};
    label();rail.appendChild?.(more);
  }
  table('ring-list',['Rank','Lead account','Ring ID (smallest account)','Accounts','Hubs','Loops','Payments','USD','Active','Accounts touching laundering'],
    data.ring_list.map(row=>[row.rank,leadAccount(row.ring_id),row.ring_id,integer(row.accounts),integer(row.hubs),integer(row.loops),integer(row.txns),dollars(row.usd),dateRange(row.first_seen,row.last_seen),share(row.laundering_rate)]));
  element('ring-list').querySelectorAll('tbody tr').forEach((tr,index)=>{
    const row=data.ring_list[index], button=document.createElement('button');
    button.textContent=leadAccount(row.ring_id);button.onclick=()=>{selectRing(row.ring_id);element('ring-explorer').scrollIntoView?.({behavior:'smooth'});};
    tr.children?.[1]?.replaceChildren?.(button);
  });
}

function selectRing(id,account=null) {
  selectedRing=id;selectedAccount=account||leadAccount(id);element('ring-picker').value=id;
  const row=ringRow(id), graph=snapshot.network.graphs[id], roles=flowRoles(graph);
  const senders=Object.values(roles).filter(role=>role==='sender').length, receivers=Object.values(roles).filter(role=>role==='receiver').length;
  element('ring-rail').querySelectorAll?.('[data-ring]').forEach(button=>{
    button.setAttribute('aria-current',String(button.dataset.ring===id));
  });
  element('ring-title').textContent=`Ring #${row.rank} · lead account ${leadAccount(id)}`;
  element('ring-summary').textContent=row.hubs?`${plural(row.hubs,'hub')} collecting from ${plural(senders,'sender')} and paying ${plural(receivers,'receiver')}${row.loops?`, plus ${plural(row.loops,'timed loop')}`:''}. Treat it as one case.${presClub(graph)?' Closed club: every hub pays every other hub. Rule out payroll or group payments before acting.':''}`
    :`${plural(row.loops,'timed loop')} linking ${plural(row.accounts,'account')}: money comes back to where it started within seven days. Treat it as one case.`;
  element('ring-kpis').innerHTML=[[integer(row.accounts),graph.truncated?`accounts (${graph.shown_accounts} drawn)`:'accounts'],[integer(row.txns),'payments'],[dollars(row.usd),'USD'],
    [dateRange(row.first_seen,row.last_seen),'active'],[share(row.laundering_rate),'touch laundering (check)']].map(([value,label])=>`<span><strong>${escapeText(value)}</strong> ${escapeText(label)}</span>`).join('');
  renderRing();
}

function findAccount(text) {
  const query=text.trim().toLowerCase();
  if(!query) {element('ring-search-result').textContent='';return;}
  for(const row of snapshot.network.ring_list) {
    const node=snapshot.network.graphs[row.ring_id].nodes.find(item=>item.id.toLowerCase().includes(query));
    if(node) {element('ring-search-result').textContent=`${node.id} is in ring #${row.rank}.`;selectRing(row.ring_id,node.id);return;}
  }
  element('ring-search-result').textContent='No drawn ring contains that account. Only the listed rings are searchable here.';
}

// Deterministic force-directed layout (Fruchterman–Reingold). Accounts repel,
// links attract and, in hub rings, senders drift left and receivers right so
// the picture reads "money in → hub → money out". Big rings (HI-Medium has
// rings of 100+ accounts with a dozen hubs) need room: hubs share a central
// band of staggered columns instead of one line, each side spreads over as many
// columns as its accounts need, and a final pass keeps dots from overlapping.
const RING_GAP={hub:36,side:26}, RING_BIG=40;
// Focus view: a big ring draws its hubs, its loop accounts and the counterparties that moved the most money;
// the rest are counted per account they deal with, and "Show all" brings them back. Small rings are drawn whole.
const RING_FOCUS_KEEP=20;
let ringFocus=true;
const ringFocusCache=new WeakMap();
function focusGraph(graph) {
  if(graph.nodes.length<=RING_BIG) return graph;
  if(ringFocusCache.has(graph)) return ringFocusCache.get(graph);
  const keep=new Set(graph.nodes.filter(node=>node.role!=='counterparty').map(node=>node.id));
  // Counterparties shared by two or more hubs are what tie the hubs into one ring, so they always stay (up to RING_FOCUS_KEEP).
  const role=new Map(graph.nodes.map(node=>[node.id,node.role])), hubsOf=new Map();
  for(const link of graph.links) for(const [mine,other] of [[link.source,link.target],[link.target,link.source]])
    if(role.get(other)==='hub'&&role.get(mine)==='counterparty') {if(!hubsOf.has(mine)) hubsOf.set(mine,new Set());hubsOf.get(mine).add(other);}
  const money=node=>node.in_usd+node.out_usd;
  graph.nodes.filter(node=>(hubsOf.get(node.id)?.size||0)>=2).sort((a,b)=>money(b)-money(a)).slice(0,RING_FOCUS_KEEP).forEach(node=>keep.add(node.id));
  graph.nodes.filter(node=>node.role==='counterparty'&&!keep.has(node.id)).sort((a,b)=>(b.in_usd+b.out_usd)-(a.in_usd+a.out_usd)).slice(0,RING_FOCUS_KEEP).forEach(node=>keep.add(node.id));
  const unseen=new Map();
  for(const link of graph.links) for(const [mine,other] of [[link.source,link.target],[link.target,link.source]])
    if(keep.has(mine)&&!keep.has(other)) {if(!unseen.has(mine)) unseen.set(mine,new Set());unseen.get(mine).add(other);}
  const focused={...graph,nodes:graph.nodes.filter(node=>keep.has(node.id)),links:graph.links.filter(link=>keep.has(link.source)&&keep.has(link.target)),
    hidden:Object.fromEntries([...unseen].map(([id,set])=>[id,set.size])),focusOf:graph.nodes.length};
  ringFocusCache.set(graph,focused);return focused;
}
// Rings with several hubs are drawn as a constellation of stars; other rings up to RING_BIG accounts keep the
// original layout, and bigger ones get room to breathe.
const ringHubs=graph=>graph.nodes.filter(node=>node.role==='hub').length;
function layoutRing(graph) {return ringHubs(graph)>=2?layoutRingConstellation(graph):graph.nodes.length>RING_BIG?layoutRingSpread(graph):layoutRingClassic(graph);}
function layoutRingSpread(graph) {
  const {width,height,margin}=GRAPH, n=graph.nodes.length, cx=width/2, cy=height/2, usable=height-2*margin;
  const roles=flowRoles(graph), hasHub=graph.nodes.some(node=>node.role==='hub');
  const side=graph.nodes.map(node=>hasHub?({sender:-1,receiver:1}[roles[node.id]]||0):0);
  const isHub=graph.nodes.map(node=>hasHub&&node.role==='hub');
  const index=new Map(graph.nodes.map((node,i)=>[node.id,i]));
  // Hub band: as many staggered columns as the hubs need at RING_GAP.hub apart.
  const hubIds=graph.nodes.map((_,i)=>i).filter(i=>isHub[i]), hubRows=Math.max(1,Math.floor(usable/RING_GAP.hub));
  const hubCols=Math.min(3,Math.max(1,Math.ceil(hubIds.length/hubRows))), hubBand=(hubCols-1)*34/2;
  const hubSlot=new Map(hubIds.map((i,k)=>[i,{col:k%hubCols,row:Math.floor(k/hubCols),rows:Math.ceil(hubIds.length/hubCols)}]));
  // Side lanes: senders (left) and receivers (right) fill extra columns inwards when one column would overflow.
  const sideIds={'-1':[],'1':[]};graph.nodes.forEach((_,i)=>{if(side[i]) sideIds[side[i]].push(i);});
  const laneRows=Math.max(1,Math.floor(usable/RING_GAP.side)), lane=new Map();
  for(const s of [-1,1]) {
    const ids=sideIds[s], lanes=Math.max(1,Math.ceil(ids.length/laneRows)), outer=(width/2-margin)*.86, inner=hubBand+90;
    const step=lanes>1?Math.min(64,(outer-inner)/(lanes-1)):0, rows=Math.ceil(ids.length/lanes);
    ids.forEach((i,k)=>lane.set(i,{x:cx+s*(outer-(k%lanes)*step),row:Math.floor(k/lanes),rows}));
  }
  const counts={'0':0}, centre=graph.nodes.filter((_,i)=>!side[i]&&!isHub[i]).length;
  const pos=graph.nodes.map((node,i)=>{
    if(isHub[i]) {const h=hubSlot.get(i);return {x:cx+(h.col-(hubCols-1)/2)*34,y:cy+(h.row-(h.rows-1)/2)*Math.min(RING_GAP.hub*hubCols,usable*.7/Math.max(h.rows,1))};}
    if(side[i]) {const l=lane.get(i);return {x:l.x,y:margin+(l.row+.5)/l.rows*usable};}
    const k=counts['0']++, angle=k*2.399963, radius=(hasHub?.3:.6)*Math.sqrt((k+.5)/Math.max(centre,1));
    return {x:cx+Math.cos(angle)*radius*(width/2-margin),y:cy+Math.sin(angle)*radius*(height/2-margin)};
  });
  const target=graph.nodes.map((_,i)=>isHub[i]?pos[i].x:side[i]?lane.get(i).x:cx);
  const edges=graph.links.map(link=>[index.get(link.source),index.get(link.target)]).filter(([a,b])=>a!=null&&b!=null);
  // Hub rings are wide fans; loop rings need more spacing between their tightly linked accounts.
  const k=Math.sqrt((width-2*margin)*usable/Math.max(n,1))*(hasHub?.7:.92);
  const clampX=v=>Math.min(width-margin,Math.max(margin,v)), clampY=v=>Math.min(height-margin,Math.max(margin,v));
  let temperature=width/8;
  for(let step=0;step<340;step++) {
    const move=pos.map(()=>({x:0,y:0}));
    for(let a=0;a<n;a++) for(let b=a+1;b<n;b++) {
      let dx=pos[a].x-pos[b].x, dy=pos[a].y-pos[b].y, d=Math.hypot(dx,dy)||.01;
      const force=k*k/d; dx/=d; dy/=d;
      move[a].x+=dx*force;move[a].y+=dy*force;move[b].x-=dx*force;move[b].y-=dy*force;
    }
    for(const [a,b] of edges) {
      let dx=pos[a].x-pos[b].x, dy=pos[a].y-pos[b].y, d=Math.hypot(dx,dy)||.01;
      const force=d*d/k; dx/=d; dy/=d;
      move[a].x-=dx*force;move[a].y-=dy*force;move[b].x+=dx*force;move[b].y+=dy*force;
    }
    for(let i=0;i<n;i++) {
      // Hubs keep to their band column, senders and receivers to their lane; the rest float.
      const pinned=side[i]!==0||isHub[i];
      move[i].x+=(target[i]-pos[i].x)*(pinned?.45:.06)*k/10;move[i].y+=(cy-pos[i].y)*.03*k/10;
      // A soft wall: accounts near the top or bottom edge are pushed back in, so they never flatten into a row on the border.
      const pad=44, top=margin+pad-pos[i].y, bottom=pos[i].y-(height-margin-pad);
      if(top>0) move[i].y+=top*.6*k/10;if(bottom>0) move[i].y-=bottom*.6*k/10;
      const length=Math.hypot(move[i].x,move[i].y)||1, limited=Math.min(length,temperature);
      pos[i].x=clampX(pos[i].x+move[i].x/length*limited);pos[i].y=clampY(pos[i].y+move[i].y/length*limited);
      if(isHub[i]) pos[i].x=target[i];
    }
    temperature*=.985;
  }
  // Within each hub column and side lane, keep accounts in their settled order but at least a gap apart,
  // spread over the full height if needed, so a column can't bunch up against an edge.
  const columns=new Map();graph.nodes.forEach((_,i)=>{if(isHub[i]||side[i]) {const key=Math.round(target[i]);if(!columns.has(key)) columns.set(key,[]);columns.get(key).push(i);}});
  for(const ids of columns.values()) {
    if(ids.length<2) continue;
    ids.sort((a,b)=>pos[a].y-pos[b].y);
    const gap=Math.min(isHub[ids[0]]?RING_GAP.hub:RING_GAP.side-4,usable/(ids.length-1)), lo=margin, hi=height-margin;
    for(let j=1;j<ids.length;j++) pos[ids[j]].y=Math.max(pos[ids[j]].y,pos[ids[j-1]].y+gap);
    if(pos[ids.at(-1)].y>hi) {pos[ids.at(-1)].y=hi;for(let j=ids.length-2;j>=0;j--) pos[ids[j]].y=Math.min(pos[ids[j]].y,pos[ids[j+1]].y-gap);}
    if(pos[ids[0]].y<lo) {const shift=lo-pos[ids[0]].y;ids.forEach(i=>{pos[i].y+=shift;});}
  }
  // Collision pass: no two dots closer than their sizes plus a gap.
  const room=graph.nodes.map((node,i)=>isHub[i]?17:node.role==='loop'?11:9);
  for(let pass=0;pass<80;pass++) {
    let moved=false;
    for(let a=0;a<n;a++) for(let b=a+1;b<n;b++) {
      const need=room[a]+room[b]+4;let dx=pos[b].x-pos[a].x, dy=pos[b].y-pos[a].y, d=Math.hypot(dx,dy);
      if(d>=need) continue;
      if(d<.01) {dx=(a%2?1:-1)*.7;dy=.7;d=1;}
      const push=(need-d)/2, ux=dx/d, uy=dy/d;moved=true;
      // Hubs and lane accounts hold their column and slide along it; free accounts move both ways.
      const ax=isHub[a]||side[a]?0:1, bx=isHub[b]||side[b]?0:1;
      pos[a].x=clampX(pos[a].x-ux*push*ax);pos[a].y=clampY(pos[a].y-uy*push);
      pos[b].x=clampX(pos[b].x+ux*push*bx);pos[b].y=clampY(pos[b].y+uy*push);
    }
    if(!moved) break;
  }
  return Object.fromEntries(graph.nodes.map((node,i)=>[node.id,pos[i]]));
}

// Rings with two or more hubs: each hub is drawn as its own small star. Hubs sit on an oval, each with a fan
// of the accounts only it deals with pointing outwards; loop accounts and accounts shared by several hubs sit
// inside the oval, near the hubs they touch. A one-hub ring already reads as a star, so it keeps its layout.
function layoutRingConstellation(graph) {
  const {width,height,margin}=GRAPH, cx=width/2, cy=height/2, n=graph.nodes.length;
  const index=new Map(graph.nodes.map((node,i)=>[node.id,i])), roles=flowRoles(graph);
  const isHub=graph.nodes.map(node=>node.role==='hub'), nbr=graph.nodes.map(()=>new Set());
  for(const link of graph.links) {const a=index.get(link.source), b=index.get(link.target);if(a==null||b==null||a===b) continue;nbr[a].add(b);nbr[b].add(a);}
  const hubs=graph.nodes.map((_,i)=>i).filter(i=>isHub[i]), money=i=>graph.nodes[i].in_usd+graph.nodes[i].out_usd;
  // Hub order round the oval: the biggest hub first, then each time the hub sharing the most with the last one.
  const shared=(a,b)=>{let s=nbr[a].has(b)?2:0;for(const x of nbr[a]) if(nbr[b].has(x)) s++;return s;};
  const order=[hubs.slice().sort((a,b)=>money(b)-money(a))[0]], left=new Set(hubs);left.delete(order[0]);
  while(left.size) {const last=order.at(-1);let best=null, score=-1;for(const h of left) {const s=shared(last,h);if(s>score) {score=s;best=h;}}order.push(best);left.delete(best);}
  // Each counterparty that deals with exactly one hub joins that hub's fan.
  const fans=new Map(hubs.map(h=>[h,[]])), inFan=new Array(n).fill(false);
  graph.nodes.forEach((node,i)=>{if(node.role!=='counterparty') return;const own=[...nbr[i]].filter(j=>isHub[j]);if(own.length===1) {fans.get(own[0]).push(i);inFan[i]=true;}});
  const H=hubs.length, span=H===2?Math.PI*1.15:Math.max(Math.PI*.3,Math.min(Math.PI*.85,Math.PI*2/H*1.5));
  // Two hubs get two big fans side by side, like a one-hub ring drawn twice; more hubs get small fans.
  const GAP=22, minR=H===2?Math.min(150,height/2-margin-12):40, maxR=H===2?minR+40:90;
  const fanShape=count=>{for(let rows=1;;rows++) {const per=Math.ceil(count/rows), r0=Math.min(maxR,Math.max(minR,per*GAP/span));if(per<=Math.floor(span*r0/GAP)+1) return {rows,r0};}};
  const depth=Math.max(...[...fans.values()].map(f=>{const {rows,r0}=fanShape(f.length);return r0+(rows-1)*GAP;}),40)+12;
  // Oval size: as big as the canvas allows once the deepest fan fits outside it.
  const ry=Math.max(70,height/2-margin-depth), rx=Math.max(110,Math.min(width/2-margin-depth,ry*1.9));
  const pos=new Array(n), fixed=new Array(n).fill(false), between=n-H-[...fans.values()].reduce((s,f)=>s+f.length,0);
  order.forEach((h,k)=>{const angle=H===2?(k?0:Math.PI):-Math.PI/2+k/H*Math.PI*2;
    pos[h]={x:cx+Math.cos(angle)*(H===2?Math.max(90+14*Math.sqrt(between),width/2-margin-depth):rx),y:cy+Math.sin(angle)*(H===2?0:ry)};fixed[h]=true;
    // Fan: senders, then two-way accounts, then receivers, spread over an arc pointing away from the middle.
    const rank={sender:0,both:1,receiver:2}, members=fans.get(h).sort((a,b)=>rank[roles[graph.nodes[a].id]]-rank[roles[graph.nodes[b].id]]||money(b)-money(a));
    const out=Math.atan2(pos[h].y-cy,pos[h].x-cx), {rows,r0}=fanShape(members.length);
    for(let row=0;row<rows;row++) {
      const mine=members.filter((_,j)=>j%rows===row), m=mine.length, radius=r0+row*GAP;
      mine.forEach((i,q)=>{const a=out-span/2+span*(q+.5+(row%2?.25:0)*(m>1?1:0))/Math.max(m,1);pos[i]={x:pos[h].x+Math.cos(a)*radius,y:pos[h].y+Math.sin(a)*radius};fixed[i]=true;});
    }
  });
  // Everything else starts near the middle of the hubs it touches, pulled towards the centre.
  const target=new Array(n);let spiral=0;
  graph.nodes.forEach((_,i)=>{if(fixed[i]) return;const near=[...nbr[i]].filter(j=>isHub[j]);
    const mx=near.length?near.reduce((s,j)=>s+pos[j].x,0)/near.length:cx, my=near.length?near.reduce((s,j)=>s+pos[j].y,0)/near.length:cy;
    target[i]={x:cx+(mx-cx)*.5,y:cy+(my-cy)*.5};const k=spiral++, a=k*2.399963, d=8*Math.sqrt(k+1);pos[i]={x:target[i].x+Math.cos(a)*d,y:target[i].y+Math.sin(a)*d};});
  const room=graph.nodes.map((node,i)=>isHub[i]?17:node.role==='loop'?11:9);
  const clampX=v=>Math.min(width-margin,Math.max(margin,v)), clampY=v=>Math.min(height-margin,Math.max(margin,v));
  for(let pass=0;pass<160;pass++) {
    let moved=false;
    for(let i=0;i<n;i++) if(!fixed[i]) {pos[i].x+=(target[i].x-pos[i].x)*.02;pos[i].y+=(target[i].y-pos[i].y)*.02;}
    for(let a=0;a<n;a++) for(let b=a+1;b<n;b++) {
      if(fixed[a]&&fixed[b]) continue;
      const need=room[a]+room[b]+4;let dx=pos[b].x-pos[a].x, dy=pos[b].y-pos[a].y, d=Math.hypot(dx,dy);
      if(d>=need) continue;if(d<.01) {dx=(a%2?1:-1)*.7;dy=.7;d=1;}
      const ux=dx/d, uy=dy/d, push=(need-d)*(fixed[a]||fixed[b]?1:.5);moved=true;
      if(!fixed[a]) {pos[a].x=clampX(pos[a].x-ux*push);pos[a].y=clampY(pos[a].y-uy*push);}
      if(!fixed[b]) {pos[b].x=clampX(pos[b].x+ux*push);pos[b].y=clampY(pos[b].y+uy*push);}
    }
    if(!moved&&pass>40) break;
  }
  // Fill the canvas (up to 1.7x), so the explorer doesn't have to zoom in, and never spill off it.
  const xs=pos.map(p=>p.x), ys=pos.map(p=>p.y), s=Math.min(1.7,(width/2-margin)/Math.max(cx-Math.min(...xs),Math.max(...xs)-cx,1),(height/2-margin)/Math.max(cy-Math.min(...ys),Math.max(...ys)-cy,1));
  return Object.fromEntries(graph.nodes.map((node,i)=>[node.id,{x:cx+(pos[i].x-cx)*s,y:cy+(pos[i].y-cy)*s}]));
}

function layoutRingClassic(graph) {
  const {width,height,margin}=GRAPH, n=graph.nodes.length, cx=width/2, cy=height/2;
  const roles=flowRoles(graph), hasHub=graph.nodes.some(node=>node.role==='hub');
  const side=graph.nodes.map(node=>hasHub?({sender:-1,receiver:1}[roles[node.id]]||0):0);
  const index=new Map(graph.nodes.map((node,i)=>[node.id,i]));
  const counts={'-1':0,'0':0,'1':0}, totals={'-1':side.filter(v=>v===-1).length,'0':side.filter(v=>v===0).length,'1':side.filter(v=>v===1).length};
  const pos=graph.nodes.map((node,i)=>{
    const s=side[i], k=counts[s]++, total=Math.max(totals[s],1);
    if(s) {const angle=(k+.5)/total*Math.PI-Math.PI/2;return {x:cx+s*Math.cos(angle)*(width/2-margin)*.85,y:cy+Math.sin(angle)*(height/2-margin)};}
    const angle=k*2.399963, radius=node.role==='hub'?.12:hasHub?.3:.6;
    return {x:cx+Math.cos(angle)*radius*(width/2-margin),y:cy+Math.sin(angle)*radius*(height/2-margin)};
  });
  const edges=graph.links.map(link=>[index.get(link.source),index.get(link.target)]).filter(([a,b])=>a!=null&&b!=null);
  // Hub rings are wide fans; loop rings need more spacing between their tightly linked accounts.
  const k=Math.sqrt((width-2*margin)*(height-2*margin)/Math.max(n,1))*(hasHub?.7:.92);
  let temperature=width/8;
  for(let step=0;step<340;step++) {
    const move=pos.map(()=>({x:0,y:0}));
    for(let a=0;a<n;a++) for(let b=a+1;b<n;b++) {
      let dx=pos[a].x-pos[b].x, dy=pos[a].y-pos[b].y, d=Math.hypot(dx,dy)||.01;
      const force=k*k/d; dx/=d; dy/=d;
      move[a].x+=dx*force;move[a].y+=dy*force;move[b].x-=dx*force;move[b].y-=dy*force;
    }
    for(const [a,b] of edges) {
      let dx=pos[a].x-pos[b].x, dy=pos[a].y-pos[b].y, d=Math.hypot(dx,dy)||.01;
      const force=d*d/k; dx/=d; dy/=d;
      move[a].x-=dx*force;move[a].y-=dy*force;move[b].x+=dx*force;move[b].y+=dy*force;
    }
    for(let i=0;i<n;i++) {
      // Hubs are pinned to the centre column; senders and receivers to their own side.
      const pinned=side[i]!==0||(hasHub&&graph.nodes[i].role==='hub'), targetX=cx+side[i]*(width/2-margin)*.82;
      move[i].x+=(targetX-pos[i].x)*(pinned?.45:.06)*k/10;move[i].y+=(cy-pos[i].y)*.03*k/10;
      const length=Math.hypot(move[i].x,move[i].y)||1, limited=Math.min(length,temperature);
      pos[i].x=Math.min(width-margin,Math.max(margin,pos[i].x+move[i].x/length*limited));
      pos[i].y=Math.min(height-margin,Math.max(margin,pos[i].y+move[i].y/length*limited));
      // Hubs stay on the centre column so money visibly enters on the left and leaves on the right.
      if(hasHub&&graph.nodes[i].role==='hub') pos[i].x=cx;
    }
    temperature*=.985;
  }
  return Object.fromEntries(graph.nodes.map((node,i)=>[node.id,pos[i]]));
}

// ---- Live ring graph: settling physics, flowing payments, zoom/pan and a hover card ----
// layoutRing gives each account its rest position. The live layer moves accounts
// around that rest position: they spread out from the centre on load, pull their
// linked accounts along while dragged and spring back afterwards. Hovering only
// highlights and enlarges the account's dot; nothing moves under the pointer. Dots travel along each link in the direction the money
// moved. Without requestAnimationFrame, or with reduced motion, the graph is
// drawn once at rest with direction arrows.
const RING_GLOW={hub:'#ff6f5e',loop:'#b794ff',sender:'#4fa8ff',receiver:'#ffb648',both:'#a9bccb'};
const RING_TONE={in:'#4fa8ff',out:'#ffb648',other:'#a9bccb',loop:'#c3a6ff'};
let ringScene=null, ringFlowPaused=false, ringVisible=true;
const motionAllowed = () => typeof requestAnimationFrame==='function' && !window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
const quadPoint = (a,c,b,t) => ({x:(1-t)*(1-t)*a.x+2*(1-t)*t*c.x+t*t*b.x,y:(1-t)*(1-t)*a.y+2*(1-t)*t*c.y+t*t*b.y});
const xy = p => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`;

function renderRing() {
  const full=snapshot.network.graphs[selectedRing];
  if(!full) return;
  // Focus view unless the selected account is one it leaves out (then the whole ring is drawn).
  const focused=ringFocus?focusGraph(full):full;
  const graph=focused!==full&&selectedAccount&&!focused.nodes.some(node=>node.id===selectedAccount)?full:focused, key=selectedRing+(graph===full?'':'|focus');
  GRAPH=graphShape();
  if(ringLayoutShape[key]!==GRAPH.width) {delete ringLayouts[key];ringLayoutShape[key]=GRAPH.width;}
  const layout=ringLayouts[key]||(ringLayouts[key]=layoutRing(graph));
  ringFocusButton(full,graph);
  const roles=flowRoles(graph), hasHub=graph.nodes.some(node=>node.role==='hub'), {width,height,margin}=GRAPH, cx=width/2, cy=height/2;
  const byId=new Map(graph.nodes.map(node=>[node.id,node]));
  const maxTxns=Math.max(1,...graph.links.map(link=>link.txns)), maxUsd=Math.max(1,...graph.nodes.map(node=>node.in_usd+node.out_usd)), maxLinkUsd=Math.max(1,...graph.links.map(link=>link.usd));
  const radius=Object.fromEntries(graph.nodes.map(node=>[node.id,node.role==='counterparty'?ROLE[roles[node.id]].radius+4*Math.sqrt((node.in_usd+node.out_usd)/maxUsd):ROLE[node.role].radius]));
  const tone=link=>link.loop?'loop':roles[link.source]==='sender'?'in':roles[link.target]==='receiver'?'out':'other';
  const pairs=new Set(graph.links.map(link=>link.source+'\n'+link.target));
  const adjacent=Object.fromEntries(graph.nodes.map(node=>[node.id,new Set()]));
  for(const link of graph.links) if(adjacent[link.source]&&adjacent[link.target]) {adjacent[link.source].add(link.target);adjacent[link.target].add(link.source);}
  const animate=motionAllowed(), previous=ringScene;
  stopRingScene();
  // Re-rendering the same ring keeps positions and zoom; a new ring spreads out from the centre.
  const same=previous&&previous.ring===selectedRing&&previous.width===width;
  const pos=Object.fromEntries(graph.nodes.map(node=>{const p=layout[node.id], old=same&&previous.pos[node.id];
    return [node.id,old?{...old}:animate?{x:cx+(p.x-cx)*.35,y:cy+(p.y-cy)*.35,vx:0,vy:0}:{x:p.x,y:p.y,vx:0,vy:0}];}));
  const dots=[];
  graph.links.forEach((link,i)=>{const count=1+Math.round(2*Math.log1p(link.txns)/Math.log1p(maxTxns));
    for(let k=0;k<count;k++) dots.push({link:i,u:(k/count+i*.618)%1,r:1.7+2.6*Math.sqrt(link.usd/maxLinkUsd)});});
  const scene=ringScene={ring:selectedRing,width,height,graph,layout,pos,roles,byId,radius,animate,dots,
    ids:graph.nodes.map(node=>node.id),adjacent:Object.fromEntries(Object.entries(adjacent).map(([id,set])=>[id,[...set]])),
    reverse:graph.links.map(link=>pairs.has(link.target+'\n'+link.source)),scale:Object.fromEntries(graph.nodes.map(node=>[node.id,1])),
    chord:ringHubs(graph)>=2?{x:cx,y:cy}:null,view:same?previous.view:null,hover:null,hoverLink:null,drag:null,gesture:null,settled:!animate,raf:null,last:0,geometry:[]};

  const gradients=graph.links.map((link,i)=>`<linearGradient id="ring-grad-${i}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="${RING_GLOW[roles[link.source]]}"/><stop offset="1" stop-color="${RING_GLOW[roles[link.target]]}"/></linearGradient>`).join('');
  const marker=name=>`<marker id="ring-arrow-${name}" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto"><path d="M1 1 L9 5 L1 9" fill="none" stroke="${RING_TONE[name]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></marker>`;
  const linkMarkup=graph.links.map((link,i)=>`<path data-link="${i}" class="ring-link ring-link-${tone(link)}" stroke="url(#ring-grad-${i})" stroke-width="${(1.2+3*Math.log1p(link.txns)/Math.log1p(maxTxns)).toFixed(2)}" marker-mid="url(#ring-arrow-${tone(link)})"/>`).join('');
  const hitMarkup=graph.links.map((link,i)=>`<path data-hit="${i}" class="ring-hit"/>`).join('');
  const dotMarkup=dots.map((dot,i)=>`<circle data-dot="${i}" class="ring-dot ring-dot-${tone(graph.links[dot.link])}" r="${dot.r.toFixed(1)}" fill="${RING_TONE[tone(graph.links[dot.link])]}"/>`).join('');
  const hubRank=new Map(graph.nodes.filter(node=>node.role==='hub').map((node,i)=>[node.id,i]));
  const nodeMarkup=graph.nodes.map(node=>{const role=roles[node.id], r=radius[node.id], minor=node.role==='counterparty'||(hubRank.get(node.id)??0)>=(graph.focusOf||graph.nodes.length>RING_BIG?1:3);
    return `<g class="ring-node ring-node-${role}${node.id===selectedAccount?' ring-node-selected':''}" data-node="${escapeText(node.id)}" tabindex="0" role="button" aria-label="${escapeText(ROLE[role].label)} ${escapeText(node.id)}, ${integer(node.fan_in)} senders, ${integer(node.fan_out)} receivers">${node.role==='hub'?`<circle class="ring-halo" r="${(r+3).toFixed(1)}"/>`:''}<circle class="ring-grab" r="${(r+7).toFixed(1)}"/><circle class="ring-core" r="${r.toFixed(1)}" fill="${RING_GLOW[role]}" filter="url(#ring-glow)"/><text class="ring-label${minor?' ring-label-minor':''}" y="${(node.role==='hub'?r+17:-r-7).toFixed(1)}" text-anchor="middle">${escapeText(shortAccount(node.id))}</text></g>`;}).join('');
  const lanes=hasHub&&ringHubs(graph)<2?`<text class="ring-lane" x="${margin-10}" y="${height-16}">← MONEY IN</text><text class="ring-lane" x="${width-margin+10}" y="${height-16}" text-anchor="end">MONEY OUT →</text>`:'';
  const loops=graph.nodes.filter(node=>node.role==='loop').length, busy=graph.nodes.length>RING_BIG||graph.links.length>80;
  // Many hubs trading with each other make a dense mesh: those links fade back until an account is hovered.
  const hubLinks=graph.links.filter(link=>byId.get(link.source)?.role==='hub'&&byId.get(link.target)?.role==='hub').length, mesh=scene.chord&&hubLinks>15, crowded=loops>8||scene.chord&&loops>4;
  element('ring-graph').innerHTML=`<svg id="ring-svg" class="${[crowded&&'ring-crowded',busy&&'ring-busy',mesh&&'ring-mesh',!animate&&'ring-static',ringFlowPaused&&'ring-paused'].filter(Boolean).join(' ')}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Money flows between ${graph.shown_accounts} accounts of the selected ring"><defs><filter id="ring-glow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="3.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>${gradients}${['in','out','other','loop'].map(marker).join('')}</defs><rect class="ring-backdrop" width="${width}" height="${height}"/>${lanes}<g id="ring-viewport"><g class="ring-links">${linkMarkup}</g><g class="ring-hits">${hitMarkup}</g><g class="ring-dots" filter="url(#ring-glow)">${dotMarkup}</g><g class="ring-accounts">${nodeMarkup}</g></g></svg>`;
  element('ring-graph-note').textContent=`${graph.focusOf?`Focus view: ${integer(graph.nodes.length)} of ${integer(graph.total_accounts??graph.focusOf)} accounts: every hub and loop account, the accounts shared between hubs, and the ${RING_FOCUS_KEEP} other counterparties that moved the most money. Show all draws ${integer(graph.focusOf)}. Hover a hub for its name. `:''}${graph.truncated&&!graph.focusOf?`Showing ${graph.shown_accounts} of ${graph.total_accounts} accounts: every hub and loop account, then counterparties by payment volume. `:''}${animate?'Moving dots are payments, travelling the way the money went; more dots means more payments, bigger dots more USD.':'Arrows show the direction of the money.'} Larger accounts moved more money. ${crowded?'Loop-account names appear on hover. ':''}Hover to trace an account, ${animate?'drag one to pull on its links':'drag to rearrange'}, drag the background to pan.`;
  element('ring-flow').hidden=!animate;element('ring-legend-flow').hidden=!animate;
  bindRingScene(scene);
  if(selectedAccount&&byId.has(selectedAccount)) showAccount(selectedAccount);
  else element('ring-node').innerHTML='<h3>Account details</h3><p>Select an account in the graph.</p>';
}

// The Focus / Show all toggle sits in the graph's HUD and only appears for big rings.
function ringFocusButton(full,graph) {
  let button=element('ring-focus');
  if(!button) {const hud=element('ring-fit')?.parentElement;if(!hud?.insertAdjacentHTML) return;
    hud.insertAdjacentHTML('afterbegin','<button id="ring-focus" type="button" aria-pressed="true"></button>');button=element('ring-focus');
    button.onclick=()=>{ringFocus=!ringFocus;renderRing();};}
  button.hidden=full.nodes.length<=RING_BIG;button.setAttribute('aria-pressed',String(graph!==full));
  button.textContent=graph!==full?`Show all ${integer(full.nodes.length)}`:'Focus view';
  button.title=graph!==full?'Draw every account in this ring':'Draw hubs, loop accounts and the counterparties that moved the most money';
}
function stopRingScene() {
  if(ringScene?.raf!=null&&typeof cancelAnimationFrame==='function') cancelAnimationFrame(ringScene.raf);
  if(ringScene) ringScene.raf=null;
  hideRingTip();
}

// Caches the drawn elements, wires up pointer, keyboard and wheel input, then starts the animation.
function bindRingScene(scene) {
  const svg=element('ring-svg');
  if(!svg?.querySelector) return;
  const all=selector=>Array.from(svg.querySelectorAll(selector));
  scene.svg=svg;scene.viewport=svg.querySelector('#ring-viewport');
  scene.nodeEls=Object.fromEntries(all('[data-node]').map(mark=>[mark.dataset.node,mark]));
  scene.linkEls=all('[data-link]');scene.hitEls=all('[data-hit]');scene.dotEls=all('[data-dot]');
  scene.gradEls=scene.graph.links.map((link,i)=>svg.querySelector(`#ring-grad-${i}`));
  scene.dotShapes=Object.fromEntries(Object.entries(scene.nodeEls).map(([id,mark])=>[id,[mark.querySelector('.ring-core'),mark.querySelector('.ring-halo')]]));
  if(!scene.view) fitRing(scene,false); else applyRingView(scene);
  drawRingNodes(scene);drawRingDots(scene,0);

  const toViewport=event=>{const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;return point.matrixTransform(scene.viewport.getScreenCTM().inverse());};
  const toSvg=event=>{const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;return point.matrixTransform(svg.getScreenCTM().inverse());};
  const activate=id=>{selectedAccount=id;showAccount(id);Object.entries(scene.nodeEls).forEach(([other,mark])=>mark.classList.toggle('ring-node-selected',other===id));};
  svg.onpointerdown=event=>{
    if(event.button>0) return;
    cancelRingTween(scene);
    const mark=event.target.closest?.('[data-node]');
    scene.gesture={id:mark?.dataset.node||null,x:event.clientX,y:event.clientY,start:toSvg(event),view:{...scene.view},moved:false};
    svg.setPointerCapture?.(event.pointerId);
  };
  svg.onpointermove=event=>{
    const gesture=scene.gesture;
    if(!gesture) {hoverFromEvent(scene,event);return;}
    if(!gesture.moved&&Math.hypot(event.clientX-gesture.x,event.clientY-gesture.y)>3) {
      gesture.moved=true;hideRingTip();
      if(gesture.id) {scene.drag=gesture.id;setRingHover(scene,gesture.id);svg.classList.add('ring-dragging');}
      else svg.classList.add('ring-panning');
    }
    if(!gesture.moved) return;
    if(gesture.id) {
      const local=toViewport(event), p=scene.pos[gesture.id], {width,height}=scene;
      p.x=Math.min(width-12,Math.max(12,local.x));p.y=Math.min(height-12,Math.max(12,local.y));p.vx=p.vy=0;
      if(!scene.animate) {scene.layout[gesture.id]={x:p.x,y:p.y};drawRingNodes(scene);}
      wakeRing(scene);
    } else {
      const now=toSvg(event);
      scene.view={...gesture.view,x:gesture.view.x+now.x-gesture.start.x,y:gesture.view.y+now.y-gesture.start.y};
      applyRingView(scene);
    }
  };
  svg.onpointerup=svg.onpointercancel=event=>{
    const gesture=scene.gesture;scene.gesture=null;
    svg.classList.remove('ring-dragging','ring-panning');
    if(!gesture) return;
    // Let go and the account springs back into place, like Obsidian's graph (it stays put when motion is off).
    if(gesture.id&&gesture.moved) {scene.drag=null;scene.settled=false;wakeRing(scene);hoverFromEvent(scene,event);}
    else if(gesture.id&&event.type==='pointerup') activate(gesture.id);
  };
  svg.onpointerleave=()=>{if(!scene.gesture) {setRingHover(scene,null);hideRingTip();}};
  // Pinch on a trackpad, or Ctrl/⌘ + scroll, zooms; plain scrolling keeps scrolling the page.
  svg.onwheel=event=>{if(!(event.ctrlKey||event.metaKey)) return;event.preventDefault();zoomRing(Math.exp(-event.deltaY*.01),toSvg(event));};
  Object.entries(scene.nodeEls).forEach(([id,mark])=>{
    mark.onfocus=()=>{setRingHover(scene,id);showRingTip(scene,{node:id},mark.getBoundingClientRect());};
    mark.onblur=()=>{setRingHover(scene,null);hideRingTip();};
    mark.onkeydown=event=>{if(event.key==='Enter'||event.key===' ') {event.preventDefault();activate(id);}};
  });
  wakeRing(scene);
}

function hoverFromEvent(scene,event) {
  const mark=event.target.closest?.('[data-node]'), hit=event.target.closest?.('[data-hit]');
  if(mark) {setRingHover(scene,mark.dataset.node);showRingTip(scene,{node:mark.dataset.node},event);}
  else if(hit) {setRingHover(scene,null,Number(hit.dataset.hit));showRingTip(scene,{link:Number(hit.dataset.hit)},event);}
  else {setRingHover(scene,null);hideRingTip();}
}

// Highlights an account and its direct links (or one link), and fades everything else.
function setRingHover(scene,id,linkIndex=null) {
  if(scene.hover===id&&scene.hoverLink===linkIndex) return;
  scene.hover=id;scene.hoverLink=linkIndex;scene.settled=false;
  const svg=scene.svg, links=scene.graph.links, active=id!=null||linkIndex!=null;
  const lit=new Set(id!=null?[id,...scene.adjacent[id]]:linkIndex!=null?[links[linkIndex].source,links[linkIndex].target]:[]);
  scene.lit=lit;
  const litLink=i=>id!=null?links[i].source===id||links[i].target===id:i===linkIndex;
  svg.classList.toggle('ring-dim',active);
  Object.entries(scene.nodeEls).forEach(([node,mark])=>mark.classList.toggle('ring-hl',lit.has(node)));
  scene.linkEls.forEach((path,i)=>path.classList.toggle('ring-hl',active&&litLink(i)));
  scene.dotEls.forEach((dot,i)=>dot.classList.toggle('ring-hl',active&&litLink(scene.dots[i].link)));
  wakeRing(scene);
}

function wakeRing(scene) {
  if(!scene.animate||scene!==ringScene||scene.raf!=null||!ringVisible) return;
  scene.last=0;scene.raf=requestAnimationFrame(time=>ringFrame(scene,time));
}

function ringFrame(scene,time) {
  scene.raf=null;
  if(scene!==ringScene||!ringVisible) return;
  const dt=scene.last?Math.min(time-scene.last,50):16.7;scene.last=time;
  if(!scene.settled) {scene.settled=stepRing(scene,dt)<.03*scene.ids.length&&!scene.drag;drawRingNodes(scene);}
  if(!ringFlowPaused) drawRingDots(scene,dt);
  if(!scene.settled||!ringFlowPaused) scene.raf=requestAnimationFrame(next=>ringFrame(scene,next));
}

// One physics step. Each account springs toward its rest position, shifted by
// part of its neighbours' average displacement (so a dragged account tows its
// links). Averaging keeps the coupling stable however many links a hub has.
function stepRing(scene,dt) {
  const f=Math.min(dt/16.7,2.5), {pos,layout,adjacent,hover,drag}=scene, damping=Math.pow(.74,f);
  const shift=Object.fromEntries(scene.ids.map(id=>[id,{x:pos[id].x-layout[id].x,y:pos[id].y-layout[id].y}]));
  let energy=0;
  for(const id of scene.ids) {
    const p=pos[id], near=adjacent[id];
    const target=scene.scale[id]+(((id===hover&&!drag)?1.35:1)-scene.scale[id])*Math.min(1,.25*f);
    energy+=Math.abs(target-scene.scale[id])*20;scene.scale[id]=target;
    if(id===drag) continue;
    let tx=layout[id].x, ty=layout[id].y;
    if(near.length) {let sx=0, sy=0;for(const other of near) {sx+=shift[other].x;sy+=shift[other].y;}tx+=.45*sx/near.length;ty+=.45*sy/near.length;}
    p.vx=(p.vx+(tx-p.x)*.07*f)*damping;p.vy=(p.vy+(ty-p.y)*.07*f)*damping;
    p.x+=p.vx*f;p.y+=p.vy*f;
    energy+=Math.abs(p.vx)+Math.abs(p.vy)+.05*(Math.abs(tx-p.x)+Math.abs(ty-p.y));
  }
  return energy;
}

// Curved link from source to target. Links that have a reverse partner bend
// further so A → B and B → A never draw on top of each other.
function ringGeometry(scene,i) {
  const link=scene.graph.links[i], a=scene.pos[link.source], b=scene.pos[link.target];
  const dx=b.x-a.x, dy=b.y-a.y, bend=scene.reverse[i]?.2:.07;
  // In a many-hub ring, hub-to-hub links bow in towards the middle like a chord diagram instead of criss-crossing.
  const pull=scene.chord&&scene.byId.get(link.source)?.role==='hub'&&scene.byId.get(link.target)?.role==='hub'?.6:0;
  const mx=(a.x+b.x)/2, my=(a.y+b.y)/2, c=pull?{x:mx+(scene.chord.x-mx)*pull+dy*bend*.5,y:my+(scene.chord.y-my)*pull-dx*bend*.5}:{x:mx+dy*bend,y:my-dx*bend};
  const trim=(p,r)=>{const ex=c.x-p.x, ey=c.y-p.y, e=Math.hypot(ex,ey)||1;return {x:p.x+ex/e*r,y:p.y+ey/e*r};};
  const from=trim(a,scene.radius[link.source]*scene.scale[link.source]+1), to=trim(b,scene.radius[link.target]*scene.scale[link.target]+2);
  return {from,c,to,length:Math.max(20,Math.hypot(dx,dy))};
}

function drawRingNodes(scene) {
  if(!scene.nodeEls) return;
  // Only the dot grows on hover. The hit circle keeps its size, so the hovered
  // account can't swell under the pointer and steal or drop the hover.
  for(const id of scene.ids) {
    const p=scene.pos[id], r=scene.radius[id]*scene.scale[id], [core,halo]=scene.dotShapes?.[id]||[];
    scene.nodeEls[id]?.setAttribute('transform',`translate(${xy(p).replace(' ',',')})`);
    core?.setAttribute('r',r.toFixed(1));halo?.setAttribute('r',(r+3).toFixed(1));
  }
  scene.graph.links.forEach((link,i)=>{
    const g=scene.geometry[i]=ringGeometry(scene,i), mid=quadPoint(g.from,g.c,g.to,.5);
    const left={x:(g.from.x+g.c.x)/2,y:(g.from.y+g.c.y)/2}, right={x:(g.c.x+g.to.x)/2,y:(g.c.y+g.to.y)/2};
    // Split at the midpoint so the direction arrow (marker-mid) sits halfway along the curve.
    const d=`M${xy(g.from)}Q${xy(left)} ${xy(mid)}Q${xy(right)} ${xy(g.to)}`;
    scene.linkEls[i]?.setAttribute('d',d);scene.hitEls[i]?.setAttribute('d',d);
    const grad=scene.gradEls[i];
    if(grad) {grad.setAttribute('x1',g.from.x.toFixed(1));grad.setAttribute('y1',g.from.y.toFixed(1));grad.setAttribute('x2',g.to.x.toFixed(1));grad.setAttribute('y2',g.to.y.toFixed(1));}
  });
}

// Payment dots move at a steady on-screen speed and swell mid-link, so each reads as a pulse of money.
function drawRingDots(scene,dt) {
  if(!scene.dotEls?.length||!scene.animate) return;
  scene.dots.forEach((dot,i)=>{
    const g=scene.geometry[dot.link];
    if(!g) return;
    dot.u=(dot.u+dt/1000*85/g.length)%1;
    const p=quadPoint(g.from,g.c,g.to,dot.u), el=scene.dotEls[i];
    el.setAttribute('cx',p.x.toFixed(1));el.setAttribute('cy',p.y.toFixed(1));
    el.setAttribute('r',(dot.r*(.35+.65*Math.sin(Math.PI*dot.u))).toFixed(2));
  });
}

// ---- Zoom, pan and fit ----
function applyRingView(scene) {
  const {k,x,y}=scene.view;
  scene.viewport?.setAttribute('transform',`translate(${x.toFixed(1)},${y.toFixed(1)}) scale(${k.toFixed(3)})`);
  scene.svg?.classList.toggle('ring-zoomed',k>=1.45);
}

function fitRing(scene,animated=true) {
  const points=scene.ids.map(id=>scene.layout[id]), pad=40;
  const minX=Math.min(...points.map(p=>p.x))-pad, maxX=Math.max(...points.map(p=>p.x))+pad;
  const minY=Math.min(...points.map(p=>p.y))-pad, maxY=Math.max(...points.map(p=>p.y))+pad;
  const k=Math.min(1.6,Math.max(.6,Math.min(scene.width/(maxX-minX),scene.height/(maxY-minY))));
  const target={k,x:scene.width/2-k*(minX+maxX)/2,y:scene.height/2-k*(minY+maxY)/2};
  if(!animated||!scene.animate||!scene.view) {scene.view=target;applyRingView(scene);return;}
  tweenRingView(scene,target);
}

function zoomRing(factor,around) {
  const scene=ringScene;
  if(!scene?.view) return;
  // Repeated clicks build on where the running zoom is heading, not where it currently is.
  const {k,x,y}=scene.tweenTarget||scene.view;
  if(around) cancelRingTween(scene);
  const next=Math.min(4,Math.max(.5,k*factor)), at=around||{x:scene.width/2,y:scene.height/2};
  const target={k:next,x:at.x-(at.x-x)*next/k,y:at.y-(at.y-y)*next/k};
  if(around) {scene.view=target;applyRingView(scene);} else tweenRingView(scene,target);
}

function tweenRingView(scene,target) {
  if(!scene.animate) {scene.view=target;applyRingView(scene);return;}
  const from={...scene.view}, start=performance.now(), token=scene.tween=(scene.tween||0)+1;
  scene.tweenTarget=target;
  const tick=time=>{
    if(scene.tween!==token||scene!==ringScene) return;
    const t=Math.min(1,(time-start)/320), e=1-Math.pow(1-t,3);
    scene.view={k:from.k+(target.k-from.k)*e,x:from.x+(target.x-from.x)*e,y:from.y+(target.y-from.y)*e};
    applyRingView(scene);
    if(t<1) requestAnimationFrame(tick); else scene.tweenTarget=null;
  };
  requestAnimationFrame(tick);
}

function cancelRingTween(scene) {scene.tween=(scene.tween||0)+1;scene.tweenTarget=null;}

// ---- Hover card ----
function ringTipContent(scene,target) {
  if(target.node!=null) {
    const node=scene.byId.get(target.node), role=scene.roles[target.node], links=scene.graph.links;
    const inRing=links.filter(link=>link.target===target.node).length, outRing=links.filter(link=>link.source===target.node).length;
    return `<span class="tip-role" style="--role:${RING_GLOW[role]}">${escapeText(ROLE[role].label)}</span><strong class="tip-title">${escapeText(node.id)}</strong><div class="tip-grid"><span><small>Received</small>${escapeText(dollars(node.in_usd))}<em>${escapeText(plural(node.fan_in,'sender'))}</em></span><span><small>Sent</small>${escapeText(dollars(node.out_usd))}<em>${escapeText(plural(node.fan_out,'receiver'))}</em></span></div><small class="tip-foot">In this ring: ${inRing} in · ${outRing} out · click for details</small>`;
  }
  const link=scene.graph.links[target.link], role=scene.roles;
  const story=link.loop?'Part of a timed loop':role[link.target]==='hub'?'Money into the hub':role[link.source]==='hub'?'Money out of the hub':'Payments between ring accounts';
  return `<span class="tip-role" style="--role:${link.loop?RING_TONE.loop:RING_GLOW[role[link.source]]}">${escapeText(story)}</span><strong class="tip-title">${escapeText(shortAccount(link.source))} → ${escapeText(shortAccount(link.target))}</strong><div class="tip-grid"><span><small>Payments</small>${escapeText(integer(link.txns))}</span><span><small>USD</small>${escapeText(dollars(link.usd))}</span></div>`;
}

// Node cards anchor to the account; link cards follow the pointer. The card takes
// whichever side (above, below, right, left) covers the fewest highlighted accounts.
function showRingTip(scene,target,at) {
  const tip=element('ring-tip'), stage=tip?.parentElement;
  if(!tip||!stage?.getBoundingClientRect||scene.gesture?.moved) return;
  const key=target.node!=null?'n'+target.node:'l'+target.link;
  if(tip.dataset.key===key&&target.node!=null&&!tip.hidden) return;
  if(tip.dataset.key!==key) {tip.innerHTML=ringTipContent(scene,target);tip.dataset.key=key;}
  tip.hidden=false;
  const box=stage.getBoundingClientRect(), w=tip.offsetWidth||220, h=tip.offsetHeight||90;
  const centre=id=>{const r=scene.nodeEls[id]?.querySelector('.ring-core')?.getBoundingClientRect();return r&&{x:r.left+r.width/2-box.left,y:r.top+r.height/2-box.top,r:r.height/2};};
  const anchor=target.node!=null?centre(target.node):null;
  const ax=anchor?anchor.x:at.clientX-box.left, ay=anchor?anchor.y:at.clientY-box.top, gap=anchor?anchor.r+28:18;
  const clamp=([x,y])=>[Math.min(Math.max(8,x),box.width-w-8),Math.min(Math.max(8,y),box.height-h-8)];
  const options=[[ax-w/2,ay-gap-h],[ax-w/2,ay+gap],[ax+gap,ay-h/2],[ax-gap-w,ay-h/2]].map(clamp);
  const lit=[...(scene.lit||[])].map(centre).filter(Boolean);
  // Each highlighted account counts with its name label (about 90px wide, above the dot).
  const covers=([x,y])=>lit.filter(p=>p.x+45>x&&p.x-45<x+w&&p.y+p.r+4>y&&p.y-p.r-22<y+h).length+(ax>x-8&&ax<x+w+8&&ay>y-8&&ay<y+h+8?100:0);
  const [left,top]=options.reduce((best,option)=>covers(option)<covers(best)?option:best);
  tip.style.left=`${left}px`;tip.style.top=`${top}px`;
}

function hideRingTip() {const tip=element('ring-tip');if(tip) {tip.hidden=true;if(tip.dataset) tip.dataset.key='';}}

function toggleRingFlow() {
  ringFlowPaused=!ringFlowPaused;
  const button=element('ring-flow');
  button.textContent=ringFlowPaused?'Play flow':'Pause flow';button.setAttribute('aria-pressed',String(ringFlowPaused));
  ringScene?.svg?.classList.toggle('ring-paused',ringFlowPaused);
  if(ringScene) wakeRing(ringScene);
}

function showAccount(id) {
  const graph=snapshot.network.graphs[selectedRing], node=graph.nodes.find(item=>item.id===id);
  if(!node) return;
  const role=flowRoles(graph)[id];
  const out=graph.links.filter(link=>link.source===id).sort((a,b)=>b.usd-a.usd), inn=graph.links.filter(link=>link.target===id).sort((a,b)=>b.usd-a.usd);
  const list=(links,key,arrow)=>links.length?`<ul class="ring-flows">${links.slice(0,6).map(link=>`<li><span title="${escapeText(link[key])}">${arrow} ${escapeText(link[key])}</span><span>${plural(link.txns,'payment')} · ${dollars(link.usd)}</span></li>`).join('')}${links.length>6?`<li class="more">…and ${links.length-6} more in this ring</li>`:''}</ul>`:'<p class="field-help">None in this ring.</p>';
  const action={hub:'Collects from many accounts and pays many others. Restricting it cuts the most flows in this ring.',loop:'Part of a timed loop: money it sends comes back within seven days.',
    sender:'Pays money into the ring.',receiver:'Receives money paid out of the ring.',both:'Both pays into and receives from the ring.'}[role];
  element('ring-node').innerHTML=`<span class="role-chip" style="background:${ROLE[role].color}">${escapeText(ROLE[role].label)}</span><h3 class="account-id">${escapeText(node.id)}</h3><p>${escapeText(action)}</p><div class="ring-facts"><span><small>Distinct senders</small>${integer(node.fan_in)}</span><span><small>Distinct receivers</small>${integer(node.fan_out)}</span><span><small>Received</small>${dollars(node.in_usd)}</span><span><small>Sent</small>${dollars(node.out_usd)}</span></div><h4>Money in, within this ring</h4>${list(inn,'source','←')}<h4>Money out, within this ring</h4>${list(out,'target','→')}<p class="field-help">Sender, receiver and USD figures cover the whole dataset.</p>`;
}

// ---- BQ2 presenter view: a full-screen, keyboard-driven story for a class or briefing ----
// Seven steps: every account as a dot -> the fan-in/fan-out candidates -> the
// rings -> replay of one ring's real payments in time order -> reveal of which
// accounts touched laundering -> base rate versus ring rates -> all drawn rings.
// Data comes only from snapshot.network (schema 2: payments, loops and
// touches_laundering on each drawn ring graph). Labels are shown only after a
// ring is found and never change detection or ranking.
const PRES_STEPS=7, PRES_SCENE_START=[0,3,4,6];
// Above PRES_LIVE_DOTS highlighted dots, the haystack stops drawing one glow per dot per frame and paints a cached layer.
// Above PRES_MAX_SWEEP fan-in/fan-out candidates, step 1 uses the ring (headline) threshold so the sweep stays readable.
const PRES_LIVE_DOTS=6000, PRES_MAX_SWEEP=20000;
// A closed club: three or more hubs that nearly all pay one another. Structure only; labels are never read.
function presClub(graph) {
  const hubs=new Set(graph.nodes.filter(node=>node.role==='hub').map(node=>node.id));if(hubs.size<3) return false;
  const pairs=new Set(graph.links.filter(link=>link.source!==link.target&&hubs.has(link.source)&&hubs.has(link.target)).map(link=>`${link.source}>${link.target}`));
  return pairs.size>=.9*hubs.size*(hubs.size-1);
}
const PRES_COLOR={...RING_GLOW,dirty:'#ff2e55',clean:'#3d5468'};
const PRES_ROLE_NAME={hub:'Hub',loop:'Loop account',sender:'Sender',receiver:'Receiver',both:'Sends and receives'};
const PRES={open:false,step:0,data:null,ring:null,raf:null,last:0,W:0,H:0,DPR:1,hay:null,clusters:[],layouts:{},
  anim:{bg:1,cand:0,gather:0,zoom:0,graph:0,wall:0,dim:0},replay:null,reveal:{on:false,at:0,order:[],dur:0},wallAt:0,wallCells:[],wallHover:-1,lastCount:-1,returnFocus:null};

// Whether a release carries what the presenter needs (older releases do not).
function presenterAvailable(net) {
  return Boolean(net?.ring_list?.length&&net.graphs?.[net.ring_list[0].ring_id]?.payments);
}

// snapshot.network -> compact rings: nodes [id, display role, touched 0/1],
// links [from, to, payments, usd], pays [link, minute, usd, laundering 0/1], tri [a, b, c].
function presenterData(net) {
  const rings=net.ring_list.filter(row=>net.graphs[row.ring_id]?.payments).map(row=>{
    // Big rings replay in focus view: payments on links to left-out accounts are dropped and the rest re-indexed.
    const whole=net.graphs[row.ring_id], graph=focusGraph(whole), roles=flowRoles(graph), index=new Map(graph.nodes.map((node,i)=>[node.id,i]));
    const linkAt=new Map(graph.links.map((link,i)=>[link,i])), remap=whole.links.map(link=>linkAt.get(link)??-1);
    const pays=graph===whole?whole.payments:whole.payments.filter(pay=>remap[pay[0]]>=0).map(pay=>[remap[pay[0]],...pay.slice(1)]);
    return {rank:row.rank,id:row.ring_id,hubs:row.hubs,loops:row.loops,usd:row.usd,rate:row.laundering_rate??0,graph,club:presClub(whole),
      start:Date.parse(String(whole.payment_start)+'Z'),total:whole.payments_total??whole.payments.length,
      focus:graph!==whole,accounts:whole.total_accounts??whole.nodes.length,
      nodes:graph.nodes.map(node=>[node.id,roles[node.id],node.touches_laundering?1:0]),
      links:graph.links.map(link=>[index.get(link.source),index.get(link.target),link.txns,link.usd]),
      pays,tri:(whole.loops||[]).map(loop=>loop.map(id=>index.get(id))).filter(loop=>loop.every(i=>i!=null))};
  });
  const head=net.thresholds.find(row=>row.headline)||net.thresholds[0], count=row=>row.hubs.accounts+row.high_volume.accounts;
  let low=net.thresholds.reduce((a,b)=>a.threshold<=b.threshold?a:b);if(count(low)>PRES_MAX_SWEEP&&count(head)<count(low)) low=head;
  // Accounts-weighted laundering share of the drawn closed-club rings versus the other drawn rings (checked after detection).
  const share=list=>{const accounts=list.reduce((sum,ring)=>sum+ring.accounts,0);return {rings:list.length,accounts,rate:accounts?list.reduce((sum,ring)=>sum+ring.rate*ring.accounts,0)/accounts:null};};
  const clubs=rings.some(ring=>ring.club)?{club:share(rings.filter(ring=>ring.club)),open:share(rings.filter(ring=>!ring.club))}:null;
  // The replay leads with the top-ranked ring, unless that is a closed club: then with the first ring that is not.
  const lead=rings[0]?.club?rings.find(ring=>!ring.club)||rings[0]:rings[0];
  return {rings,lead,clubs,totals:net.totals,summary:net.rings,low,head,candidates:count(low),
    sizes:net.ring_sizes||net.ring_list.map(row=>row.accounts),loopDays:net.parameters.loop_window_days,headline:net.parameters.headline_threshold};
}

function presRandom(seed) {return ()=>{seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return ((t^t>>>14)>>>0)/4294967296;};}
const presClamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const presEase=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;
const presUsd=n=>n>=1e6?`$${(n/1e6).toFixed(n>=1e7?1:2)}M`:n>=1e3?`$${(n/1e3).toFixed(n>=1e5?0:1)}K`:`$${Math.round(n)}`;
const presPct=x=>x==null?'n/a':`${(x*100).toFixed(x<.1&&x>0?1:0)}%`;
function presRgba(hex,a) {const n=parseInt(hex.slice(1),16);return `rgba(${n>>16},${n>>8&255},${n&255},${a})`;}

// Earliest moment each drawn A->B->C->A loop completes, in order, within the loop window.
function presLoopCloses(ring,windowDays=7) {
  const byLink=new Map(ring.links.map((link,i)=>[`${link[0]}>${link[1]}`,i])), at=ring.links.map(()=>[]);
  ring.pays.forEach(pay=>at[pay[0]].push(pay[1]));
  const window=windowDays*1440;
  return ring.tri.map(([a,b,c])=>{
    const ab=at[byLink.get(`${a}>${b}`)]||[], bc=at[byLink.get(`${b}>${c}`)]||[], ca=at[byLink.get(`${c}>${a}`)]||[];
    let best=null;
    for(const t1 of ab) for(const t2 of bc) {if(t2<t1) continue;for(const t3 of ca) if(t3>=t2&&t3-t1<=window&&(!best||t3<best.t)) best={t:t3,days:(t3-t1)/1440,nodes:[a,b,c]};}
    return best;
  });
}

// Replay clock in minutes after the ring's first payment.
function presSetRing(ring) {
  const pays=ring.pays, t0=pays.length?pays[0][1]:0, t1=pays.length?pays[pays.length-1][1]:0, span=Math.max(t1-t0,60);
  PRES.ring=ring;
  PRES.replay={t:Infinity,t0,t1,span,mps:span/presClamp(9+pays.length*.075,11,26),speed:1,playing:false,next:0,recv:[],lastLink:[],moved:0,paid:0,
    loops:presLoopCloses(ring,PRES.data?.loopDays||7),loopShown:new Set(),flash:[],loopFlash:null};
  presSeek(t0-span*.03);
}
const presFlight=()=>PRES.replay.mps*PRES.replay.speed*.9;
const presEnd=()=>PRES.replay.t1+presFlight()+1;
function presSeek(t) {
  const r=PRES.replay, ring=PRES.ring;
  if(t<r.t) {r.next=0;r.recv=ring.nodes.map(()=>0);r.lastLink=ring.links.map(()=>-1e9);r.moved=0;r.paid=0;r.loopShown=new Set();r.flash=[];}
  r.t=t;const flight=presFlight(), now=typeof performance==='object'?performance.now():0;
  while(r.next<ring.pays.length&&ring.pays[r.next][1]+flight<=t) {
    const pay=ring.pays[r.next], link=ring.links[pay[0]];
    r.recv[link[1]]+=pay[2];r.moved+=pay[2];r.paid++;r.lastLink[pay[0]]=pay[1]+flight;r.next++;r.flash.push({node:link[1],at:now});
  }
  if(r.flash.length>60) r.flash.splice(0,r.flash.length-60);
  r.loops.forEach((loop,k)=>{if(loop&&loop.t+flight<=t&&!r.loopShown.has(k)) {r.loopShown.add(k);if(r.playing) presLoopToast(loop);}});
}
let presToastTimer=0;
function presLoopToast(loop) {
  const tail=i=>escapeText(PRES.ring.nodes[i][0].split(':')[1]?.slice(-4)||PRES.ring.nodes[i][0]), el=element('pres-toast');
  el.innerHTML=`Loop closed: …${tail(loop.nodes[0])} → …${tail(loop.nodes[1])} → …${tail(loop.nodes[2])} → back in <b>${loop.days.toFixed(1)} days</b>`;
  el.hidden=false;clearTimeout(presToastTimer);presToastTimer=setTimeout(()=>{el.hidden=true;},2600);
  PRES.replay.loopFlash={nodes:loop.nodes,at:performance.now()};
}
function presSetPlaying(on) {
  const r=PRES.replay;if(on&&r.t>=presEnd()-1) presSeek(r.t0-r.span*.03);
  r.playing=on;const play=element('pres-play');play.textContent=on?'❚❚':'▶';play.setAttribute('aria-label',on?'Pause':'Play');
}

function presReveal(on) {
  const n=PRES.ring.nodes.length, order=Array.from({length:n},(_,i)=>i), random=presRandom(PRES.ring.rank*7);
  for(let i=n-1;i>0;i--) {const j=Math.floor(random()*(i+1));[order[i],order[j]]=[order[j],order[i]];}
  PRES.reveal={on,at:performance.now()-(motionAllowed()?0:1e5),order:order.reduce((acc,node,k)=>(acc[node]=k,acc),[]),dur:Math.min(1600,70*n)};
  PRES.lastCount=-1;
}
function presRevealP(i) {const v=PRES.reveal;if(!v.on) return 0;return presClamp((performance.now()-v.at-v.order[i]/PRES.ring.nodes.length*v.dur)/380);}

// Story text for the current step (also used by tests).
function presStoryHTML(step=PRES.step,data=PRES.data,ring=PRES.ring,revealed=0) {
  const t=data.totals, s=data.summary, n=ring.nodes.length, dirty=ring.nodes.filter(node=>node[2]).length;
  const skipped=ring===data.lead&&data.lead!==data.rings[0]?data.rings.indexOf(ring):0, times=x=>`${x.toFixed(x<10?1:0)}×`;
  switch(step) {
    case 0:return `<div class="pres-eyebrow">This release · ${integer(t.rows)} payments</div><h2>${integer(t.accounts)} accounts. A small share of them are working together.</h2><p>Every dot is one real account. Where it sits means nothing yet. The job: find the ones that move money as a team.</p>`;
    case 1:return `<div class="pres-eyebrow">Step 1 · fan-in and fan-out</div><h2><em>${integer(data.candidates)}</em> accounts collect from ${data.low.threshold}+ senders and pay ${data.low.threshold}+ receivers.</h2><p>Counted over ${integer(t.links)} account-to-account links. Pooling money from many and passing it on to many is what a mule does.</p>`;
    case 2:return `<div class="pres-eyebrow">Step 2 · hubs, loops, rings</div><h2>${integer(s.accounts)} accounts fall into <em>${integer(s.rings)} rings</em>.</h2><p>Hubs with ${data.headline}+ senders and ${data.headline}+ receivers, timed loops (A → B → C → A within ${data.loopDays} days), and the accounts they deal with. Each cluster is one case to review. Ring #1 sits in the middle.</p>`;
    case 3:return `<div class="pres-eyebrow">Ring #${ring.rank} · ${skipped?'the top-ranked open ring':`lead account ${escapeText(ring.nodes[0]?.[0]||ring.id)}`}</div><h2>${ring.hubs?`${ring.hubs===1?'One hub':`${ring.hubs} hubs`}, ${integer((ring.accounts??n)-ring.hubs)} other accounts, <em>${presUsd(ring.usd)}</em> moved.`:`${integer(n)} accounts passing money in <em>${plural(ring.loops,'timed loop')}</em>.`}</h2><p>${integer(ring.pays.length)}${ring.total>ring.pays.length?` of ${integer(ring.total)}`:''} real payments, replayed in the order they happened.${ring.focus?` Shown in focus: ${integer(n)} of ${integer(ring.accounts)} accounts, every hub and loop account, the accounts shared between hubs and the ones that moved the most money.`:''} ${ring.hubs===1?'Senders sit on the left and receivers on the right. Watch the hub collect, then pay out.':ring.hubs?'Each hub is its own star: accounts only it deals with fan out around it, shared accounts sit in the middle.':`Each purple flash is a loop closing: money leaves an account and comes back within ${data.loopDays} days.`}${skipped?` The ${plural(skipped,'ring')} ranked above it ${skipped===1?'is a closed club':'are closed clubs'}: every hub pays every other hub. The case list at the end shows them.`:ring.club?' Every hub here pays every other hub: a closed club.':''}</p>`;
    case 4:return dirty?`<div class="pres-eyebrow">Ring #${ring.rank} · ground truth</div><h2><span class="pres-dirty" id="pres-count">${revealed}</span> of ${integer(n)}${ring.focus?' shown':''} accounts touched a laundering payment.</h2><p>The detector never saw these labels. They come with the dataset and are only shown after the ring was found.</p>`
      :`<div class="pres-eyebrow">Ring #${ring.rank} · ground truth</div><h2><span id="pres-count">${revealed}</span> of ${integer(n)}${ring.focus?' shown':''} accounts touched a laundering payment.</h2><p>${ring.club?'Every hub pays every other hub, a payment or two each, spread over months: a closed club rather than a funnel. ':''}Dense structure is not proof. Payroll, payment processors and escrow accounts look like this too, so an analyst still makes the call.</p>`;
    case 5: {
      const h=data.head.hubs, hubLift=t.laundering_rate?h.laundering_rate/t.laundering_rate:null;
      return `<div class="pres-eyebrow">Does it hold up?</div><h2>${presPct(t.laundering_rate)} of all accounts touch laundering. Inside the rings it's <span class="pres-dirty">${presPct(s.laundering_rate)}</span>; among hubs, <span class="pres-dirty">${presPct(h.laundering_rate)}</span>.</h2><p>That is ${s.lift==null?'well above':times(s.lift)} and ${hubLift==null?'far above':times(hubLift)} the base rate, found without using a single label.</p>`;
    }
    default:{
      const c=data.clubs;
      if(!c) return `<div class="pres-eyebrow">The case list</div><h2>The top ${integer(data.rings.length)} rings, ranked.</h2><p>Red accounts touched a laundering payment. Click any ring to replay it.</p>`;
      const pct=x=>`${Math.round(x*100)}%`;
      return `<div class="pres-eyebrow">The top ${integer(data.rings.length)} rings · two shapes</div><h2>Open rings: <span class="pres-dirty">${pct(c.open.rate)}</span> laundering. Closed clubs: ${pct(c.club.rate)}.</h2><p>Share of accounts that touched a laundering payment. Faded tiles are the ${integer(c.club.rings)} closed clubs, where every hub pays every other hub. We spotted this split only after checking the labels, so it is the next rule to test, not a result. Click any ring to replay it.</p>`;
    }
  }
}

function openPresenter() {
  const net=snapshot?.network;if(!presenterAvailable(net)) return;
  PRES.data=presenterData(net);PRES.layouts={};PRES.hay=null;PRES.open=true;PRES.returnFocus=document.activeElement;
  const pick=element('pres-ring');selectOptions('pres-ring',PRES.data.rings.map(ring=>String(ring.rank)),rank=>{const ring=PRES.data.rings.find(item=>String(item.rank)===rank);return `#${ring.rank} · ${integer(ring.accounts??ring.nodes.length)} accounts · ${ring.hubs?plural(ring.hubs,'hub'):plural(ring.loops,'loop')} · ${presUsd(ring.usd)}`;});
  pick.onchange=()=>presChooseRing(+pick.value);
  const {rings,lead}=PRES.data, loopRing=rings.find(ring=>ring.loops&&!ring.hubs), clean=rings.find(ring=>ring.rate===0);
  const quick=lead!==rings[0]?[[lead,'top open'],[rings[0],'top club'],[loopRing,'loops']]:[[rings[0],'biggest'],[loopRing,'loops'],[clean,'clean']];
  element('pres-quick').innerHTML=quick.filter(([ring],i,all)=>ring&&all.findIndex(([other])=>other===ring)===i)
    .map(([ring,label])=>`<button type="button" data-rank="${ring.rank}">#${ring.rank} ${label}</button>`).join('');
  element('pres-quick').onclick=event=>{const button=event.target.closest('[data-rank]');if(button) presChooseRing(+button.dataset.rank);};
  presSetRing(lead);pick.value=String(lead.rank);
  element('presenter').hidden=false;document.body?.classList?.add('pres-lock');
  presResize();presGo(0,true);
  element('pres-next').focus?.();
  if(!PRES.raf&&typeof requestAnimationFrame==='function') PRES.raf=requestAnimationFrame(presFrame);
}
function closePresenter() {
  PRES.open=false;element('presenter').hidden=true;document.body?.classList?.remove('pres-lock');
  if(PRES.replay) PRES.replay.playing=false;
  if(PRES.raf&&typeof cancelAnimationFrame==='function') cancelAnimationFrame(PRES.raf);PRES.raf=null;
  if(document.fullscreenElement) document.exitFullscreen?.().catch?.(()=>{});
  PRES.returnFocus?.focus?.();
}
function presChooseRing(rank) {
  presSetRing(PRES.data.rings.find(ring=>ring.rank===rank));element('pres-ring').value=String(rank);
  if(PRES.step===4||PRES.step===5) {presSeek(presEnd());presReveal(true);} else {PRES.reveal.on=false;presSetPlaying(motionAllowed());}
  presUpdateText();
}

function presGo(n,force=false) {
  n=presClamp(n,0,PRES_STEPS-1);if(n===PRES.step&&!force) return;
  const previous=PRES.step;PRES.step=n;
  const replayish=n>=3&&n<=5;
  element('pres-readout').hidden=!replayish||(n===5&&PRES.W<820);element('pres-timeline').hidden=!replayish;
  element('pres-verdict').hidden=n!==5;element('pres-toast').hidden=true;
  if(n===3) {if(PRES.reveal.on) PRES.reveal.on=false;if(previous!==3||force) {presSeek(PRES.replay.t0-PRES.replay.span*.03);presSetPlaying(motionAllowed()&&!force);}}
  if(n===4) {presSetPlaying(false);presSeek(presEnd());if(!PRES.reveal.on||previous!==5) presReveal(true);}
  if(n===5) {if(!PRES.reveal.on) {presSeek(presEnd());presReveal(true);}presVerdict();}
  if(n===6) {PRES.wallAt=performance.now();presSetPlaying(false);}
  if(n<3) presSetPlaying(false);
  presUpdateText();
  element('pres-back').disabled=n===0;element('pres-next').disabled=n===PRES_STEPS-1;element('pres-next').textContent=n===PRES_STEPS-1?'End':'Next →';
  element('pres-steps').innerHTML=Array.from({length:PRES_STEPS},(_,i)=>`<i class="${i<=n?'on':''}"></i>`).join('');
  const scene=n<3?0:n===3?3:n<6?4:6;
  element('presenter').querySelectorAll?.('[data-pres-step]').forEach(button=>button.setAttribute('aria-current',+button.dataset.presStep===scene?'step':'false'));
}
function presUpdateText() {
  element('pres-story').innerHTML=`<div class="pres-fade">${presStoryHTML()}</div>`;PRES.lastCount=-1;
  presLegend();presMeters();presTimeline();
}
function presMeters() {
  const r=PRES.replay, ring=PRES.ring;
  element('pres-pays').textContent=`${integer(r.paid)} / ${integer(ring.pays.length)}`;element('pres-usd').textContent=presUsd(r.moved);
  if(PRES.step===4||PRES.step===5) return;
  element('pres-third-label').textContent=ring.tri.length?'Timed loops closed':'Accounts paid so far';
  element('pres-third').textContent=ring.tri.length?`${r.loopShown.size} / ${ring.tri.length}`:`${r.recv.filter(v=>v>0).length} / ${ring.nodes.length}`;
}
function presRevealCount() {
  const ring=PRES.ring, n=ring.nodes.reduce((sum,node,i)=>sum+(node[2]&&presRevealP(i)>=.5?1:0),0);
  if(n===PRES.lastCount) return;PRES.lastCount=n;
  element('pres-third-label').textContent='Touched a laundering payment';element('pres-third').textContent=`${n} of ${ring.nodes.length}`;
  const count=element('pres-count');if(count) count.textContent=n;
}
function presLegend() {
  const item=(color,label)=>`<span style="color:${color}"><i></i><span>${escapeText(label)}</span></span>`;
  element('pres-legend').innerHTML=PRES.reveal.on?item(PRES_COLOR.dirty,'Touched laundering')+item(PRES_COLOR.clean,'No laundering found')
    :['hub','loop','sender','receiver','both'].filter(role=>PRES.ring.nodes.some(node=>node[1]===role)).map(role=>item(PRES_COLOR[role],PRES_ROLE_NAME[role])).join('');
}
function presVerdict() {
  const d=PRES.data, t=d.totals, s=d.summary, h=d.head.hubs;
  const rows=[['All accounts',`${integer(t.laundering_accounts)} of ${integer(t.accounts)}`,t.laundering_rate,false],
    ['Accounts in rings',`${integer(s.laundering_accounts)} of ${integer(s.accounts)}`,s.laundering_rate,true],
    [`Hubs (${d.headline}+ in, ${d.headline}+ out)`,`${integer(h.laundering_accounts)} of ${integer(h.accounts)}`,h.laundering_rate,true]];
  element('pres-verdict').innerHTML=rows.map(([label,count,value,hot])=>`<div class="pres-vrow${hot?' hot':''}"><div class="pres-vlab">${escapeText(label)}<small>${escapeText(count)}</small></div><div class="pres-vtrack"><div class="pres-vfill" data-w="${(value||0)*100}"></div></div><strong>${presPct(value)}</strong></div>`).join('')
    +'<p>Share of accounts that touched at least one laundering payment. Labels were used only to check these results.</p>';
  const fill=()=>element('pres-verdict').querySelectorAll?.('.pres-vfill').forEach(el=>{el.style.width=`${el.dataset.w}%`;});
  typeof requestAnimationFrame==='function'?requestAnimationFrame(()=>requestAnimationFrame(fill)):fill();
}

// ---- drawing ----
function presResize() {
  const canvas=element('pres-canvas');if(!canvas?.getBoundingClientRect) return;
  const box=canvas.getBoundingClientRect();PRES.DPR=Math.min(window.devicePixelRatio||1,2);PRES.W=box.width;PRES.H=box.height;
  canvas.width=Math.round(box.width*PRES.DPR);canvas.height=Math.round(box.height*PRES.DPR);
  const tl=element('pres-tl'), tb=tl.getBoundingClientRect();tl.width=Math.round(tb.width*PRES.DPR);tl.height=Math.round(tb.height*PRES.DPR);
  presBuildHaystack();presTimeline();
}
function presDisc() {const {W,H}=PRES;return {cx:W*.56,cy:H*.52,rx:Math.min(W*.47,H*.78),ry:H*.44};}
function presBuildHaystack() {
  const {W,H,DPR}=PRES, data=PRES.data;if(!W||!data) return;
  if(!PRES.points||PRES.points.n!==data.totals.accounts) {
    // Every dot is one real account (positions are arbitrary); the highlighted sets have their real sizes.
    const n=data.totals.accounts, random=presRandom(459), px=new Float32Array(n), py=new Float32Array(n), pb=new Uint8Array(n);
    for(let i=0;i<n;i++) {const a=random()*Math.PI*2, r=Math.pow(random(),.62);px[i]=.5+Math.cos(a)*r*.5;py[i]=.5+Math.sin(a)*r*.5;pb[i]=40+random()*90|0;}
    const pick=(k,avoid)=>{const set=new Set();k=Math.min(k,n-(avoid?.size||0));while(set.size<k) {const i=random()*n|0;if(!avoid||!avoid.has(i)) set.add(i);}return [...set];};
    const cand=pick(data.candidates), ringDots=pick(data.summary.accounts,new Set(cand));
    PRES.points={n,px,py,pb,cand,ringDots};
  }
  const P=PRES.points, d=presDisc(), xy=i=>[d.cx+(P.px[i]-.5)*2*d.rx,d.cy+(P.py[i]-.5)*2*d.ry];PRES.hayXY=xy;
  const w=Math.round(W*DPR), h=Math.round(H*DPR), hay=document.createElement('canvas');hay.width=w;hay.height=h;
  const hc=hay.getContext('2d'), image=hc.createImageData(w,h), pixels=image.data;
  for(let i=0;i<P.n;i++) {
    const [x,y]=xy(i), X=x*DPR|0, Y=y*DPR|0;if(X<0||Y<0||X>=w||Y>=h) continue;
    const k=(Y*w+X)*4, b=P.pb[i];pixels[k]=Math.min(255,pixels[k]+b*.55);pixels[k+1]=Math.min(255,pixels[k+1]+b*.8);pixels[k+2]=Math.min(255,pixels[k+2]+b);pixels[k+3]=255;
  }
  hc.putImageData(image,0,0);PRES.hay=hay;PRES.gatherLayer=null;
  // Many candidates: paint them once; each frame then draws one image instead of one glow per account.
  PRES.candLayer=P.cand.length>PRES_LIVE_DOTS?presLayer(lc=>{lc.globalCompositeOperation='lighter';const glow=presGlow(PRES_COLOR.receiver);
    P.cand.forEach(i=>{const [x,y]=xy(i);lc.drawImage(glow,x-5,y-5,10,10);});lc.globalCompositeOperation='source-over';lc.fillStyle=PRES_COLOR.receiver;
    const dots=new Path2D();P.cand.forEach(i=>{const [x,y]=xy(i);dots.rect(x-1.1,y-1.1,2.2,2.2);});lc.fill(dots);}):null;
  // Rings as a sunflower in rank order, ring #1 in the centre.
  const sizes=data.sizes, golden=Math.PI*(3-Math.sqrt(5)), step=Math.min(d.ry*.96/Math.sqrt(sizes.length),d.rx*.96/Math.sqrt(sizes.length)/1.15), clusters=[];
  let at=0;
  sizes.forEach((size,k)=>{
    const rr=step*Math.sqrt(k+.35), cx=d.cx+Math.cos(k*golden)*rr*1.15, cy=d.cy+Math.sin(k*golden)*rr, spread=step*.085*Math.sqrt(size);
    for(let j=0;j<size&&at<P.ringDots.length;j++) {const q=Math.sqrt((j+.5)/size)*spread;clusters[at++]=[cx+Math.cos(j*golden)*q,cy+Math.sin(j*golden)*q];}
  });
  PRES.clusters=clusters;PRES.clusterCenter=[d.cx,d.cy];
}
// An offscreen canvas the size of the stage, drawn in CSS pixels.
function presLayer(draw) {
  const {W,H,DPR}=PRES, layer=document.createElement('canvas');layer.width=Math.round(W*DPR);layer.height=Math.round(H*DPR);
  const lc=layer.getContext('2d');lc.setTransform(DPR,0,0,DPR,0,0);draw(lc);return layer;
}
// Many ring accounts: one path per colour, with glows on ring #1 and an even sample of about 1,500 others.
function presRingDots(ctx,count,first,at) {
  const stride=Math.ceil(count/1500), hub=new Path2D(), rest=new Path2D(), glowHub=presGlow(PRES_COLOR.hub), glowRest=presGlow('#9fd6ff');
  ctx.save();ctx.globalCompositeOperation='lighter';
  for(let k=0;k<count;k++) {const p=at(k);if(!p) continue;const [x,y]=p;(k<first?hub:rest).rect(x-1.25,y-1.25,2.5,2.5);
    if(k<first||k%stride===0) ctx.drawImage(k<first?glowHub:glowRest,x-4.5,y-4.5,9,9);}
  ctx.restore();ctx.fillStyle='#9fd6ff';ctx.fill(rest);ctx.fillStyle=PRES_COLOR.hub;ctx.fill(hub);
}
const presSprites=new Map();
function presGlow(color) {
  if(presSprites.has(color)) return presSprites.get(color);
  const c=document.createElement('canvas'), s=64;c.width=c.height=s;const g=c.getContext('2d'), grad=g.createRadialGradient(s/2,s/2,0,s/2,s/2,s/2);
  grad.addColorStop(0,presRgba(color,.9));grad.addColorStop(.25,presRgba(color,.35));grad.addColorStop(1,presRgba(color,0));
  g.fillStyle=grad;g.fillRect(0,0,s,s);presSprites.set(color,c);return c;
}
function presDot(ctx,x,y,r,color,glow) {
  if(glow) {const mode=ctx.globalCompositeOperation;ctx.globalCompositeOperation='lighter';ctx.drawImage(presGlow(color),x-glow,y-glow,glow*2,glow*2);ctx.globalCompositeOperation=mode;}
  ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();
}
const presCurve=(a,b,bend=.14)=>[(a[0]+b[0])/2-(b[1]-a[1])*bend,(a[1]+b[1])/2+(b[0]-a[0])*bend];
// Hub-to-hub links in a many-hub ring bow towards the hubs' centre, as in the explorer.
function presHubCentre(ring,P) {
  if(!(ring.hubs>=2)) return null;let x=0, y=0, k=0;
  ring.nodes.forEach((node,i)=>{if(node[1]==='hub') {x+=P[i][0];y+=P[i][1];k++;}});return k>1?[x/k,y/k]:null;
}
function presLinkCurve(ring,link,a,b,centre) {
  if(!centre||ring.nodes[link[0]][1]!=='hub'||ring.nodes[link[1]][1]!=='hub') return presCurve(a,b);
  const mx=(a[0]+b[0])/2, my=(a[1]+b[1])/2;return [mx+(centre[0]-mx)*.6-(b[1]-a[1])*.05,my+(centre[1]-my)*.6+(b[0]-a[0])*.05];
}
const presQuad=(a,c,b,u)=>{const v=1-u;return [v*v*a[0]+2*v*u*c[0]+u*u*b[0],v*v*a[1]+2*v*u*c[1]+u*u*b[1]];};
// Rest positions come from the explorer's own deterministic layout (layoutRing).
function presLayout(ring) {
  if(PRES.layouts[ring.id]) return PRES.layouts[ring.id];
  const pos=layoutRing(ring.graph), pts=ring.nodes.map(node=>[pos[node[0]].x,pos[node[0]].y]), xs=pts.map(p=>p[0]), ys=pts.map(p=>p[1]);
  return PRES.layouts[ring.id]={pts,minX:Math.min(...xs),maxX:Math.max(...xs),minY:Math.min(...ys),maxY:Math.max(...ys)};
}
function presFit(layout,box,cap=2.4) {
  const w=Math.max(layout.maxX-layout.minX,1), h=Math.max(layout.maxY-layout.minY,1), s=Math.min(box.w/w,box.h/h,cap);
  const ox=box.x+box.w/2-(layout.minX+w/2)*s, oy=box.y+box.h/2-(layout.minY+h/2)*s;
  return {s,pts:layout.pts.map(([x,y])=>[ox+x*s,oy+y*s])};
}
function presGraphBox() {
  const {W,H}=PRES;if(W<820) return {x:26,y:150,w:W-52,h:Math.max(H-270,120)};
  // Many-hub rings fill their box edge to edge, so they start clear of the story text instead of tucking under it.
  const left=Math.min(W*.3,470), tuck=PRES.ring?.hubs>=2?left+10:left*.35;return {x:40+tuck,y:70,w:W-300-60-tuck,h:H-110};
}
function presNodeColor(ring,i) {const p=presRevealP(i);return p>=.5?(ring.nodes[i][2]?PRES_COLOR.dirty:PRES_COLOR.clean):PRES_COLOR[ring.nodes[i][1]];}

function presDrawHaystack(ctx,t) {
  const A=PRES.anim, alpha=1-A.zoom;if(alpha<=.01||!PRES.hay) return;
  ctx.save();
  if(A.zoom>0) {const z=1+presEase(A.zoom)*14, [cx,cy]=PRES.clusterCenter;ctx.translate(cx,cy);ctx.scale(z,z);ctx.translate(-cx,-cy);}
  ctx.globalAlpha=alpha*A.bg;ctx.drawImage(PRES.hay,0,0,PRES.W,PRES.H);ctx.globalAlpha=alpha;
  const P=PRES.points, xy=PRES.hayXY;
  if(A.cand>.01) {
    if(PRES.candLayer) {ctx.globalAlpha=alpha*A.cand*(.8+.2*Math.sin(t/400));ctx.drawImage(PRES.candLayer,0,0,PRES.W,PRES.H);ctx.globalAlpha=alpha;}
    else P.cand.forEach((i,k)=>{const [x,y]=xy(i);presDot(ctx,x,y,1.3+A.cand*.9,PRES_COLOR.receiver,A.cand*(5+2*Math.sin(t/400+k)));});
  }
  if(A.gather>.01) {
    const g=presEase(A.gather), first=PRES.data.sizes[0]||0, count=P.ringDots.length;
    const at=k=>{const target=PRES.clusters[k];if(!target) return null;const [x0,y0]=xy(P.ringDots[k]), u=presEase(presClamp(g*1.6-(k%97)/97*.6));return [x0+(target[0]-x0)*u,y0+(target[1]-y0)*u];};
    if(count<=PRES_LIVE_DOTS) P.ringDots.forEach((i,k)=>{const p=at(k);if(p) presDot(ctx,p[0],p[1],1.5,k<first?PRES_COLOR.hub:'#9fd6ff',4.5);});
    else if(A.gather<1) presRingDots(ctx,count,first,at);
    else {PRES.gatherLayer||=presLayer(lc=>presRingDots(lc,count,first,k=>PRES.clusters[k]||null));ctx.drawImage(PRES.gatherLayer,0,0,PRES.W,PRES.H);}
  }
  ctx.restore();
}
function presDrawGraph(ctx,t,alpha) {
  if(alpha<=.01) return;
  const ring=PRES.ring, r=PRES.replay, box=presGraphBox(), F=presFit(presLayout(ring),box), P=F.pts, s=presClamp(F.s,.7,2.2);
  const flight=presFlight(), maxRecv=Math.max(...r.recv,1), now=performance.now();
  ctx.save();ctx.globalAlpha=alpha*(1-.72*PRES.anim.dim);
  if(ring.hubs===1&&PRES.W>=820) {ctx.font='600 11px ui-monospace,Menlo,monospace';ctx.fillStyle='rgba(120,150,175,.55)';ctx.textAlign='center';
    ctx.fillText('SENDERS  →',box.x+box.w*.12,box.y+box.h-4);ctx.fillText('→  RECEIVERS',box.x+box.w*.88,box.y+box.h-4);}
  const centre=presHubCentre(ring,P);
  ring.links.forEach((link,i)=>{
    const a=P[link[0]], b=P[link[1]], c=presLinkCurve(ring,link,a,b,centre), heat=presClamp(1-(r.t-r.lastLink[i])/(r.mps*r.speed*2.2)), seen=r.lastLink[i]>-1e8;
    ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.quadraticCurveTo(c[0],c[1],b[0],b[1]);
    ctx.strokeStyle=heat>0?presRgba('#cfe3f2',.18+heat*.6):presRgba('#7f9db5',(seen?.3:.1)*(ring.nodes.length>RING_BIG?.45:1)*(centre&&ring.hubs>=4&&ring.nodes[link[0]][1]==='hub'&&ring.nodes[link[1]][1]==='hub'?.5:1));ctx.lineWidth=(1+heat*1.6+Math.log1p(link[2])*.35)*Math.min(s,1.4);ctx.stroke();
  });
  if(r.loopFlash) {const age=(now-r.loopFlash.at)/1600;if(age<1) {const [a,b,c]=r.loopFlash.nodes.map(i=>P[i]);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.lineTo(c[0],c[1]);ctx.closePath();
    ctx.fillStyle=presRgba(PRES_COLOR.loop,.22*(1-age));ctx.fill();ctx.strokeStyle=presRgba(PRES_COLOR.loop,.9*(1-age));ctx.lineWidth=2.5;ctx.stroke();}}
  for(let k=r.next;k<ring.pays.length;k++) {
    const pay=ring.pays[k];if(pay[1]>r.t) break;const u=(r.t-pay[1])/flight;if(u<0||u>=1) continue;
    const link=ring.links[pay[0]], a=P[link[0]], b=P[link[1]], [x,y]=presQuad(a,presLinkCurve(ring,link,a,b,centre),b,presEase(u)), role=ring.nodes[link[0]][1];
    const color=PRES.reveal.on?(pay[3]?PRES_COLOR.dirty:'#9fb6c8'):role==='both'?'#d7e4ee':PRES_COLOR[role];
    presDot(ctx,x,y,(1.6+Math.sqrt(pay[2]/2e5)*2.2)*Math.min(s,1.3),color,9*Math.min(s,1.3));
  }
  ring.nodes.forEach((node,i)=>{
    const [x,y]=P[i], base=node[1]==='hub'?9:node[1]==='loop'?5.5:4.2, flash=r.flash.reduce((m,e)=>e.node===i?Math.max(m,1-(now-e.at)/500):m,0);
    const radius=(base+7*Math.sqrt(r.recv[i]/maxRecv))*Math.min(s,1.5)*(1+.35*presClamp(flash)), p=presRevealP(i), hot=p>=.5&&node[2];
    ctx.save();ctx.translate(x,y);ctx.scale(PRES.reveal.on?Math.max(Math.abs(Math.cos(p*Math.PI)),.06):1,1);
    presDot(ctx,0,0,radius,presNodeColor(ring,i),(node[1]==='hub'?34:hot?22:14)*Math.min(s,1.4)*(hot?1+.15*Math.sin(t/220+i):1));
    ctx.lineWidth=1.5;ctx.strokeStyle='rgba(7,13,20,.9)';ctx.stroke();ctx.restore();
    if(node[1]==='hub'&&p<.5) {const phase=(t/1800)%1;ctx.beginPath();ctx.arc(x,y,radius*(1.2+phase*1.6),0,Math.PI*2);ctx.strokeStyle=presRgba(PRES_COLOR.hub,.55*(1-phase));ctx.lineWidth=1.5;ctx.stroke();}
  });
  ctx.restore();
}
function presWallLayout() {
  // The grid starts below the story text, so a longer case-list caption never sits on the first row.
  const {W,H}=PRES, cols=W<820?5:10, rows=Math.ceil(PRES.data.rings.length/cols), story=element('pres-story')?.getBoundingClientRect?.(), stage=element('pres-canvas')?.getBoundingClientRect?.();
  const top=presClamp(story&&stage&&story.height?story.bottom-stage.top+14:150,150,H*.45), cw=(W-40)/cols, ch=(H-top-20)/rows;
  return PRES.data.rings.map((ring,k)=>({ring,x:20+(k%cols)*cw,y:top+Math.floor(k/cols)*ch,w:cw,h:ch}));
}
function presDrawWall(ctx) {
  if(PRES.anim.wall<=.01) return;
  PRES.wallCells=presWallLayout();const now=performance.now();ctx.save();
  PRES.wallCells.forEach((cell,k)=>{
    const a=PRES.anim.wall*presClamp((now-PRES.wallAt-k*22)/350);if(a<=0) return;
    const ring=cell.ring, hover=k===PRES.wallHover;ctx.globalAlpha=a*(ring.club&&!hover?.4:1);
    ctx.fillStyle=hover?'rgba(63,208,184,.08)':'rgba(13,24,36,.55)';ctx.strokeStyle=hover?'rgba(63,208,184,.7)':'rgba(31,51,70,.8)';ctx.lineWidth=1;ctx.setLineDash(ring.club?[4,3]:[]);
    ctx.beginPath();ctx.roundRect?ctx.roundRect(cell.x+3,cell.y+3,cell.w-6,cell.h-6,8):ctx.rect(cell.x+3,cell.y+3,cell.w-6,cell.h-6);ctx.fill();ctx.stroke();ctx.setLineDash([]);
    if(ring.club) {ctx.font='500 10px ui-monospace,Menlo,monospace';ctx.fillStyle='#8aa1b4';ctx.textAlign='right';ctx.fillText('club',cell.x+cell.w-10,cell.y+18);}
    const P=presFit(presLayout(ring),{x:cell.x+10,y:cell.y+10,w:cell.w-20,h:cell.h-32},1.2).pts;
    // Big rings: quieter links and smaller counterparties, so the hubs and loops carry the glyph.
    ctx.lineWidth=.6;ctx.strokeStyle=ring.focus?'rgba(127,157,181,.14)':'rgba(127,157,181,.28)';
    const centre=presHubCentre(ring,P), a0=ctx.globalAlpha;
    ring.links.forEach(link=>{const a=P[link[0]], b=P[link[1]], c=centre&&ring.nodes[link[0]][1]==='hub'&&ring.nodes[link[1]][1]==='hub'?presLinkCurve(ring,link,a,b,centre):null;ctx.globalAlpha=a0*(c&&ring.hubs>=4?.45:1);ctx.beginPath();ctx.moveTo(a[0],a[1]);c?ctx.quadraticCurveTo(c[0],c[1],b[0],b[1]):ctx.lineTo(b[0],b[1]);ctx.stroke();});ctx.globalAlpha=a0;
    ring.nodes.forEach((node,i)=>{ctx.beginPath();ctx.arc(P[i][0],P[i][1],node[1]==='hub'?3.2:ring.focus&&node[1]!=='loop'?1.3:1.9,0,Math.PI*2);ctx.fillStyle=node[2]?PRES_COLOR.dirty:'#4f6a80';ctx.fill();});
    if(ring.focus) {ctx.font='500 10px ui-monospace,Menlo,monospace';ctx.fillStyle='#5f7a8e';ctx.textAlign='center';ctx.fillText(`${integer(ring.accounts)} accts`,cell.x+cell.w/2,cell.y+cell.h-10);}
    ctx.font='500 11px ui-monospace,Menlo,monospace';ctx.fillStyle='#8aa1b4';ctx.textAlign='left';ctx.fillText(`#${ring.rank}`,cell.x+10,cell.y+cell.h-10);
    ctx.fillStyle=ring.rate>=.5?PRES_COLOR.dirty:ring.rate>0?'#e7a3b1':'#5f7a8e';ctx.textAlign='right';ctx.fillText(presPct(ring.rate),cell.x+cell.w-10,cell.y+cell.h-10);
  });
  ctx.restore();
}
function presTimeline() {
  const canvas=element('pres-tl');if(!canvas?.getContext||!canvas.width||!PRES.replay) return;
  const g=canvas.getContext('2d'), w=canvas.width, h=canvas.height, r=PRES.replay, D=PRES.DPR, bins=90, lo=r.t0, hi=r.t1+1;
  const counts=new Array(bins).fill(0), dirty=new Array(bins).fill(0);
  PRES.ring.pays.forEach(pay=>{const b=Math.min(bins-1,Math.floor((pay[1]-lo)/(hi-lo)*bins));counts[b]++;if(pay[3]) dirty[b]++;});
  const max=Math.max(...counts,1), bw=w/bins;g.clearRect(0,0,w,h);
  counts.forEach((n,b)=>{if(!n) return;const bh=Math.max(2*D,n/max*(h-8*D)), x=b*bw;
    g.fillStyle=lo+(b+.5)/bins*(hi-lo)<=r.t?'rgba(63,208,184,.85)':'rgba(110,140,165,.45)';g.fillRect(x+1,h-bh,Math.max(bw-2,1),bh);
    if(PRES.reveal.on&&dirty[b]) {const dh=bh*dirty[b]/n;g.fillStyle=PRES_COLOR.dirty;g.fillRect(x+1,h-dh,Math.max(bw-2,1),dh);}});
  g.fillStyle='rgba(138,161,180,.35)';
  const startDay=new Date(PRES.ring.start).getUTCHours()*60+new Date(PRES.ring.start).getUTCMinutes();
  for(let m=1440-startDay;m<=hi;m+=1440) if(m>=lo) g.fillRect((m-lo)/(hi-lo)*w,0,1,4*D);
  g.fillStyle='#e4edf4';g.fillRect(presClamp((r.t-lo)/(hi-lo))*w-D,0,2*D,h);
  const date=new Date(PRES.ring.start+Math.max(r.t,0)*60000), month=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][date.getUTCMonth()];
  element('pres-clock').innerHTML=`${month} ${date.getUTCDate()} · ${String(date.getUTCHours()).padStart(2,'0')}:${String(date.getUTCMinutes()).padStart(2,'0')}<small>UTC · ${date.getUTCFullYear()}</small>`;
}
function presFrame(t) {
  PRES.raf=null;if(!PRES.open) return;
  const n=PRES.step, target={bg:[1,.4,.07,0,0,0,0][n],cand:n===1?1:0,gather:n>=2?1:0,zoom:n>=3?1:0,graph:n>=3&&n<=5?1:0,wall:n===6?1:0,dim:n===5?1:0}, still=!motionAllowed();
  for(const key in PRES.anim) {
    const rate=key==='gather'?.035:key==='zoom'?(target.zoom>PRES.anim.zoom?.045:.13):.1, d=target[key]-PRES.anim[key];
    PRES.anim[key]=still||Math.abs(d)<.002?target[key]:PRES.anim[key]+d*rate;
  }
  const r=PRES.replay;
  if(r.playing&&n>=3&&n<=5) {
    const dt=Math.min((t-(PRES.last||t))/1000,.25), next=r.t+dt*r.mps*r.speed;
    if(next>=presEnd()) {presSeek(presEnd());presSetPlaying(false);} else presSeek(next);
    presMeters();presTimeline();
  }
  PRES.last=t;
  const canvas=element('pres-canvas'), ctx=canvas.getContext('2d');
  ctx.setTransform(PRES.DPR,0,0,PRES.DPR,0,0);ctx.clearRect(0,0,PRES.W,PRES.H);
  presDrawHaystack(ctx,t);presDrawGraph(ctx,t,PRES.anim.graph*presClamp(PRES.anim.zoom*1.4-.3));presDrawWall(ctx);
  if(n===4&&PRES.reveal.on) presRevealCount();
  PRES.raf=requestAnimationFrame(presFrame);
}

// ---- controls ----
function presScrub(event) {
  const box=element('pres-tl').getBoundingClientRect(), u=presClamp((event.clientX-box.left)/box.width), r=PRES.replay;
  presSeek(r.t0+u*(r.t1+1-r.t0));presMeters();presTimeline();
}
function presFullscreen() {const promise=document.fullscreenElement?document.exitFullscreen?.():element('presenter').requestFullscreen?.();promise?.catch?.(()=>{});}
function presKey(event) {
  if(!PRES.open||event.target?.matches?.('select,input')) return;
  if(event.target?.closest?.('button')&&(event.key===' '||event.key==='Enter')) return;
  const replayish=PRES.step>=3&&PRES.step<=5;
  if(event.key==='ArrowRight'||event.key==='PageDown') {event.preventDefault();presGo(PRES.step+1);}
  else if(event.key==='ArrowLeft'||event.key==='PageUp') {event.preventDefault();presGo(PRES.step-1);}
  else if(event.key===' ') {event.preventDefault();if(replayish) presSetPlaying(!PRES.replay.playing);}
  else if(event.key==='r'||event.key==='R') {if(replayish) {presReveal(!PRES.reveal.on);presLegend();presTimeline();if(!PRES.reveal.on) presMeters();}}
  else if(event.key==='f'||event.key==='F') presFullscreen();
  else if(event.key==='Escape') {if(!document.fullscreenElement) closePresenter();}
  else if(/^[1-4]$/.test(event.key)) presGo(PRES_SCENE_START[+event.key-1]);
}
function presWallCell(event) {
  const box=element('pres-canvas').getBoundingClientRect(), x=event.clientX-box.left, y=event.clientY-box.top;
  return PRES.wallCells.findIndex(cell=>x>=cell.x&&x<cell.x+cell.w&&y>=cell.y&&y<cell.y+cell.h);
}
function bindPresenter() {
  element('network-present').onclick=openPresenter;
  element('pres-close').onclick=closePresenter;element('pres-fs').onclick=presFullscreen;
  element('pres-next').onclick=()=>presGo(PRES.step+1);element('pres-back').onclick=()=>presGo(PRES.step-1);
  element('pres-scenes').onclick=event=>{const button=event.target.closest?.('[data-pres-step]');if(button) presGo(+button.dataset.presStep);};
  element('pres-play').onclick=()=>presSetPlaying(!PRES.replay.playing);
  element('pres-speed').onclick=()=>{const r=PRES.replay;r.speed=r.speed===1?3:r.speed===3?.5:1;element('pres-speed').textContent=`${r.speed}×`;};
  const tl=element('pres-tl'), canvas=element('pres-canvas');
  tl.onpointerdown=event=>{tl.setPointerCapture?.(event.pointerId);presScrub(event);tl.onpointermove=presScrub;};
  tl.onpointerup=()=>{tl.onpointermove=null;};
  canvas.onpointermove=event=>{if(PRES.step!==6) return;PRES.wallHover=presWallCell(event);canvas.style.cursor=PRES.wallHover>=0?'pointer':'';};
  canvas.onclick=event=>{if(PRES.step!==6) return;const k=presWallCell(event);if(k>=0) {PRES.reveal.on=false;presSetRing(PRES.data.rings[k]);element('pres-ring').value=String(PRES.data.rings[k].rank);presGo(3,false);presSetPlaying(motionAllowed());}};
  document.addEventListener?.('keydown',presKey);
  if(typeof ResizeObserver==='function') new ResizeObserver(()=>{if(PRES.open) presResize();}).observe(element('pres-stage'));
  else window.addEventListener?.('resize',()=>{if(PRES.open) presResize();});
}

// ---- BQ1 presenter: the review queue as a full-screen story ----
// Every test-period payment -> model score bands -> the review cut-off -> rules
// versus model on the published hit counts -> every review size -> one case
// replayed from its sender's history -> where BQ1 alerts meet BQ2 rings.
// Data comes only from snapshot.exploration.aml (test period, whole population)
// and snapshot.network. The export carries no per-payment labels, so ground
// truth appears only as each queue's published hit count, drawn as a tally.
// The button and overlay are built here, so index.html needs no markup.
const QP_STEPS=7, QP_SCENE_START=[0,2,3,5,6], QP_KS=['25','50','100','250'], QP_MAX_DOTS=1200000;
const QP_COLOR={model:'#3fd0b8',rules:'#ffb648',hit:'#ff2e55',miss:'#2c4255',ring:'#b794ff',flag:'#ffb648',sender:'#4fa8ff',receiver:'#ffb648'};
const QP_HEAT=['#4fa8ff','#3fd0b8','#ffb648','#ff6f5e'];
const QP={open:false,snap:null,step:0,data:null,k:'100',row:null,raf:null,last:0,W:0,H:0,DPR:1,pts:null,layer:null,image:null,layerKey:'',storyBottom:200,
  anim:{hay:1,col:0,squeeze:0,dim:0,grid:0,duel:0,chart:0,caseView:0,link:0,wall:0},wallCells:[],wallGlyphs:{},tallyAt:0,chartAt:0,linkAt:0,hover:-1,replay:null,glyphs:{},counts:'',returnFocus:null};
const qpNow=()=>typeof performance==='object'?performance.now():Date.now();
const qpAccount=(bank,account)=>`${String(bank).replace(/^0+(?=.)/,'')}:${account}`;
const qpHits=(metrics,k)=>metrics.hits??Math.round((metrics.precision_at_k||0)*Number(k));
const qpRow=rank=>QP.data.queue.find(row=>row.rank===Number(rank))||QP.data.queue[0];
const qpOverlap=(rows,k)=>rows.slice(0,Number(k)).filter(row=>row.ring).length;
const qpLerp=(a,b,u)=>a+(b-a)*u;
function qpMix(a,b,u) {const x=parseInt(a.slice(1),16), y=parseInt(b.slice(1),16), c=s=>Math.round(qpLerp(x>>s&255,y>>s&255,u));return `#${[16,8,0].map(s=>c(s).toString(16).padStart(2,'0')).join('')}`;}
function qpHeat(score) {const s=Math.round(presClamp(Number(score)||0)*48)/48*(QP_HEAT.length-1), i=Math.min(Math.floor(s),QP_HEAT.length-2);return qpMix(QP_HEAT[i],QP_HEAT[i+1],s-i);}

function qpScope(snap) {return snap?.exploration?.aml?.periods?.test?.scopes?.find(scope=>scope.id==='all')||null;}
// Whether a release carries what the presenter needs (legacy snapshots do not).
function bq1PresenterAvailable(snap) {
  const scope=qpScope(snap);
  return Boolean(snap?.model&&scope?.model?.histogram?.length&&scope.model.queue?.length>=25&&Array.isArray(scope.rules?.queue)
    &&scope.model.metrics_by_k?.['25']?.rows>0&&scope.rules.metrics_by_k?.['25']);
}
function bq1PresenterData(snap) {
  const scope=qpScope(snap), aml=snap.exploration.aml, fields=aml.record_fields;
  const record=id=>{const row=aml.records[id];return row?Object.fromEntries(fields.map((field,i)=>[field,row[i]])):null;};
  const ks=QP_KS.filter(k=>scope.model.metrics_by_k[k]&&scope.rules.metrics_by_k[k]&&scope.model.queue.length>=Number(k));
  const pick=source=>Object.fromEntries(ks.map(k=>[k,source.metrics_by_k[k]]));
  // BQ2 cross-check: every account in the rings the network section draws.
  const net=snap.network, ranks=new Map((net?.ring_list||[]).map(row=>[row.ring_id,row.rank])), ringOf=new Map();
  Object.entries(net?.graphs||{}).forEach(([id,graph])=>(graph.nodes||[]).forEach(node=>{if(!ringOf.has(node.id)) ringOf.set(node.id,id);}));
  const ringFor=row=>ringOf.get(qpAccount(row.from_bank,row.from_account))||ringOf.get(qpAccount(row.to_bank,row.to_account))||null;
  const queue=scope.model.queue.map((id,i)=>{const row=record(id);return row&&{...row,rank:i+1,ring:ringFor(row)};}).filter(Boolean);
  queue.forEach(row=>{row.ringRank=row.ring?ranks.get(row.ring)??null:null;});
  const rulesQueue=scope.rules.queue.map(record).filter(Boolean).map(row=>({...row,ring:ringFor(row)}));
  const first=scope.model.metrics_by_k[ks[0]];
  // No top alert touches a drawn ring (e.g. a release whose drawn rings miss the queue): end on the plain queue instead of an empty cross-check.
  const linked=ringOf.size&&queue.slice(0,Number(ks.at(-1)||0)).some(row=>row.ring);
  return {ks,model:pick(scope.model),rules:pick(scope.rules),hist:scope.model.histogram.map(bin=>Number(bin.transactions)||0),
    rows:first.rows,positives:first.positives,queue,rulesQueue,histories:aml.histories||{},ringOf,
    rings:linked?{accounts:ringOf.size,drawn:Object.keys(net.graphs).length,population:net.totals?.accounts??null,graphs:net.graphs,ranks}:null,
    period:evaluationPeriodLabel('test')};
}

// Story text for the current step (also used by tests).
function qpStoryHTML(step=QP.step,data=QP.data,k=QP.k,row=QP.row) {
  const n=Number(k), m=data.model[k], r=data.rules[k], top=data.ks.at(-1), random=n*data.positives/data.rows;
  const dots=data.rows>QP_MAX_DOTS?`Each dot stands for about ${integer(Math.round(data.rows/QP_MAX_DOTS))} real payments`:'Every dot is one real payment from the test days';
  switch(step) {
    case 0:return `<div class="pres-eyebrow">${escapeText(data.period)} · held out from training</div><h2>${integer(data.rows)} payments. <span class="pres-dirty">${integer(data.positives)}</span> of them are laundering.</h2><p>About 1 in ${integer(Math.round(data.rows/Math.max(data.positives,1)))}. ${dots}; where it sits means nothing yet. The job: put the right ones in front of an analyst first.</p>`;
    case 1:return `<div class="pres-eyebrow">Step 1 · score every payment</div><h2><em>${integer(data.hist[0])}</em> score almost zero. Just ${integer(data.hist.at(-1))} reach the top band.</h2><p>The frozen model ranks each payment from its sender's recent history: counterparties, amounts and payment rail. Columns are score bands from 0 to 1, heights on a log scale. A score is a review priority, not a probability.</p>`;
    case 2:return `<div class="pres-eyebrow">Step 2 · the review cut-off</div><h2>Analysts can open <em>${integer(n)}</em> cases. The model hands them its top ${integer(n)}.</h2><p>That is ${(n/data.rows*100).toFixed(3)}% of the payments. Each square is one real queued payment, in rank order. Hover for details; click one to open its case.</p>`;
    case 3:return `<div class="pres-eyebrow">Step 3 · ground truth</div><h2>Rules find <span class="qp-rules" id="qp-count-r">${integer(qpHits(r,k))}</span> of ${integer(n)}. The model finds <span class="pres-dirty" id="qp-count-m">${integer(qpHits(m,k))}</span>.</h2><p>The labels come with the dataset and were never used to score these days. ${integer(n)} payments picked at random would hold about ${random.toFixed(random<1?2:1)}. The dots are a tally of each queue's published count, not particular payments.</p>`;
    case 4: {
      const mt=data.model[top], rt=data.rules[top], ratio=rt.average_precision>0?mt.average_precision/rt.average_precision:null;
      return `<div class="pres-eyebrow">Step 4 · every review size</div><h2>At ${integer(Number(top))} reviews: model <em>${integer(qpHits(mt,top))}</em>, rules ${integer(qpHits(rt,top))}.</h2><p>Not one lucky cut-off. Average precision scores the whole ranking: ${Number(mt.average_precision).toFixed(3)} for the model, ${Number(rt.average_precision).toFixed(4)} for the rules${ratio?` (${ratio.toFixed(0)}×)`:''}. The dashed line is a perfect queue.</p>`;
    }
    case 5: {
      const history=data.histories[row.transaction_id], shown=history?.events?.length||0, seen=history?.observed_prior_transactions??shown;
      return `<div class="pres-eyebrow">Case file · rank #${integer(row.rank)} of ${integer(data.rows)}</div><h2>${presUsd(row.amount_usd_asof)} by ${escapeText(row.payment_format)} in ${escapeText(row.payment_currency)}, score <em>${Number(row.model_score).toFixed(3)}</em>.</h2><p>${shown?`Replaying the sender's ${seen>shown?`latest ${integer(shown)} of ${integer(seen)}`:integer(shown)} earlier payments, then the flagged one.`:'No earlier payments from this sender are published.'} Whether this payment was laundering is not published; an analyst makes the call.</p>`;
    }
    default: {
      if(!data.rings) {const shown=Math.min(n,QP_WALL_MAX), all=qpHits(m,k)>=n, top=data.queue.slice(0,n), formats=new Map();
        top.forEach(row=>formats.set(row.payment_format,(formats.get(row.payment_format)||0)+1));
        const usd=top.map(row=>Number(row.amount_usd_asof)||0).sort((a,b)=>a-b), median=usd[Math.floor(usd.length/2)]||0, [lead,leadCount]=[...formats.entries()].sort((a,b)=>b[1]-a[1])[0]||['',0];
        const mix=leadCount===top.length?`Every one is a${/^[AEIOU]/i.test(lead)?'n':''} ${escapeText(lead)} payment`:`${integer(leadCount)} of ${integer(top.length)} are ${escapeText(lead)} payments`;
        return `<div class="pres-eyebrow">The review queue · top ${integer(n)}</div><h2>${integer(n)} cases, <span class="pres-dirty">${integer(qpHits(m,k))}</span> of them laundering.</h2><p>Each tile is one flagged payment: the sender on the left, everyone it paid before on the right, and the flagged payment ${all?'in red. Every one is labelled laundering':'in amber'}.${shown<n?` The first ${integer(shown)} are shown.`:''} Click a tile to open its case file.</p><p>${mix}; the median is ${presUsd(median)}.</p>`;}
      const rings=new Set(data.queue.slice(0,n).filter(item=>item.ring).map(item=>item.ring)).size;
      return `<div class="pres-eyebrow">BQ1 × BQ2</div><h2><em class="qp-ring">${integer(qpOverlap(data.queue,k))}</em> of the model's top ${integer(n)} touch an account in a BQ2 ring.</h2><p>They fall in ${plural(rings,'ring')} of the ${integer(data.rings.drawn)} BQ2 draws, which hold ${integer(data.rings.accounts)}${data.rings.population?` of ${integer(data.rings.population)}`:''} accounts. One method scores single payments, the other maps who pays whom, and they land on the same accounts. The rules' top ${integer(n)}: ${integer(qpOverlap(data.rulesQueue,k))}.</p>`;
    }
  }
}

// ---- mounting: the glowing button in the AML heading, and the overlay on first open ----
function bq1PresenterRefresh() {
  element('network-present')?.classList?.add('present-cta','section-cta');
  let button=element('aml-present');
  if(!button) {
    const host=document.querySelector?.('#aml .heading .controls');if(!host?.insertAdjacentHTML) return;
    host.insertAdjacentHTML('afterbegin','<button id="aml-present" class="present-button present-cta section-cta" type="button" hidden>▶ Present the queue</button>');
    button=element('aml-present');if(!button) return;
    button.onclick=openBq1Presenter;
  }
  button.hidden=!bq1PresenterAvailable(snapshot);
  if(QP.open&&button.hidden) closeBq1Presenter();
}
function qpEnsureOverlay() {
  if(element('qp')) return;
  const scenes=[['0','The haystack'],['2','The queue'],['3','Head to head'],['5','Case file'],['6','BQ1 × BQ2']].map(([step,label],i)=>`<li><button type="button" data-qp-step="${step}"><span>${i+1}</span><b id="qp-scene-${step}" style="font-weight:inherit">${label}</b></button></li>`).join('');
  document.body.insertAdjacentHTML('beforeend',`<div id="qp" class="presenter qp" hidden role="dialog" aria-modal="true" aria-labelledby="qp-label"><div class="pres-app">
<div class="pres-top"><div class="pres-brand" id="qp-label"><b>BQ1</b> · AML review queue · presenter view</div><ol class="pres-scenes" id="qp-scenes" aria-label="Story sections">${scenes}</ol><button type="button" class="pres-icon" id="qp-fs">Full screen</button><button type="button" class="pres-icon" id="qp-close">Close ✕</button></div>
<div class="pres-stage" id="qp-stage"><canvas id="qp-canvas" style="position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none" role="img" aria-label="Animated view of scored payments and the review queue"></canvas><div class="pres-story" id="qp-story" aria-live="polite"></div>
<div class="qp-capacity" id="qp-capacity" role="group" aria-label="Review capacity" hidden><span>Review capacity</span><span id="qp-ks"></span></div>
<aside class="pres-readout qp-readout" id="qp-readout" hidden><label>Case<select id="qp-case"></select></label><div class="pres-quick" id="qp-quick"></div><div class="pres-meters" id="qp-meters"></div><div class="qp-evidence" id="qp-evidence"></div><div class="qp-badge" id="qp-badge" hidden></div></aside>
<div class="ring-tip qp-tip" id="qp-tip" role="tooltip" hidden></div><div class="pres-toast qp-toast" id="qp-toast" hidden></div></div>
<div class="pres-foot"><div class="pres-timeline" id="qp-timeline" hidden><button type="button" class="pres-play" id="qp-play" aria-label="Play">▶</button><div class="pres-clock" id="qp-clock"></div><canvas id="qp-tl" style="width:100%;height:38px;display:block;cursor:pointer;touch-action:none;min-width:0" aria-label="Sender payment timeline. Click to jump."></canvas><button type="button" class="pres-icon" id="qp-speed">1×</button></div>
<div class="pres-bar"><button type="button" class="pres-nav" id="qp-back">← Back</button><div class="pres-mid"><div class="pres-steps" id="qp-steps"></div><div class="pres-hint"><span><kbd>→</kbd>next</span><span><kbd>←</kbd>back</span><span><kbd>Space</kbd>play / pause</span><span><kbd>R</kbd>replay step</span><span><kbd>F</kbd>full screen</span><span><kbd>Esc</kbd>close</span></div></div><button type="button" class="pres-nav next" id="qp-next">Next →</button></div></div>
</div></div>`);
  qpBind();
}

function openBq1Presenter() {
  if(!bq1PresenterAvailable(snapshot)) return;
  qpEnsureOverlay();
  if(QP.snap!==snapshot) {QP.snap=snapshot;QP.data=bq1PresenterData(snapshot);QP.pts=null;QP.glyphs={};QP.wallGlyphs={};}
  const lastScene=element('qp-scene-6');if(lastScene) lastScene.textContent=QP.data.rings?'BQ1 × BQ2':'The cases';
  QP.k=QP.data.ks.includes('100')?'100':QP.data.ks.at(-1);QP.open=true;QP.returnFocus=document.activeElement;QP.layerKey='';
  element('qp-ks').innerHTML=QP.data.ks.map(k=>`<button type="button" data-k="${k}" aria-pressed="false">${k}</button>`).join('');
  qpCaseOptions();qpSetCase(QP.data.queue[0]);
  element('qp').hidden=false;document.body?.classList?.add('pres-lock');
  if(!qpFreshCanvas()) qpResize();qpGo(0,true);element('qp-next').focus?.();
  if(!QP.raf&&typeof requestAnimationFrame==='function') QP.raf=requestAnimationFrame(qpFrame);
}
function closeBq1Presenter() {
  QP.open=false;element('qp').hidden=true;document.body?.classList?.remove('pres-lock');
  if(QP.replay) QP.replay.playing=false;qpHideTip();
  // Release the large pixel buffers; they are rebuilt on the next open.
  QP.layer=null;QP.image=null;QP.layerKey='';const canvas=element('qp-canvas');if(canvas) {canvas.width=0;canvas.height=0;}
  if(QP.raf&&typeof cancelAnimationFrame==='function') cancelAnimationFrame(QP.raf);QP.raf=null;
  if(document.fullscreenElement) document.exitFullscreen?.().catch?.(()=>{});
  QP.returnFocus?.focus?.();
}

function qpGo(n,force=false) {
  n=presClamp(n,0,QP_STEPS-1);if(n===QP.step&&!force) return;
  const previous=QP.step;QP.step=n;
  element('qp-readout').hidden=n!==5;element('qp-timeline').hidden=n!==5;element('qp-capacity').hidden=!((n>=2&&n<=4)||n===6);
  element('qp-toast').hidden=true;qpHideTip();QP.hover=-1;
  if(n===3&&(previous!==3||force)) QP.tallyAt=qpNow();
  if(n===4) QP.chartAt=qpNow();
  if(n===6) QP.linkAt=qpNow();
  if(n===5) {qpSizeTimeline();if(motionAllowed()) {qpSeek(QP.replay.t0-QP.replay.span*.04);qpSetPlaying(true);} else {qpSeek(qpEnd());qpSetPlaying(false);}}
  else qpSetPlaying(false);
  qpUpdateText();
  element('qp-back').disabled=n===0;element('qp-next').disabled=n===QP_STEPS-1;element('qp-next').textContent=n===QP_STEPS-1?'End':'Next →';
  element('qp-steps').innerHTML=Array.from({length:QP_STEPS},(_,i)=>`<i class="${i<=n?'on':''}"></i>`).join('');
  const scene=QP_SCENE_START.filter(start=>start<=n).at(-1);
  element('qp').querySelectorAll?.('[data-qp-step]').forEach(button=>button.setAttribute('aria-current',+button.dataset.qpStep===scene?'step':'false'));
}
function qpUpdateText() {
  element('qp-story').innerHTML=`<div class="pres-fade">${qpStoryHTML()}</div>`;QP.counts='';
  element('qp-ks').querySelectorAll?.('[data-k]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.k===QP.k)));
  qpReadout();qpMeasureStory();
}
function qpSetK(k) {
  if(!QP.data.ks.includes(k)||k===QP.k) return;
  QP.k=k;QP.hover=-1;qpHideTip();if(QP.step===3) QP.tallyAt=qpNow();if(QP.step===6) QP.linkAt=qpNow();
  qpCaseOptions();qpUpdateText();
}

// ---- case file: the sender's published history, then the flagged payment ----
function qpCaseOptions() {
  const rows=QP.data.queue.slice(0,Math.max(Number(QP.k),25));
  selectOptions('qp-case',rows.map(row=>String(row.rank)),rank=>{const row=qpRow(rank);return `#${row.rank} · ${presUsd(row.amount_usd_asof)} · ${row.payment_format} · ${Number(row.model_score).toFixed(3)}${row.ring?' · BQ2':''}`;});
  if(QP.row) element('qp-case').value=String(QP.row.rank);
  const inRing=rows.find(row=>row.ring), largest=rows.slice(0,25).reduce((a,b)=>b.amount_usd_asof>a.amount_usd_asof?b:a,rows[0]);
  element('qp-quick').innerHTML=[[rows[0],'top score'],[inRing,'in a BQ2 ring'],[largest,'largest']].filter(([row],i,all)=>row&&all.findIndex(([other])=>other===row)===i)
    .map(([row,label])=>`<button type="button" data-rank="${row.rank}">#${row.rank} ${label}</button>`).join('');
}
function qpSetCase(row) {
  QP.row=row;
  const events=(QP.data.histories[row.transaction_id]?.events||[]).slice().sort((a,b)=>String(a.timestamp).localeCompare(String(b.timestamp)));
  const sender=qpAccount(row.from_bank,row.from_account), nodes=[{id:sender,got:0}], index=new Map([[sender,0]]);
  const node=(bank,account)=>{const id=qpAccount(bank,account);if(!index.has(id)) {index.set(id,nodes.length);nodes.push({id,got:0});}return index.get(id);};
  const pays=events.map(event=>({to:node(event.to_bank,event.to_account),at:Date.parse(event.timestamp+'Z'),usd:Number(event.amount_usd_asof)||0,flag:false}))
    .concat([{to:node(row.to_bank,row.to_account),at:Date.parse(row.timestamp+'Z'),usd:Number(row.amount_usd_asof)||0,flag:true}]);
  nodes.forEach(item=>{const ring=QP.data.ringOf.get(item.id);item.ringRank=ring?QP.data.rings?.ranks.get(ring)??null:null;});
  const t0=pays[0].at, t1=pays.at(-1).at, span=Math.max(t1-t0,3600000), dur=presClamp(4+pays.length*.7,6,12);
  QP.replay={nodes,pays,flagTo:pays.at(-1).to,t0,t1,span,dur,t:t0,playing:false,speed:1,toast:false};
  qpSeek(t0-span*.04);
  if(element('qp-case')) element('qp-case').value=String(row.rank);
}
const qpFlight=()=>QP.replay.span/QP.replay.dur*.8;
const qpEnd=()=>QP.replay.t1+qpFlight()*1.05;
function qpSeek(t) {
  const r=QP.replay;r.t=t;r.nodes.forEach(item=>{item.got=0;});
  r.pays.forEach(pay=>{if(pay.at+qpFlight()<=t) r.nodes[pay.to].got+=pay.usd;});
  const done=t>=r.t1+qpFlight();
  if(done&&!r.toast&&r.playing) {r.toast=true;qpToast(`Flagged · rank #${integer(QP.row.rank)} of ${integer(QP.data.rows)} · score ${Number(QP.row.model_score).toFixed(4)}`);}
  if(!done) r.toast=false;
}
function qpSetPlaying(on) {
  const r=QP.replay;if(!r) return;if(on&&r.t>=qpEnd()-1) qpSeek(r.t0-r.span*.04);
  r.playing=on;const play=element('qp-play');if(play) {play.textContent=on?'❚❚':'▶';play.setAttribute('aria-label',on?'Pause':'Play');}
}
let qpToastTimer=0;
function qpToast(html) {const el=element('qp-toast');el.innerHTML=html;el.hidden=false;clearTimeout(qpToastTimer);qpToastTimer=setTimeout(()=>{el.hidden=true;},3200);}
function qpEvidence(row) {
  return (row.model_evidence||'').split('; ').filter(Boolean).map(part=>{const colon=part.lastIndexOf(':');return {label:part.slice(0,colon),value:Number(part.slice(colon+1).replace('margin','').trim())};}).filter(part=>part.label&&Number.isFinite(part.value));
}
function qpReadout() {
  const row=QP.row;if(!row||QP.step!==5) return;
  element('qp-meters').innerHTML=`<div><small>Amount</small><strong>${presUsd(row.amount_usd_asof)}</strong></div><div><small>Score</small><strong>${Number(row.model_score).toFixed(4)}</strong></div>`
    +`<div class="wide"><small>Rail · currency</small><strong>${escapeText(row.payment_format)} · ${escapeText(row.payment_currency)}</strong></div>`
    +`<div><small>Counterparties, 24h</small><strong>${row.distinct_counterparties_24h??'–'}</strong></div><div><small>Payments, last hour</small><strong>${row.txn_count_1h??'–'}</strong></div>`;
  const parts=qpEvidence(row), top=Math.max(.01,...parts.map(part=>Math.abs(part.value)));
  element('qp-evidence').innerHTML=parts.length?`<small>Why it ranked high</small>${parts.map(part=>`<div class="qp-ev${part.value<0?' neg':''}"><span>${escapeText(part.label)}</span><b>${part.value>0?'+':''}${part.value.toFixed(2)}</b><i style="--w:${(Math.abs(part.value)/top*100).toFixed(1)}%"></i></div>`).join('')}`:'';
  const sender=QP.data.ringOf.get(qpAccount(row.from_bank,row.from_account)), receiver=QP.data.ringOf.get(qpAccount(row.to_bank,row.to_account)), ranks=QP.data.rings?.ranks;
  const notes=[sender&&`the sender is in BQ2 ring #${ranks.get(sender)}`,receiver&&`the receiver is in BQ2 ring #${ranks.get(receiver)}`].filter(Boolean);
  element('qp-badge').hidden=!notes.length;element('qp-badge').textContent=notes.length?`Cross-check: ${notes.join(' and ')}.`:'';
}

// ---- drawing ----
function qpResize() {
  const canvas=element('qp-canvas');if(!canvas?.getBoundingClientRect) return;
  const box=canvas.getBoundingClientRect();QP.DPR=Math.min(window.devicePixelRatio||1,2,Math.sqrt(8e6/Math.max(box.width*box.height,1)));QP.W=box.width;QP.H=box.height;
  canvas.width=Math.round(box.width*QP.DPR);canvas.height=Math.round(box.height*QP.DPR);
  qpSizeTimeline();qpMeasureStory();qpBuildPoints();QP.layerKey='';
}
function qpSizeTimeline() {
  const tl=element('qp-tl');if(!tl?.getBoundingClientRect) return;
  const box=tl.getBoundingClientRect();tl.width=Math.round(box.width*QP.DPR);tl.height=Math.round(box.height*QP.DPR);qpTimeline();
}
function qpMeasureStory() {
  const story=element('qp-story')?.getBoundingClientRect?.(), stage=element('qp-canvas')?.getBoundingClientRect?.();
  QP.storyBottom=story&&stage&&story.height?story.bottom-stage.top:200;
}
function qpBuildPoints() {
  const data=QP.data;if(!QP.W||!data) return;
  const scale=Math.max(1,data.rows/QP_MAX_DOTS), counts=data.hist.map(count=>count?Math.max(1,Math.round(count/scale)):0), n=counts.reduce((a,b)=>a+b,0);
  if(QP.pts?.n===n) return;
  // Each dot is one payment (or a fixed share of them); bins hold their published counts.
  const bins=counts.length, random=presRandom(1459), hx=new Float32Array(n), hy=new Float32Array(n), cx=new Float32Array(n), cy=new Float32Array(n), pb=new Uint8Array(n), bin=new Uint8Array(n), start=[];
  const peak=Math.log10(1+Math.max(...data.hist,1));let at=0;
  counts.forEach((count,b)=>{start[b]=at;const height=Math.log10(1+data.hist[b])/peak;
    for(let j=0;j<count;j++,at++) {const a=random()*Math.PI*2, r=Math.pow(random(),.62);hx[at]=.5+Math.cos(a)*r*.5;hy[at]=.5+Math.sin(a)*r*.5;pb[at]=40+random()*90|0;bin[at]=b;cx[at]=(b+.14+random()*.72)/bins;cy[at]=random()*height;}});
  start[bins]=n;
  const seeds=presRandom(77);
  QP.pts={n,hx,hy,cx,cy,pb,bin,start,bins,heights:data.hist.map(count=>Math.log10(1+count)/peak),seed:QP.data.queue.map(()=>[seeds(),seeds()])};
}
function qpDisc() {const {W,H}=QP;return {cx:W*.56,cy:H*.52,rx:Math.min(W*.47,H*.78),ry:H*.44};}
function qpHistBox() {
  const {W,H}=QP, top=Math.max(QP.storyBottom+40,H*.3), u=presEase(QP.anim.squeeze), narrow=W<820;
  return {x:qpLerp(48,narrow?20:40,u),w:qpLerp(W-96,narrow?W-40:W*.5,u),base:H-60,h:Math.max(H-60-top,60)};
}
function qpRenderLayer() {
  const {W,H}=QP, P=QP.pts, A=QP.anim;if(!P) return null;
  const col=presEase(A.col), fade=A.hay*(1-.72*A.dim), box=qpHistBox(), d=qpDisc();
  const key=`${W}x${H}|${col.toFixed(3)}|${fade.toFixed(3)}|${box.x.toFixed(1)}|${box.w.toFixed(1)}|${box.h.toFixed(1)}`;
  if(key===QP.layerKey) return fade<=.01?null:QP.layer;
  if(fade<=.01) {QP.layerKey=key;return null;}
  // Drawn at CSS-pixel resolution: one pixel per dot, added up so dense bands glow.
  const w=Math.max(1,Math.round(W)), h=Math.max(1,Math.round(H));
  if(!QP.layer||!QP.image||QP.image.width!==w||QP.image.height!==h) {QP.layer=document.createElement('canvas');QP.layer.width=w;QP.layer.height=h;QP.image=QP.layer.getContext('2d',{willReadFrequently:true}).createImageData(w,h);}
  const image=QP.image, px=image.data;px.fill(0);
  for(let i=0;i<P.n;i++) {
    const x0=d.cx+(P.hx[i]-.5)*2*d.rx, y0=d.cy+(P.hy[i]-.5)*2*d.ry, X=qpLerp(x0,box.x+P.cx[i]*box.w,col)|0, Y=qpLerp(y0,box.base-P.cy[i]*box.h,col)|0;
    if(X<0||Y<0||X>=w||Y>=h) continue;
    const k=(Y*w+X)*4, b=P.pb[i]*fade;px[k]=Math.min(110,px[k]+b*.5);px[k+1]=Math.min(205,px[k+1]+b*.8);px[k+2]=Math.min(255,px[k+2]+b);px[k+3]=255;
  }
  QP.layer.getContext('2d').putImageData(image,0,0);QP.layerKey=key;return QP.layer;
}
function qpDotXY(i,col,box,d) {const P=QP.pts;return [qpLerp(d.cx+(P.hx[i]-.5)*2*d.rx,box.x+P.cx[i]*box.w,col),qpLerp(d.cy+(P.hy[i]-.5)*2*d.ry,box.base-P.cy[i]*box.h,col)];}
function qpDrawDots(ctx) {
  const A=QP.anim, P=QP.pts;if(A.hay<=.01||!P) return;
  const layer=qpRenderLayer();
  if(layer) {ctx.save();ctx.globalCompositeOperation='lighter';ctx.drawImage(layer,0,0,QP.W,QP.H);ctx.restore();}
  const col=presEase(A.col);if(col<=.01) return;
  const box=qpHistBox(), d=qpDisc(), alpha=col*A.hay*(1-.6*A.dim);
  ctx.save();ctx.globalAlpha=alpha;
  // The rare high-score payments glow in their band's heat colour.
  for(let i=P.start[1]??P.n;i<P.n;i++) {const [x,y]=qpDotXY(i,col,box,d);presDot(ctx,x,y,1.4,qpHeat(P.bin[i]/(P.bins-1)),5);}
  ctx.font='500 10px ui-monospace,Menlo,monospace';ctx.textAlign='center';
  const bw=box.w/P.bins;
  QP.data.hist.forEach((count,b)=>{if(!count||(bw<34&&b%4&&b!==P.bins-1)) return;ctx.fillStyle=b?qpHeat(b/(P.bins-1)):'#9fd6ff';
    ctx.fillText(count>=1e4?compact(count):integer(count),box.x+(b+.5)*bw,box.base-P.heights[b]*box.h-9);});
  ctx.strokeStyle='rgba(127,157,181,.35)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(box.x,box.base+6);ctx.lineTo(box.x+box.w,box.base+6);ctx.stroke();
  ctx.fillStyle='#8aa1b4';[0,.5,1].forEach(v=>ctx.fillText(v.toFixed(1),box.x+v*box.w,box.base+22));
  ctx.textAlign='right';ctx.fillStyle='rgba(138,161,180,.7)';ctx.fillText('MODEL SCORE  →',box.x+box.w,box.base+38);
  ctx.restore();
}
function qpGridShape(k) {const n=Number(k);return n<=25?[5,5]:n<=50?[10,5]:n<=100?[10,10]:[25,10];}
function qpGridCells(box,k) {
  const [cols,rows]=qpGridShape(k), size=Math.max(Math.min(box.w/cols,box.h/rows),3), ox=box.x+(box.w-size*cols)/2, oy=box.y+(box.h-size*rows)/2;
  return Array.from({length:Number(k)},(_,i)=>({x:ox+(i%cols+.5)*size,y:oy+(Math.floor(i/cols)+.5)*size,s:Math.max(size*.74,2)}));
}
function qpBoxes() {
  const {W,H}=QP, top=Math.max(QP.storyBottom+24,80), bottom=H-36, narrow=W<820;
  const duelW=narrow?(W-48)/2:Math.min((W-200)/2,(bottom-top-70)*1.15);
  return {queue:narrow?{x:16,y:top,w:W-32,h:(bottom-top)*.55}:{x:W*.58,y:70,w:W*.42-36,h:H-130},
    rules:{x:narrow?16:W/2-50-duelW,y:top+18,w:duelW,h:bottom-top-90},model:{x:narrow?W/2+8:W/2+50,y:top+18,w:duelW,h:bottom-top-90},
    link:narrow?{x:16,y:top,w:W-32,h:(bottom-top)*.42}:{x:44,y:top,w:Math.min(W*.4,bottom-top),h:bottom-top}};
}
function qpModelCells() {
  const B=qpBoxes(), A=QP.anim, q=qpGridCells(B.queue,QP.k), m=qpGridCells(B.model,QP.k), l=qpGridCells(B.link,QP.k), du=presEase(A.duel), li=presEase(A.link);
  return q.map((c,i)=>({x:c.x+(m[i].x-c.x)*du+(l[i].x-c.x)*li,y:c.y+(m[i].y-c.y)*du+(l[i].y-c.y)*li,s:c.s+(m[i].s-c.s)*du+(l[i].s-c.s)*li}));
}
// Published hit counts revealed as a tally that fills over time (no per-payment labels exist).
function qpTally(now=qpNow()) {
  if(QP.step!==3||QP.anim.duel<.6) return {on:false,p:0};
  const p=motionAllowed()?presEase(presClamp((now-QP.tallyAt-900)/1800)):1;return {on:now-QP.tallyAt>900||!motionAllowed(),p};
}
function qpSquare(ctx,x,y,s,color,glow,round) {
  if(glow) {const mode=ctx.globalCompositeOperation;ctx.globalCompositeOperation='lighter';ctx.drawImage(presGlow(color),x-glow,y-glow,glow*2,glow*2);ctx.globalCompositeOperation=mode;}
  ctx.beginPath();if(round) ctx.arc(x,y,s*.36,0,Math.PI*2);else if(ctx.roundRect) ctx.roundRect(x-s/2,y-s/2,s,s,Math.min(4,s*.2));else ctx.rect(x-s/2,y-s/2,s,s);
  ctx.fillStyle=color;ctx.fill();
}
function qpDrawGrids(ctx,t) {
  const A=QP.anim;if(A.grid<=.01) return;
  const data=QP.data, k=QP.k, n=Number(k), rows=data.queue.slice(0,n), cells=qpModelCells(), tally=qpTally(), P=QP.pts;
  const box=qpHistBox(), mHits=qpHits(data.model[k],k), rHits=qpHits(data.rules[k],k), shownM=Math.round(mHits*tally.p), shownR=Math.round(rHits*tally.p);
  ctx.save();
  if(A.duel>.01) {
    const cellsR=qpGridCells(qpBoxes().rules,k);ctx.globalAlpha=A.grid*A.duel;
    data.rulesQueue.slice(0,n).forEach((row,i)=>{const c=cellsR[i];if(!c) return;
      const color=tally.on?(i<shownR?QP_COLOR.hit:QP_COLOR.miss):qpMix('#3a2a12',QP_COLOR.rules,.25+.75*presClamp((row.risk_score||0)/100));
      qpSquare(ctx,c.x,c.y,c.s,color,tally.on&&i<shownR?c.s*1.6:0,tally.on);});
  }
  rows.forEach((row,i)=>{
    const c=cells[i], u=presEase(presClamp(A.grid*1.6-i/n*.6)), seed=P?.seed[i]||[.5,.5], b=Math.min(data.hist.length-1,Math.floor(presClamp(Number(row.model_score))*data.hist.length));
    const sx=box.x+(b+.14+seed[0]*.72)/data.hist.length*box.w, sy=box.base-seed[1]*(P?.heights[b]||.2)*box.h, x=qpLerp(sx,c.x,u), y=qpLerp(sy,c.y,u);
    const dimmed=A.link>.5&&!row.ring, hit=tally.on&&i<shownM;
    const color=tally.on?(hit?QP_COLOR.hit:QP_COLOR.miss):A.link>.5&&row.ring?QP_COLOR.ring:qpHeat(row.model_score);
    ctx.globalAlpha=A.grid*(dimmed?.32:1);
    qpSquare(ctx,x,y,Math.max(c.s*(.3+.7*u),2),color,tally.on?(hit?c.s*1.6:0):dimmed?0:c.s*(i===QP.hover?2.4:A.link>.5?1.4:1.1),tally.on);
    if(i===QP.hover&&!tally.on) {ctx.strokeStyle='#e4edf4';ctx.lineWidth=1.5;ctx.strokeRect(x-c.s/2-2,y-c.s/2-2,c.s+4,c.s+4);}
  });
  if(A.duel>.05) {
    const B=qpBoxes(), label=(box,name,shown,color)=>{ctx.textAlign='center';ctx.font='600 11px ui-monospace,Menlo,monospace';ctx.fillStyle='#8aa1b4';
      ctx.fillText(`${name} · TOP ${integer(n)}`,box.x+box.w/2,box.y-8);
      ctx.font=`700 ${Math.round(presClamp(box.w/9,26,46))}px system-ui,-apple-system,"Segoe UI",sans-serif`;ctx.fillStyle=color;
      ctx.fillText(`${integer(tally.on?shown:0)} / ${integer(n)}`,box.x+box.w/2,box.y+box.h+46);};
    ctx.globalAlpha=A.duel;label(B.rules,'RULE BASELINE',shownR,shownR?QP_COLOR.hit:'#8aa1b4');label(B.model,'FROZEN MODEL',shownM,QP_COLOR.hit);
    if(tally.on) {ctx.font='500 10px ui-monospace,Menlo,monospace';ctx.fillStyle='rgba(138,161,180,.75)';ctx.fillText('RED = PUBLISHED COUNT OF LABELLED LAUNDERING, NOT PARTICULAR PAYMENTS',QP.W/2,B.model.y+B.model.h+70);}
    const cr=tally.on?shownR:0, cm=tally.on?shownM:0, text=`${cr}|${cm}`;
    if(text!==QP.counts) {QP.counts=text;const r=element('qp-count-r'), m=element('qp-count-m');if(r) r.textContent=integer(cr);if(m) m.textContent=integer(cm);}
  }
  ctx.restore();
}
function qpDrawChart(ctx) {
  const a=QP.anim.chart;if(a<=.01) return;
  const d=QP.data, ks=d.ks.map(Number), maxK=ks.at(-1), {W,H}=QP, x0=W<820?44:84, x1=W-(W<820?24:300), y1=H-62, y0=Math.max(QP.storyBottom+56,110);
  const X=v=>x0+v/maxK*(x1-x0), Y=v=>y1-v/maxK*(y1-y0), p=motionAllowed()?presEase(presClamp((qpNow()-QP.chartAt)/1700)):1, reach=p*maxK;
  ctx.save();ctx.globalAlpha=a;ctx.font='500 11px ui-monospace,Menlo,monospace';ctx.lineWidth=1;
  [0,.5,1].forEach(f=>{ctx.strokeStyle='rgba(127,157,181,.14)';ctx.beginPath();ctx.moveTo(x0,Y(f*maxK));ctx.lineTo(x1,Y(f*maxK));ctx.stroke();
    ctx.fillStyle='#8aa1b4';ctx.textAlign='right';ctx.fillText(integer(f*maxK),x0-10,Y(f*maxK)+4);});
  ctx.textAlign='center';[0,...ks].forEach(v=>ctx.fillText(integer(v),X(v),y1+20));
  ctx.fillStyle='rgba(138,161,180,.75)';ctx.textAlign='right';ctx.fillText('PAYMENTS REVIEWED  →',x1,y1+40);ctx.textAlign='left';ctx.fillText('LAUNDERING CAUGHT',x0-40,y0-14);
  const dashed=(to,color,text)=>{ctx.setLineDash([5,6]);ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(X(0),Y(0));ctx.lineTo(X(maxK),Y(to));ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle=color;ctx.textAlign='right';ctx.fillText(text,X(maxK)-6,Y(to)-8);};
  dashed(Math.min(maxK,d.positives),'rgba(228,237,244,.4)','perfect queue');dashed(maxK*d.positives/d.rows,'rgba(138,161,180,.5)','random picks');
  const series=(source,color,area)=>{
    const pts=[[0,0],...ks.map(k=>[k,qpHits(source[String(k)],k)])], path=[];
    for(let i=0;i<pts.length;i++) {const [kx,hy]=pts[i];if(kx<=reach) {path.push(pts[i]);continue;}const [px,py]=pts[i-1];path.push([reach,py+(hy-py)*(reach-px)/Math.max(kx-px,1e-9)]);break;}
    if(area&&path.length>1) {const g=ctx.createLinearGradient(0,y0,0,y1);g.addColorStop(0,presRgba(color,.32));g.addColorStop(1,presRgba(color,0));
      ctx.beginPath();ctx.moveTo(X(0),Y(0));path.forEach(([x,y])=>ctx.lineTo(X(x),Y(y)));ctx.lineTo(X(path.at(-1)[0]),Y(0));ctx.closePath();ctx.fillStyle=g;ctx.fill();}
    [[8,.18],[3,1]].forEach(([width,alpha])=>{ctx.beginPath();path.forEach(([x,y],i)=>i?ctx.lineTo(X(x),Y(y)):ctx.moveTo(X(x),Y(y)));ctx.strokeStyle=presRgba(color,alpha);ctx.lineWidth=width;ctx.lineJoin='round';ctx.stroke();});
    pts.slice(1).forEach(([kx,hy])=>{if(kx>reach) return;presDot(ctx,X(kx),Y(hy),4.5,color,16);ctx.fillStyle=color;ctx.textAlign='center';ctx.font='600 13px ui-monospace,Menlo,monospace';ctx.textAlign=area?'center':'left';ctx.fillText(integer(hy),X(kx)+(area?0:9),Y(hy)-(area?14:9));});
  };
  series(d.rules,QP_COLOR.rules,false);series(d.model,QP_COLOR.model,true);
  ctx.setLineDash([2,5]);ctx.strokeStyle='rgba(228,237,244,.35)';ctx.beginPath();ctx.moveTo(X(Number(QP.k)),y0);ctx.lineTo(X(Number(QP.k)),y1);ctx.stroke();ctx.setLineDash([]);
  ctx.font='500 11px ui-monospace,Menlo,monospace';ctx.fillStyle='#c4d3df';ctx.textAlign='left';ctx.fillText('YOUR CAPACITY',X(Number(QP.k))+6,y0+12);
  if(W>=820) {const lx=x1+36;[[QP_COLOR.model,'Frozen model'],[QP_COLOR.rules,'Rule baseline']].forEach(([color,name],i)=>{presDot(ctx,lx,y0+30+i*26,5,color,14);ctx.fillStyle='#c4d3df';ctx.textAlign='left';ctx.font='13px system-ui,sans-serif';ctx.fillText(name,lx+14,y0+34+i*26);});}
  ctx.restore();
}
function qpCaseBox() {
  const {W,H}=QP, top=Math.max(QP.storyBottom+16,70);
  return W<820?{x:20,y:top,w:W-40,h:Math.max(H-top-24,120)}:{x:40,y:top,w:W-40-340,h:Math.max(H-top-30,140)};
}
function qpCaseLayout() {
  const r=QP.replay, box=qpCaseBox(), S=[box.x+box.w*.17,box.y+box.h*.55], others=r.nodes.map((_,i)=>i).filter(i=>i>0), pos=[S];
  others.forEach((i,k)=>{const y=box.y+box.h*(k+.5)/Math.max(others.length,1), x=box.x+box.w*.84-Math.abs(y-S[1])/box.h*box.w*.14;pos[i]=[x,y];});
  return {box,pos,S,loop:Math.min(44,box.h*.11)};
}
function qpPayPoint(L,pay,u) {
  if(pay.to===0) {const a=Math.PI/2+u*Math.PI*2, c=[L.S[0],L.S[1]-L.loop-14];return [c[0]+Math.cos(a)*L.loop,c[1]+Math.sin(a)*L.loop];}
  const a=L.S, b=L.pos[pay.to];return presQuad(a,presCurve(a,b,.1),b,u);
}
function qpDrawCase(ctx,t) {
  const a=QP.anim.caseView;if(a<=.01||!QP.replay) return;
  const r=QP.replay, L=qpCaseLayout(), flight=qpFlight(), done=r.t>=r.t1+flight, maxGot=Math.max(...r.nodes.map(item=>item.got),1);
  ctx.save();ctx.globalAlpha=a;
  const used=new Set(r.pays.map(pay=>pay.to));
  if(used.has(0)) {ctx.strokeStyle='rgba(183,148,255,.28)';ctx.lineWidth=1.2;ctx.beginPath();ctx.arc(L.S[0],L.S[1]-L.loop-14,L.loop,0,Math.PI*2);ctx.stroke();
    ctx.font='500 10px ui-monospace,Menlo,monospace';ctx.fillStyle='rgba(195,166,255,.8)';ctx.textAlign='center';ctx.fillText('PAYS ITSELF',L.S[0],L.S[1]-L.loop*2-22);}
  used.forEach(to=>{if(!to) return;const b=L.pos[to], c=presCurve(L.S,b,.1), flagged=to===r.flagTo&&done;
    ctx.beginPath();ctx.moveTo(L.S[0],L.S[1]);ctx.quadraticCurveTo(c[0],c[1],b[0],b[1]);ctx.strokeStyle=flagged?presRgba(QP_COLOR.flag,.85):'rgba(127,157,181,.22)';ctx.lineWidth=flagged?2.6:1.2;ctx.stroke();});
  r.pays.forEach(pay=>{const u=(r.t-pay.at)/flight;if(u<0||u>=1) return;const [x,y]=qpPayPoint(L,pay,presEase(u));
    presDot(ctx,x,y,pay.flag?5.5:2.2+Math.sqrt(pay.usd/2e4)*1.6,pay.flag?QP_COLOR.flag:'#9fd6ff',pay.flag?28:12);});
  r.nodes.forEach((item,i)=>{
    const [x,y]=L.pos[i]||L.S, sender=i===0, flagged=i===r.flagTo, arrived=r.pays.some(pay=>pay.to===i&&r.t>=pay.at+flight&&r.t<pay.at+flight*1.4);
    const radius=sender?13:5+7*Math.sqrt(item.got/maxGot), color=sender?QP_COLOR.sender:flagged&&done?QP_COLOR.flag:QP_COLOR.receiver;
    presDot(ctx,x,y,radius*(arrived?1.25:1),color,sender?36:flagged&&done?30:arrived?22:12);
    if(item.ringRank) {ctx.strokeStyle=presRgba(QP_COLOR.ring,.9);ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,radius+6,0,Math.PI*2);ctx.stroke();}
    if(flagged&&done) {const phase=(t/1500)%1;ctx.beginPath();ctx.arc(x,y,radius*(1.4+phase*1.8),0,Math.PI*2);ctx.strokeStyle=presRgba(QP_COLOR.flag,.6*(1-phase));ctx.lineWidth=1.6;ctx.stroke();}
    ctx.font='500 11px ui-monospace,Menlo,monospace';ctx.fillStyle=sender?'#cfe3f2':'#a9bccb';ctx.textAlign=sender?'center':'left';
    const label=`${sender?'SENDER ':''}${shortAccount(item.id)}${item.ringRank?` · BQ2 ring #${item.ringRank}`:''}`;
    sender?ctx.fillText(label,x,y+radius+20):ctx.fillText(label,x+radius+10,y+4);
  });
  if(done) {const b=L.pos[r.flagTo]||L.S, m=presCurve(L.S,b,.1);ctx.font='700 12px ui-monospace,Menlo,monospace';ctx.fillStyle=QP_COLOR.flag;ctx.textAlign='center';
    ctx.fillText(`FLAGGED · ${presUsd(QP.row.amount_usd_asof)} · SCORE ${Number(QP.row.model_score).toFixed(3)}`,(L.S[0]+b[0])/2,m[1]-12);}
  ctx.restore();
}
function qpTimeline() {
  const canvas=element('qp-tl'), r=QP.replay;if(!canvas?.getContext||!canvas.width||!r) return;
  const g=canvas.getContext('2d'), w=canvas.width, h=canvas.height, D=QP.DPR, lo=r.t0-r.span*.04, hi=qpEnd(), X=v=>(v-lo)/(hi-lo)*w;
  g.clearRect(0,0,w,h);g.fillStyle='rgba(110,140,165,.25)';g.fillRect(0,h/2-1*D,w,2*D);
  g.fillStyle='rgba(63,208,184,.8)';g.fillRect(0,h/2-1*D,presClamp((r.t-lo)/(hi-lo))*w,2*D);
  r.pays.forEach(pay=>{const x=X(pay.at), on=pay.at<=r.t, tall=pay.flag?h-6*D:h*.55;g.fillStyle=pay.flag?QP_COLOR.flag:on?'#9fd6ff':'rgba(138,161,180,.55)';g.fillRect(x-(pay.flag?2:1)*D,(h-tall)/2,(pay.flag?4:2)*D,tall);});
  g.fillStyle='#e4edf4';g.fillRect(presClamp((r.t-lo)/(hi-lo))*w-D,0,2*D,h);
  const date=new Date(presClamp(r.t,r.t0,r.t1)), month=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][date.getUTCMonth()];
  element('qp-clock').innerHTML=`${month} ${date.getUTCDate()} · ${String(date.getUTCHours()).padStart(2,'0')}:${String(date.getUTCMinutes()).padStart(2,'0')}<small>UTC · ${date.getUTCFullYear()}</small>`;
}
function qpGlyph(id) {
  if(!QP.glyphs[id]) {const graph=QP.data.rings.graphs[id], ring={id,graph,nodes:graph.nodes.map(node=>[node.id])}, index=new Map(graph.nodes.map((node,i)=>[node.id,i]));
    QP.glyphs[id]={ring,layout:presLayout(ring),links:graph.links.map(link=>[index.get(link.source),index.get(link.target)]).filter(([x,y])=>x!=null&&y!=null)};}
  return QP.glyphs[id];
}
function qpDrawLink(ctx,t) {
  const a=QP.anim.link, data=QP.data;if(a<=.01||!data.rings) return;
  const n=Number(QP.k), rows=data.queue.slice(0,n), cells=qpModelCells(), {W,H}=QP, narrow=W<820, now=qpNow();
  const groups=[...new Set(rows.filter(row=>row.ring).map(row=>row.ring))].sort((x,y)=>(data.rings.ranks.get(x)??999)-(data.rings.ranks.get(y)??999));
  if(!groups.length) return;
  const area=narrow?{x:16,y:qpBoxes().link.y+qpBoxes().link.h+20,w:W-32,h:H-qpBoxes().link.y-qpBoxes().link.h-40}:{x:W*.5,y:Math.max(QP.storyBottom-60,70),w:W*.5-36,h:H-Math.max(QP.storyBottom-60,70)-30};
  const cols=narrow?Math.min(groups.length,5):groups.length<=4?2:groups.length<=9?3:4, gridRows=Math.ceil(groups.length/cols), cw=area.w/cols, ch=Math.min(area.h/gridRows,cw*.9);
  const touched=new Set(rows.filter(row=>row.ring).flatMap(row=>[qpAccount(row.from_bank,row.from_account),qpAccount(row.to_bank,row.to_account)]));
  const cellOf=k=>({x:area.x+(k%cols)*cw,y:area.y+Math.floor(k/cols)*ch,w:cw,h:ch}), centre=Object.fromEntries(groups.map((id,k)=>{const cell=cellOf(k);return [id,[cell.x+6,cell.y+cell.h/2-8]];}));
  ctx.save();
  // Links first, so the opaque ring cards hide any crossings.
  ctx.globalCompositeOperation='lighter';
  rows.forEach((row,i)=>{if(!row.ring||!centre[row.ring]) return;const u=presClamp((now-QP.linkAt-500-i*14)/900)*a;if(u<=0) return;
    const from=[cells[i].x+cells[i].s/2,cells[i].y], to=centre[row.ring], c=[qpLerp(from[0],to[0],.6),qpLerp(from[1],to[1],.15)], end=presQuad(from,c,to,presEase(u));
    ctx.globalAlpha=.4*a;ctx.strokeStyle=QP_COLOR.ring;ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(from[0],from[1]);
    for(let s=1;s<=16;s++) {const p=presQuad(from,c,to,presEase(u)*s/16);ctx.lineTo(p[0],p[1]);}ctx.stroke();
    if(u<1) presDot(ctx,end[0],end[1],2.4,QP_COLOR.ring,10);});
  ctx.globalCompositeOperation='source-over';
  groups.forEach((id,k)=>{
    const cell=cellOf(k), appear=presClamp((now-QP.linkAt-k*90)/500)*a;if(appear<=0) return;
    const glyph=qpGlyph(id), P=presFit(glyph.layout,{x:cell.x+12,y:cell.y+12,w:cell.w-24,h:cell.h-40},1.1).pts, hits=rows.filter(row=>row.ring===id).length;
    ctx.globalAlpha=appear;
    ctx.fillStyle='rgba(11,21,32,.94)';ctx.strokeStyle='rgba(109,85,168,.6)';ctx.lineWidth=1;ctx.beginPath();ctx.roundRect?ctx.roundRect(cell.x+4,cell.y+4,cell.w-8,cell.h-8,10):ctx.rect(cell.x+4,cell.y+4,cell.w-8,cell.h-8);ctx.fill();ctx.stroke();
    ctx.strokeStyle='rgba(127,157,181,.25)';ctx.lineWidth=.7;glyph.links.forEach(([x,y])=>{ctx.beginPath();ctx.moveTo(P[x][0],P[x][1]);ctx.lineTo(P[y][0],P[y][1]);ctx.stroke();});
    glyph.ring.nodes.forEach(([node],i)=>{const hot=touched.has(node);presDot(ctx,P[i][0],P[i][1],hot?3.6:1.9,hot?QP_COLOR.ring:'#4f6a80',hot?12*(1+.15*Math.sin(t/260+i)):0);});
    ctx.font='500 11px ui-monospace,Menlo,monospace';ctx.fillStyle='#c3a6ff';const rank=data.rings.ranks.get(id)??'?';
    if(cell.w<150) {ctx.textAlign='center';ctx.fillText(`#${rank}`,cell.x+cell.w/2,cell.y+cell.h-12);}
    else {ctx.textAlign='left';ctx.fillText(`BQ2 RING #${rank}`,cell.x+14,cell.y+cell.h-14);ctx.textAlign='right';ctx.fillStyle='#8aa1b4';ctx.fillText(plural(hits,'alert'),cell.x+cell.w-14,cell.y+cell.h-14);}
  });
  ctx.restore();
}
// The last step without a BQ2 cross-check: the top cases as tiles, each a small
// star of the sender's published payments with the flagged one highlighted.
const QP_WALL_MAX=100;
function qpWallGlyph(row) {
  const key=row.transaction_id;if(QP.wallGlyphs[key]) return QP.wallGlyphs[key];
  const sender=qpAccount(row.from_bank,row.from_account), flagged=qpAccount(row.to_bank,row.to_account), counts=new Map();
  (QP.data.histories[key]?.events||[]).forEach(event=>{const id=qpAccount(event.to_bank,event.to_account);counts.set(id,(counts.get(id)||0)+1);});
  counts.set(flagged,(counts.get(flagged)||0)+1);
  const self=counts.has(sender);counts.delete(sender);
  // Most-paid receivers first, at most 14 drawn; the flagged receiver always stays.
  let receivers=[...counts.entries()].sort((a,b)=>b[1]-a[1]).slice(0,14);
  if(!receivers.some(([id])=>id===flagged)&&flagged!==sender) receivers[receivers.length-1]=[flagged,counts.get(flagged)];
  receivers=receivers.sort((a,b)=>a[0]<b[0]?-1:1);
  return QP.wallGlyphs[key]={receivers,flagged,self:self||flagged===sender,extra:Math.max(counts.size-receivers.length,0)};
}
function qpDrawWall(ctx,t) {
  const a=QP.anim.wall;if(a<=.01) {QP.wallCells=[];return;}
  const data=QP.data, k=QP.k, n=Math.min(Number(k),QP_WALL_MAX), rows=data.queue.slice(0,n), {W,H}=QP, now=qpNow();
  const all=qpHits(data.model[k],k)>=Number(k), flagColor=all?QP_COLOR.hit:QP_COLOR.flag;
  // Wide screens: the story keeps the left column and the tiles fill the rest, edge to edge.
  const narrow=W<820, area=narrow?{x:16,y:presClamp(QP.storyBottom+14,80,H*.5),w:W-32}:{x:Math.min(W*.36,560),y:70,w:W-Math.min(W*.36,560)-24};area.h=H-area.y-(narrow?46:40);
  const cols=narrow?5:n<=25?5:n<=50?Math.min(10,Math.ceil(Math.sqrt(n*area.w/area.h))):10, gridRows=Math.ceil(n/cols), cw=area.w/cols, ch=Math.min(area.h/gridRows,cw*1.1);
  QP.wallCells=rows.map((row,i)=>({x:area.x+(i%cols)*cw,y:area.y+Math.floor(i/cols)*ch,w:cw,h:ch}));
  ctx.save();
  QP.wallCells.forEach((cell,i)=>{
    const appear=a*presClamp((now-QP.linkAt-i*14)/350);if(appear<=0) return;
    const row=rows[i], g=qpWallGlyph(row), hover=i===QP.hover, small=cell.w<110;ctx.globalAlpha=appear;
    ctx.fillStyle=hover?'rgba(63,208,184,.08)':'rgba(13,24,36,.55)';ctx.strokeStyle=hover?'rgba(63,208,184,.7)':'rgba(31,51,70,.8)';ctx.lineWidth=1;
    ctx.beginPath();ctx.roundRect?ctx.roundRect(cell.x+3,cell.y+3,cell.w-6,cell.h-6,8):ctx.rect(cell.x+3,cell.y+3,cell.w-6,cell.h-6);ctx.fill();ctx.stroke();
    const box={x:cell.x+10,y:cell.y+(small?20:22),w:cell.w-20,h:cell.h-(small?40:44)}, S=[box.x+box.w*.12,box.y+box.h/2], m=g.receivers.length;
    const pos=g.receivers.map((_,j)=>{const y=m===1?S[1]:box.y+box.h*(j+.5)/m;return [box.x+box.w*.9-Math.abs(y-S[1])/Math.max(box.h,1)*box.w*.18,y];});
    g.receivers.forEach(([id,count],j)=>{if(id===g.flagged) return;const b=pos[j], c=presCurve(S,b,.1);
      ctx.beginPath();ctx.moveTo(S[0],S[1]);ctx.quadraticCurveTo(c[0],c[1],b[0],b[1]);ctx.strokeStyle='rgba(127,157,181,.3)';ctx.lineWidth=.6+Math.min(Math.log1p(count)*.5,1.6);ctx.stroke();
      ctx.beginPath();ctx.arc(b[0],b[1],1.7,0,Math.PI*2);ctx.fillStyle='#6f8aa0';ctx.fill();});
    if(g.self) {ctx.beginPath();ctx.arc(S[0],S[1]-7,6,0,Math.PI*2);ctx.strokeStyle='rgba(183,148,255,.5)';ctx.lineWidth=1;ctx.stroke();}
    const fj=g.receivers.findIndex(([id])=>id===g.flagged);
    if(fj>=0) {const b=pos[fj], c=presCurve(S,b,.1), pulse=1+.15*Math.sin(t/260+i);
      ctx.beginPath();ctx.moveTo(S[0],S[1]);ctx.quadraticCurveTo(c[0],c[1],b[0],b[1]);ctx.strokeStyle=presRgba(flagColor,.9);ctx.lineWidth=1.8;ctx.stroke();
      presDot(ctx,b[0],b[1],2.8,flagColor,9*pulse);}
    presDot(ctx,S[0],S[1],small?3.2:4,QP_COLOR.sender,small?9:12);
    ctx.font='500 10px ui-monospace,Menlo,monospace';ctx.textAlign='left';ctx.fillStyle='#8aa1b4';ctx.fillText(`#${row.rank}`,cell.x+10,cell.y+16);
    ctx.textAlign='right';ctx.fillStyle='#5f7a8e';ctx.fillText(small?String(row.payment_format||'').slice(0,6):String(row.payment_format||''),cell.x+cell.w-10,cell.y+16);
    ctx.textAlign='center';ctx.fillStyle=all?'#ff8aa0':'#ffcf8a';ctx.fillText(presUsd(row.amount_usd_asof),cell.x+cell.w/2,cell.y+cell.h-10);
  });
  ctx.restore();
}
function qpFrame(t) {
  QP.raf=null;if(!QP.open) return;
  const n=QP.step, still=!motionAllowed();
  const wall=n===6&&!QP.data.rings;
  const target={hay:[1,1,QP.W<820?.3:1,0,0,0,0][n],col:n>=1?1:0,squeeze:n>=2?1:0,dim:n>=2?1:0,grid:n===2||n===3||(n===6&&!wall)?1:0,duel:n===3?1:0,chart:n===4?1:0,caseView:n===5?1:0,link:n===6&&!wall?1:0,wall:wall?1:0};
  for(const key in QP.anim) {const rate=key==='col'?.05:key==='grid'?.05:.1, d=target[key]-QP.anim[key];QP.anim[key]=still||Math.abs(d)<.002?target[key]:QP.anim[key]+d*rate;}
  const r=QP.replay;
  if(r?.playing&&n===5) {const dt=Math.min((t-(QP.last||t))/1000,.25), next=r.t+dt*r.span/r.dur*r.speed;if(next>=qpEnd()) {qpSeek(qpEnd());qpSetPlaying(false);} else qpSeek(next);qpTimeline();}
  QP.last=t;
  try {
    qpFreshCanvas();
    const ctx=element('qp-canvas').getContext('2d');
    ctx.setTransform(QP.DPR,0,0,QP.DPR,0,0);ctx.clearRect(0,0,QP.W,QP.H);
    qpDrawDots(ctx);qpDrawGrids(ctx,t);qpDrawChart(ctx);qpDrawCase(ctx,t);qpDrawLink(ctx,t);qpDrawWall(ctx,t);
  } catch(error) {if(!QP.warned) {QP.warned=true;console.error('BQ1 presenter frame failed',error);}}
  QP.raf=requestAnimationFrame(qpFrame);
}

// ---- controls ----
function qpCellAt(event) {
  if(!(QP.step===2||QP.step===3||QP.step===6)||qpTally().on) return -1;
  const box=element('qp-canvas').getBoundingClientRect(), x=event.clientX-box.left, y=event.clientY-box.top;
  if(QP.step===6&&!QP.data.rings) return QP.wallCells.findIndex(cell=>x>=cell.x&&x<cell.x+cell.w&&y>=cell.y&&y<cell.y+cell.h);
  return qpModelCells().findIndex(c=>Math.abs(x-c.x)<=c.s/2+2&&Math.abs(y-c.y)<=c.s/2+2);
}
function qpHideTip() {const tip=element('qp-tip');if(tip) tip.hidden=true;}
function qpShowTip(i,event) {
  const row=QP.data.queue[i], tip=element('qp-tip'), stage=element('qp-stage').getBoundingClientRect();
  tip.innerHTML=`<b>#${integer(row.rank)} · ${presUsd(row.amount_usd_asof)}</b> · ${escapeText(row.payment_format)} · ${escapeText(row.payment_currency)}<br>Score ${Number(row.model_score).toFixed(4)} · ${escapeText(String(row.timestamp).replace('T',' ').slice(0,16))}<br>${escapeText(shortAccount(qpAccount(row.from_bank,row.from_account)))} → ${escapeText(shortAccount(qpAccount(row.to_bank,row.to_account)))}${row.ring?`<br><span style="color:#c3a6ff">Touches BQ2 ring #${row.ringRank}</span>`:''}<br><small>Click to open the case</small>`;
  tip.hidden=false;const x=event.clientX-stage.left, y=event.clientY-stage.top;tip.style.left=`${Math.min(x+14,stage.width-280)}px`;tip.style.top=`${Math.max(y-90,8)}px`;
}
function qpOpenCase(row) {qpSetCase(row);qpCaseOptions();if(QP.step===5) {qpSetPlaying(motionAllowed());if(!motionAllowed()) qpSeek(qpEnd());qpUpdateText();qpTimeline();} else qpGo(5);}
function qpScrub(event) {
  const box=element('qp-tl').getBoundingClientRect(), r=QP.replay, lo=r.t0-r.span*.04, u=presClamp((event.clientX-box.left)/box.width);
  qpSeek(lo+u*(qpEnd()-lo));qpTimeline();
}
function qpFullscreen() {const promise=document.fullscreenElement?document.exitFullscreen?.():element('qp').requestFullscreen?.();promise?.catch?.(()=>{});}
function qpKey(event) {
  if(!QP.open||event.target?.matches?.('select,input')) return;
  if(event.target?.closest?.('button')&&(event.key===' '||event.key==='Enter')) return;
  if(event.key==='ArrowRight'||event.key==='PageDown') {event.preventDefault();qpGo(QP.step+1);}
  else if(event.key==='ArrowLeft'||event.key==='PageUp') {event.preventDefault();qpGo(QP.step-1);}
  else if(event.key===' ') {event.preventDefault();if(QP.step===5) qpSetPlaying(!QP.replay.playing);}
  else if(event.key==='r'||event.key==='R') qpReplayStep();
  else if(event.key==='f'||event.key==='F') qpFullscreen();
  else if(event.key==='Escape') {if(!document.fullscreenElement) closeBq1Presenter();}
  else if(/^[1-5]$/.test(event.key)) qpGo(QP_SCENE_START[+event.key-1]);
}
// R replays whatever the current step animates.
function qpReplayStep() {
  const A=QP.anim, n=QP.step;
  if(n===0) A.hay=0;if(n===1) A.col=0;if(n===2||n===6) A.grid=0;if(n===3) QP.tallyAt=qpNow();if(n===4) QP.chartAt=qpNow();if(n===6) QP.linkAt=qpNow();
  if(n===5) {qpSeek(QP.replay.t0-QP.replay.span*.04);qpSetPlaying(true);}
}
// A canvas whose GPU context was dropped (memory pressure) stays blank; swap in a fresh one.
function qpFreshCanvas() {
  const old=element('qp-canvas');if(!old?.getContext||!old.getContext('2d')?.isContextLost?.()) return false;
  const canvas=old.cloneNode(false);old.replaceWith(canvas);qpBindCanvas(canvas);QP.layer=null;QP.image=null;QP.layerKey='';qpResize();return true;
}
function qpBindCanvas(canvas) {
  canvas.onpointermove=event=>{const i=qpCellAt(event);QP.hover=i;canvas.style.cursor=i>=0?'pointer':'';if(i>=0) qpShowTip(i,event);else qpHideTip();};
  canvas.onpointerleave=()=>{QP.hover=-1;qpHideTip();};
  canvas.onclick=event=>{const i=qpCellAt(event);if(i>=0) qpOpenCase(QP.data.queue[i]);};
}
function qpBind() {
  element('qp-close').onclick=closeBq1Presenter;element('qp-fs').onclick=qpFullscreen;
  element('qp-next').onclick=()=>qpGo(QP.step+1);element('qp-back').onclick=()=>qpGo(QP.step-1);
  element('qp-scenes').onclick=event=>{const button=event.target.closest?.('[data-qp-step]');if(button) qpGo(+button.dataset.qpStep);};
  element('qp-ks').onclick=event=>{const button=event.target.closest?.('[data-k]');if(button) qpSetK(button.dataset.k);};
  element('qp-case').onchange=()=>qpOpenCase(qpRow(element('qp-case').value));
  element('qp-quick').onclick=event=>{const button=event.target.closest?.('[data-rank]');if(button) qpOpenCase(qpRow(button.dataset.rank));};
  element('qp-play').onclick=()=>qpSetPlaying(!QP.replay.playing);
  element('qp-speed').onclick=()=>{const r=QP.replay;r.speed=r.speed===1?2:r.speed===2?.5:1;element('qp-speed').textContent=`${r.speed}×`;};
  const tl=element('qp-tl'), canvas=element('qp-canvas');
  tl.onpointerdown=event=>{tl.setPointerCapture?.(event.pointerId);qpSetPlaying(false);qpScrub(event);tl.onpointermove=qpScrub;};
  tl.onpointerup=()=>{tl.onpointermove=null;};
  qpBindCanvas(canvas);
  document.addEventListener?.('keydown',qpKey);
  if(typeof ResizeObserver==='function') new ResizeObserver(()=>{if(QP.open) qpResize();}).observe(element('qp-stage'));
  else window.addEventListener?.('resize',()=>{if(QP.open) qpResize();});
}
