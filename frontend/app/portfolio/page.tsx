"use client";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
import { apiFetch } from "@/lib/apiFetch";

import { useState, useEffect, useCallback, useMemo } from "react";
import {Disclosure,MetricStrip,ResearchNote,WorkspaceHeading,WorkspaceLoading,WorkspaceNav} from "@/components/workspace-ui";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import dynamic from "next/dynamic";
const PortfolioReconciliation=dynamic(()=>import("@/components/portfolio-reconciliation"));
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine, CartesianGrid, Legend,
} from "recharts";
import {
  Brain,
  ChevronUp, ChevronDown, ChevronsUpDown,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface Position {
  id: number;
  ticker: string;
  status: "open" | "closed";
  virtual_dollars: number;
  entry_price: number;
  entry_date: string;
  entry_signal: string;
  entry_quant_score: number | null;
  entry_fcf_yield: number | null;
  entry_peg_ratio: number | null;
  entry_upside_pct: number | null;
  entry_piotroski: number | null;
  entry_altman_z: number | null;
  entry_beneish: number | null;
  close_price: number | null;
  close_date: string | null;
  close_signal: string | null;
  close_reason: string | null;
  realized_return_pct: number | null;
  current_price: number | null;
  current_signal: string | null;
  current_score: number | null;
  return_pct: number | null;
  unrealized_pnl: number | null;
}

interface Summary {
  open_return_pct?: number | null;
  integrity?: { status: string; basis: string; open_entry_first: string | null; open_entry_last: string | null;
    missing_price_count: number; older_analysis_count: number; outlier_count: number; limitations: string[];
    open_completed_action_count?:number|null;completed_action_evidence_status?:string;
    open_completed_action_reviews?:{ticker:string;report_id:string;reported_conversion_date:string}[]|null };
  open_positions: number;
  closed_positions: number;
  open_deployed_usd: number;
  open_value_usd: number;
  open_unrealized_pnl: number;
  closed_realized_pnl: number;
  win_rate: number | null;
  avg_return_pct: number | null;
  best_return_pct: number | null;
  worst_return_pct: number | null;
  signal_accuracy: Record<string, { count: number; wins: number; avg_return: number; win_rate: number }>;
}

interface Correlation { r: number | null; n: number; label: string; }

interface RegressionEntry { coef: number; label: string; }
interface RegressionMeta  { r2: number | null; n: number; intercept: number; }

interface LearningRun {
  run_id?: number;
  id?: number;
  run_at: string;
  positions_analyzed: number;
  open_included?: number;
  closed_included?: number;
  correlations: Record<string, Correlation>;
  signal_accuracy: Record<string, { count: number; wins: number; avg_return: number; win_rate: number }>;
  regression?: Record<string, RegressionEntry> & { _meta?: RegressionMeta };
  interpretation?: string;
}

interface EquityCurvePoint { date: string; value: number; deployed: number; spy?: number; }
interface QuintileData { quintile: number; label: string; score_range: string; avg_return: number; win_rate: number; count: number; }
interface SectorStat { sector: string; count: number; win_rate: number; avg_return: number; best: number; worst: number; }

type SortDir = "asc" | "desc";
type OpenCol  = "ticker" | "entry_date" | "entry_signal" | "entry_quant_score" | "entry_price" | "current_price" | "return_pct" | "current_signal" | "age";
type ClosedCol = "ticker" | "entry_date" | "entry_signal" | "entry_quant_score" | "entry_price" | "close_price" | "realized_return_pct" | "close_reason" | "age";

// ─── Constants ────────────────────────────────────────────────────────────────

const SIGNAL_ORDER: Record<string, number> = {
  STRONG_BUY: 0, BUY: 1, HOLD: 2, AVOID: 3, STRONG_AVOID: 4,
};

const SIGNAL_CONFIG: Record<string, { label: string; color: string }> = {
  STRONG_BUY:   { label: "Strong Buy",   color: "bg-emerald-600 text-white" },
  BUY:          { label: "Buy",          color: "bg-green-600 text-white" },
  HOLD:         { label: "Hold",         color: "bg-yellow-600 text-white" },
  AVOID:        { label: "Avoid",        color: "bg-orange-600 text-white" },
  STRONG_AVOID: { label: "Strong Avoid", color: "bg-red-700 text-white" },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function SignalBadge({ signal }: { signal: string | null }) {
  if (!signal) return <span className="text-zinc-500">—</span>;
  const cfg = SIGNAL_CONFIG[signal] ?? { label: signal, color: "bg-zinc-600 text-white" };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg.color}`}>{cfg.label}</span>;
}

function fmtPct(v: number | null, decimals = 1) {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}%`;
}

function fmtUsd(v: number) { return `$${v.toFixed(2)}`; }

function ReturnCell({ v }: { v: number | null }) {
  if (v === null) return <span className="text-zinc-500">—</span>;
  const cls = v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-zinc-400";
  return <span className={cls}>{fmtPct(v)}</span>;
}

function daysSince(dateStr: string) {
  return Math.floor((Date.now() - new Date(dateStr).getTime()) / 86_400_000);
}

function SortIcon({ col, active, dir }: { col: string; active: string; dir: SortDir }) {
  if (col !== active) return <ChevronsUpDown size={12} className="text-zinc-600 ml-1 inline" />;
  return dir === "asc"
    ? <ChevronUp size={12} className="text-zinc-300 ml-1 inline" />
    : <ChevronDown size={12} className="text-zinc-300 ml-1 inline" />;
}

function Th({
  label, col, active, dir, onSort, right,
}: {
  label: string; col: string; active: string; dir: SortDir;
  onSort: (c: string) => void; right?: boolean;
}) {
  return (
    <th
      className={`px-3 py-2 text-zinc-400 font-medium cursor-pointer select-none hover:text-zinc-200 whitespace-nowrap ${right ? "text-right" : "text-left"}`}
      onClick={() => onSort(col)}
    >
      {label}<SortIcon col={col} active={active} dir={dir} />
    </th>
  );
}

function sortPositions<T extends Position>(
  rows: T[], col: string, dir: SortDir,
): T[] {
  return [...rows].sort((a, b) => {
    let va: number | string | null;
    let vb: number | string | null;

    switch (col) {
      case "ticker":           va = a.ticker;           vb = b.ticker; break;
      case "entry_date":       va = a.entry_date;       vb = b.entry_date; break;
      case "entry_signal":
        va = SIGNAL_ORDER[a.entry_signal] ?? 99;
        vb = SIGNAL_ORDER[b.entry_signal] ?? 99; break;
      case "entry_quant_score": va = a.entry_quant_score ?? -1; vb = b.entry_quant_score ?? -1; break;
      case "entry_price":      va = a.entry_price;      vb = b.entry_price; break;
      case "current_price":    va = a.current_price ?? -1;   vb = b.current_price ?? -1; break;
      case "close_price":      va = a.close_price ?? -1;     vb = b.close_price ?? -1; break;
      case "return_pct":       va = a.return_pct ?? -9999;   vb = b.return_pct ?? -9999; break;
      case "realized_return_pct": va = a.realized_return_pct ?? -9999; vb = b.realized_return_pct ?? -9999; break;
      case "current_signal":
        va = SIGNAL_ORDER[a.current_signal ?? ""] ?? 99;
        vb = SIGNAL_ORDER[b.current_signal ?? ""] ?? 99; break;
      case "close_reason":     va = a.close_reason ?? ""; vb = b.close_reason ?? ""; break;
      case "age":
        va = daysSince(a.entry_date);
        vb = daysSince(b.entry_date); break;
      default: return 0;
    }

    if (va === null || va === undefined) va = typeof vb === "string" ? "" : -9999;
    if (vb === null || vb === undefined) vb = typeof va === "string" ? "" : -9999;

    const cmp = typeof va === "string"
      ? va.localeCompare(vb as string)
      : (va as number) - (vb as number);
    return dir === "asc" ? cmp : -cmp;
  });
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const [activeTab,setActiveTab]=useState("positions");
  const [loadError,setLoadError]=useState("");
  const [analyticsLoading,setAnalyticsLoading]=useState(false);
  const [revision,setRevision]=useState(0);
  const [positions, setPositions]     = useState<Position[]>([]);
  const [summary, setSummary]         = useState<Summary | null>(null);
  const [latestLearn, setLatestLearn] = useState<LearningRun | null>(null);
  const [loading, setLoading]         = useState(true);
  const [equityCurve, setEquityCurve] = useState<EquityCurvePoint[]>([]);
  const [quintiles, setQuintiles]     = useState<QuintileData[]>([]);
  const [sectorStats, setSectorStats] = useState<SectorStat[]>([]);
  const [snapshotStatus, setSnapshotStatus] = useState<string | null>(null);
  const [refreshStatus, setRefreshStatus]   = useState<string | null>(null);
  const [learnStatus, setLearnStatus] = useState<string | null>(null);
  const [closingId, setClosingId]     = useState<number | null>(null);
  const [showClosed, setShowClosed]   = useState(false);
  const [search, setSearch]           = useState("");

  // Separate sort state for open / closed tabs
  const [openSort,   setOpenSort]   = useState<{ col: OpenCol;   dir: SortDir }>({ col: "return_pct",          dir: "desc" });
  const [closedSort, setClosedSort] = useState<{ col: ClosedCol; dir: SortDir }>({ col: "realized_return_pct", dir: "desc" });

  const load = useCallback(async()=>{
    setLoadError("");
    try {
      const [posRes,sumRes]=await Promise.all([apiFetch(`${API}/api/portfolio/positions`),apiFetch(`${API}/api/portfolio/summary`)]);
      if(!posRes.ok||!sumRes.ok)throw new Error("Portfolio unavailable. Please try again.");
      const [posData,sumData]=await Promise.all([posRes.json(),sumRes.json()]);
      setPositions(posData.positions);setSummary(sumData);setRevision(v=>v+1);
    }catch(e){setLoadError(e instanceof Error?e.message:"Could not refresh portfolio.");}
    finally{setLoading(false);}
  },[]);
  const loadAnalytics=useCallback(async()=>{
    setAnalyticsLoading(true);
    try {
      const responses=await Promise.all([apiFetch(`${API}/api/portfolio/learn/history`),apiFetch(`${API}/api/portfolio/equity-curve`),apiFetch(`${API}/api/portfolio/quintile-analysis`),apiFetch(`${API}/api/portfolio/sector-breakdown`)]);
      if(responses.some(r=>!r.ok))throw new Error("Some analytics are unavailable. Your holdings are still available.");
      const [learn,curve,quint,sect]=await Promise.all(responses.map(r=>r.json()));
      if(learn.runs?.length)setLatestLearn(learn.runs[0]);setEquityCurve(curve.curve??[]);setQuintiles(quint.quintiles??[]);setSectorStats(sect.sectors??[]);
    }catch(e){setLoadError(e instanceof Error?e.message:"Analytics unavailable.");}
    finally{setAnalyticsLoading(false);}
  },[]);
  useEffect(()=>{void Promise.resolve().then(()=>load());},[load]);
  useEffect(()=>{if(activeTab==="performance"||activeTab==="learning")void Promise.resolve().then(()=>loadAnalytics());},[activeTab,revision,loadAnalytics]);

  const refreshPrices = async () => {
    setRefreshStatus("fetching…");
    try {
      const r = await apiFetch(`${API}/api/portfolio/refresh-prices`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) { setRefreshStatus(d.detail || "Price refresh failed"); return; }
      setRefreshStatus(`Updated ${d.updated} tickers`);
      await load();
    } catch { setRefreshStatus("Error"); }
  };

  const runSnapshot = async () => {
    setSnapshotStatus("running…");
    try {
      const r = await apiFetch(`${API}/api/portfolio/snapshot`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) { setSnapshotStatus(d.detail || "Snapshot failed"); return; }
      setSnapshotStatus(`Snapped ${d.snapped} positions, closed ${d.auto_closed}`);
      await load();
    } catch { setSnapshotStatus("Error"); }
  };

  const runLearn = async () => {
    setLearnStatus("analyzing…");
    try {
      const r = await apiFetch(`${API}/api/portfolio/learn`, { method: "POST" });
      if (!r.ok) { setLearnStatus((await r.json()).detail || "Analysis failed"); return; }
      const d: LearningRun = await r.json();
      setLatestLearn(d);
      setLearnStatus(`Done — ${d.positions_analyzed} positions`);
    } catch { setLearnStatus("Error"); }
  };

  const closePosition = async (id: number) => {
    setClosingId(id);
    try {
      await apiFetch(`${API}/api/portfolio/positions/${id}/close`, { method: "POST" });
      await load();
    } catch {}
    setClosingId(null);
  };

  const toggleSort = useCallback((col: string, isOpen: boolean) => {
    if (isOpen) {
      setOpenSort(prev => ({
        col: col as OpenCol,
        dir: prev.col === col ? (prev.dir === "asc" ? "desc" : "asc") : "desc",
      }));
    } else {
      setClosedSort(prev => ({
        col: col as ClosedCol,
        dir: prev.col === col ? (prev.dir === "asc" ? "desc" : "asc") : "desc",
      }));
    }
  }, []);

  const openPos   = useMemo(() => positions.filter(p => p.status === "open"),   [positions]);
  const closedPos = useMemo(() => positions.filter(p => p.status === "closed"), [positions]);

  const filtered = useMemo(() => {
    const q = search.trim().toUpperCase();
    const base = showClosed ? closedPos : openPos;
    return q ? base.filter(p => p.ticker.includes(q)) : base;
  }, [search, showClosed, openPos, closedPos]);

  const sorted = useMemo(() => {
    const { col, dir } = showClosed ? closedSort : openSort;
    return sortPositions(filtered, col, dir);
  }, [filtered, showClosed, openSort, closedSort]);

  const pnlChartData = useMemo(() =>
    closedPos
      .filter(p => p.realized_return_pct !== null)
      .sort((a, b) => b.realized_return_pct! - a.realized_return_pct!)
      .slice(0, 30)
      .map(p => ({ ticker: p.ticker, return: p.realized_return_pct! })),
    [closedPos]);

  const corrData = useMemo(() =>
    latestLearn
      ? Object.entries(latestLearn.correlations ?? {})
          .filter(([, c]) => c.r !== null)
          .sort((a, b) => Math.abs(b[1].r!) - Math.abs(a[1].r!))
          .map(([, c]) => ({ label: c.label, r: c.r!, n: c.n }))
      : [],
    [latestLearn]);

  if(loading)return <main className="min-h-screen bg-zinc-950 text-zinc-100"><WorkspaceNav/><div className="workspace-page"><WorkspaceHeading title="Paper portfolio"/><WorkspaceLoading title="Loading holdings"/></div></main>;

  const { col: oCol, dir: oDir } = openSort;
  const { col: cCol, dir: cDir } = closedSort;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <WorkspaceNav/>
      <div className="workspace-page pb-0"><WorkspaceHeading eyebrow="Simulated holdings" title="Paper portfolio" description="Explore saved positions. These are not broker balances."><Button variant="outline" size="sm" onClick={refreshPrices} disabled={refreshStatus==="fetching…"}>Refresh prices</Button><Button variant="outline" size="sm" onClick={()=>void load()}>Refresh</Button></WorkspaceHeading></div>
      {refreshStatus && (
        <div className="bg-zinc-800 border-b border-zinc-700 px-6 py-2 text-sm text-zinc-300">
          {refreshStatus}
        </div>
      )}
      {snapshotStatus && (
        <div className="bg-zinc-800 border-b border-zinc-700 px-6 py-2 text-sm text-zinc-300">
          {snapshotStatus}
        </div>
      )}

      <div className="workspace-page">
        {loadError&&<div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-900 p-3 text-sm text-amber-200">{loadError}<Button variant="outline" size="sm" onClick={()=>void load()}>Try again</Button></div>}
        <MetricStrip items={[{label:"Open positions",value:summary?.open_positions??"—",note:summary?.integrity?`${summary.integrity.missing_price_count} missing prices`:undefined},{label:"Closed positions",value:summary?closedPos.length:"—"}]}/>
        <ResearchNote message="Paper holdings · historical returns unverified."><p>These saved positions and historical figures are simulated and incompletely verified. They are separate from your forward paper cash account and any real brokerage account.</p></ResearchNote>
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          {analyticsLoading&&<p role="status" className="text-xs text-zinc-400">Updating analytics…</p>}
          <TabsList className="h-auto min-h-10 max-w-full flex-nowrap justify-start overflow-x-auto bg-zinc-900 border border-zinc-800 [&>button]:shrink-0">
            <TabsTrigger value="positions">
              Holdings
            </TabsTrigger>
            <TabsTrigger value="performance">Performance</TabsTrigger>
            <TabsTrigger value="reconciliation">Records &amp; checks</TabsTrigger>
            <TabsTrigger value="learning">
              <Brain size={14} className="mr-1" /> Diagnostics
            </TabsTrigger>
          </TabsList>

          {/* ── Positions Tab ─────────────────────────────────────────── */}
          <TabsContent value="reconciliation" className="mt-4"><Button variant="outline" size="sm" onClick={runSnapshot}>Save daily snapshot</Button><Disclosure title="Portfolio data notes">        {summary?.integrity && <section aria-label="Portfolio evidence quality" className="rounded-xl border border-amber-800 bg-amber-950/20 p-4 text-sm text-amber-200 space-y-2">
          <p className="font-semibold">Paper results · performance not yet verified</p>
          <p>{summary.integrity.basis}</p>
          <p>Open positions entered {summary.integrity.open_entry_first ?? "—"} to {summary.integrity.open_entry_last ?? "—"}. Marked return on their cost: {fmtPct(summary.open_return_pct ?? null)}.</p>
          <p>{summary.integrity.older_analysis_count} open positions have analyses older than two days; {summary.integrity.missing_price_count} have missing/invalid marks. {summary.integrity.outlier_count} closed returns need unusually large-move review.</p>
          {!!summary.integrity.open_completed_action_count&&<p>{summary.integrity.open_completed_action_count} positions still recorded open span issuer-reported completed conversions. Their old-symbol book marks cannot establish current holdings or value: {summary.integrity.open_completed_action_reviews?.map((r,i)=><span key={r.report_id}>{i>0?', ':''}<a href={`/api/backend/api/completed-actions/reports/${r.report_id}`} className="underline" target="_blank" rel="noreferrer">{r.ticker} · {r.reported_conversion_date}</a></span>)}. Actual settlement records are needed.</p>}
          {summary.integrity.completed_action_evidence_status==='integrity_failed'&&<p role="alert">Corporate-action evidence failed verification. The affected-position count is unknown.</p>}
          <details><summary className="cursor-pointer">What these numbers do and do not establish</summary><ul className="list-disc pl-5 mt-2 space-y-1">{summary.integrity.limitations.map(line => <li key={line}>{line}</li>)}</ul></details>
        </section>}
</Disclosure>        <details className="rounded-lg border border-zinc-800 p-3 mb-4"><summary className="cursor-pointer text-sm text-amber-200">Original recorded portfolio figures · unverified</summary>
        {/* Summary Strip */}
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            {[
              { label: "Open Positions",  value: summary.open_positions.toString() },
              { label: "Open-position cost",        value: fmtUsd(summary.open_deployed_usd) },
              { label: "Open marked value",   value: fmtUsd(summary.open_value_usd) },
              { label: "Unrealized P&L",  value: fmtUsd(summary.open_unrealized_pnl),
                color: summary.open_unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400" },
              { label: "Recorded price-only P&L",   value: fmtUsd(summary.closed_realized_pnl) },
              { label: "Positive recorded price changes",        value: summary.win_rate !== null ? `${summary.win_rate}%` : "—" },
              { label: "Mean recorded price change",      value: fmtPct(summary.avg_return_pct) },
            ].map(({ label, value, color }) => (
              <Card key={label} className="bg-zinc-900 border-zinc-800">
                <CardContent className="p-3">
                  <div className="text-xs text-zinc-500 mb-1">{label}</div>
                  <div className={`text-lg font-semibold ${color ?? ""}`}>{value}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
        <p className="text-xs text-amber-200 mt-2">Recorded closed-position metrics use the original price ratios and can omit split share changes. Review the historical market comparisons under Reconciliation & ledger; these recorded statistics are not verified account performance.</p>

        </details>

<PortfolioReconciliation /></TabsContent>
          <TabsContent value="positions" className="mt-4 space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={() => setShowClosed(false)}
                className={`text-sm px-3 py-1 rounded ${!showClosed ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                Open ({openPos.length})
              </button>
              <button
                onClick={() => setShowClosed(true)}
                className={`text-sm px-3 py-1 rounded ${showClosed ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
              >
                Closed ({closedPos.length})
              </button>
              <input
                className="ml-auto bg-zinc-900 border border-zinc-700 rounded px-3 py-1 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none focus:border-zinc-500 uppercase w-36"
                placeholder="Search ticker…"
                value={search}
                onChange={e => setSearch(e.target.value.toUpperCase())}
              />
              <span className="text-xs text-zinc-600">{sorted.length} shown</span>
            </div>

            {sorted.length === 0 ? (
              <div className="text-zinc-500 text-sm py-8 text-center">
                {search ? `No positions matching "${search}"` : showClosed
                  ? "No closed positions yet."
                  : "No open positions. Run the screener to allocate positions automatically."}
              </div>
            ) : (
              <div className="overflow-x-auto rounded border border-zinc-800">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-zinc-800 bg-zinc-900">
                      {!showClosed ? (
                        <>
                          <Th label="Ticker"  col="ticker"           active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} />
                          <Th label="Entry"   col="entry_date"       active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} />
                          <Th label="Signal"  col="entry_signal"     active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} />
                          <Th label="Score"   col="entry_quant_score" active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} right />
                          <Th label="Entry $" col="entry_price"      active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} right />
                          <Th label="Saved mark"   col="current_price"    active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} right />
                          <Th label="Price change"  col="return_pct"       active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} right />
                          <Th label="Model view" col="current_signal" active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} />
                          <Th label="Age"     col="age"              active={oCol} dir={oDir} onSort={c => toggleSort(c, true)} right />
                          <th className="px-3 py-2" />
                        </>
                      ) : (
                        <>
                          <Th label="Ticker"  col="ticker"              active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} />
                          <Th label="Entry"   col="entry_date"          active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} />
                          <Th label="Signal"  col="entry_signal"        active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} />
                          <Th label="Score"   col="entry_quant_score"   active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} right />
                          <Th label="Entry $" col="entry_price"         active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} right />
                          <Th label="Exit $"  col="close_price"         active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} right />
                          <Th label="Price change"  col="realized_return_pct" active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} right />
                          <Th label="Reason"  col="close_reason"        active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} />
                          <Th label="Age"     col="age"                 active={cCol} dir={cDir} onSort={c => toggleSort(c, false)} right />
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map(p => (
                      <tr key={p.id} className="border-b border-zinc-800/50 hover:bg-zinc-900/50">
                        <td className="px-3 py-2 font-mono font-semibold text-zinc-100">{p.ticker}</td>
                        <td className="px-3 py-2 text-zinc-400 text-xs">{p.entry_date}</td>
                        <td className="px-3 py-2"><SignalBadge signal={p.entry_signal} /></td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {!showClosed && p.current_score != null && p.entry_quant_score != null ? (
                            <span className="inline-flex items-center gap-1">
                              <span className="text-zinc-500">{p.entry_quant_score}</span>
                              <span className="text-zinc-600">→</span>
                              <span className={p.current_score > p.entry_quant_score ? "text-emerald-400" : p.current_score < p.entry_quant_score ? "text-red-400" : "text-zinc-400"}>
                                {p.current_score}
                              </span>
                              {p.current_score !== p.entry_quant_score && (
                                <span className={`text-[10px] ${p.current_score > p.entry_quant_score ? "text-emerald-500" : "text-red-500"}`}>
                                  ({p.current_score > p.entry_quant_score ? "+" : ""}{p.current_score - p.entry_quant_score})
                                </span>
                              )}
                            </span>
                          ) : (
                            <span className="text-zinc-400">{p.entry_quant_score ?? "—"}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right text-zinc-300 tabular-nums">${p.entry_price.toFixed(2)}</td>
                        <td className="px-3 py-2 text-right text-zinc-300 tabular-nums">
                          {!showClosed
                            ? (p.current_price ? `$${p.current_price.toFixed(2)}` : "—")
                            : (p.close_price   ? `$${p.close_price.toFixed(2)}`   : "—")}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          <ReturnCell v={!showClosed ? p.return_pct : p.realized_return_pct} />
                        </td>
                        {!showClosed
                          ? <td className="px-3 py-2"><SignalBadge signal={p.current_signal} /></td>
                          : <td className="px-3 py-2 text-xs text-zinc-500">{p.close_reason ?? "—"}</td>
                        }
                        <td className="px-3 py-2 text-right text-xs text-zinc-500 tabular-nums">
                          {daysSince(p.entry_date)}d
                        </td>
                        {!showClosed && (
                          <td className="px-3 py-2 text-right">
                            <Button
                              variant="ghost" size="sm"
                              className="text-xs text-zinc-500 hover:text-red-400 h-6 px-2"
                              disabled={closingId === p.id}
                              onClick={() => closePosition(p.id)}
                            >
                              {closingId === p.id ? "…" : "Close"}
                            </Button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>

          {/* ── Performance Tab ───────────────────────────────────────── */}
          <TabsContent value="performance" className="mt-4 space-y-6">

            {/* Equity Curve */}
            {equityCurve.length > 0 && (
              <Card className="bg-zinc-900 border-zinc-800">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-zinc-300">
                    Recorded position values
                    <span className="text-zinc-500 font-normal ml-2 text-xs">changing snapshot coverage; not a portfolio return</span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={equityCurve} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#71717a" }} />
                      <YAxis tickFormatter={v => `$${(v / 1000).toFixed(1)}k`} tick={{ fontSize: 10, fill: "#71717a" }} />
                      <Tooltip
                        formatter={(v: number | undefined, name: string | undefined) => {
                          if (v == null) return ["—", name ?? ""];
                          if (name === "value") return [`$${v.toFixed(2)}`, "Portfolio Value"];
                          if (name === "spy")   return [`$${v.toFixed(2)}`, "SPY (normalized)"];
                          return [`$${v.toFixed(2)}`, "Deployed"];
                        }}
                        contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }}
                      />
                      <Legend wrapperStyle={{ fontSize: 11, color: "#71717a" }} />
                      <ReferenceLine y={equityCurve[0]?.deployed} stroke="#52525b" strokeDasharray="4 4" />
                      <Line type="monotone" dataKey="deployed" stroke="#3f3f46" strokeWidth={1} dot={false} name="Deployed" />
                      <Line type="monotone" dataKey="value" stroke="#10b981" strokeWidth={2} dot={{ r: 3, fill: "#10b981" }} name="Portfolio Value" />
                    </LineChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}

            {/* Quintile Analysis */}
            {quintiles.length > 0 && (
              <Card className="bg-zinc-900 border-zinc-800">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-zinc-300">
                    Score Quintile Performance
                    <span className="text-zinc-500 font-normal ml-2 text-xs">— does higher score = better return?</span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={quintiles} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                      <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#71717a" }}
                        tickFormatter={(v, i) => `${v}\n${quintiles[i]?.score_range ?? ""}`} />
                      <YAxis tickFormatter={v => `${v > 0 ? "+" : ""}${v}%`} tick={{ fontSize: 10, fill: "#71717a" }} />
                      <Tooltip
                        content={({ active, payload }) => {
                          if (!active || !payload?.length) return null;
                          const d = payload[0].payload as QuintileData;
                          return (
                            <div className="bg-zinc-900 border border-zinc-700 rounded p-2 text-xs space-y-1">
                              <div className="text-zinc-300 font-medium">{d.label} — Score {d.score_range}</div>
                              <div className={d.avg_return >= 0 ? "text-emerald-400" : "text-red-400"}>Avg Return: {fmtPct(d.avg_return)}</div>
                              <div className="text-zinc-400">Win Rate: {d.win_rate}%</div>
                              <div className="text-zinc-500">{d.count} positions</div>
                            </div>
                          );
                        }}
                      />
                      <ReferenceLine y={0} stroke="#52525b" />
                      <Bar dataKey="avg_return" radius={[2, 2, 0, 0]} name="Avg Return">
                        {quintiles.map((d, i) => (
                          <Cell key={i} fill={d.avg_return >= 0 ? "#10b981" : "#ef4444"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                  <div className="flex gap-4 mt-3">
                    {quintiles.map(q => (
                      <div key={q.quintile} className="flex-1 text-center text-xs text-zinc-500">
                        <div className="text-zinc-400">{q.label}</div>
                        <div>{q.score_range}</div>
                        <div className="text-zinc-600">{q.win_rate}% win</div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Sector Breakdown */}
            {sectorStats.length > 0 && (
              <Card className="bg-zinc-900 border-zinc-800">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-zinc-300">
                    Sector Performance
                    <span className="text-zinc-500 font-normal ml-2 text-xs">— by avg return across all positions</span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-zinc-500 border-b border-zinc-800">
                          <th className="text-left pb-2 font-medium">Sector</th>
                          <th className="text-right pb-2 font-medium">Positions</th>
                          <th className="text-right pb-2 font-medium">Win Rate</th>
                          <th className="text-right pb-2 font-medium">Avg Return</th>
                          <th className="text-right pb-2 font-medium">Best</th>
                          <th className="text-right pb-2 font-medium">Worst</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sectorStats.map(s => (
                          <tr key={s.sector} className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
                            <td className="py-2 text-zinc-300 font-medium">{s.sector}</td>
                            <td className="py-2 text-right text-zinc-400">{s.count}</td>
                            <td className="py-2 text-right text-zinc-400">{s.win_rate}%</td>
                            <td className={`py-2 text-right font-semibold ${s.avg_return >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                              {s.avg_return >= 0 ? "+" : ""}{s.avg_return}%
                            </td>
                            <td className="py-2 text-right text-emerald-500/80">+{s.best}%</td>
                            <td className="py-2 text-right text-red-500/80">{s.worst}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Closed-position charts */}
            {closedPos.length > 0 && (
              <>
                {summary?.signal_accuracy && Object.keys(summary.signal_accuracy).length > 0 && (
                  <Card className="bg-zinc-900 border-zinc-800">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-medium text-zinc-300">Saved signal outcomes (closed)</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-6">
                        {Object.entries(summary.signal_accuracy)
                          .sort((a, b) => (SIGNAL_ORDER[a[0]] ?? 99) - (SIGNAL_ORDER[b[0]] ?? 99))
                          .map(([sig, acc]) => (
                          <div key={sig} className="flex flex-col items-center gap-1">
                            <SignalBadge signal={sig} />
                            <div className="text-xs text-zinc-400">{acc.count} positions</div>
                            <div className={`text-sm font-semibold ${acc.avg_return >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                              {fmtPct(acc.avg_return)} avg
                            </div>
                            <div className="text-xs text-zinc-500">{acc.win_rate}% win</div>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                )}
                {pnlChartData.length > 0 && (
                  <Card className="bg-zinc-900 border-zinc-800">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-medium text-zinc-300">Saved price changes (top 30)</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={pnlChartData} margin={{ top: 4, right: 8, left: 0, bottom: 40 }}>
                          <XAxis dataKey="ticker" tick={{ fontSize: 10, fill: "#71717a" }} angle={-45} textAnchor="end" />
                          <YAxis tickFormatter={v => `${v > 0 ? "+" : ""}${v}%`} tick={{ fontSize: 10, fill: "#71717a" }} />
                          <Tooltip
                            formatter={(v: number | undefined) => v !== undefined ? [`${v >= 0 ? "+" : ""}${v.toFixed(1)}%`, "Return"] : ["—", "Return"]}
                            contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }}
                          />
                          <ReferenceLine y={0} stroke="#52525b" />
                          <Bar dataKey="return" radius={[2, 2, 0, 0]}>
                            {pnlChartData.map((d, i) => (
                              <Cell key={i} fill={d.return >= 0 ? "#10b981" : "#ef4444"} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </CardContent>
                  </Card>
                )}
              </>
            )}

            {equityCurve.length === 0 && quintiles.length === 0 && closedPos.length === 0 && (
              <div className="text-zinc-500 text-sm py-8 text-center">
                Run a Daily Snapshot to start tracking portfolio performance over time.
              </div>
            )}
          </TabsContent>

          {/* ── Learning Tab ──────────────────────────────────────────── */}
          <TabsContent value="learning" className="mt-4 space-y-4">
            <div className="flex items-start justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-base font-semibold text-zinc-200">Factor Correlation Analysis</h2>
                <p className="text-xs text-zinc-500 mt-1">
                  Uses all positions (open + closed). Open positions use current unrealized return as signal.
                  Pearson r describes correlation. OLS coefficients describe this sample; they are not recommended trading weights.
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <Button size="sm" onClick={runLearn} className="bg-violet-700 hover:bg-violet-600 text-white">
                  <Brain size={14} className="mr-1" /> Run Analysis (open + closed)
                </Button>
                {learnStatus && <span className="text-xs text-zinc-400">{learnStatus}</span>}
              </div>
            </div>

            {latestLearn ? (
              <>
                <div className="text-xs text-zinc-500 flex gap-4 flex-wrap">
                  <span>Last run: {new Date(latestLearn.run_at).toLocaleString()}</span>
                  <span>{latestLearn.positions_analyzed} positions
                    {latestLearn.open_included != null && ` (${latestLearn.open_included} open · ${latestLearn.closed_included} closed)`}
                  </span>
                  {latestLearn.regression?._meta?.r2 != null && (
                    <span className="text-violet-400">Model R² = {latestLearn.regression._meta.r2.toFixed(3)}</span>
                  )}
                </div>

                {corrData.length > 0 && (
                  <Card className="bg-zinc-900 border-zinc-800">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-medium text-zinc-300">
                        Pearson r: Factor → Saved price change
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="text-xs text-zinc-500 mb-3">
                        Green = positive sample correlation · Red = negative · Width = magnitude
                      </div>
                      <div className="space-y-2">
                        {corrData.map(({ label, r, n }) => (
                          <div key={label} className="flex items-center gap-3">
                            <div className="w-32 text-xs text-zinc-400 text-right shrink-0">{label}</div>
                            <div className="flex-1 h-5 bg-zinc-800 rounded relative overflow-hidden">
                              <div
                                className={`h-full rounded ${r >= 0 ? "bg-emerald-600" : "bg-red-600"}`}
                                style={{ width: `${Math.min(100, Math.abs(r) * 100)}%` }}
                              />
                            </div>
                            <div className="w-20 text-xs font-mono text-right shrink-0">
                              <span className={r >= 0 ? "text-emerald-400" : "text-red-400"}>
                                r={r >= 0 ? "+" : ""}{r.toFixed(3)}
                              </span>
                              <span className="text-zinc-600 ml-1">n={n}</span>
                            </div>
                            <div className="w-16 text-right shrink-0">
                              {Math.abs(r) >= 0.3
                                ? <Badge className="text-xs bg-violet-900 text-violet-300 border-0">signal</Badge>
                                : Math.abs(r) >= 0.1
                                ? <Badge className="text-xs bg-zinc-800 text-zinc-400 border-0">weak</Badge>
                                : <Badge className="text-xs bg-zinc-900 text-zinc-600 border-0">noise</Badge>}
                            </div>
                          </div>
                        ))}
                      </div>
                      <p className="text-xs text-zinc-600 mt-4">
                        {latestLearn.interpretation}{" "}
                        Beneish M: lower = cleaner, so negative r is expected.
                      </p>
                    </CardContent>
                  </Card>
                )}

                {/* OLS Regression — descriptive sample associations */}
                {latestLearn.regression && latestLearn.regression._meta?.r2 != null && (() => {
                  const reg = latestLearn.regression!;
                  const meta = reg._meta as RegressionMeta;
                  const factors = Object.entries(reg)
                    .filter(([k]) => k !== "_meta" && k !== "_error")
                    .map(([, v]) => v as RegressionEntry)
                    .sort((a, b) => Math.abs(b.coef) - Math.abs(a.coef));
                  const maxAbs = Math.max(...factors.map(f => Math.abs(f.coef)), 0.01);
                  return (
                    <Card className="bg-zinc-900 border-zinc-800">
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-zinc-300">
                          OLS Regression — In-sample Associations
                          <span className="text-zinc-500 font-normal ml-2 text-xs">R² = {meta.r2?.toFixed(3)} · n = {meta.n}</span>
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        <div className="text-xs text-zinc-500 mb-3">
                          Standardized coefficients fitted to the same sample. Colors show coefficient sign; bar widths show magnitude.
                        </div>
                        <div className="space-y-2">
                          {factors.map(f => (
                            <div key={f.label} className="flex items-center gap-3">
                              <div className="w-32 text-xs text-zinc-400 text-right shrink-0">{f.label}</div>
                              <div className="flex-1 h-5 bg-zinc-800 rounded relative overflow-hidden">
                                <div
                                  className={`h-full rounded ${f.coef >= 0 ? "bg-emerald-600" : "bg-red-600"}`}
                                  style={{ width: `${Math.min(100, Math.abs(f.coef) / maxAbs * 100)}%` }}
                                />
                              </div>
                              <div className="w-24 text-xs font-mono text-right shrink-0">
                                <span className={f.coef >= 0 ? "text-emerald-400" : "text-red-400"}>
                                  {f.coef >= 0 ? "+" : ""}{f.coef.toFixed(3)}
                                </span>
                              </div>
                              <div className="w-20 text-right shrink-0 text-xs">
                                {Math.abs(f.coef) / maxAbs >= 0.5
                                  ? <Badge className="text-xs bg-violet-900 text-violet-300 border-0">high impact</Badge>
                                  : Math.abs(f.coef) / maxAbs >= 0.2
                                  ? <Badge className="text-xs bg-zinc-800 text-zinc-400 border-0">moderate</Badge>
                                  : <Badge className="text-xs bg-zinc-900 text-zinc-600 border-0">low</Badge>}
                              </div>
                            </div>
                          ))}
                        </div>
                        <p className="text-xs text-zinc-600 mt-4">
                          Do not use these coefficients as trading weights. Split-distorted returns, unequal holding periods and overlapping positions can dominate the fit. Validate on corrected data and an untouched test period first.
                        </p>
                      </CardContent>
                    </Card>
                  );
                })()}

                {Object.keys(latestLearn.signal_accuracy).length > 0 && (
                  <Card className="bg-zinc-900 border-zinc-800">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-medium text-zinc-300">Saved signal outcomes (all positions)</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-zinc-800">
                            <th className="text-left py-2 text-zinc-500 font-normal text-xs">Signal</th>
                            <th className="text-right py-2 text-zinc-500 font-normal text-xs">Count</th>
                            <th className="text-right py-2 text-zinc-500 font-normal text-xs">Win Rate</th>
                            <th className="text-right py-2 text-zinc-500 font-normal text-xs">Avg Return</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(latestLearn.signal_accuracy)
                            .sort((a, b) => (SIGNAL_ORDER[a[0]] ?? 99) - (SIGNAL_ORDER[b[0]] ?? 99))
                            .map(([sig, acc]) => (
                            <tr key={sig} className="border-b border-zinc-800/50">
                              <td className="py-2"><SignalBadge signal={sig} /></td>
                              <td className="py-2 text-right text-zinc-400">{acc.count}</td>
                              <td className="py-2 text-right text-zinc-400">{acc.win_rate}%</td>
                              <td className="py-2 text-right"><ReturnCell v={acc.avg_return} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </CardContent>
                  </Card>
                )}
              </>
            ) : (
              <div className="text-zinc-500 text-sm py-8 text-center">
                No analysis yet. Click &ldquo;Run Analysis&rdquo; to analyze all {openPos.length} open positions now.
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
