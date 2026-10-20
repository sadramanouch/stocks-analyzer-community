"use client";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

type Reference = { accession: string; ticker: string; filed_date: string; status: string; transactions?: number; rows_with_issues?: number; owners?: { name: string }[] };
type Review = { total: number; archived: number; context_reviewed: number; needs_manual_mapping: number; unreviewed: number; offset: number; limit: number; references: Reference[];
  runs: { id: number; state: string; selected: number; reviewed: number; failed: number; message: string }[] };
type Document = { accession: string; source_url: string; sha256: string; observed_at: string; document_text: string;
  review: { limitations: string[]; amendment: boolean; explanation_text: string; transactions: { table_number: number; row_number: number; security: string; reported_date?: string; transaction_code: string;
    reported_shares: string | null; reported_price: string | null; reported_notional: string | null; issues: string[]; cells: string[]; source_cells: string[]; numbered_annotations: string[] }[];
    unresolved: { reason: string }[]; tables: unknown[] } };
async function read(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : `Request failed (${response.status})`);
  return value;
}

export default function LegacyOwnershipPanel({ ticker = "", refreshToken }: { ticker?: string; refreshToken?: string }) {
  const [data, setData] = useState<Review | null>(null);
  const [offset, setOffset] = useState(0);
  const [document, setDocument] = useState<Document | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback((signal?: AbortSignal) =>
    apiFetch(`/api/trade-history/legacy?ticker=${encodeURIComponent(ticker)}&offset=${offset}&limit=10`, { signal }).then(read)
      .then(result => { if (!signal?.aborted) { setData(result); setError(""); } })
      .catch(e => { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Review unavailable"); }), [ticker, offset]);
  useEffect(() => { const c = new AbortController(); void load(c.signal); return () => c.abort(); }, [load, refreshToken]);
  const running = data?.runs.some(run => run.state === "running") ?? false;
  useEffect(() => {
    if (!running) return;
    const c = new AbortController();
    const timer = setInterval(() => void load(c.signal), 2000);
    return () => { clearInterval(timer); c.abort(); };
  }, [running, load]);
  async function reviewNext() {
    setBusy(true); setError("");
    try {
      await read(await apiFetch("/api/trade-history/legacy/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker, limit: 25 }) }));
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Review failed"); }
    finally { setBusy(false); }
  }
  async function inspect(accession: string) {
    setBusy(true); setError("");
    try { setDocument(await read(await apiFetch(`/api/trade-history/legacy/documents/${encodeURIComponent(accession)}`))); }
    catch (e) { setError(e instanceof Error ? e.message : "Document unavailable"); }
    finally { setBusy(false); }
  }
  const button = "rounded border border-zinc-700 px-3 py-2 text-xs disabled:opacity-40";
  return <section aria-label="Legacy filing review" className="rounded-xl border border-zinc-800 p-5 space-y-3">
    <h2 className="font-medium">Legacy Form 4 review</h2>
    <p className="text-sm text-zinc-400">{data?.archived ?? "…"}/{data?.total ?? "…"} original submissions archived and checked · {data?.needs_manual_mapping ?? "…"} filings with unresolved fields · {data?.unreviewed ?? "…"} awaiting archive review.</p>
    <p className="text-xs text-amber-200">These HTML filings remain historical context. Reported cells and footnotes do not establish trader profitability or executable copy returns. Original XML trade counts above remain separate.</p>
    <button className={button} disabled={busy || running || !data?.unreviewed} onClick={() => void reviewNext()}>Archive & review next 25 legacy filings</button>
    {data?.runs.slice(0,1).map(run => <p key={run.id} className="text-xs text-zinc-400">{run.state} · {run.reviewed}/{run.selected} checked · {run.failed} failed · {run.message}</p>)}
    {data?.references.map(ref => <div key={ref.accession} className="flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-2 text-xs">
      <span className="min-w-0 flex-1">{ref.ticker} · {ref.filed_date} · {ref.owners?.map(o => o.name).join("; ") || ref.accession}<span className="block text-zinc-500">{ref.accession} · {ref.transactions ?? 0} reported rows · {ref.status.replaceAll("_", " ")}</span></span>
      <button className={button} disabled={busy || ref.status === "unreviewed"} onClick={() => void inspect(ref.accession)}>Inspect {ref.accession}</button>
    </div>)}
    {data && <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500"><button className={button} disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0,offset-10))}>Previous legacy filings</button><span>{data.total ? offset+1 : 0}–{Math.min(offset+data.references.length,data.total)} of {data.total}</span><button className={button} disabled={busy || offset+data.references.length >= data.total} onClick={() => setOffset(offset+10)}>Next legacy filings</button></div>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    {document && <div className="min-w-0 space-y-3 rounded border border-zinc-700 p-3 text-xs">
      <p>{document.accession} · first archived {new Date(document.observed_at).toLocaleString()}</p>
      <a href={document.source_url} target="_blank" rel="noreferrer" className="text-sky-400 underline">Original complete SEC submission</a>
      <p className="break-all text-zinc-500">SHA-256: {document.sha256}</p>
      {document.review.amendment && <p className="text-amber-200">Amended filing: original transactions require reconciliation. Corrections do not create additional executions.</p>}
      {document.review.explanation_text && <details open><summary>Filing explanation and annotations</summary><p className="mt-2 whitespace-pre-wrap text-amber-200">{document.review.explanation_text}</p></details>}
      <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead><tr><th>Source row</th><th>Security / date</th><th>Code</th><th>Reported shares</th><th>Reported price</th><th>Review</th></tr></thead><tbody>{document.review.transactions.map(row => <tr key={`${row.table_number}-${row.row_number}`} className="border-t border-zinc-800">
        <td className="py-2 pr-3">Table {row.table_number} / {row.row_number}</td><td className="pr-3">{row.security}<span className="block text-zinc-500">{row.reported_date ?? "Date unresolved"}</span></td><td>{row.transaction_code}</td><td>{row.reported_shares ?? "Unknown"}</td><td>{row.reported_price === null ? "Not numeric / blank" : `$${row.reported_price}`}</td>
        <td className="max-w-60 py-2 text-amber-200">{row.issues.join("; ") || "Context only"}{row.numbered_annotations.length > 0 && <span className="block">Source notes {row.numbered_annotations.join(", ")}</span>}<details><summary>Raw cells</summary><pre className="max-w-60 whitespace-pre-wrap break-words text-zinc-400">{JSON.stringify(row.source_cells,null,2)}</pre></details></td>
      </tr>)}</tbody></table></div>
      {document.review.unresolved.map((gap,i) => <p key={i} className="text-amber-200">{gap.reason}</p>)}
      <details><summary>Review scope and limits</summary><ul className="mt-2 list-disc pl-4 text-zinc-400">{document.review.limitations.map(note => <li key={note}>{note}</li>)}</ul></details>
      <details><summary>Archived source text and all ownership tables</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words">{document.document_text}</pre><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(document.review.tables,null,2)}</pre></details>
    </div>}
  </section>;
}
