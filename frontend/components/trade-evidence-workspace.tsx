"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {DetailDrawer,Disclosure,MetricStrip,ResearchNote,WorkspaceHeading,WorkspaceNav} from "./workspace-ui";
import { ArrowUpRight, Bookmark, Database, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/apiFetch";
import TradeHistoryPanel from "@/components/trade-history-panel";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Kind = "all" | "congress" | "insiders" | "crypto";
type Trade = {
  id: string; kind: Kind; actor: string; actor_id: string; asset: string; side: string;
  token_address: string | null; instrument: string; amount_label: string;
  traded_at: string; disclosed_at: string; first_seen_at: string; available_at: string;
  traded_at_display: string; disclosed_at_display: string;
  date_status?: string; disclosure_lag_days: number; public_age_days: number; source_url: string;
  source_label: string; notes: string[]; reasons: string[]; priority: string; watched: boolean;
  fundamentals: { quant_score: number | null; fcf_yield: number | null; red_flags_high: number | null; analyzed_at: string } | null;
};
type Provider = {
  id: Kind; name: string; configured: boolean; required_env: string; docs: string; coverage: string;
  sync: { state: string; succeeded_at: string | null; message: string } | null;
};
type Dashboard = { as_of: string; demo: boolean; events: Trade[]; total: number; total_saved: number; offset: number; limit: number; providers: Provider[]; methodology: string;
  primary_history?: { documents: number; transaction_lines: number; contact_configured: boolean; complete_history: boolean; latest_run: { state: string; finished_at: string | null; message: string } | null } | null };
const labels: Record<string, string> = {
  research: "Research candidate", needs_evidence: "Needs evidence", delayed: "Delayed signal",
  context_only: "Context only", risk_flag: "Financial risk",
};
const date = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";

async function readResponse(response: Response) {
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === "string" ? error.detail : `Request failed (${response.status})`);
  }
  return response.json();
}

export default function TradeEvidenceWorkspace({ embedded = false, ticker, onAnalyze }: { embedded?: boolean; ticker?: string; onAnalyze?: (ticker: string) => void }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [kind, setKind] = useState<Kind>("all");
  const [search, setSearch] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [watchOnly, setWatchOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [raw, setRaw] = useState<string | null>(null);

  useEffect(() => { const timer = setTimeout(() => setSearchQuery(search), 250); return () => clearTimeout(timer); }, [search]);
  const load = useCallback((signal?: AbortSignal) => {setLoading(true);return apiFetch(`${API}/api/trade-evidence?limit=100&offset=${offset}&kind=${kind}&watched=${watchOnly}&search=${encodeURIComponent(searchQuery)}${ticker ? `&ticker=${encodeURIComponent(ticker)}` : ""}`, { signal })
    .then(readResponse)
    .then(result => { if (!signal?.aborted) { setData(result); setError(""); } })
    .catch(error => { if (!signal?.aborted) setError(error instanceof Error ? error.message : "Unable to load trade evidence."); })
    .finally(() => { if (!signal?.aborted) setLoading(false); });}, [ticker, offset, kind, watchOnly, searchQuery]);
  useEffect(() => { const c = new AbortController(); void Promise.resolve().then(()=>{if(!c.signal.aborted)return load(c.signal);}); return () => c.abort(); }, [load]);

  async function sync(provider: Provider) {
    setBusy(provider.id); setNotice(""); setError("");
    try {
      const result = await readResponse(await apiFetch(`${API}/api/trade-evidence/sync/${provider.id}`, { method: "POST" }));
      await load(); setNotice(result.message);
    } catch (err) {
      await load();
      setError(err instanceof Error ? err.message : "Sync failed.");
    } finally { setBusy(null); }
  }
  async function toggleWatch(trade: Trade) {
    setBusy(trade.id);
    try {
      await readResponse(await apiFetch(`${API}/api/trade-evidence/${trade.id}/watch`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ watched: !trade.watched }),
      }));
      await load();
      setError("");
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to update watchlist."); }
    finally { setBusy(null); }
  }
  async function viewRaw(trade: Trade) {
    setBusy("evidence");
    try {
      const result = await readResponse(await apiFetch(`${API}/api/trade-evidence/${trade.id}/evidence`));
      setRaw(JSON.stringify(result, null, 2));
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to load source record."); }
    finally { setBusy(null); }
  }

  const events = data?.events ?? [];
  const shown = events;
  const detail = events.find(trade => trade.id === selected);
  const actors = new Map<string, { name: string; count: number; lag: number; lagCount: number }>();
  shown.forEach(trade => {
    const key = `${trade.kind}:${trade.actor_id}`;
    const actor = actors.get(key) ?? { name: trade.actor, count: 0, lag: 0, lagCount: 0 };
    actor.count += 1;
    if (trade.date_status !== "unconfirmed_award") { actor.lag += trade.disclosure_lag_days; actor.lagCount += 1; }
    actors.set(key, actor);
  });
  const button = "inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-zinc-400 disabled:opacity-40 disabled:cursor-not-allowed";

  return <section aria-label={ticker ? `Public trades for ${ticker}` : "Public trade intelligence"} className="bg-zinc-950 text-zinc-100">
    {!embedded&&<WorkspaceNav/>}
    <div className="workspace-page">
      {data?.demo && <div role="status" className="rounded-lg border border-amber-700 bg-amber-950/40 p-4 text-sm text-amber-200"><strong>DEMO · Synthetic test data.</strong> These invented actors, assets and financial figures demonstrate the workflow. No live feeds or real trades are connected. Watchlist changes affect only this temporary preview.</div>}
      <WorkspaceHeading eyebrow="Public disclosures" title={ticker?`${ticker} · Public trades`:"Public trades"} description="See who bought or sold, and when the disclosure became public."><button className={button} onClick={()=>void load()} disabled={loading||!!busy}><RefreshCw size={14}/>Refresh</button></WorkspaceHeading>
      <ResearchNote><p>Reported activity does not establish trader profitability. SEC filings are separate from paid government and crypto feeds.</p></ResearchNote>
      {error && <div role="alert" className="rounded-lg border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">{error}</div>}
      {notice && <div role="status" className="rounded-lg border border-emerald-900 p-4 text-sm text-emerald-300">{notice}</div>}
      <MetricStrip items={[{label:"Saved disclosures",value:data?.total_saved??"—"},{label:"For review in this view",value:data?shown.filter(t=>t.priority==='research').length:"—"}]}/>
      {loading&&data&&<p role="status" className="text-xs text-zinc-500">Updating disclosures…</p>}<section className="workspace-table">
        <div className="p-4 flex flex-wrap gap-3 justify-between border-b border-zinc-800">
          <div className="flex flex-wrap gap-1" aria-label="Trade category">{([ ["all", "All"], ["congress", "Congress"], ["insiders", "Insiders"], ["crypto", "Crypto"] ] as const).filter(([key]) => !ticker || key !== "crypto").map(([key, label]) => <button key={key} aria-pressed={kind === key} onClick={() => { setKind(key); setOffset(0); }} className={`px-3 py-2 rounded-lg text-xs ${kind === key ? "bg-emerald-400 text-zinc-950 font-semibold" : "text-zinc-400 hover:bg-zinc-800"}`}>{label}</button>)}</div>
          <div className="flex flex-wrap items-center gap-3"><input aria-label="Search people, wallets or assets" placeholder="Search person, wallet or asset" value={search} maxLength={100} onChange={e => { setSearch(e.target.value); setOffset(0); }} className="bg-zinc-950 border border-zinc-700 rounded-lg p-2 text-xs w-44 min-w-0 flex-1" /><label className="flex items-center gap-2 text-xs text-zinc-400"><input type="checkbox" checked={watchOnly} onChange={e => { setWatchOnly(e.target.checked); setOffset(0); }} /> Watched</label></div>
        </div>
        {loading && !data ? <p className="p-12 text-center text-zinc-400" role="status">Loading saved evidence…</p> : !data ? <div className="p-12 text-center text-zinc-400">Unable to load the backend. Use Reload saved data to retry.</div> : shown.length === 0 ? <div className="p-12 text-center"><Database className="mx-auto text-zinc-600 mb-4" /><h2 className="font-medium">{data.total_saved ? "No trades match these filters" : "Your evidence ledger starts here"}</h2><p className="text-sm text-zinc-500 max-w-md mx-auto mt-2">{data.total_saved ? "Try another category or clear your search." : "Connect a provider and sync its recent sample. No example trades or invented returns are mixed into your data."}</p></div> : <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead className="text-xs text-zinc-500 bg-zinc-900/30"><tr>{["Actor / asset", "Activity", "Disclosure delay", "Fundamentals", "Assessment", "Review"].map(h => <th key={h} className="p-4 font-medium">{h}</th>)}</tr></thead><tbody>{shown.map(trade => <tr key={trade.id} className="border-t border-zinc-800 hover:bg-zinc-900/40">
          <td className="p-4 max-w-60"><p className="font-medium truncate" title={trade.actor}>{trade.actor}</p><p className="text-xs text-zinc-500 mt-1 break-all">{trade.asset}</p></td>
          <td className="p-4"><p className="capitalize">{trade.side}</p><p className="text-xs text-zinc-500 mt-1">{trade.amount_label}</p></td>
          <td className="p-4 tabular-nums">{trade.date_status === "unconfirmed_award" ? "Date unconfirmed" : `${trade.disclosure_lag_days}d`}<p className="text-xs text-zinc-500 mt-1">Public {trade.public_age_days}d ago</p></td>
          <td className="p-4">{trade.fundamentals?.quant_score != null ? `${trade.fundamentals.quant_score}/99` : "Unknown"}<p className="text-xs text-zinc-500 mt-1">{trade.fundamentals?.fcf_yield != null ? `${(trade.fundamentals.fcf_yield * 100).toFixed(1)}% FCF yield` : "FCF not assessed"}</p></td>
          <td className="p-4"><span className={`text-xs ${trade.priority === "research" ? "text-emerald-300" : trade.priority === "risk_flag" ? "text-red-300" : "text-amber-300"}`}>{labels[trade.priority]}</span><p className="text-xs text-zinc-600 mt-1">Trader edge unverified</p></td>
          <td className="p-4"><button className={button} onClick={() => { setSelected(trade.id); setRaw(null); }}>Details</button></td>
        </tr>)}</tbody></table></div>}
        <div className="p-3 border-t border-zinc-800 space-y-2"><p className="text-xs text-zinc-500">Showing {data?.total ? (data.offset + 1) : 0}–{Math.min((data?.offset ?? 0) + shown.length, data?.total ?? 0)} of {data?.total ?? 0} matching saved records. Search covers all saved records; imported histories remain incomplete.</p><div className="flex gap-2"><button className={button} disabled={!data || offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous trades</button><button className={button} disabled={!data || data.offset + events.length >= data.total} onClick={() => setOffset(offset + 100)}>Next trades</button></div></div>
      </section>
      {detail && <DetailDrawer title={`${detail.actor} · ${detail.asset}`} onClose={()=>setSelected(null)}><section aria-label="Selected trade evidence" className="rounded-xl border border-emerald-900 bg-zinc-900/30 p-5 space-y-4">
        <div className="flex flex-wrap justify-between gap-3"><div><p className="text-xs text-emerald-400 mb-2">EVIDENCE CARD</p><h2 className="text-xl font-medium">{detail.actor} · {detail.asset}</h2><p className="text-xs text-zinc-500 mt-1 break-all">{detail.instrument} · {detail.actor_id}</p></div><button className={button} disabled={!!busy} onClick={() => void toggleWatch(detail)}><Bookmark size={15} fill={detail.watched ? "currentColor" : "none"} />{detail.watched ? "Remove from watchlist" : "Watch this trade"}</button></div>
        <dl className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">{[["Traded (provider date)", detail.traded_at_display ?? detail.traded_at], ["Reported public (provider date)", detail.disclosed_at_display ?? detail.disclosed_at], ["First seen by this app", date(detail.first_seen_at)], ["Earliest app availability", date(detail.available_at)]].map(([label, value]) => <div key={label}><dt className="text-zinc-500 mb-1">{label}</dt><dd>{value}</dd></div>)}</dl>
        {detail.token_address && <p className="text-xs text-zinc-400 break-all">Received token contract: {detail.token_address}</p>}
        <ul className="text-sm text-zinc-300 list-disc pl-5 space-y-2">{detail.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
        {detail.fundamentals && <p className="text-xs text-zinc-500">Company analysis saved {date(detail.fundamentals.analyzed_at)}. Current context only; not a snapshot from when the trade happened.</p>}
        <details className="text-xs text-zinc-400"><summary className="cursor-pointer">Source limitations and interpretation</summary><ul className="list-disc pl-5 mt-3 space-y-2">{detail.notes.map(note => <li key={note}>{note}</li>)}</ul></details>
        <div className="flex flex-wrap gap-3"><a href={detail.source_url} target="_blank" rel="noreferrer" className={button}>{detail.source_label}<ArrowUpRight size={14} /></a><button disabled={!!busy} className={button} onClick={() => void viewRaw(detail)}>Inspect saved provider record</button>{detail.kind !== "crypto" && onAnalyze ? <button className={button} onClick={() => onAnalyze(detail.asset)}>Analyze {detail.asset}</button> : <Link href="/" className={button}>Open stock analyzer</Link>}</div>
        {raw && <pre className="max-h-80 overflow-auto bg-zinc-950 p-4 rounded-lg text-xs text-zinc-400">{raw}</pre>}
      </section></DetailDrawer>}
      <Disclosure title="People in this sample & screening rules">      <section className="grid md:grid-cols-2 gap-5">
        <div className="border border-zinc-800 rounded-xl p-5"><h2 className="font-medium mb-2">Actors in this sample</h2><p className="text-xs text-zinc-500 mb-4">Sorted by observed activity, not profitability. A full track record is still needed.</p>{Array.from(actors.entries()).sort((a, b) => b[1].count-a[1].count).slice(0, 8).map(([key, actor]) => <div key={key} className="flex justify-between gap-3 py-2 text-sm border-t border-zinc-800"><span className="truncate">{actor.name}</span><span className="text-zinc-500 text-xs shrink-0">{actor.count} rows · {actor.lagCount ? `${(actor.lag/actor.lagCount).toFixed(1)}d avg delay` : "Dates unconfirmed"}</span></div>)}{!actors.size && <p className="text-sm text-zinc-600">No actors to assess yet.</p>}</div>
        <div className="border border-zinc-800 rounded-xl p-5"><h2 className="font-medium mb-3">What earns a research flag?</h2><p className="text-sm text-zinc-400 leading-6">A reported purchase, public for no more than 7 days, with no more than 14 days of disclosure delay. The saved company analysis must be within 7 days, have positive free-cash-flow yield, quant score ≥53 and zero recorded high-severity flags.</p><p className="text-xs text-zinc-500 leading-5 mt-3">These are explicit initial triage rules, not calibrated forecasts. Options, private transactions and trading plans need filing review. Crypto swaps stay unassessed until token-risk and execution checks exist. Watching a trade saves a research bookmark; it does not place an order.</p></div>
      </section>
</Disclosure>
      <Disclosure title="Sources & connections">      {data?.primary_history && <section className="rounded-xl border border-emerald-900 p-5 text-sm"><h2 className="font-medium">SEC · Original corporate filings</h2><p className="mt-2 text-zinc-400">{data.primary_history.documents.toLocaleString()} original documents · {data.primary_history.transaction_lines.toLocaleString()} transaction lines · contact {data.primary_history.contact_configured ? 'configured' : 'missing'}</p><p className="mt-2 text-xs text-zinc-500">Latest import: {data.primary_history.latest_run?.state ?? 'not tested'} · complete actor performance remains unverified. This free source is separate from Quiver’s provider feed.</p></section>}
      <section aria-label="Data connections" className="grid gap-3 md:grid-cols-3">
        {data?.providers.map(provider => <div key={provider.id} className="rounded-xl border border-zinc-800 p-4 bg-zinc-900/30 flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-medium">{provider.name}</h2><Database size={15} className="text-zinc-500" /></div>
          <p className={`text-xs ${provider.sync?.state === "error" ? "text-red-300" : "text-amber-300"}`}>{!provider.configured ? "Not connected" : provider.sync?.state === "error" ? "Last sync failed" : provider.sync?.state === "partial" ? "Partial import" : provider.sync?.succeeded_at ? "Saved sample available" : "Configured · not tested"}</p>
          <p className="text-xs text-zinc-400 leading-5 flex-1">{provider.coverage}</p>
          <p className="text-xs text-zinc-500">Last successful sync: {date(provider.sync?.succeeded_at ?? null)}</p>
          {provider.sync && <p className="text-xs text-zinc-400">{provider.sync.message}</p>}
          {!provider.configured && <details className="text-xs text-zinc-400"><summary className="cursor-pointer">Connection setup</summary><p className="mt-2">Configure {provider.required_env} on the backend with an entitled API subscription. Keys stay on the server.</p></details>}
          <div className="flex items-center justify-between"><a href={provider.docs} target="_blank" rel="noreferrer" className="text-xs text-emerald-400 inline-flex items-center gap-1">Provider guide <ArrowUpRight size={12} /></a>
            <button disabled={!provider.configured || !!busy} className={button} onClick={() => void sync(provider)}>{busy === provider.id ? "Syncing…" : "Sync sample"}</button></div>
        </div>)}
      </section>
</Disclosure>
      <Disclosure title="Historical filings & source checks"><TradeHistoryPanel initialTicker={ticker} refreshToken={data?.as_of} onImported={load} /></Disclosure>
      <Disclosure title="About these disclosures"><p className="text-xs text-zinc-600 leading-5">{data?.methodology} Original filing evidence and documented, fresh company inputs are required for research flags. Provider rows alone remain unverified.</p></Disclosure>
    </div>
  </section>;
}
