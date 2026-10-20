"use client";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import PerformanceAuditPanel from "@/components/performance-audit-panel";
import MarketHistoryPanel from "@/components/market-history-panel";
import LedgerImportPanel from "@/components/ledger-import-panel";
import CompletedActionReviews from "@/components/completed-action-reviews";

type Reconciliation = { total: number; verified_positions: number; positions_with_confirmed_splits: number; positions_with_reviewed_distributions?: number; positions_with_completed_action_review?:number; methodology: string;
  positions: { id: number; ticker: string; entry_date: string; end_date: string; legacy_return_pct: number | null; split_scenario_return_pct: number | null;
    actions: { source: string; effective_date: string; new_shares: number; old_shares: number }[]; missing: string[]; distributions?: {source:string;effective_date:string;note:string}[];
    primary_completed_actions?:{report_id:string;reported_conversion_date:string;reported_consideration:string}[] }[] };
type Ledger = { cash: string; net_contributions: string; marked_value: string; pnl: string; event_count: number; stale_marks: number; basis: string;
  positions: { ticker: string; shares: string; mark: string; value: string; stale: boolean }[] };
const money = (value: string) => Number(value).toLocaleString(undefined, {style:'currency', currency:'USD'});
async function read(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Request failed');
  return value;
}

export default function PortfolioReconciliation() {
  const [report, setReport] = useState<Reconciliation | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState('deposit');
  const [notice, setNotice] = useState('');
  const pendingEventId = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([apiFetch('/api/portfolio/reconciliation', {signal:controller.signal}).then(read),
                 apiFetch('/api/paper-ledger', {signal:controller.signal}).then(read)])
      .then(([report, ledger]) => { if (!controller.signal.aborted) { setReport(report); setLedger(ledger); } })
      .catch(error => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, []);

  async function record(form: HTMLFormElement) {
    setBusy(true); setError(''); setNotice('');
    try {
      const fields = Object.fromEntries(new FormData(form).entries()) as Record<string,string>;
      const at = new Date(fields.occurred_at).toISOString();
      pendingEventId.current ??= crypto.randomUUID();
      const event = { ...fields, kind, event_id: pendingEventId.current, occurred_at: at,
        ticker: fields.ticker?.trim().toUpperCase(), quote_at: fields.quote_at ? new Date(fields.quote_at).toISOString() : undefined,
        entitlement_at: fields.entitlement_at ? new Date(fields.entitlement_at).toISOString() : undefined, price_basis:'unadjusted' };
      await read(await apiFetch('/api/paper-ledger/events',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(event)}));
      setLedger(await read(await apiFetch('/api/paper-ledger')));
      setNotice('Paper event recorded. No order was placed.');
      pendingEventId.current = null;
      form.reset();
    } catch (error) { setError(error instanceof Error ? error.message : 'Unable to record event'); }
    finally { setBusy(false); }
  }
  const needsTicker = ['buy','sell','mark','split','dividend'].includes(kind);
  const fields = [...(['deposit','withdrawal','fee','dividend'].includes(kind) ? ['amount'] : []),
    ...(['buy','sell'].includes(kind) ? ['shares','price','fee'] : kind==='mark' ? ['price'] : kind==='split' ? ['new_shares','old_shares'] : []),
    ...(kind==='dividend' ? ['entitled_shares','cash_per_share'] : [])];
  return <div className="space-y-6">
    <PerformanceAuditPanel />
    <MarketHistoryPanel />
    <CompletedActionReviews onSaved={()=>{void apiFetch('/api/portfolio/reconciliation').then(read).then(setReport).catch(e=>setError(e.message));}}/>
    {error && <p role="alert" className="rounded-lg border border-red-800 p-3 text-red-300">{error}</p>}
    {notice && <p role="status" className="text-emerald-300">{notice}</p>}
    <section aria-label="Historical reconciliation" className="rounded-xl border border-zinc-800 p-4 space-y-3">
      <h2 className="font-semibold">Historical reconciliation</h2>
      {!report ? <p>Loading evidence…</p> : <>
        <p className="text-sm text-zinc-400">{report.methodology}</p>
        <p>{report.total} positions reviewed · {report.positions_with_confirmed_splits} overlap reviewed split notices · {report.positions_with_reviewed_distributions??0} have reviewed distributions · {report.verified_positions} fully reconciled</p>
        {!!report.positions_with_completed_action_review&&<p className="text-sm text-amber-200">{report.positions_with_completed_action_review} original positions span an issuer-reported completed conversion. Old-symbol book marks and price ratios do not establish the resulting position value.</p>}
        <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-sm"><thead><tr className="text-left text-zinc-400"><th>Position</th><th>Recorded price return</th><th>Split-only scenario</th><th>Primary evidence</th></tr></thead>
          <tbody>{report.positions.filter(row=>row.actions.length||row.distributions?.length||row.primary_completed_actions?.length).map(row=><tr key={row.id} className="border-t border-zinc-800">
            <td className="py-3 pr-3">{row.ticker}<span className="block text-xs text-zinc-500">{row.entry_date} → {row.end_date}</span></td>
            <td>{row.legacy_return_pct==null?'Unknown':`${row.legacy_return_pct.toFixed(2)}%`}</td><td>{row.split_scenario_return_pct==null?'Unknown':`${row.split_scenario_return_pct.toFixed(2)}%`}<span className="block text-xs text-amber-300">Unverified price basis</span></td>
            <td>{row.actions.map(action=><a className="block text-sky-400 underline" key={action.source} href={action.source} target="_blank" rel="noreferrer">{action.new_shares}:{action.old_shares} · {action.effective_date}</a>)}{row.distributions?.map(d=><p key={d.source} className="text-xs text-amber-200"><a href={d.source} target="_blank" rel="noreferrer" className="text-sky-400 underline">Distribution {d.effective_date}</a> · {d.note}</p>)}{row.primary_completed_actions?.map(a=><p key={a.report_id} className="text-xs text-amber-200"><a className="text-sky-400 underline" href={`/api/backend/api/completed-actions/reports/${a.report_id}`} target="_blank" rel="noreferrer">Reported conversion {a.reported_conversion_date}</a> · {a.reported_consideration} Receipt and resulting value remain unverified.</p>)}</td>
          </tr>)}</tbody></table></div>
        <details><summary className="cursor-pointer">Remaining evidence gaps</summary><p className="text-sm text-zinc-400 mt-2">Unadjusted historical entry/exit prices, all splits and dividends, instrument identity, contributions, trading costs and fill times. Unknown actions remain unknown; the reviewed notices are not complete coverage.</p></details>
      </>}
    </section>
    <section aria-label="Independent paper ledger" className="rounded-xl border border-zinc-800 p-4 space-y-3">
      <h2 className="font-semibold">Independent paper ledger</h2>
      <p className="text-sm text-zinc-400">Start a separate paper record with explicit cash funding and shares. Legacy positions are not imported. These are manual research entries; no broker orders are sent.</p>
      <LedgerImportPanel onImported={async()=>setLedger(await read(await apiFetch('/api/paper-ledger')))} />
      {ledger && <>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{[['Cash',ledger.cash],['Net contributions',ledger.net_contributions],['Marked value',ledger.marked_value],['Paper P&L',ledger.pnl]].map(([label,value])=><div key={label} className="rounded bg-zinc-900 p-3"><div className="text-xs text-zinc-400">{label}</div><div>{money(value)}</div></div>)}</div>
        <p className="text-xs text-zinc-400">{ledger.event_count} events · {ledger.stale_marks} stale marks. {ledger.basis}</p>
        {ledger.positions.map(position=><p key={position.ticker} className="text-sm">{position.ticker}: {Number(position.shares).toLocaleString()} shares · {money(position.value)} {position.stale ? '· stale' : ''}</p>)}
      </>}
      <details><summary className="cursor-pointer">Record a manual paper event</summary>
        <form onSubmit={event=>{event.preventDefault(); void record(event.currentTarget);}} className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Event<select value={kind} onChange={event=>setKind(event.target.value)} className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2">{['deposit','withdrawal','buy','sell','mark','split','dividend','fee'].map(value=><option key={value}>{value}</option>)}</select></label>
          <label className="text-sm">Event time (local)<input name="occurred_at" type="datetime-local" required className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2" /></label>
          {needsTicker && <label className="text-sm">Ticker<input name="ticker" required maxLength={24} className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2" /></label>}
          {fields.map(field=><label key={kind+field} className="text-sm">{field.replaceAll('_',' ')}<input name={field} type="number" min="0" step="any" required defaultValue={field==='fee' ? '0' : undefined} className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2" /></label>)}
          {['buy','sell','mark'].includes(kind) && <label className="text-sm">Quote time (local)<input name="quote_at" type="datetime-local" required className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2" /></label>}
          {kind==='dividend' && <label className="text-sm">Entitlement time (local)<input name="entitlement_at" type="datetime-local" required className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2" /></label>}
          <label className="text-sm">Evidence URL or manual source reference<input name="source" required maxLength={1000} className="block w-full bg-zinc-900 border border-zinc-700 rounded p-2" /></label>
          <p className="text-xs text-amber-300 sm:col-span-2">Prices must be unadjusted as traded. Events are permanent, chronological paper records. Correct economic entries through offsetting events; never rewrite history.</p>
          <button disabled={busy} className="rounded bg-emerald-800 px-4 py-2 disabled:opacity-50">{busy ? 'Recording…' : 'Record paper event'}</button>
        </form>
      </details>
    </section>
  </div>;
}
