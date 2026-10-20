"use client";
import { useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
type Preview={preview_token:string;new_events:number;identical_existing_events:number;source_status:string;
  result:{cash:string;net_contributions:string;marked_value:string;pnl:string;event_count:number}};
async function read(r:Response){const v=await r.json();if(!r.ok)throw new Error(typeof v.detail==='string'?v.detail:'Import failed');return v;}
export default function LedgerImportPanel({onImported}:{onImported:()=>Promise<void>}){
  const [text,setText]=useState('');const [preview,setPreview]=useState<Preview|null>(null);const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
  async function review(){setBusy(true);setError('');setNotice('');setPreview(null);try{const events=JSON.parse(text);setPreview(await read(await apiFetch('/api/paper-ledger/import/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({events})})));}catch(e){setError(e instanceof Error?e.message:'Invalid import');}finally{setBusy(false);}}
  async function commit(){if(!preview)return;setBusy(true);setError('');try{const result=await read(await apiFetch('/api/paper-ledger/import/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({events:JSON.parse(text),preview_token:preview.preview_token})}));await onImported();setNotice(`${result.created} source-referenced paper events recorded. No order was placed.`);setPreview(null);setText('');}catch(e){setError(e instanceof Error?e.message:'Import failed');}finally{setBusy(false);}}
  return <details className="text-sm"><summary className="cursor-pointer">Review and import an ordered ledger history</summary><div className="mt-3 space-y-3">
    <p className="text-xs text-zinc-400">Paste a JSON array of normalized cash, fill, fee, split and dividend events in chronological order. Every event needs a unique event_id, timezone-aware occurred_at, source reference and currency: USD. Buys/sells/marks need unadjusted prices and quote timestamps. This records a paper reconstruction; statement references do not independently verify executions.</p>
    <textarea aria-label="Ordered ledger events JSON" value={text} onChange={e=>{setText(e.target.value);setPreview(null);setNotice('');}} maxLength={800000} rows={8} placeholder="[]" className="w-full rounded border border-zinc-700 bg-zinc-900 p-3 font-mono text-xs"/>
    <button disabled={busy||!text.trim()} onClick={()=>void review()} className="rounded border border-zinc-700 px-3 py-2 text-xs disabled:opacity-40">Review import without recording</button>
    {preview&&<div className="rounded border border-zinc-700 p-3 text-xs space-y-2"><p>{preview.new_events} new events · {preview.identical_existing_events} identical events already recorded · {preview.result.event_count} resulting events.</p><p>Cash ${preview.result.cash} · net contributions ${preview.result.net_contributions} · marked value ${preview.result.marked_value} · paper P&L ${preview.result.pnl}</p><p className="text-amber-200">{preview.source_status}</p><button disabled={busy||!preview.new_events} onClick={()=>void commit()} className="rounded border border-zinc-700 px-3 py-2 disabled:opacity-40">Record reviewed paper history</button></div>}
    {error&&<p role="alert" className="text-red-300">{error}</p>}{notice&&<p role="status" className="text-emerald-300">{notice}</p>}
  </div></details>;
}
