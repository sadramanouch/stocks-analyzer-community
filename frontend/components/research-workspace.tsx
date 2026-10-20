"use client";

import {DetailDrawer,Disclosure,MetricStrip,ResearchNote,WorkspaceHeading,WorkspaceNav} from "./workspace-ui";
import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { apiFetch } from "@/lib/apiFetch";
const SourceCampaigns=dynamic(()=>import("@/components/source-campaigns"));
const FinancialContext=dynamic(()=>import("@/components/financial-context"));
const StatementChecks=dynamic(()=>import("@/components/statement-checks"));
const TradeHistoryPanel=dynamic(()=>import("@/components/trade-history-panel"));
import SecurityIdentity, { type SecurityIdentityEvidence } from "@/components/security-identity";

type Item = {
  ticker: string; company_name: string; status: string; model_version: string; rebuilt_at: string;
  currently_fresh: boolean; input_observed_at: string | null; financial_period_end: string | null;
  financial_source: string | null; quote_as_of: string | null; quote_source: string | null;
  provider_period_label: string | null;
  quote_currency: string | null; financial_currency: string | null;
  price: number | null; market_cap: number | null; fcf: number | null; fcf_yield: number | null;
  financials: Record<string, number | null>; issues: string[]; limitations: string;
  identity: { title: string; exchange: string; security_type: string; source_url: string; retrieved_at: string; revision_id: number; source_file_time: string; class_evidence?: SecurityIdentityEvidence } | null;
  financial_provenance: Record<string, { concept?: string; end: string; filed?: string; accn?: string; form?: string; source_url?: string; method?: string; statement_basis?: string; reported_precision_usd?: number }>;
  input_sha256: string; legacy_analyzed_at: string | null; legacy_fcf_yield: number | null;
};
type Edition = { id: number; created_at: string; model_version: string; item_count: number; basis: string; content_hash: string };
type Data = { version: number | null; versions: Edition[]; counts: Record<string, number>; total: number; items: Item[];
  identity_batch: { retrieved_at: string; id: string } | null;
  refresh_job: { id: number; state: string; completed: number; failed: number; current_ticker: string | null; tickers_json: string; errors_json: string } | null;
  methodology: string };
const fieldLabels:Record<string,string>={capex:"Capital spending",net_income:"Net income",operating_cf:"Operating cash flow",revenue:"Revenue"};
const labels: Record<string, string> = { documented_inputs: "Annual figures sourced", needs_evidence: "Needs source data", excluded: "Outside this model" };
const date = (v: string | null) => v ? new Date(v.includes("T") && !/(Z|[+-]\d\d:\d\d)$/.test(v) ? `${v}Z` : v).toLocaleString() : "Unknown";
const value = (v: number | null, currency = false) => v === null ? "Unknown" : v.toLocaleString(undefined, { maximumFractionDigits: 2, ...(currency ? { style: "currency", currency: "USD" } : {}) });
const amount = (v: number | null, currency: string | null) => v === null ? "Unknown" : `${value(v, currency === "USD")}${currency === "USD" ? "" : ` ${currency ?? "(currency unknown)"}`}`;
const button = "rounded-lg border border-zinc-700 px-3 py-2 text-sm hover:border-zinc-400 disabled:opacity-40";
async function read(r: Response) {
  const data = await r.json();
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${r.status})`);
  return data;
}

export default function ResearchWorkspace() {
  const [data, setData] = useState<Data | null>(null);
  const [version, setVersion] = useState("");
  const [status, setStatus] = useState("documented_inputs");
  const [search, setSearch] = useState("");
  const [searchQuery,setSearchQuery]=useState("");
  useEffect(()=>{const timer=setTimeout(()=>setSearchQuery(search),250);return()=>clearTimeout(timer);},[search]);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<string | null>(null);
  const [tickers, setTickers] = useState("AAPL, MSFT, HRMY, SIG");
  const [error, setError] = useState("");
  const [loading,setLoading]=useState(true);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const query = new URLSearchParams({ status, search:searchQuery, offset: String(offset), limit: "50", ...(version ? { version } : {}) });
  const endpoint = `/api/research?${query}`;
  const load = useCallback((signal?: AbortSignal) => {setLoading(true);return apiFetch(endpoint, { signal }).then(read)
    .then(result => { if (!signal?.aborted) { setData(result); setError(""); } })
    .catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Unable to load research."); }).finally(()=>{if(!signal?.aborted)setLoading(false);});}, [endpoint]);
  useEffect(() => { const c = new AbortController(); void Promise.resolve().then(()=>{if(!c.signal.aborted)return load(c.signal);}); return () => c.abort(); }, [load]);

  async function action(path: string, body?: object) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await read(await apiFetch(`/api/research/${path}`, { method: "POST", ...(body ? { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } } : {}) }));
      setNotice(result.job_id ? `Refresh ${result.job_id} started for ${result.count} tickers. Check progress to see the result.` : result.version_id ? `Saved immutable edition ${result.version_id}; ${result.items} records audited.` : result.message);
      setVersion(""); setOffset(0); if (!version && offset === 0) await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Action failed."); }
    finally { setBusy(false); }
  }
  async function viewHistory(ticker: string) {
    setHistory(null); setBusy(true);
    try { setHistory(JSON.stringify(await read(await apiFetch(`/api/research/history/${encodeURIComponent(ticker)}`)), null, 2)); }
    catch (e) { setError(e instanceof Error ? e.message : "History unavailable."); }
    finally { setBusy(false); }
  }
  const item = data?.items.find(i => i.ticker === selected);
  const edition = data?.versions.find(e => e.id === data.version);
  const total = Object.values(data?.counts ?? {}).reduce((a, b) => a + b, 0);
  const job = data?.refresh_job;

  return <main className="min-h-screen bg-zinc-950 text-zinc-100">
    <WorkspaceNav/>
    <div className="workspace-page">
      <WorkspaceHeading eyebrow="Company research" title="Financials" description="Compare saved company figures. Select a company to see its financials and sources."/>
      {error && <p role="alert" className="rounded-lg border border-red-800 p-3 text-sm text-red-300">{error}</p>}
      {notice && <p role="status" className="rounded-lg border border-emerald-800 p-3 text-sm text-emerald-300">{notice}</p>}
      <MetricStrip items={[{label:"Annual figures sourced",value:data?.counts.documented_inputs??"—",note:"Specific annual inputs, not every field"},{label:"All saved companies",value:data?total:"—",note:"Use filters to explore the full list"}]}/><ResearchNote><p>Source checks cover specific annual inputs. Prices, other ratios and forecasts have separate limits. Saved dates and all supporting evidence remain available below.</p></ResearchNote>
      <div className="workspace-toolbar">{loading&&<span role="status" className="text-xs text-zinc-500">Updating results…</span>}<label htmlFor="research-status" className="sr-only">Validation status</label><select id="research-status" className="rounded border border-zinc-700 bg-zinc-900 p-2 text-sm" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); setSelected(null); }}><option value="all">All saved companies</option>{Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select><label htmlFor="research-search" className="sr-only">Search ticker</label><input id="research-search" placeholder="Search ticker" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); setSelected(null); }} className="rounded border border-zinc-700 bg-zinc-900 p-2 text-sm" /></div>

      <div className="workspace-table"><table className="w-full text-left text-sm"><thead className="bg-zinc-900 text-xs text-zinc-400"><tr>{["Company", "Validation", "Saved price", "Annual free cash flow", "Cash flow / market value"].map(c => <th className="p-3" key={c}>{c}</th>)}</tr></thead><tbody>{data?.items.map(i => <tr key={i.ticker} className="border-t border-zinc-800"><td className="p-3"><button className="text-emerald-300 underline" onClick={() => { setSelected(i.ticker); setHistory(null); }}>{i.ticker}</button><p className="mt-1 max-w-72 text-xs text-zinc-500">{i.company_name}</p></td><td className="p-3 text-xs">{labels[i.status]}{i.status === "documented_inputs" && !i.currently_fresh && <p className="text-amber-300">Now stale; refresh required</p>}</td><td className="p-3 whitespace-nowrap">{amount(i.price, i.quote_currency)}<p className="text-xs text-zinc-500">{date(i.quote_as_of)}</p></td><td className="p-3 whitespace-nowrap">{amount(i.fcf, i.financial_currency)}<p className="text-xs text-zinc-500">{i.financial_period_end ?? "Unknown"}</p></td><td className="p-3 whitespace-nowrap">{i.fcf_yield === null ? "Unknown" : `${(i.fcf_yield * 100).toFixed(2)}%`}<p className="text-xs text-zinc-500">{i.status === "documented_inputs" ? "Annual inputs sourced" : "Needs checking"}</p></td></tr>)}</tbody></table>{data && !data.items.length && <p className="p-8 text-center text-sm text-zinc-500">No records meet this filter. Missing evidence is not a zero score.</p>}</div>
      <div className="flex justify-between gap-3 text-sm"><button className={button} disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 50)); setSelected(null); }}>Previous</button><p className="self-center text-zinc-500">{data?.total ? `${offset + 1}–${Math.min(offset + 50, data.total)} of ${data.total}` : "0 records"}</p><button className={button} disabled={!data || offset + 50 >= data.total} onClick={() => { setOffset(offset + 50); setSelected(null); }}>Next</button></div>
      {item&&<DetailDrawer title={`${item.ticker} · Financials`} onClose={()=>setSelected(null)}>
        <p className="text-sm text-zinc-400">{item.company_name}</p><a href={`/?ticker=${encodeURIComponent(item.ticker)}`} className="text-sm text-emerald-300 underline">Open company overview</a>
        <MetricStrip items={[{label:"Saved price",value:amount(item.price,item.quote_currency)},{label:"Annual free cash flow",value:amount(item.fcf,item.financial_currency)},{label:"Cash flow / market value",value:item.fcf_yield===null?"Unknown":`${(item.fcf_yield*100).toFixed(2)}%`},{label:"Financial year end",value:item.financial_period_end??"Unknown"}]}/>
        <p className="text-xs text-zinc-400">{labels[item.status]} · quote {date(item.quote_as_of)}{!item.currently_fresh&&' · refresh needed'}</p>
        {!!item.issues.length&&<Disclosure title={`${item.issues.length} data notes`}><ul className="list-disc pl-5 text-sm text-amber-200">{item.issues.map(issue=><li key={issue}>{issue}</li>)}</ul></Disclosure>}
        <div className="grid grid-cols-2 gap-3">{Object.entries(item.financials).map(([field,v])=><div key={field} className="rounded-lg border border-zinc-800 p-3"><p className="text-xs text-zinc-500">{fieldLabels[field]??field.replaceAll('_',' ')}</p><p className="mt-1 text-sm">{value(v,item.financial_currency==='USD')}</p></div>)}</div>
        <Disclosure title="Sources, dates & calculation checks"><section className="rounded-xl border border-emerald-900 p-5 space-y-4"><h2 className="text-xl font-medium">{item.ticker} · saved evidence</h2>{item.identity ? <p className="text-sm text-zinc-400">{item.identity.title} · {item.identity.security_type} · exchange {item.identity.exchange} · identity revision {item.identity.revision_id}<br /><a className="text-emerald-300 underline" href={item.identity.source_url} target="_blank" rel="noreferrer">Official source directory</a> · retrieved {date(item.identity.retrieved_at)}<br />{item.identity.source_file_time}</p> : <p className="text-amber-300 text-sm">No authoritative instrument match. Historical symbols require additional evidence.</p>}{item.identity?.class_evidence && <SecurityIdentity key={item.identity.class_evidence.report_id} evidence={item.identity.class_evidence} />}<ul className="list-disc pl-5 text-sm text-amber-200 space-y-1">{item.issues.map(i => <li key={i}>{i}</li>)}</ul><p className="text-xs text-zinc-400">Financial source: {item.financial_source ?? "Unknown"} · period {item.financial_period_end ?? "Unknown"} {item.provider_period_label && item.provider_period_label !== item.financial_period_end && `(provider label ${item.provider_period_label})`} · input observed {date(item.input_observed_at)}<br />Quote source: {item.quote_source ?? "Unknown"} · as of {date(item.quote_as_of)}</p><div className="grid gap-3 sm:grid-cols-2">{Object.entries(item.financials).map(([field, amount]) => <div key={field} className="rounded border border-zinc-800 p-3 text-sm"><p className="text-zinc-500">{field.replaceAll("_", " ")}</p><p>{value(amount, item.financial_currency === "USD")}</p>{item.financial_provenance[field] && <p className="mt-1 text-xs text-zinc-500">{item.financial_provenance[field].source_url ? <><a className="text-emerald-300 underline" href={item.financial_provenance[field].source_url} target="_blank" rel="noreferrer">Primary annual statement</a><br />{item.financial_provenance[field].statement_basis}<br />Reported precision: ${item.financial_provenance[field].reported_precision_usd?.toLocaleString()}</> : <>{item.financial_provenance[field].concept} · {item.financial_provenance[field].form} · filed {item.financial_provenance[field].filed}<br />SEC accession {item.financial_provenance[field].accn}</>}</p>}</div>)}</div><p className="text-xs text-zinc-500">Legacy FCF yield: {item.legacy_fcf_yield === null ? "Unknown" : `${(item.legacy_fcf_yield * 100).toFixed(2)}%`} · original saved {date(item.legacy_analyzed_at)}. The original analysis is preserved.</p><p className="text-xs text-zinc-500 break-all">Input SHA-256: {item.input_sha256}</p><p className="text-xs text-zinc-400">{item.limitations}</p><button className={button} disabled={busy} onClick={() => void viewHistory(item.ticker)}>Inspect saved editions & change history</button>{history && <pre className="max-h-96 overflow-auto rounded bg-zinc-900 p-3 text-xs">{history}</pre>}{data?.version&&<FinancialContext ticker={item.ticker} version={data.version}/>}{data?.version&&<StatementChecks key={`filed:${item.ticker}:${data.version}`} ticker={item.ticker} version={data.version}/>}<TradeHistoryPanel key={item.ticker} initialTicker={item.ticker} /></section></Disclosure>
      </DetailDrawer>}
      <Disclosure title="Trade history & source coverage"><TradeHistoryPanel/></Disclosure>
      <Disclosure title="Manage data & saved editions"><div className="workspace-toolbar"><label htmlFor="edition" className="text-xs text-zinc-400">Edition</label><select id="edition" value={version} onChange={e => { setVersion(e.target.value); setOffset(0); setSelected(null); }} className="max-w-full rounded border border-zinc-700 bg-zinc-900 p-2 text-sm"><option value="">Latest edition</option>{data?.versions.map(e => <option key={e.id} value={e.id}>#{e.id} · {date(e.created_at)} · {e.item_count} records</option>)}</select></div>      <section className="rounded-xl border border-zinc-800 p-5 space-y-4">
        <div className="flex flex-wrap gap-3"><button className={button} disabled={busy} onClick={() => void action("identities/sync")}>Refresh official identities & audit list</button><button className={button} disabled={busy} onClick={() => void action("rebuild")}>Audit saved inputs into new edition</button><button className={button} disabled={busy} onClick={() => void load()}>Check progress / reload</button></div>
        <p className="text-xs text-zinc-500">Identity snapshot retrieved {date(data?.identity_batch?.retrieved_at ?? null)}. An exact common-share classification is required; issuer names and Yahoo EQUITY labels alone do not establish instrument identity.</p>
        <div className="flex flex-wrap items-center gap-3"><label className="text-sm text-zinc-400" htmlFor="refresh-tickers">Refresh saved tickers</label><input id="refresh-tickers" className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm" value={tickers} onChange={e => setTickers(e.target.value)} /><button className={button} disabled={busy || job?.state === "running"} onClick={() => void action("refresh", { tickers: tickers.split(",").map(t => t.trim()).filter(Boolean), limit: 25 })}>Fetch source inputs</button></div>
        <p className="text-xs text-zinc-500">Up to 100 saved tickers per refresh; leave the input empty for the next 25 oldest observations. Each batch finishes with an audit of the entire saved list. Failed sources retain their original dates.</p>
        {job && <div className="text-sm text-zinc-300"><p>Latest refresh: {job.state} · {job.completed}/{JSON.parse(job.tickers_json).length} attempted · {job.failed} source failures {job.current_ticker && `· ${job.current_ticker}`}</p>{job.state === "running" && <button className={`${button} mt-2`} onClick={() => void action("refresh/cancel")}>Cancel after current request</button>}{["interrupted", "cancelled", "error"].includes(job.state) && <button className={`${button} mt-2 ml-2`} disabled={busy || job.completed >= JSON.parse(job.tickers_json).length} onClick={() => void action(`refresh/resume/${job.id}`)}>Resume unattempted tickers</button>}{job.failed > 0 && <details className="mt-2 text-xs text-amber-300"><summary>Source failures</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap">{JSON.stringify(JSON.parse(job.errors_json), null, 2)}</pre></details>}</div>}
      </section>
<SourceCampaigns/></Disclosure>
      <Disclosure title="Saved edition & methodology">{edition && <p className="text-xs text-zinc-500">#{edition.id} · {edition.model_version} · {edition.basis} · {date(edition.created_at)}<br /><span className="break-all">Edition SHA-256: {edition.content_hash}</span></p>}</Disclosure>
      <Disclosure title="About this saved list"><p className="text-xs text-zinc-500 leading-5">{data?.methodology}</p></Disclosure>
    </div>
  </main>;
}
