"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { ArrowLeft, ChevronDown, ChevronUp, BookOpen } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ThresholdBand {
  label: string;
  color: string;
  range: string;
  description: string;
}

interface ModelComponent {
  key: string;
  name: string;
  description: string;
}

interface Model {
  id: string;
  name: string;
  subtitle: string;
  origin: string;
  year: number;
  category: "risk" | "quality" | "integrity" | "valuation";
  summary: string;
  detail: string;
  thresholds: ThresholdBand[];
  components?: ModelComponent[];
  whyItMatters: string;
}

// ─── Category Config ──────────────────────────────────────────────────────────

const CATEGORIES = {
  risk:      { label: "Risk",      color: "text-red-400",     bg: "bg-red-500/10",     border: "border-red-500/20",     dot: "bg-red-400",     glow: "hover:border-red-500/40 hover:shadow-red-500/8"     },
  quality:   { label: "Quality",   color: "text-blue-400",    bg: "bg-blue-500/10",    border: "border-blue-500/20",    dot: "bg-blue-400",    glow: "hover:border-blue-500/40 hover:shadow-blue-500/8"    },
  integrity: { label: "Integrity", color: "text-purple-400",  bg: "bg-purple-500/10",  border: "border-purple-500/20",  dot: "bg-purple-400",  glow: "hover:border-purple-500/40 hover:shadow-purple-500/8"  },
  valuation: { label: "Valuation", color: "text-emerald-400", bg: "bg-emerald-500/10", border: "border-emerald-500/20", dot: "bg-emerald-400", glow: "hover:border-emerald-500/40 hover:shadow-emerald-500/8" },
};

// ─── Model Data ───────────────────────────────────────────────────────────────

const MODELS: Model[] = [
  {
    id: "altman-z",
    name: "Altman Z-Score",
    subtitle: "Bankruptcy risk within 2 years",
    origin: "Edward Altman",
    year: 1968,
    category: "risk",
    summary: "A weighted formula combining 5 financial ratios that predicts the probability of a company entering bankruptcy within two years. Developed by NYU professor Edward Altman by studying 66 manufacturing firms — half bankrupt, half solvent.",
    detail: "Altman originally published this model in the Journal of Finance in 1968 as a way to quantify what credit analysts were doing intuitively. It became one of the most widely used corporate distress models in finance and is still cited in credit agreements and bond covenants today. The original model was designed for publicly traded manufacturers; variants exist for private firms and non-manufacturers.",
    thresholds: [
      { label: "Safe Zone",     color: "bg-emerald-500", range: "> 2.99",      description: "Low probability of bankruptcy. Financially sound." },
      { label: "Grey Zone",     color: "bg-yellow-500",  range: "1.81 – 2.99", description: "Elevated risk — watch closely. Distress is possible." },
      { label: "Distress Zone", color: "bg-red-500",     range: "< 1.81",      description: "High bankruptcy probability within 2 years." },
    ],
    components: [
      { key: "X1", name: "Working Capital / Total Assets",       description: "Measures short-term liquidity. Negative working capital signals potential cash crisis." },
      { key: "X2", name: "Retained Earnings / Total Assets",     description: "Captures cumulative profitability. Low or negative retained earnings signal chronic losses." },
      { key: "X3", name: "EBIT / Total Assets",                  description: "Operating efficiency — how much profit the asset base generates before interest and tax." },
      { key: "X4", name: "Market Cap / Total Liabilities",       description: "Market-based solvency: how much equity cushion exists relative to total debt obligations." },
      { key: "X5", name: "Revenue / Total Assets",               description: "Asset utilisation — how efficiently the company turns its assets into sales." },
    ],
    whyItMatters: "We use Altman Z as a hard-stop filter. A Distress Zone score is a red flag regardless of how attractive the valuation looks — a company heading toward bankruptcy erases all upside.",
  },
  {
    id: "piotroski",
    name: "Piotroski F-Score",
    subtitle: "Business quality across 9 binary signals",
    origin: "Joseph Piotroski",
    year: 2000,
    category: "quality",
    summary: "A 9-point checklist that scores a company's improving or deteriorating fundamentals across three dimensions: profitability, leverage/solvency, and operating efficiency. Each signal passes (1) or fails (0) — no partial credit.",
    detail: "Stanford accounting professor Joseph Piotroski published this model in the Journal of Accounting Research. He showed that buying high book-to-market stocks with high F-Scores (7–9) and shorting those with low F-Scores (0–2) generated 23% annual returns. The model's power comes from its focus on change — it rewards improving businesses, not just good ones.",
    thresholds: [
      { label: "Strong",  color: "bg-emerald-500", range: "7 – 9", description: "Improving fundamentals across most dimensions. Low financial risk." },
      { label: "Neutral", color: "bg-yellow-500",  range: "4 – 6", description: "Mixed signals. Neither great nor bad. Requires deeper review." },
      { label: "Weak",    color: "bg-red-500",     range: "0 – 3", description: "Deteriorating financials. Multiple red flags. High risk." },
    ],
    components: [
      { key: "ROA",        name: "Positive ROA (Profitability)",           description: "Net income / Total assets > 0. Is the company profitable at all?" },
      { key: "OCF",        name: "Positive Operating Cash Flow",           description: "Core operations generate real cash — not just accounting profit." },
      { key: "ΔROA",       name: "Improving ROA",                         description: "Profitability is trending up year-over-year." },
      { key: "Accrual",    name: "Cash Flow > Net Income",                description: "Operating cash flow exceeds net income — conservative, high-quality earnings." },
      { key: "Leverage",   name: "Decreasing Long-Term Debt Ratio",       description: "Less debt relative to assets than last year. Deleveraging is a strength signal." },
      { key: "Liquidity",  name: "Improving Current Ratio",               description: "Better ability to cover short-term obligations than a year ago." },
      { key: "Dilution",   name: "No New Share Issuance",                 description: "No new shares issued. Existing investors were not diluted." },
      { key: "Margin",     name: "Improving Gross Margin",                description: "Keeping more of each revenue dollar — pricing power or cost discipline." },
      { key: "Turnover",   name: "Improving Asset Turnover",              description: "Generating more revenue from the same asset base. Improving operational efficiency." },
    ],
    whyItMatters: "Piotroski isolates companies actively improving, not just currently good. A high score combined with a low valuation is one of the strongest signals in quantitative finance.",
  },
  {
    id: "sloan",
    name: "Sloan Accrual Ratio",
    subtitle: "Are profits backed by real cash?",
    origin: "Richard Sloan",
    year: 1996,
    category: "quality",
    summary: "Measures the gap between net income and operating cash flow relative to assets. Large positive accruals (profits far exceeding cash flow) suggest accounting inflation. Sloan found that high-accrual stocks significantly underperform low-accrual stocks in the following year.",
    detail: "Published in The Accounting Review, Sloan's research documented what he called the 'accrual anomaly' — markets systematically overprice high-accrual stocks because investors fixate on net income rather than cash flow. The ratio exposes when a company is boosting reported earnings through accounting choices (deferring expenses, aggressively recognising revenue) rather than real cash generation.",
    thresholds: [
      { label: "Very High Quality", color: "bg-emerald-600", range: "< −10%",  description: "Cash flows dramatically exceed reported profits. Extremely conservative earnings." },
      { label: "High Quality",      color: "bg-emerald-400", range: "−10 – 0%", description: "Profits backed by real cash. Earnings are trustworthy." },
      { label: "Moderate",          color: "bg-yellow-500",  range: "0 – 10%",  description: "Some accruals present. Not alarming but worth monitoring." },
      { label: "Low Quality",       color: "bg-red-500",     range: "> 10%",    description: "Profits significantly exceed cash flow. Likely accounting inflation. Historical underperformance signal." },
    ],
    whyItMatters: "Sloan ratio is our primary earnings quality filter. A high score on Piotroski but a high Sloan accrual is a contradiction we take seriously — it often signals that the improvement is cosmetic.",
  },
  {
    id: "beneish",
    name: "Beneish M-Score",
    subtitle: "Statistical fraud detection across 8 variables",
    origin: "Messod Beneish",
    year: 1999,
    category: "integrity",
    summary: "A probabilistic model using 8 financial ratios to detect earnings manipulation. Famously flagged Enron as a likely manipulator before its collapse. A score above −1.78 indicates meaningful probability of manipulation.",
    detail: "Indiana University professor Messod Beneish trained this model on a sample of companies later proven to have manipulated earnings. Each variable captures a different manipulation pattern — inflating receivables, hiding cost increases, aggressive revenue recognition, or covering up declining fundamentals with acquisition-masked numbers. The model was made famous when Cornell MBAs used it to flag Enron in 1998, three years before the fraud became public.",
    thresholds: [
      { label: "Likely Clean",       color: "bg-emerald-500", range: "< −2.22", description: "Low probability of earnings manipulation." },
      { label: "Uncertain",          color: "bg-yellow-500",  range: "−2.22 – −1.78", description: "In the grey zone. Some red flags worth investigating." },
      { label: "Likely Manipulator", color: "bg-red-500",     range: "> −1.78", description: "High manipulation probability. Treat all reported numbers with skepticism." },
    ],
    components: [
      { key: "DSRI",  name: "Days Sales Receivable Index",         description: "Spike suggests revenue being recognised faster than customers are paying — a classic manipulation tactic." },
      { key: "GMI",   name: "Gross Margin Index",                  description: "Deteriorating margins create pressure to manipulate. GMI > 1 indicates declining gross margin." },
      { key: "AQI",   name: "Asset Quality Index",                 description: "Increase in non-current, non-physical assets (like goodwill) suggests deferring costs off the income statement." },
      { key: "SGI",   name: "Sales Growth Index",                  description: "High growth creates both incentive and opportunity for manipulation. Not negative alone, but dangerous in combination." },
      { key: "DEPI",  name: "Depreciation Index",                  description: "Slowing depreciation rate inflates net income. DEPI > 1 suggests assets are being depreciated more slowly." },
      { key: "SGAI",  name: "Sales, General & Admin Expenses Index", description: "Disproportionate SGA growth is inconsistent with scale — often a sign of hidden costs." },
      { key: "LVGI",  name: "Leverage Index",                      description: "Increasing leverage creates incentive to meet debt covenants through earnings management." },
      { key: "TATA",  name: "Total Accruals to Total Assets",      description: "High total accruals relative to assets directly measures the gap between accounting income and cash income." },
    ],
    whyItMatters: "We run every company through Beneish before acting on any other signal. No valuation argument matters if the numbers themselves can't be trusted.",
  },
  {
    id: "red-flags",
    name: "Forensic Red Flag System",
    subtitle: "7 rule-based checks for financial warning patterns",
    origin: "Academic forensic accounting research",
    year: 2000,
    category: "integrity",
    summary: "A battery of 7 rule-based checks that scan for patterns historically linked to deteriorating financial health, hidden risk, or earnings manipulation. Separate from Beneish — these are hard triggers on specific year-over-year ratio movements, not a probabilistic model. Each HIGH severity flag directly penalises the quant_score.",
    detail: "Forensic accounting warning patterns were systematically documented by Sloan (1996), Beneish (1999), and Dechow et al. This system translates the most robust academic findings into year-over-year comparisons requiring two consecutive years of filings — so a one-time event cannot trigger a flag. The RED FLAGS system targets patterns that Beneish and Sloan miss, particularly liquidity deterioration and receivables quality. It is the only mechanism in the system that can completely override an otherwise strong fundamental picture.",
    thresholds: [
      { label: "0 HIGH flags",   color: "bg-emerald-500", range: "0 pt penalty",    description: "Clean forensic profile. No serious warning patterns detected." },
      { label: "1 HIGH flag",    color: "bg-yellow-500",  range: "−4 pts",          description: "One significant pattern. Monitor closely." },
      { label: "2 HIGH flags",   color: "bg-orange-500",  range: "−8 pts",          description: "Multiple warning signals. Caution warranted." },
      { label: "≥ 3 HIGH flags", color: "bg-red-500",     range: "−12 to −15 pts",  description: "Maximum penalty zone. Serious forensic concerns across multiple dimensions." },
    ],
    components: [
      { key: "E/C",  name: "Earnings / Cash Divergence  [HIGH]",  description: "Net income grew >10% while operating cash flow fell >5%. Profits are not backed by real cash — classic sign of non-cash earnings inflation." },
      { key: "RECV", name: "Receivables Inflation  [HIGH]",        description: "Accounts receivable grew >1.5× revenue growth and >15% absolute. Signals aggressive revenue recognition or channel stuffing." },
      { key: "DSO",  name: "DSO Spike  [HIGH]",                    description: "Days Sales Outstanding jumped >25% and now exceeds 60 days. Customers taking much longer to pay — revenue quality is deteriorating." },
      { key: "CR",   name: "Liquidity Crisis  [HIGH]",             description: "Current ratio < 1.0. The company may be unable to meet short-term obligations without new financing." },
      { key: "D/E",  name: "High Leverage  [HIGH]",                description: "Debt/Equity > 3.0×. Highly leveraged — vulnerable to rising rates, refinancing squeezes, and covenant breaches." },
      { key: "GW",   name: "Goodwill Heavy  [MEDIUM]",             description: "Goodwill > 30% of total assets. Heavy acquisition reliance with risk of large impairment write-downs in a downturn." },
      { key: "DIL",  name: "Share Dilution  [MEDIUM]",             description: "Shares outstanding grew >3% year-over-year. Existing investors' ownership stake is being diluted — EPS headwind." },
    ],
    whyItMatters: "HIGH flags carry a direct −4 pt penalty each against quant_score (capped at −15 total). A company with 3 HIGH flags loses 12 points before any positive model is considered. MEDIUM flags are shown as informational warnings but carry no score penalty.",
  },
  {
    id: "reverse-dcf",
    name: "Reverse DCF",
    subtitle: "What growth rate is the market already pricing in?",
    origin: "Inverted DCF Framework",
    year: 2000,
    category: "valuation",
    summary: "Instead of forecasting future cash flows to arrive at a price, we start with the current market price and solve backwards — what FCF growth rate must be true for this price to be fair value? Low implied growth = low expectations = more margin of safety.",
    detail: "Traditional DCF models require you to forecast the future, introducing enormous subjective error. Reverse DCF flips this: given today's price, what growth does the market assume? If the implied growth is -2%, the market has priced in decline. If you believe the company is stable (even 0% growth), you're buying embedded margin of safety. Extremely high implied growth (>50%) means the stock is priced for perfection and any miss is punished severely.",
    thresholds: [
      { label: "Priced for Perfection", color: "bg-red-500",     range: "> 50%/yr",  description: "Enormous embedded expectations. No room for execution error." },
      { label: "High Expectations",     color: "bg-orange-500",  range: "25 – 50%",  description: "Strong growth already baked in. Requires continued outperformance." },
      { label: "Moderate",              color: "bg-yellow-500",  range: "10 – 25%",  description: "Reasonable growth priced in. Fair to moderately demanding valuation." },
      { label: "Low Expectations",      color: "bg-emerald-500", range: "< 10%/yr",  description: "Market expects little. A stable business at these levels offers significant upside." },
    ],
    whyItMatters: "Reverse DCF keeps us grounded. A cheap-looking P/E can still be expensive if it implies 30% perpetual growth. This model checks the assumptions baked into the price.",
  },
  {
    id: "magic-formula",
    name: "Magic Formula (Greenblatt)",
    subtitle: "Cheap price + high returns on capital",
    origin: "Joel Greenblatt",
    year: 2005,
    category: "valuation",
    summary: "Joel Greenblatt's framework from 'The Little Book That Beats the Market' ranks stocks by two factors: earnings yield (cheap) and return on invested capital (quality). Buying the intersection of cheap AND good businesses has historically outperformed the market.",
    detail: "Greenblatt, founder of Gotham Capital, designed this as a systematic approach to value investing. He showed backtested returns of 30%+ annually over 17 years (1988–2004). The key insight: most cheap stocks are cheap for a reason (bad businesses), and most good businesses are expensive. The magic is finding the overlap. Earnings yield uses EBIT/EV (more accurate than P/E because it's capital-structure neutral and uses economic earnings). ROIC uses EBIT/Invested Capital to strip out excess cash and goodwill.",
    thresholds: [
      { label: "Excellent", color: "bg-emerald-500", range: "High EY + High ROIC", description: "Cheap AND capital-efficient. The core Magic Formula target." },
      { label: "Good",      color: "bg-blue-500",    range: "High in one dimension", description: "Either cheap or high quality — but not both." },
      { label: "Avoid",     color: "bg-red-500",     range: "Low EY + Low ROIC",    description: "Expensive AND capital-inefficient. Double penalty." },
    ],
    components: [
      { key: "EY",   name: "Earnings Yield (EBIT / EV)",               description: "How much operating profit you buy per dollar of enterprise value. Capital-structure neutral — better than P/E." },
      { key: "ROIC", name: "Return on Invested Capital (EBIT / IC)",    description: "How efficiently the company generates operating profit from the capital invested in the business." },
    ],
    whyItMatters: "Magic Formula earnings yield is one of our highest-weighted valuation signals. ROIC feeds directly into the ROIC vs WACC spread calculation.",
  },
  {
    id: "roic-wacc",
    name: "ROIC vs WACC",
    subtitle: "Is management creating or destroying shareholder value?",
    origin: "Corporate Finance Theory",
    year: 1958,
    category: "valuation",
    summary: "Compares the return a company generates on its invested capital (ROIC) against the cost of that capital (WACC). A positive spread means the business is creating economic value. A negative spread means it's destroying it — regardless of reported profits.",
    detail: "Modigliani and Miller's 1958 capital structure theory laid the groundwork for understanding the cost of capital. The ROIC-WACC spread is the definitive measure of value creation. A company can be profitable and still destroy value if its cost of capital exceeds its returns — this is why accounting profit alone is insufficient. Companies with persistent positive spreads (true economic moats) tend to compound shareholder wealth over time.",
    thresholds: [
      { label: "Strong Value Creator", color: "bg-emerald-500", range: "Spread > 15%",  description: "Exceptional economic moat. Returns far exceed cost of capital." },
      { label: "Value Creator",        color: "bg-green-500",   range: "Spread 5–15%",  description: "Healthy value creation. Business earns well above its cost of capital." },
      { label: "Breakeven",            color: "bg-yellow-500",  range: "Spread 0–5%",   description: "Barely covering cost of capital. Marginal value creation." },
      { label: "Value Destroyer",      color: "bg-red-500",     range: "Spread < 0%",   description: "Destroying shareholder value. Returns below the cost of financing." },
    ],
    components: [
      { key: "ROIC", name: "Return on Invested Capital",      description: "EBIT × (1 - tax rate) / (Total Assets - Current Liabilities - Excess Cash). Measures operating return on capital deployed." },
      { key: "WACC", name: "Weighted Average Cost of Capital", description: "Blended cost of debt and equity. We use CAPM for the equity component (risk-free rate + beta × equity risk premium)." },
    ],
    whyItMatters: "ROIC-WACC spread is our primary moat indicator. A wide, persistent spread is the defining characteristic of compounders — businesses worth holding for the long term.",
  },
  {
    id: "epv",
    name: "EPV — Earnings Power Value",
    subtitle: "Intrinsic value assuming zero growth",
    origin: "Bruce Greenwald",
    year: 2001,
    category: "valuation",
    summary: "Columbia Business School professor Bruce Greenwald's framework values a business purely on its current normalised earnings power — no growth assumptions. EPV = Normalised EBIT(1-t) / WACC. Anything above EPV is growth premium; anything below EPV is a potential margin of safety.",
    detail: "Greenwald argued in 'Value Investing: From Graham to Buffett and Beyond' that growth assumptions in DCF models are unreliable and introduce speculative risk. EPV strips growth out entirely — it answers: 'What is this business worth if it never grows again?' If the market price is below EPV, you're getting growth for free. If price is 2× EPV, you're paying a 100% premium for growth that may never materialise.",
    thresholds: [
      { label: "Below EPV",     color: "bg-emerald-500", range: "Price < EPV",        description: "Buying below no-growth intrinsic value. Growth is free." },
      { label: "Near EPV",      color: "bg-blue-500",    range: "Price ≈ EPV",        description: "Fair value on a no-growth basis. Upside requires actual growth." },
      { label: "Growth Premium",color: "bg-yellow-500",  range: "Price 10–50% > EPV", description: "Paying a reasonable premium for growth expectations." },
      { label: "Speculative",   color: "bg-red-500",     range: "Price > 2× EPV",     description: "Heavily dependent on optimistic growth. High downside if growth disappoints." },
    ],
    whyItMatters: "EPV is our conservative anchor. When a stock trades at or below EPV, we have a hard valuation floor that doesn't depend on future assumptions.",
  },
  {
    id: "peg",
    name: "PEG Ratio (Lynch)",
    subtitle: "Growth-adjusted price-to-earnings",
    origin: "Peter Lynch",
    year: 1989,
    category: "valuation",
    summary: "Peter Lynch popularised PEG in 'One Up on Wall Street' as a quick sanity check on growth valuations. PEG = P/E ÷ Earnings Growth Rate. A PEG below 1.0 historically signals that you're not paying full price for the growth you're getting.",
    detail: "Lynch managed Fidelity Magellan to 29.2% annual returns from 1977–1990. He argued that a P/E ratio is meaningless without context — a P/E of 30× is cheap if the company grows 50%/yr, and expensive if it grows 5%/yr. PEG normalises for growth. Lynch's rule of thumb: PEG < 1 = undervalued, PEG = 1 = fairly valued, PEG > 2 = expensive. Note that PEG breaks down for negative or near-zero earnings growth.",
    thresholds: [
      { label: "Undervalued",   color: "bg-emerald-500", range: "< 1.0",   description: "Not paying full price for the growth. Lynch's buy signal." },
      { label: "Fair Value",    color: "bg-blue-500",    range: "1.0 – 2.0", description: "Growth is appropriately priced. No obvious bargain." },
      { label: "Expensive",     color: "bg-yellow-500",  range: "2.0 – 3.0", description: "Paying a premium for growth. Execution must be flawless." },
      { label: "Very Expensive",color: "bg-red-500",     range: "> 3.0",    description: "Pricing in years of perfect execution. High risk of disappointment." },
    ],
    whyItMatters: "PEG gives us a quick read on whether a fast-grower is actually cheap. We weight it lower than yield-based metrics because it's dependent on analyst growth estimates, which are often unreliable.",
  },
  {
    id: "net-net",
    name: "Net-Net Working Capital (Graham)",
    subtitle: "Buying assets cheaper than liquidation value",
    origin: "Benjamin Graham",
    year: 1934,
    category: "valuation",
    summary: "Benjamin Graham's ultimate deep-value screen. Net-Net Working Capital (NNWC) = Current Assets − All Liabilities. If a stock trades below NNWC, you're buying the liquid assets at a discount and getting the business for free. Graham called these 'cigar butt' investments.",
    detail: "Graham introduced this in 'Security Analysis' (1934) and 'The Intelligent Investor' (1949). He argued that buying companies below net working capital was 'almost certain to show reasonably good results' because even in liquidation, you'd recover more than you paid. The strategy worked exceptionally well in the 1930s–1960s. Today, true net-net stocks are rare in developed markets but appear occasionally in distressed situations, small-caps, and post-event dislocations.",
    thresholds: [
      { label: "Classic Net-Net",    color: "bg-emerald-500", range: "Price < NNWC",         description: "Trading below liquidation value. Graham's ultimate bargain." },
      { label: "Partial Asset Cover",color: "bg-blue-500",    range: "Price < 1.5× NNWC",   description: "Assets provide meaningful support but not full coverage." },
      { label: "Above Asset Value",  color: "bg-yellow-500",  range: "Price > 1.5× NNWC",   description: "Price relies on earnings power, not asset support." },
    ],
    whyItMatters: "NNWC is a hard floor check. A positive net-net value limits downside even in a liquidation scenario and is particularly meaningful in distressed situations.",
  },
  {
    id: "owner-earnings",
    name: "Owner Earnings (Buffett)",
    subtitle: "True cash earnings after reinvestment costs",
    origin: "Warren Buffett",
    year: 1986,
    category: "valuation",
    summary: "Buffett's preferred measure of a company's true earning power: Net Income + Depreciation/Amortisation − Maintenance CapEx. It strips out non-cash charges and adds back real cash costs that reported earnings ignore, giving the actual cash available to owners.",
    detail: "Buffett introduced this concept in his 1986 Berkshire Hathaway letter, arguing that neither GAAP earnings nor operating cash flow fully captured owner economics. D&A is a real economic cost (assets wear out) but it's also a non-cash charge — adding it back to net income and then subtracting the CapEx required to maintain competitive position gives a truer number. Berkshire's internal valuations are built almost entirely on owner earnings yield.",
    thresholds: [
      { label: "Exceptional",  color: "bg-emerald-500", range: "OE Yield > 8%",  description: "Excellent cash return on current market price. Buffett territory." },
      { label: "Attractive",   color: "bg-green-500",   range: "5 – 8%",         description: "Strong owner earnings yield. Compares favourably to bonds." },
      { label: "Adequate",     color: "bg-blue-500",    range: "2 – 5%",         description: "Acceptable. In-line with market average." },
      { label: "Uncompelling", color: "bg-yellow-500",  range: "0 – 2%",         description: "Low cash return. Requires significant growth to justify." },
      { label: "Negative",     color: "bg-red-500",     range: "< 0%",           description: "Business consumes more cash than it generates. Capital destruction." },
    ],
    whyItMatters: "Owner earnings yield is our primary cash-based valuation anchor — especially for capital-intensive businesses where D&A and CapEx diverge significantly from reported earnings.",
  },
  {
    id: "fcf-yield",
    name: "FCF Yield",
    subtitle: "Cash earnings return at today's market price",
    origin: "Graham / Buffett tradition",
    year: 1934,
    category: "valuation",
    summary: "Free Cash Flow Yield = Free Cash Flow ÷ Market Capitalisation. Measures how much real cash the business generates relative to what the market charges you to own it. The highest-weighted single input in the scoring system — up to 8 points in the Valuation bucket.",
    detail: "FCF Yield is the cash-based equivalent of earnings yield. Unlike P/E or EPS-based metrics, it uses actual cash generated (Operating Cash Flow minus CapEx) — a number that is far harder to inflate through accounting choices than GAAP net income. The concept traces back to Benjamin Graham's emphasis on cash and asset values, and was refined by Buffett in his 1986 'owner earnings' framework. FCF Yield differs from Owner Earnings Yield in that it uses the standard FCF definition (OCF − CapEx) rather than adjusting for maintenance vs growth CapEx — making it more readily comparable across companies.",
    thresholds: [
      { label: "Exceptional",   color: "bg-emerald-600", range: "≥ 8%",  description: "Outstanding cash return at current price. Maximum 8 pts — the highest single score in the entire system." },
      { label: "Strong",        color: "bg-emerald-400", range: "6–8%",  description: "Well above average. Business generates strong free cash relative to price." },
      { label: "Decent",        color: "bg-blue-500",    range: "4–6%",  description: "Reasonable yield. In line with healthy mature businesses." },
      { label: "Below Average", color: "bg-yellow-500",  range: "2–4%",  description: "Low yield. Price reflects high expectations. Limited margin of safety." },
      { label: "Very Low",      color: "bg-orange-500",  range: "0–2%",  description: "Near-zero cash return at current price. Speculation — requires exceptional growth to be justified." },
      { label: "Negative",      color: "bg-red-500",     range: "< 0%",  description: "Business burns more cash than it generates. Capital destruction — zero points, no exceptions." },
    ],
    whyItMatters: "FCF Yield receives the highest single weight in the Valuation bucket (0–8 pts) because it combines actual cash generation with today's real market price — no model assumptions, no accounting adjustments required. A ≥8% FCF Yield combined with strong Piotroski and clean Beneish is one of the most reliable buy setups in the system.",
  },
  {
    id: "dupont",
    name: "DuPont ROE Decomposition",
    subtitle: "What is actually driving profitability?",
    origin: "DuPont Corporation",
    year: 1920,
    category: "valuation",
    summary: "Breaks Return on Equity into three multiplicative components: Net Margin (profitability), Asset Turnover (efficiency), and Equity Multiplier (leverage). Identical ROE numbers can have completely different underlying drivers — DuPont reveals which.",
    detail: "Developed by Donaldson Brown at DuPont Corporation in 1920 as an internal management tool. The decomposition matters because high ROE from genuine margin and turnover (earned ROE) is fundamentally different from high ROE from financial leverage (borrowed ROE). A company borrowing heavily to inflate ROE looks identical to a genuinely efficient business in headline numbers — DuPont exposes the difference.",
    thresholds: [
      { label: "Margin-Driven",   color: "bg-emerald-500", range: "High Margin dominant",    description: "Pricing power or cost efficiency. Sustainable competitive advantage." },
      { label: "Turnover-Driven", color: "bg-blue-500",    range: "High Turnover dominant",  description: "Operational efficiency. Sustainable but lower margin business model." },
      { label: "Leverage-Driven", color: "bg-yellow-500",  range: "High Leverage dominant",  description: "Financial engineering. ROE inflated by debt — monitor closely." },
      { label: "Balanced",        color: "bg-purple-500",  range: "Balanced contribution",   description: "No single dominant driver. Healthy diversification of ROE sources." },
    ],
    components: [
      { key: "NM",  name: "Net Margin",        description: "Net Income / Revenue. What percentage of each revenue dollar flows to the bottom line." },
      { key: "AT",  name: "Asset Turnover",    description: "Revenue / Total Assets. How efficiently the company generates revenue from its asset base." },
      { key: "EM",  name: "Equity Multiplier", description: "Total Assets / Equity. Financial leverage ratio — how much debt is amplifying equity returns." },
    ],
    whyItMatters: "We use DuPont to validate ROE. High leverage-driven ROE in a cyclical business is a hidden risk. Margin or turnover-driven ROE is far more durable.",
  },
  {
    id: "rule-of-40",
    name: "Rule of 40",
    subtitle: "Growth + profitability balance for software businesses",
    origin: "SaaS Investing Community",
    year: 2015,
    category: "valuation",
    summary: "A benchmark for software and subscription businesses: Revenue Growth % + Operating Margin % ≥ 40 indicates a healthy balance between growth investment and profitability. Elite companies exceed 60.",
    detail: "Popularised by venture capitalists Brad Feld and Fred Wilson around 2015 as a quick health check for SaaS companies. The insight is that growth and profitability trade off against each other — a company growing 60% with -20% margins is as healthy as one growing 20% with 20% margins. The combined score benchmarks where a company sits on this trade-off curve. We apply it broadly to technology and subscription-model companies, not just traditional SaaS.",
    thresholds: [
      { label: "Elite",         color: "bg-emerald-500", range: "≥ 60", description: "Best-in-class balance of growth and profitability. Premium justified." },
      { label: "Healthy",       color: "bg-green-500",   range: "40–59", description: "Meeting the benchmark. Solid business fundamentals." },
      { label: "Below Par",     color: "bg-yellow-500",  range: "20–39", description: "Growing or profitable but not both. Improvement needed." },
      { label: "Underperforming", color: "bg-red-500",   range: "< 20", description: "Neither growing enough nor profitable enough. Value-destructive trajectory." },
    ],
    whyItMatters: "Rule of 40 is applied selectively to technology and software businesses. A low score here doesn't disqualify a traditional industrial company — context is everything.",
  },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function useInView(threshold = 0.1) {
  const ref = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) setInView(true); },
      { threshold }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return { ref, inView };
}

// ─── Components ───────────────────────────────────────────────────────────────

function ThresholdBar({ bands }: { bands: ThresholdBand[] }) {
  return (
    <div className="space-y-1 mt-3">
      {bands.map((b) => (
        <div key={b.label} className="flex items-stretch gap-3 rounded-lg px-2 py-2 hover:bg-zinc-800/50 transition-colors duration-150 group/band">
          <div className={`w-0.5 rounded-full flex-shrink-0 ${b.color} opacity-70 group-hover/band:opacity-100 transition-opacity`} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-zinc-200">{b.label}</span>
              <span className="text-xs font-mono text-zinc-500 bg-zinc-800/80 border border-zinc-700/50 px-2 py-0.5 rounded-md">{b.range}</span>
            </div>
            <p className="text-xs text-zinc-500 mt-0.5 leading-relaxed">{b.description}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function ModelCard({ model }: { model: Model }) {
  const [expanded, setExpanded] = useState(false);
  const { ref, inView } = useInView();
  const cat = CATEGORIES[model.category];

  return (
    <div
      ref={ref}
      id={model.id}
      style={{ scrollMarginTop: "88px" }}
      className={`group/card rounded-xl border bg-zinc-900/50 backdrop-blur-sm overflow-hidden transition-all duration-500 shadow-lg hover:shadow-xl ${
        inView ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"
      } ${cat.border} ${cat.glow}`}
    >
      {/* Header */}
      <div className={`px-6 py-5 border-b ${cat.border} ${cat.bg} relative overflow-hidden`}>
        {/* Subtle left accent bar */}
        <div className={`absolute left-0 top-0 bottom-0 w-0.5 ${cat.dot} opacity-60 group-hover/card:opacity-100 transition-opacity duration-300`} />
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1.5">
              <span className={`text-xs font-semibold uppercase tracking-widest px-2.5 py-0.5 rounded-full ${cat.bg} ${cat.color} border ${cat.border}`}>
                {cat.label}
              </span>
              <span className="text-xs text-zinc-600 font-mono">{model.origin} · {model.year}</span>
            </div>
            <h2 className={`text-xl font-bold tracking-tight ${cat.color}`}>{model.name}</h2>
            <p className="text-sm text-zinc-400 mt-0.5">{model.subtitle}</p>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="px-6 py-5 space-y-5">

        {/* Summary */}
        <p className="text-sm text-zinc-300 leading-relaxed">{model.summary}</p>

        {/* Thresholds */}
        <div>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Score Ranges</h3>
          <ThresholdBar bands={model.thresholds} />
        </div>

        {/* Components — expandable */}
        {model.components && (
          <div>
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-2 text-xs font-semibold text-zinc-400 hover:text-zinc-200 uppercase tracking-wider transition-colors"
            >
              {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              {expanded ? "Hide" : "Show"} Components ({model.components.length})
            </button>
            {expanded && (
              <div className="mt-3 space-y-2">
                {model.components.map((c) => (
                  <div key={c.key} className="flex gap-3 p-3 rounded-lg bg-zinc-800/50 border border-zinc-700/40">
                    <span className={`text-xs font-mono font-bold ${cat.color} mt-0.5 min-w-[3rem]`}>{c.key}</span>
                    <div>
                      <p className="text-xs font-semibold text-zinc-300">{c.name}</p>
                      <p className="text-xs text-zinc-500 mt-0.5">{c.description}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Backstory */}
        <details className="group/details">
          <summary className="flex items-center gap-2 text-xs font-semibold text-zinc-600 hover:text-zinc-400 uppercase tracking-widest cursor-pointer list-none transition-colors duration-150">
            <ChevronDown className="w-3.5 h-3.5 group-open/details:rotate-180 transition-transform duration-300" />
            Origin & Backstory
          </summary>
          <p className="mt-3 text-xs text-zinc-500 leading-relaxed pl-5 border-l border-zinc-800">{model.detail}</p>
        </details>

        {/* Why it matters */}
        <div className={`rounded-lg p-4 ${cat.bg} border ${cat.border} relative overflow-hidden`}>
          <div className={`absolute left-0 top-0 bottom-0 w-0.5 ${cat.dot} opacity-40`} />
          <p className="text-xs font-semibold text-zinc-500 uppercase tracking-widest mb-1.5 pl-2">How we use it</p>
          <p className={`text-xs leading-relaxed ${cat.color} pl-2`}>{model.whyItMatters}</p>
        </div>
      </div>
    </div>
  );
}

// ─── Composite Score Section ──────────────────────────────────────────────────

function CompositeSection() {
  const { ref, inView } = useInView();
  const [openBucket, setOpenBucket] = useState<string | null>(null);

  const buckets = [
    {
      name: "Valuation",         max: 30, color: "bg-emerald-500", textColor: "text-emerald-400",
      inputs: ["FCF Yield (0–8 pts)", "PEG Ratio (0–8 pts)", "Reverse DCF (0–7 pts)", "Upside to Fair Value (0–7 pts)"],
    },
    {
      name: "Financial Quality", max: 25, color: "bg-blue-500",    textColor: "text-blue-400",
      inputs: ["Piotroski F-Score (0–13 pts)", "Sloan Accrual (0–8 pts)", "Owner Earnings Yield (0–4 pts)"],
    },
    {
      name: "Value Creation",    max: 20, color: "bg-purple-500",  textColor: "text-purple-400",
      inputs: ["ROIC-WACC Spread (0–12 pts)", "Magic Formula EY (0–8 pts)"],
    },
    {
      name: "Risk & Integrity",  max: 15, color: "bg-red-500",     textColor: "text-red-400",
      inputs: ["Altman Z-Score (0–9 pts)", "Beneish M-Score (0–6 pts)"],
    },
    {
      name: "Growth Efficiency", max: 10, color: "bg-yellow-500",  textColor: "text-yellow-400",
      inputs: ["Rule of 40 (0–10 pts)"],
    },
  ];

  const signals = [
    { label: "STRONG_BUY",   range: "≥ 70",  bg: "bg-emerald-500/15", text: "text-emerald-400", border: "border-emerald-500/30" },
    { label: "BUY",          range: "53–69", bg: "bg-green-500/15",   text: "text-green-400",   border: "border-green-500/30"   },
    { label: "HOLD",         range: "38–52", bg: "bg-yellow-500/15",  text: "text-yellow-400",  border: "border-yellow-500/30"  },
    { label: "AVOID",        range: "22–37", bg: "bg-orange-500/15",  text: "text-orange-400",  border: "border-orange-500/30"  },
    { label: "STRONG_AVOID", range: "< 22",  bg: "bg-red-500/15",     text: "text-red-400",     border: "border-red-500/30"     },
  ];

  const penalties = [
    { trigger: "Each HIGH Red Flag",                         pts: "−4 pts each (max −15)" },
    { trigger: "Beneish M > −1.78 (likely manipulator)",    pts: "−5 pts" },
    { trigger: "Altman Z < 1.81 (distress zone)",           pts: "−3 pts" },
    { trigger: "Reverse DCF implied growth < −15%",         pts: "−3 pts" },
    { trigger: "Price >20% above model fair value",         pts: "−3 to −15 pts" },
  ];

  return (
    <div
      ref={ref}
      id="quant-score"
      className={`rounded-xl border border-zinc-600/50 bg-zinc-900/60 backdrop-blur-sm overflow-hidden transition-all duration-700 ${
        inView ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"
      }`}
    >
      {/* Header */}
      <div className="px-6 py-4 border-b border-zinc-600/40 bg-gradient-to-r from-zinc-800/60 to-zinc-900/40">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-zinc-700/60 text-zinc-200 border border-zinc-500/30">
            Composite
          </span>
          <span className="text-xs text-zinc-500 font-mono">All models combined · deterministic · no AI</span>
        </div>
        <h2 className="text-xl font-bold text-zinc-100">
          quant_score <span className="text-zinc-500 font-normal text-base">(1–99)</span>
        </h2>
        <p className="text-sm text-zinc-400 mt-0.5">The unified conviction score that drives all buy/sell signals</p>
      </div>

      <div className="px-6 py-5 space-y-6">
        <p className="text-sm text-zinc-300 leading-relaxed">
          Every model above feeds a single deterministic score from 1 to 99. No AI required — the computation
          runs on SEC filing data and market prices alone, making it fully reproducible and auditable.
          Independent models that agree amplify each other; conflicting signals cancel, creating a natural
          uncertainty buffer.
        </p>

        {/* Bucket bars — click to expand inputs */}
        <div>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3">
            Score Buckets <span className="font-normal normal-case text-zinc-600">(click to see inputs)</span>
          </h3>
          <div className="space-y-2.5">
            {buckets.map((b) => (
              <div key={b.name}>
                <button
                  onClick={() => setOpenBucket(openBucket === b.name ? null : b.name)}
                  className="w-full flex items-center gap-3 group"
                >
                  <div className={`w-36 text-xs ${b.textColor} text-right shrink-0`}>{b.name}</div>
                  <div className="flex-1 bg-zinc-800 rounded-full h-5 overflow-hidden">
                    <div
                      className={`h-full ${b.color} opacity-75 group-hover:opacity-100 rounded-full transition-all duration-700`}
                      style={{ width: inView ? `${b.max}%` : "0%" }}
                    />
                  </div>
                  <div className="w-14 text-xs text-zinc-500 font-mono text-left shrink-0">0–{b.max} pts</div>
                  <ChevronDown className={`w-3 h-3 text-zinc-600 shrink-0 transition-transform duration-200 ${openBucket === b.name ? "rotate-180" : ""}`} />
                </button>
                {openBucket === b.name && (
                  <div className="mt-2 ml-[156px] space-y-1.5 pb-1">
                    {b.inputs.map((inp) => (
                      <div key={inp} className="flex items-center gap-2">
                        <div className={`w-1.5 h-1.5 rounded-full ${b.color} opacity-60 shrink-0`} />
                        <span className="text-xs text-zinc-500">{inp}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Penalties */}
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
          <h3 className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-3">Penalty Deductions</h3>
          <div className="space-y-2">
            {penalties.map((p) => (
              <div key={p.trigger} className="flex items-start justify-between gap-4">
                <span className="text-xs text-zinc-400">{p.trigger}</span>
                <span className="text-xs font-mono text-red-400 shrink-0">{p.pts}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Signal thresholds */}
        <div>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3">Signal Thresholds</h3>
          <div className="flex gap-2 flex-wrap">
            {signals.map((s) => (
              <div key={s.label} className={`flex-1 min-w-[90px] rounded-lg border ${s.border} ${s.bg} p-3 text-center`}>
                <div className={`text-xs font-bold ${s.text} mb-1`}>{s.label.replace("_", " ")}</div>
                <div className={`text-xs font-mono ${s.text} opacity-70`}>{s.range}</div>
              </div>
            ))}
          </div>
        </div>

        {/* How we use it */}
        <div className="rounded-lg p-3 bg-zinc-800/50 border border-zinc-700/40">
          <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">How we use it</p>
          <p className="text-xs text-zinc-400 leading-relaxed">
            quant_score drives position sizing in the paper portfolio. Signal flips to AVOID or STRONG_AVOID
            automatically close open BUY/HOLD positions at end of day. Scores update automatically when new
            10-K or 10-Q filings are detected on SEC EDGAR — no manual intervention required.
          </p>
        </div>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const CATEGORY_ORDER: Array<"risk" | "quality" | "integrity" | "valuation"> = [
  "risk", "quality", "integrity", "valuation"
];

export default function LegendPage() {
  const [activeModel, setActiveModel] = useState<string | null>(null);

  function scrollTo(id: string) {
    setActiveModel(id);
    const el = document.getElementById(id);
    if (!el) return;
    const y = el.getBoundingClientRect().top + window.scrollY - 88;
    window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
  }

  return (
    <div className="min-h-screen bg-[#080a0f] text-zinc-100">

      {/* ── Top Nav ── */}
      <header className="sticky top-0 z-50 border-b border-zinc-800/60 bg-[#080a0f]/90 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href="/" className="flex items-center gap-1.5 text-sm text-zinc-400 hover:text-zinc-100 transition-colors">
              <ArrowLeft className="w-4 h-4" />
              Screener
            </Link>
            <span className="text-zinc-700">|</span>
            <Link href="/portfolio" className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors">Portfolio</Link>
            <span className="text-zinc-700">|</span>
            <Link href="/leaderboard" className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors">Leaderboard</Link>
          </div>
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-zinc-500" />
            <span className="text-sm font-semibold text-zinc-300">Scoring Engine</span>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-8 flex gap-8">

        {/* ── Sidebar ── */}
        <aside className="hidden lg:block w-56 flex-shrink-0">
          <div className="sticky top-24 space-y-5">
            <div>
              <p className="text-xs font-semibold text-zinc-600 uppercase tracking-wider mb-2">Jump to</p>
              {CATEGORY_ORDER.map((cat) => {
                const catCfg = CATEGORIES[cat];
                const catModels = MODELS.filter((m) => m.category === cat);
                return (
                  <div key={cat} className="mb-4">
                    <p className={`text-xs font-bold uppercase tracking-wider mb-1.5 ${catCfg.color}`}>
                      {catCfg.label}
                    </p>
                    <div className="space-y-0.5">
                      {catModels.map((m) => (
                        <button
                          key={m.id}
                          onClick={() => scrollTo(m.id)}
                          className={`w-full text-left text-xs px-2 py-1.5 rounded-md transition-all duration-200 relative ${
                            activeModel === m.id
                              ? `${catCfg.bg} ${catCfg.color} font-semibold`
                              : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/40"
                          }`}
                        >
                          {activeModel === m.id && (
                            <span className={`absolute left-0 top-1 bottom-1 w-0.5 rounded-full ${catCfg.dot}`} />
                          )}
                          <span className={activeModel === m.id ? "pl-1.5" : ""}>{m.name}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mb-4">
              <p className="text-xs font-bold uppercase tracking-wider mb-1.5 text-zinc-400">Composite</p>
              <button
                onClick={() => scrollTo("quant-score")}
                className={`w-full text-left text-xs px-2 py-1.5 rounded-md transition-all duration-200 relative ${
                  activeModel === "quant-score"
                    ? "bg-zinc-700/40 text-zinc-200 font-semibold"
                    : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/40"
                }`}
              >
                {activeModel === "quant-score" && (
                  <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-zinc-400" />
                )}
                <span className={activeModel === "quant-score" ? "pl-1.5" : ""}>quant_score (1–99)</span>
              </button>
            </div>

            <div className="pt-3 border-t border-zinc-800">
              <p className="text-xs text-zinc-600 leading-relaxed">
                All scores are computed from SEC EDGAR filings and yfinance data. No analyst opinions or sentiment.
              </p>
            </div>
          </div>
        </aside>

        {/* ── Main Content ── */}
        <main className="flex-1 min-w-0 space-y-10">

          {/* Hero */}
          <div className="pb-4">
            <p className="text-xs font-semibold text-zinc-600 uppercase tracking-widest mb-3">Reference Documentation</p>
            <h1 className="text-4xl font-bold tracking-tight mb-3">
              <span className="text-zinc-100">The Scoring</span>{" "}
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-blue-400">Engine</span>
            </h1>
            <p className="text-zinc-400 max-w-2xl leading-relaxed text-sm">
              Every signal in this system is derived from hard financial data in SEC filings — no analyst opinions,
              no sentiment, no black boxes. Here is the complete reference for every model: where it came from,
              what it measures, and exactly how it feeds the score.
            </p>
            <div className="flex flex-wrap gap-2 mt-5">
              {CATEGORY_ORDER.map((cat) => {
                const c = CATEGORIES[cat];
                const count = MODELS.filter((m) => m.category === cat).length;
                return (
                  <span key={cat} className={`text-xs font-semibold px-3 py-1.5 rounded-full border ${c.bg} ${c.color} ${c.border} cursor-pointer hover:opacity-80 transition-opacity`}
                    onClick={() => { const first = MODELS.find(m => m.category === cat); if (first) scrollTo(first.id); }}>
                    {c.label} · {count} model{count > 1 ? "s" : ""}
                  </span>
                );
              })}
            </div>
          </div>

          {/* Sections by category */}
          {CATEGORY_ORDER.map((cat) => {
            const catCfg = CATEGORIES[cat];
            const catModels = MODELS.filter((m) => m.category === cat);
            return (
              <section key={cat}>
                <div className="flex items-center gap-3 mb-6">
                  <div className={`w-1 h-7 rounded-full ${catCfg.dot} opacity-80`} />
                  <h2 className={`text-base font-bold tracking-wide uppercase ${catCfg.color}`}>{catCfg.label}</h2>
                  <div className="flex-1 h-px bg-gradient-to-r from-zinc-700/60 to-transparent" />
                </div>
                <div className="space-y-5">
                  {catModels.map((model) => (
                    <ModelCard key={model.id} model={model} />
                  ))}
                </div>
              </section>
            );
          })}

          {/* Composite Score */}
          <section>
            <div className="flex items-center gap-3 mb-6">
              <div className="w-1 h-7 rounded-full bg-zinc-500 opacity-80" />
              <h2 className="text-base font-bold tracking-wide uppercase text-zinc-400">Composite</h2>
              <div className="flex-1 h-px bg-gradient-to-r from-zinc-700/60 to-transparent" />
            </div>
            <CompositeSection />
          </section>

          {/* Footer note */}
          <div className="pt-6 border-t border-zinc-800 pb-12">
            <p className="text-xs text-zinc-600 leading-relaxed max-w-2xl">
              Scores are updated when new SEC filings (10-K, 10-Q) are detected.
              The composite quant_score (0–99) is a weighted combination of all models above,
              calibrated so that high scores across multiple independent frameworks carry more conviction than any single model.
            </p>
          </div>
        </main>
      </div>
    </div>
  );
}
