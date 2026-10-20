"use client";
import {useCallback,useEffect,useState} from "react";
import {apiFetch} from "@/lib/apiFetch";
import QuoteChecks from "./quote-checks";

type Reference={price:string|null;as_of:string|null;currency:string|null;source:string|null;age_seconds:string|null;paper_quote_requirements_met:boolean};
type Status={ticker:string;research_version:number;input_sha256:string;observed_at:string;state:string;valid_until:string|null;reference:Reference;
 comparison:{id:string;report_sha256:string;captured_at:string;state_at_capture:string}|null;reasons:string[];displayed_reference_corroborated_now:boolean;
 displayed_reference_comparisons:{provider:string;source_price:string;source_as_of:string;absolute_difference:string;price_tolerance:string;time_difference_seconds:string;within_price_tolerance:boolean;within_time_window:boolean;same_current_exchange:boolean}[];
 capture_window:{available_now:boolean;next_open:string|null};limits:string};
type Props={ticker:string;version:number;inputSha256:string;price:number|null;asOf:string|null;currency:string|null;source:string|null};
type Loaded={key:string;data?:Status;error?:string;requestElapsed:number};
const labels:Record<string,string>={displayed_reference_agrees:"Displayed quote agrees within the comparison policy",comparison_expired:"Retained comparison is stale",missing_comparison:"No retained price comparison",source_disagreement:"Source prices disagree",sources_not_comparable:"Source observations are not comparable",displayed_reference_not_corroborated:"Displayed quote is not corroborated",displayed_reference_expired:"Displayed quote is stale for corroboration",identity_needs_review:"Instrument identity needs review",earlier_research_edition:"Earlier saved research edition",invalid_displayed_reference:"Displayed quote needs review",evidence_integrity_failed:"Retained evidence failed integrity checks",future_comparison:"Comparison clock needs review"};
const date=(value:string|null)=>value?new Date(value).toLocaleString():"Unknown";

export default function PriceReadiness({ticker,version,inputSha256,price,asOf,currency,source}:Props){
 const key=JSON.stringify([ticker,version,inputSha256,price,asOf,currency,source]);
 const [loaded,setLoaded]=useState<Loaded|null>(null),[expiredKey,setExpiredKey]=useState<string|null>(null),[detailsOpen,setDetailsOpen]=useState(false);
 const load=useCallback(async(signal?:AbortSignal)=>{const start=performance.now();try{
  const response=await apiFetch(`/api/price-readiness/${encodeURIComponent(ticker)}?version=${version}&input_sha256=${encodeURIComponent(inputSha256)}`,{signal});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"Price evidence unavailable.");
  if(!signal?.aborted){setLoaded({key,data,requestElapsed:performance.now()-start});setExpiredKey(null);}
 }catch(e){if(!signal?.aborted)setLoaded({key,error:e instanceof Error?e.message:"Price evidence unavailable.",requestElapsed:performance.now()-start});}},[ticker,version,inputSha256,key]);
 useEffect(()=>{const c=new AbortController();void Promise.resolve().then(()=>load(c.signal));return()=>c.abort();},[load]);
 const current=loaded?.key===key?loaded:null,d=current?.data;
 useEffect(()=>{
  if(!d?.displayed_reference_corroborated_now||!d.valid_until)return;
  const remaining=Date.parse(d.valid_until)-Date.parse(d.observed_at)-(current?.requestElapsed??0)-1000;
  const timer=setTimeout(()=>{setExpiredKey(key);},Math.max(0,remaining));return()=>clearTimeout(timer);
 },[d,current?.requestElapsed,key]);
 const matches=d&&d.ticker===ticker&&d.research_version===version&&d.input_sha256===inputSha256&&d.reference.as_of===asOf&&d.reference.currency===currency&&d.reference.source===source&&
  (price===null?d.reference.price===null:d.reference.price!==null&&Number(d.reference.price)===price);
 const expired=expiredKey===key;
 return <section aria-label={`Displayed quote evidence for ${ticker}`} className="min-w-0 space-y-3 rounded-lg border border-amber-900 p-4">
  <div className="flex flex-wrap justify-between gap-3"><h3 className="font-medium">Price evidence · displayed reference</h3><button className="rounded-lg border border-zinc-700 px-3 py-2 text-xs" onClick={()=>void load()}>Reload price status</button></div>
  {!current&&<p role="status" className="text-sm text-zinc-400">Checking the displayed input against retained source evidence…</p>}
  {current?.error&&<p role="alert" className="text-sm text-amber-200">{current.error}</p>}
  {d&&<>
   <p className="text-sm text-amber-200">{!matches?"Displayed input changed · reload company evidence":expired?"Price corroboration expired · reload status":labels[d.state]??"Price evidence needs review"}</p>
   <p className="text-xs leading-5 text-zinc-400">Displayed quote: {currency??"Unknown currency"} {price??"Unknown"} · as of {date(asOf)} · source {source??"Unknown"}. Status evaluated {date(d.observed_at)}. {matches&&d.reference.paper_quote_requirements_met?"The dated USD reference passes the legacy 24-hour quote check at that evaluation time. Compared previews separately require a fresh two-source agreement within 20 minutes.":"The saved reference has not passed the legacy quote check in this view. Compared paper previews check their own recent source receipt."}</p>
   {d.reasons.map(reason=><p key={reason} className="text-xs text-amber-200">{reason}</p>)}
   {d.comparison&&<p className="text-xs text-zinc-400">Last comparison captured {date(d.comparison.captured_at)} · result at capture: {d.comparison.state_at_capture.replaceAll('_',' ')}. <a className="text-emerald-300 underline" href={`/api/backend/api/quote-checks/reports/${d.comparison.id}`}>Replayed report</a></p>}
   {!!d.displayed_reference_comparisons.length&&<div aria-label="Displayed quote compared with each source" className="grid min-w-0 gap-3 sm:grid-cols-2">{d.displayed_reference_comparisons.map(c=><div key={c.provider} className="min-w-0 space-y-1 rounded border border-zinc-800 p-3 text-xs"><p className="font-medium uppercase">{c.provider} · USD {c.source_price}</p><p className="text-zinc-400">Source quote {date(c.source_as_of)}</p><p>Difference USD {c.absolute_difference} · tolerance USD {c.price_tolerance} · {c.within_price_tolerance?'within price tolerance':'outside price tolerance'}</p><p className={c.within_time_window?'text-zinc-400':'text-amber-200'}>Quote timestamps {c.time_difference_seconds} seconds apart · limit 60 seconds · {c.within_time_window?'within time window':'outside time window'}</p>{!c.same_current_exchange&&<p className="text-amber-200">Current listing exchange does not match.</p>}</div>)}</div>}
   {matches&&!d.displayed_reference_corroborated_now&&<p className="text-xs text-zinc-400">Reloading status checks this saved quote; it does not change the quote. <a href="/research" className="text-emerald-300 underline">Refresh company inputs in Source research</a> to obtain a newer saved reference.</p>}
   {!d.capture_window.available_now&&<p className="text-xs text-zinc-400">New regular-session comparison is outside its useful capture window. {d.capture_window.next_open?`Next supported opening: ${date(d.capture_window.next_open)}.`:"The retained calendar does not provide a next opening."} Existing scheduled trial checks remain separate.</p>}
   <p className="text-xs leading-5 text-zinc-500">Independent upstream accuracy and executable prices remain unverified. Agreement between separately retrieved websites can share market-data feeds. {d.limits}</p>
   <a className="block break-all text-xs text-emerald-300 underline" href={`/api/backend/api/price-readiness/${encodeURIComponent(ticker)}?version=${version}&input_sha256=${encodeURIComponent(inputSha256)}`}>Inspect this input-bound price status</a>
   <details onToggle={e=>setDetailsOpen(e.currentTarget.open)} className="text-xs"><summary className="cursor-pointer">Compare sources and inspect original quotes</summary>{detailsOpen&&<div className="mt-3"><QuoteChecks ticker={ticker} captureAllowed={d.capture_window.available_now&&!!matches} onCaptured={()=>void load()}/></div>}</details>
  </>}
 </section>;
}
