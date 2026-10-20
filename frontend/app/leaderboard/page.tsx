"use client";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
import { apiFetch } from "@/lib/apiFetch";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ArrowLeft, RefreshCw, Trophy } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface MetricMeta {
  key: string;
  label: string;
  higher_better: boolean;
}

interface LeaderboardRow {
  rank: number;
  ticker: string;
  company_name: string | null;
  signal: string | null;
  quant_score: number | null;
  value: number | null;
  upside_pct: number | null;
  current_price: number | null;
  percentile: number | null;
}

interface LeaderboardData {
  metric: string;
  label: string;
  higher_better: boolean;
  total_universe: number;
  excluded_count?: number;
  eligibility_basis?: string;
  results: LeaderboardRow[];
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const SIGNAL_CONFIG: Record<string, { label: string; color: string }> = {
  STRONG_BUY:   { label: "Strong Buy",   color: "bg-emerald-600 text-white" },
  BUY:          { label: "Buy",          color: "bg-green-600 text-white" },
  HOLD:         { label: "Hold",         color: "bg-yellow-600 text-white" },
  AVOID:        { label: "Avoid",        color: "bg-orange-600 text-white" },
  STRONG_AVOID: { label: "Strong Avoid", color: "bg-red-700 text-white" },
};

function SignalBadge({ signal }: { signal: string | null }) {
  if (!signal) return <span className="text-zinc-500">—</span>;
  const cfg = SIGNAL_CONFIG[signal] ?? { label: signal, color: "bg-zinc-600 text-white" };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg.color}`}>{cfg.label}</span>;
}

function fmtValue(val: number | null, metric: string): string {
  if (val === null) return "—";
  if (metric === "fcf_yield") return `${(val * 100).toFixed(2)}%`;
  if (["upside_pct", "roic_spread", "rule_of_40_score", "sloan_accrual", "owner_earnings_yield"].includes(metric))
    return `${val > 0 ? "+" : ""}${val.toFixed(1)}%`;
  if (metric === "peg_ratio_val") return `${val.toFixed(2)}×`;
  if (metric === "beneish_score") return val.toFixed(3);
  return val.toFixed(1);
}

function RankMedal({ rank }: { rank: number }) {
  if (rank === 1) return <span className="text-yellow-400 text-base">🥇</span>;
  if (rank === 2) return <span className="text-zinc-300 text-base">🥈</span>;
  if (rank === 3) return <span className="text-amber-600 text-base">🥉</span>;
  return <span className="text-zinc-500 tabular-nums text-sm">{rank}</span>;
}

function PercentileBar({ pct }: { pct: number | null }) {
  if (pct === null) return null;
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2 w-28">
      <div className="flex-1 h-1.5 bg-zinc-800 rounded overflow-hidden">
        <div className={`h-full rounded ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-zinc-400 tabular-nums w-8 text-right">{pct}%</span>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function LeaderboardPage() {
  const [metrics, setMetrics]     = useState<MetricMeta[]>([]);
  const [selected, setSelected]   = useState("quant_score");
  const [limit, setLimit]         = useState(100);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: string; data: LeaderboardData | null; error: string } | null>(null);
  const requestKey = `${selected}:${limit}:${revision}`;
  const data = result?.key === requestKey ? result.data : null;
  const error = result?.key === requestKey ? result.error : '';
  const loading = result?.key !== requestKey;
  const [search, setSearch]       = useState("");

  // Load metric list once
  useEffect(() => {
    apiFetch(`${API}/api/leaderboard/metrics`)
      .then(r => r.json())
      .then(d => setMetrics(d.metrics))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API}/api/leaderboard?metric=${selected}&limit=${limit}`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`Unable to load rankings (${response.status}). Please retry.`);
        const data: LeaderboardData = await response.json();
        if (!controller.signal.aborted) setResult({ key: requestKey, data, error: '' });
      })
      .catch(error => {
        if (!controller.signal.aborted) setResult({ key: requestKey, data: null, error: error.message });
      });
    return () => controller.abort();
  }, [selected, limit, requestKey]);

  const shown = data?.results.filter(r =>
    !search || r.ticker.includes(search.toUpperCase()) ||
    (r.company_name?.toLowerCase().includes(search.toLowerCase()))
  ) ?? [];

  const meta = metrics.find(m => m.key === selected);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {data?.eligibility_basis && <p className="px-6 py-3 text-xs text-amber-300">{data.eligibility_basis} {data.excluded_count} saved records excluded.</p>}
      {/* Header */}
      <div className="border-b border-zinc-800 px-6 py-4 flex flex-wrap gap-3 items-center justify-between">
        <div className="flex flex-wrap items-center gap-4">
          <Link href="/" className="text-zinc-400 hover:text-zinc-100 flex items-center gap-1 text-sm">
            <ArrowLeft size={14} /> Screener
          </Link>
          <span className="text-zinc-600">|</span>
          <Link href="/portfolio" className="text-zinc-400 hover:text-zinc-100 text-sm">Portfolio</Link>
          <span className="text-zinc-600">|</span>
          <Link href="/legend" className="text-zinc-400 hover:text-zinc-100 text-sm">Guide</Link>
          <span className="text-zinc-600">|</span>
          <div className="flex items-center gap-2">
            <Trophy size={16} className="text-yellow-400" />
            <h1 className="text-lg font-semibold">Leaderboard</h1>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={() => setRevision(value => value + 1)} disabled={loading} className="border-zinc-700 text-zinc-300">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </Button>
      </div>

      <div className="p-6 max-w-5xl mx-auto space-y-5">
        {/* Controls */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Metric selector */}
          <div className="flex flex-wrap gap-1.5">
            {metrics.map(m => (
              <button
                key={m.key}
                onClick={() => setSelected(m.key)}
                className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                  selected === m.key
                    ? "bg-violet-700 border-violet-600 text-white"
                    : "bg-zinc-900 border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <input
              className="bg-zinc-900 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none focus:border-zinc-500 w-36"
              placeholder="Filter ticker…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            <select
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
              className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-300 outline-none"
            >
              <option value={25}>Top 25</option>
              <option value={50}>Top 50</option>
              <option value={100}>Top 100</option>
              <option value={250}>Top 250</option>
            </select>
          </div>
        </div>

        {error && <p role="alert" className="text-sm text-red-300">{error}</p>}

        {/* Header stat */}
        {data && (
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-400">
            <span className="font-medium text-zinc-200">{meta?.label}</span>
            <span>·</span>
            <span>{data.total_universe} companies ranked</span>
            <span>·</span>
            <span className="text-zinc-500">
              {meta?.higher_better ? "Higher = better" : "Lower = better"}
            </span>
            {search && <span className="text-zinc-500 ml-2">— {shown.length} match</span>}
          </div>
        )}

        {/* Table */}
        <p className="text-xs text-zinc-500">Saved analysis values. Scores can differ from the dashboard recalculation until companies are reanalyzed. These rankings are not verified return forecasts.</p>
        <div className="rounded border border-zinc-800 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900">
                <th className="text-center px-3 py-2.5 text-zinc-400 font-medium w-12">#</th>
                <th className="text-left px-3 py-2.5 text-zinc-400 font-medium">Ticker</th>
                <th className="text-left px-3 py-2.5 text-zinc-400 font-medium hidden md:table-cell">Company</th>
                <th className="text-left px-3 py-2.5 text-zinc-400 font-medium">Signal</th>
                <th className="text-right px-3 py-2.5 text-zinc-400 font-medium">Score</th>
                <th className="text-right px-3 py-2.5 text-zinc-400 font-medium">
                  {meta?.label ?? "Value"}
                </th>
                <th className="text-right px-3 py-2.5 text-zinc-400 font-medium hidden sm:table-cell">Upside</th>
                <th className="text-right px-3 py-2.5 text-zinc-400 font-medium hidden sm:table-cell">Price</th>
                <th className="px-3 py-2.5 text-zinc-400 font-medium text-right">Percentile</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-zinc-500">Loading…</td>
                </tr>
              )}
              {!loading && !error && shown.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-zinc-500">No data yet — run the screener first.</td>
                </tr>
              )}
              {!loading && shown.map(row => (
                <tr key={row.ticker} className="border-b border-zinc-800/50 hover:bg-zinc-900/60 transition-colors">
                  <td className="px-3 py-2.5 text-center">
                    <RankMedal rank={row.rank} />
                  </td>
                  <td className="px-3 py-2.5">
                    <Link
                      href={`/?ticker=${row.ticker}`}
                      className="font-mono font-semibold text-zinc-100 hover:text-violet-300 transition-colors"
                    >
                      {row.ticker}
                    </Link>
                  </td>
                  <td className="px-3 py-2.5 text-zinc-400 text-xs hidden md:table-cell max-w-40 truncate">
                    {row.company_name ?? "—"}
                  </td>
                  <td className="px-3 py-2.5">
                    <SignalBadge signal={row.signal} />
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-zinc-300">
                    {row.quant_score ?? "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums font-medium text-zinc-100">
                    {fmtValue(row.value, selected)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-zinc-400 hidden sm:table-cell">
                    {row.upside_pct !== null ? `${row.upside_pct > 0 ? "+" : ""}${row.upside_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-zinc-400 hidden sm:table-cell">
                    {row.current_price ? `$${row.current_price.toFixed(2)}` : "—"}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <div className="flex justify-end">
                      <PercentileBar pct={row.percentile} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
