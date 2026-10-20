"use client";
import {useEffect,useState} from 'react';
import {apiFetch} from '@/lib/apiFetch';
type Fact={status:string;value:string|null;label:string;scope:string;reason?:string;start?:string;end?:string;filed?:string;accn?:string;form?:string;concept?:string};
type Measure={label:string;status:string;value:string|null;spending_scope:string;reason?:string;operating_cf_basis?:string};
type Evidence={ticker:string;research_version:number;saved_at:string;status:string;content_sha256:string;coverage:{source_matched_facts:number;available_cash_measures:number;derived_total_operating_periods:number};sources:{id:string;sha256:string}[];periods:{end:string|null;expected_year_offset:number;facts:Record<string,Fact>;total_operating_cf:{status:string;value:string|null;inputs?:string[];reason?:string};operating_scope_reconciliation:{status:string;difference:string|null;reason:string};cash_measures:Record<string,Measure>}[];issues:string[];limitations:string[]};
const words=(x:string)=>x.replaceAll('_',' ');
const dollars=(x:string|null)=>x==null?'Unknown':`USD ${Number(x).toLocaleString(undefined,{maximumFractionDigits:2})}`;
export default function CashflowEvidence({ticker,version}:{ticker:string;version:number|null}){
 const [data,setData]=useState<Evidence|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState<{id:string;report_sha256:string}|null>(null);
 useEffect(()=>{const c=new AbortController();void Promise.resolve().then(async()=>{try{
  if(version==null)return;
  const r=await apiFetch(`/api/cashflow-evidence/${encodeURIComponent(ticker)}?version=${version}`,{signal:c.signal});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Filed cash-flow evidence unavailable');if(!c.signal.aborted){setData(d);setError('');}
 }catch(e){if(!c.signal.aborted)setError(e instanceof Error?e.message:'Filed cash-flow evidence unavailable');}});return()=>c.abort();},[ticker,version]);
 async function capture(){if(!data)return;setBusy(true);setError('');try{
  const r=await apiFetch(`/api/cashflow-evidence/${encodeURIComponent(ticker)}/capture`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({research_version:data.research_version,content_sha256:data.content_sha256,request_id:crypto.randomUUID().replaceAll('-','')})});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Cash-flow capture failed');setSaved(d);
 }catch(e){setError(e instanceof Error?e.message:'Cash-flow capture failed');}finally{setBusy(false);}}
 return <details aria-label={`Filed cash-flow scopes for ${ticker}`} className="min-w-0 rounded-lg border border-zinc-700 p-3">
  <summary className="cursor-pointer text-sm font-medium">Filed cash-flow scopes · {ticker}<span className="mt-1 block text-xs font-normal text-zinc-500">{data?`${data.coverage.source_matched_facts} original annual facts · ${data.coverage.available_cash_measures} available cash measures`:version==null?'No saved research edition':'Loading original cash-flow evidence…'}</span></summary>
  <div className="mt-3 min-w-0 space-y-3"><p className="text-xs leading-5 text-zinc-400">PP&E, productive assets and oil/gas investment have different scopes. Broader spending is reported separately. These measures preserve the existing capex, FCF, ranking and trading policy.</p>
   {error&&<p role="alert" className="text-sm text-amber-200">{error}</p>}
   {data&&<><p className="text-xs text-zinc-500">Saved edition {data.research_version} · {new Date(data.saved_at).toLocaleString()} · {words(data.status)}. Later filings and restatements can change these dated facts.</p>
    {data.issues.map(x=><p key={x} className="text-xs text-amber-200">{x}</p>)}
    <div className="grid min-w-0 gap-3 lg:grid-cols-3">{data.periods.map(p=><div key={p.expected_year_offset} className="min-w-0 space-y-3 rounded border border-zinc-800 p-3 text-xs">
     <h5 className="font-semibold">{p.end??`Prior year ${p.expected_year_offset} · unresolved`}</h5>
     <div><p>Total operating cash flow: {dollars(p.total_operating_cf.value)}</p><p className="mt-1 text-zinc-500">{words(p.total_operating_cf.status)}{p.total_operating_cf.inputs&&` · ${p.total_operating_cf.inputs.map(key=>`${p.facts[key]?.label??words(key)}: ${dollars(p.facts[key]?.value??null)}`).join(' + ')}`}</p>{p.total_operating_cf.reason&&<p className="mt-1 text-amber-200">{p.total_operating_cf.reason}</p>}</div>
     {Object.entries(p.cash_measures).map(([key,m])=><div key={key}><p>{m.label}</p><p className="mt-1 text-sm">{dollars(m.value)}</p>{p.facts[m.spending_scope]?.value!=null&&<p className="mt-1 text-zinc-400">Reported payments: {dollars(p.facts[m.spending_scope].value)}</p>}<p className="mt-1 text-zinc-500">{m.reason??words(m.status)}</p></div>)}
     {p.operating_scope_reconciliation.difference!=null&&<p className="text-amber-200">Reported total − continuing − discontinued: {dollars(p.operating_scope_reconciliation.difference)} · {words(p.operating_scope_reconciliation.status)}</p>}
     <details><summary className="cursor-pointer">Original facts and filing identities</summary><div className="mt-3 space-y-3">{Object.entries(p.facts).map(([key,f])=><div key={key} className="min-w-0"><p>{f.label}: {dollars(f.value)} · {words(f.status)}</p><p className="mt-1 text-zinc-500">{f.scope}</p>{f.concept&&<p className="mt-1 break-all text-zinc-500">{f.concept}<br/>{f.start} to {f.end}<br/>{f.form} · filed {f.filed} · {f.accn}</p>}{f.reason&&<p className="mt-1 text-amber-200">{f.reason}</p>}</div>)}</div></details>
    </div>)}</div>
    <div className="space-y-2 text-xs">{data.sources.map(s=><a key={s.id} className="block break-all text-emerald-300 underline" href={`/api/backend/api/research/coverage/sources/${s.id}`}>Original SEC response · {s.sha256.slice(0,16)}…</a>)}<button disabled={busy||!data.coverage.source_matched_facts} onClick={()=>void capture()} className="rounded-lg border border-zinc-700 px-3 py-2 text-sm disabled:opacity-40">{busy?'Saving cash-flow evidence…':'Save cash-flow evidence'}</button>{saved&&<a className="block break-all text-emerald-300 underline" href={`/api/backend/api/cashflow-evidence/reports/${saved.id}`}>Open replayed cash-flow capture · SHA-256 {saved.report_sha256}</a>}<p className="break-all text-zinc-500">Context SHA-256 {data.content_sha256}</p></div>
    <details className="text-xs leading-5 text-zinc-500"><summary className="cursor-pointer">Cash-flow scope limits</summary><div className="mt-2 space-y-2">{data.limitations.map(x=><p key={x}>{x}</p>)}</div></details>
   </>}
  </div>
 </details>;
}
