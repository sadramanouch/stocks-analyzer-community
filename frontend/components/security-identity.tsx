"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/apiFetch";

export type SecurityIdentityEvidence = {
  report_id: string; report_sha256: string; captured_at: string; valid_until: string; cik: string;
  filing: { url: string; form: string; filingDate: string; accessionNumber: string };
  share_class: { title: string; exchange: string };
};
type Report = { id: string; report_sha256: string; report: { status: string; source_ids: Record<string, string>; limits: string } };

export default function SecurityIdentity({ evidence }: { evidence: SecurityIdentityEvidence }) {
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void apiFetch(`/api/security-identity/reports/${encodeURIComponent(evidence.report_id)}`, { signal: controller.signal })
      .then(async response => {
        const result = await response.json() as Report;
        if (!response.ok || result.id !== evidence.report_id || result.report_sha256 !== evidence.report_sha256 || result.report.status !== "matched_common_share_class") throw new Error("The archived share-class evidence could not be reproduced.");
        if (!controller.signal.aborted) setData(result);
      }).catch(() => { if (!controller.signal.aborted) setError("The archived share-class evidence could not be reproduced. Treat the class as unverified until this is resolved."); });
    return () => controller.abort();
  }, [evidence.report_id, evidence.report_sha256]);
  return <section aria-label="SEC share-class evidence" className="space-y-3 rounded-lg border border-zinc-700 p-4 text-sm">
    <h3 className="font-medium">SEC share-class evidence</h3>
    <p>{evidence.share_class.title} · {evidence.share_class.exchange} · issuer CIK {evidence.cik}</p>
    <p className="text-xs text-zinc-400">{data ? "Reproduced from original SEC responses and the official listing." : error || "Checking the archived evidence…"}</p>
    <p className="text-xs text-zinc-400">Captured {evidence.captured_at} · valid before {evidence.valid_until}. The saved edition keeps its original evidence; current use also requires fresh listing and class evidence.</p>
    <a className="text-emerald-300 underline" href={evidence.filing.url} target="_blank" rel="noreferrer">SEC {evidence.filing.form} · filed {evidence.filing.filingDate}</a>
    {data && <details><summary className="cursor-pointer">Original sources and limits</summary><div className="mt-3 space-y-2 text-xs">
      {Object.entries(data.report.source_ids).map(([kind, id]) => <p key={kind}><a className="text-emerald-300 underline" href={`/api/backend/api/security-identity/sources/${encodeURIComponent(id)}`}>Download original {kind} response</a></p>)}
      <p className="text-zinc-400">{data.report.limits}</p>
      <p className="break-all text-zinc-500">Report SHA-256: {evidence.report_sha256}</p>
    </div></details>}
  </section>;
}
