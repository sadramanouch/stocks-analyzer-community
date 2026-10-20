"use client";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

type Fact = { status:string;value:string|null;reason?:string;concept?:string;start?:string;end?:string;filed?:string;accn?:string;form?:string;unit?:string };
type Metric = { status:string;value:string|null;reason?:string };
type Period = { end:string|null;expected_year_offset:number;annual_inputs_coherent:boolean;annual_fields:Record<string,Fact>;balance_fields:Record<string,Fact>;metrics:Record<string,Metric> };
type Context = { ticker:string;research_version:number;methodology:string;status:string;saved_at:string;content_sha256:string;issues:string[];limitations:string[];periods:Period[];
  sources:{id:string;source_url:string;observed_at:string;sha256:string}[];three_year_summary?:{status:string;ocf_less_ppe_sum:string|null;positive_cashflow_periods:number|null} };
const FLOW:Record<string,string> = {revenue:"Revenue",net_income:"Net income",operating_cf:"Operating cash flow",capex:"PP&E payments"};
const BALANCE:Record<string,string> = {cash:"Cash and cash equivalents",current_assets:"Current assets",current_liabilities:"Current liabilities",assets:"Assets",liabilities:"Liabilities",stockholders_equity:"Stockholders’ equity",equity_including_nci:"Equity including noncontrolling interests",debt_current_portion:"Long-term debt · current portion",debt_noncurrent:"Long-term debt · noncurrent",short_term_borrowings:"Short-term borrowings"};
const format = (v:string|null|undefined, kind:"money"|"ratio"|"percent"="money") => {
  if(v==null)return "Unknown";
  const n=Number(v);if(!Number.isFinite(n))return v;
  if(kind==="percent")return `${(n*100).toLocaleString(undefined,{maximumFractionDigits:2})}%`;
  return n.toLocaleString(undefined,kind==="money"?{style:"currency",currency:"USD",maximumFractionDigits:2}:{maximumFractionDigits:3});
};
function Detail({fact}:{fact:Fact}) {
  return <details className="mt-1 text-xs text-zinc-500"><summary className="cursor-pointer">{fact.status==="source_matched"?"Fact provenance":fact.status.replaceAll("_"," ")}</summary>{fact.reason&&<p className="mt-1 text-amber-200">{fact.reason}</p>}{fact.concept&&<p className="mt-1 break-words">{fact.concept}<br/>{fact.start?`${fact.start} to `:""}{fact.end} · {fact.unit}<br/>{fact.form} · filed {fact.filed}<br/>Accession {fact.accn}<br/>Exact source amount: {fact.value}</p>}</details>;
}
export default function FinancialContext({ticker,version}:{ticker:string;version:number}) {
  const key=`${ticker}:${version}`;
  const [loaded,setLoaded]=useState<{key:string;data?:Context;error?:string}|null>(null);
  useEffect(()=>{const c=new AbortController();const requestKey=`${ticker}:${version}`;
    apiFetch(`/api/financial-context/${encodeURIComponent(ticker)}?version=${version}`,{signal:c.signal}).then(async r=>{const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"Financial context unavailable.");return d as Context;})
      .then(data=>{if(!c.signal.aborted)setLoaded({key:requestKey,data});}).catch(e=>{if(!c.signal.aborted)setLoaded({key:requestKey,error:e instanceof Error?e.message:"Financial context unavailable."});});return()=>c.abort();
  },[ticker,version]);
  const current=loaded?.key===key?loaded:null;const data=current?.data;const latest=data?.periods[0];
  return <section aria-label={`Primary financial context for ${ticker}`} className="min-w-0 space-y-4 rounded-xl border border-zinc-800 p-4">
    <div><h3 className="text-lg font-medium">Annual history & balance-sheet evidence</h3><p className="mt-2 text-xs leading-5 text-zinc-400">Three annual periods from archived SEC facts, with exact issuer, USD units and filing provenance. Ratios require aligned periods and filings. These dated figures add context to financial and public-purchase research; they do not establish expected returns.</p></div>
    {!current&&<p role="status" className="text-sm text-zinc-500">Loading original financial evidence…</p>}
    {current?.error&&<p role="alert" className="text-sm text-amber-200">{current.error}</p>}
    {data&&<>
      <p className="text-xs text-zinc-500">{ticker} · saved research edition {data.research_version} · {data.status.replaceAll("_"," ")}</p>
      {data.issues.length>0&&<ul className="list-disc space-y-1 pl-4 text-xs text-amber-200">{data.issues.map(i=><li key={i}>{i}</li>)}</ul>}
      {latest&&<>
        <div role="region" aria-label="Annual financial history table" tabIndex={0} className="overflow-auto rounded-lg border border-zinc-800"><table className="w-full text-left text-sm"><thead className="bg-zinc-900 text-xs text-zinc-400"><tr><th className="p-3">USD annual facts</th>{data.periods.map(p=><th key={p.expected_year_offset} className="p-3 whitespace-nowrap">{p.end??`Prior year ${p.expected_year_offset} · missing`}<p className="mt-1 font-normal text-zinc-500">{p.annual_inputs_coherent?"Aligned annual filing":"Partial or incomparable"}</p></th>)}</tr></thead><tbody>
          {Object.entries(FLOW).map(([field,label])=><tr key={field} className="border-t border-zinc-800"><th className="p-3 text-xs font-normal">{label}</th>{data.periods.map(p=><td key={p.expected_year_offset} className="p-3 align-top"><span className="whitespace-nowrap" title={p.annual_fields[field].value??"Unknown"}>{format(p.annual_fields[field].value)}</span><Detail fact={p.annual_fields[field]}/></td>)}</tr>)}
          {[['ocf_less_ppe','OCF less PP&E payments','money'],['net_margin','Net income / revenue','percent'],['ocf_margin','OCF / revenue','percent']].map(([field,label,kind])=><tr key={field} className="border-t border-zinc-800"><th className="p-3 text-xs font-normal">{label}</th>{data.periods.map(p=><td key={p.expected_year_offset} className="p-3 whitespace-nowrap" title={p.metrics[field].reason??p.metrics[field].value??"Unknown"}>{format(p.metrics[field].value,kind as "money"|"percent")}</td>)}</tr>)}
        </tbody></table></div>
        <p className="text-xs text-zinc-400">Three consecutive comparable periods: {data.three_year_summary?.status==="complete_comparable_periods"?`${data.three_year_summary.positive_cashflow_periods}/3 with positive OCF less PP&E payments; combined ${format(data.three_year_summary.ocf_less_ppe_sum)}.`:"Not established. Missing or incomparable years are not filled or treated as zero."}</p>
        <details><summary className="cursor-pointer text-sm">Balance-sheet facts · {latest.end??"period unresolved"}</summary><div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">{Object.entries(BALANCE).map(([field,label])=><div key={field} className="min-w-0 rounded-lg border border-zinc-800 p-3"><p className="text-xs text-zinc-500">{label}</p><p className="mt-1 text-sm">{format(latest.balance_fields[field].value)}</p><Detail fact={latest.balance_fields[field]}/></div>)}</div><div className="mt-3 space-y-2 text-xs text-zinc-400">{[['current_ratio','Current assets / current liabilities','ratio'],['liabilities_to_assets','Liabilities / assets','percent'],['balance_equation_difference','Assets − liabilities − equity including NCI','money']].map(([field,label,kind])=><p key={field}>{label}: {format(latest.metrics[field].value,kind as "money"|"ratio"|"percent")}{latest.metrics[field].reason&&<span className="block text-zinc-500">{latest.metrics[field].reason}</span>}</p>)}<p>A nonzero balance difference needs review. Equity excluding noncontrolling interests is not substituted into this equation. Separate borrowing components do not prove complete debt coverage.</p></div></details>
      </>}
      <details><summary className="cursor-pointer text-sm">Original sources, method & limits</summary><div className="mt-3 space-y-3 text-xs text-zinc-500">{data.sources.map(s=><p key={s.id} className="break-all"><a className="text-emerald-300 underline" href={`/api/backend/api/research/coverage/sources/${s.id}`}>Archived original SEC response</a> · <a className="text-emerald-300 underline" href={s.source_url} target="_blank" rel="noreferrer">Source URL</a><br/>Observed {s.observed_at}<br/>Source SHA-256 {s.sha256}</p>)}<ul className="list-disc space-y-1 pl-4">{data.limitations.map(l=><li key={l}>{l}</li>)}</ul><p className="break-all">{data.methodology}<br/>Context SHA-256 {data.content_sha256}</p></div></details>
    </>}
  </section>;
}
