"use client";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

type Audit = { audit_id: number | null; generated_at?: string; content_sha256?: string; input_sha256?: string; methodology_note?: string;
  metrics: { metric: string; status: string; evidence: string; blockers: string[] }[];
  arithmetic?: { positions_checked: number; snapshots_checked: number; issue_count: number; issue_counts: Record<string,number>; tolerance: string;
    examples: { kind: string; ticker: string | null; position_id: number; snapshot_id?: number; date?: string; detail: string }[] };
  snapshot_coverage?: { recorded_days: number; days_with_missing_positions: number; days_with_outside_positions: number; basis: string };
  legacy_review?: { documents: number; context_rows: number; documents_with_mapping_gaps: number; transaction_codes: Record<string,number> };
  purchase_evidence?: { actor: string; asset: string; accession: string; traded_at_display: string; available_at: string; source_url: string; source_verification: string }[] };
async function read(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : `Request failed (${response.status})`);
  return value;
}
export default function PerformanceAuditPanel() {
  const [data, setData] = useState<Audit | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    apiFetch("/api/performance-audit", { signal: c.signal }).then(read).then(result => { if (!c.signal.aborted) setData(result); })
      .catch(e => { if (!c.signal.aborted) setError(e instanceof Error ? e.message : "Audit unavailable"); });
    return () => c.abort();
  }, []);
  async function audit() {
    setBusy(true); setError("");
    try { setData(await read(await apiFetch("/api/performance-audit", { method: "POST" }))); }
    catch (e) { setError(e instanceof Error ? e.message : "Audit failed"); }
    finally { setBusy(false); }
  }
  return <section aria-label="Performance evidence audit" className="min-w-0 rounded-xl border border-zinc-800 p-5 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-medium">Performance evidence audit</h2><button disabled={busy} onClick={() => void audit()} className="rounded border border-zinc-700 px-3 py-2 text-xs disabled:opacity-40">{busy ? "Auditing…" : "Audit current evidence"}</button></div>
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    {data?.audit_id ? <>
      <p className="text-xs text-zinc-500">Edition {data.audit_id} · captured {new Date(data.generated_at!).toLocaleString()}. Each audit archives its exact inputs, fingerprint and results.</p>
      <div className="grid gap-3 lg:grid-cols-3">{data.metrics.map(metric => <div key={metric.metric} className="rounded border border-zinc-800 p-3 text-xs space-y-2"><h3 className="font-medium text-sm">{metric.metric} · <span className="text-amber-200">{metric.status}</span></h3><p className="text-zinc-400">{metric.evidence}</p><ul className="list-disc pl-4 text-zinc-400">{metric.blockers.map(note => <li key={note}>{note}</li>)}</ul></div>)}</div>
      {data.arithmetic && <div className="text-xs space-y-2"><p>{data.arithmetic.positions_checked} original positions · {data.arithmetic.snapshots_checked.toLocaleString()} snapshot prices checked · {data.arithmetic.issue_count.toLocaleString()} arithmetic/date issues.</p><p className="text-zinc-500">{data.arithmetic.tolerance}</p>
        {data.arithmetic.issue_count > 0 && <details><summary>Recorded data discrepancies</summary><div className="mt-2 text-amber-200">{Object.entries(data.arithmetic.issue_counts).map(([kind,count]) => <p key={kind}>{kind.replaceAll("_"," ")}: {count.toLocaleString()}</p>)}</div><div className="mt-3 max-h-80 overflow-auto space-y-2 text-zinc-400">{data.arithmetic.examples.map((issue,index) => <p key={index}>{issue.ticker ?? "Unknown instrument"} · position {issue.position_id}{issue.snapshot_id ? ` / snapshot ${issue.snapshot_id}` : ""} · {issue.date ?? ""} · {issue.detail}</p>)}</div></details>}
      </div>}
      {data.snapshot_coverage && <p className="text-xs text-zinc-400">{data.snapshot_coverage.recorded_days} dates with snapshots · {data.snapshot_coverage.days_with_missing_positions} dates have incomplete position coverage · {data.snapshot_coverage.days_with_outside_positions} dates include positions outside their holding period. {data.snapshot_coverage.basis}</p>}
      {data.legacy_review && <p className="text-xs text-zinc-400">Legacy review: {data.legacy_review.documents} original submissions · {data.legacy_review.context_rows} reported rows · {data.legacy_review.documents_with_mapping_gaps} filings retain unresolved fields. Codes: {Object.entries(data.legacy_review.transaction_codes).map(([code,count]) => `${code}=${count}`).join(" · ")}. Option exercises and compensation grants do not establish trading skill.</p>}
      {!!data.purchase_evidence?.length && <details className="text-xs text-zinc-400"><summary>Reported purchase evidence and copy availability</summary>{data.purchase_evidence.map(row => <p key={row.accession} className="mt-2"><a href={row.source_url} target="_blank" rel="noreferrer" className="text-sky-400 underline">{row.asset} · {row.actor}</a> · reported {row.traded_at_display} · first available to this system {new Date(row.available_at).toLocaleString()} · {row.source_verification.replaceAll("_"," ")}</p>)}</details>}
      <p className="text-xs text-amber-200">{data.methodology_note}</p>
      <a className="inline-block text-xs text-sky-400 underline" href={`/api/backend/api/performance-audit/${data.audit_id}/inputs`} download={`performance-audit-${data.audit_id}-inputs.json`}>Download this audit’s source records</a>
      <details className="text-xs text-zinc-500"><summary>Audit fingerprints</summary><p className="break-all mt-2">Inputs: {data.input_sha256}</p><p className="break-all">Results: {data.content_sha256}</p></details>
    </> : <p className="text-sm text-zinc-400">Run the evidence audit to inspect recorded arithmetic and the remaining requirements for verified returns.</p>}
  </section>;
}
