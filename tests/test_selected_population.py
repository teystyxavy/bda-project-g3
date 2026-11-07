import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from bda_pipeline.date_evaluation import build_baseline
from bda_pipeline.ingest_contract import sha
from bda_pipeline.selected_population import validate_selection, validate_snapshot_population
from bda_pipeline.release import ASSETS, prepare_release
from test_batch import fixture, daily_proof


def staged(tmp_path):
    inputs=tmp_path/'inputs'
    for name in ['run','source','model','curated']:(inputs/name).mkdir(parents=True)
    frame=fixture()
    frame['transaction_id']=['HI-Large:'+str(i) for i in frame.source_row_index]
    frame['timestamp']=pd.to_datetime(['2022-09-28']*60+['2022-10-01']*60+['2022-10-02']*60+['2022-10-03']*60)
    frame['txn_date']=frame.timestamp.dt.strftime('%Y-%m-%d')
    frame.iloc[60:].to_parquet(inputs/'curated/part.parquet',index=False)
    source={'subset':'HI-Large','rows':240,'files':{},'start_date':'2022-09-28','end_date':'2022-10-03'}
    proof={'status':'passed','rows':240,'positives':int(frame.is_laundering.sum()),'amount_usd':float(frame.amount_usd.sum()),'daily':daily_proof(frame)}
    training={'aml': {'test': {'queue': [{'transaction_id': 'HI-Large:1'}]}}, 'provenance':{'export_dates':['2022-09-01'],'training_p99':{'ACH':50.,'Wire':50.}}}
    training_bytes=json.dumps(training).encode();training_hash=hashlib.sha256(training_bytes).hexdigest()
    selection={'kind':'date-range','start_date':'2022-10-01','end_date':'2022-10-03','source_run_id':'source-run','source_manifest_sha256':sha(source),'source_proof_sha256':sha(proof),'training_snapshot_sha256':training_hash,'network_scope':'full-source'}
    manifest={'selection':selection,'rows':180,'source_index_rows':240,'network_rows':240,'train':False,'export_dates':None,'source_scope':'date-subset','subset':'HI-Large','start_date':'2022-10-01','end_date':'2022-10-03','files':{}}
    for name,value in [('source/manifest.json',source),('source/proof.json',proof),('run/manifest.json',manifest)]: (inputs/name).write_text(json.dumps(value))
    (inputs/'source/training-dashboard.json').write_bytes(training_bytes)
    report={'splits':{'train':['2022-09-01','2022-09-07'],'validation':['2022-09-07','2022-09-09'],'test':['2022-09-09','2023-01-13']},'execution':{'snapshot_sha256':training_hash}}
    (inputs/'model/comparison.json').write_text(json.dumps(report))
    return inputs,manifest


def test_sparse_original_ids_and_frozen_thresholds_build_complete_selected_release(tmp_path):
    inputs,manifest=staged(tmp_path);output=tmp_path/'output';assets=Path(__file__).resolve().parents[1]/'dashboard'
    result=build_baseline(inputs,output,assets)
    assert result['rows']==180
    data=pd.read_parquet(output/'baseline/bq1_scored.parquet')
    assert data.source_row_index.min()==60 and data.source_row_index.max()==239
    assert data.amount_usd_asof.gt(50).all()
    assert data.risk_reasons.str.contains('above training P99',regex=False).all()
    snapshot=json.loads((output/'manifest/dashboard.json').read_text())
    assert snapshot['aml_scope']=='selected-population'
    assert set(snapshot['splits'])=={'train','test'}
    assert snapshot['full_rows']==180 and snapshot['source_index_rows']==240
    assert snapshot['population']['network_rows']==240
    assert 'BQ2 covers the complete source population' in snapshot['population']['history_policy']
    validate_snapshot_population(snapshot)
    # The selected population must finish verification before it can be released.
    site=tmp_path/'site';site.mkdir()
    snapshot.update(buildStatus='complete',model={'provenance':{'model_sha256':'a'*64}})
    for name in ASSETS:(site/name).write_text(json.dumps(snapshot) if name.endswith('.json') else 'asset')
    release=prepare_release(site,tmp_path/'release')
    assert release['total_bytes']>0


def test_selected_daily_counts_must_match_independent_source_proof(tmp_path):
    inputs,_=staged(tmp_path)
    data=pd.read_parquet(inputs/'curated/part.parquet').iloc[:-1]
    data.to_parquet(inputs/'curated/part.parquet',index=False)
    with pytest.raises(ValueError,match='daily population'):
        build_baseline(inputs,tmp_path/'output',Path('dashboard'))


def test_training_or_validation_dates_cannot_be_evaluation(tmp_path):
    inputs,_=staged(tmp_path)
    report=json.loads((inputs/'model/comparison.json').read_text());report['splits']['validation'][1]='2022-10-02'
    (inputs/'model/comparison.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='training or validation'):
        build_baseline(inputs,tmp_path/'output',Path('dashboard'))


@pytest.mark.parametrize('change',[{'train':True},{'network_rows':180},{'source_index_rows':100},{'source_scope':'selected-subset'}])
def test_inconsistent_scope_is_rejected(tmp_path,change):
    _,manifest=staged(tmp_path);manifest.update(change)
    with pytest.raises(ValueError):validate_selection(manifest)


def test_export_reconciles_different_bq1_and_bq2_populations(tmp_path):
    from test_ingestion import staged_run, serve_inputs, ROOT
    from bda_pipeline.ingest_compute import baseline_stage, export_stage
    from bda_pipeline.batch import AmlSummary
    inputs,value=staged_run(tmp_path, "HI-Small")
    baseline=inputs/'baseline'
    baseline_stage(inputs,baseline,ROOT/'dashboard')
    reference,cloud=serve_inputs(tmp_path,inputs,baseline,value)
    full=pd.read_parquet(reference)
    selected=full.loc[full.timestamp.ge('2022-09-12')].copy()
    selected.to_parquet(reference,index=False)
    cloud.loc[cloud.timestamp.ge('2022-09-12')].to_parquet(inputs/'scored/data.parquet',index=False)
    (inputs/'network_reference').mkdir()
    full.to_parquet(inputs/'network_reference/part.parquet',index=False)
    selection={'kind':'date-range','start_date':'2022-09-12','end_date':'2022-09-12',
        'source_run_id':'source-run','source_manifest_sha256':'a'*64,'source_proof_sha256':'b'*64,
        'training_snapshot_sha256':'c'*64,'network_scope':'full-source','network_start_date':'2022-09-01',
        'network_end_date':'2022-09-12','development_dates':['2022-09-01','2022-09-07','2022-09-09']}
    value.update(rows=len(selected),source_index_rows=len(full),network_rows=len(full),selection=selection,
                 start_date='2022-09-12',end_date='2022-09-12',source_scope='date-subset')
    (inputs/'run/manifest.json').write_text(json.dumps(value))
    snapshot=json.loads((baseline/'manifest/dashboard.json').read_text())
    summary=AmlSummary();summary.add(selected)
    snapshot.update(full_rows=len(selected),cohort_rows=len(selected),source_index_rows=len(full),aml_scope='selected-population',
        population={**selection,'rows':len(selected),'source_subset':value['subset']},
        splits={'train':['2022-09-01','2022-09-07'],'test':['2022-09-12','2022-09-13']},aml={'test':summary.view()})
    snapshot['provenance']['subset_verification']={'status':'passed','rows':len(selected),'days':1}
    (baseline/'manifest/dashboard.json').write_text(json.dumps(snapshot))
    output=tmp_path/'selected-export'
    result=export_stage(inputs,output,ROOT/'dashboard')
    assert result['verification']['scores']['rows']==80
    assert result['verification']['network']['reference_rows']==320
    public=json.loads((output/'release/site/dashboard.json').read_text())
    assert public['model']['views']['test']['metrics']['rows']==80
    assert public['model']['test_date_breakdown']['unseen_dates']['metrics']['rows']==80
    assert public['model']['test_date_breakdown']['previous_test_dates']['metrics']['rows']==0
    assert public['network']['totals']['rows']==320
    assert public['network']['population']['rows']==320
    # A duplicated/substituted scored row cannot hide among unselected source IDs.
    bad=cloud.loc[cloud.timestamp.ge('2022-09-12')].copy()
    bad.loc[bad.index[0],'source_row_index']=0
    bad.loc[bad.index[0],'transaction_id']='HI-Small:0'
    bad.to_parquet(inputs/'scored/data.parquet',index=False)
    with pytest.raises((AssertionError,ValueError)):
        export_stage(inputs,tmp_path/'bad-selected-export',ROOT/'dashboard')
