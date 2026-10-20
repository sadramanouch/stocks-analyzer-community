"use client";
import {useCallback,useEffect,useState} from 'react';
import {apiFetch} from '@/lib/apiFetch';

type Quote={provider:string;state:string;price?:string;as_of?:string;currency?:string;exchange?:string;reported_feed?:string;reason?:string;source_id:string|null;source_sha256?:string};
type Check={id:string;report_sha256:string;report:{ticker:string;captured_at:string;quotes:Quote[];comparison:{state:string;absolute_difference:string|null;relative_difference:string|null;time_difference_seconds:string|null;price_tolerance:string|null};issues:string[];policy:{limits:string;independence:string}}};
const words=(s:string)=>s.replaceAll('_',' ');
const numeric=(s:string|null|undefined)=>s==null?'Unknown':Number(s).toLocaleString(undefined,{maximumFractionDigits:6});
export default function QuoteChecks({ticker,onCaptured,captureAllowed=true}:{ticker:string;onCaptured?:()=>void;captureAllowed?:boolean}){
 const [check,setCheck]=useState<Check|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const load=useCallback(async(signal:AbortSignal)=>{try{
  const response=await apiFetch(`/api/quote-checks/${encodeURIComponent(ticker)}`,{signal});const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Quote checks unavailable');
  if(!signal.aborted){setCheck(data.report?data:null);setError('');}
 }catch(e){if(!signal.aborted)setError(e instanceof Error?e.message:'Quote checks unavailable');}},[ticker]);
 useEffect(()=>{const c=new AbortController();void Promise.resolve().then(()=>load(c.signal));return()=>c.abort();},[load]);
 async function capture(){setBusy(true);setError('');try{
  const response=await apiFetch(`/api/quote-checks/${encodeURIComponent(ticker)}/capture`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Quote check failed');setCheck(data);onCaptured?.();
 }catch(e){setError(e instanceof Error?e.message:'Quote check failed');}finally{setBusy(false);}}
 const r=check?.report,c=r?.comparison;
 return <div aria-label={`Cross-provider quote checks for ${ticker}`} className="min-w-0 space-y-3 rounded-lg border border-zinc-700 p-3">
  <h4 className="font-medium">Cross-provider quote checks · {ticker}</h4>
  <p className="text-xs leading-5 text-zinc-400">Compare separately retrieved Yahoo and CNBC regular-market references. These may share upstream market data. A comparison never certifies an executable price or your cached dashboard quote.</p>
  <button disabled={busy||!captureAllowed} onClick={()=>void capture()} className="rounded-lg border border-zinc-700 px-3 py-2 text-sm disabled:opacity-40">{busy?'Checking both sources…':'Check Yahoo and CNBC'}</button>
  <p className="text-xs text-zinc-500">Captures are retained and replayed from original bytes. Requests within five minutes reuse the last check. Active study symbols are checked on a bounded New York weekday schedule; pausing study collection also pauses those checks.</p>
  {error&&<p role="alert" className="text-sm text-amber-200">{error}</p>}
  {!r&&!error&&<p className="text-xs text-zinc-400">No captured comparison yet. Nothing is certified by the absence of a check.</p>}
  {r&&c&&<>
   <p className="text-sm">At capture: <strong>{words(c.state)}</strong></p>
   <p className="text-xs text-zinc-400">Captured {new Date(r.captured_at).toLocaleString()}. This is a dated observation, not a continuously refreshed quote.</p>
   <div className="grid min-w-0 gap-3 sm:grid-cols-2">{r.quotes.map(q=><div key={q.provider} className="min-w-0 space-y-1 rounded border border-zinc-800 p-2 text-xs">
    <p className="uppercase">{q.provider} · {words(q.state)}</p>
    {q.price&&<p>{q.currency} {numeric(q.price)} · {q.exchange}<br/>{q.as_of?new Date(q.as_of).toLocaleString():'Missing source time'}</p>}
    <p className="break-words text-zinc-400">{q.reported_feed??q.reason??'Source unavailable'}</p>
    {q.source_id&&<a className="block break-all text-emerald-300 underline" href={`/api/backend/api/quote-checks/sources/${q.source_id}`}>Original response · {q.source_sha256?.slice(0,16)}…</a>}
   </div>)}</div>
   {c.absolute_difference!=null&&<p className="text-xs text-zinc-400">Absolute difference: USD {numeric(c.absolute_difference)} · relative difference: {(Number(c.relative_difference)*100).toFixed(4)}% · declared tolerance: USD {numeric(c.price_tolerance)} · source time gap: {numeric(c.time_difference_seconds)} seconds.</p>}
   {r.issues.map(x=><p key={x} className="text-xs text-amber-200">{x}</p>)}
   <details className="text-xs leading-5 text-zinc-500"><summary className="cursor-pointer">Comparison policy and retained evidence</summary><div className="mt-2 space-y-2"><p>Exact current common-share listing and USD are required. Quotes must be within 20 minutes of capture, no more than 60 seconds apart and inside a supported scheduled regular session. Tolerance is the greater of one cent and 0.1% of the lower price.</p><p>{r.policy.independence} {r.policy.limits}</p><a className="block break-all text-emerald-300 underline" href={`/api/backend/api/quote-checks/reports/${check.id}`}>Open replayed comparison · SHA-256 {check.report_sha256}</a></div></details>
  </>}
 </div>;
}
