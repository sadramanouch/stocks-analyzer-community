"use client";
import {useCallback,useEffect,useState} from 'react';
import {apiFetch} from '@/lib/apiFetch';
type Bar={date:string;close:number};
type Capture={id:string;report_sha256:string;report:{ticker:string;provider_symbol:string;effective_date:string;captured_at:string;start_date:string;end_date:string;history:{bars:Bar[]};sources:{id:string;source_kind:string;source_url:string;sha256:string;observed_at:string}[]}};
type Review={supported_links:{ticker:string;provider_symbol:string;effective_date:string;notice_url:string}[];captures:Capture[];notice:string};
export default function SymbolHistoryLinks({onSaved}:{onSaved:()=>void}){
  const [data,setData]=useState<Review|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState('');
  const load=useCallback(async(signal?:AbortSignal)=>{
    try{const r=await apiFetch('/api/market-history/lineage',{signal});if(!r.ok)throw new Error(`Symbol continuity review unavailable (${r.status})`);const d=await r.json();if(!signal?.aborted){setData(d);setError('');}}
    catch(e){if(!signal?.aborted){setData(null);setError(e instanceof Error?e.message:'Symbol continuity review unavailable');}}
  },[]);
  useEffect(()=>{const controller=new AbortController();void Promise.resolve().then(()=>load(controller.signal));return()=>controller.abort();},[load]);
  async function capture(ticker:string){
    setBusy(ticker);setError('');
    try{const r=await apiFetch(`/api/market-history/lineage/${ticker}/capture`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)throw new Error(`Source capture needs review (${r.status})`);await load();onSaved();}
    catch(e){setError(e instanceof Error?e.message:'Source capture unavailable');}finally{setBusy('');}
  }
  return <div className="space-y-3 border-t border-zinc-800 pt-4" aria-label="Historical symbol continuity">
    <h3 className="text-sm font-medium">Documented symbol changes</h3>
    <p className="text-xs text-zinc-400">Linked histories retain the provider’s actual symbol. Each link requires the issuer’s unchanged-CUSIP notice, matching SEC issuer index and original Yahoo response. The newer policy also checks both former and current registered share classes. These support inspection of old records.</p>
    {error&&<p role="alert" className="text-xs text-red-300">{error}</p>}
    {data?.supported_links.map(link=>{const saved=data.captures.find(c=>c.report.ticker===link.ticker);return <div key={link.ticker} className="min-w-0 rounded-lg border border-zinc-800 p-3 text-xs">
      <p className="font-medium">{link.ticker} → {link.provider_symbol} · effective {link.effective_date}</p>
      {saved?<><p className="mt-2 text-zinc-400">{saved.report.history.bars.length} daily bars · {saved.report.start_date} through {saved.report.end_date} · archived {new Date(saved.report.captured_at).toLocaleString()}</p>
        <a className="mt-2 inline-block text-emerald-300 underline" href={`/api/backend/api/market-history/lineage/${saved.id}`} target="_blank" rel="noreferrer">Open replayed symbol-link report</a>
        <details className="mt-2"><summary>Original sources and fingerprints</summary><p className="mt-2 break-all text-zinc-500">Report SHA-256 {saved.report_sha256}</p>{saved.report.sources.map(source=><div className="mt-3 min-w-0" key={source.id}><a className="text-emerald-300 underline" href={`/api/backend/api/market-history/sources/${source.id}`} target="_blank" rel="noreferrer">Archived {source.source_kind.replaceAll('_',' ')}</a><p className="break-all text-zinc-500">SHA-256 {source.sha256}</p><a className="text-zinc-400 underline" href={source.source_url} target="_blank" rel="noreferrer">Publisher source</a></div>)}</details></>
      :<button disabled={!!busy} onClick={()=>void capture(link.ticker)} className="mt-2 rounded border border-zinc-700 px-3 py-2 disabled:opacity-50">{busy===link.ticker?'Checking original sources…':`Check and archive ${link.ticker} continuity`}</button>}
    </div>;})}
    <p className="text-xs leading-5 text-zinc-500">{data?.notice}</p>
  </div>;
}
