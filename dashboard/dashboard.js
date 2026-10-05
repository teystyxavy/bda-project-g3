'use strict';
let snapshot;
const element = id => document.getElementById(id);
const integer = value => Number(value).toLocaleString('en-US');
const percent = value => value == null ? 'Unavailable' : `${(value * 100).toFixed(2)}%`;
const compact = value => new Intl.NumberFormat('en-US', {notation:'compact',maximumFractionDigits:2}).format(value);
const dollars = value => `$${compact(value)}`;
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

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

function isModelQueue() {return Boolean(snapshot.model) && element('scoring').value==='model';}
function currentQueue() {return isModelQueue()?snapshot.model.views[element('split').value]:snapshot.aml[element('split').value];}

function renderAml() {
  const rules=snapshot.aml[element('split').value], current=currentQueue(), metrics=current.metrics, model=isModelQueue();
  cards('aml-kpis', [[snapshot.aml_scope==='full'?'Transactions':'Cohort transactions',integer(metrics.rows),'Selected period'],model?['Review capacity',integer(metrics.k),'Top-ranked queue · no model threshold']:['Rule-flagged transactions',integer(rules.flagged),'Rule score ≥60'],[`Precision@${metrics.k}`,percent(metrics.precision_at_k),'Label-positive fraction of top queue'],[`Recall@${metrics.k}`,percent(metrics.recall_at_k),`${integer(metrics.positives)} labelled positives in period`]]);
  const note=`Exploratory ${model?'frozen model':'rule baseline'} · average precision ${metrics.average_precision==null?'unavailable':metrics.average_precision.toFixed(4)} · lift@${metrics.k} ${metrics.lift_at_k==null?'unavailable':metrics.lift_at_k.toFixed(2)+'×'}.`;
  element('aml-note').textContent=note+(element('split').value==='tail'?' Sparse tail: unusually few transactions and high positive-label prevalence. Do not compare directly with the main test period.':' Positive labels evaluate the queue; scores are not probabilities.');
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

function showDetail(row) {
  const model=isModelQueue();
  element('detail').hidden=false;
  element('detail').innerHTML=`<h3>${escapeText(row.transaction_id)}</h3><p>Sender ${escapeText(row.from_bank)} / ${escapeText(row.from_account)} → receiver ${escapeText(row.to_bank)} / ${escapeText(row.to_account)}</p><p>${escapeText(model?row.model_evidence:row.risk_reasons||'No rule triggered')}. Prior hour: ${integer(row.txn_count_1h)} transactions. Prior 24 hours: ${integer(row.distinct_counterparties_24h)} distinct counterparties. Original payment currency: ${escapeText(row.payment_currency)}.</p>${model?`<p>Top three signed model-margin contributions; positive values push the score up, negative values push it down. Bias margin ${Number(row.explanation_bias_margin).toFixed(4)}; full total margin ${Number(row.explanation_total_margin).toFixed(4)} includes all features. This is not causal evidence or a calibrated probability.</p>`:''}<button id="close-detail">Close detail</button>`;
  element('close-detail').onclick=()=>{element('detail').hidden=true;};
}

function renderTreasury() {
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

async function start() {
  try {
    const response=await fetch('dashboard.json');
    if(!response.ok) throw new Error(`Data request failed (${response.status})`);
    snapshot=await response.json();
    if(snapshot.schema_version!==1) throw new Error('Unsupported snapshot version');
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
      element('scoring').onchange=renderAml;
    }
    snapshot.treasury_daily.forEach(row=>{const option=document.createElement('option');option.value=row.day;option.textContent=row.day;element('day').appendChild(option);});
    element('aml-tab').onclick=()=>{element('aml').hidden=false;element('treasury').hidden=true;element('aml-tab').setAttribute('aria-pressed','true');element('treasury-tab').setAttribute('aria-pressed','false');};
    element('treasury-tab').onclick=()=>{element('aml').hidden=true;element('treasury').hidden=false;element('aml-tab').setAttribute('aria-pressed','false');element('treasury-tab').setAttribute('aria-pressed','true');renderTreasury();};
    element('split').onchange=renderAml;element('day').onchange=renderTreasury;
    element('export-queue').onclick=()=>download(currentQueue().queue,`queue-${isModelQueue()?'model':'rules'}-${element('split').value}.csv`);
    element('export-corridors').onclick=()=>download(snapshot.treasury[element('day').value].banks,`corridors-${element('day').value}.csv`);
    table('variants',['Variant','Precision@100','Recall@100','Average precision'],snapshot.diagnostics.variants.map(row=>[row.variant,percent(row.precision_at_k),percent(row.recall_at_k),row.average_precision==null?'Unavailable':row.average_precision.toFixed(4)]));
    table('reasons',['Score','Reasons','Reviewed','Positives','False positives'],snapshot.diagnostics.top100_reasons.map(row=>[row.risk_score,row.risk_reasons,row.reviewed,row.positives,row.false_positives]));
    element('provenance').textContent=JSON.stringify({generated_at:snapshot.generated_at,sources:snapshot.sources,provenance:snapshot.provenance,model:snapshot.model?{name:snapshot.model.name,provenance:snapshot.model.provenance}:undefined},null,2);
    element('aml').hidden=false;renderAml();
  } catch(error) {element('status').textContent=`Unable to load dashboard: ${error.message}. Serve this folder over HTTP and rebuild the snapshot if needed.`;}
}
start();
