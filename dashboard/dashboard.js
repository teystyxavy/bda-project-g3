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
  for(const name of ['aml','incoming','network','ingestion']) {
    element(name).hidden=name!==view || (['aml','network'].includes(name) && !snapshot);
    element(`${name}-tab`).setAttribute('aria-pressed',String(name===view));
  }
  const live=['ingestion','incoming'].includes(view);
  element('status').hidden=live;element('methods').hidden=live;
  if(view==='network' && snapshot) renderNetwork();
}
for(const view of ['aml','incoming','network','ingestion']) element(`${view}-tab`).onclick=()=>selectDashboardView(view);

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
    if(ok) {loadedRelease=run.release_url;element('ingestion-run-note').textContent='Verified results loaded. Open AML review or Mule networks to explore this run.';}
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
      waiting:'Waiting for the first incoming transaction to be scored.',
      running:`Updated ${ago(age(data))}. Committed scores appear here within about 15 seconds; this page checks every 10 seconds.`,
      paused:'The Kinesis consumer is disabled, so nothing new is arriving. Scores committed so far are shown.',
      completed:`All ${integer(data.expected)} incoming transactions and ${integer(data.senders.expected)} sender histories reconcile with the reviewed input.`,
      failed:'The streaming consumer reported record failures. Committed scores are shown; later events may be held back until the failure is resolved.'}[data.state];
    const cutoff=review(data), verified=parity(data);
    cards('incoming-kpis',[['Transactions scored',`${integer(data.processed)} / ${integer(data.expected)}`,`${(share*100).toFixed(1)}% of expected transactions`],
      ['Complete sender histories',`${integer(data.senders.complete)} / ${integer(data.senders.expected)}`,'Rolling features need each sender’s full history'],
      ['Highest ranking score',data.max_score==null?'—':Number(data.max_score).toFixed(4),'Uncalibrated · not a probability'],
      ['Latest event time',eventTime(data.replay_position),'Historical event time · September 2022'],
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
function flowRoles(graph) {
  const sends=new Set(graph.links.map(link=>link.source)), receives=new Set(graph.links.map(link=>link.target));
  return Object.fromEntries(graph.nodes.map(node=>[node.id,node.role!=='counterparty'?node.role:
    sends.has(node.id)&&!receives.has(node.id)?'sender':receives.has(node.id)&&!sends.has(node.id)?'receiver':'both']));
}

function renderNetwork() {
  const data=snapshot?.network;
  element('network-body').hidden=!data;element('network-empty').hidden=Boolean(data);
  if(!data) {
    element('network-context').textContent=`${integer(snapshot?.full_rows||0)} transactions in this release.`;
    element('network-empty').textContent='This release was published before the mule-network analysis. Run the pipeline again to publish BQ2 network results.';
    return;
  }
  const t=data.totals, p=data.parameters, headline=data.thresholds.find(row=>row.headline)||data.thresholds[0];
  const three=data.loops.three_account, rings=data.rings;
  element('network-question').textContent=data.question;
  element('network-context').innerHTML=`${data.verification?.status==='passed'?'<span class="verified-chip">✓ Verified</span> Glue tables matched an independent recomputation · ':''}${escapeText(integer(t.accounts))} accounts · ${escapeText(integer(t.links))} account links after merging repeat payments · ${escapeText(integer(t.self_transfers))} self-transfers excluded`;
  if(snapshot.pipeline) {const p=snapshot.pipeline; element('network-context').innerHTML=`${escapeText(p.subset)} · ${escapeText(p.start_date)} to ${escapeText(p.end_date)} · ${escapeText(integer(t.rows))} transactions · `+element('network-context').innerHTML;}
  cards('network-kpis',[
    ['Rings to investigate',integer(rings.rings),`${integer(rings.accounts)} accounts · largest ${integer(rings.largest)}`],
    ['Ring accounts touching laundering',share(rings.laundering_rate),`${times(rings.lift)} the ${share(t.laundering_rate)} base rate`],
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
  element('signal-bars').innerHTML=`<div class="signal-bars">${signals.map(([label,row,kind])=>`<div class="signal-row${kind?` signal-${kind}`:''}"><span>${escapeText(label)}<small>${escapeText(integer(row.accounts))} accounts</small></span><div class="bar-track"><div class="bar-fill" style="width:${(row.laundering_rate||0)/top*100}%"></div></div><strong>${escapeText(share(row.laundering_rate))}<small>${kind==='base'?'base':escapeText(times(row.lift))}</small></strong></div>`).join('')}</div>`;
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
    element('ring-reset').onclick=()=>{if(selectedRing){delete ringLayouts[selectedRing];stopRingScene();ringScene=null;renderRing();}};
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
  element('ring-rail').innerHTML=`<ol>${data.ring_list.map(row=>`<li><button type="button" data-ring="${escapeText(row.ring_id)}" title="Ring ${escapeText(row.ring_id)}"><span class="rail-rank">#${row.rank}</span><span class="rail-main"><strong>${escapeText(shortAccount(leadAccount(row.ring_id)))}</strong><span>${row.hubs?`<b class="flag flag-hub">${plural(row.hubs,'hub')}</b>`:''}${row.loops?`<b class="flag flag-loop">${plural(row.loops,'loop')}</b>`:''}</span><small>${integer(row.accounts)} accounts · ${dollars(row.usd)}</small></span></button></li>`).join('')}</ol>`;
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
  element('ring-summary').textContent=row.hubs?`${plural(row.hubs,'hub')} collecting from ${plural(senders,'sender')} and paying ${plural(receivers,'receiver')}${row.loops?`, plus ${plural(row.loops,'timed loop')}`:''}. Treat it as one case.`
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
// the picture reads "money in → hub → money out".
function layoutRing(graph) {
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
  const graph=snapshot.network.graphs[selectedRing];
  if(!graph) return;
  GRAPH=graphShape();
  if(ringLayoutShape[selectedRing]!==GRAPH.width) {delete ringLayouts[selectedRing];ringLayoutShape[selectedRing]=GRAPH.width;}
  const layout=ringLayouts[selectedRing]||(ringLayouts[selectedRing]=layoutRing(graph));
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
    view:same?previous.view:null,hover:null,hoverLink:null,drag:null,gesture:null,settled:!animate,raf:null,last:0,geometry:[]};

  const gradients=graph.links.map((link,i)=>`<linearGradient id="ring-grad-${i}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="${RING_GLOW[roles[link.source]]}"/><stop offset="1" stop-color="${RING_GLOW[roles[link.target]]}"/></linearGradient>`).join('');
  const marker=name=>`<marker id="ring-arrow-${name}" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto"><path d="M1 1 L9 5 L1 9" fill="none" stroke="${RING_TONE[name]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></marker>`;
  const linkMarkup=graph.links.map((link,i)=>`<path data-link="${i}" class="ring-link ring-link-${tone(link)}" stroke="url(#ring-grad-${i})" stroke-width="${(1.2+3*Math.log1p(link.txns)/Math.log1p(maxTxns)).toFixed(2)}" marker-mid="url(#ring-arrow-${tone(link)})"/>`).join('');
  const hitMarkup=graph.links.map((link,i)=>`<path data-hit="${i}" class="ring-hit"/>`).join('');
  const dotMarkup=dots.map((dot,i)=>`<circle data-dot="${i}" class="ring-dot ring-dot-${tone(graph.links[dot.link])}" r="${dot.r.toFixed(1)}" fill="${RING_TONE[tone(graph.links[dot.link])]}"/>`).join('');
  const nodeMarkup=graph.nodes.map(node=>{const role=roles[node.id], r=radius[node.id], minor=node.role==='counterparty';
    return `<g class="ring-node ring-node-${role}${node.id===selectedAccount?' ring-node-selected':''}" data-node="${escapeText(node.id)}" tabindex="0" role="button" aria-label="${escapeText(ROLE[role].label)} ${escapeText(node.id)}, ${integer(node.fan_in)} senders, ${integer(node.fan_out)} receivers">${node.role==='hub'?`<circle class="ring-halo" r="${(r+3).toFixed(1)}"/>`:''}<circle class="ring-grab" r="${(r+7).toFixed(1)}"/><circle class="ring-core" r="${r.toFixed(1)}" fill="${RING_GLOW[role]}" filter="url(#ring-glow)"/><text class="ring-label${minor?' ring-label-minor':''}" y="${(node.role==='hub'?r+17:-r-7).toFixed(1)}" text-anchor="middle">${escapeText(shortAccount(node.id))}</text></g>`;}).join('');
  const lanes=hasHub?`<text class="ring-lane" x="${margin-10}" y="${height-16}">← MONEY IN</text><text class="ring-lane" x="${width-margin+10}" y="${height-16}" text-anchor="end">MONEY OUT →</text>`:'';
  const crowded=graph.nodes.filter(node=>node.role==='loop').length>8;
  element('ring-graph').innerHTML=`<svg id="ring-svg" class="${[crowded&&'ring-crowded',!animate&&'ring-static',ringFlowPaused&&'ring-paused'].filter(Boolean).join(' ')}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Money flows between ${graph.shown_accounts} accounts of the selected ring"><defs><filter id="ring-glow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="3.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>${gradients}${['in','out','other','loop'].map(marker).join('')}</defs><rect class="ring-backdrop" width="${width}" height="${height}"/>${lanes}<g id="ring-viewport"><g class="ring-links">${linkMarkup}</g><g class="ring-hits">${hitMarkup}</g><g class="ring-dots" filter="url(#ring-glow)">${dotMarkup}</g><g class="ring-accounts">${nodeMarkup}</g></g></svg>`;
  element('ring-graph-note').textContent=`${graph.truncated?`Showing ${graph.shown_accounts} of ${graph.total_accounts} accounts: every hub and loop account, then counterparties by payment volume. `:''}${animate?'Moving dots are payments, travelling the way the money went; more dots means more payments, bigger dots more USD.':'Arrows show the direction of the money.'} Larger accounts moved more money. ${crowded?'Loop-account names appear on hover. ':''}Hover to trace an account, ${animate?'drag one to pull on its links':'drag to rearrange'}, drag the background to pan.`;
  element('ring-flow').hidden=!animate;element('ring-legend-flow').hidden=!animate;
  bindRingScene(scene);
  if(selectedAccount&&byId.has(selectedAccount)) showAccount(selectedAccount);
  else element('ring-node').innerHTML='<h3>Account details</h3><p>Select an account in the graph.</p>';
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
  const c={x:(a.x+b.x)/2+dy*bend,y:(a.y+b.y)/2-dx*bend};
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
    const graph=net.graphs[row.ring_id], roles=flowRoles(graph), index=new Map(graph.nodes.map((node,i)=>[node.id,i]));
    return {rank:row.rank,id:row.ring_id,hubs:row.hubs,loops:row.loops,usd:row.usd,rate:row.laundering_rate??0,graph,
      start:Date.parse(String(graph.payment_start)+'Z'),total:graph.payments_total??graph.payments.length,
      nodes:graph.nodes.map(node=>[node.id,roles[node.id],node.touches_laundering?1:0]),
      links:graph.links.map(link=>[index.get(link.source),index.get(link.target),link.txns,link.usd]),
      pays:graph.payments,tri:(graph.loops||[]).map(loop=>loop.map(id=>index.get(id))).filter(loop=>loop.every(i=>i!=null))};
  });
  const low=net.thresholds.reduce((a,b)=>a.threshold<=b.threshold?a:b), head=net.thresholds.find(row=>row.headline)||net.thresholds[0];
  return {rings,totals:net.totals,summary:net.rings,low,head,candidates:low.hubs.accounts+low.high_volume.accounts,
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
  switch(step) {
    case 0:return `<div class="pres-eyebrow">This release · ${integer(t.rows)} payments</div><h2>${integer(t.accounts)} accounts. A few hundred of them are working together.</h2><p>Every dot is one real account. Where it sits means nothing yet. The job: find the ones that move money as a team.</p>`;
    case 1:return `<div class="pres-eyebrow">Step 1 · fan-in and fan-out</div><h2><em>${integer(data.candidates)}</em> accounts collect from ${data.low.threshold}+ senders and pay ${data.low.threshold}+ receivers.</h2><p>Counted over ${integer(t.links)} account-to-account links. Pooling money from many and passing it on to many is what a mule does.</p>`;
    case 2:return `<div class="pres-eyebrow">Step 2 · hubs, loops, rings</div><h2>${integer(s.accounts)} accounts fall into <em>${integer(s.rings)} rings</em>.</h2><p>Hubs with ${data.headline}+ senders and ${data.headline}+ receivers, timed loops (A → B → C → A within ${data.loopDays} days), and the accounts they deal with. Each cluster is one case to review. Ring #1 sits in the middle.</p>`;
    case 3:return `<div class="pres-eyebrow">Ring #${ring.rank} · lead account ${escapeText(ring.nodes[0]?.[0]||ring.id)}</div><h2>${ring.hubs?`${ring.hubs===1?'One hub':`${ring.hubs} hubs`}, ${integer(n-ring.hubs)} other accounts, <em>${presUsd(ring.usd)}</em> moved.`:`${integer(n)} accounts passing money in <em>${plural(ring.loops,'timed loop')}</em>.`}</h2><p>${integer(ring.pays.length)}${ring.total>ring.pays.length?` of ${integer(ring.total)}`:''} real payments, replayed in the order they happened. ${ring.hubs?'Senders sit on the left and receivers on the right. Watch the hub collect, then pay out.':`Each purple flash is a loop closing: money leaves an account and comes back within ${data.loopDays} days.`}</p>`;
    case 4:return dirty?`<div class="pres-eyebrow">Ring #${ring.rank} · ground truth</div><h2><span class="pres-dirty" id="pres-count">${revealed}</span> of ${integer(n)} accounts touched a laundering payment.</h2><p>The detector never saw these labels. They come with the dataset and are only shown after the ring was found.</p>`
      :`<div class="pres-eyebrow">Ring #${ring.rank} · ground truth</div><h2><span id="pres-count">${revealed}</span> of ${integer(n)} accounts touched a laundering payment.</h2><p>Dense structure is not proof. Payroll, payment processors and escrow accounts look like this too, so an analyst still makes the call.</p>`;
    case 5:return `<div class="pres-eyebrow">Does it hold up?</div><h2>${presPct(t.laundering_rate)} of all accounts touch laundering. Inside the rings it's <span class="pres-dirty">${presPct(s.laundering_rate)}</span>.</h2><p>That is ${s.lift==null?'well above':`${s.lift.toFixed(0)}×`} the base rate, found without using a single label.</p>`;
    default:return `<div class="pres-eyebrow">The case list</div><h2>The top ${integer(data.rings.length)} rings, ranked.</h2><p>Red accounts touched a laundering payment. Click any ring to replay it.</p>`;
  }
}

function openPresenter() {
  const net=snapshot?.network;if(!presenterAvailable(net)) return;
  PRES.data=presenterData(net);PRES.layouts={};PRES.hay=null;PRES.open=true;PRES.returnFocus=document.activeElement;
  const pick=element('pres-ring');selectOptions('pres-ring',PRES.data.rings.map(ring=>String(ring.rank)),rank=>{const ring=PRES.data.rings.find(item=>String(item.rank)===rank);return `#${ring.rank} · ${ring.nodes.length} accounts · ${ring.hubs?plural(ring.hubs,'hub'):plural(ring.loops,'loop')} · ${presUsd(ring.usd)}`;});
  pick.onchange=()=>presChooseRing(+pick.value);
  const loopRing=PRES.data.rings.find(ring=>ring.loops&&!ring.hubs), clean=PRES.data.rings.find(ring=>ring.rate===0);
  element('pres-quick').innerHTML=[[PRES.data.rings[0],'biggest'],[loopRing,'loops'],[clean,'clean']].filter(([ring])=>ring)
    .map(([ring,label])=>`<button type="button" data-rank="${ring.rank}">#${ring.rank} ${label}</button>`).join('');
  element('pres-quick').onclick=event=>{const button=event.target.closest('[data-rank]');if(button) presChooseRing(+button.dataset.rank);};
  presSetRing(PRES.data.rings[0]);pick.value=String(PRES.data.rings[0].rank);
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
  hc.putImageData(image,0,0);PRES.hay=hay;
  // Rings as a sunflower in rank order, ring #1 in the centre.
  const sizes=data.sizes, golden=Math.PI*(3-Math.sqrt(5)), step=Math.min(d.ry*.96/Math.sqrt(sizes.length),d.rx*.96/Math.sqrt(sizes.length)/1.15), clusters=[];
  let at=0;
  sizes.forEach((size,k)=>{
    const rr=step*Math.sqrt(k+.35), cx=d.cx+Math.cos(k*golden)*rr*1.15, cy=d.cy+Math.sin(k*golden)*rr, spread=step*.085*Math.sqrt(size);
    for(let j=0;j<size&&at<P.ringDots.length;j++) {const q=Math.sqrt((j+.5)/size)*spread;clusters[at++]=[cx+Math.cos(j*golden)*q,cy+Math.sin(j*golden)*q];}
  });
  PRES.clusters=clusters;PRES.clusterCenter=[d.cx,d.cy];
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
  const left=Math.min(W*.3,470);return {x:40+left*.35,y:70,w:W-300-60-left*.35,h:H-110};
}
function presNodeColor(ring,i) {const p=presRevealP(i);return p>=.5?(ring.nodes[i][2]?PRES_COLOR.dirty:PRES_COLOR.clean):PRES_COLOR[ring.nodes[i][1]];}

function presDrawHaystack(ctx,t) {
  const A=PRES.anim, alpha=1-A.zoom;if(alpha<=.01||!PRES.hay) return;
  ctx.save();
  if(A.zoom>0) {const z=1+presEase(A.zoom)*14, [cx,cy]=PRES.clusterCenter;ctx.translate(cx,cy);ctx.scale(z,z);ctx.translate(-cx,-cy);}
  ctx.globalAlpha=alpha*A.bg;ctx.drawImage(PRES.hay,0,0,PRES.W,PRES.H);ctx.globalAlpha=alpha;
  const P=PRES.points, xy=PRES.hayXY;
  if(A.cand>.01) P.cand.forEach((i,k)=>{const [x,y]=xy(i);presDot(ctx,x,y,1.3+A.cand*.9,PRES_COLOR.receiver,A.cand*(5+2*Math.sin(t/400+k)));});
  if(A.gather>.01) {
    const g=presEase(A.gather), first=PRES.data.sizes[0]||0;
    P.ringDots.forEach((i,k)=>{const target=PRES.clusters[k];if(!target) return;const [x0,y0]=xy(i), u=presEase(presClamp(g*1.6-(k%97)/97*.6));
      presDot(ctx,x0+(target[0]-x0)*u,y0+(target[1]-y0)*u,1.5,k<first?PRES_COLOR.hub:'#9fd6ff',4.5);});
  }
  ctx.restore();
}
function presDrawGraph(ctx,t,alpha) {
  if(alpha<=.01) return;
  const ring=PRES.ring, r=PRES.replay, box=presGraphBox(), F=presFit(presLayout(ring),box), P=F.pts, s=presClamp(F.s,.7,2.2);
  const flight=presFlight(), maxRecv=Math.max(...r.recv,1), now=performance.now();
  ctx.save();ctx.globalAlpha=alpha*(1-.72*PRES.anim.dim);
  if(ring.hubs&&PRES.W>=820) {ctx.font='600 11px ui-monospace,Menlo,monospace';ctx.fillStyle='rgba(120,150,175,.55)';ctx.textAlign='center';
    ctx.fillText('SENDERS  →',box.x+box.w*.12,box.y+box.h-4);ctx.fillText('→  RECEIVERS',box.x+box.w*.88,box.y+box.h-4);}
  ring.links.forEach((link,i)=>{
    const a=P[link[0]], b=P[link[1]], c=presCurve(a,b), heat=presClamp(1-(r.t-r.lastLink[i])/(r.mps*r.speed*2.2)), seen=r.lastLink[i]>-1e8;
    ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.quadraticCurveTo(c[0],c[1],b[0],b[1]);
    ctx.strokeStyle=heat>0?presRgba('#cfe3f2',.18+heat*.6):presRgba('#7f9db5',seen?.3:.1);ctx.lineWidth=(1+heat*1.6+Math.log1p(link[2])*.35)*Math.min(s,1.4);ctx.stroke();
  });
  if(r.loopFlash) {const age=(now-r.loopFlash.at)/1600;if(age<1) {const [a,b,c]=r.loopFlash.nodes.map(i=>P[i]);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.lineTo(c[0],c[1]);ctx.closePath();
    ctx.fillStyle=presRgba(PRES_COLOR.loop,.22*(1-age));ctx.fill();ctx.strokeStyle=presRgba(PRES_COLOR.loop,.9*(1-age));ctx.lineWidth=2.5;ctx.stroke();}}
  for(let k=r.next;k<ring.pays.length;k++) {
    const pay=ring.pays[k];if(pay[1]>r.t) break;const u=(r.t-pay[1])/flight;if(u<0||u>=1) continue;
    const link=ring.links[pay[0]], a=P[link[0]], b=P[link[1]], [x,y]=presQuad(a,presCurve(a,b),b,presEase(u)), role=ring.nodes[link[0]][1];
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
  const {W,H}=PRES, cols=W<820?5:10, rows=Math.ceil(PRES.data.rings.length/cols), top=150, cw=(W-40)/cols, ch=(H-top-20)/rows;
  return PRES.data.rings.map((ring,k)=>({ring,x:20+(k%cols)*cw,y:top+Math.floor(k/cols)*ch,w:cw,h:ch}));
}
function presDrawWall(ctx) {
  if(PRES.anim.wall<=.01) return;
  PRES.wallCells=presWallLayout();const now=performance.now();ctx.save();
  PRES.wallCells.forEach((cell,k)=>{
    const a=PRES.anim.wall*presClamp((now-PRES.wallAt-k*22)/350);if(a<=0) return;
    const ring=cell.ring, hover=k===PRES.wallHover;ctx.globalAlpha=a;
    ctx.fillStyle=hover?'rgba(63,208,184,.08)':'rgba(13,24,36,.55)';ctx.strokeStyle=hover?'rgba(63,208,184,.7)':'rgba(31,51,70,.8)';ctx.lineWidth=1;
    ctx.beginPath();ctx.roundRect?ctx.roundRect(cell.x+3,cell.y+3,cell.w-6,cell.h-6,8):ctx.rect(cell.x+3,cell.y+3,cell.w-6,cell.h-6);ctx.fill();ctx.stroke();
    const P=presFit(presLayout(ring),{x:cell.x+10,y:cell.y+10,w:cell.w-20,h:cell.h-32},1.2).pts;
    ctx.lineWidth=.6;ctx.strokeStyle='rgba(127,157,181,.28)';
    ring.links.forEach(link=>{ctx.beginPath();ctx.moveTo(P[link[0]][0],P[link[0]][1]);ctx.lineTo(P[link[1]][0],P[link[1]][1]);ctx.stroke();});
    ring.nodes.forEach((node,i)=>{ctx.beginPath();ctx.arc(P[i][0],P[i][1],node[1]==='hub'?3.2:1.9,0,Math.PI*2);ctx.fillStyle=node[2]?PRES_COLOR.dirty:'#4f6a80';ctx.fill();});
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
