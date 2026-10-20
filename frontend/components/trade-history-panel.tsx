"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";
import LegacyOwnershipPanel from "@/components/legacy-ownership-panel";
import PerformanceAuditPanel from "@/components/performance-audit-panel";
import MarketHistoryPanel from "@/components/market-history-panel";

type Run = { id: number; ticker: string; state: string; started_at: string; attempted: number; selected: number; imported: number; failed: number; message: string };
type History = { indexed_filings: number; index_date_range: (string | null)[]; parsed_documents: number; parsed_transaction_lines: number; remaining_xml_documents: number; sec_user_agent_configured: boolean; message: string;
  latest_index: { ticker: string; observed_at: string; added_references: number } | null;
  failed_attempts: { accession: string; message: string }[];
  index_archives: { ticker: string; older_files_total: number; older_files_archived: number; older_files_remaining: number; unsupported_legacy_references: number; unsupported_current_references?: number }[];
  runs: Run[] };
type Actor = { actor_id: string; name: string; filings: number; assets: string[]; transactions: number; transaction_codes: Record<string,number>; planned_trades: number; needs_filing_review: number; first_reported_trade: string; last_reported_trade: string; blocking_reasons: string[] };
type ActorPage = { actors: Actor[]; total: number; offset: number; limit: number };
async function read(response: Response) {
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data;
}
export default function TradeHistoryPanel({ initialTicker = "", refreshToken, onImported }: { initialTicker?: string; refreshToken?: string; onImported?: () => Promise<void> }) {
  const [ticker, setTicker] = useState(initialTicker);
  const [data, setData] = useState<History | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [actors, setActors] = useState<ActorPage | null>(null);
  const [actorSearch, setActorSearch] = useState("");
  const pendingRun = useRef<number | null>(null);
  const load = useCallback((signal?: AbortSignal) => apiFetch(`/api/trade-history?ticker=${encodeURIComponent(initialTicker)}`, { signal }).then(read)
    .then(result => {
      if (signal?.aborted) return;
      setData(result); setError("");
      const finished = result.runs.find((run: Run) => run.id === pendingRun.current && run.state !== "running");
      if (finished) { pendingRun.current = null; setActors(null); void onImported?.(); }
    }).catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "History status unavailable."); }), [initialTicker, onImported]);
  useEffect(() => { const c = new AbortController(); void load(c.signal); return () => c.abort(); }, [load, refreshToken]);
  const running = data?.runs.some(run => run.state === "running") ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [running, load]);
  async function sync(backfill = false) {
    setBusy(true); setError("");
    try {
      const result = await read(await apiFetch("/api/trade-history/sec/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker, limit: backfill ? 50 : 10, older_files_limit: backfill ? 2 : 0 }) }));
      pendingRun.current = result.run_id; await load();
    } catch (e) { setError(e instanceof Error ? e.message : "SEC import failed."); }
    finally { setBusy(false); }
  }
  async function inspectActors(offset = 0) {
    setBusy(true); setError("");
    try { setActors(await read(await apiFetch(`/api/trade-history/actors?ticker=${encodeURIComponent(initialTicker)}&search=${encodeURIComponent(actorSearch)}&offset=${offset}&limit=20`))); }
    catch (e) { setError(e instanceof Error ? e.message : "Actor coverage unavailable."); }
    finally { setBusy(false); }
  }
  const button = "rounded-lg border border-zinc-700 px-3 py-2 text-sm disabled:opacity-40";
  return <section aria-label="Primary trade history" className="rounded-xl border border-zinc-800 p-5 space-y-3">
    <h2 className="font-medium">Original SEC trade history</h2>
    <p className="text-sm text-zinc-400">{data?.indexed_filings.toLocaleString() ?? "…"} indexed Form 4 references · {data?.parsed_documents ?? 0} original documents parsed · {data?.parsed_transaction_lines ?? 0} transaction lines.</p>
    <p className="text-xs text-zinc-500">Stored filing index: {data?.index_date_range[0] ?? "Unknown"} to {data?.index_date_range[1] ?? "Unknown"}. Complete actor track record: unverified.</p>
    {data && <p className="text-xs text-zinc-500">{data.remaining_xml_documents.toLocaleString()} indexed XML documents still unparsed. Coverage below is {initialTicker || "across imported issuers"}.</p>}
    <div className="flex flex-wrap gap-3">
      <label htmlFor={`history-ticker-${initialTicker}`} className="sr-only">SEC history ticker</label>
      <input id={`history-ticker-${initialTicker}`} value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} placeholder="Ticker, e.g. AAPL" className="min-w-0 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm" />
      <button className={button} disabled={busy || running || !ticker.trim()} onClick={() => void sync()}>Refresh SEC index & import next 10</button>
      <button className={button} disabled={busy || running || !ticker.trim()} onClick={() => void sync(true)}>Backfill older index & import next 50</button>
      <button className={button} disabled={busy} onClick={() => void load()}>Check import status</button>
    </div>
    <p className="text-xs text-zinc-500">Historical backfill follows up to two SEC archive index files per run and resumes from archived files. Each import remains bounded; index coverage does not establish complete holdings or returns.</p>
    {data?.latest_index && <p className="text-xs text-emerald-300">Current SEC index for {data.latest_index.ticker} retrieved {new Date(data.latest_index.observed_at).toLocaleString()} · {data.latest_index.added_references} new current references added · original references retained.</p>}
    {data?.index_archives?.map(index => <p key={index.ticker} className="text-xs text-zinc-400">{index.ticker}: {index.older_files_archived}/{index.older_files_total} historical index files archived · {index.older_files_remaining} remaining · {index.unsupported_legacy_references} historical non-XML references in separate review. {index.unsupported_current_references??0} non-XML references retained in the original current index; not parsed transactions or copy signals.</p>)}
    {data?.failed_attempts?.length ? <details className="text-xs text-amber-300"><summary>Document validation failures</summary>{data.failed_attempts.map((failure,index) => <p key={`${failure.accession}-${index}`} className="mt-2">{failure.accession}: {failure.message}</p>)}</details> : null}
    <button className={button} disabled={busy} onClick={() => void inspectActors()}>Inspect actor coverage</button>
    {actors && <div className="space-y-3">
      <div className="flex flex-wrap gap-2"><input aria-label="Search observed owners" value={actorSearch} onChange={e => setActorSearch(e.target.value)} maxLength={100} placeholder="Name, owner CIK or ticker" className="min-w-0 rounded border border-zinc-700 bg-zinc-900 p-2 text-sm" /><button className={button} disabled={busy} onClick={() => void inspectActors()}>Search owners</button></div>
      <p className="text-xs text-zinc-500">Showing {actors.total ? actors.offset + 1 : 0}–{Math.min(actors.offset + actors.actors.length, actors.total)} of {actors.total} observed owners matching this search. Activity counts do not rank profitability. P=purchase, S=sale, A=award/other acquisition, M=exercise/conversion, F=payment/withholding; other codes require filing review.</p>
      {actors.actors.map(actor => <details key={actor.actor_id} className="rounded border border-zinc-800 p-3 text-xs"><summary>{actor.name} · {actor.filings} filings · {actor.transactions} lines · performance unverified</summary><p className="mt-2 text-zinc-400">{actor.actor_id} · {actor.assets.join(", ")} · {actor.first_reported_trade} to {actor.last_reported_trade}</p><p className="mt-2 text-zinc-400">{Object.entries(actor.transaction_codes).map(([code,count]) => `${code}: ${count}`).join(" · ")} · {actor.planned_trades} planned lines · {actor.needs_filing_review} require review</p><ul className="mt-2 list-disc pl-4 text-amber-200">{actor.blocking_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></details>)}
      {!actors.total && <p className="text-xs text-zinc-500">No original transactions match this scope and search.</p>}
      <div className="flex gap-2"><button className={button} disabled={busy || actors.offset === 0} onClick={() => void inspectActors(Math.max(0,actors.offset - actors.limit))}>Previous owners</button><button className={button} disabled={busy || actors.offset + actors.actors.length >= actors.total} onClick={() => void inspectActors(actors.offset + actors.limit)}>Next owners</button></div>
    </div>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    {data && !data.sec_user_agent_configured && <p className="text-xs text-amber-300">SEC operator contact is missing. Set EDGAR_USER_AGENT with an application name and contact email on the backend.</p>}
    {data?.runs.slice(0,3).map(run => <div key={run.id} className="border-t border-zinc-800 pt-3 text-xs text-zinc-400"><p>{run.ticker} · {run.state} · started {new Date(run.started_at).toLocaleString()} · {run.attempted}/{run.selected} attempted · {run.imported} documents imported · {run.failed} failed</p><p className="mt-1 text-amber-200">{run.message}</p></div>)}
    <p className="text-xs text-zinc-500 leading-5">{data?.message} Filing IDs, owner CIKs, transaction codes, derivatives, footnotes and amendments are retained. Grants, exercises, planned trades and unresolved amendments do not earn copy-research flags.</p>
    <LegacyOwnershipPanel ticker={initialTicker} refreshToken={data?.latest_index?.observed_at ?? refreshToken} />
    <PerformanceAuditPanel />
    <MarketHistoryPanel />
  </section>;
}
