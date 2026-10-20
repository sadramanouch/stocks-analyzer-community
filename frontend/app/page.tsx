"use client";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
import { apiFetch } from "@/lib/apiFetch";
import { fcfScore } from "@/lib/fcf-score";
const TradeEvidenceWorkspace=dynamic(()=>import("@/components/trade-evidence-workspace"));
import type { NumberReport } from "@/components/number-evidence";
import dynamic from "next/dynamic";
import {ResearchNote,WorkspaceNav} from "@/components/workspace-ui";
const NumberEvidence=dynamic(()=>import("@/components/number-evidence"));

import { useState, useRef, useEffect, useCallback } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from "recharts";
import {
  Search,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Minus,
  Brain,
  Activity,
  CheckCircle,
  XCircle,
  Circle,
  FileText,
  ExternalLink,
  BarChart2,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface FreshnessData {
  score: number;
  label: string;
  color: string;
  days_old: number;
  filing_date: string;
  warning: string;
}

interface AltmanZ {
  score: number | null;
  zone: string;
  color: string;
  components?: Record<string, number>;
}

interface PiotroskiF {
  score: number | null;
  verdict: string;
  color: string;
  breakdown?: Record<string, number>;
}

interface Sloan {
  ratio: number | null;
  quality: string;
  color: string;
}

interface RedFlag {
  severity: string;
  code: string;
  detail: string;
}

interface BeneishM {
  score: number;
  verdict: string;
  color: string;
  components: Record<string, number>;
}

interface ReverseDCF {
  implied_growth_rate: number;
  assessment: string;
  color: string;
  interpretation: string;
}

interface MagicFormula {
  earnings_yield: number;
  roic: number;
  verdict: string;
  color: string;
}

interface EPV {
  epv_total: number;
  epv_per_share: number | null;
  growth_premium_pct: number | null;
  verdict: string;
  color: string;
}

interface RoicWacc {
  roic: number;
  wacc: number;
  spread: number;
  verdict: string;
  color: string;
}

interface PegRatio {
  peg: number;
  verdict: string;
  color: string;
}

interface NetNet {
  nnwc: number;
  nnwc_per_share: number;
  vs_price_pct: number | null;
  verdict: string;
  color: string;
}

interface OwnerEarnings {
  owner_earnings: number;
  owner_earnings_per_share: number | null;
  owner_earnings_yield: number | null;
  vs_reported_fcf_pct: number;
  verdict: string;
  color: string;
}

interface DuPont {
  roe: number;
  net_margin: number;
  asset_turnover: number;
  equity_multiplier: number;
  dominant_driver: string;
  quality: string;
  color: string;
}

interface RuleOf40 {
  score: number;
  revenue_growth_pct: number;
  op_margin_pct: number;
  verdict: string;
  color: string;
}

interface StockData {
  ticker: string;
  company_name: string;
  sector: string;
  industry: string;
  employees: number | null;
  description: string;
  exchange: string;
  price: number | null;
  market_cap: number | null;
  enterprise_value: number | null;
  beta: number | null;
  high_52w: number | null;
  low_52w: number | null;
  avg_volume: number | null;
  pct_52w: number | null;
  short_pct_float: number | null;
  shares_outstanding: number | null;
  pe_ttm: number | null;
  pe_fwd: number | null;
  pb: number | null;
  ps: number | null;
  peg: number | null;
  ev_ebitda: number | null;
  ev_revenue: number | null;
  fcf_yield: number | null;
  dividend_yield: number | null;
  payout_ratio: number | null;
  gross_margin: number | null;
  op_margin: number | null;
  net_margin: number | null;
  roe: number | null;
  roa: number | null;
  rev_growth: number | null;
  eps_growth: number | null;
  eps: number | null;
  revenue: number | null;
  gross_profit: number | null;
  ebit: number | null;
  net_income: number | null;
  operating_cf: number | null;
  capex: number | null;
  fcf: number | null;
  da: number | null;
  cash: number | null;
  total_debt: number | null;
  net_debt: number | null;
  total_assets: number | null;
  total_equity: number | null;
  retained_earnings: number | null;
  goodwill: number | null;
  current_ratio: number | null;
  quick_ratio: number | null;
  de_ratio: number | null;
  bvps: number | null;
  target_price: number | null;
  analyst_count: number | null;
  analyst_rec: number | null;
  dcf_price: number | null;
  graham_number: number | null;
  avg_fair_value: number | null;
  margin_of_safety: number | null;
  altman_z: AltmanZ;
  piotroski_f: PiotroskiF;
  sloan: Sloan;
  red_flags: { total: number; flags: RedFlag[]; high: number; medium: number };
  revenue_trend: { quarter: string; revenue: number }[];
  ni_trend: { quarter: string; net_income: number }[];
  freshness: FreshnessData;
  financial_period_end?: string;
  financial_data_source?: string;
  market_data_fetched_at?: string;
  valuation_status?: string;
  number_evidence?: NumberReport;
  instrument_eligibility?: { status: string; reasons: string[] };
  valuation_scenario?: { status: string; reason?: string; cash_flow_basis?: string; discount_rate?: number; shares_basis?: string;
    limitations: string[]; sensitivity: { discount_rate: number; growth_rate: number; price: number }[] };
  financial_provenance?: Record<string, { concept?: string; filed?: string; end?: string }>;
  // Advanced valuation frameworks
  beneish_m: BeneishM | null;
  reverse_dcf: ReverseDCF | null;
  magic_formula: MagicFormula | null;
  epv: EPV | null;
  roic_wacc: RoicWacc | null;
  peg_ratio: PegRatio | null;
  net_net: NetNet | null;
  owner_earnings: OwnerEarnings | null;
  dupont: DuPont | null;
  rule_of_40: RuleOf40 | null;
}

// ─── EDGAR Types ──────────────────────────────────────────────────────────────

interface Filing {
  form_type: string;
  filed_date: string;
  period_of_report: string | null;
  accession_number: string;
  description: string;
  url: string;
}

interface FilingSummary {
  last_10k: { filed_date: string; period_of_report: string | null; url: string } | null;
  last_10q: { filed_date: string; period_of_report: string | null; url: string } | null;
  recent_8k_count: number;
  recent_form4_count: number;
}

interface FilingsData {
  ticker: string;
  cached: boolean;
  total: number;
  filings: Filing[];
  summary: FilingSummary | null;
  error?: string;
  notice?: string;
  stale?: boolean;
  cache_as_of?: string;
}

// ─── Filing Deep Analysis Types ───────────────────────────────────────────────

interface FilingDeepMeta {
  type: "meta";
  ticker: string;
  company_name: string;
  current_price: number | null;
  filings_analyzed: Array<{
    type: string;
    filed_date: string;
    period: string | null;
    url: string;
  }>;
}

interface FilingVerdict {
  signal: string;
  confidence: string;
  price_target_low: number;
  price_target_base: number;
  price_target_high: number;
  upside_pct: number;
  verdict: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return "N/A";
  return v.toFixed(decimals);
}

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "N/A";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  return `${sign}$${abs.toLocaleString()}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "N/A";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function scoreColor(color: string): string {
  if (color === "green") return "text-emerald-600 dark:text-emerald-400";
  if (color === "yellow") return "text-amber-500 dark:text-amber-400";
  if (color === "orange") return "text-orange-500 dark:text-orange-400";
  if (color === "red") return "text-red-600 dark:text-red-400";
  if (color === "blue") return "text-blue-600 dark:text-blue-400";
  return "text-muted-foreground";
}

function scoreBg(color: string): string {
  if (color === "green") return "bg-emerald-50 border-emerald-200 dark:bg-emerald-950 dark:border-emerald-800";
  if (color === "yellow") return "bg-amber-50 border-amber-200 dark:bg-amber-950 dark:border-amber-800";
  if (color === "orange") return "bg-orange-50 border-orange-200 dark:bg-orange-950 dark:border-orange-800";
  if (color === "red") return "bg-red-50 border-red-200 dark:bg-red-950 dark:border-red-800";
  if (color === "blue") return "bg-blue-50 border-blue-200 dark:bg-blue-950 dark:border-blue-800";
  return "bg-muted border-border";
}



function VerdictBadge({ verdict, color }: { verdict: string; color: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold border ${scoreBg(color)} ${scoreColor(color)}`}>
      {verdict}
    </span>
  );
}

function severityBg(severity: string): string {
  if (severity === "HIGH") return "bg-red-50 border-red-300 dark:bg-red-950 dark:border-red-800";
  if (severity === "MEDIUM") return "bg-amber-50 border-amber-300 dark:bg-amber-950 dark:border-amber-800";
  return "bg-muted border-border";
}

function severityBadge(severity: string) {
  if (severity === "HIGH") return <Badge className="bg-red-600 text-white text-[10px]">HIGH</Badge>;
  if (severity === "MEDIUM") return <Badge className="bg-amber-500 text-white text-[10px]">MED</Badge>;
  return <Badge variant="secondary" className="text-[10px]">LOW</Badge>;
}

function formTypeBadge(form: string) {
  const styles: Record<string, string> = {
    "10-K":    "bg-blue-700 text-white",
    "10-Q":    "bg-blue-400 text-white",
    "8-K":     "bg-amber-500 text-white",
    "4":       "bg-purple-600 text-white",
    "DEF 14A": "bg-slate-500 text-white",
  };
  return (
    <Badge className={`font-mono text-[10px] ${styles[form] ?? "bg-muted text-foreground"}`}>
      {form === "4" ? "Form 4" : form}
    </Badge>
  );
}

// ─── Signal Config ────────────────────────────────────────────────────────────

function signalConfig(signal: string) {
  switch (signal) {
    case "STRONG_BUY": return { label: "STRONG BUY", bg: "bg-emerald-700", border: "border-emerald-600", text: "text-emerald-600 dark:text-emerald-400", icon: TrendingUp };
    case "BUY":        return { label: "BUY",         bg: "bg-emerald-500", border: "border-emerald-400", text: "text-emerald-600 dark:text-emerald-400", icon: TrendingUp };
    case "HOLD":       return { label: "HOLD",        bg: "bg-amber-500",   border: "border-amber-400",   text: "text-amber-600 dark:text-amber-400",   icon: Minus };
    case "AVOID":      return { label: "AVOID",       bg: "bg-orange-500",  border: "border-orange-400",  text: "text-orange-600 dark:text-orange-400", icon: TrendingDown };
    case "STRONG_AVOID": return { label: "STRONG AVOID", bg: "bg-red-600", border: "border-red-500",   text: "text-red-600 dark:text-red-400",         icon: TrendingDown };
    default:           return { label: signal || "N/A", bg: "bg-slate-500", border: "border-slate-400", text: "text-muted-foreground",                  icon: Minus };
  }
}

// ─── Verdict Card ─────────────────────────────────────────────────────────────

function VerdictCard({ verdict, meta }: { verdict: FilingVerdict; meta: FilingDeepMeta }) {
  const cfg = signalConfig(verdict.signal);
  const Icon = cfg.icon;
  const isUp = verdict.upside_pct >= 0;
  const cardBg =
    verdict.signal === "STRONG_BUY" || verdict.signal === "BUY"
      ? "bg-emerald-50 dark:bg-emerald-950/60"
      : verdict.signal === "HOLD"
      ? "bg-amber-50 dark:bg-amber-950/60"
      : "bg-red-50 dark:bg-red-950/60";

  return (
    <div className={`rounded-xl border-2 ${cfg.border} ${cardBg} p-6`}>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div>
          <div className="flex items-center gap-2">
            <FileText className="size-5 text-muted-foreground" />
            <span className="font-bold text-lg">Filing Deep Analysis</span>
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">
            Based on actual EDGAR document content (10-K · 10-Q · 8-K)
          </p>
        </div>
        <Badge className={`${cfg.bg} text-white text-sm px-4 py-1.5 flex items-center gap-1.5`}>
          <Icon className="size-4" />
          {cfg.label}
        </Badge>
      </div>

      {/* Price comparison */}
      <div className="grid grid-cols-3 gap-4 mb-6 items-center">
        <div>
          <div className="text-xs text-muted-foreground uppercase tracking-wide mb-1">Current Price</div>
          <div className="text-3xl font-bold font-mono">
            {meta.current_price !== null ? `$${meta.current_price.toFixed(2)}` : "N/A"}
          </div>
          <div className="text-xs text-muted-foreground mt-1">market price</div>
        </div>

        <div className="flex flex-col items-center gap-1">
          <Icon className={`size-8 ${cfg.text}`} />
          <span className={`text-2xl font-bold ${cfg.text}`}>
            {isUp ? "+" : ""}{verdict.upside_pct.toFixed(1)}%
          </span>
          <span className="text-xs text-muted-foreground">
            {isUp ? "upside" : "downside"}
          </span>
        </div>

        <div className="text-right">
          <div className="text-xs text-muted-foreground uppercase tracking-wide mb-1">AI Price Target</div>
          <div className={`text-3xl font-bold font-mono ${cfg.text}`}>
            ${verdict.price_target_base.toFixed(2)}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            Confidence: <span className="font-semibold">{verdict.confidence}</span>
          </div>
        </div>
      </div>

      {/* Price range bar */}
      <div className="rounded-lg bg-zinc-900/70 border border-zinc-800 px-4 py-4 mb-4">
        <div className="text-xs text-muted-foreground mb-3 uppercase tracking-wide font-medium">
          Price Target Range
        </div>
        <div className="flex items-center gap-3">
          <div className="text-center min-w-[60px]">
            <div className="text-base font-semibold">${verdict.price_target_low.toFixed(0)}</div>
            <div className="text-xs text-muted-foreground">Bear</div>
          </div>

          <div className="flex-1 relative h-3 bg-muted rounded-full overflow-visible">
            <div className="absolute inset-0 rounded-full bg-gradient-to-r from-red-300 via-amber-300 to-emerald-400 opacity-60" />
            {/* Base target marker */}
            {verdict.price_target_high > verdict.price_target_low && (() => {
              const range = verdict.price_target_high - verdict.price_target_low;
              const basePct = Math.max(0, Math.min(100, ((verdict.price_target_base - verdict.price_target_low) / range) * 100));
              const currPct = meta.current_price !== null
                ? Math.max(0, Math.min(100, ((meta.current_price - verdict.price_target_low) / range) * 100))
                : null;
              return (
                <>
                  <div
                    className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 size-4 rounded-full bg-foreground border-2 border-background shadow-md z-10"
                    style={{ left: `${basePct}%` }}
                    title={`Base target: $${verdict.price_target_base}`}
                  />
                  {currPct !== null && (
                    <div
                      className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 size-3 rounded-full bg-blue-500 border-2 border-background shadow z-10"
                      style={{ left: `${currPct}%` }}
                      title={`Current: $${meta.current_price}`}
                    />
                  )}
                </>
              );
            })()}
          </div>

          <div className="text-center min-w-[60px]">
            <div className="text-base font-semibold">${verdict.price_target_high.toFixed(0)}</div>
            <div className="text-xs text-muted-foreground">Bull</div>
          </div>
        </div>
        <div className="flex justify-center gap-4 mt-3 text-xs text-muted-foreground">
          <div className="flex items-center gap-1">
            <div className="size-2.5 rounded-full bg-foreground" />
            Base target ${verdict.price_target_base.toFixed(0)}
          </div>
          <div className="flex items-center gap-1">
            <div className="size-2.5 rounded-full bg-blue-500" />
            Current ${meta.current_price?.toFixed(0) ?? "N/A"}
          </div>
        </div>
      </div>

      {/* Verdict one-liner */}
      {verdict.verdict && (
        <p className="text-sm text-muted-foreground italic border-t pt-3 mt-2">
          &ldquo;{verdict.verdict}&rdquo;
        </p>
      )}
    </div>
  );
}

// ─── Metric Row ───────────────────────────────────────────────────────────────

function RankPill({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return null;
  const color = pct >= 80 ? "bg-emerald-900 text-emerald-300" : pct >= 50 ? "bg-yellow-900 text-yellow-300" : "bg-red-900 text-red-300";
  const label = pct >= 80 ? `Top ${(100 - pct).toFixed(0)}%` : pct >= 50 ? `Top ${(100 - pct).toFixed(0)}%` : `Bot ${(100 - pct).toFixed(0)}%`;
  return <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium ${color}`}>{label}</span>;
}

function MetricRow({ label, value, sub, rankPct }: { label: string; value: string; sub?: string; rankPct?: number | null }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="text-right flex items-center justify-end">
        <span className="text-sm font-medium">{value}</span>
        {rankPct != null && <RankPill pct={rankPct} />}
        {sub && <span className="ml-1 text-xs text-muted-foreground">{sub}</span>}
      </div>
    </div>
  );
}

// ─── Piotroski Criteria ───────────────────────────────────────────────────────

const PIOTROSKI_META: Record<string, { label: string; category: string; what: string; passMsg: string; failMsg: string }> = {
  F1_positive_roa: {
    label: "Positive ROA",
    category: "Profitability",
    what: "Return on Assets — is the company earning profit relative to its asset base?",
    passMsg: "The company is profitable. It's generating positive net income relative to the assets it owns.",
    failMsg: "The company is losing money — net income is negative relative to its assets. A warning sign.",
  },
  F2_positive_ocf: {
    label: "Positive Operating Cash Flow",
    category: "Profitability",
    what: "Is the core business generating real cash (not just accounting profit)?",
    passMsg: "Operations are generating actual cash. This is more reliable than net income alone.",
    failMsg: "The business is burning cash from operations — it may depend on financing or asset sales to survive.",
  },
  F3_improving_roa: {
    label: "Improving ROA",
    category: "Profitability",
    what: "Is profitability improving year-over-year? Trend matters as much as the absolute level.",
    passMsg: "The company is becoming more profitable — ROA improved versus last year.",
    failMsg: "Profitability is declining year-over-year — the business is getting less efficient at using assets.",
  },
  F4_cash_quality: {
    label: "Cash Flow > Net Income",
    category: "Profitability",
    what: "Are profits backed by real cash? (Operating Cash Flow > Net Income). Detects earnings manipulation.",
    passMsg: "Cash flow exceeds reported profit — earnings are conservative and high quality.",
    failMsg: "Reported profits exceed cash collected — the gap may be accounting accruals or one-time items. Raises earnings quality concerns.",
  },
  F5_decreasing_leverage: {
    label: "Decreasing Leverage",
    category: "Leverage & Solvency",
    what: "Is the company taking on less debt relative to assets compared to last year?",
    passMsg: "Debt burden is shrinking — the company is deleveraging, reducing financial risk.",
    failMsg: "The company is taking on more debt relative to its assets — increasing financial risk.",
  },
  F6_improving_liquidity: {
    label: "Improving Liquidity",
    category: "Leverage & Solvency",
    what: "Is the current ratio (current assets / current liabilities) improving? Can it pay short-term bills?",
    passMsg: "The company's ability to cover short-term obligations has improved.",
    failMsg: "Short-term liquidity is weakening — the company may struggle to meet near-term obligations.",
  },
  F7_no_dilution: {
    label: "No Share Dilution",
    category: "Leverage & Solvency",
    what: "Did the company issue new shares? New share issuance dilutes existing investors.",
    passMsg: "No new shares issued — existing shareholders weren't diluted.",
    failMsg: "New shares were issued, diluting existing investors. Often done to raise cash when internal cash is insufficient.",
  },
  F8_improving_gross_margin: {
    label: "Improving Gross Margin",
    category: "Operating Efficiency",
    what: "Is the company keeping more of each dollar of revenue after direct costs? Margin expansion is a sign of pricing power.",
    passMsg: "Gross margin improved — the company is either raising prices or cutting costs more efficiently.",
    failMsg: "Gross margin contracted — the company is earning less per dollar of revenue, often due to rising costs or pricing pressure.",
  },
  F9_improving_asset_turnover: {
    label: "Improving Asset Turnover",
    category: "Operating Efficiency",
    what: "Is the company generating more revenue from the same asset base? Measures operational productivity.",
    passMsg: "Asset turnover improved — the company is using its assets more productively to generate sales.",
    failMsg: "Asset turnover declined — the company is generating less revenue per dollar of assets, suggesting operational inefficiency.",
  },
};

const PIOTROSKI_CATEGORIES = ["Profitability", "Leverage & Solvency", "Operating Efficiency"];

function PiotroskiCriteria({ breakdown }: { breakdown: Record<string, number> }) {
  return (
    <div className="space-y-5">
      {PIOTROSKI_CATEGORIES.map(cat => {
        const signals = Object.entries(PIOTROSKI_META).filter(([, m]) => m.category === cat);
        const passed = signals.filter(([k]) => breakdown[k] === 1).length;
        return (
          <div key={cat}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-bold uppercase tracking-widest text-zinc-500">{cat}</span>
              <span className="text-xs text-zinc-600">{passed}/{signals.length} passed</span>
            </div>
            <div className="space-y-2">
              {signals.map(([key, meta]) => {
                const pass = breakdown[key] === 1;
                return (
                  <div
                    key={key}
                    className={`rounded-xl border px-4 py-3 ${
                      pass
                        ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950"
                        : "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950"
                    }`}
                  >
                    <div className="flex items-start gap-2.5">
                      {pass
                        ? <CheckCircle className="size-4 shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400" />
                        : <XCircle    className="size-4 shrink-0 mt-0.5 text-red-600 dark:text-red-400" />
                      }
                      <div className="flex-1 min-w-0">
                        <div className={`font-semibold text-sm ${pass ? "text-emerald-800 dark:text-emerald-200" : "text-red-800 dark:text-red-200"}`}>
                          {meta.label}
                        </div>
                        <div className="text-xs text-muted-foreground mt-0.5 italic">{meta.what}</div>
                        <div className={`text-xs mt-1 ${pass ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"}`}>
                          {pass ? meta.passMsg : meta.failMsg}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Dashboard Types ──────────────────────────────────────────────────────────

  async function fetchDashboard() {
    for (let attempt = 0; attempt < 3; attempt++) {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 12000);
      try {
        const r = await apiFetch(`${API}/api/dashboard`, { signal: ctrl.signal });
        clearTimeout(t);
        if (r.ok) {
          return r.json();
        }
      } catch {
        clearTimeout(t);
        if (attempt < 2) await new Promise(res => setTimeout(res, 2000));
      }
    }
    return null;
  }

interface DashboardRow {
  instrument_eligibility?: { status: string; reasons: string[] };
  ticker: string;
  company_name: string;
  sector: string | null;
  industry: string | null;
  current_price: number | null;
  piotroski_score: number | null;
  signal: string;
  confidence: number | null;
  price_target_low: number | null;
  price_target_base: number | null;
  price_target_high: number | null;
  upside_pct: number | null;
  verdict_text: string | null;
  filings_count: number;
  filings_detail: Array<{ type: string; filed_date: string; period: string | null; url: string }>;
  analyzed_at: string;
  altman_z_score: number | null;
  sloan_accrual: number | null;
  red_flags_count: number | null;
  red_flags_high: number | null;
  // Advanced frameworks
  beneish_score: number | null;
  reverse_dcf_growth: number | null;
  magic_ey: number | null;
  roic_spread: number | null;
  peg_ratio_val: number | null;
  owner_earnings_yield: number | null;
  rule_of_40_score: number | null;
  fcf_yield: number | null;
  // Screener fields
  quant_score: number | null;
  data_source: string | null;  // 'ai' | 'xbrl' | 'yfinance' | null
}

interface DashboardData {
  preview_label?: string | null;
  companies: DashboardRow[];
  total: number;
  industries: Record<string, number>;
  signals: Record<string, number>;
}

interface ScreenStatus {
  running: boolean;
  total: number;
  done: number;
  failed: number;
  current_ticker: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancelled: boolean;
  last_error: string | null;
}

// ─── Industry Colors ─────────────────────────────────────────────────────────

const INDUSTRY_COLORS: Record<string, string> = {
  "Technology":               "#6366f1",
  "Software":                 "#8b5cf6",
  "Semiconductors":           "#a855f7",
  "Internet":                 "#3b82f6",
  "Hardware":                 "#0ea5e9",
  "Financial Services":       "#f59e0b",
  "Banking":                  "#d97706",
  "Insurance":                "#f97316",
  "Healthcare":               "#10b981",
  "Biotechnology":            "#059669",
  "Pharmaceuticals":          "#14b8a6",
  "Consumer Defensive":       "#84cc16",
  "Consumer Cyclical":        "#22c55e",
  "Retail":                   "#16a34a",
  "Industrials":              "#64748b",
  "Aerospace & Defense":      "#475569",
  "Energy":                   "#ef4444",
  "Oil & Gas":                "#dc2626",
  "Utilities":                "#94a3b8",
  "Real Estate":              "#f472b6",
  "Communication Services":  "#ec4899",
  "Media":                    "#db2777",
};
function industryColor(ind: string | null): string {
  if (!ind) return "#6b7280";
  for (const [k, v] of Object.entries(INDUSTRY_COLORS)) {
    if (ind.toLowerCase().includes(k.toLowerCase())) return v;
  }
  const hash = Array.from(ind).reduce((a, c) => a + c.charCodeAt(0), 0);
  const palette = ["#6366f1","#8b5cf6","#3b82f6","#10b981","#f59e0b","#ef4444","#ec4899","#14b8a6"];
  return palette[hash % palette.length];
}

function computeUpside(row: DashboardRow): number {
  if (row.price_target_base !== null && row.current_price !== null)
    return ((row.price_target_base - row.current_price) / row.current_price) * 100;
  return row.upside_pct ?? 0;
}

// ─── Conviction Score — 5-Dimension Framework (0–100 pts) ───────────────────
//
// Dimension 1 — VALUATION (0–30 pts)
//   What are you paying vs what the business is worth?
//   a) AI Upside (0–15): Credible upside to AI price target. Extreme (>100%) discounted
//      — AI models frequently overshoot for cheap/distressed stocks.
//   b) PEG Ratio (0–8): P/E ÷ growth rate (Peter Lynch). <1 = cheap relative to growth.
//      Compares actual EPS growth rate against what the market charges per unit of growth.
//   c) Reverse DCF (0–7): What FCF growth rate is already baked into today's price?
//      Low implied growth (<10%/yr) = low expectations = easier to beat = more upside.
//
// Dimension 2 — FINANCIAL QUALITY (0–25 pts)
//   Are the reported numbers trustworthy and improving?
//   a) Piotroski F-Score (0–13): 9 binary signals across profitability (ROA trend, OCF
//      coverage, accruals), leverage (debt reduction, liquidity, dilution), and efficiency
//      (gross margin trend, asset turnover). Score of 8–9 = improving on almost every axis.
//   b) Sloan Accrual Ratio (0–8): Net income minus operating cash flow divided by assets.
//      Negative = cash flow exceeds profits (real earnings). >10% = profits are accounting
//      entries, not cash — historically predicts underperformance.
//   c) Owner Earnings Yield (0–4): Buffett's true earnings = Net income + D&A − CapEx,
//      divided by market cap. Measures actual cash returned per dollar invested.
//
// Dimension 3 — VALUE CREATION (0–20 pts)
//   Is management turning capital into more capital?
//   a) ROIC vs WACC Spread (0–12): Return on Invested Capital minus the Weighted Average
//      Cost of Capital. Positive spread = company earns more than its capital costs.
//      Every percentage point above zero creates permanent shareholder value.
//   b) Magic Formula EY (0–8): Greenblatt's Earnings Yield = EBIT ÷ Enterprise Value.
//      Pure measure of operating cheapness — what you earn on the total price paid.
//
// Dimension 4 — RISK & INTEGRITY (0–15 pts)
//   What could destroy or overstate value?
//   a) Altman Z-Score (0–9): Five-ratio bankruptcy model (working capital, retained earnings,
//      EBIT, market cap vs debt, revenue vs assets). >3.0 = Safe Zone. <1.81 = Distress.
//   b) Beneish M-Score (0–6): Eight-ratio earnings manipulation detector (receivables,
//      margins, asset quality, sales growth, depreciation, SG&A, leverage, accruals).
//      < −2.22 = non-manipulator; > −1.78 = probable earnings manipulation.
//
// Dimension 5 — GROWTH EFFICIENCY (0–10 pts)
//   Is the business growing in a sustainable, profitable way?
//   Rule of 40 (0–10): Revenue growth % + Operating margin %. A score ≥40 means
//   the business is efficiently monetising its growth. Benchmark for SaaS/tech but
//   informative across all high-growth companies.
//
// PENALTIES (deducted after scoring):
//   High-severity red flags: −4 pts each (max −15). These are specific accounting
//   anomalies, debt traps, or structural risks flagged by the analysis engine.
//   Beneish manipulation confirmed (> −1.78): additional −5 pts.
//   Altman Distress Zone (< 1.81): additional −3 pts.

interface ConvictionDim { pts: number; max: number; drivers: string[] }
interface ConvictionBreakdown {
  total: number;
  valuation:      ConvictionDim;
  quality:        ConvictionDim;
  value_creation: ConvictionDim;
  risk:           ConvictionDim;
  growth:         ConvictionDim;
  penalties:      number;
  penalty_drivers: string[];
}

function convictionBreakdown(row: DashboardRow): ConvictionBreakdown {
  const upside = computeUpside(row);

  // ── 1. VALUATION (0–30) ──────────────────────────────────────────────────
  // a) FCF Yield (0–8): independent market-based signal — the market sets price, we observe yield
  const { value: fcfYield, points: fcfYPts } = fcfScore(row.fcf_yield);

  // b) Upside to avg_fair_value (0–7): model-derived, reduced weight
  let upPts: number;
  if      (upside >= 100) upPts = 4;   // extreme → skepticism cap
  else if (upside >=  60) upPts = 7;   // sweet spot
  else if (upside >=  35) upPts = 5;
  else if (upside >=  20) upPts = 4;
  else if (upside >=  10) upPts = 3;
  else if (upside >=   0) upPts = 2;
  else if (upside >= -15) upPts = 1;
  else                    upPts = 0;

  // c) PEG ratio (0–8): independent — earnings growth vs market PE
  const peg = row.peg_ratio_val;
  let pegPts = 4;
  if (peg !== null) {
    if      (peg > 0 && peg < 0.75) pegPts = 8;
    else if (peg < 1.0)              pegPts = 6;
    else if (peg < 1.5)              pegPts = 4;
    else if (peg < 2.5)              pegPts = 2;
    else                             pegPts = 0;
  }

  // d) Reverse DCF (0–7): independent — market-implied growth rate, not our model
  const rdcf = row.reverse_dcf_growth;
  let rdcfPts = 3;
  if (rdcf !== null) {
    if      (rdcf < -15) rdcfPts = 3;   // extreme negative — suspicious
    else if (rdcf <   0) rdcfPts = 6;   // priced for mild decline — cheap but not max
    else if (rdcf <   5) rdcfPts = 7;   // very low growth priced in — best signal
    else if (rdcf <  10) rdcfPts = 5;
    else if (rdcf <  20) rdcfPts = 3;
    else if (rdcf <  35) rdcfPts = 1;
    else                 rdcfPts = 0;   // priced for perfection
  }

  const valPts = fcfYPts + upPts + pegPts + rdcfPts;

  // ── 2. FINANCIAL QUALITY (0–25) ──────────────────────────────────────────
  // a) Piotroski F-Score (0–13): null reduced — missing data should not imply quality
  const f = row.piotroski_score;
  let fioPts: number;
  if      (f === null) fioPts = 5;   // was 7
  else if (f >= 8)     fioPts = 13;
  else if (f >= 6)     fioPts = 10;
  else if (f >= 4)     fioPts = 6;
  else if (f >= 2)     fioPts = 2;
  else                 fioPts = 0;

  // b) Sloan accrual ratio (0–8)
  const sloan = row.sloan_accrual;
  let sloanPts: number;
  if      (sloan === null) sloanPts = 4;
  else if (sloan < -10)    sloanPts = 8;
  else if (sloan <   0)    sloanPts = 6;
  else if (sloan <   5)    sloanPts = 3;
  else if (sloan <  10)    sloanPts = 1;
  else                     sloanPts = 0;

  // c) Owner Earnings Yield (0–4)
  const oeYield = row.owner_earnings_yield;
  let oePts = 2;
  if (oeYield !== null) {
    if      (oeYield > 8)  oePts = 4;
    else if (oeYield > 5)  oePts = 3;
    else if (oeYield > 2)  oePts = 2;
    else if (oeYield > 0)  oePts = 1;
    else                   oePts = 0;
  }

  const qualPts = fioPts + sloanPts + oePts;

  // ── 3. VALUE CREATION (0–20) ─────────────────────────────────────────────
  // a) ROIC vs WACC spread (0–12)
  // Raised bar for max: >15% was too easy for capital-light businesses
  // null reduced: missing data should not assume strong value creation
  // Cap at 100: above this is almost always a capital structure artifact (negative WC)
  const spread = row.roic_spread !== null ? Math.min(row.roic_spread, 100) : null;
  let spreadPts: number;
  if      (spread === null) spreadPts = 4;   // was 6
  else if (spread >  30)    spreadPts = 12;  // new top tier
  else if (spread >  15)    spreadPts = 9;   // was 12
  else if (spread >  10)    spreadPts = 7;   // was 10
  else if (spread >   5)    spreadPts = 5;   // was 7
  else if (spread >   0)    spreadPts = 3;   // was 4
  else if (spread >  -5)    spreadPts = 1;
  else                      spreadPts = 0;

  // b) Magic Formula Earnings Yield (0–8)
  const magicEY = row.magic_ey;
  let magicPts = 4;
  if (magicEY !== null) {
    if      (magicEY > 10) magicPts = 8;
    else if (magicEY >  7) magicPts = 6;
    else if (magicEY >  4) magicPts = 4;
    else if (magicEY >  2) magicPts = 2;
    else                   magicPts = 0;
  }

  const vcPts = spreadPts + magicPts;

  // ── 4. RISK & INTEGRITY (0–15) ───────────────────────────────────────────
  // a) Altman Z-Score (0–9)
  const az = row.altman_z_score;
  let azPts: number;
  if      (az === null) azPts = 5;
  else if (az > 3.0)    azPts = 9;
  else if (az > 2.5)    azPts = 7;
  else if (az > 1.81)   azPts = 4;
  else if (az > 1.5)    azPts = 1;
  else                  azPts = 0;

  // b) Beneish M-Score (0–6)
  const bm = row.beneish_score;
  let benPts: number;
  if      (bm === null)  benPts = 3;
  else if (bm < -2.22)   benPts = 6;
  else if (bm < -1.78)   benPts = 3;
  else                   benPts = 0;

  const riskPts = azPts + benPts;

  // ── 5. GROWTH EFFICIENCY (0–10) ──────────────────────────────────────────
  // R40 null reduced: missing growth data should not assume average efficiency
  const r40 = row.rule_of_40_score;
  let r40Pts: number;
  if      (r40 === null) r40Pts = 3;   // was 5
  else if (r40 >= 60)    r40Pts = 10;
  else if (r40 >= 40)    r40Pts = 8;
  else if (r40 >= 20)    r40Pts = 4;
  else                   r40Pts = 0;

  // ── PENALTIES ────────────────────────────────────────────────────────────
  const rfHigh = row.red_flags_high ?? 0;
  const overvalPen = upside < -50 ? 15 : upside < -35 ? 8 : upside < -20 ? 3 : 0;
  const penalties = Math.min(15, rfHigh * 4)
    + (bm   !== null && bm   > -1.78 ? 5 : 0)   // confirmed manipulation
    + (az   !== null && az   < 1.81  ? 3 : 0)    // distress zone
    + (rdcf !== null && rdcf < -15   ? 3 : 0)    // extreme negative rDCF
    + overvalPen;                                 // price significantly above fair value

  const total = Math.min(99, Math.max(1,
    valPts + qualPts + vcPts + riskPts + r40Pts - penalties
  ));

  return {
    total,
    valuation:      { pts: valPts,   max: 30, drivers: [
      fcfYield !== null ? `FCF Yield ${(fcfYield * 100).toFixed(1)}% → ${fcfYPts}/8` : `FCF Yield n/a → ${fcfYPts}/8`,
      `Upside ${upside >= 0 ? "+" : ""}${upside.toFixed(1)}% → ${upPts}/7`,
      peg      !== null ? `PEG ${peg.toFixed(2)}× → ${pegPts}/8`           : `PEG n/a → ${pegPts}/8`,
      rdcf     !== null ? `Rev-DCF ${rdcf.toFixed(1)}%/yr → ${rdcfPts}/7`  : `Rev-DCF n/a → ${rdcfPts}/7`,
    ]},
    quality:        { pts: qualPts,  max: 25, drivers: [
      f        !== null ? `Piotroski ${f}/9 → ${fioPts}/13`            : `Piotroski n/a → ${fioPts}/13`,
      sloan    !== null ? `Sloan ${sloan.toFixed(1)}% → ${sloanPts}/8` : `Sloan n/a → ${sloanPts}/8`,
      oeYield  !== null ? `OE Yield ${oeYield.toFixed(1)}% → ${oePts}/4` : `OE Yield n/a → ${oePts}/4`,
    ]},
    value_creation: { pts: vcPts,    max: 20, drivers: [
      spread   !== null ? `ROIC−WACC ${spread > 0 ? "+" : ""}${spread.toFixed(1)}% → ${spreadPts}/12` : `ROIC−WACC n/a → ${spreadPts}/12`,
      magicEY  !== null ? `Magic EY ${magicEY.toFixed(1)}% → ${magicPts}/8` : `Magic EY n/a → ${magicPts}/8`,
    ]},
    risk:           { pts: riskPts,  max: 15, drivers: [
      az       !== null ? `Altman Z ${az.toFixed(2)} → ${azPts}/9`    : `Altman Z n/a → ${azPts}/9`,
      bm       !== null ? `Beneish M ${bm.toFixed(2)} → ${benPts}/6`  : `Beneish n/a → ${benPts}/6`,
    ]},
    growth:         { pts: r40Pts,   max: 10, drivers: [
      r40      !== null ? `Rule of 40: ${r40.toFixed(0)} → ${r40Pts}/10` : `Rule of 40 n/a → ${r40Pts}/10`,
    ]},
    penalties,
    penalty_drivers: [
      ...(overvalPen > 0 ? [`Overvalued ${upside.toFixed(1)}% vs fair value → −${overvalPen}`] : []),
      ...(rfHigh > 0 ? [`${rfHigh} high-severity red flag${rfHigh > 1 ? "s" : ""} → −${Math.min(15, rfHigh * 4)}`] : []),
      ...(bm !== null && bm > -1.78 ? [`Beneish M ${bm.toFixed(2)} (manipulation risk) → −5`] : []),
      ...(az !== null && az < 1.81  ? [`Altman Z ${az.toFixed(2)} (distress zone) → −3`] : []),
      ...(rdcf !== null && rdcf < -15 ? [`Rev-DCF ${rdcf.toFixed(1)}%/yr (extreme) → −3`] : []),
    ],
  };
}

function convictionPct(row: DashboardRow): number {
  return convictionBreakdown(row).total;
}

function convictionLabel(pct: number): string {
  if (pct >= 70) return "Strong Buy";
  if (pct >= 53) return "Buy";
  if (pct >= 38) return "Hold";
  if (pct >= 22) return "Sell";
  return "Avoid";
}

function convictionColor(pct: number): string {
  if (pct >= 70) return "#22c55e";
  if (pct >= 53) return "#86efac";
  if (pct >= 38) return "#f59e0b";
  if (pct >= 22) return "#f97316";
  return "#ef4444";
}

// Keep rankScore for sort ordering
function rankScore(row: DashboardRow): number {
  return convictionPct(row);
}

// ─── Dashboard Component ──────────────────────────────────────────────────────

type SortKey = "rank" | "upside_pct" | "current_price" | "price_target_base" | "piotroski_score" | "roic_spread" | "peg_ratio_val" | "rule_of_40_score";

// ─── ScreenPanel ──────────────────────────────────────────────────────────────

function ScreenPanel({
  status,
  onStart,
  onCancel,
  onClose,
  starting,
}: {
  status: ScreenStatus | null;
  onStart: () => void;
  onCancel: () => void;
  onClose: () => void;
  starting: boolean;
}) {
  const isRunning = status?.running ?? false;
  const isFinished = !isRunning && !!status?.finished_at;
  const done = status?.done ?? 0;
  const failed = status?.failed ?? 0;
  const total = status?.total ?? 0;
  const pct = total > 0 ? Math.round(((done + failed) / total) * 100) : 0;

  return (
    <div className="px-5 py-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart2 className="size-4 text-emerald-400" />
          <span className="font-bold text-sm text-zinc-200">Run Full Screen (EDGAR XBRL)</span>
          {isRunning && (
            <span className="text-[10px] font-bold tracking-widest uppercase rounded px-1.5 py-0.5 bg-emerald-900/60 text-emerald-400 border border-emerald-700/50">
              Running
            </span>
          )}
          {isFinished && (
            <span className="text-[10px] font-bold tracking-widest uppercase rounded px-1.5 py-0.5 bg-zinc-800 text-zinc-400 border border-zinc-700">
              Done
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-zinc-600 hover:text-zinc-400 transition-colors text-lg leading-none">✕</button>
      </div>

      {!isRunning && !isFinished && (
        <p className="text-xs text-zinc-500">
          Scores 1,000+ companies from their actual SEC EDGAR filings — no AI required.
          Runs in ~2–10 min. Results replace Yahoo Finance data with authoritative filed numbers.
        </p>
      )}

      {(isRunning || isFinished) && total > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>
              <span className="text-emerald-400 font-bold font-mono">{done}</span> scored
              {failed > 0 && <span className="text-zinc-600 font-mono"> · {failed} failed</span>}
              {" / "}<span className="font-mono">{total}</span> total
            </span>
            <span className="font-mono font-bold">{pct}%</span>
          </div>
          <div className="w-full h-2 rounded-full bg-zinc-800 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${pct}%`,
                background: isFinished ? "#22c55e" : "linear-gradient(90deg,#10b981,#34d399)",
              }}
            />
          </div>
          {isRunning && status?.current_ticker && (
            <p className="text-[11px] text-zinc-500 font-mono">
              Analyzing <span className="text-zinc-300 font-bold">{status.current_ticker}</span>…
            </p>
          )}
          {status?.last_error && (
            <p className="text-[11px] text-red-400/70 font-mono truncate" title={status.last_error}>
              Last error: {status.last_error}
            </p>
          )}
          {isFinished && (
            <p className="text-[11px] text-emerald-500">
              Screen complete — dashboard updated with XBRL scores.
            </p>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        {!isRunning && (
          <button
            onClick={onStart}
            disabled={starting}
            className="flex items-center gap-1.5 rounded-lg border border-emerald-700 bg-emerald-950/50 px-4 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-900/50 transition-colors disabled:opacity-50"
          >
            <BarChart2 className="size-3" />
            {starting ? "Starting…" : isFinished ? "Re-run Screen" : "Start Screen"}
          </button>
        )}
        {isRunning && (
          <button
            onClick={onCancel}
            className="flex items-center gap-1.5 rounded-lg border border-red-800 bg-red-950/40 px-4 py-1.5 text-xs font-semibold text-red-400 hover:bg-red-900/40 transition-colors"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

function Dashboard({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [sigFilter, setSigFilter] = useState<string | null>(null);
  const [indFilter, setIndFilter] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [showIndDropdown, setShowIndDropdown] = useState(false);
  const [includeExcluded, setIncludeExcluded] = useState(false);
  const [companySearch, setCompanySearch] = useState("");
  const [tableView,setTableView]=useState("simple");
  const [pageState, setPageState] = useState({ key: "", index: 0 });
  const indDropdownRef = useRef<HTMLDivElement | null>(null);

  const [showScreen, setShowScreen] = useState(false);
  const [screenStatus, setScreenStatus] = useState<ScreenStatus | null>(null);
  const [screenStarting, setScreenStarting] = useState(false);
  const screenPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const screenWasRunningRef = useRef(false);

  const load = useCallback(() => fetchDashboard().then(result => { if (result) setData(result); }).finally(() => setLoading(false)), []);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    async function fetchScreenStatus() {
      try {
        const r = await apiFetch(`${API}/api/screen/status`);
        if (r.ok) {
          const s: ScreenStatus = await r.json();
          setScreenStatus(s);
          if (s.running) setShowScreen(true);
          if (screenWasRunningRef.current && !s.running && s.finished_at) load();
          screenWasRunningRef.current = s.running;
        }
      } catch { /* backend not running */ }
    }
    fetchScreenStatus();
    screenPollRef.current = setInterval(fetchScreenStatus, 2500);
    return () => { if (screenPollRef.current) clearInterval(screenPollRef.current); };
  }, [load]);

  async function startScreen() {
    setScreenStarting(true);
    try {
      const r = await apiFetch(`${API}/api/screen/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tickers: [] }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        alert(b.detail || "Failed to start screen");
        return;
      }
      const r2 = await apiFetch(`${API}/api/screen/status`);
      if (r2.ok) setScreenStatus(await r2.json());
      setShowScreen(true);
    } finally {
      setScreenStarting(false);
    }
  }

  async function cancelScreen() {
    await apiFetch(`${API}/api/screen/run`, { method: "DELETE" });
    const r = await apiFetch(`${API}/api/screen/status`);
    if (r.ok) setScreenStatus(await r.json());
  }

  async function handleDelete(ticker: string) {
    setDeleting(ticker);
    await apiFetch(`${API}/api/dashboard/${ticker}`, { method: "DELETE" });
    await load();
    setDeleting(null);
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(d => (d === 1 ? -1 : 1) as 1 | -1);
    else { setSortKey(key); setSortDir(1); }
  }

  const SIG_ORDER = ["STRONG_BUY", "BUY", "HOLD", "AVOID", "STRONG_AVOID"];

  const rows = (data?.companies ?? [])
    .filter(row => includeExcluded || row.instrument_eligibility?.status !== "excluded")
    .map(r => ({ ...r, _rank: rankScore(r) }))
    .filter(r => {
      if (companySearch && !`${r.ticker} ${r.company_name}`.toLowerCase().includes(companySearch.toLowerCase())) return false;
      if (sigFilter && r.signal !== sigFilter) return false;
      if (indFilter && r.industry !== indFilter) return false;
      return true;
    })
    .sort((a, b) => {
      let av: number, bv: number;
      if (sortKey === "rank") { av = a._rank; bv = b._rank; }
      else { av = (a[sortKey] as number | null) ?? -Infinity; bv = (b[sortKey] as number | null) ?? -Infinity; }
      return sortDir * (bv - av);
    });

  const pageKey = JSON.stringify([sigFilter, indFilter, sortKey, sortDir, companySearch, includeExcluded]);
  const pageCount = Math.max(1, Math.ceil(rows.length / 50));
  const pageIndex = pageState.key === pageKey ? Math.min(pageState.index, pageCount - 1) : 0;
  const visibleRows = rows.slice(pageIndex * 50, (pageIndex + 1) * 50);

  function renderSortHeader(label: string, k: SortKey, right = false) {
    const active = sortKey === k;
    return (
      <th
        className={`px-3 py-3 ${right ? "text-right" : "text-left"} font-bold text-[11px] tracking-widest uppercase cursor-pointer select-none whitespace-nowrap transition-colors ${active ? "text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}
        onClick={() => toggleSort(k)}
      >
        {label}
        <span className="ml-1 opacity-60">{active ? (sortDir === -1 ? "↓" : "↑") : "↕"}</span>
      </th>
    );
  }

  if (loading && !data) return (
    <div className="rounded-2xl bg-zinc-950 p-6 space-y-4">
      {[1,2,3,4,5].map(i => (
        <div key={i} className="h-14 rounded-lg bg-zinc-800/60 animate-pulse" />
      ))}
      <p className="text-center text-xs text-zinc-500 pt-1">Loading rankings… this takes a few seconds on first visit</p>
    </div>
  );

  if (!data) return (
    <div className="rounded-2xl bg-zinc-950 border border-zinc-800 flex flex-col items-center gap-4 py-16 text-center px-8">
      <p className="text-zinc-400 text-sm">Could not load data. The server may be starting up.</p>
      <button onClick={load} className="rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-600 px-4 py-2 text-sm text-zinc-200 transition-colors">
        Retry
      </button>
    </div>
  );

  if (data.total === 0) return (
    <div className="mt-4 rounded-xl border border-zinc-800 overflow-hidden">
      {screenStatus?.running && (
        <div className="bg-emerald-950/10">
          <ScreenPanel status={screenStatus} onStart={startScreen} onCancel={cancelScreen} starting={screenStarting} onClose={() => {}} />
        </div>
      )}
      <div className="flex flex-col items-center gap-6 py-20 text-center px-8">
        <BarChart2 className="size-14 text-zinc-700" />
        <div>
          <p className="font-bold text-base text-zinc-200">No analyses yet</p>
          <p className="text-sm mt-1 text-zinc-500 max-w-sm">
            Run the XBRL screen to rank 1,000+ companies from their SEC filings in minutes, or search a single ticker above.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          {["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL"].map(t => (
            <button
              key={t}
              onClick={() => onSelectTicker(t)}
              className="rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 px-4 py-2 text-sm font-mono font-bold text-zinc-200 transition-colors"
            >
              {t}
            </button>
          ))}
        </div>
        {!screenStatus?.running && (
          <button
            onClick={startScreen}
            disabled={screenStarting}
            className="flex items-center gap-2 rounded-xl border border-emerald-700 bg-emerald-950/60 px-5 py-3 text-sm font-semibold text-emerald-300 hover:bg-emerald-900/60 transition-colors disabled:opacity-50"
          >
            <BarChart2 className="size-4" />
            {screenStarting ? "Starting…" : "Run Screen (EDGAR XBRL)"}
          </button>
        )}
      </div>
    </div>
  );

  return (
    <>
      <ResearchNote><p>Scores, price targets and buy/sell labels are estimates, not verified returns. <Link href="/research" className="underline">View financial sources</Link> before relying on them.</p></ResearchNote>
      {data.preview_label && <div role="status" className="my-4 rounded-lg border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-200">{data.preview_label}</div>}
      {/* ── Top bar — sticky below header ── */}
      <div className="sticky top-0 md:top-[60px] z-30 flex flex-wrap items-center gap-3 px-6 py-3 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur-sm -mx-6">
        <input aria-label="Search saved companies" placeholder="Find saved company…" value={companySearch} onChange={e => setCompanySearch(e.target.value)} className="w-44 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100" />
        <label className="text-xs text-amber-300"><input type="checkbox" checked={includeExcluded} onChange={event=>setIncludeExcluded(event.target.checked)} /> Include excluded instruments (research only)</label>
        {/* Signal filters */}
        {SIG_ORDER.filter(s => (data.signals[s] ?? 0) > 0).map(sig => {
          const cfg = signalConfig(sig);
          const active = sigFilter === sig;
          return (
            <button
              key={sig}
              onClick={() => setSigFilter(active ? null : sig)}
              className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[11px] font-bold tracking-wide transition-all ${
                active
                  ? `${cfg.bg} text-white border-transparent`
                  : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
              }`}
            >
              {cfg.label}
              <span className={`rounded px-1 text-[10px] ${active ? "bg-white/20" : "bg-zinc-800 text-zinc-500"}`}>
                {data.signals[sig]}
              </span>
            </button>
          );
        })}

        {/* Industry filter — dropdown */}
        <div className="relative" ref={indDropdownRef}>
          <button
            onClick={() => setShowIndDropdown(v => !v)}
            style={indFilter ? { borderColor: industryColor(indFilter), color: industryColor(indFilter) } : {}}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[11px] font-semibold transition-all ${
              indFilter
                ? "border-current bg-current/10"
                : showIndDropdown
                ? "border-zinc-500 text-zinc-200 bg-zinc-800"
                : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
            }`}
          >
            {indFilter ? (
              <>
                <span className="size-2 rounded-full shrink-0" style={{ background: industryColor(indFilter) }} />
                {indFilter}
              </>
            ) : (
              <>
                <span className="text-zinc-500">⬡</span>
                Industry
              </>
            )}
            <span className="ml-1 opacity-60">{showIndDropdown ? "▲" : "▼"}</span>
            {indFilter && (
              <span
                className="ml-1 rounded-full size-3.5 flex items-center justify-center bg-current/20 text-[9px] font-bold hover:bg-current/40"
                onClick={e => { e.stopPropagation(); setIndFilter(null); }}
              >✕</span>
            )}
          </button>

          {showIndDropdown && (
            <div
              className="absolute top-full left-0 mt-1.5 z-50 w-72 rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl overflow-hidden"
              onMouseLeave={() => setShowIndDropdown(false)}
            >
              <div className="px-3 py-2 border-b border-zinc-800 text-[10px] font-bold tracking-widest text-zinc-500 uppercase">
                Filter by Industry
              </div>
              <div className="max-h-72 overflow-y-auto p-1.5 flex flex-col gap-0.5">
                {Object.entries(data.industries).sort(([,a],[,b]) => b - a).map(([ind, cnt]) => {
                  const col = industryColor(ind);
                  const active = indFilter === ind;
                  return (
                    <button
                      key={ind}
                      onClick={() => { setIndFilter(active ? null : ind); setShowIndDropdown(false); }}
                      className={`flex items-center gap-2 w-full rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-left transition-colors ${
                        active ? "bg-zinc-700 text-zinc-100" : "hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                      }`}
                    >
                      <span className="size-2 rounded-full shrink-0" style={{ background: col }} />
                      <span className="flex-1 truncate">{ind}</span>
                      <span className="text-[10px] font-mono text-zinc-600">{cnt}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center rounded-lg border border-zinc-700 p-0.5" aria-label="Company table detail">{["simple","detailed"].map(mode=><button key={mode} aria-pressed={tableView===mode} onClick={()=>setTableView(mode)} className={`rounded px-2 py-1 text-[11px] ${tableView===mode?"bg-zinc-700 text-white":"text-zinc-500"}`}>{mode==="simple"?"Simple":"All columns"}</button>)}</div>
          <span className="text-xs text-zinc-600 font-mono">{rows.length} companies</span>
          <button
            onClick={() => setShowScreen(v => !v)}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[11px] font-semibold transition-all ${
              screenStatus?.running
                ? "border-emerald-700 bg-emerald-950/40 text-emerald-400"
                : showScreen
                ? "border-emerald-600 bg-emerald-950/40 text-emerald-400"
                : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
            }`}
          >
            <BarChart2 className="size-3" />
            {screenStatus?.running
              ? `Screening ${screenStatus.done + screenStatus.failed}/${screenStatus.total}…`
              : "Run Screen"}
          </button>
        </div>
      </div>

      {/* ── Screen Panel ── */}
      {showScreen && (
        <div className="border-b border-zinc-800 bg-emerald-950/10 -mx-6 px-6">
          <ScreenPanel
            status={screenStatus}
            onStart={startScreen}
            onCancel={cancelScreen}
            starting={screenStarting}
            onClose={() => setShowScreen(false)}
          />
        </div>
      )}

      {/* ── Table ── */}
      <div className="mt-4 rounded-xl border border-zinc-800 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="company-table w-full text-sm" data-view={tableView}>
          <thead>
            <tr className="border-b border-zinc-800 bg-zinc-900/60">
              {renderSortHeader("Model score", "rank")}
              <th className="px-4 py-3 text-left font-bold text-[11px] tracking-widest uppercase text-zinc-500">Company</th>
              <th className="px-3 py-3 text-left font-bold text-[11px] tracking-widest uppercase text-zinc-500">Industry</th>
              {renderSortHeader("Saved price", "current_price", true)}
              {renderSortHeader("Model value", "price_target_base", true)}
              {renderSortHeader("Model gap", "upside_pct", true)}
              {renderSortHeader("Financial Health", "piotroski_score", true)}
              <th className="px-3 py-3 text-right font-bold text-[11px] tracking-widest uppercase text-zinc-500">Quant Signals</th>
              <th className="px-3 py-3 w-8" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-14 text-center text-zinc-600 text-sm">
                  No companies match your filters.
                </td>
              </tr>
            ) : visibleRows.map((row) => {
              const computedUpside = computeUpside(row);
              const isUp = computedUpside >= 0;
              const fScore = row.piotroski_score;
              const fPct   = fScore !== null ? (fScore / 9) * 100 : 0;
              const fCol   = fScore === null ? "#52525b" : fScore >= 7 ? "#22c55e" : fScore >= 4 ? "#f59e0b" : "#ef4444";
              const fLabel = fScore === null ? null : fScore >= 7 ? "Strong" : fScore >= 4 ? "Neutral" : "Weak";
              const breakdown = convictionBreakdown(row);
              const conv   = breakdown.total;
              const cCol   = convictionColor(conv);
              const cLabel = convictionLabel(conv);
              const indCol = industryColor(row.industry);

              return (
                <tr
                  key={row.ticker}
                  className="border-b border-zinc-800/60 last:border-0 hover:bg-zinc-900/50 transition-colors group cursor-pointer"
                  onClick={() => onSelectTicker(row.ticker)}
                >
                  {/* Conviction */}
                  <td className="px-3 py-3 w-28">
                    <div className="flex flex-col items-center gap-1">
                      <div
                        className="w-14 h-14 rounded-xl flex items-center justify-center font-black text-xl font-mono"
                        style={{ background: `${cCol}20`, color: cCol, border: `2px solid ${cCol}50` }}
                      >
                        {conv}%
                      </div>
                      <span className="text-[10px] font-bold tracking-wide" style={{ color: cCol }}>{cLabel}</span>
                      {/* 5-dimension mini bars */}
                      <div className="model-dimensions flex gap-0.5 mt-0.5" title={[
                        `Valuation: ${breakdown.valuation.pts}/${breakdown.valuation.max} — ${breakdown.valuation.drivers.join(" | ")}`,
                        `Quality: ${breakdown.quality.pts}/${breakdown.quality.max} — ${breakdown.quality.drivers.join(" | ")}`,
                        `Value Creation: ${breakdown.value_creation.pts}/${breakdown.value_creation.max} — ${breakdown.value_creation.drivers.join(" | ")}`,
                        `Risk: ${breakdown.risk.pts}/${breakdown.risk.max} — ${breakdown.risk.drivers.join(" | ")}`,
                        `Growth: ${breakdown.growth.pts}/${breakdown.growth.max} — ${breakdown.growth.drivers.join(" | ")}`,
                        breakdown.penalties > 0 ? `Penalties: −${breakdown.penalties} pts${breakdown.penalty_drivers.length ? " (" + breakdown.penalty_drivers.join(", ") + ")" : ""}` : "",
                      ].filter(Boolean).join("\n")}>
                        {([
                          { d: breakdown.valuation,      label: "V" },
                          { d: breakdown.quality,        label: "Q" },
                          { d: breakdown.value_creation, label: "C" },
                          { d: breakdown.risk,           label: "R" },
                          { d: breakdown.growth,         label: "G" },
                        ] as { d: ConvictionDim; label: string }[]).map(({ d, label }) => {
                          const pct = d.pts / d.max;
                          const col = pct >= 0.7 ? "#22c55e" : pct >= 0.4 ? "#f59e0b" : "#ef4444";
                          return (
                            <div key={label} className="flex flex-col items-center gap-0.5">
                              <div className="w-5 h-1 rounded-full bg-zinc-800 overflow-hidden">
                                <div className="h-full rounded-full" style={{ width: `${pct * 100}%`, background: col }} />
                              </div>
                              <span className="text-[8px] font-bold" style={{ color: col }}>{label}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </td>

                  {/* Company */}
                  <td className="px-4 py-3 min-w-[200px]">
                    <div className="flex items-center gap-1.5">
                      <button className="font-bold text-zinc-100 font-mono tracking-wide hover:text-emerald-300 focus-visible:outline-2 focus-visible:outline-emerald-400" onClick={e=>{e.stopPropagation();onSelectTicker(row.ticker);}} aria-label={`Open ${row.ticker} company`}>{row.ticker}</button>
                      {row.data_source === "xbrl" && (
                        <span className="text-[8px] font-bold tracking-widest uppercase rounded px-1 py-0.5 bg-emerald-900/50 text-emerald-400 border border-emerald-800/60">
                          XBRL
                        </span>
                      )}
                      {row.data_source === "ai" && (
                        <span className="text-[8px] font-bold tracking-widest uppercase rounded px-1 py-0.5 bg-indigo-900/50 text-indigo-400 border border-indigo-800/60">
                          AI
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-zinc-400 truncate max-w-[220px] mt-0.5">{row.company_name}</div>
                    {row.verdict_text && (
                      <div className="text-[11px] text-zinc-500 truncate max-w-[220px] mt-1 italic leading-tight">
                        {row.verdict_text.length > 80 ? row.verdict_text.slice(0, 80) + "…" : row.verdict_text}
                      </div>
                    )}
                  </td>

                  {/* Industry */}
                  <td className="px-3 py-3 min-w-[140px]">
                    {row.industry ? (
                      <span
                        className="flex items-center gap-1.5 w-fit text-[11px] font-semibold rounded px-2 py-0.5"
                        style={{ background: `${indCol}22`, color: indCol }}
                      >
                        <span className="size-1.5 rounded-full shrink-0" style={{ background: indCol }} />
                        {row.industry}
                      </span>
                    ) : <span className="text-zinc-700">—</span>}
                  </td>

                  {/* Current price */}
                  <td className="px-3 py-3 text-right">
                    <span className="font-mono text-zinc-200 text-sm">
                      {row.current_price !== null ? `$${row.current_price.toFixed(2)}` : "—"}
                    </span>
                  </td>

                  {/* AI target */}
                  <td className="px-3 py-3 text-right">
                    <span className="font-mono font-semibold text-sm" style={{ color: cCol }}>
                      {row.price_target_base !== null ? `$${row.price_target_base.toFixed(2)}` : "—"}
                    </span>
                    {row.price_target_low !== null && row.price_target_high !== null && (
                      <div className="text-[10px] text-zinc-600 font-mono">
                        ${row.price_target_low.toFixed(0)}–${row.price_target_high.toFixed(0)}
                      </div>
                    )}
                  </td>

                  {/* Upside */}
                  <td className="px-3 py-3 text-right">
                    <span
                      className="font-mono font-black text-lg tabular-nums"
                      style={{ color: isUp ? "#22c55e" : "#ef4444" }}
                    >
                      {isUp ? "+" : ""}{computedUpside.toFixed(1)}%
                    </span>
                    <div className="text-[10px] text-zinc-600 mt-0.5">
                      to fair value
                    </div>
                  </td>

                  {/* Financial Health (Piotroski F-Score) */}
                  <td className="px-3 py-3 text-right min-w-[110px]">
                    {fScore !== null ? (
                      <div className="flex flex-col items-end gap-1">
                        <div className="flex items-center gap-1.5">
                          <span className="font-bold text-xs" style={{ color: fCol }}>{fLabel}</span>
                          <span className="font-mono text-xs text-zinc-500">{fScore}/9</span>
                        </div>
                        <div className="w-20 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                          <div className="h-full rounded-full transition-all" style={{ width: `${fPct}%`, background: fCol }} />
                        </div>
                        <div className="text-[9px] text-zinc-600">Piotroski F-Score</div>
                      </div>
                    ) : (
                      <div className="flex flex-col items-end gap-0.5">
                        <span className="text-zinc-700 text-xs">—</span>
                        <div className="text-[9px] text-zinc-700">N/A for sector</div>
                      </div>
                    )}
                  </td>

                  {/* Quant Signals */}
                  <td className="px-3 py-3">
                    <div className="flex flex-col gap-1 items-end">
                      {/* Beneish */}
                      {(() => {
                        const v = row.beneish_score;
                        if (v === null) return null;
                        const [col, lbl] = v > -1.78 ? ["#ef4444","Manip"] : v > -2.22 ? ["#f59e0b","Gray"] : ["#22c55e","Clean"];
                        return (
                          <div className="flex items-center gap-1" title={`Beneish M-Score: ${v.toFixed(2)}`}>
                            <span className="text-[9px] text-zinc-600 font-mono">Beneish</span>
                            <span className="text-[10px] font-bold font-mono px-1 rounded" style={{ color: col, background: `${col}20` }}>{lbl}</span>
                          </div>
                        );
                      })()}
                      {/* ROIC Spread */}
                      {(() => {
                        const v = row.roic_spread;
                        if (v === null) return null;
                        const [col] = v > 10 ? ["#22c55e"] : v > 3 ? ["#86efac"] : v >= 0 ? ["#f59e0b"] : ["#ef4444"];
                        return (
                          <div className="flex items-center gap-1" title={`ROIC−WACC Spread: ${v.toFixed(1)}%`}>
                            <span className="text-[9px] text-zinc-600 font-mono">ROIC−WACC</span>
                            <span className="text-[10px] font-bold font-mono px-1 rounded" style={{ color: col, background: `${col}20` }}>{v > 0 ? "+" : ""}{v.toFixed(0)}%</span>
                          </div>
                        );
                      })()}
                      {/* PEG */}
                      {(() => {
                        const v = row.peg_ratio_val;
                        if (v === null) return null;
                        const [col] = v > 0 && v < 1 ? ["#22c55e"] : v <= 2 ? ["#f59e0b"] : ["#ef4444"];
                        return (
                          <div className="flex items-center gap-1" title={`PEG Ratio: ${v.toFixed(2)}`}>
                            <span className="text-[9px] text-zinc-600 font-mono">PEG</span>
                            <span className="text-[10px] font-bold font-mono px-1 rounded" style={{ color: col, background: `${col}20` }}>{v.toFixed(2)}</span>
                          </div>
                        );
                      })()}
                      {/* Rule of 40 */}
                      {(() => {
                        const v = row.rule_of_40_score;
                        if (v === null) return null;
                        const [col] = v >= 40 ? ["#22c55e"] : v >= 20 ? ["#f59e0b"] : ["#ef4444"];
                        return (
                          <div className="flex items-center gap-1" title={`Rule of 40: ${v.toFixed(1)}`}>
                            <span className="text-[9px] text-zinc-600 font-mono">R40</span>
                            <span className="text-[10px] font-bold font-mono px-1 rounded" style={{ color: col, background: `${col}20` }}>{v.toFixed(0)}</span>
                          </div>
                        );
                      })()}
                      {row.beneish_score === null && row.roic_spread === null && row.peg_ratio_val === null && row.rule_of_40_score === null && (
                        <span className="text-zinc-700 text-xs">—</span>
                      )}
                    </div>
                  </td>

                  {/* Delete */}
                  <td className="px-3 py-3" onClick={e => e.stopPropagation()}>
                    <button
                      onClick={() => handleDelete(row.ticker)}
                      disabled={deleting === row.ticker}
                      className="opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-500 transition-all text-xs"
                    >
                      {deleting === row.ticker ? "…" : "✕"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      </div>
      <nav aria-label="Company pages" className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-800 py-4 text-xs text-zinc-400">
        <span>{rows.length ? pageIndex * 50 + 1 : 0}–{Math.min((pageIndex + 1) * 50, rows.length)} of {rows.length.toLocaleString()} matching companies · {data.total.toLocaleString()} saved</span>
        <div className="flex items-center gap-3">
          <button className="rounded border border-zinc-700 px-3 py-2 disabled:opacity-40" disabled={pageIndex === 0} onClick={() => setPageState({ key: pageKey, index: pageIndex - 1 })}>Previous companies</button>
          <span>Page {pageIndex + 1} of {pageCount}</span>
          <button className="rounded border border-zinc-700 px-3 py-2 disabled:opacity-40" disabled={pageIndex + 1 >= pageCount} onClick={() => setPageState({ key: pageKey, index: pageIndex + 1 })}>Next companies</button>
        </div>
      </nav>
    </>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function StockAnalyzer() {
  const [view, setView] = useState<"analyzer" | "dashboard">("dashboard");
  const [tickerInput, setTickerInput] = useState("");
  const [data, setData] = useState<StockData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingSecs, setLoadingSecs] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const [aiText, setAiText] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const aiAbortRef = useRef<AbortController | null>(null);

  const [filings, setFilings] = useState<FilingsData | null>(null);
  const [filingsLoading, setFilingsLoading] = useState(false);
  const [filingsError, setFilingsError] = useState<string | null>(null);

  const [rankings, setRankings] = useState<Record<string, { percentile: number | null; label: string; higher_better: boolean }> | null>(null);

  const [deepMeta, setDeepMeta] = useState<FilingDeepMeta | null>(null);
  const [deepVerdict, setDeepVerdict] = useState<FilingVerdict | null>(null);
  const [deepText, setDeepText] = useState("");
  const [deepLoading, setDeepLoading] = useState(false);
  const [deepError, setDeepError] = useState<string | null>(null);
  const deepAbortRef = useRef<AbortController | null>(null);
  const fetchAbortRef = useRef<AbortController | null>(null);
  const stockCacheRef = useRef<Map<string, StockData>>(new Map());
  const dashboardScrollRef=useRef(0);
  const deepLinkHandled=useRef(false);
  const tabsRef = useRef<HTMLDivElement | null>(null);
  const [activeTab, setActiveTab] = useState("overview");

  // Tick loading timer so user knows the page is still working
  useEffect(() => {
    if (!loading) return;
    const iv = setInterval(() => setLoadingSecs(s => s + 1), 1000);
    return () => clearInterval(iv);
  }, [loading]);

  const fetchData=useCallback(async(ticker: string, reload = false) => {
    // Cancel any in-flight fetchData from a previous company selection
    if (fetchAbortRef.current) fetchAbortRef.current.abort();
    const myCtrl = new AbortController();
    fetchAbortRef.current = myCtrl;

    const t = ticker.toUpperCase();
    if(!reload&&data?.ticker!==t){setActiveTab("overview");if(view==="dashboard")dashboardScrollRef.current=window.scrollY;window.scrollTo(0,0);}

    // ── Serve from in-memory cache instantly if available ──
    const cached = reload ? undefined : stockCacheRef.current.get(t);
    if (cached) {
      setData(cached);
      setLoading(false);
      setLoadingSecs(0);
      setError(null);
      setAiText("");
      setAiError(null);
      setFilings(null);
      setFilingsError(null);
      setFilingsLoading(false);
      setDeepMeta(null);
      setDeepVerdict(null);
      setDeepText("");
      setDeepError(null);
      setDeepLoading(false);
    } else {
      setLoading(true);
      setLoadingSecs(0);
      setError(null);
      setData(null);
      setAiText("");
      setAiError(null);
      setFilings(null);
      setFilingsError(null);
      setFilingsLoading(false);
      setDeepMeta(null);
      setDeepVerdict(null);
      setDeepText("");
      setDeepError(null);
      setDeepLoading(false);
    }

    // ── Fetch stock data (critical path — unblocks the page immediately) ──
    if (!cached) {
      const stockCtrl = new AbortController();
      const stockTimeout = setTimeout(() => stockCtrl.abort(), 45000);
      myCtrl.signal.addEventListener("abort", () => stockCtrl.abort(), { once: true });

      try {
        const stockRes = await apiFetch(`${API}/api/data/${t}`, { signal: stockCtrl.signal });
        clearTimeout(stockTimeout);
        if (myCtrl.signal.aborted) return;

        if (!stockRes.ok) {
          const body = await stockRes.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${stockRes.status}`);
        }
        const stockJson: StockData = await stockRes.json();
        if (myCtrl.signal.aborted) return;
        stockCacheRef.current.set(t, stockJson);
        setData(stockJson);
      } catch (e: unknown) {
        clearTimeout(stockTimeout);
        if (myCtrl.signal.aborted) return;
        const isTimeout = e instanceof Error && e.name === "AbortError";
        setError(isTimeout ? "Backend took too long to respond. Please tap Retry." : (e instanceof Error ? e.message : "Failed to fetch data"));
      } finally {
        if (!myCtrl.signal.aborted) setLoading(false);
      }
    }

    if (myCtrl.signal.aborted) return;

    // ── Fetch percentile rankings in background ──
    setRankings(null);
    apiFetch(`${API}/api/rankings/${t}`,{signal:myCtrl.signal})
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d || myCtrl.signal.aborted) return;
        const flat: Record<string, { percentile: number | null; label: string; higher_better: boolean }> = {};
        for (const [k, v] of Object.entries(d.rankings as Record<string, { percentile: number | null; label: string; higher_better: boolean }>)) {
          flat[k] = { percentile: v.percentile, label: v.label, higher_better: v.higher_better };
        }
        setRankings(flat);
      })
      .catch(() => {});

  },[data?.ticker,view]);
  useEffect(()=>{
    if(deepLinkHandled.current)return;
    deepLinkHandled.current=true;
    const ticker=new URLSearchParams(window.location.search).get('ticker')?.trim().toUpperCase();
    if(ticker&&/^[A-Z0-9][A-Z0-9.\-^=]{0,15}$/.test(ticker))void Promise.resolve().then(()=>{setTickerInput(ticker);setView("analyzer");return fetchData(ticker);});
  },[fetchData]);

  async function loadCompanyFilings(t:string) {
    const myCtrl=fetchAbortRef.current;
    if(!myCtrl || myCtrl.signal.aborted || filingsLoading || filings) return;
    setFilingsLoading(true);
    // ── Fetch filings in background (non-blocking — page already visible) ──
    const filingsCtrl = new AbortController();
    const filingsTimeout = setTimeout(() => filingsCtrl.abort(), 60000);
    myCtrl.signal.addEventListener("abort", () => filingsCtrl.abort(), { once: true });

    try {
      const filingsRes = await apiFetch(`${API}/api/filings/${t}`, { signal: filingsCtrl.signal });
      clearTimeout(filingsTimeout);
      if (myCtrl.signal.aborted) return;

      if (filingsRes.ok) {
        const filingsJson: FilingsData = await filingsRes.json();
        if (!myCtrl.signal.aborted) {
          if (filingsJson.error) setFilingsError(filingsJson.error);
          else { setFilings(filingsJson); startFilingAnalysis(t); }
        }
      } else {
        if (!myCtrl.signal.aborted) setFilingsError("EDGAR filings unavailable");
      }
    } catch {
      clearTimeout(filingsTimeout);
      if (!myCtrl.signal.aborted) setFilingsError("EDGAR filings unavailable");
    } finally {
      if (!myCtrl.signal.aborted) setFilingsLoading(false);
    }
  }

  async function startAI(ticker: string) {
    if (aiAbortRef.current) aiAbortRef.current.abort();
    const ctrl = new AbortController();
    aiAbortRef.current = ctrl;

    setAiText("");
    setAiError(null);
    setAiLoading(true);

    try {
      const res = await apiFetch(`${API}/api/analyze/${ticker.toUpperCase()}`, {
        signal: ctrl.signal,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") break;
          try {
            const obj = JSON.parse(payload);
            if (obj.error) {
              setAiError(obj.error);
              return;
            }
            if (obj.content) {
              setAiText((prev) => prev + obj.content);
            }
          } catch {
            // ignore parse errors
          }
        }
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") {
        setAiError(e.message);
      }
    } finally {
      setAiLoading(false);
    }
  }

  async function startFilingAnalysis(ticker: string) {
    if (deepAbortRef.current) deepAbortRef.current.abort();
    const ctrl = new AbortController();
    deepAbortRef.current = ctrl;

    setDeepMeta(null);
    setDeepVerdict(null);
    setDeepText("");
    setDeepError(null);
    setDeepLoading(true);

    let capturedMeta: FilingDeepMeta | null = null;
    let capturedVerdict: FilingVerdict | null = null;

    try {
      const res = await apiFetch(
        `${API}/api/filing-deep-analysis/${ticker.toUpperCase()}`,
        { signal: ctrl.signal }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let verdictParsed = false;
      let fullText = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") break;
          try {
            const obj = JSON.parse(payload);
            if (obj.type === "meta") {
              capturedMeta = obj as FilingDeepMeta;
              setDeepMeta(capturedMeta);
            } else if (obj.type === "error") {
              setDeepError(obj.error);
              return;
            } else if (obj.type === "content" && obj.content) {
              fullText += obj.content;
              setDeepText(fullText);
              if (!verdictParsed) {
                const nl = fullText.indexOf("\n");
                if (nl > 0) {
                  const firstLine = fullText.slice(0, nl).trim();
                  try {
                    const v = JSON.parse(firstLine);
                    if (v.signal && v.price_target_base !== undefined) {
                      // Always recompute upside_pct — the model miscalculates this
                      const cp = capturedMeta?.current_price ?? 0;
                      if (cp && v.price_target_base) {
                        v.upside_pct = ((v.price_target_base - cp) / cp) * 100;
                      }
                      capturedVerdict = v as FilingVerdict;
                      setDeepVerdict(capturedVerdict);
                      verdictParsed = true;
                    }
                  } catch {
                    // not ready yet
                  }
                }
              }
            }
          } catch {
            // ignore parse errors
          }
        }
      }

      // Auto-save to dashboard after streaming completes
      if (capturedVerdict && capturedMeta) {
        apiFetch(`${API}/api/save-analysis`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ticker: capturedMeta.ticker,
            company_name: capturedMeta.company_name,
            sector: data?.sector ?? null,
            industry: data?.industry ?? null,
            current_price: capturedMeta.current_price,
            piotroski_score: data?.piotroski_f?.score ?? null,
            signal: capturedVerdict.signal,
            confidence: typeof capturedVerdict.confidence === "number"
              ? capturedVerdict.confidence
              : parseFloat(capturedVerdict.confidence) || null,
            price_target_low: capturedVerdict.price_target_low,
            price_target_base: capturedVerdict.price_target_base,
            price_target_high: capturedVerdict.price_target_high,
            upside_pct: capturedVerdict.upside_pct,
            verdict_text: capturedVerdict.verdict ?? null,
            filings_count: capturedMeta.filings_analyzed.length,
            filings_detail: capturedMeta.filings_analyzed,
            altman_z_score: data?.altman_z?.score ?? null,
            sloan_accrual: data?.sloan?.ratio ?? null,
            red_flags_count: data?.red_flags?.total ?? null,
            red_flags_high: data?.red_flags?.high ?? null,
          }),
        }).catch(() => {/* silent — dashboard save is best-effort */});
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== "AbortError") {
        setDeepError(e.message);
      }
    } finally {
      setDeepLoading(false);
    }
  }

  const priceChange = data?.pct_52w;

  function goBack() {
    fetchAbortRef.current?.abort();
    const url=new URL(window.location.href);url.searchParams.delete("ticker");window.history.replaceState(window.history.state,"",url);
    setView("dashboard");
    requestAnimationFrame(()=>window.scrollTo(0,dashboardScrollRef.current));
    setData(null);
    setError(null);
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {view==="dashboard"&&<WorkspaceNav/>}
      {/* ── Header ── */}
      <header className={`border-b border-zinc-800/70 bg-zinc-950/95 backdrop-blur-sm ${view==="analyzer"?"md:sticky top-0 z-40":""} px-5 py-3`}>
        <div className={`mx-auto flex flex-wrap items-center gap-4 ${view === "dashboard" ? "max-w-[1400px]" : "max-w-6xl"}`}>
          {view === "analyzer" ? (
            /* Detail header: back + company info */
            <>
              <button
                onClick={goBack}
                className="flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-100 transition-colors shrink-0"
              >
                <span className="text-lg leading-none">←</span>
                Companies
              </button>
              <div className="h-5 w-px bg-zinc-800" />
              {data && (
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="font-bold font-mono text-zinc-100">{data.ticker}</span>
                  <span className="text-zinc-500 text-sm truncate hidden sm:block">{data.company_name}</span>
                  {deepLoading && (
                    <span className="flex items-center gap-1.5 ml-2 text-xs text-amber-500 shrink-0">
                      <span className="size-1.5 rounded-full bg-amber-500 animate-pulse" />
                      Analyzing SEC filings…
                    </span>
                  )}
                  {deepVerdict && !deepLoading && (
                    <span className="flex items-center gap-1.5 ml-2 text-xs text-emerald-500 shrink-0">
                      <span className="size-1.5 rounded-full bg-emerald-500" />
                      Saved to rankings
                    </span>
                  )}
                  {data.price !== null && (
                    <span className="font-mono font-semibold ml-auto shrink-0">${data.price.toFixed(2)}</span>
                  )}
                </div>
              )}
              {loading && <span className="text-sm text-zinc-500">Loading…</span>}
            </>
          ) : (
            /* List header: logo + add ticker */
            <>
              <div className="flex flex-wrap items-center gap-3 min-w-0">
                <div className="flex items-center gap-2">
                  <Activity className="size-5 text-emerald-400" />
                  <h1 className="text-lg font-semibold text-zinc-100 tracking-tight">Companies</h1>
                </div>

              </div>
              <form
                onSubmit={(e) => { e.preventDefault(); const t = tickerInput.trim(); if (t) { setView("analyzer"); fetchData(t); } }}
                className="flex gap-2 flex-1 min-w-0 max-w-sm ml-auto"
              >
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-500" />
                  <input
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg pl-9 pr-3 h-9 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none focus:border-zinc-500 uppercase transition-colors"
                    aria-label="Analyze a ticker" placeholder="Search a ticker…"
                    value={tickerInput}
                    onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading || !tickerInput.trim()}
                  className="h-9 px-4 rounded-lg bg-zinc-100 text-zinc-950 text-sm font-semibold hover:bg-white transition-colors disabled:opacity-40"
                >
                  {loading ? "…" : "Analyze"}
                </button>
              </form>
              <span className="text-xs text-zinc-600 hidden lg:block shrink-0">SEC EDGAR · XBRL</span>
            </>
          )}
        </div>
      </header>

      <main className={`mx-auto px-6 bg-zinc-950 ${view === "dashboard" ? "pb-6" : "py-6"}`}>
        {/* ── Rankings table — always mounted so state is preserved on back navigation ── */}
        <div className={view === "dashboard" ? "max-w-[1400px] mx-auto" : "hidden"}>
          <Dashboard
            onSelectTicker={(t) => {
              setTickerInput(t);
              setView("analyzer");
              fetchData(t);
            }}
          />
        </div>

        <div className={view === "analyzer" ? "max-w-6xl mx-auto space-y-6" : "hidden"}>
        <>
        {/* ── Error ── */}
        {error && (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 shrink-0" />
              {error}
            </div>
            <button
              onClick={() => tickerInput && fetchData(tickerInput)}
              className="shrink-0 rounded px-2 py-1 text-xs font-medium bg-red-100 hover:bg-red-200 dark:bg-red-900 dark:hover:bg-red-800 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {/* ── Loading skeleton ── */}
        {loading && (
          <div className="space-y-4">
            <Skeleton className="h-28 w-full rounded-xl" />
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full rounded-xl" />
              ))}
            </div>
            <Skeleton className="h-64 w-full rounded-xl" />
            {loadingSecs >= 5 && (
              <p className="text-center text-sm text-muted-foreground">
                {loadingSecs < 20
                  ? `Fetching market data… (${loadingSecs}s)`
                  : `Still loading — Yahoo Finance is slow for this ticker. (${loadingSecs}s)`}
              </p>
            )}
          </div>
        )}

        {/* ── Data ── */}
        {data && !loading && (
          <>

            {/* Company header */}
            <Card className="gap-3">
              <CardContent className="pt-0">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h1 className="text-2xl font-bold">{data.company_name}</h1>
                      <Badge variant="outline">{data.ticker}</Badge>
                      <Badge variant="secondary">{data.exchange}</Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {data.sector} · {data.industry}
                      {data.employees && ` · ${data.employees.toLocaleString()} employees`}
                    </p>
                    {data.description && (
                      <details className="mt-2 max-w-2xl text-xs text-muted-foreground">
                        <summary className="cursor-pointer">About this company</summary>
                        <p className="mt-2 leading-6">{data.description}</p>
                      </details>
                    )}
                  </div>

                  <div className="text-right">
                    <div className="text-3xl font-bold">
                      {data.price !== null ? `$${fmt(data.price)}` : "N/A"}
                    </div>
                    {priceChange !== null && priceChange !== undefined && (
                      <div
                        className={`flex items-center justify-end gap-1 text-sm font-medium ${
                          priceChange >= 0
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-red-600 dark:text-red-400"
                        }`}
                      >
                        {priceChange >= 0 ? (
                          <TrendingUp className="size-4" />
                        ) : (
                          <TrendingDown className="size-4" />
                        )}
                        {fmtPct(priceChange)} (52W)
                      </div>
                    )}
                    <div className="mt-1 text-sm text-muted-foreground">
                      Mkt Cap: {fmtMoney(data.market_cap)}
                    </div>
                  </div>
                </div>

                {data.freshness.warning && (
                  <p className="mt-3 flex items-center gap-2 text-xs text-amber-300" title={data.freshness.warning}>
                    <AlertTriangle className="size-3.5 shrink-0" />
                    Annual period · {data.freshness.days_old} days old
                  </p>
                )}
              </CardContent>
            </Card>

            <ResearchNote><p>Annual inputs and individual calculations have different levels of evidence. Open Sources &amp; checks for dates, missing fields and calculation details.</p></ResearchNote>
            {/* ── Tabs ── */}
            <div ref={tabsRef} />
            <Tabs value={activeTab} onValueChange={tab=>{setActiveTab(tab);if(tab==="filings"||tab==="deep-analysis")void loadCompanyFilings(data.ticker);}}>
              <TabsList className="h-auto min-h-10 w-full flex-nowrap justify-start overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/70 p-1 [&>button]:shrink-0">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="charts">Charts</TabsTrigger>
                <TabsTrigger value="public-trades">Public trades</TabsTrigger>
                <TabsTrigger value="scores">Scores</TabsTrigger>
                <TabsTrigger value="sources">Sources &amp; checks</TabsTrigger>
                <TabsTrigger value="flags">
                  Red Flags
                  {data.red_flags.high > 0 && (
                    <span className="ml-1.5 rounded-full bg-red-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
                      {data.red_flags.high}
                    </span>
                  )}
                </TabsTrigger>
                <TabsTrigger value="filings">
                  <FileText className="mr-1.5 size-3.5" />
                  Filings
                  {filings && filings.total > 0 && (
                    <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium">
                      {filings.total}
                    </span>
                  )}
                  {filingsLoading && (
                    <span className="ml-1.5 size-3 animate-spin rounded-full border border-current border-t-transparent" />
                  )}
                </TabsTrigger>
                <TabsTrigger value="deep-analysis">
                  <Brain className="mr-1.5 size-3.5" />
                  Deep Analysis
                  {deepVerdict && (
                    <span className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-bold text-white ${
                      deepVerdict.signal === "STRONG_BUY" || deepVerdict.signal === "BUY"
                        ? "bg-emerald-500"
                        : deepVerdict.signal === "HOLD"
                        ? "bg-amber-500"
                        : "bg-red-500"
                    }`}>
                      {(() => {
                        const cp = deepMeta?.current_price ?? 0;
                        const u = cp && deepVerdict.price_target_base
                          ? ((deepVerdict.price_target_base - cp) / cp) * 100
                          : deepVerdict.upside_pct;
                        return `${u >= 0 ? "+" : ""}${u.toFixed(0)}%`;
                      })()}
                    </span>
                  )}
                  {deepLoading && (
                    <span className="ml-1.5 size-3 animate-spin rounded-full border border-current border-t-transparent" />
                  )}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="sources" className="mt-4 space-y-4">
                {data.freshness.warning && <p className="rounded-lg border border-amber-900 p-3 text-sm text-amber-200">Annual period: {data.freshness.label} ({data.freshness.days_old} days old). {data.freshness.warning}</p>}
            {data.number_evidence && <NumberEvidence key={`${data.number_evidence.input_sha256}:${data.number_evidence.primary_context?.content_sha256}`} report={data.number_evidence} onReload={()=>void fetchData(data.ticker,true)}/>}                {data.valuation_scenario && <details className="mt-3 rounded-lg border border-amber-900 p-3 text-xs">
                  <summary className="cursor-pointer">Valuation assumptions · {data.valuation_scenario.status}</summary>
                  {data.valuation_scenario.reason && <p className="mt-2 text-amber-300">{data.valuation_scenario.reason}</p>}
                  <p className="mt-2">{data.valuation_scenario.cash_flow_basis} · {data.valuation_scenario.shares_basis}</p>
                  <ul className="list-disc pl-4 mt-2">{data.valuation_scenario.limitations.map(line=><li key={line}>{line}</li>)}</ul>
                  {data.valuation_scenario.sensitivity.length > 0 && <div className="overflow-x-auto mt-3"><table className="w-full text-left"><thead><tr><th>Cost of equity</th><th>Initial growth</th><th>Scenario / share</th></tr></thead><tbody>{data.valuation_scenario.sensitivity.map((point,index)=><tr key={index}><td>{(point.discount_rate*100).toFixed(1)}%</td><td>{(point.growth_rate*100).toFixed(1)}%</td><td>${point.price.toFixed(2)}</td></tr>)}</tbody></table></div>}
                </details>}
                {data.financial_period_end && (
                  <details className="mt-3 text-xs text-muted-foreground">
                    <summary className="cursor-pointer">Financial input dates · annual period {data.financial_period_end}</summary>
                    <p className="mt-2">{data.financial_data_source}</p>
                    <p className="mt-2">Retrieved {data.market_data_fetched_at ? new Date(data.market_data_fetched_at).toLocaleString() : "during this request"}. Retrieval time is not the exchange timestamp of the quote.</p>
                    <div className="mt-2 grid gap-1">
                      {Object.entries(data.financial_provenance ?? {}).filter(([key]) => !key.includes("_prior")).map(([key, fact]) => (
                        <p key={key}>{key.replaceAll("_", " ")}: {fact.concept} · period {fact.end} · filed {fact.filed}</p>
                      ))}
                    </div>
                  </details>
                )}
</TabsContent>

              {/* ── Overview tab ── */}
              <TabsContent value="public-trades" className="mt-4">
                <TradeEvidenceWorkspace key={data.ticker} embedded ticker={data.ticker} />
              </TabsContent>
              <TabsContent value="overview" className="mt-4">
                <div className="mb-5 space-y-5">            {/* Key metrics strip */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                { label: "Annual revenue", value: fmtMoney(data.revenue) },
                { label: "Free cash flow", value: fmtMoney(data.fcf) },
                { label: "Cash on hand", value: fmtMoney(data.cash) },
                { label: "Total debt", value: fmtMoney(data.total_debt) },
              ].map((m) => (
                <Card key={m.label} className="gap-1">
                  <CardContent className="pt-0">
                    <div className="text-xs text-muted-foreground">{m.label}</div>
                    <div className="text-lg font-semibold">{m.value}</div>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* ── Quick Take ── */}
            {(() => {
              const price = data.price ?? 0;
              const bullets: { label: string; detail: string; good: boolean | null }[] = [];

              // 1. Is the stock cheap or expensive right now?
              const fv = data.avg_fair_value;
              const dcf = data.dcf_price;
              const graham = data.graham_number;
              if (price > 0) {
                const dcfBad = dcf && dcf > price * 5;
                const ref = dcfBad ? graham : fv;
                if (ref && ref > 0) {
                  const prem = ((price - ref) / ref) * 100;
                  const cheap = prem < -15;
                  const expensive = prem > 15;
                  const refName = dcfBad ? `Graham Number ($${fmt(graham)})` : `fair value ($${fmt(fv)})`;
                  bullets.push({
                    label: cheap ? `Looks undervalued — ${Math.abs(prem).toFixed(0)}% below fair value`
                         : expensive ? `Looks overvalued — ${prem.toFixed(0)}% above fair value`
                         : `Fairly priced — within 15% of fair value`,
                    detail: `Current price $${fmt(price)} vs ${refName}${dcfBad ? " (DCF excluded — unreliable for this sector)" : ` based on DCF $${fmt(dcf)} and Graham $${fmt(graham)}`}.`,
                    good: cheap ? true : expensive ? false : null,
                  });
                }
              }

              // 2. Is the company in good financial shape? (Piotroski)
              const f = data.piotroski_f?.score;
              if (f !== null && f !== undefined) {
                bullets.push({
                  label: f >= 7 ? `Financially healthy (${f}/9 score)` : f >= 4 ? `Average financial health (${f}/9 score)` : `Financially stressed (${f}/9 score)`,
                  detail: `The Piotroski F-Score grades 9 financial signals — profitability, leverage trends, liquidity, and efficiency. ${f >= 7 ? "A score of 7–9 means the business is improving across most dimensions." : f >= 4 ? "A mid-range score means the company passes some tests but has weaknesses." : "A score below 4 signals deteriorating fundamentals — red flag for investors."}`,
                  good: f >= 7 ? true : f >= 4 ? null : false,
                });
              }

              // 2b. Bankruptcy risk (Altman Z-Score)
              const az = data.altman_z?.score;
              if (az !== null && az !== undefined) {
                const azSafe = az > 2.99;
                const azGrey = az > 1.81;
                bullets.push({
                  label: azSafe ? `Low bankruptcy risk (Altman Z: ${az.toFixed(2)})` : azGrey ? `Elevated financial risk (Altman Z: ${az.toFixed(2)} — Grey Zone)` : `High distress risk (Altman Z: ${az.toFixed(2)} — Distress Zone)`,
                  detail: `The Altman Z-Score predicts bankruptcy probability within 2 years using 5 financial ratios. ${azSafe ? "Above 2.99 is Safe Zone — the company is financially stable." : azGrey ? "Between 1.81–2.99 is Grey Zone — elevated risk. The company isn't in immediate danger but is worth monitoring." : "Below 1.81 is Distress Zone — high probability of financial difficulty. This is a serious red flag."}`,
                  good: azSafe ? true : azGrey ? null : false,
                });
              }

              // 2c. Earnings quality (Sloan Accrual Ratio)
              const sloan = data.sloan?.ratio;
              if (sloan !== null && sloan !== undefined) {
                const sloanGood = sloan < 0;
                const sloanBad = sloan > 10;
                bullets.push({
                  label: sloan < -10 ? `Very high earnings quality (Sloan: ${sloan.toFixed(1)}%)` : sloanGood ? `Earnings backed by real cash (Sloan: ${sloan.toFixed(1)}%)` : sloanBad ? `Earnings quality concern — profits exceed cash flows (Sloan: ${sloan.toFixed(1)}%)` : `Moderate earnings quality (Sloan: ${sloan.toFixed(1)}%)`,
                  detail: `The Sloan Accrual Ratio measures whether profits are backed by actual cash flows. ${sloanBad ? `A ratio of ${sloan.toFixed(1)}% means the company is booking more profit than it's collecting in cash — this could signal aggressive accounting. Stocks with high accruals historically underperform.` : sloanGood ? "Negative accruals mean cash flows exceed reported profits — a sign of high-quality, conservative earnings." : "Some accruals are normal, but this warrants watching."}`,
                  good: sloanBad ? false : sloanGood ? true : null,
                });
              }

              // 3. Is the company profitable and generating real cash?
              const roe = data.roe;
              const fcfY = data.fcf_yield;
              const fcfPct = fcfY !== null ? fcfY * 100 : null;
              if (roe !== null || fcfPct !== null) {
                const roeGood = roe !== null && roe > 12;
                const fcfGood = fcfPct !== null && fcfPct > 3;
                const both = (roe !== null) && (fcfPct !== null);
                const label = both
                  ? (roeGood && fcfGood ? "Strong profitability and cash flow"
                    : !roeGood && !fcfGood ? "Weak profitability and cash flow"
                    : "Mixed profitability signals")
                  : roe !== null ? (roeGood ? "Good return on equity" : "Low return on equity") : (fcfGood ? "Healthy cash generation" : "Thin cash generation");
                const parts = [];
                if (roe !== null) parts.push(`ROE ${fmt(roe)}% — for every $100 of shareholders' equity, the company earned $${fmt(roe)}`);
                if (fcfPct !== null) parts.push(`FCF Yield ${fmt(fcfPct)}% — the company generates ${fmt(fcfPct)} cents of free cash for every $1 of market value`);
                bullets.push({ label, detail: parts.join(". ") + ".", good: roeGood || fcfGood ? (roeGood && fcfGood ? true : null) : false });
              }

              // 4. Is the company growing?
              const revG = data.rev_growth;
              const epsG = data.eps_growth;
              if (revG !== null || epsG !== null) {
                const revGood = (revG ?? 0) > 5;
                const epsGood = (epsG ?? 0) > 10;
                const growthGood = revGood || epsGood;
                const parts = [];
                if (revG !== null) parts.push(`revenue ${revG > 0 ? "grew" : "fell"} ${Math.abs(revG).toFixed(1)}% year-over-year`);
                if (epsG !== null) parts.push(`earnings per share ${epsG > 0 ? "grew" : "fell"} ${Math.abs(epsG).toFixed(1)}%`);
                bullets.push({
                  label: growthGood ? "Growing business" : "Slow or declining growth",
                  detail: `The company's ${parts.join(" and ")}. ${growthGood ? "Growth supports the current valuation." : "Slower growth makes the valuation harder to justify."}`,
                  good: growthGood,
                });
              }

              // 5. Can it handle its debt?
              const deRatio = data.de_ratio;
              const curRatio = data.current_ratio;
              if (deRatio !== null || curRatio !== null) {
                const highDebt = deRatio !== null && deRatio > 2;
                const lowLiq = curRatio !== null && curRatio < 1;
                const ok = !highDebt && !lowLiq;
                const parts = [];
                if (deRatio !== null) parts.push(`debt is ${fmt(deRatio)}× equity${highDebt ? " — carrying a lot of debt" : " — manageable debt level"}`);
                if (curRatio !== null) parts.push(`current ratio ${fmt(curRatio)}×${lowLiq ? " — may struggle to cover short-term bills" : " — has enough liquid assets to cover short-term bills"}`);
                bullets.push({
                  label: ok ? "Manageable debt and liquidity" : highDebt && lowLiq ? "High debt and liquidity risk" : highDebt ? "High debt load" : "Liquidity concern",
                  detail: `The company's ${parts.join("; ")}.`,
                  good: ok ? true : false,
                });
              }

              // 6. Any red flags?
              const highFlags = data.red_flags.high;
              if (highFlags > 0) {
                bullets.push({
                  label: `${highFlags} serious warning${highFlags > 1 ? "s" : ""} detected`,
                  detail: `Our models flagged ${highFlags} high-severity issue${highFlags > 1 ? "s" : ""} — such as extreme leverage, earnings manipulation risk, or distress signals. See the Red Flags tab for specifics.`,
                  good: false,
                });
              }

              const sig = deepVerdict?.signal;
              const sigCfg = sig ? signalConfig(sig) : null;
              const upside = deepVerdict && deepMeta?.current_price && deepVerdict.price_target_base
                ? ((deepVerdict.price_target_base - deepMeta.current_price) / deepMeta.current_price) * 100
                : null;

              const greens = bullets.filter(b => b.good === true).length;
              const reds   = bullets.filter(b => b.good === false).length;
              const summaryText  = greens > reds ? `${greens} positive · ${reds} negative`
                                 : reds > greens ? `${reds} concern${reds > 1 ? "s" : ""} · ${greens} positive`
                                 : "Mixed signals";

              const summaryBadgeClass = greens > reds
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                : reds > greens
                ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";

              return (
                <Card>
                  <CardHeader className="pb-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex items-center gap-2.5">
                        <CardTitle className="text-base">Quick Take</CardTitle>
                        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${summaryBadgeClass}`}>
                          {summaryText}
                        </span>
                        <span className="text-xs text-muted-foreground hidden sm:inline">· based on financial data only</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {sigCfg && upside !== null && (
                          <div className="flex items-center gap-1.5">
                            <span className={`rounded px-2 py-0.5 text-xs font-bold text-white ${sigCfg.bg}`}>{sigCfg.label}</span>
                            <span className={`text-sm font-bold ${upside >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                              {upside >= 0 ? "+" : ""}{upside.toFixed(1)}%
                            </span>
                          </div>
                        )}
                        <button
                          onClick={() => {
                            setActiveTab("deep-analysis");
                            tabsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
                          }}
                          className="rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 transition-colors whitespace-nowrap"
                        >
                          Full SEC Filing Analysis →
                        </button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="pt-0">
                    <div className="space-y-2">
                      {bullets.map((b, i) => (
                        <div
                          key={i}
                          className={`rounded-lg border px-4 py-3 ${
                            b.good === true
                              ? "border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40"
                              : b.good === false
                              ? "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/40"
                              : "border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800/40"
                          }`}
                        >
                          <div className="flex items-start gap-2.5">
                            {b.good === true
                              ? <CheckCircle className="size-4 shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400" />
                              : b.good === false
                              ? <XCircle className="size-4 shrink-0 mt-0.5 text-red-600 dark:text-red-400" />
                              : <span className="size-4 shrink-0 mt-0.5 flex items-center justify-center text-zinc-400 text-xs font-bold">–</span>
                            }
                            <div>
                              <div className={`text-sm font-semibold ${
                                b.good === true ? "text-emerald-800 dark:text-emerald-200"
                                : b.good === false ? "text-red-800 dark:text-red-200"
                                : "text-zinc-700 dark:text-zinc-300"
                              }`}>{b.label}</div>
                              <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{b.detail}</div>
                            </div>
                          </div>
                        </div>
                      ))}
                      {bullets.length === 0 && (
                        <p className="text-sm text-muted-foreground py-2">Not enough data to generate a summary.</p>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })()}

</div>
                <div className="grid gap-4 md:grid-cols-3">
                  {/* Valuation */}
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Valuation Ratios</CardTitle>
                    </CardHeader>
                    <CardContent className="divide-y divide-border">
                      <MetricRow label="P/E (TTM)" value={data.pe_ttm !== null ? `${fmt(data.pe_ttm)}x` : "N/A"} />
                      <MetricRow label="Forward P/E" value={data.pe_fwd !== null ? `${fmt(data.pe_fwd)}x` : "N/A"} />
                      <MetricRow label="P/B" value={data.pb !== null ? `${fmt(data.pb)}x` : "N/A"} />
                      <MetricRow label="P/S" value={data.ps !== null ? `${fmt(data.ps)}x` : "N/A"} />
                      <MetricRow label="EV/EBITDA" value={data.ev_ebitda !== null ? `${fmt(data.ev_ebitda)}x` : "N/A"} />
                      <MetricRow label="PEG" value={fmt(data.peg)} rankPct={rankings?.peg_ratio_val?.percentile} />
                      <MetricRow label="FCF Yield" value={data.fcf_yield !== null ? `${(data.fcf_yield * 100).toFixed(2)}%` : "N/A"} rankPct={rankings?.fcf_yield?.percentile} />
                      <MetricRow label="Dividend Yield" value={data.dividend_yield !== null ? `${fmt(data.dividend_yield)}%` : "N/A"} />
                    </CardContent>
                  </Card>

                  {/* Profitability */}
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Profitability</CardTitle>
                    </CardHeader>
                    <CardContent className="divide-y divide-border">
                      <MetricRow label="Gross Margin" value={data.gross_margin !== null ? `${fmt(data.gross_margin)}%` : "N/A"} />
                      <MetricRow label="Op. Margin" value={data.op_margin !== null ? `${fmt(data.op_margin)}%` : "N/A"} rankPct={rankings?.roic_spread?.percentile} />
                      <MetricRow label="Net Margin" value={data.net_margin !== null ? `${fmt(data.net_margin)}%` : "N/A"} />
                      <MetricRow label="ROE" value={data.roe !== null ? `${fmt(data.roe)}%` : "N/A"} />
                      <MetricRow label="ROA" value={data.roa !== null ? `${fmt(data.roa)}%` : "N/A"} />
                      <MetricRow label="Revenue Growth" value={data.rev_growth !== null ? `${fmtPct(data.rev_growth)}` : "N/A"} />
                      <MetricRow label="EPS Growth" value={data.eps_growth !== null ? `${fmtPct(data.eps_growth)}` : "N/A"} />
                      <MetricRow label="EPS" value={data.eps !== null ? `$${fmt(data.eps)}` : "N/A"} />
                    </CardContent>
                  </Card>

                  {/* Balance Sheet */}
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Balance Sheet</CardTitle>
                    </CardHeader>
                    <CardContent className="divide-y divide-border">
                      <MetricRow label="Cash" value={fmtMoney(data.cash)} />
                      <MetricRow label="Total Debt" value={fmtMoney(data.total_debt)} />
                      <MetricRow label="Net Debt" value={fmtMoney(data.net_debt)} />
                      <MetricRow label="Total Assets" value={fmtMoney(data.total_assets)} />
                      <MetricRow label="Total Equity" value={fmtMoney(data.total_equity)} />
                      <MetricRow label="Current Ratio" value={fmt(data.current_ratio)} />
                      <MetricRow label="Quick Ratio" value={fmt(data.quick_ratio)} />
                      <MetricRow label="Interest-bearing Debt / Equity" value={fmt(data.de_ratio)} />
                    </CardContent>
                  </Card>
                </div>

                {/* Financials row */}
                <Card className="mt-4">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Annual Financials</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 gap-x-8 gap-y-0 sm:grid-cols-4">
                      {[
                        ["Revenue", fmtMoney(data.revenue)],
                        ["Gross Profit", fmtMoney(data.gross_profit)],
                        ["EBIT", fmtMoney(data.ebit)],
                        ["Net Income", fmtMoney(data.net_income)],
                        ["Op. Cash Flow", fmtMoney(data.operating_cf)],
                        ["CapEx", fmtMoney(data.capex)],
                        ["Free Cash Flow", fmtMoney(data.fcf)],
                        ["D&A", fmtMoney(data.da)],
                      ].map(([label, value]) => (
                        <MetricRow key={label} label={label} value={value} />
                      ))}
                    </div>
                  </CardContent>
                </Card>

                {/* Valuation estimates */}
                <Card className="mt-4">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Valuation Estimates</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {/* Model inputs — supporting data */}
                    <div className="grid grid-cols-2 gap-x-8 sm:grid-cols-3">
                      <MetricRow label="DCF Estimate" value={data.dcf_price !== null ? `$${fmt(data.dcf_price)}` : "N/A"} sub={data.dcf_price !== null && data.price !== null && data.dcf_price > data.price * 5 ? "⚠ unreliable for this sector" : undefined} />
                      <MetricRow label="Graham Number" value={data.graham_number !== null ? `$${fmt(data.graham_number)}` : "N/A"} />
                      <MetricRow label="Quant Avg (model inputs)" value={data.avg_fair_value !== null ? `$${fmt(data.avg_fair_value)}` : "N/A"} />
                    </div>
                    {/* AI target — primary signal, always consistent with rankings table */}
                    {deepVerdict?.price_target_base != null && deepMeta?.current_price != null && (() => {
                      const u = ((deepVerdict.price_target_base - deepMeta.current_price) / deepMeta.current_price) * 100;
                      const isPos = u >= 0;
                      const borderCol = isPos ? "border-emerald-700/50" : "border-red-700/50";
                      const bgCol = isPos ? "bg-emerald-950/30" : "bg-red-950/30";
                      const uColor = isPos ? "text-emerald-400" : "text-red-400";
                      return (
                        <div className={`rounded-lg border ${borderCol} ${bgCol} px-4 py-3`}>
                          <div className="text-xs text-muted-foreground mb-1">AI Price Target <span className="text-zinc-600">(matches ranking table)</span></div>
                          <div className="flex items-baseline gap-3">
                            <div className="text-2xl font-bold">${deepVerdict.price_target_base.toFixed(2)}</div>
                            <div className={`text-lg font-black ${uColor}`}>{isPos ? "+" : ""}{u.toFixed(1)}%</div>
                            <div className="text-xs text-zinc-500">{isPos ? "upside" : "downside"} from ${fmt(deepMeta.current_price)}</div>
                          </div>
                          {deepVerdict.price_target_low != null && deepVerdict.price_target_high != null && (
                            <div className="text-xs text-zinc-600 mt-1">Range: ${deepVerdict.price_target_low.toFixed(0)} – ${deepVerdict.price_target_high.toFixed(0)}</div>
                          )}
                        </div>
                      );
                    })()}
                  </CardContent>
                </Card>
              </TabsContent>

              {/* ── Charts tab ── */}
              <TabsContent value="charts" className="mt-4"><div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">{[
                { label: "52W High", value: data.high_52w !== null ? `$${fmt(data.high_52w)}` : "N/A" },
                { label: "52W Low", value: data.low_52w !== null ? `$${fmt(data.low_52w)}` : "N/A" },
                { label: "Beta", value: fmt(data.beta) },
                { label: "Short Float", value: data.short_pct_float !== null ? `${fmt(data.short_pct_float)}%` : "N/A" },
              ].map(m=><Card key={m.label}><CardContent><p className="text-xs text-muted-foreground">{m.label}</p><p className="mt-1 font-semibold">{m.value}</p></CardContent></Card>)}</div>
                <div className="grid gap-4 md:grid-cols-2">
                  {/* Revenue trend */}
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Quarterly Revenue</CardTitle>
                    </CardHeader>
                    <CardContent>
                      {data.revenue_trend.length > 0 ? (
                        <ResponsiveContainer width="100%" height={220}>
                          <BarChart data={data.revenue_trend} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                            <XAxis
                              dataKey="quarter"
                              tick={{ fontSize: 11 }}
                              tickFormatter={(v: string) => v.slice(0, 7)}
                            />
                            <YAxis
                              tick={{ fontSize: 11 }}
                              tickFormatter={(v: number) => {
                                if (v >= 1e9) return `$${(v / 1e9).toFixed(0)}B`;
                                if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
                                return `$${v}`;
                              }}
                            />
                            <Tooltip
                              formatter={(v: number | undefined) => [fmtMoney(v ?? 0), "Revenue"]}
                              labelFormatter={(l) => `Quarter: ${l}`}
                            />
                            <Bar dataKey="revenue" radius={[4, 4, 0, 0]}>
                              {data.revenue_trend.map((_, i) => (
                                <Cell key={i} fill="#3b82f6" />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
                          No quarterly data available
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  {/* Net Income trend */}
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Quarterly Net Income</CardTitle>
                    </CardHeader>
                    <CardContent>
                      {data.ni_trend.length > 0 ? (
                        <ResponsiveContainer width="100%" height={220}>
                          <BarChart data={data.ni_trend} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                            <XAxis
                              dataKey="quarter"
                              tick={{ fontSize: 11 }}
                              tickFormatter={(v: string) => v.slice(0, 7)}
                            />
                            <YAxis
                              tick={{ fontSize: 11 }}
                              tickFormatter={(v: number) => {
                                const abs = Math.abs(v);
                                const sign = v < 0 ? "-" : "";
                                if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(0)}B`;
                                if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`;
                                return `${sign}$${abs}`;
                              }}
                            />
                            <ReferenceLine y={0} stroke="currentColor" strokeOpacity={0.3} />
                            <Tooltip
                              formatter={(v: number | undefined) => [fmtMoney(v ?? 0), "Net Income"]}
                              labelFormatter={(l) => `Quarter: ${l}`}
                            />
                            <Bar dataKey="net_income" radius={[4, 4, 0, 0]}>
                              {data.ni_trend.map((d, i) => (
                                <Cell key={i} fill={d.net_income >= 0 ? "#10b981" : "#ef4444"} />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
                          No quarterly data available
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </div>
              </TabsContent>

              {/* ── Scores tab ── */}
              <TabsContent value="scores" className="mt-4 space-y-4">

                {/* Score explanation banner */}
                <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-xs text-muted-foreground leading-relaxed space-y-2">
                  <div><span className="font-semibold text-foreground">Conviction Score — 5 dimensions, 100 pts total.</span> Each score below feeds into the Conviction % shown in the rankings table.</div>
                  <div className="grid grid-cols-1 gap-1.5">
                    <div><span className="font-semibold text-zinc-300">Valuation (30 pts)</span> — AI upside vs fair value + PEG ratio (P/E ÷ growth rate, where &lt;1× = cheap) + Reverse DCF (what annual FCF growth % the market is pricing in — low expectations = more room to beat).</div>
                    <div><span className="font-semibold text-zinc-300">Financial Quality (25 pts)</span> — Piotroski F-Score (9 binary signals: ROA trend, cash coverage, debt reduction, dilution, margin trend, asset turnover) + Sloan Accrual Ratio (negative = cash exceeds profits = real earnings; &gt;10% = accounting inflation) + Owner Earnings Yield (Buffett: net income + D&amp;A − CapEx, divided by market cap).</div>
                    <div><span className="font-semibold text-zinc-300">Value Creation (20 pts)</span> — ROIC minus WACC spread (every point above zero = permanent value created per dollar deployed) + Magic Formula Earnings Yield (Greenblatt: EBIT ÷ Enterprise Value = what you earn on the full purchase price).</div>
                    <div><span className="font-semibold text-zinc-300">Risk &amp; Integrity (15 pts)</span> — Altman Z-Score (5 ratios: working capital, retained earnings, EBIT, market cap ÷ debt, revenue ÷ assets — predicts bankruptcy within 2 years) + Beneish M-Score (8 ratios measuring receivables growth, margin compression, asset quality, SG&amp;A creep — detects earnings manipulation).</div>
                    <div><span className="font-semibold text-zinc-300">Growth Efficiency (10 pts)</span> — Rule of 40: revenue growth % + operating margin %. A score ≥40 means the business earns enough margin to justify its growth spend. ≥60 = elite.</div>
                    <div><span className="font-semibold text-zinc-400">Penalties</span>: −4 pts per high-severity red flag (max −15), −5 pts Beneish manipulation, −3 pts Altman distress, −3/−8/−15 pts for price 20%/35%/50%+ above fair value.</div>
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-3">
                  {/* Altman Z */}
                  <Card className={`border-2 ${scoreBg(data.altman_z.color)}`}>
                    <CardHeader className="pb-1">
                      <CardTitle className="text-sm font-bold">Altman Z-Score</CardTitle>
                      <p className="text-xs text-muted-foreground mt-0.5">Bankruptcy risk within 2 years</p>
                    </CardHeader>
                    <CardContent>
                      <div className={`text-4xl font-bold tabular-nums ${scoreColor(data.altman_z.color)}`}>
                        {data.altman_z.score !== null ? data.altman_z.score.toFixed(3) : "N/A"}
                      </div>
                      <div className={`mt-1 text-sm font-bold ${scoreColor(data.altman_z.color)}`}>
                        {data.altman_z.zone}
                      </div>
                      <Separator className="my-3" />
                      <div className="space-y-2 text-xs">
                        {[
                          { range: "> 2.99", label: "Safe Zone", desc: "Low risk of financial distress", col: "text-emerald-600 dark:text-emerald-400" },
                          { range: "1.81–2.99", label: "Grey Zone", desc: "Elevated risk — watch closely", col: "text-amber-600 dark:text-amber-400" },
                          { range: "< 1.81", label: "Distress Zone", desc: "High risk of bankruptcy", col: "text-red-600 dark:text-red-400" },
                        ].map(z => (
                          <div key={z.range} className={`rounded-lg px-2.5 py-1.5 ${data.altman_z.zone === z.label ? "bg-current/10 border border-current/20" : ""}`} style={data.altman_z.zone === z.label ? {} : {}}>
                            <div className="flex justify-between items-center">
                              <span className={`font-semibold ${z.col}`}>{z.range} → {z.label}</span>
                            </div>
                            <div className="text-muted-foreground mt-0.5">{z.desc}</div>
                          </div>
                        ))}
                      </div>
                      {data.altman_z.components && (
                        <>
                          <Separator className="my-3" />
                          <p className="text-xs font-semibold mb-2">Components</p>
                          <div className="space-y-1">
                            {[
                              { k: "X1_liquidity", label: "X1 — Working Capital / Assets", desc: "Liquidity buffer" },
                              { k: "X2_profitability", label: "X2 — Retained Earnings / Assets", desc: "Long-term profitability" },
                              { k: "X3_operating", label: "X3 — EBIT / Assets", desc: "Operating efficiency" },
                              { k: "X4_solvency", label: "X4 — Market Cap / Total Debt", desc: "Debt coverage" },
                              { k: "X5_turnover", label: "X5 — Revenue / Assets", desc: "Asset utilisation" },
                            ].map(({ k, label, desc }) => {
                              const v = data.altman_z.components![k];
                              if (v === undefined) return null;
                              return (
                                <div key={k} className="flex justify-between text-xs gap-2">
                                  <div>
                                    <div className="text-foreground font-medium">{label}</div>
                                    <div className="text-muted-foreground">{desc}</div>
                                  </div>
                                  <span className="font-mono font-bold shrink-0">{(v as number).toFixed(3)}</span>
                                </div>
                              );
                            })}
                          </div>
                        </>
                      )}
                    </CardContent>
                  </Card>

                  {/* Piotroski F */}
                  <Card className={`border-2 ${scoreBg(data.piotroski_f.color)}`}>
                    <CardHeader className="pb-1">
                      <CardTitle className="text-sm font-bold">Piotroski F-Score</CardTitle>
                      <p className="text-xs text-muted-foreground mt-0.5">Business quality across 9 signals</p>
                    </CardHeader>
                    <CardContent>
                      <div className={`text-4xl font-bold ${scoreColor(data.piotroski_f.color)}`}>
                        {data.piotroski_f.score !== null ? `${data.piotroski_f.score}/9` : "N/A"}
                      </div>
                      <div className={`mt-1 text-sm font-bold ${scoreColor(data.piotroski_f.color)}`}>
                        {data.piotroski_f.verdict}
                      </div>
                      <Separator className="my-3" />
                      <div className="space-y-2 text-xs">
                        {[
                          { range: "7–9", label: "Strong", desc: "Improving fundamentals, low risk", col: "text-emerald-600 dark:text-emerald-400" },
                          { range: "4–6", label: "Neutral", desc: "Mixed signals — neither great nor bad", col: "text-amber-600 dark:text-amber-400" },
                          { range: "0–3", label: "Weak", desc: "Deteriorating financials, avoid", col: "text-red-600 dark:text-red-400" },
                        ].map(z => (
                          <div key={z.range}>
                            <span className={`font-semibold ${z.col}`}>{z.range} → {z.label}</span>
                            <div className="text-muted-foreground">{z.desc}</div>
                          </div>
                        ))}
                      </div>
                      <Separator className="my-3" />
                      <p className="text-xs text-muted-foreground">Evaluates: profitability (ROA, OCF, ROA trend, accruals), leverage (debt reduction, liquidity improvement, dilution), and efficiency (margin trend, asset turnover).</p>
                    </CardContent>
                  </Card>

                  {/* Sloan */}
                  <Card className={`border-2 ${scoreBg(data.sloan.color)}`}>
                    <CardHeader className="pb-1">
                      <CardTitle className="text-sm font-bold">Sloan Accrual Ratio</CardTitle>
                      <p className="text-xs text-muted-foreground mt-0.5">Are profits backed by real cash?</p>
                    </CardHeader>
                    <CardContent>
                      <div className={`text-4xl font-bold tabular-nums ${scoreColor(data.sloan.color)}`}>
                        {data.sloan.ratio !== null ? `${data.sloan.ratio}%` : "N/A"}
                      </div>
                      <div className={`mt-1 text-sm font-bold ${scoreColor(data.sloan.color)}`}>
                        {data.sloan.quality}
                      </div>
                      <Separator className="my-3" />
                      <div className="space-y-2 text-xs">
                        {[
                          { range: "< −10%", label: "Very High Quality", desc: "Cash flows far exceed reported profits — very reliable earnings", col: "text-emerald-600 dark:text-emerald-400" },
                          { range: "−10–0%", label: "High Quality", desc: "Profits backed by cash — earnings are real", col: "text-emerald-600 dark:text-emerald-400" },
                          { range: "0–10%", label: "Moderate", desc: "Some accruals — monitor trends", col: "text-amber-600 dark:text-amber-400" },
                          { range: "> 10%", label: "Low Quality ⚠", desc: "Profits significantly exceed cash flow — likely accounting inflation. Stocks with high accruals historically underperform.", col: "text-red-600 dark:text-red-400" },
                        ].map(z => (
                          <div key={z.range}>
                            <span className={`font-semibold ${z.col}`}>{z.range} → {z.label}</span>
                            <div className="text-muted-foreground">{z.desc}</div>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                </div>

                {/* Piotroski breakdown */}
                {data.piotroski_f.breakdown && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-bold">Piotroski F-Score — Detailed Breakdown</CardTitle>
                      <p className="text-xs text-muted-foreground">Each signal scores 1 (pass) or 0 (fail). Total out of 9.</p>
                    </CardHeader>
                    <CardContent>
                      <PiotroskiCriteria breakdown={data.piotroski_f.breakdown} />
                    </CardContent>
                  </Card>
                )}

                {/* ── Advanced Valuation Frameworks ── */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base font-bold">Advanced Valuation Frameworks</CardTitle>
                    <p className="text-xs text-muted-foreground">10 quantitative models: manipulation detection, reverse DCF, Greenblatt, Graham, Buffett, and DuPont decomposition.</p>
                  </CardHeader>
                  <CardContent className="space-y-4">

                    {/* Row 1: Beneish + Reverse DCF */}
                    <div className="grid gap-4 md:grid-cols-2">

                      {/* Beneish M-Score */}
                      {data.beneish_m ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.beneish_m.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">Beneish M-Score</div>
                              <div className="text-xs text-muted-foreground">Earnings manipulation detection</div>
                            </div>
                            <VerdictBadge verdict={data.beneish_m.verdict} color={data.beneish_m.color} />
                          </div>
                          <div className={`text-3xl font-bold tabular-nums mb-2 ${scoreColor(data.beneish_m.color)}`}>
                            {data.beneish_m.score.toFixed(2)}
                          </div>
                          <div className="text-xs text-muted-foreground mb-2">Threshold: &gt; −1.78 = likely manipulator | &lt; −2.22 = clean</div>
                          <div className="grid grid-cols-4 gap-1">
                            {Object.entries(data.beneish_m.components).map(([k, v]) => (
                              <div key={k} className="text-center rounded bg-zinc-800/60 p-1">
                                <div className="text-[10px] font-bold text-muted-foreground">{k}</div>
                                <div className="text-xs font-mono">{v.toFixed(2)}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">Beneish M-Score</div>
                          <div className="text-xs text-muted-foreground">Insufficient data (requires 2 years of financials)</div>
                        </div>
                      )}

                      {/* Reverse DCF */}
                      {data.reverse_dcf ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.reverse_dcf.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">Reverse DCF</div>
                              <div className="text-xs text-muted-foreground">Implied FCF growth priced in</div>
                            </div>
                            <VerdictBadge verdict={data.reverse_dcf.assessment} color={data.reverse_dcf.color} />
                          </div>
                          <div className={`text-3xl font-bold tabular-nums mb-1 ${scoreColor(data.reverse_dcf.color)}`}>
                            {data.reverse_dcf.implied_growth_rate.toFixed(1)}%<span className="text-lg font-normal">/yr</span>
                          </div>
                          <div className="text-xs text-muted-foreground">{data.reverse_dcf.interpretation}</div>
                          <div className="mt-2 text-xs text-muted-foreground">
                            &gt;50% = Priced for perfection · 25–50% = High · 10–25% = Moderate · &lt;10% = Low
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">Reverse DCF</div>
                          <div className="text-xs text-muted-foreground">Insufficient data (requires positive FCF)</div>
                        </div>
                      )}
                    </div>

                    {/* Row 2: Magic Formula + ROIC vs WACC */}
                    <div className="grid gap-4 md:grid-cols-2">

                      {/* Magic Formula */}
                      {data.magic_formula ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.magic_formula.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">Magic Formula (Greenblatt)</div>
                              <div className="text-xs text-muted-foreground">Earnings yield + Return on invested capital</div>
                            </div>
                            <VerdictBadge verdict={data.magic_formula.verdict} color={data.magic_formula.color} />
                          </div>
                          <div className="grid grid-cols-2 gap-3 mt-2">
                            <div>
                              <div className="text-xs text-muted-foreground">Earnings Yield</div>
                              <div className={`text-2xl font-bold ${scoreColor(data.magic_formula.color)}`}>
                                {data.magic_formula.earnings_yield.toFixed(1)}%
                              </div>
                              <div className="text-[10px] text-muted-foreground">EBIT / Enterprise Value</div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">ROIC</div>
                              <div className={`text-2xl font-bold ${scoreColor(data.magic_formula.color)}`}>
                                {data.magic_formula.roic.toFixed(1)}%
                              </div>
                              <div className="text-[10px] text-muted-foreground">EBIT / Invested Capital</div>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">Magic Formula (Greenblatt)</div>
                          <div className="text-xs text-muted-foreground">Insufficient data</div>
                        </div>
                      )}

                      {/* ROIC vs WACC */}
                      {data.roic_wacc ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.roic_wacc.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">ROIC vs WACC</div>
                              <div className="text-xs text-muted-foreground">Is the company creating or destroying value?</div>
                            </div>
                            <VerdictBadge verdict={data.roic_wacc.verdict} color={data.roic_wacc.color} />
                          </div>
                          <div className="grid grid-cols-3 gap-2 mt-2">
                            <div>
                              <div className="text-xs text-muted-foreground">ROIC</div>
                              <div className={`text-xl font-bold ${scoreColor(data.roic_wacc.color)}`}>{data.roic_wacc.roic.toFixed(1)}%</div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">WACC</div>
                              <div className="text-xl font-bold text-muted-foreground">{data.roic_wacc.wacc.toFixed(1)}%</div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">Spread</div>
                              <div className={`text-xl font-bold ${data.roic_wacc.spread > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                                {data.roic_wacc.spread > 0 ? "+" : ""}{data.roic_wacc.spread.toFixed(1)}%
                              </div>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">ROIC vs WACC</div>
                          <div className="text-xs text-muted-foreground">Insufficient data</div>
                        </div>
                      )}
                    </div>

                    {/* Row 3: EPV + PEG */}
                    <div className="grid gap-4 md:grid-cols-2">

                      {/* EPV */}
                      {data.epv ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.epv.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">EPV (Earnings Power Value)</div>
                              <div className="text-xs text-muted-foreground">No-growth intrinsic value (Greenwald)</div>
                            </div>
                            <VerdictBadge verdict={data.epv.verdict} color={data.epv.color} />
                          </div>
                          <div className={`text-3xl font-bold tabular-nums mb-1 ${scoreColor(data.epv.color)}`}>
                            {data.epv.epv_per_share !== null ? `$${data.epv.epv_per_share.toFixed(2)}` : "N/A"}
                            <span className="text-base font-normal text-muted-foreground">/share</span>
                          </div>
                          {data.epv.growth_premium_pct !== null && (
                            <div className="text-xs text-muted-foreground">
                              Growth premium paid: <span className={`font-semibold ${data.epv.growth_premium_pct > 50 ? "text-red-500" : data.epv.growth_premium_pct > 0 ? "text-amber-500" : "text-emerald-500"}`}>
                                {data.epv.growth_premium_pct > 0 ? "+" : ""}{data.epv.growth_premium_pct.toFixed(1)}%
                              </span>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">EPV (Earnings Power Value)</div>
                          <div className="text-xs text-muted-foreground">Insufficient data</div>
                        </div>
                      )}

                      {/* PEG Ratio */}
                      {data.peg_ratio ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.peg_ratio.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">PEG Ratio (Lynch)</div>
                              <div className="text-xs text-muted-foreground">Growth-adjusted valuation</div>
                            </div>
                            <VerdictBadge verdict={data.peg_ratio.verdict} color={data.peg_ratio.color} />
                          </div>
                          <div className={`text-3xl font-bold tabular-nums ${scoreColor(data.peg_ratio.color)}`}>
                            {data.peg_ratio.peg.toFixed(2)}x
                          </div>
                          <div className="mt-2 text-xs text-muted-foreground">
                            &lt;1 = Undervalued · 1–2 = Fair · 2–3 = Expensive · &gt;3 = Very expensive
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">PEG Ratio (Lynch)</div>
                          <div className="text-xs text-muted-foreground">Requires positive P/E and earnings growth</div>
                        </div>
                      )}
                    </div>

                    {/* Row 4: Net-Net + Owner Earnings */}
                    <div className="grid gap-4 md:grid-cols-2">

                      {/* Net-Net */}
                      {data.net_net ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.net_net.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">Net-Net Working Capital (Graham)</div>
                              <div className="text-xs text-muted-foreground">Current assets minus all liabilities per share</div>
                            </div>
                            <VerdictBadge verdict={data.net_net.verdict} color={data.net_net.color} />
                          </div>
                          <div className={`text-3xl font-bold tabular-nums mb-1 ${scoreColor(data.net_net.color)}`}>
                            ${data.net_net.nnwc_per_share.toFixed(2)}<span className="text-base font-normal text-muted-foreground">/share</span>
                          </div>
                          {data.net_net.vs_price_pct !== null && (
                            <div className="text-xs text-muted-foreground">
                              vs current price: <span className={`font-semibold ${data.net_net.vs_price_pct > 0 ? "text-emerald-500" : "text-red-500"}`}>
                                {data.net_net.vs_price_pct > 0 ? "+" : ""}{data.net_net.vs_price_pct.toFixed(1)}%
                              </span>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">Net-Net (Graham)</div>
                          <div className="text-xs text-muted-foreground">Insufficient balance sheet data</div>
                        </div>
                      )}

                      {/* Owner Earnings */}
                      {data.owner_earnings ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.owner_earnings.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">Owner Earnings (Buffett)</div>
                              <div className="text-xs text-muted-foreground">Net income + D&A − CapEx (true cash return)</div>
                            </div>
                            <VerdictBadge verdict={data.owner_earnings.verdict} color={data.owner_earnings.color} />
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <div className="text-xs text-muted-foreground">Per Share</div>
                              <div className={`text-xl font-bold ${scoreColor(data.owner_earnings.color)}`}>
                                {data.owner_earnings.owner_earnings_per_share !== null ? `$${data.owner_earnings.owner_earnings_per_share.toFixed(2)}` : "N/A"}
                              </div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">OE Yield</div>
                              <div className={`text-xl font-bold ${scoreColor(data.owner_earnings.color)}`}>
                                {data.owner_earnings.owner_earnings_yield !== null ? `${data.owner_earnings.owner_earnings_yield.toFixed(1)}%` : "N/A"}
                              </div>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">Owner Earnings (Buffett)</div>
                          <div className="text-xs text-muted-foreground">Insufficient data</div>
                        </div>
                      )}
                    </div>

                    {/* Row 5: DuPont + Rule of 40 */}
                    <div className="grid gap-4 md:grid-cols-2">

                      {/* DuPont */}
                      {data.dupont ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.dupont.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">DuPont ROE Decomposition</div>
                              <div className="text-xs text-muted-foreground">Retained annual flows and ending balances</div>
                            </div>
                            <VerdictBadge verdict={data.dupont.quality} color={data.dupont.color} />
                          </div>
                          <div className={`text-3xl font-bold tabular-nums mb-3 ${scoreColor(data.dupont.color)}`}>
                            {data.dupont.roe.toFixed(1)}% ROE
                          </div>
                          <div className="text-xs text-muted-foreground mb-2 font-mono">
                            = {data.dupont.net_margin.toFixed(1)}% margin × {data.dupont.asset_turnover.toFixed(2)}× turnover × {data.dupont.equity_multiplier.toFixed(2)}× leverage
                          </div>
                          <div className="grid grid-cols-3 gap-1">
                            {[
                              { label: "Net Margin", value: `${data.dupont.net_margin.toFixed(1)}%`, desc: "Profitability" },
                              { label: "Asset Turnover", value: `${data.dupont.asset_turnover.toFixed(2)}×`, desc: "Efficiency" },
                              { label: "Equity Mult.", value: `${data.dupont.equity_multiplier.toFixed(2)}×`, desc: "Leverage" },
                            ].map(item => (
                              <div key={item.label} className="rounded bg-zinc-800/60 p-2 text-center">
                                <div className="text-[10px] text-muted-foreground">{item.label}</div>
                                <div className="text-sm font-bold">{item.value}</div>
                                <div className="text-[9px] text-muted-foreground">{item.desc}</div>
                              </div>
                            ))}
                          </div>
                          <div className="mt-2 text-xs font-medium">
                            Dominant driver: <span className={scoreColor(data.dupont.color)}>{data.dupont.dominant_driver}</span>
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">DuPont Analysis</div>
                          <div className="text-xs text-muted-foreground">Insufficient data</div>
                        </div>
                      )}

                      {/* Rule of 40 */}
                      {data.rule_of_40 ? (
                        <div className={`rounded-xl border-2 p-4 ${scoreBg(data.rule_of_40.color)}`}>
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div>
                              <div className="font-bold text-sm">Rule of 40</div>
                              <div className="text-xs text-muted-foreground">Revenue growth% + Operating margin% (SaaS benchmark)</div>
                            </div>
                            <VerdictBadge verdict={data.rule_of_40.verdict} color={data.rule_of_40.color} />
                          </div>
                          <div className={`text-4xl font-bold tabular-nums mb-2 ${scoreColor(data.rule_of_40.color)}`}>
                            {data.rule_of_40.score.toFixed(0)}
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <div className="text-xs text-muted-foreground">Revenue Growth</div>
                              <div className="text-lg font-bold">{data.rule_of_40.revenue_growth_pct.toFixed(1)}%</div>
                            </div>
                            <div>
                              <div className="text-xs text-muted-foreground">Op. Margin</div>
                              <div className="text-lg font-bold">{data.rule_of_40.op_margin_pct.toFixed(1)}%</div>
                            </div>
                          </div>
                          <div className="mt-2 text-xs text-muted-foreground">
                            ≥60 = Elite · 40–60 = Healthy · 20–40 = Below Par · &lt;20 = Underperforming
                          </div>
                        </div>
                      ) : (
                        <div className="rounded-xl border-2 p-4 bg-muted border-border">
                          <div className="font-bold text-sm mb-1">Rule of 40</div>
                          <div className="text-xs text-muted-foreground">Insufficient data</div>
                        </div>
                      )}
                    </div>

                  </CardContent>
                </Card>

              </TabsContent>

              {/* ── Red Flags tab ── */}
              <TabsContent value="flags" className="mt-4">
                <div className="mb-4 flex flex-wrap gap-2">
                  <Badge className="bg-red-600 text-white">{data.red_flags.high} High</Badge>
                  <Badge className="bg-amber-500 text-white">{data.red_flags.medium} Medium</Badge>
                  <Badge variant="secondary">{data.red_flags.total} Total</Badge>
                </div>

                {data.red_flags.flags.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-6 dark:border-emerald-800 dark:bg-emerald-950">
                    <CheckCircle className="size-5 text-emerald-600 dark:text-emerald-400" />
                    <span className="text-emerald-800 dark:text-emerald-200">No red flags detected.</span>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {data.red_flags.flags.map((flag, i) => (
                      <div
                        key={i}
                        className={`rounded-xl border px-4 py-3 ${severityBg(flag.severity)}`}
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          {severityBadge(flag.severity)}
                          <code className="text-xs font-mono font-semibold">
                            {flag.code.replace(/_/g, " ")}
                          </code>
                        </div>
                        <p className="mt-1.5 text-sm text-muted-foreground">{flag.detail}</p>
                      </div>
                    ))}
                  </div>
                )}
              </TabsContent>

              {/* ── Filings tab ── */}
              <TabsContent value="filings" className="mt-4 space-y-4">
                {filings?.notice && !filingsLoading && <p role="status" className="rounded-lg border border-amber-800 p-3 text-sm text-amber-300">
                  {filings.notice} {filings.cache_as_of && <>Saved as of {filings.cache_as_of}.</>}
                </p>}
                {filingsLoading && (
                  <div className="space-y-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <Skeleton key={i} className="h-14 w-full rounded-xl" />
                    ))}
                  </div>
                )}

                {filingsError && !filingsLoading && (
                  <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                    <AlertTriangle className="size-4 shrink-0" />
                    EDGAR data unavailable: {filingsError}
                  </div>
                )}

                {filings && filings.summary && !filingsLoading && (
                  <>
                    {/* Summary cards */}
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <Card className="gap-1">
                        <CardContent className="pt-0">
                          <div className="text-xs text-muted-foreground">Last 10-K Filed</div>
                          <div className="text-sm font-semibold">
                            {filings.summary.last_10k?.filed_date ?? "N/A"}
                          </div>
                          {filings.summary.last_10k?.period_of_report && (
                            <div className="text-xs text-muted-foreground">
                              Period: {filings.summary.last_10k.period_of_report}
                            </div>
                          )}
                          {filings.summary.last_10k && (
                            <a href={filings.summary.last_10k.url} target="_blank" rel="noopener noreferrer" className="mt-1 flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400">
                              <ExternalLink className="size-3" /> View
                            </a>
                          )}
                        </CardContent>
                      </Card>

                      <Card className="gap-1">
                        <CardContent className="pt-0">
                          <div className="text-xs text-muted-foreground">Last 10-Q Filed</div>
                          <div className="text-sm font-semibold">
                            {filings.summary.last_10q?.filed_date ?? "N/A"}
                          </div>
                          {filings.summary.last_10q?.period_of_report && (
                            <div className="text-xs text-muted-foreground">
                              Period: {filings.summary.last_10q.period_of_report}
                            </div>
                          )}
                          {filings.summary.last_10q && (
                            <a href={filings.summary.last_10q.url} target="_blank" rel="noopener noreferrer" className="mt-1 flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400">
                              <ExternalLink className="size-3" /> View
                            </a>
                          )}
                        </CardContent>
                      </Card>

                      <Card className="gap-1">
                        <CardContent className="pt-0">
                          <div className="text-xs text-muted-foreground">8-K Events (90 days)</div>
                          <div className="text-2xl font-bold">{filings.summary.recent_8k_count}</div>
                          <div className="text-xs text-muted-foreground">material events</div>
                        </CardContent>
                      </Card>

                      <Card className="gap-1">
                        <CardContent className="pt-0">
                          <div className="text-xs text-muted-foreground">Insider Trans. (90 days)</div>
                          <div className="text-2xl font-bold">{filings.summary.recent_form4_count}</div>
                          <div className="text-xs text-muted-foreground">Form 4 filings</div>
                        </CardContent>
                      </Card>
                    </div>

                    {/* Filing timeline */}
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm">
                          Filing Timeline
                          <span className="ml-2 text-xs font-normal text-muted-foreground">
                            — {filings.total} tracked filings · {filings.cached ? "from cache" : "live from EDGAR"}
                          </span>
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        <div className="space-y-1.5">
                          {filings.filings.map((f, i) => (
                            <div
                              key={i}
                              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 transition-colors hover:bg-muted/40"
                            >
                              <div className="flex flex-wrap items-center gap-2 min-w-0">
                                {formTypeBadge(f.form_type)}
                                <span className="truncate text-sm">{f.description || f.form_type}</span>
                                {f.period_of_report && (
                                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                                    Period: {f.period_of_report}
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-3 shrink-0">
                                <span className="text-xs text-muted-foreground">{f.filed_date}</span>
                                <a
                                  href={f.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400"
                                >
                                  EDGAR <ExternalLink className="size-3" />
                                </a>
                              </div>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  </>
                )}

                {filings && filings.total === 0 && !filingsLoading && !filingsError && (
                  <div className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-8 text-sm text-muted-foreground">
                    <FileText className="size-4 shrink-0" />
                    No tracked filings found for this ticker in EDGAR.
                  </div>
                )}
              </TabsContent>

              {/* ── Deep Analysis tab ── */}
              <TabsContent value="deep-analysis" className="mt-4 space-y-4">
                {/* CTA when not yet run */}
                {!deepLoading && !deepText && !deepError && (
                  <Card>
                    <CardContent className="py-10 flex flex-col items-center gap-4 text-center">
                      <div className="rounded-full bg-muted p-4">
                        <FileText className="size-8 text-muted-foreground" />
                      </div>
                      <div>
                        <h3 className="font-semibold text-base">Filing Deep Analysis</h3>
                        <p className="mt-1 text-sm text-muted-foreground max-w-md">
                          Downloads the actual 10-K, 10-Q, and 8-K documents from SEC EDGAR, extracts
                          MD&amp;A, risk factors, and results of operations, then asks Qwen2.5 to
                          derive a price target and buy/sell/hold signal from the filing content.
                        </p>
                      </div>
                      <Button onClick={() => startFilingAnalysis(data.ticker)} size="lg">
                        <Brain className="size-4 mr-2" />
                        Analyze SEC Filings
                      </Button>
                    </CardContent>
                  </Card>
                )}

                {/* Loading state */}
                {deepLoading && (
                  <Card>
                    <CardContent className="py-5">
                      <div className="flex items-center gap-3 text-muted-foreground">
                        <div className="size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent" />
                        <span className="text-sm">
                          Fetching EDGAR filings and analyzing with Qwen2.5 — this may take 30–90 seconds…
                        </span>
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* Error */}
                {deepError && (
                  <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
                    <AlertTriangle className="size-4 shrink-0" />
                    {deepError}
                  </div>
                )}

                {/* Verdict card */}
                {deepVerdict && deepMeta && (
                  <VerdictCard verdict={deepVerdict} meta={deepMeta} />
                )}

                {/* Filings analyzed */}
                {deepMeta && deepMeta.filings_analyzed.length > 0 && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Documents Analyzed</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-2">
                        {deepMeta.filings_analyzed.map((f, i) => (
                          <a
                            key={i}
                            href={f.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-2 rounded-lg border px-3 py-2 text-xs hover:bg-muted/40 transition-colors"
                          >
                            {formTypeBadge(f.type.split(" ")[0])}
                            <span className="font-medium">{f.type}</span>
                            <span className="text-muted-foreground">·</span>
                            <span className="text-muted-foreground">{f.filed_date}</span>
                            {f.period && (
                              <>
                                <span className="text-muted-foreground">·</span>
                                <span className="text-muted-foreground">Period: {f.period}</span>
                              </>
                            )}
                            <ExternalLink className="size-3 text-muted-foreground" />
                          </a>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* Analysis narrative */}
                {deepText && (
                  <Card>
                    <CardHeader className="pb-2">
                      <div className="flex items-center justify-between">
                        <CardTitle className="text-sm flex items-center gap-2">
                          <Brain className="size-4" />
                          AI Filing Analysis
                          <span className="text-xs font-normal text-muted-foreground">(Qwen2.5:7b)</span>
                        </CardTitle>
                        {!deepLoading && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => startFilingAnalysis(data.ticker)}
                          >
                            Re-run
                          </Button>
                        )}
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="prose prose-sm dark:prose-invert max-w-none">
                        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
                          {deepText.trimStart().startsWith("{")
                            ? deepText.slice(deepText.indexOf("\n") + 1)
                            : deepText}
                          {deepLoading && (
                            <span className="inline-block h-4 w-0.5 animate-pulse bg-foreground align-middle ml-0.5" />
                          )}
                        </pre>
                      </div>
                    </CardContent>
                  </Card>
                )}
              </TabsContent>
            </Tabs>

            {/* ── AI Analysis ── */}
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                    <Brain className="size-5" />
                    AI Analysis
                    <span className="text-xs font-normal text-muted-foreground">(Ollama · qwen2.5:7b)</span>
                  </CardTitle>
                  <Button
                    size="sm"
                    onClick={() => startAI(data.ticker)}
                    disabled={aiLoading}
                    variant={aiText ? "outline" : "default"}
                  >
                    {aiLoading ? "Analyzing…" : aiText ? "Re-run" : "Run Analysis"}
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {aiError && (
                  <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
                    <AlertTriangle className="size-4 shrink-0" />
                    {aiError}
                  </div>
                )}

                {!aiText && !aiLoading && !aiError && (
                  <div className="flex items-center gap-2 rounded-lg border border-dashed px-4 py-8 text-sm text-muted-foreground">
                    <Circle className="size-4 shrink-0" />
                    Click &ldquo;Run Analysis&rdquo; to get an AI-powered investment analysis using your local Ollama model.
                  </div>
                )}

                {aiLoading && !aiText && (
                  <div className="space-y-2">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <Skeleton key={i} className={`h-4 ${i === 3 ? "w-2/3" : "w-full"}`} />
                    ))}
                  </div>
                )}

                {aiText && (
                  <div className="prose prose-sm dark:prose-invert max-w-none">
                    <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
                      {aiText}
                      {aiLoading && (
                        <span className="inline-block h-4 w-0.5 animate-pulse bg-foreground align-middle ml-0.5" />
                      )}
                    </pre>
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        )}
        </>
        </div>
      </main>
    </div>
  );
}
