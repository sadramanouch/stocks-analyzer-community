'use client';
import {useCallback, useEffect, useState} from 'react';
import {apiFetch} from '@/lib/apiFetch';
import {Disclosure} from './workspace-ui';

type Job = {id:string;title:string;registered:boolean;collection_enabled:boolean|null;next_run_at:string|null;trigger:string|null;timezone:string|null;record_kind:string;latest_record:Record<string,string|number|null>|null};
type Status = {observed_at:string;scheduler_state:string;all_expected_jobs_registered:boolean;jobs:Job[];access:Record<string,boolean>;counts:Record<string,number>;saved_list:{version:number|null;records:number;documented_inputs:number};limits:string};
const accessLabels:Record<string,string> = {ibkr_token_present:'IBKR reporting token',ibkr_query_present:'IBKR query ID',ibkr_reporting_configured:'IBKR reporting configuration',quiver_key_present:'Quiver credential',nansen_key_present:'Nansen credential'};
const countLabels:Record<string,string> = {broker_flex_receipts:'Archived broker downloads',broker_flex_downloads:'Broker download jobs',paper_ledger_events:'Virtual ledger events',signal_trials:'Registered filing trials',signal_trial_observations:'Archived trial closes',quote_check_reports:'Archived quote checks'};

export default function SystemStatus() {
  const [data,setData]=useState<Status|null>(null), [error,setError]=useState(''), [busy,setBusy]=useState(false);
  const load=useCallback(async(signal?:AbortSignal)=>{
    setBusy(true);setError('');setData(null);
    try {const r=await apiFetch('/api/system-status',{signal});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Operational evidence unavailable');if(!signal?.aborted)setData(d);}
    catch(e){if(!signal?.aborted)setError(e instanceof Error?e.message:'Operational evidence unavailable');}
    finally{if(!signal?.aborted)setBusy(false);}
  },[]);
  useEffect(()=>{const c=new AbortController();void Promise.resolve().then(()=>load(c.signal));return()=>c.abort();},[load]);
  return <section className="space-y-5" aria-label="Operational evidence">
    <button className="rounded-lg border border-zinc-700 px-3 py-2 text-sm disabled:opacity-40" disabled={busy} onClick={()=>void load()}>Reload system status</button>
    {busy&&<p role="status">Loading operational evidence…</p>}
    {error&&<p role="alert" className="text-amber-300">{error} Current status is unknown.</p>}
    {data&&<>
      <div className="rounded-xl border border-zinc-800 p-5"><h2 className="text-lg">Scheduler: {data.scheduler_state}</h2><p className="mt-2 text-sm text-zinc-400">{data.jobs.filter(j=>j.registered).length}/{data.jobs.length} expected jobs registered · observed {new Date(data.observed_at).toLocaleString()}. Reload to update this snapshot.</p>{!data.all_expected_jobs_registered&&<p className="mt-2 text-amber-300">Some expected schedules are missing. Collection coverage needs review.</p>}</div>
      <div className="grid gap-4 md:grid-cols-2"><section className="rounded-xl border border-zinc-800 p-5"><h2 className="text-lg">Access dependencies</h2><dl className="mt-3 space-y-2 text-sm">{Object.entries(data.access).map(([k,v])=><div key={k} className="flex flex-wrap justify-between gap-2"><dt>{accessLabels[k]??k}</dt><dd className={v?'text-emerald-300':'text-amber-300'}>{v?(k==='ibkr_reporting_configured'?'Configured':'Present'):'Missing or invalid'}</dd></div>)}</dl><p className="mt-3 text-xs leading-5 text-zinc-400">Configuration does not prove access or entitlement. A genuine download is required to validate the broker connection. Government and crypto feeds require their own provider access.</p></section>
      <section className="rounded-xl border border-zinc-800 p-5"><h2 className="text-lg">Archived evidence</h2><p className="mt-2 text-sm text-zinc-400">Saved edition {data.saved_list.version??'none'} · {data.saved_list.documented_inputs}/{data.saved_list.records} records with documented annual inputs. This count does not certify current freshness or every financial field.</p><dl className="mt-3 space-y-2 text-sm">{Object.entries(data.counts).map(([k,v])=><div key={k} className="flex flex-wrap justify-between gap-2"><dt>{countLabels[k]??k}</dt><dd>{v}</dd></div>)}</dl></section></div>
      <Disclosure title="How to read this status"><p className="text-sm leading-6 text-zinc-400">{data.limits}</p></Disclosure>
      <div className="grid gap-4 md:grid-cols-2">{data.jobs.map(j=><article key={j.id} className="min-w-0 space-y-3 rounded-xl border border-zinc-800 p-5"><h2 className="font-medium">{j.title}</h2><p className="text-sm">{j.registered?'Schedule registered':'Schedule missing'}{j.collection_enabled!==null?` · collection ${j.collection_enabled?'enabled':'paused'}`:''}</p><p className="text-sm text-zinc-400">Next scheduled invocation: {j.next_run_at?new Date(j.next_run_at).toLocaleString():'Unavailable or paused'}</p>{j.latest_record?.state&&<p className={['needs_review','failed','blocked','interrupted'].includes(String(j.latest_record.state))?'text-sm text-amber-300':'text-xs text-zinc-400'}>Latest result: {String(j.latest_record.state).replaceAll('_',' ')}</p>}<Disclosure title="Schedule & recent work"><p className="break-words text-xs text-zinc-500">{j.trigger??'No trigger available'} · schedule timezone {j.timezone??'unknown'}</p><div className="border-t border-zinc-800 pt-3"><p className="mb-2 text-xs text-zinc-400">Latest {j.record_kind} · may predate this deployment</p>{j.latest_record?<dl className="space-y-1 text-xs">{Object.entries(j.latest_record).map(([k,v])=><div key={k} className="flex flex-wrap justify-between gap-2"><dt className="text-zinc-500">{k.replaceAll('_',' ')}</dt><dd className="max-w-full break-all">{v??'Not recorded'}</dd></div>)}</dl>:<p className="text-xs text-zinc-400">No archived record. Successful collection is unconfirmed.</p>}</div></Disclosure></article>)}</div>
    </>}
  </section>;
}
