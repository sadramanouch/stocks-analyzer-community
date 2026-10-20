"use client";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

type Result = {costed_stock_reference_return:string|null;costed_benchmark_reference_return:string|null;reference_difference:string|null};
type Trial = {id:string;ticker:string;accession:string;registered_at:string;entry_date:string;exit_date:string;input_sha256:string;state:string;purchase_lines:number;buyers:string[];missing_capture_sessions:string[];later_amendments:unknown[];observations:{id:number;session_date:string}[];result:Result|null;collection_issue?:string};
type Data = {enabled:boolean;observed_at:string;trials:Trial[];completed_trials:number;verified_executed_returns:number;integrity_errors:{id:string;message:string}[];latest_screening:{research_version:number;registered_at:string;screened:unknown[]}|null;policy:{version:string;selection:string;sampling:string;entry:string;exit:string;cost_model:string;price_basis:string;capture:string;limitations:string};runs:{id:number;state:string;message:string;finished_at:string|null}[]};
const button="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500 disabled:opacity-40";
const percent=(v:string|null|undefined)=>v==null?"Pending":`${(Number(v)*100).toFixed(2)}%`;
async function read(r:Response){const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:`Request failed (${r.status})`);return d;}

export default function SignalTrials(){
  const [data,setData]=useState<Data|null>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const load=useCallback((signal?:AbortSignal)=>apiFetch('/api/signal-trials',{signal}).then(read).then(d=>{if(!signal?.aborted){setData(d);setError("");}}).catch(e=>{if(!signal?.aborted)setError(e instanceof Error?e.message:"Signal trials unavailable");}),[]);
  useEffect(()=>{const c=new AbortController();void load(c.signal);return()=>c.abort();},[load]);
  const running=data?.runs[0]?.state==='running';
  useEffect(()=>{if(!running)return;const timer=setInterval(()=>void load(),4000);return()=>clearInterval(timer);},[running,load]);
  async function action(name:string){setBusy(true);setError("");try{await read(await apiFetch(`/api/signal-trials/${name}`,{method:'POST'}));await load();}catch(e){setError(e instanceof Error?e.message:"Trial action failed");}finally{setBusy(false);}}
  return <section aria-label="Prospective public-purchase trials" className="space-y-4 rounded-xl border border-zinc-800 p-5">
    <div><p className="text-xs uppercase tracking-widest text-emerald-400">Financial evidence + public purchases</p><h2 className="mt-2 text-xl font-medium">Study results</h2><p className="mt-2 max-w-4xl text-sm leading-6 text-zinc-400">Compare registered purchase signals with SPY over five closes. Results use price references and modeled costs, not real trades.</p></div>
    {error&&<p role="alert" className="text-sm text-red-300">{error}</p>}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{[["Registered studies",data?data.trials.length:'Loading'],["Completed studies",data?data.completed_trials:'Loading'],["Verified executed returns",data?data.verified_executed_returns:'Loading'],["Automatic collection",data?(data.enabled?'Active':'Paused'):'Loading']].map(([label,value])=><div key={label} className="rounded border border-zinc-800 p-3"><p className="text-xs text-zinc-500">{label}</p><p className="mt-2 text-lg">{value}</p></div>)}</div>
    {data&&<p className="text-xs text-zinc-500">Status observed {new Date(data.observed_at).toLocaleString()}.</p>}
    <div className="flex flex-wrap gap-3"><button className={button} disabled={busy||running||!data} onClick={()=>void action('start')}>{data?.enabled?'Screen signals and check planned closes':'Start or resume prospective trials'}</button>{data?.enabled&&<button className={button} disabled={busy} onClick={()=>void action('pause')}>Pause future trial passes</button>}<button className={button} disabled={busy} onClick={()=>void load()}>Reload trials</button></div>
    {data?.runs[0]&&<p role="status" className="text-xs text-zinc-400">Latest pass: {data.runs[0].state.replaceAll('_',' ')} · {data.runs[0].message}</p>}
    {data?.latest_screening&&<p className="text-xs text-zinc-500">Last changed screening receipt: edition {data.latest_screening.research_version} · {new Date(data.latest_screening.registered_at).toLocaleString()}. The eligible universe is limited to source disclosures actually observed by this application.</p>}
    {data?.integrity_errors.map(e=><p role="alert" key={e.id} className="break-words text-sm text-red-300">Evidence blocked: {e.message} · {e.id}</p>)}
    {data&&!data.trials.length&&<p className="text-sm text-zinc-400">No filing trials registered. A fresh purchase and documented financial inputs must both qualify; an empty set is not a zero return.</p>}
    {data?.trials.map(t=><article key={t.id} className="space-y-3 rounded-lg border border-zinc-800 p-4">
      <div className="flex flex-wrap justify-between gap-2"><h3 className="font-medium">{t.ticker} · {t.buyers.join(', ')}</h3><span className="text-sm text-amber-200">{t.state.replaceAll('_',' ')}</span></div>
      <p className="text-xs text-zinc-400">{t.purchase_lines} purchase line{t.purchase_lines===1?'':'s'} grouped into one filing trial · {t.observations.length} of 5 planned closes observed.</p>
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3"><div><dt className="text-xs text-zinc-500">Registered</dt><dd>{new Date(t.registered_at).toLocaleString()}</dd></div><div><dt className="text-xs text-zinc-500">Planned entry close · New York</dt><dd>{t.entry_date}</dd></div><div><dt className="text-xs text-zinc-500">Planned exit close · New York</dt><dd>{t.exit_date}</dd></div></dl>
      <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3"><p>Stock after modeled costs: <strong>{percent(t.result?.costed_stock_reference_return)}</strong></p><p>SPY with same costs: <strong>{percent(t.result?.costed_benchmark_reference_return)}</strong></p><p>Difference: <strong>{t.result?.reference_difference==null?'Pending':`${(Number(t.result.reference_difference)*100).toFixed(2)} pp`}</strong></p></div>
      {t.collection_issue&&<p className="text-xs text-amber-200">Collection review: {t.collection_issue}</p>}
      {!!t.missing_capture_sessions.length&&<p className="text-xs text-amber-200">Missed archive deadlines: {t.missing_capture_sessions.join(', ')}. These observations cannot be backfilled into a completed prospective result.</p>}
      {!!t.later_amendments.length&&<p className="text-xs text-amber-200">A later amendment requires original-line reconciliation.</p>}
      <details className="text-xs text-zinc-500"><summary className="cursor-pointer">Frozen evidence and trial identity</summary><p className="mt-2 break-all">Filing {t.accession}<br/>Input SHA-256 {t.input_sha256}</p><a className="mt-2 inline-block text-emerald-300 underline" href={`/api/backend/api/signal-trials/trials/${t.id}`}>Inspect frozen policy, financial context, purchase lines and replayed observations</a>{t.observations.map(o=><p key={o.id}><a className="mt-2 inline-block text-emerald-300 underline" href={`/api/backend/api/signal-trials/observations/${o.id}`}>{o.session_date} · exact archived observation</a></p>)}</details>
    </article>)}
    <details className="text-xs leading-5 text-zinc-500"><summary className="cursor-pointer">Fixed policy, source checks and limits</summary><div className="mt-2 space-y-2"><p>{data?.policy.version} · {data?.policy.selection}</p><p>{data?.policy.sampling}</p><p>{data?.policy.entry} {data?.policy.exit}</p><p>{data?.policy.cost_model}</p><p>{data?.policy.price_basis} {data?.policy.capture}</p><p>While active, bounded passes run weekdays at 09:20 and 18:20 New York time. No future result is inferred. New qualifying filings use their own later planned windows; repeated filings are never re-enrolled.</p><p>{data?.policy.limitations}</p><a href="https://www.nyse.com/trade/hours-calendars" target="_blank" rel="noreferrer" className="underline">Exchange calendar reference</a></div></details>
  </section>;
}
