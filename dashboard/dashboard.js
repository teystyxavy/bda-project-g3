'use strict';
let snapshot;
let activeView='aml';
let snapshotLoad=0;
const element = id => document.getElementById(id);
const integer = value => Number(value).toLocaleString('en-US');
const percent = value => value == null ? 'Unavailable' : `${(value * 100).toFixed(2)}%`;
const compact = value => new Intl.NumberFormat('en-US', {notation:'compact',maximumFractionDigits:2}).format(value);
const dollars = value => `$${compact(value)}`;
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

function selectDashboardView(view) {
  activeView=view;
  for(const name of ['aml','incoming','treasury','ingestion']) {
    element(name).hidden=name!==view || (['aml','treasury'].includes(name) && !snapshot);
    element(`${name}-tab`).setAttribute('aria-pressed',String(name===view));
  }
  const live=['ingestion','incoming'].includes(view);
  element('status').hidden=live;element('methods').hidden=live;
  if(view==='treasury' && snapshot) renderTreasury();
}
for(const view of ['aml','incoming','treasury','ingestion']) element(`${view}-tab`).onclick=()=>selectDashboardView(view);

function cards(target, items) {
  element(target).innerHTML = items.map(([label,value,detail]) => `<div class="kpi"><span>${escapeText(label)}</span><strong>${escapeText(value)}</strong><span>${escapeText(detail)}</span></div>`).join('');
}

function table(target, headings, rows) {
  element(target).innerHTML = `<table><thead><tr>${headings.map(label=>`<th scope="col">${escapeText(label)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(value=>`<td>${escapeText(value)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function bars(target, rows, label, value, formatter=integer) {
  if (!rows.length) { element(target).textContent='No observations in this period.'; return; }
  const maximum = Math.max(...rows.map(row=>row[value]),1);
  element(target).innerHTML = `<div class="bars">${rows.map(row=>`<div class="bar-row" title="${escapeText(label(row))}: ${escapeText(formatter(row[value]))}"><span class="bar-label">${escapeText(label(row))}</span><div class="bar-track"><div class="bar-fill" style="width:${row[value]/maximum*100}%"></div></div><span class="bar-value">${escapeText(formatter(row[value]))}</span></div>`).join('')}</div>`;
}

function trend(target, rows, field, formatter, selected) {
  const width=560, height=225, left=66, top=18, plotWidth=460, plotHeight=157;
  const maximum = field==='hhi' ? 1 : Math.max(...rows.map(row=>row[field] ?? 0),1);
  const start=Date.parse(rows[0].day), end=Date.parse(rows[rows.length-1].day);
  const xvalue=row=>left+(Date.parse(row.day)-start)/Math.max(end-start,1)*plotWidth;
  const yvalue=row=>top+plotHeight-(row[field]??0)/maximum*plotHeight;
  const points=rows.filter(row=>row[field]!=null).map(row=>`${xvalue(row)},${yvalue(row)}`).join(' ');
  const ticks=[0,.5,1].map(fraction=>`<line x1="${left}" x2="${left+plotWidth}" y1="${top+plotHeight-fraction*plotHeight}" y2="${top+plotHeight-fraction*plotHeight}" stroke="#dce6eb"/><text x="${left-8}" y="${top+plotHeight-fraction*plotHeight+4}" text-anchor="end" font-size="11" fill="#526879">${escapeText(formatter(maximum*fraction))}</text>`).join('');
  const dates=rows.filter((row,index)=>index===0||index===rows.length-1||index%4===0).map(row=>`<text x="${xvalue(row)}" y="202" text-anchor="middle" font-size="11" fill="#526879">Sep ${Number(row.day.slice(-2))}</text>`).join('');
  const marks=rows.filter(row=>row[field]!=null).map(row=>`<circle tabindex="0" cx="${xvalue(row)}" cy="${yvalue(row)}" r="${row.day===selected?6:3.5}" fill="${row.day===selected?'#bd7819':'#168477'}"><title>${escapeText(row.day)}: ${escapeText(formatter(row[field]))}</title></circle>`).join('');
  element(target).innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeText(field)} by day">${ticks}<polyline points="${points}" fill="none" stroke="#168477" stroke-width="2.5"/>${marks}${dates}</svg><details><summary>Daily values</summary><div id="${target}-values"></div></details>`;
  table(`${target}-values`,['Date',field==='hhi'?'HHI':'USD volume'],rows.map(row=>[row.day,row[field]==null?'Unavailable':formatter(row[field])]));
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
  const window=[...snapshot.splits[period]], end=snapshot?.pipeline?.end_date || snapshot?.treasury_daily?.at(-1)?.day;
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
  const end=snapshot?.pipeline?.end_date || snapshot?.treasury_daily?.at(-1)?.day || '2022-09-18';
  const date=new Date(end+'T00:00:00Z');
  const finish=date.getUTCMonth()===8 ? String(date.getUTCDate()) : date.toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
  return `Later period · Sep 11–${finish}`;
}
function updateEvaluationLabels() {
  const names=periodNames();
  for(const option of element('split').options) {
    option.textContent=evaluationPeriodLabel(option.value);
    option.hidden=!names.includes(option.value);
  }
  if(!names.includes(element('split').value)) element('split').value=names.includes('test')?'test':names[0];
  if(snapshot?.splits) {
    const train=windowText(snapshot.splits.train);
    element('evaluation-methods').textContent=`Training reference: ${train}; ${names.map(name=>`${name}: ${windowText(periodWindow(name))}`).join('; ')}. Evaluation views show separate chronological populations. Rolling history before ${monthDay(snapshot.splits.train[0])} is unavailable.`;
    element('model-methods').textContent=element('model-methods').textContent.replace(/trained on [^;]*;/,`trained on ${train};`);
    element('validation-window').textContent=element('validation-window').textContent.replace(/^.*?only\./,`${windowText(periodWindow('validation'))} only.`);
    return;
  }
  element('evaluation-methods').textContent=`Training reference: Sep 1–6; validation: Sep 7–8; test: Sep 9–10; ${evaluationPeriodLabel('tail').replace(' · ', ': ')}. Evaluation views show separate chronological populations. Rolling history before Sep 1 is unavailable.`;
}

function renderScoringCoverage() {
  const periods=periodNames(), trainText=snapshot?.splits?windowText(snapshot.splits.train):'Sep 1–6';
  const counts=periods.map(period=>snapshot?.model?.views?.[period]?.metrics?.rows);
  const total=snapshot?.full_rows;
  const evaluated=counts.reduce((sum,count)=>sum+count,0);
  const valid=snapshot?.aml_scope==='full' && Number.isSafeInteger(total) &&
    counts.every(count=>Number.isSafeInteger(count) && count>=0) && evaluated<=total;
  element('scoring-coverage').hidden=!valid;
  if(!valid) return;
  const reference=total-evaluated;
  element('scoring-coverage-title').textContent=`All ${integer(total)} transactions scored`;
  element('scoring-coverage-summary').textContent='The frozen AML model scored every transaction in this dataset. The evaluation dropdown shows separate later-date populations, so its counts are smaller than the full scoring population.';
  cards('scoring-coverage-kpis',[
    ['Transactions scored',integer(total),'All transaction dates in this run'],
    ['Transactions in evaluation',integer(evaluated),snapshot.splits?`${monthDay(snapshot.splits[periods[0]][0])} onward · across all ${periods.length} periods`:'Sep 7 onward · across all three periods'],
    ['Scored, excluded from evaluation',integer(reference),`${trainText} · training-reference dates`]]);
  const medium=snapshot.pipeline?.subset!==undefined && snapshot.pipeline.subset!=='HI-Small' && !snapshot.pipeline.train;
  element('scoring-coverage-explanation').textContent=`Why exclude ${trainText}?`+' The original model used these dates for training; the dashboard preserves that training-reference window and reports later dates separately for evaluation. These earlier rows still received scores.'+
    (medium?' The model was trained on HI-Small and reused for HI-Medium without retraining. This is transfer evaluation; the synthetic populations may overlap, so it is not independent evidence of generalisation.':' Training-reference scores are not included in the reported evaluation metrics.');
  table('scoring-coverage-periods',['Date period','Transactions scored','Evaluation coverage'],[
    [trainText,integer(reference),'Excluded · training-reference dates'],
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

function renderTreasuryLegacy() {
  const selection=element('day').value, current=snapshot.treasury[selection];
  cards('treasury-kpis',[['Transactions',integer(current.transactions),'Full dataset · selected period'],['USD-equivalent volume',dollars(current.volume_usd),'Historical same-day FX'],['Top-five bank-pair share',percent(current.top5_share),`${integer(current.bank_pairs)} directed pairs`],['Bank-pair HHI',current.hhi==null?'Unavailable':current.hhi.toFixed(4),'0–1 · larger = more concentrated']]);
  bars('banks',current.banks,row=>`${row.from_bank} → ${row.to_bank}`,'volume_usd',dollars);
  bars('currencies',current.currencies.slice(0,15),row=>`${row.payment_currency} → ${row.receiving_currency}`,'volume_usd',dollars);
  trend('volume-trend',snapshot.treasury_daily,'volume_usd',dollars,selection);
  trend('hhi-trend',snapshot.treasury_daily,'hhi',value=>value.toFixed(2),selection);
  table('corridors',['From bank','To bank','Transactions','USD volume','Period share'],current.banks.map(row=>[row.from_bank,row.to_bank,integer(row.transactions),dollars(row.volume_usd),percent(row.share)]));
  element('bank-share').textContent=`Shown top ${current.banks.length} pairs account for ${percent(current.banks.reduce((sum,row)=>sum+row.share,0))} of selected-period volume. Shares and HHI use all pairs, including those outside this table.`;
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
    element('status').textContent=`Snapshot generated ${snapshot.generated_at.slice(0,10)} · AML ${fullAml ? 'full population' : 'cohort'} ${integer(snapshot.cohort_rows)} rows · Treasury ${integer(snapshot.full_rows)} rows`;
    if (fullAml) {
      element('aml-scope').textContent='Retrospective full-population queue. Rule scores are priorities, not probabilities.';
      element('score-scope').textContent='All transactions in the selected period.';
      element('method-scope').textContent='Both views cover the full HI-Small population. Features come from the verified AWS Glue run; rule scoring and snapshot export run locally.';
    }
    if (snapshot.model) {
      element('scoring-control').hidden=false;element('model-comparison').hidden=false;element('model-methods').hidden=false;
      element('aml-scope').textContent='Retrospective full-population queues. Compare frozen-model ranking with the rule baseline; neither score is a probability.';
      if(snapshot.model.inference_source==='aws-glue') {
        element('method-scope').textContent='Features and model scores come from verified AWS Glue runs. Rule scoring, explanation reconstruction and dashboard export run locally. Treasury uses the full population.';
      }
      element('scoring').onchange=renderAml;
    }
    if(snapshot.pipeline) {
      const p=snapshot.pipeline;
      element('status').textContent=`Run ${p.run_id} · ${integer(snapshot.full_rows)} transactions · ${p.start_date} to ${p.end_date} · Dataset version ${p.dataset_version??'unrecorded local copy'} · Refreshed ${p.refresh_time}`;
      const scope=p.source_scope==='smoke-prefix' ? 'Demonstration sample' : `Selected ${p.subset || 'HI-Small'} population`;
      element('method-scope').textContent=`${scope}. Both views use the same verified transaction run. ${snapshot.model?.evaluation_scope || 'Optional training creates a candidate for separate review.'}`;
      element('day').options[0].textContent='All transaction dates';
      document.querySelector('#treasury .heading p').textContent=`${integer(snapshot.full_rows)} transactions in this run. High volume and concentration do not imply suspicious activity.`;
      document.querySelectorAll('#treasury article p').forEach(node=>{
        if(node.textContent.startsWith('Full 18-day context')) node.textContent='Full transaction-date context; summary selection is highlighted.';
      });
      document.querySelector('footer').textContent='Group 3 · Verified batch analysis';
    }
    element('day').options.length=1;
    snapshot.treasury_daily.forEach(row=>{const option=document.createElement('option');option.value=row.day;option.textContent=row.day;element('day').appendChild(option);});
    element('split').onchange=renderAml;element('day').onchange=renderTreasury;
    element('export-queue').onclick=()=>download(currentQueue().queue,`queue-${isModelQueue()?'model':'rules'}-${element('split').value}.csv`);
    element('export-corridors').onclick=()=>download(snapshot.treasury[element('day').value].banks,`corridors-${element('day').value}.csv`);
    table('variants',['Variant','Precision@100','Recall@100','Average precision'],snapshot.diagnostics.variants.map(row=>[row.variant,percent(row.precision_at_k),percent(row.recall_at_k),row.average_precision==null?'Unavailable':row.average_precision.toFixed(4)]));
    table('reasons',['Score','Reasons','Reviewed','Positives','False positives'],snapshot.diagnostics.top100_reasons.map(row=>[row.risk_score,row.risk_reasons,row.reviewed,row.positives,row.false_positives]));
    element('provenance').textContent=JSON.stringify({generated_at:snapshot.generated_at,pipeline:snapshot.pipeline,sources:snapshot.sources,provenance:snapshot.provenance,model:snapshot.model?{name:snapshot.model.name,provenance:snapshot.model.provenance}:undefined},null,2);
    selectDashboardView(activeView);renderAml();return true;
  } catch(error) {if(loadId===snapshotLoad) element('status').textContent=`Unable to load dashboard: ${error.message}. Serve this folder over HTTP and rebuild the snapshot if needed.`;return false;}
}
start();

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
    const full=element('ingestion-size').value==='full';
    element('ingestion-rows-field').hidden=element('ingestion-size').value!=='custom';
    element('ingestion-preview').disabled=!login || submitting || pending;
    element('ingestion-train').disabled=!full;
    if(!full) element('ingestion-train').checked=false;
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
      const stage=run.stages.find(item=>item.id===node.dataset.stage);
      node.dataset.state=stage?.status||'pending';
      node.querySelector('.map-status').textContent=stageLabels[stage?.status]||'Waiting';
    });
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
    if(ok) {loadedRelease=run.release_url;element('ingestion-run-note').textContent='Verified results loaded. Open AML review or Treasury corridors to explore this run.';}
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
    element('ingestion-title').textContent=config?'Ingest data. Follow every stage.':'Follow every stage in AWS.';
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
    if(rows!==null && (!Number.isInteger(rows)||rows<1||rows>5100000)) throw new Error('Choose a transaction limit from 1 to 5,100,000.');
    if(version!==null && (!Number.isInteger(version)||version<1||version>100000)) throw new Error('Choose a positive whole-number dataset version.');
    if(element('ingestion-subset').value!=='HI-Small') throw new Error('Cloud ingestion currently supports HI-Small only.');
    return {subset:'HI-Small',rows,dataset_version:version,refresh:element('ingestion-refresh').checked,
      rerun:element('ingestion-rerun').checked,train:mode==='full'&&element('ingestion-train').checked};
  }
  element('ingestion-preview').onclick=()=>{
    try {
      const options=selectedRunOptions();
      let command='.\\scripts\\ingestion\\ingest.ps1 -NoWait';
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
  const states={waiting:'Waiting for events',running:'Live · scoring',paused:'Paused',completed:'Replay complete',failed:'Consumer failure'};
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
  const parity=data=>{const value=data.verification;
    return value && value.features_exact===true && Number.isSafeInteger(value.transactions) &&
      Number.isFinite(value.max_absolute_score_difference) && typeof value.verified_on==='string'?value:null;};
  element('incoming-tab').hidden=true;

  function valid(data) {
    return data?.schema_version===1 && data.run_id===config.run_id && Object.hasOwn(states,data.state) &&
      typeof data.generated_at==='string' && Number.isSafeInteger(data.processed) && Number.isSafeInteger(data.expected) &&
      Array.isArray(data.queue) && Array.isArray(data.latest) && Array.isArray(data.progress) && data.progress.length>0;
  }

  function render(data) {
    const old=stale(data), share=data.expected?data.processed/data.expected:0;
    element('incoming-state').textContent=old?'Stale':states[data.state];
    element('incoming-state').dataset.state=old||data.state==='failed'?'failed':data.state==='running'?'running':'';
    element('incoming-message').textContent=old?`No successful update for ${Math.round(age(data)/60)} min. The last published scores are shown.`:{
      waiting:'Waiting for the first replayed transaction to be scored.',
      running:`Updated ${ago(age(data))}. Committed scores appear here within about 15 seconds; this page checks every 10 seconds.`,
      paused:'The Kinesis consumer is disabled, so nothing new is arriving. Scores committed so far are shown.',
      completed:`All ${integer(data.expected)} replayed transactions and ${integer(data.senders.expected)} sender histories reconcile with the reviewed replay input.`,
      failed:'The streaming consumer reported record failures. Committed scores are shown; later events may be held back until the failure is resolved.'}[data.state];
    const cutoff=review(data), verified=parity(data);
    cards('incoming-kpis',[['Transactions scored',`${integer(data.processed)} / ${integer(data.expected)}`,`${(share*100).toFixed(1)}% of the replay cohort`],
      ['Complete sender histories',`${integer(data.senders.complete)} / ${integer(data.senders.expected)}`,'Rolling features need each sender’s full history'],
      ['Highest ranking score',data.max_score==null?'—':Number(data.max_score).toFixed(4),'Uncalibrated · not a probability'],
      ['Replay position',eventTime(data.replay_position),'Historical event time · September 2022'],
      ...(cutoff?[['Review now',`${integer(cutoff.flagged)} / ${integer(data.processed)}`,`Score ≥ ${cutoff.cutoff.toFixed(4)} · batch top-${integer(cutoff.capacity)} cutoff`]]:[]),
      ...(verified?[['Matches batch model',`${integer(verified.transactions)} / ${integer(verified.transactions)}`,
        `Max score difference ${verified.max_absolute_score_difference.toExponential(1)} · recorded ${verified.verified_on}`]]:[])]);
    const bar=element('incoming-progressbar');
    bar.setAttribute('aria-valuenow',String(data.processed));bar.setAttribute('aria-valuemax',String(data.expected));
    bar.innerHTML=`<i style="width:${(share*100).toFixed(1)}%"></i>`;
    // Up to ~24 points keeps the shared chart's axis labels readable; first and last are always kept.
    const step=Math.ceil(data.progress.length/24), points=data.progress.filter((point,index,all)=>index%step===0||index===all.length-1);
    chartLines('incoming-progress',[{name:'Transactions scored',color:'#14786f',rows:points.map(point=>({x:Date.parse(point.at),y:point.processed,
      label:clock(point.at)}))}],Math.max(1,data.expected),null,`Time scored · ${new Date(points[0].at).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})}`,integer);
    // Review column only when the snapshot carries the batch cutoff.
    const flag=row=>cutoff?[row.review_now===true?'Review now':'Below cutoff']:[];
    table('incoming-latest',['Event time','Scored at','Transaction','Sender → receiver','Ranking score',...(cutoff?['Review']:[]),'Payment','Prior-day USD'],
      data.latest.map(row=>[eventTime(row.timestamp),row.scored_at?clock(row.scored_at):'—',row.transaction_id,parties(row),Number(row.model_score).toFixed(4),...flag(row),row.payment_format,dollars(row.amount_usd_asof)]));
    if(!data.latest.length) element('incoming-latest').textContent='No transactions scored yet.';
    const reviewNote=cutoff?` ${cutoff.flagged?`${integer(cutoff.flagged)} reach`:'None reach'} the batch top-${integer(cutoff.capacity)} review cutoff (score ≥ ${cutoff.cutoff.toFixed(4)}, set on ${cutoff.period} validation days).`:'';
    element('incoming-queue-note').textContent=`Top ${integer(data.queue.length)} of ${integer(data.processed)} transactions scored so far; ties use event time then source row. Scores rank review priority and are not probabilities.${reviewNote}`;
    table('incoming-queue',['Rank','Transaction','Event time','Sender → receiver','Ranking score',...(cutoff?['Review']:[]),'Payment / currency','Prior-day USD','Sender transactions 1h / 24h / 7d','Counterparties 24h / 7d'],
      data.queue.map(row=>[row.rank,row.transaction_id,eventTime(row.timestamp),parties(row),Number(row.model_score).toFixed(4),...flag(row),`${row.payment_format} / ${row.payment_currency}`,dollars(row.amount_usd_asof),
        `${row.txn_count_1h} / ${row.txn_count_24h} / ${row.txn_count_7d}`,`${row.distinct_counterparties_24h} / ${row.distinct_counterparties_7d}`]));
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
const signedPercent = value => `${value>=0?'+':''}${(value*100).toFixed(2)} pp`;
function projectionRecord(id) {
  const data=snapshot.exploration.aml;
  return Object.fromEntries(data.record_fields.map((field,index)=>[field,data.records[id][index]]));
}
function selectedPopulation() {
  return snapshot.exploration.aml.periods[element('split').value].scopes.find(item=>item.id===element('aml-population').value)
    || snapshot.exploration.aml.periods[element('split').value].scopes[0];
}
function selectedCapacity() {return element('review-capacity').value||'100';}
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
  element('corridor-picker').onchange=renderCorridor;
  element('treasury-reset').onclick=()=>{element('day').value='all';element('corridor-picker').value='0';renderTreasury();};
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
function chartLines(target,series,maxY=1,maxX=null,xLabel='Date',yFormatter=percent) {
  const width=600,height=240,left=72,top=18,w=485,h=168;
  const valid=series.flatMap(item=>item.rows).filter(row=>row.y!=null && Number.isFinite(row.y));
  if(!valid.length) {element(target).textContent='No measurements for this selection.';return;}
  const minX=xLabel==='Recall'?0:Math.min(...valid.map(row=>row.x)), endX=maxX??Math.max(...valid.map(row=>row.x));
  const x=row=>left+(row.x-minX)/Math.max(endX-minX,1e-9)*w, y=row=>top+h-row.y/Math.max(maxY,1e-9)*h;
  const ticks=[0,.5,1].map(f=>`<line x1="${left}" x2="${left+w}" y1="${top+h-f*h}" y2="${top+h-f*h}" stroke="#dce6eb"/><text x="${left-8}" y="${top+h-f*h+4}" text-anchor="end">${escapeText(yFormatter(f*maxY))}</text>`).join('');
  const paths=series.map(item=>`<polyline points="${item.rows.filter(row=>row.y!=null).map(row=>`${x(row)},${y(row)}`).join(' ')}" fill="none" stroke="${item.color}" stroke-width="2.5"/>${item.rows.filter(row=>row.y!=null).map(row=>`<circle tabindex="0" cx="${x(row)}" cy="${y(row)}" r="3" fill="${item.color}"><title>${escapeText(item.name)} · ${escapeText(row.label)}: ${escapeText(yFormatter(row.y))}</title></circle>`).join('')}`).join('');
  const sample=series[0].rows.filter((row,index,rows)=>index===0||index===rows.length-1||index%4===0);
  const dates=xLabel==='Recall'?[0,.5,1].map(f=>`<text x="${left+f*w}" y="210" text-anchor="middle">${percent(f)}</text>`).join(''):
    sample.map(row=>`<text x="${x(row)}" y="210" text-anchor="middle">${escapeText(row.label)}</text>`).join('');
  element(target).innerHTML=`<div class="chart-legend">${series.map(item=>`<span><i style="background:${item.color}"></i>${escapeText(item.name)}</span>`).join('')}</div><svg class="analysis-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeText(xLabel)} comparison">${ticks}${paths}${dates}<text x="${left+w/2}" y="235" text-anchor="middle">${escapeText(xLabel)}</text></svg>`;
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
    element('model-comparison').hidden=!snapshot?.model;
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
  cards('aml-kpis',[['Transactions in population',integer(metrics.rows),`${population.label} · ${evaluationPeriodLabel(period)}`],['Review capacity',integer(metrics.k),`Top ${k} · chronological tie policy`],
    [`Precision@${metrics.k}`,percent(metrics.precision_at_k),`${integer(metrics.hits)} label-positive / ${integer(metrics.k)} reviewed`],
    [`Recall@${metrics.k}`,percent(metrics.recall_at_k),`${integer(metrics.positives)} labelled positives in population`]]);
  const pipeline=snapshot.pipeline||{};
  element('aml-context').textContent=`${population.label} · ${evaluationPeriodLabel(period)} · ${model?'Frozen model':'Rule baseline'} · Dataset ${pipeline.dataset_version??'unversioned'} · ${pipeline.source_scope==='smoke-prefix'?'Demo sample':'Full verified source'} · Model ${snapshot.model.provenance.model_sha256.slice(0,12)} · Data publication ${pipeline.refresh_time||snapshot.generated_at}. ${snapshot.exploration.aml.filter_policy}`;
  element('aml-note').textContent=`Average precision ${metrics.average_precision==null?'Unavailable':metrics.average_precision.toFixed(4)} · lift@${metrics.k} ${metrics.lift_at_k==null?'Unavailable':metrics.lift_at_k.toFixed(2)+'×'}. Scores rank review priority; they are not probabilities.`+(period==='tail'?' Later-period evaluation; compare separately from the main test period.':' Retrospective evaluation; labels are absent from investigation records.');
  element('investigation-panel').hidden=amlMode!=='investigation';
  element('model-comparison').hidden=amlMode!=='evaluation';
  element('evaluation-charts').hidden=amlMode!=='evaluation';
  element('validation-diagnostics').hidden=amlMode!=='evaluation';
  element('investigation-mode').setAttribute('aria-pressed',String(amlMode==='investigation'));
  element('evaluation-mode').setAttribute('aria-pressed',String(amlMode==='evaluation'));
  element('model-comparison').querySelector('p').textContent=`Same selected population, ${period} period and review capacity ${k}. Precision = positive labels / reviewed; recall = captured positives / all positives.`;
  table('comparison',['Scorer','Label-positive in queue',`Precision@${k}`,`Recall@${k}`,'Lift','Average precision'],['model','rules'].map(kind=>{
    const m=population[kind].metrics_by_k[k];return [kind==='model'?'Frozen model':'Rules',`${m.hits} / ${m.k}`,percent(m.precision_at_k),percent(m.recall_at_k),m.lift_at_k==null?'Unavailable':m.lift_at_k.toFixed(2)+'×',m.average_precision==null?'Unavailable':m.average_precision.toFixed(4)];}));
  const alertArticle=element('alerts').closest('article'), scoreArticle=element('scores').closest('article');
  alertArticle.querySelector('h3').textContent='Selected review queue through time';
  alertArticle.querySelector('p').textContent=`Top ${k} for the entire selected period and population, grouped by date; these are review selections, not a model alert threshold.`;
  bars('alerts',data.daily_by_k[k],row=>row.day,'reviewed');
  scoreArticle.querySelector('h3').textContent=model?'Model score distribution':'Rule score distribution';
  scoreArticle.querySelector('p').textContent='Every transaction in the selected population; queue-search filters do not change this chart.';
  bars('scores',data.histogram,row=>row.label,'transactions');
  chartLines('pr-chart',['model','rules'].map((kind,i)=>({name:kind==='model'?'Frozen model':'Rules',color:i?'#b5823d':'#14786f',rows:population[kind].pr_curve.map(row=>({x:row.recall,y:row.precision,label:`Recall ${percent(row.recall)}`}))})),1,1,'Recall');
  chartLines('capacity-chart',['model','rules'].map((kind,i)=>({name:kind==='model'?'Frozen model':'Rules',color:i?'#b5823d':'#14786f',rows:Object.entries(population[kind].metrics_by_k).map(([capacity,m])=>({x:Number(capacity),y:m.hits,label:capacity}))})),Math.max(1,...['model','rules'].flatMap(kind=>Object.values(population[kind].metrics_by_k).map(m=>m.hits))),null,'Transactions reviewed',integer);
  const allRows=data.queue.map(projectionRecord);
  selectOptions('queue-payment',['',...new Set(allRows.map(row=>row.payment_format))],value=>value||'All formats');
  selectOptions('queue-currency',['',...new Set(allRows.map(row=>row.payment_currency))],value=>value||'All currencies');
  element('detail').hidden=true;renderQueue();
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
  element('queue-note').textContent=`Top ${k} from every transaction in ${population.label.toLowerCase()}. Select a transaction to inspect evidence.`;
  element('queue-coverage').textContent=`Showing ${rows.length} of ${Math.min(Number(k),data.queue.length)} ranked rows. Search, payment, currency and amount controls narrow this exported queue; they do not search all transactions or recompute population metrics. Population presets above do.`;
  table('queue',['Rank','Transaction','Event time','Sender → receiver','Ranking score','Payment / currency','Prior-day USD','Evidence'],rows.map(row=>[row.rank,row.transaction_id,row.timestamp.replace('T',' ').slice(0,16),`${row.from_bank} → ${row.to_bank}`,model?Number(row.model_score).toFixed(4):row.risk_score,`${row.payment_format} / ${row.payment_currency}`,dollars(row.amount_usd_asof),model?(row.model_evidence||'Inspect behaviour signals'):row.risk_reasons||'No rule triggered']));
  element('queue').querySelectorAll('tbody tr').forEach((row,index)=>{const button=document.createElement('button');button.textContent=rows[index].transaction_id;button.onclick=()=>showDetail(rows[index]);row.children[1].replaceChildren(button);});
  element('export-queue').onclick=()=>download(displayedQueue,`queue-${model?'model':'rules'}-${element('split').value}-${k}.csv`);
}
function showDetail(row) {
  if(!snapshot?.exploration) {showDetailLegacy(row);return;}
  const history=snapshot.exploration.aml.histories[row.transaction_id], model=isModelQueue();
  const parts=(row.model_evidence||'').split('; ').filter(Boolean).map(part=>{const colon=part.lastIndexOf(':');return {label:part.slice(0,colon),value:Number(part.slice(colon+1).replace('margin','').trim())};}).filter(part=>Number.isFinite(part.value));
  const maximum=Math.max(.01,...parts.map(part=>Math.abs(part.value)));
  element('detail').hidden=false;
  element('detail').innerHTML=`<div class="heading"><h3>Transaction ${escapeText(row.transaction_id)}</h3><button id="close-detail">Close evidence</button></div><div class="transfer-path"><span>Sender<br><strong>${escapeText(row.from_bank)} / ${escapeText(row.from_account)}</strong></span><span aria-hidden="true">→</span><span>Receiver<br><strong>${escapeText(row.to_bank)} / ${escapeText(row.to_account)}</strong></span></div><div class="evidence-facts"><span><small>Event time</small>${escapeText(row.timestamp.replace('T',' '))}</span><span><small>Original payment</small>${row.amount_paid==null?'Amount not retained in this export':escapeText(integer(row.amount_paid))} ${escapeText(row.payment_currency)}</span><span><small>Prior-day USD valuation</small>${dollars(row.amount_usd_asof)}</span><span><small>Payment format</small>${escapeText(row.payment_format)}</span><span><small>Prior hour</small>${integer(row.txn_count_1h)} outgoing transactions</span><span><small>Prior 24 hours</small>${integer(row.distinct_counterparties_24h)} counterparties</span></div><h3>${model?'Signed model contributions':'Rule evidence'}</h3>${model?(parts.length?`<div class="contribution-list">${parts.map(part=>`<div><span>${escapeText(part.label)}</span><div class="signed-track"><i style="${part.value>=0?'left:50%':'right:50%'};width:${Math.abs(part.value)/maximum*48}%;background:${part.value>=0?'#14786f':'#b5823d'}"></i></div><strong>${part.value>=0?'+':''}${part.value.toFixed(4)}</strong></div>`).join('')}</div><p class="field-help">Displayed contributions sum to ${parts.reduce((sum,p)=>sum+p.value,0).toFixed(4)}; bias ${Number(row.explanation_bias_margin).toFixed(4)}; remaining features ${(Number(row.explanation_total_margin)-Number(row.explanation_bias_margin)-parts.reduce((sum,p)=>sum+p.value,0)).toFixed(4)}; full margin ${Number(row.explanation_total_margin).toFixed(4)}. Positive contributions push the ranking score up. These are model explanations, not causal proof or probabilities.</p>`:'<p>Signed explanations are exported for the whole-period top 250. This filtered-population record shows observed behaviour signals; an exact model contribution is unavailable.</p>'):`<p>${escapeText(row.risk_reasons||'No rule triggered')}</p>`}<h3>Account activity preceding this transaction</h3>${history?`<p>${integer(history.observed_prior_transactions)} preceding outgoing transactions observed since the dataset began. Latest ${history.events.length} shown; no later activity is included.</p><div id="account-timeline" class="table-wrap"></div>`:'<p>Account history is exported for the whole-period model/rule top 250. This record is outside that bounded history projection.</p>'}`;
  if(history) table('account-timeline',['Event time','Receiver bank / account','Payment','Prior-day USD'],history.events.map(event=>[event.timestamp.replace('T',' ').slice(0,16),`${event.to_bank} / ${event.to_account}`,event.payment_format,dollars(event.amount_usd_asof)]));
  element('close-detail').onclick=()=>{element('detail').hidden=true;};
}
function renderTreasury() {
  renderTreasuryLegacy();
  if(!snapshot?.exploration) {element('treasury-explore').hidden=true;element('treasury-context').hidden=true;return;}
  initialiseExploration();element('treasury-explore').hidden=false;element('treasury-context').hidden=false;
  const data=snapshot.exploration.treasury, period=element('day').value, current=snapshot.treasury[period];
  element('treasury-context').textContent=`${period==='all'?'All transaction dates':period} · ${integer(current.transactions)} transactions · ${dollars(current.volume_usd)} USD-equivalent · Same-day historical FX · Source ${snapshot.pipeline?.run_id||'verified batch'} · Volume describes treasury exposure.`;
  element('corridor-coverage').textContent=data.coverage;
  selectOptions('corridor-picker',data.corridors.map((row,index)=>String(index)),value=>{const row=data.corridors[Number(value)];return `${row.from_bank} → ${row.to_bank}`;});
  const nodes=data.heatmap_nodes, cells=data.heatmaps[period], lookup=new Map(cells.map(row=>[JSON.stringify([row.source,row.target]),row]));
  const max=Math.max(1,...cells.map(row=>row.volume_usd));
  element('bank-heatmap').innerHTML=`<table class="heatmap"><thead><tr><th>Send ↓ / receive →</th>${nodes.map(node=>`<th>${escapeText(node)}</th>`).join('')}</tr></thead><tbody>${nodes.map(source=>`<tr><th>${escapeText(source)}</th>${nodes.map(target=>{const row=lookup.get(JSON.stringify([source,target]));const amount=row?.volume_usd||0;const intensity=amount/max;const index=data.corridors.findIndex(item=>item.from_bank===source&&item.to_bank===target);return `<td style="background:rgba(43,108,154,${.04+intensity*.85})"><button ${index<0?'disabled':''} data-corridor="${index}" title="${escapeText(source)} → ${escapeText(target)}: ${dollars(amount)}, ${integer(row?.transaction_count||0)} transactions" style="color:${intensity>.55?'white':'#183d52'}">${amount?compact(amount):'0'}</button></td>`;}).join('')}</tr>`).join('')}</tbody></table><p class="field-help">Cell labels are USD volume. Linear colour scale; darker = larger volume. Residual groups and untracked corridors have no individual drilldown.</p>`;
  element('bank-heatmap').querySelectorAll('button[data-corridor]').forEach(button=>{button.onclick=()=>{element('corridor-picker').value=button.dataset.corridor;renderCorridor();};});
  chartLines('share-trend',[{name:'Top-one share',color:'#2b6c9a',rows:data.daily_shares.map(row=>({x:Date.parse(row.day),y:row.top1_share,label:row.day.slice(5)}))},{name:'Top-five share',color:'#8971b0',rows:data.daily_shares.map(row=>({x:Date.parse(row.day),y:row.top5_share,label:row.day.slice(5)}))}],1);
  const comparison=data.comparisons[period];
  element('movers-period').textContent=comparison?`${comparison.label}. Share changes use all bank pairs and each period’s total volume. This is a descriptive comparison; it does not identify a cause.`:'No earlier observed period is available for comparison.';
  if(comparison) table('corridor-movers',['Sender','Receiver','Previous USD','Current USD','USD change','Share change'],comparison.movers.map(row=>[row.from_bank,row.to_bank,dollars(row.previous),dollars(row.current),`${row.volume_change>=0?'+':''}${dollars(row.volume_change)}`,signedPercent(row.share_change)]));
  else element('corridor-movers').textContent='Unavailable for the first observed day.';
  renderCorridor();renderCurrencyFlow();
  element('corridors').querySelectorAll('tbody tr').forEach((tr,index)=>{const row=current.banks[index];const button=document.createElement('button');button.textContent=row.from_bank;button.onclick=()=>{const selected=data.corridors.findIndex(item=>item.from_bank===row.from_bank&&item.to_bank===row.to_bank);if(selected>=0){element('corridor-picker').value=String(selected);renderCorridor();}};tr.firstChild.replaceChildren(button);});
}
function renderCorridor() {
  const row=snapshot.exploration.treasury.corridors[Number(element('corridor-picker').value||0)];
  if(!row) return;
  const period=element('day').value, selected=period==='all'?row.daily:row.daily.filter(item=>item.day===period);
  const volume=selected.reduce((sum,item)=>sum+item.volume_usd,0), count=selected.reduce((sum,item)=>sum+item.transactions,0);
  cards('corridor-kpis',[['Selected corridor',`${row.from_bank} → ${row.to_bank}`,period==='all'?'All observed dates':period],['USD volume',dollars(volume),'Same-day historical FX'],['Transactions',integer(count),'Selected period'],['Share of period volume',percent(snapshot.treasury[period].volume_usd?volume/snapshot.treasury[period].volume_usd:0),'All bank pairs in denominator']]);
  chartLines('corridor-volume',[{name:'USD volume',color:'#2b6c9a',rows:row.daily.map(item=>({x:Date.parse(item.day),y:item.volume_usd,label:item.day.slice(5)}))}],Math.max(1,...row.daily.map(item=>item.volume_usd)),null,'Date',dollars);
  chartLines('corridor-share',[{name:'Daily share',color:'#8971b0',rows:row.daily.map(item=>({x:Date.parse(item.day),y:item.share,label:item.day.slice(5)}))}],Math.max(.01,...row.daily.map(item=>item.share)));
  bars('corridor-currencies',row.currencies,item=>`${item.payment_currency} → ${item.receiving_currency}`,'volume_usd',dollars);
  table('corridor-transactions',['Date','Transactions','Average USD'],row.daily.map(item=>[item.day,integer(item.transactions),item.transactions?dollars(item.volume_usd/item.transactions):'Unavailable']));
}
function renderCurrencyFlow() {
  const current=snapshot.treasury[element('day').value], flows=current.currencies.slice(0,8).map(row=>({...row}));
  const rest=current.currencies.slice(8).reduce((sum,row)=>sum+row.volume_usd,0);
  if(rest>0) flows.push({payment_currency:'Other',receiving_currency:'Other',volume_usd:rest});
  const max=Math.max(1,...flows.map(row=>row.volume_usd));
  element('currency-flow').innerHTML=`<svg class="currency-flow" viewBox="0 0 950 ${70+flows.length*43}" role="img" aria-label="Directed currency flows, width proportional to USD volume"><text x="10" y="20">Payment currency</text><text x="690" y="20">Receiving currency</text>${flows.map((row,index)=>{const y=55+index*43;return `<g data-flow="${index}" tabindex="0" role="button" aria-label="${escapeText(row.payment_currency)} to ${escapeText(row.receiving_currency)}, ${dollars(row.volume_usd)}"><text x="10" y="${y+4}">${escapeText(row.payment_currency)}</text><path d="M170 ${y} C300 ${y-8},550 ${y+8},670 ${y}" fill="none" stroke="#729db9" stroke-width="${row.volume_usd/max*26}"/><text x="690" y="${y+4}">${escapeText(row.receiving_currency)}</text><text x="925" y="${y+4}" text-anchor="end">${dollars(row.volume_usd)}</text><title>${escapeText(row.payment_currency)} → ${escapeText(row.receiving_currency)}: ${dollars(row.volume_usd)}</title></g>`;}).join('')}</svg><p class="field-help">Ribbon widths use the same linear volume scale. Other combines the remaining flows; it is not a currency.</p>`;
  element('currency-flow-detail').hidden=true;
  element('currency-flow').querySelectorAll('[data-flow]').forEach(mark=>{const activate=()=>{const row=flows[Number(mark.dataset.flow)];element('currency-flow-detail').hidden=false;if(row.payment_currency==='Other'){element('currency-flow-detail').textContent='Other aggregates flows outside the eight shown; select an individually labelled flow for daily values.';return;}const values=snapshot.treasury_daily.map(day=>{const match=snapshot.treasury[day.day].currencies.find(item=>item.payment_currency===row.payment_currency&&item.receiving_currency===row.receiving_currency);return [day.day,dollars(match?.volume_usd||0),integer(match?.transactions||0)];});element('currency-flow-detail').innerHTML=`<h3>${escapeText(row.payment_currency)} → ${escapeText(row.receiving_currency)}</h3><div id="currency-daily" class="table-wrap"></div>`;table('currency-daily',['Date','USD volume','Transactions'],values);};mark.onclick=activate;mark.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activate();}};});
}
