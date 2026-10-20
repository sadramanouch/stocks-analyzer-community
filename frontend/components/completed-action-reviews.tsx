"use client";
import {useCallback,useEffect,useState} from 'react';
import {apiFetch} from '@/lib/apiFetch';
type Capture={id:string;report_sha256:string;report:{ticker:string;reported_conversion_date:string;reported_consideration:string;captured_at:string;sources:{id:string;sha256:string;source_kind:string;source_url:string}[]}};
type Data={supported_cases:{ticker:string;reported_conversion_date:string}[];captures:Capture[];limits:string};
export default function CompletedActionReviews({onSaved}:{onSaved?:()=>void}){
 const [data,setData]=useState<Data|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState('');
 const load=useCallback(async(signal?:AbortSignal)=>{try{const r=await apiFetch('/api/completed-actions',{signal});if(!r.ok)throw Error(`Completed-action review unavailable (${r.status})`);const d=await r.json();if(!signal?.aborted){setData(d);setError('');}}catch(e){if(!signal?.aborted){setData(null);setError(e instanceof Error?e.message:'Completed-action review unavailable');}}},[]);
 useEffect(()=>{const c=new AbortController();void Promise.resolve().then(()=>load(c.signal));return()=>c.abort();},[load]);
 async function capture(ticker:string){setBusy(ticker);setError('');try{const r=await apiFetch(`/api/completed-actions/${ticker}/capture`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)throw Error(`Source capture requires review (${r.status})`);await load();onSaved?.();}catch(e){setError(e instanceof Error?e.message:'Source capture unavailable');}finally{setBusy('');}}
 return <section className="space-y-3 rounded-xl border border-amber-900/60 p-4" aria-label="Completed corporate-action evidence">
  <h3 className="text-sm font-medium">Completed conversions · legacy marks need review</h3>
  <p className="text-xs text-zinc-400">An issuer’s merger terms describe a contractual entitlement. Original legacy positions can still show old-symbol book marks; verifying the resulting cash or successor shares requires actual records.</p>
  {error&&<p role="alert" className="text-xs text-red-300">{error}</p>}
  {data?.supported_cases.map(c=>{const saved=data.captures.find(r=>r.report.ticker===c.ticker);return <div key={c.ticker} className="min-w-0 space-y-2 rounded-lg border border-zinc-800 p-3 text-xs">
   <p className="font-medium">{c.ticker} · reported conversion {c.reported_conversion_date}</p>
   {saved?<><p className="text-amber-200">{saved.report.reported_consideration}</p><p className="text-zinc-500">Issuer report captured {new Date(saved.report.captured_at).toLocaleString()}. Payment, position value and return remain unverified.</p>
    <a className="block text-emerald-300 underline" href={`/api/backend/api/completed-actions/reports/${saved.id}`} target="_blank" rel="noreferrer">Open replayed conversion report</a>
    <details><summary>Original filings and fingerprints</summary><p className="mt-2 break-all text-zinc-500">Report SHA-256 {saved.report_sha256}</p>{saved.report.sources.map(s=><div key={s.id} className="mt-3 min-w-0"><a className="text-emerald-300 underline" href={`/api/backend/api/market-history/sources/${s.id}`} target="_blank" rel="noreferrer">Archived {s.source_kind.replaceAll('_',' ')}</a><p className="break-all text-zinc-500">SHA-256 {s.sha256}</p><a className="text-zinc-400 underline" href={s.source_url} target="_blank" rel="noreferrer">Publisher source</a></div>)}</details></>
   :<button disabled={!!busy} className="rounded border border-zinc-700 px-3 py-2 disabled:opacity-50" onClick={()=>void capture(c.ticker)}>{busy===c.ticker?'Checking original filing…':`Check and archive ${c.ticker} conversion`}</button>}
  </div>;})}
  <p className="text-xs leading-5 text-zinc-500">{data?.limits}</p>
 </section>;
}
