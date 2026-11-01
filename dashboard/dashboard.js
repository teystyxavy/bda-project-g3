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
  for(const name of ['aml','network','ingestion']) {
    element(name).hidden=name!==view || (name!=='ingestion' && !snapshot);
    element(`${name}-tab`).setAttribute('aria-pressed',String(name===view));
  }
  element('status').hidden=view==='ingestion';element('methods').hidden=view==='ingestion';
  if(view==='network' && snapshot) renderNetwork();
}
for(const view of ['aml','network','ingestion']) element(`${view}-tab`).onclick=()=>selectDashboardView(view);

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
  if(!networkReady) {
    networkReady=true;
    element('ring-picker').onchange=()=>selectRing(element('ring-picker').value);
    element('ring-search').oninput=()=>findAccount(element('ring-search').value);
    element('ring-reset').onclick=()=>{if(selectedRing){delete ringLayouts[selectedRing];renderRing();}};
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
    const current=button.dataset.ring===id;button.setAttribute('aria-current',String(current));
    if(current) button.scrollIntoView?.({block:'nearest'});
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

function renderRing() {
  const graph=snapshot.network.graphs[selectedRing];
  if(!graph) return;
  GRAPH=graphShape();
  if(ringLayoutShape[selectedRing]!==GRAPH.width) {delete ringLayouts[selectedRing];ringLayoutShape[selectedRing]=GRAPH.width;}
  const layout=ringLayouts[selectedRing]||(ringLayouts[selectedRing]=layoutRing(graph));
  const roles=flowRoles(graph), hasHub=graph.nodes.some(node=>node.role==='hub');
  const byId=new Map(graph.nodes.map(node=>[node.id,node]));
  const maxTxns=Math.max(1,...graph.links.map(link=>link.txns)), maxUsd=Math.max(1,...graph.nodes.map(node=>node.in_usd+node.out_usd));
  const radius=node=>node.role==='counterparty'?ROLE[roles[node.id]].radius+4*Math.sqrt((node.in_usd+node.out_usd)/maxUsd):ROLE[node.role].radius;
  const tone=link=>link.loop?'loop':roles[link.source]==='sender'?'in':roles[link.target]==='receiver'?'out':'other';
  const linkMarkup=graph.links.map((link,i)=>`<line data-link="${i}" class="ring-link ring-link-${tone(link)}" stroke-width="${(1+3*Math.log1p(link.txns)/Math.log1p(maxTxns)).toFixed(2)}" marker-end="url(#ring-arrow-${tone(link)})"><title>${escapeText(link.source)} → ${escapeText(link.target)}: ${integer(link.txns)} payment${link.txns===1?'':'s'}, ${dollars(link.usd)}${link.loop?' · part of a timed loop':''}</title></line>`).join('');
  const nodeMarkup=graph.nodes.map(node=>{const role=ROLE[roles[node.id]], r=radius(node);return `<g class="ring-node ring-node-${roles[node.id]}${node.id===selectedAccount?' ring-node-selected':''}" data-node="${escapeText(node.id)}" tabindex="0" role="button" aria-label="${escapeText(role.label)} ${escapeText(node.id)}, ${integer(node.fan_in)} senders, ${integer(node.fan_out)} receivers"><circle r="${r.toFixed(1)}" fill="${role.color}"/>${node.role==='counterparty'?'':`<text y="${(node.role==='hub'?r+16:-r-6).toFixed(1)}" text-anchor="middle">${escapeText(shortAccount(node.id))}</text>`}<title>${escapeText(node.id)} · ${escapeText(role.label)} · ${integer(node.fan_in)} senders, ${integer(node.fan_out)} receivers overall</title></g>`;}).join('');
  const marker=(name,color)=>`<marker id="ring-arrow-${name}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${color}"/></marker>`;
  const lanes=hasHub?`<text class="ring-lane" x="${GRAPH.margin-10}" y="22">← MONEY IN</text><text class="ring-lane" x="${GRAPH.width-GRAPH.margin+10}" y="22" text-anchor="end">MONEY OUT →</text>`:'';
  const crowded=graph.nodes.filter(node=>node.role==='loop').length>8;
  element('ring-graph').innerHTML=`<svg id="ring-svg" class="${crowded?'ring-crowded':''}" viewBox="0 0 ${GRAPH.width} ${GRAPH.height}" role="img" aria-label="Money flows between ${graph.shown_accounts} accounts of the selected ring"><defs>${marker('in','#7fa3bf')}${marker('out','#d6a85a')}${marker('other','#9aabb7')}${marker('loop','#7c5cb0')}</defs>${lanes}<g>${linkMarkup}</g><g>${nodeMarkup}</g></svg>`;
  element('ring-graph-note').textContent=`${graph.truncated?`Showing ${graph.shown_accounts} of ${graph.total_accounts} accounts: every hub and loop account, then counterparties by payment volume. `:''}Arrows show direction; thicker links carry more payments; larger dots moved more money. ${crowded?'Loop-account names appear on hover. ':''}Hover to trace an account, drag to rearrange.`;
  placeRing(graph,layout,byId,radius);
  const svg=element('ring-svg');
  const neighbours=id=>new Set([id,...graph.links.filter(link=>link.source===id||link.target===id).flatMap(link=>[link.source,link.target])]);
  const highlight=id=>{
    if(!svg?.classList) return;
    svg.classList.toggle('ring-dim',Boolean(id));
    const keep=id?neighbours(id):new Set();
    svg.querySelectorAll('[data-node]').forEach(mark=>mark.classList.toggle('ring-hl',keep.has(mark.dataset.node)));
    svg.querySelectorAll('[data-link]').forEach(line=>{const link=graph.links[Number(line.dataset.link)];line.classList.toggle('ring-hl',Boolean(id)&&(link.source===id||link.target===id));});
  };
  svg?.querySelectorAll?.('[data-node]').forEach(mark=>{
    const id=mark.dataset.node, activate=()=>{selectedAccount=id;showAccount(id);svg.querySelectorAll('[data-node]').forEach(other=>other.classList.toggle('ring-node-selected',other.dataset.node===id));};
    mark.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activate();}};
    mark.onpointerenter=()=>highlight(id);mark.onpointerleave=()=>highlight(null);mark.onfocus=()=>highlight(id);mark.onblur=()=>highlight(null);
    let drag=null;
    mark.onpointerdown=event=>{drag={x:event.clientX,y:event.clientY,moved:false};mark.setPointerCapture?.(event.pointerId);};
    mark.onpointermove=event=>{
      if(!drag) return;
      if(Math.hypot(event.clientX-drag.x,event.clientY-drag.y)>3) drag.moved=true;
      if(!drag.moved) return;
      const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
      const local=point.matrixTransform(svg.getScreenCTM().inverse());
      layout[id]={x:Math.min(GRAPH.width-GRAPH.margin,Math.max(GRAPH.margin,local.x)),y:Math.min(GRAPH.height-GRAPH.margin,Math.max(GRAPH.margin,local.y))};
      placeRing(graph,layout,byId,radius);
    };
    mark.onpointerup=()=>{const click=drag&&!drag.moved;drag=null;if(click) activate();};
  });
  if(selectedAccount&&byId.has(selectedAccount)) showAccount(selectedAccount);
  else element('ring-node').innerHTML='<h3>Account details</h3><p>Select an account in the graph.</p>';
}

// Moves nodes and trims each link to stop at the edge of its target circle.
function placeRing(graph,layout,byId,radius) {
  const svg=element('ring-svg');
  if(!svg?.querySelectorAll) return;
  svg.querySelectorAll('[data-node]').forEach(mark=>{const p=layout[mark.dataset.node];mark.setAttribute('transform',`translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`);});
  svg.querySelectorAll('[data-link]').forEach(line=>{
    const link=graph.links[Number(line.dataset.link)], a=layout[link.source], b=layout[link.target];
    const d=Math.hypot(b.x-a.x,b.y-a.y)||1, from=radius(byId.get(link.source))+1, to=radius(byId.get(link.target))+3;
    line.setAttribute('x1',(a.x+(b.x-a.x)*from/d).toFixed(1));line.setAttribute('y1',(a.y+(b.y-a.y)*from/d).toFixed(1));
    line.setAttribute('x2',(b.x-(b.x-a.x)*to/d).toFixed(1));line.setAttribute('y2',(b.y-(b.y-a.y)*to/d).toFixed(1));
  });
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
