"use client";
import {useCallback,useEffect,useState} from "react";
import Link from "next/link";
import {apiFetch} from "@/lib/apiFetch";

type Outcome={advanced:boolean;tickers:string[];child_state:string;imported:number;failed:number;outcomes:{ticker:string;status:string;currently_fresh:boolean;issues:string[]}[]};
type Pass={id:number;kind:string;state:string;cursor:number;total:number;scope_version:number;updated_at:string;basis:string;message:string;since_date:string|null;scope_sha256:string;outcomes:Outcome[]};
type Data={campaigns:Pass[];limits:string;restart_policy:string};
const button="rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-400 disabled:opacity-40";
export default function SourceCampaigns(){
  const [data,setData]=useState<Data|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const load=useCallback(async(signal?:AbortSignal)=>{
    try{const r=await apiFetch("/api/source-campaigns",{signal});const d=await r.json();if(!r.ok)throw Error(d.detail||"Source coverage unavailable");setData(d);setError("");}
    catch(e){if(!signal?.aborted)setError(e instanceof Error?e.message:"Source coverage unavailable");}
  },[]);
  useEffect(()=>{const controller=new AbortController();void Promise.resolve().then(()=>load(controller.signal));return()=>controller.abort();},[load]);
  const active=data?.campaigns.find(p=>["queued","running","paused"].includes(p.state));
  async function action(route:string){setBusy(true);setError("");try{const r=await apiFetch(`/api/source-campaigns/${route}`,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});const d=await r.json();if(!r.ok)throw Error(d.detail||"Source action failed");await load();}catch(e){setError(e instanceof Error?e.message:"Source action failed");}finally{setBusy(false);}}
  return <section className="space-y-4 rounded-xl border border-zinc-800 p-5">
    <div><h2 className="text-xl font-semibold">Portfolio source coverage</h2><p className="mt-2 text-sm leading-6 text-zinc-400">Work through the eligible original portfolio with a frozen, resumable pass. Annual evidence requires exact primary statement values and matching instruments. Insider discovery checks every currently documented eligible company beyond the focused 25-company watchlist.</p></div>
    {error&&<p role="alert" className="text-sm text-red-300">{error}</p>}
    <div className="flex flex-wrap gap-3"><button className={button} disabled={busy||!!active} onClick={()=>void action("financial/start")}>Expand remaining annual evidence</button><button className={button} disabled={busy||!!active} onClick={()=>void action("insider/start")}>Discover public insider filings</button><button className={button} disabled={busy} onClick={()=>void load()}>Check coverage progress</button></div>
    {!data&&<p className="text-sm text-zinc-500">Loading source passes…</p>}
    {data&&data.campaigns.length===0&&<p className="text-sm text-zinc-400">No portfolio source pass has been started.</p>}
    {data?.campaigns.map(pass=>{const receipts=pass.outcomes.filter(o=>o.advanced),qualified=receipts.flatMap(o=>o.outcomes).filter(o=>o.status==="documented_inputs"&&o.currently_fresh).length,imported=receipts.reduce((sum,o)=>sum+o.imported,0),unresolved=receipts.flatMap(o=>o.outcomes).filter(o=>o.status!=="documented_inputs"||!o.currently_fresh);
      return <article key={pass.id} className="space-y-3 border-t border-zinc-800 pt-4">
        <p className="font-medium">{pass.kind==="financial"?"Annual financial evidence":"Public insider discovery"} · {pass.state} · {pass.cursor}/{pass.total} companies attempted</p>
        <p className="text-sm text-zinc-400">{pass.kind==="financial"?`${qualified} qualified after their batch checks; ${unresolved.length} still need evidence or review.`:`${imported} new original filings imported. Recent seven-day index window since ${pass.since_date}; at most 10 unparsed documents per issuer.`} These counts describe this pass; they do not establish complete coverage or profitability.</p>
        <p className="text-xs leading-5 text-zinc-500">{pass.message} Saved scope edition {pass.scope_version}. Updated {new Date(pass.updated_at).toLocaleString()}. Later data can change eligibility; open <Link className="underline" href="/research">Research</Link> for current findings and <Link className="underline" href="/invest">Equity decisions</Link> for purchase-policy checks.</p>
        <div className="flex flex-wrap gap-3"><a className={button} href={`/api/backend/api/source-campaigns/${pass.id}/scope`} target="_blank" rel="noreferrer">View frozen scope & hash</a>{["queued","running"].includes(pass.state)&&<button className={button} disabled={busy} onClick={()=>void action(`${pass.id}/pause`)}>Pause after current batch</button>}{pass.state==="paused"&&<button className={button} disabled={busy} onClick={()=>void action(`${pass.id}/resume`)}>Resume frozen pass</button>}{["queued","running","paused"].includes(pass.state)&&<button className={button} disabled={busy} onClick={()=>void action(`${pass.id}/cancel`)}>Cancel pass</button>}</div>
        {unresolved.length>0&&<details className="text-sm text-zinc-400"><summary className="cursor-pointer">Companies needing review ({unresolved.length})</summary><ul className="mt-3 space-y-2">{unresolved.map(o=><li key={o.ticker}><span className="font-medium text-zinc-200">{o.ticker}</span>: {o.issues.join(" ")||"Required evidence not qualified."}</li>)}</ul></details>}
      </article>;
    })}
    <p className="text-xs leading-5 text-zinc-500">{data?.limits} SEC access blocks and storage limits pause the pass. {data?.restart_policy} No broker connection, live orders, verified trader profits or guaranteed returns are created.</p>
  </section>;
}
