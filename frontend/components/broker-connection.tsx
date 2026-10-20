"use client";
import {useCallback,useEffect,useState} from 'react';
import {apiFetch} from '@/lib/apiFetch';

type Job={id:string;created_at:string;finished_at:string|null;state:string;message:string;receipt_id:string|null};
type Status={configured:boolean;jobs:Job[];guide:string;limitations:string};
type Receipt={id:string;observed_at:string;input_sha256:string;result_sha256:string;result:{account_label:string;arithmetic_reconciled:boolean;cash_difference:string|null;share_differences:Record<string,string>|null;issues:string[];row_reviews:{section:string;row:number;reason:string}[];mapped_events:unknown[]};limitations:string};
const button='rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-emerald-500 disabled:opacity-40';
const input='min-w-0 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm';
async function read(r:Response){const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:`Request failed (${r.status})`);return d;}

export default function BrokerConnection(){
  const [data,setData]=useState<Status|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const [opening,setOpening]=useState('');const [closing,setClosing]=useState('');const [label,setLabel]=useState('');const [zone,setZone]=useState('');
  const [receipt,setReceipt]=useState<Receipt|null>(null);const [chosen,setChosen]=useState('');const [receiptRevision,setReceiptRevision]=useState(0);
  const load=useCallback(async(signal?:AbortSignal)=>{try{const d=await read(await apiFetch('/api/broker-connection',{signal}));if(!signal?.aborted)setData(d);}catch(e){if(!signal?.aborted)setError(e instanceof Error?e.message:'Broker connection unavailable');}},[]);
  useEffect(()=>{const c=new AbortController();void Promise.resolve().then(()=>load(c.signal));return()=>c.abort();},[load]);
  const running=data?.jobs.some(j=>j.state==='queued'||j.state==='running');
  useEffect(()=>{if(!running)return;const timer=setInterval(()=>void load(),4000);return()=>clearInterval(timer);},[running,load]);
  useEffect(()=>{if(!chosen)return;const c=new AbortController();apiFetch(`/api/broker-connection/receipts/${chosen}`,{signal:c.signal}).then(read).then(d=>{if(!c.signal.aborted)setReceipt(d);}).catch(e=>{if(!c.signal.aborted)setError(e instanceof Error?e.message:'Source receipt unavailable');});return()=>c.abort();},[chosen,receiptRevision]);
  async function start(){setBusy(true);setError('');try{await read(await apiFetch('/api/broker-connection/downloads',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({opening_date:opening,closing_date:closing,account_label:label.trim(),export_timezone:zone})}));await load();}catch(e){setError(e instanceof Error?e.message:'Broker download could not start');}finally{setBusy(false);}}
  return <section id="broker-connection" aria-label="Read-only broker connection" className="space-y-4 rounded-xl border border-zinc-800 p-5">
    <h2 className="text-lg">IBKR connection · reporting only</h2>
    <p className="text-sm leading-6 text-zinc-400">Download IBKR statements to check cash, holdings and trades. Your virtual account stays separate.</p>
    <p role="status" className="text-sm text-amber-200">{data?data.configured?'Reporting credentials configured · access still needs a successful download.':'Not connected · backend reporting token and query ID are missing or invalid.':'Checking reporting configuration…'}</p>
    <details className="space-y-2 text-xs leading-6 text-zinc-400"><summary className="cursor-pointer">Set up read-only reporting access</summary>
      <ol className="list-decimal space-y-2 pl-5"><li>In your own IBKR Client Portal, create one Activity Flex query in XML format using the required native fields below. Use YYYYMMDD dates, YYYYMMDD;HHMMSS timestamps and the actual export timezone. Do not use a trade-confirmation query or consolidated accounts.</li><li>Enable Flex Web Service and create a reporting token. In the existing backend Fly app, securely configure secrets named <code>IBKR_FLEX_TOKEN</code> and <code>IBKR_FLEX_QUERY_ID</code>. Keep token values out of chat, source code and uploaded files. Your broker account and its permissions must already exist.</li><li>Reload configuration, then choose a completed opening day and closing day within the previous 365 days. Activity starts the next calendar day. Each run makes at most eight reporting requests, spaces them seven seconds apart and stops after its bounded window; only the documented generation-in-progress response is polled. A two-minute cooldown follows a completed or failed run.</li></ol>
      <p>The supported native profile is single-account USD stock records with unit multipliers. Transfers, unsupported actions/assets, mixed currencies or incomplete sections require review. Download success alone does not establish complete records, valid mappings, a best executable price or profitability.</p>
      <a className="text-emerald-300 underline" href={data?.guide??'https://www.interactivebrokers.com/docs/web-api/api-reference/send-request'} target="_blank" rel="noreferrer">Official IBKR reporting instructions</a>
    </details>
    <div className="flex flex-wrap items-end gap-3">
      <label className="grid gap-1 text-xs">Opening statement day<input className={input} type="date" value={opening} onChange={e=>setOpening(e.target.value)}/></label>
      <label className="grid gap-1 text-xs">Closing statement day<input className={input} type="date" value={closing} onChange={e=>setClosing(e.target.value)}/></label>
      <label className="grid gap-1 text-xs">Account nickname<input className={input} value={label} maxLength={80} onChange={e=>setLabel(e.target.value)} autoComplete="off" placeholder="Nickname, not account number"/></label>
      <label className="grid gap-1 text-xs">Actual export timezone<select className={input} value={zone} onChange={e=>setZone(e.target.value)}><option value="">Choose export timezone</option><option value="UTC">UTC</option><option value="America/New_York">America/New_York</option><option value="America/Toronto">America/Toronto</option><option value="America/Vancouver">America/Vancouver</option></select></label>
      <button className={button} disabled={busy||running||!data?.configured||!opening||!closing||!label.trim()||!zone} onClick={()=>void start()}>Fetch and archive broker records</button>
      <button className={button} disabled={busy||running} onClick={()=>{setError('');void load();}}>Reload reporting configuration</button>
    </div>
    {error&&<p role="alert" className="text-sm text-red-300">{error}</p>}
    {data?.jobs.map(j=><div key={j.id} className="space-y-2 rounded border border-zinc-800 p-3 text-xs"><p>{new Date(j.created_at).toLocaleString()} · {j.state}</p><p>{j.message}</p>{j.receipt_id&&<button className={button} onClick={()=>{setReceipt(null);setError('');setChosen(j.receipt_id!);setReceiptRevision(v=>v+1);}}>Inspect downloaded source receipt</button>}</div>)}
    {receipt&&<div className="space-y-2 rounded border border-zinc-700 p-4 text-sm"><h3>Downloaded record review · {receipt.result.account_label}</h3><p>Observed {new Date(receipt.observed_at).toLocaleString()} · {receipt.result.mapped_events.length} mapped economic records · cash/share arithmetic {receipt.result.arithmetic_reconciled?'reconciles':'requires review'}.</p><p>Cash difference: {receipt.result.cash_difference??'Missing coverage'} · share differences: {receipt.result.share_differences?Object.keys(receipt.result.share_differences).length:'Missing coverage'}</p>{receipt.result.issues.map((v,i)=><p key={i} className="text-xs text-amber-200">{v}</p>)}{receipt.result.row_reviews.map((v,i)=><p key={i} className="text-xs text-amber-200">{v.section} row {v.row}: {v.reason}</p>)}<a className="text-xs text-emerald-300 underline" href={`/api/backend/api/broker-connection/receipts/${receipt.id}`} target="_blank" rel="noreferrer">Original XML bytes, transport receipt and replayed review</a><p className="break-all text-xs text-zinc-500">Input SHA-256: {receipt.input_sha256}<br/>Result SHA-256: {receipt.result_sha256}</p><p className="text-xs text-zinc-400">{receipt.limitations}</p></div>}
    <p className="text-xs text-zinc-500">Reporting service access and source reconciliation are separate from market-data entitlements and investment performance. No real broker records are assumed when this panel is empty.</p>
  </section>;
}
