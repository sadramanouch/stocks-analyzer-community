"use client";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

type Archive = { id:string; sha256:string; status_code:number; observed_at:string };
type Calendar = { state:string; reason?:string; date?:string; source_url?:string; scope?:string };
type Comparison = {
  id:number; ticker:string; status:string; recorded_return_pct:number|null;
  reference_price_return_pct:number|null; reference_adjusted_close_return_pct:number|null;
  conditional_recorded_split_return_pct?:number; source_url?:string; source_observed_at?:string;
  entry_date?:string; exit_date?:string; entry_reference_close?:number; exit_reference_close?:number;
  entry_rebased_price?:string; notes:string[]; entry_calendar?:Calendar; exit_calendar?:Calendar;
  calendar_source_archive?:Archive; original_source_document_id?:string;
  provider_symbol?:string;symbol_lineage?:{id:string};
  action_review_notes?:{source:string;note:string;source_archive?:Archive}[];
  distribution_checks?:{source:string;effective_date:string;note:string;source_archive?:Archive}[];
  primary_split_checks?:{effective_date:string;vendor_matches:boolean;source:string;note?:string;corroborating_source?:string;source_archive?:Archive}[];
};
type Overview = {
  documents:number; positions:number; positions_with_reference:number; positions_with_split_ratio_warning:number;
  positions_with_history:number; comparison_statuses:Record<string,number>; non_session_record_dates:number;
  methodology:string; comparisons:Comparison[];
  runs:{id:number;state:string;selected:number;archived:number;failed:number;message:string}[];
  failures:{ticker:string;message:string;source_archive?:Archive}[];
};
type Study = {
  study_id:number|null; observed_at?:string; purchase_rows?:number; eligible_signals?:number;
  completed_reference_studies?:number; verified_executed_returns:number;
  policy:{version:string;entry:string;holding_bars:number;fees_bps_each_side:number;slippage_bps_each_side:number;limitations:string};
  results:{event_id:string;actor:string;asset:string;status:string;reasons:string[];net_reference_return_pct:number|null;benchmark_reference_return_pct:number|null}[];
};
async function read(r:Response) {
  const v=await r.json(); if(!r.ok)throw new Error(typeof v.detail==="string"?v.detail:`Request failed (${r.status})`); return v;
}
const percent=(n:number|null|undefined)=>n==null?"Unknown":`${n.toFixed(2)}%`;
const closedDate=(c?:Calendar)=>c?.state==='scheduled_closed';
const unresolved=(r:Comparison)=>['missing_reference','outside_reference_ranges','corporate_action_review_required','invalid_recorded_dates','invalid_recorded_prices','split_share_change_missing_from_price_ratio'].includes(r.status)||closedDate(r.entry_calendar)||closedDate(r.exit_calendar);
const button="rounded border border-zinc-700 px-3 py-2 text-xs disabled:opacity-40";
function ArchiveLink({archive}:{archive?:Archive}) {
  return archive ? <a className="block text-sky-400 underline" href={`/api/backend/api/market-history/sources/${archive.id}`} target="_blank" rel="noreferrer">Archived response · HTTP {archive.status_code} · SHA {archive.sha256.slice(0,12)}</a> : <span className="block text-zinc-500">Original response archive not captured</span>;
}
export default function MarketHistoryPanel() {
  const [data,setData]=useState<Overview|null>(null),[study,setStudy]=useState<Study|null>(null);
  const [tickers,setTickers]=useState("SVRN,HCAI,ADV,HKIT,RYDE,SKK,TLIH,CAR,MSFT,SPY");
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[view,setView]=useState('unresolved'),[search,setSearch]=useState('');
  const load=useCallback((signal?:AbortSignal)=>apiFetch('/api/market-history',{signal}).then(read).then(v=>{if(!signal?.aborted)setData(v);}).catch(e=>{if(!signal?.aborted)setError(e.message);}),[]);
  useEffect(()=>{const c=new AbortController();void load(c.signal);apiFetch('/api/copy-study',{signal:c.signal}).then(read).then(v=>{if(!c.signal.aborted)setStudy(v);}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[load]);
  const running=data?.runs.some(r=>r.state==='running')??false;
  useEffect(()=>{if(!running)return;const c=new AbortController();const timer=setInterval(()=>void load(c.signal),2000);return()=>{clearInterval(timer);c.abort();};},[running,load]);
  async function refresh(portfolio=false) {
    setBusy(true);setError('');try{await read(await apiFetch('/api/market-history/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tickers:portfolio?[]:tickers.split(',').map(s=>s.trim()).filter(Boolean),limit:25})}));await load();}catch(e){setError(e instanceof Error?e.message:'History unavailable');}finally{setBusy(false);}
  }
  async function evaluate() {
    setBusy(true);setError('');try{setStudy(await read(await apiFetch('/api/copy-study',{method:'POST'})));}catch(e){setError(e instanceof Error?e.message:'Study unavailable');}finally{setBusy(false);}
  }
  const rows=data?.comparisons.filter(r=>r.ticker.includes(search.trim().toUpperCase())&&(view==='all'||view==='unresolved'&&unresolved(r)||view==='missing'&&r.status==='missing_reference'||view==='calendar'&&(closedDate(r.entry_calendar)||closedDate(r.exit_calendar))))??[];
  return <section aria-label="Market reference validation" className="min-w-0 rounded-xl border border-zinc-800 p-5 space-y-4">
    <h2 className="font-semibold">Historical market references & copy study</h2>
    <p className="text-sm text-zinc-400">{data?.documents??'…'} symbol histories archived · {data?.positions_with_reference??0}/{data?.positions??'…'} positions have historical entry references · {data?.positions_with_split_ratio_warning??0} recorded price ratios cross a corroborated split without the share-count change.</p>
    {data&&<p className="text-xs text-amber-200">{data.positions-data.positions_with_history} positions lack an archive · {data.comparison_statuses.missing_reference??0} lack an exact endpoint reference · {data.comparison_statuses.outside_reference_ranges??0} price pairs fall outside regular-session ranges · {data.comparison_statuses.corporate_action_review_required??0} need corporate-action review · {data.non_session_record_dates??0} have literal record dates with no scheduled NYSE regular session. Counts can overlap. Record dates and matching ranges do not prove quote times or fills.</p>}
    <p className="text-xs text-zinc-400">{data?.methodology} Reference prices, recorded ratios and conditional scenarios remain separate from verified account performance.</p>
    <div className="flex flex-wrap gap-2"><input aria-label="Historical reference tickers" value={tickers} onChange={e=>setTickers(e.target.value)} className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"/><button className={button} disabled={busy||running} onClick={()=>void refresh()}>Check these histories</button><button className={button} disabled={busy||running} onClick={()=>void refresh(true)}>Check next 25 portfolio symbols</button></div>
    {data?.runs.slice(0,1).map(r=><p key={r.id} className="text-xs text-zinc-400">{r.state} · {r.archived}/{r.selected} archived · {r.failed} unavailable · {r.message}</p>)}
    {!!data?.failures.length&&<details className="text-xs text-amber-200"><summary>Recorded source and validation failures</summary><p className="mt-2">Prior failed attempts remain recorded even if a later archive succeeds.</p>{data.failures.map((f,i)=><p className="mt-2" key={i}>{f.ticker}: {f.message}<ArchiveLink archive={f.source_archive}/></p>)}</details>}
    <div className="flex flex-wrap gap-2 text-xs"><select aria-label="Position reference view" value={view} onChange={e=>setView(e.target.value)} className="rounded border border-zinc-700 bg-zinc-900 p-2"><option value="unresolved">Unresolved checks</option><option value="all">All original positions</option><option value="missing">Missing endpoint references</option><option value="calendar">Non-session record dates</option></select><input aria-label="Find position reference" placeholder="Find ticker…" value={search} onChange={e=>setSearch(e.target.value)} className="min-w-0 rounded border border-zinc-700 bg-zinc-900 p-2"/><span className="p-2 text-zinc-400">{rows.length} of {data?.positions??'…'} positions shown</span></div>
    <div className="overflow-x-auto"><table className="min-w-[680px] w-full text-left text-xs"><thead><tr><th>Position</th><th>Recorded price ratio</th><th>Vendor close ratio</th><th>Split-only scenario</th><th>Evidence</th></tr></thead><tbody>{rows.map(r=><tr key={r.id} className="border-t border-zinc-800">
      <td className="py-3 pr-3">{r.ticker}<span className="block text-zinc-500">{r.entry_date??'Unknown'} → {r.exit_date??'Open; mark time unknown'}</span></td>
      <td>{percent(r.recorded_return_pct)}</td><td>{percent(r.reference_price_return_pct)}<span className="block text-zinc-500">Adjusted-close proxy {percent(r.reference_adjusted_close_return_pct)}</span></td>
      <td>{percent(r.conditional_recorded_split_return_pct)}<span className="block text-amber-200">Conditional; excludes costs and fractional-share treatment</span></td>
      <td className="max-w-72 py-3"><span className="block text-amber-200">{r.status.replaceAll('_',' ')}</span>{r.source_url?<a href={r.source_url} target="_blank" rel="noreferrer" className="text-sky-400 underline">Vendor history</a>:<span className="text-zinc-400">History unavailable; no replacement symbol or price inferred</span>}
        <details><summary>Basis and source comparison</summary><p className="mt-2 text-zinc-400">Source entry/exit closes: {r.entry_reference_close??'Unknown'} / {r.exit_reference_close??'Unknown'} · rebased recorded entry {r.entry_rebased_price??'Unknown'} · retrieved {r.source_observed_at?new Date(r.source_observed_at).toLocaleString():'Unknown'}</p>
          {r.symbol_lineage&&<a className="mt-2 block text-sky-400 underline" href={`/api/backend/api/market-history/lineage/${r.symbol_lineage.id}`} target="_blank" rel="noreferrer">Unchanged-CUSIP symbol proof · provider {r.provider_symbol}</a>}
          {r.primary_split_checks?.map(c=><div className="mt-2" key={c.source+c.effective_date}><a href={c.source} target="_blank" rel="noreferrer" className="text-sky-400 underline">Primary split trading date {c.effective_date}</a> · {c.vendor_matches?'Vendor ratio agrees':'Vendor evidence differs or is missing'}{c.note&&<p className="text-amber-200">{c.note}</p>}<ArchiveLink archive={c.source_archive}/>{c.corroborating_source&&<a href={c.corroborating_source} target="_blank" rel="noreferrer" className="block text-sky-400 underline">Additional primary notice</a>}</div>)}
          <ul className="mt-2 pl-4 list-disc text-amber-200">{r.distribution_checks?.map(d=><li key={d.source}><a href={d.source} target="_blank" rel="noreferrer" className="text-sky-400 underline">Distribution notice {d.effective_date}</a> · {d.note}<ArchiveLink archive={d.source_archive}/></li>)}{r.action_review_notes?.map(a=><li key={a.source}><a href={a.source} target="_blank" rel="noreferrer" className="text-sky-400 underline">Primary action review</a> · {a.note}<ArchiveLink archive={a.source_archive}/></li>)}{r.notes.map(n=><li key={n}>{n}</li>)}</ul>
          {(closedDate(r.entry_calendar)||closedDate(r.exit_calendar))&&<div className="mt-2"><a href={r.entry_calendar?.source_url} target="_blank" rel="noreferrer" className="text-sky-400 underline">Published NYSE calendar</a><ArchiveLink archive={r.calendar_source_archive}/></div>}
          {r.original_source_document_id&&<a className="block text-sky-400 underline mt-2" href={`/api/backend/api/market-history/sources/${r.original_source_document_id}`} target="_blank" rel="noreferrer">Original chart response</a>}
        </details>
      </td></tr>)}</tbody></table></div>
    {!rows.length&&<p className="text-xs text-zinc-500">No positions match this view.</p>}
    <div className="border-t border-zinc-800 pt-4 space-y-2"><h3 className="font-medium">Prospective copy reference study</h3><button className={button} disabled={busy||running} onClick={()=>void evaluate()}>Evaluate observed purchases</button>
      {study&&<><p className="text-xs text-zinc-400">Study {study.study_id??'not captured'} · policy {study.policy.version} · {study.observed_at?new Date(study.observed_at).toLocaleString():'Capture needed'}</p><p className="text-xs text-zinc-400">{study.purchase_rows??0} observed purchase rows · {study.eligible_signals??0} eligible under this policy · {study.completed_reference_studies??0} completed reference studies · {study.verified_executed_returns} verified executed returns.</p><p className="text-xs text-zinc-400">{study.policy.entry} · {study.policy.holding_bars} complete trading bars · {study.policy.fees_bps_each_side} bps fees + {study.policy.slippage_bps_each_side} bps slippage per side. {study.policy.limitations}</p>{study.results.map(r=><details key={r.event_id} className="text-xs text-zinc-400"><summary>{r.asset} · {r.actor} · {r.status.replaceAll('_',' ')}</summary><p className="mt-2">{r.reasons.join(' · ')}</p>{r.net_reference_return_pct!==null&&<p>Hypothetical reference: {percent(r.net_reference_return_pct)} · SPY with same costs: {percent(r.benchmark_reference_return_pct)}</p>}</details>)}</>}
    </div>{error&&<p role="alert" className="text-sm text-red-300">{error}</p>}
  </section>;
}
