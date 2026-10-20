"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

type Fact = { label: string; concept: string; status: string; value: string | null; reason: string;
  row_label?: string; rendered_number?: string; scale?: number; reported_rounding_unit_usd?: string | null; fact_id?: string };
type Measure = { label: string; status: string; value: string | null; reason: string; operating_fact: string; spending_fact: string };
type Report = { ticker: string; research_version: number; captured_at: string; status: string;
  filing: { url: string; form: string; filingDate: string; accessionNumber: string };
  sources: { id: string; source_url: string; observed_at: string; sha256: string }[];
  periods: { end: string; facts: Record<string, Fact>; cash_measures: Record<string, Measure>; operating_component_reconciliation: string }[];
  coverage: { matched_original_facts: number; source_mismatches: number; available_cash_measures: number };
  issues: string[]; limitations: string[] };
type Saved = { id: string; report_sha256: string; report: Report; historical_report?: boolean; requested_research_version?: number; current_scope_sha256?: string };
type Pending = { status: string; ticker: string; research_version: number; scope_sha256?: string; reason?: string; limitations: string[] };
type Inspection = Saved | Pending;
const isSaved = (value: Inspection): value is Saved => "report" in value;
const usd = (value: string | null) => value === null ? "Unknown" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value));
const label = (status: string) => status === "matched" ? "Matched to both originals" : status === "filed_statement_only" ? "Filed statement only" : status === "source_mismatch" ? "Sources disagree" : status === "requires_review" ? "Requires review" : "Unavailable";

async function read(response: Response): Promise<Inspection> {
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Statement check unavailable.");
  return value as Inspection;
}

export default function StatementChecks({ ticker, version }: { ticker: string; version: number | null }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Inspection | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open || !version) return;
    const controller = new AbortController();
    void apiFetch(`/api/statement-checks/${encodeURIComponent(ticker)}?version=${version}`, { signal: controller.signal })
      .then(read).then(value => { if (!controller.signal.aborted) { setData(value); setError(""); } })
      .catch(() => { if (!controller.signal.aborted) { setData(null); setError("The original statement check could not be loaded. Treat its values as unverified until this is resolved."); } });
    return () => controller.abort();
  }, [open, ticker, version]);

  async function capture() {
    if (!data) return;
    const scope = isSaved(data) ? data.current_scope_sha256 : data.scope_sha256;
    const targetVersion = isSaved(data) ? data.requested_research_version : data.research_version;
    if (!scope || !targetVersion) return;
    setBusy(true); setError("");
    try {
      const response = await apiFetch(`/api/statement-checks/${encodeURIComponent(ticker)}/capture`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ research_version: targetVersion, scope_sha256: scope, request_id: crypto.randomUUID().replaceAll("-", "") }) });
      setData(await read(response));
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Original statement collection stopped."); }
    finally { setBusy(false); }
  }

  const saved = data && isSaved(data) ? data : null;
  const pending = data && !isSaved(data) ? data : null;
  const report = saved?.report;
  if (!version) return null;
  return <details aria-label={`Filed statement checks for ${ticker}`} onToggle={event => { if (event.target !== event.currentTarget) return; const next = event.currentTarget.open; if (next) { setData(null); setError(""); } setOpen(next); }} className="rounded-lg border border-zinc-700 p-4">
    <summary className="cursor-pointer text-sm font-medium">Original filed statement cross-check · {ticker}</summary>
    {open && <div className="mt-4 space-y-4 text-sm">
      <p className="text-xs text-zinc-400">Compare the reported cash-flow table with the archived SEC company-facts response. Each spending category keeps its own scope. These checks do not replace legacy capex, qualify an investment or establish returns.</p>
      {error && <p role="alert" className="text-amber-300">{error}</p>}
      {!data && !error && <p className="text-zinc-500">Replaying original statement evidence…</p>}
      {pending && <div className="space-y-3"><p>{pending.reason ?? "No original statement cross-check has been captured for this research edition."}</p>{pending.scope_sha256 && <button disabled={busy} onClick={() => void capture()} className="rounded-lg border border-zinc-700 px-3 py-2 text-sm disabled:opacity-40">{busy ? "Checking original SEC filing…" : "Check and archive original SEC statement"}</button>}</div>}
      {report && <>
        {saved?.historical_report && <div className="space-y-2 rounded border border-amber-900 p-3 text-xs"><p className="text-amber-300">Archived evidence from edition {report.research_version}. The selected edition {saved.requested_research_version} has not received this statement cross-check. These figures keep their original source dates.</p><button disabled={busy} onClick={() => void capture()} className="rounded border border-zinc-700 px-3 py-2 disabled:opacity-40">{busy ? "Checking selected edition…" : "Check selected edition against the original statement"}</button></div>}
        <p className="text-xs text-zinc-400">{report.coverage.matched_original_facts} facts matched to both originals · {report.coverage.source_mismatches} differing source values · edition {report.research_version}<br />{report.filing.form}, filed {report.filing.filingDate}. Captured {report.captured_at}.</p>
        {report.issues.map(issue => <p key={issue} className="text-amber-300">{issue}</p>)}
        {report.periods.map((period, index) => <details key={period.end} open={index === 0} className="rounded border border-zinc-800 p-3">
          <summary className="cursor-pointer">Annual period ending {period.end}</summary>
          <div className="mt-3 space-y-4">
            {period.operating_component_reconciliation === "source_mismatch" && <p className="text-amber-300">Total operating cash flow does not reconcile with the reported continuing and discontinued components. Subtractions are withheld.</p>}
            <div className="grid gap-3 md:grid-cols-2">{Object.entries(period.facts).filter(([key, fact]) => key === "ppe_payments" || fact.status !== "unavailable").map(([key, fact]) => <div key={key} className="min-w-0 rounded border border-zinc-800 p-3">
              <p className="font-medium">{fact.label}</p><p className="mt-1">{usd(fact.value)}</p>
              <p className={`mt-1 text-xs ${fact.status === "matched" ? "text-emerald-300" : "text-amber-300"}`}>{label(fact.status)}</p>
              {fact.row_label && <p className="mt-2 text-xs text-zinc-400">Issuer row: {fact.row_label}</p>}
              <details className="mt-2 text-xs"><summary className="cursor-pointer text-zinc-400">Source amount and definition</summary><div className="mt-2 space-y-1 break-words text-zinc-500"><p>{fact.reason}</p><p className="break-all">us-gaap:{fact.concept}</p>{fact.rendered_number !== undefined && <p>Filed display: {fact.rendered_number} × 10^{fact.scale} USD. Reported rounding unit: {fact.reported_rounding_unit_usd === null ? "exact" : usd(fact.reported_rounding_unit_usd ?? null)}.</p>}{fact.fact_id && <a className="text-emerald-300 underline" href={`${report.filing.url}#${encodeURIComponent(fact.fact_id)}`} target="_blank" rel="noreferrer">View original filing fact</a>}</div></details>
            </div>)}</div>
            <div className="space-y-2 border-t border-zinc-800 pt-3">{Object.entries(period.cash_measures).filter(([key, measure]) => key === "cash_after_ppe_payments" || measure.value !== null).map(([key, measure]) => <div key={key} className="text-xs"><p className="font-medium">{measure.label}: {measure.value === null ? "Unavailable" : usd(measure.value)}</p>{measure.value !== null ? <p className="mt-1 text-zinc-400">{usd(period.facts[measure.operating_fact].value)} − {usd(period.facts[measure.spending_fact].value)} = {usd(measure.value)}. One spending category only; other obligations remain outside this measure.</p> : <p className="mt-1 text-zinc-500">{measure.reason}</p>}</div>)}</div>
          </div>
        </details>)}
        <details><summary className="cursor-pointer text-xs">Archived originals and verification limits</summary><div className="mt-3 space-y-3 text-xs text-zinc-400">
          {report.sources.map(source => <p key={source.id} className="break-all"><a className="text-emerald-300 underline" href={`/api/backend/api/statement-checks/sources/${source.id}`}>Download original {source.source_url.includes("/submissions/") ? "submissions index" : "annual filing"}</a><br />Observed {source.observed_at}<br />SHA-256: {source.sha256}</p>)}
          <a className="block text-emerald-300 underline" href={`/api/backend/api/financial-context/${encodeURIComponent(ticker)}?version=${report.research_version}`}>Original company-facts references and financial context</a>
          {report.limitations.map(limit => <p key={limit}>{limit}</p>)}
          <p className="break-all">Report SHA-256: {saved?.report_sha256}</p><a className="text-emerald-300 underline" href={`/api/backend/api/statement-checks/reports/${saved?.id}`}>Open replayed statement report</a>
        </div></details>
      </>}
    </div>}
  </details>;
}
