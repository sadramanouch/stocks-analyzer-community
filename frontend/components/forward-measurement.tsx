"use client";
import {useCallback,useEffect,useState} from "react";
import Link from "next/link";
import {apiFetch} from "@/lib/apiFetch";

type Checkpoint={id:number;observed_at:string;input_sha256:string;reference_status:string;issues:string[];account:{marked_value:string;cash:string;event_count:number}};
type Measurement={checkpoint_count:number;checkpoints:Checkpoint[];reference_time_weighted_return:string|null;evidence_state:string;integrity_errors:{id:number;message:string}[];intervals:{from:string;to:string;reference_return:string|null;external_flow:string;issues:string[]}[];benchmark_status:string;limitations:string;method_source_url:string};
type Coverage={version:number;scope_count:number;scope:string;counts:Record<string,number>;queued:number;next_tickers:string[];issues:Record<string,number>;original_sec_receipts:Record<string,number>;schedule:string};
type Job={id:number;state:string;completed:number;failed:number;tickers_json:string;current_ticker:string|null};
const button="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500 disabled:opacity-40";
const money=(v:string)=>Number(v).toLocaleString(undefined,{style:"currency",currency:"USD"});
const percent=(v:string|null)=>v===null?"Not measured":`${(Number(v)*100).toFixed(2)}%`;
async function read(response:Response){const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:`Request failed (${response.status})`);return data;}

export default function ForwardMeasurement({revision,onRefresh}:{revision:number;onRefresh:()=>void}){
  const [measurement,setMeasurement]=useState<Measurement|null>(null);const [coverage,setCoverage]=useState<Coverage|null>(null);const [job,setJob]=useState<Job|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState("");const [notice,setNotice]=useState("");
  const load=useCallback(async(signal?:AbortSignal)=>{
    const results=await Promise.allSettled([apiFetch('/api/forward-performance',{signal}).then(read),apiFetch('/api/research/coverage',{signal}).then(read),apiFetch('/api/research?limit=1',{signal}).then(read)]);
    if(signal?.aborted)return;
    if(results[0].status==='fulfilled')setMeasurement(results[0].value);else setError(results[0].reason?.message??'Measurement unavailable');
    if(results[1].status==='fulfilled')setCoverage(results[1].value);else setError(results[1].reason?.message??'Coverage unavailable');
    if(results[2].status==='fulfilled')setJob(results[2].value.refresh_job);
  },[]);
  useEffect(()=>{const c=new AbortController();void Promise.resolve().then(()=>load(c.signal));return()=>c.abort();},[load,revision]);
  const running=job?.state==='running';
  useEffect(()=>{if(!running)return;const timer=setInterval(()=>void load(),4000);return()=>clearInterval(timer);},[running,load]);
  useEffect(()=>{if(job?.state==='completed')onRefresh();},[job?.id,job?.state,onRefresh]);
  async function action(path:string){setBusy(true);setError("");setNotice("");try{const data=await read(await apiFetch(path,{method:'POST'}));setNotice(path.includes('coverage')?`Source refresh ${data.job_id} started for ${data.count} saved symbols.`:`Checkpoint ${data.id} captured. ${data.issues.length?'Missing inputs are recorded as blocked.':'This is paper reference evidence.'}`);await load();}catch(e){setError(e instanceof Error?e.message:'Action failed');}finally{setBusy(false);}}
  return <>
    <section className="space-y-4 rounded-xl border border-zinc-800 p-5" aria-labelledby="measurement-heading">
      <h2 id="measurement-heading" className="text-lg">Forward measurement · paper reference evidence</h2>
      <p className="text-sm leading-6 text-zinc-400">Checkpoints observe the current ledger. They preserve exact source inputs and replay cash, shares and costs before measuring returns. Deposits and withdrawals are external flows. A flow while shares are held needs a boundary valuation; a backdated addition blocks the interval.</p>
      {error&&<p role="alert" className="text-sm text-red-300">{error}</p>}{notice&&<p role="status" className="text-sm text-emerald-300">{notice}</p>}
      <div className="grid gap-3 sm:grid-cols-3"><div><p className="text-xs text-zinc-500">Recorded checkpoints</p><p>{measurement?.checkpoint_count??'—'}</p></div><div><p className="text-xs text-zinc-500">Paper reference time-weighted return</p><p>{percent(measurement?.reference_time_weighted_return??null)}</p></div><div><p className="text-xs text-zinc-500">Evidence state</p><p className="text-sm">{measurement?.evidence_state.replaceAll('_',' ')??'Loading'}</p></div></div>
      <button className={button} disabled={busy||revision===0} onClick={()=>void action('/api/forward-performance/capture')}>Capture current measurement checkpoint</button>
      <p className="text-xs text-zinc-500">A checkpoint is also saved after new workspace funding, recorded paper trades and reference marks, and weekdays at 17:20 New York time. Capturing does not place trades or update prices. One observation and cash-only balances do not establish strategy results.</p>
      {measurement?.integrity_errors.map(e=><p key={e.id} className="text-sm text-red-300">Checkpoint {e.id}: {e.message}</p>)}
      {!!measurement?.checkpoints.length&&<div className="max-w-full overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-zinc-500"><tr><th className="p-2">Observation</th><th className="p-2">Reference NAV</th><th className="p-2">Status / exact evidence</th></tr></thead><tbody>{[...measurement.checkpoints].reverse().map(c=><tr key={c.id} className="border-t border-zinc-800"><td className="p-2">{new Date(c.observed_at).toLocaleString()}</td><td className="p-2">{money(c.account.marked_value)}</td><td className="p-2"><a className="text-emerald-300 underline" href={`/api/backend/api/forward-performance/checkpoints/${c.id}`}>Checkpoint {c.id} · {c.reference_status.replaceAll('_',' ')}</a>{c.issues.map((i,n)=><p key={n} className="max-w-xl text-xs text-amber-200">{i}</p>)}</td></tr>)}</tbody></table></div>}
      {measurement?.intervals.filter(i=>i.issues.length).map((i,n)=><p key={n} className="text-xs text-amber-200">Interval ending {new Date(i.to).toLocaleString()}: {i.issues.join(' ')}</p>)}
      <p className="text-xs leading-5 text-zinc-500">{measurement?.benchmark_status} {measurement?.limitations} <a className="underline" href={measurement?.method_source_url} target="_blank" rel="noreferrer">Cash-flow methodology reference</a></p>
    </section>
    <section className="space-y-3 rounded-xl border border-zinc-800 p-5" aria-labelledby="coverage-heading">
      <h2 id="coverage-heading" className="text-lg">Expand financial source coverage</h2>
      <p className="text-sm text-zinc-400">{coverage?.scope_count??'—'} original portfolio symbols · {coverage?.counts.documented_inputs??0} with documented required annual inputs · {coverage?.queued??0} eligible common equities awaiting evidence. Financial refreshes preserve original SEC responses and compare all four required annual facts with their exact issuer, period, unit and accession.</p>
      <p className="break-words text-xs text-zinc-500">Next bounded batch: {coverage?.next_tickers.join(', ')||'No eligible symbols pending'}. {coverage?.schedule}</p>
      {job&&<p className="text-xs">Source job {job.id}: {job.state} · attempted {job.completed}/{JSON.parse(job.tickers_json).length} · fetch failures {job.failed}{job.current_ticker?` · ${job.current_ticker}`:''}. A successful fetch can still fail evidence validation.</p>}
      <div className="flex flex-wrap gap-3"><button className={button} disabled={busy||running||!coverage?.next_tickers.length} onClick={()=>void action('/api/research/coverage/refresh')}>Refresh next saved portfolio batch</button><Link className={button} href="/research">Inspect complete research list</Link></div>
      {!!coverage&&<p className="text-xs text-zinc-500">Archived SEC receipt HTTP statuses: {Object.entries(coverage.original_sec_receipts).map(([s,n])=>`${s}: ${n}`).join(' · ')||'None yet'}. A denial pauses company-facts requests for 15 minutes. Provider-only values remain unverified.</p>}
      <details className="text-xs text-zinc-400"><summary className="cursor-pointer">Why records need evidence</summary><ul className="mt-3 space-y-2">{Object.entries(coverage?.issues??{}).sort((a,b)=>b[1]-a[1]).slice(0,12).map(([reason,count])=><li key={reason}>{count} records: {reason}</li>)}</ul></details>
    </section>
  </>;
}
