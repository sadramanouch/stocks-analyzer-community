# MASTER AI STOCK ANALYSIS APP — COMPLETE BUILD GUIDE
## Data Sources · Scoring Models · Forensic Detection · Recency System · Full AI Prompt

> **Goal:** Build an app that pulls public financial statements, compares them to current
> stock prices, runs valuation + forensic scoring, and uses a local AI (Qwen 2.5) to
> produce a complete buy/sell/hold analysis with red-flag detection.

---

## TABLE OF CONTENTS
1. [Quick Start — What to Install](#1-quick-start)
2. [Data Sources — Free to Paid](#2-data-sources)
3. [GitHub Repos & Libraries](#3-github-repos--libraries)
4. [All Scoring Models — Full Code](#4-scoring-models)
5. [Forensic Red Flag Detector](#5-forensic-red-flag-detector)
6. [Data Recency & Freshness System](#6-data-recency--freshness-system)
7. [Complete Data Pipeline](#7-complete-data-pipeline)
8. [The Master AI Prompt](#8-the-master-ai-prompt)
9. [Sector-Specific Adjustments](#9-sector-specific-adjustments)
10. [Recommended App Architecture](#10-recommended-app-architecture)

---

## 1. QUICK START

### Install Everything You Need

```bash
# ── CORE DATA ────────────────────────────────────────────────────────
pip install yfinance                   # Prices + financials from Yahoo Finance
pip install pandas-datareader          # Stooq prices + FRED macro data
pip install fredapi                    # FRED (risk-free rate, inflation, GDP)
pip install simfin                     # Pre-cleaned fundamental database
pip install edgartools                 # Best SEC EDGAR parser (10-K/10-Q/8-K/Form 4)
pip install sec-edgar-downloader       # Bulk download SEC filings
pip install openbb                     # Unified data aggregator (100+ providers)

# ── FINANCIAL ANALYSIS ───────────────────────────────────────────────
pip install financetoolkit             # 100+ ratios, Altman Z, Piotroski F, Beneish M, DCF
pip install numpy-financial            # NPV, IRR for DCF calculations
pip install scipy                      # Optimization (reverse DCF)

# ── SCREENING / TECHNICAL ────────────────────────────────────────────
pip install finvizfinance              # Finviz screener wrapper
pip install ta                         # Technical indicators (RSI, MACD, BB, etc.)
pip install pandas-ta                  # 130+ technical indicators

# ── SEC / XBRL PARSING ───────────────────────────────────────────────
pip install python-xbrl lxml beautifulsoup4

# ── AI / LLM INTEGRATION ─────────────────────────────────────────────
pip install langchain llama-index ollama   # For Qwen 2.5 via Ollama

# ── GENERAL ──────────────────────────────────────────────────────────
pip install requests pandas numpy matplotlib plotly
```

### Set EDGAR Identity (Required — No API Key Needed)
```python
from edgar import set_identity
set_identity("YourName your@email.com")   # Required by SEC. Just your real name + email.
```

---

## 2. DATA SOURCES

### TIER 1 — COMPLETELY FREE, NO API KEY

---

#### A. SEC EDGAR (The Primary Truth Source)
Every US public company files here by law. All financial statements, insider trades, events.

```
Base URL:        https://data.sec.gov
Rate limit:      10 requests/second
Auth:            None — just User-Agent header: "YourApp your@email.com"

KEY ENDPOINTS:
  Company submissions (all filings):
    GET /submissions/CIK{10-digit-CIK}.json
    Example: https://data.sec.gov/submissions/CIK0000320193.json   ← AAPL

  All XBRL facts (balance sheet, income, cash flow — everything):
    GET /api/xbrl/companyfacts/CIK{CIK}.json
    Example: https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json

  Single concept (e.g., just Revenue over time):
    GET /api/xbrl/companyconcept/CIK{CIK}/{taxonomy}/{tag}.json
    Example: https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json

  Cross-company frame (all companies reporting X in a period):
    GET /api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json
    Example: https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/CY2023Q4I.json

  Full-text search:
    GET https://efts.sec.gov/EFTS/api/search.json?q="going+concern"&dateRange=custom&startdt=2024-01-01

BULK DOWNLOAD (nightly — download once, work offline):
  All company facts:   https://data.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
  Submissions index:   https://data.sec.gov/submissions/

CRITICAL XBRL TAGS:
  Revenue:                us-gaap/Revenues  OR  us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax
  Net Income:             us-gaap/NetIncomeLoss
  EPS (Diluted):          us-gaap/EarningsPerShareDiluted
  Total Assets:           us-gaap/Assets
  Total Liabilities:      us-gaap/Liabilities
  Stockholders Equity:    us-gaap/StockholdersEquity
  Cash:                   us-gaap/CashAndCashEquivalentsAtCarryingValue
  Long-term Debt:         us-gaap/LongTermDebt
  Operating Income:       us-gaap/OperatingIncomeLoss
  Operating Cash Flow:    us-gaap/NetCashProvidedByUsedInOperatingActivities
  CapEx:                  us-gaap/PaymentsToAcquirePropertyPlantAndEquipment
  Shares Outstanding:     us-gaap/CommonStockSharesOutstanding
  Gross Profit:           us-gaap/GrossProfit
  COGS:                   us-gaap/CostOfRevenue
  Accounts Receivable:    us-gaap/AccountsReceivableNetCurrent
  Inventory:              us-gaap/InventoryNet
  Current Assets:         us-gaap/AssetsCurrent
  Current Liabilities:    us-gaap/LiabilitiesCurrent
  PP&E Net:               us-gaap/PropertyPlantAndEquipmentNet
  Goodwill:               us-gaap/Goodwill
  Depreciation:           us-gaap/DepreciationDepletionAndAmortization
  SG&A:                   us-gaap/SellingGeneralAndAdministrativeExpense
  R&D:                    us-gaap/ResearchAndDevelopmentExpense
  Interest Expense:       us-gaap/InterestExpense
  Retained Earnings:      us-gaap/RetainedEarningsAccumulatedDeficit
```

---

#### B. FRED — Federal Reserve Economic Data
Essential for DCF discount rates, macro context, and risk-free rate.

```
API:  https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key=FREE_KEY&file_type=json
Docs: https://fred.stlouisfed.org/docs/api/fred/

Register for free key: https://fred.stlouisfed.org/docs/api/api_key.html

KEY SERIES:
  DGS10       → 10-Year Treasury Yield (risk-free rate for CAPM/DCF)
  DGS2        → 2-Year Treasury
  FEDFUNDS    → Fed Funds Rate
  CPIAUCSL    → CPI Inflation (YoY)
  T10YIE      → 10-Year Breakeven Inflation (market inflation expectations)
  GDP         → Gross Domestic Product
  GDPC1       → Real GDP
  UNRATE      → Unemployment Rate
  BAMLH0A0HYM2 → High Yield Credit Spread (market stress gauge)
  SP500       → S&P 500 Index

Python:
  from fredapi import Fred
  fred = Fred(api_key="your_free_key")
  rf_rate = fred.get_series("DGS10").iloc[-1] / 100   # Current risk-free rate
  inflation = fred.get_series("CPIAUCSL").pct_change(12).iloc[-1]
```

---

#### C. Stooq — Free Global EOD Prices
No API key, no rate limits, 20+ years of history. Great fallback for price data.

```
Direct CSV:
  https://stooq.com/q/d/l/?s=AAPL.US&i=d     # Daily OHLCV
  https://stooq.com/q/d/l/?s=MSFT.US&i=w     # Weekly

Python:
  import pandas_datareader as pdr
  df = pdr.get_data_stooq("AAPL.US", start="2020-01-01")
```

---

#### D. OpenInsider — Insider Transactions (Web Scraping)
```
URL: http://openinsider.com/
Data: CEO/CFO/Director buy/sell transactions pulled from Form 4 filings
Filter by: ticker, cluster (multiple insiders same day), transaction size

Also via EDGAR Form 4 RSS:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=40&output=atom
```

---

#### E. FINRA — Short Interest Data (Free Bulk Download)
```
API:  https://api.finra.org/data/group/otcmarket/name/weeklySummary
Docs: https://developer.finra.org/
Download: https://www.finra.org/investors/learn-to-invest/advanced-investing/short-selling/

Key fields: short_volume, total_volume, short_exempt_volume
```

---

### TIER 2 — FREE WITH API KEY (No Credit Card)

---

#### F. Financial Modeling Prep (FMP) — Best Free Tier for Pre-Computed Data
```
Free tier: 250 requests/day, 5 years history
Register:  https://financialmodelingprep.com/register

BASE: https://financialmodelingprep.com/api

ENDPOINTS:
  /v3/income-statement/{ticker}?limit=10&apikey=KEY         # Annual income stmt
  /v3/income-statement/{ticker}?period=quarter&limit=8      # Quarterly
  /v3/balance-sheet-statement/{ticker}?limit=10
  /v3/cash-flow-statement/{ticker}?limit=10
  /v3/ratios/{ticker}?limit=4                               # All key ratios
  /v3/key-metrics/{ticker}?limit=4                          # EV, FCF yield, ROIC
  /v4/score?symbol={ticker}                                 # Altman Z + Piotroski F BUILT IN
  /v3/discounted-cash-flow/{ticker}                         # FMP's own DCF estimate
  /v3/analyst-estimates/{ticker}?limit=4                    # Forward EPS/Revenue estimates
  /v3/earnings-surprises/{ticker}                           # Beat/miss history
  /v4/insider-trading?symbol={ticker}&limit=50              # Insider trades
  /v3/institutional-holder/{ticker}                         # 13F holders
  /v4/sector_price_earning_ratio?date=2024-01-01&exchange=NYSE  # Sector P/E medians
  /v3/earning_calendar?from=2024-01-01&to=2024-03-31        # Upcoming earnings
  /v3/historical-price-full/{ticker}?from=2020-01-01        # Price history
  /v4/company-outlook?symbol={ticker}                       # Full company profile

Python example:
  import requests
  BASE = "https://financialmodelingprep.com/api"
  KEY  = "YOUR_FREE_KEY"

  def fmp(endpoint):
      sep = "&" if "?" in endpoint else "?"
      return requests.get(f"{BASE}{endpoint}{sep}apikey={KEY}").json()

  ratios   = fmp("/v3/ratios/AAPL?limit=4")
  scores   = fmp("/v4/score?symbol=AAPL")
  dcf      = fmp("/v3/discounted-cash-flow/AAPL")
  # Altman Z:      scores[0]["altmanZScore"]
  # Piotroski F:   scores[0]["piotroskiScore"]
  # DCF estimate:  dcf[0]["dcf"]
```

---

#### G. Finnhub — Best for Real-Time + Sentiment + Insider
```
Free tier: 60 req/min, real-time US quotes, news sentiment
Register:  https://finnhub.io/register
Install:   pip install finnhub-python

import finnhub
client = finnhub.Client(api_key="YOUR_KEY")

client.quote("AAPL")                                              # Real-time
client.company_news("AAPL", _from="2024-01-01", to="2024-12-31") # News
client.news_sentiment("AAPL")                                     # Sentiment score
client.recommendation_trends("AAPL")                              # Buy/Hold/Sell counts
client.earnings_calendar(symbol="AAPL")                           # Next earnings date
client.company_earnings("AAPL", limit=8)                          # Earnings history + surprises
client.insider_transactions("AAPL", _from="2024-01-01", to="2024-12-31")
client.stock_insider_sentiment("AAPL", "2024-01-01", "2024-12-31") # Net buying score
client.peers("AAPL")                                               # Comparable companies
client.company_profile2(symbol="AAPL")                            # SIC code, employees
```

---

#### H. Alpha Vantage
```
Free tier: 25 requests/day (limited) — better for testing
Register:  https://www.alphavantage.co/support/#api-key
Install:   pip install alpha-vantage

from alpha_vantage.fundamentaldata import FundamentalData
fd = FundamentalData(key="YOUR_KEY", output_format="pandas")

fd.get_overview("AAPL")                    # All key metrics in one call
fd.get_income_statement_annual("AAPL")     # 5yr annual P&L
fd.get_income_statement_quarterly("AAPL") # 5yr quarterly P&L
fd.get_balance_sheet_annual("AAPL")
fd.get_cash_flow_annual("AAPL")
fd.get_earnings("AAPL")                   # EPS history + estimates
```

---

#### I. SimFin — Pre-Cleaned Fundamental Database
```
Free tier: 3000+ US stocks, 20+ years history (12-month delay on free tier)
Register:  https://simfin.com/api/v3/  (get free API key)
Install:   pip install simfin
GitHub:    https://github.com/SimFin/simfin
Tutorials: https://github.com/SimFin/simfin-tutorials

import simfin as sf
from simfin.names import *

sf.set_api_key("YOUR_FREE_KEY")
sf.set_data_dir("~/simfin_data/")          # Caches data locally after first download

# Load everything — downloads once, cached
income   = sf.load_income(variant=ANNUAL, market="us")      # or QUARTERLY
balance  = sf.load_balance(variant=ANNUAL, market="us")
cashflow = sf.load_cashflow(variant=ANNUAL, market="us")
prices   = sf.load_shareprices(variant=DAILY, market="us")

# Pre-computed derived ratios (P/E, P/B, P/FCF, gross margin, ROE, ROA, etc.)
derived  = sf.load_derived(variant=ANNUAL, market="us")

# Filter to a single company
msft = income.loc["MSFT"]
```

---

#### J. yfinance — Easiest All-in-One
```
Install: pip install yfinance
GitHub:  https://github.com/ranaroussi/yfinance

import yfinance as yf
t = yf.Ticker("AAPL")

# Prices
t.history(period="5y")               # OHLCV + dividends
t.info["currentPrice"]
t.info["marketCap"]
t.info["beta"]
t.info["52WeekChange"]
t.info["fiftyTwoWeekHigh"]
t.info["fiftyTwoWeekLow"]
t.info["averageVolume"]

# Fundamental ratios (pre-computed)
t.info["trailingPE"]
t.info["forwardPE"]
t.info["priceToBook"]
t.info["debtToEquity"]
t.info["returnOnEquity"]
t.info["returnOnAssets"]
t.info["freeCashflow"]
t.info["operatingCashflow"]
t.info["revenueGrowth"]
t.info["earningsGrowth"]
t.info["grossMargins"]
t.info["operatingMargins"]
t.info["profitMargins"]
t.info["totalDebt"]
t.info["totalCash"]
t.info["currentRatio"]
t.info["quickRatio"]

# Financial statements (annual + quarterly)
t.financials              # Annual income statement
t.quarterly_financials
t.balance_sheet           # Annual balance sheet
t.quarterly_balance_sheet
t.cashflow                # Annual cash flow
t.quarterly_cashflow

# Ownership
t.institutional_holders   # 13F holders
t.major_holders           # % breakdown
t.insider_transactions    # Form 4 trades

# Analyst data
t.recommendations         # Buy/Hold/Sell history
t.analyst_price_target    # Consensus targets
t.earnings_forecasts

# Options
t.options                 # Available expiry dates
t.option_chain("2025-06-20")  # Full chain
```

---

#### K. OpenBB Platform — Unified Aggregator
```
GitHub:  https://github.com/OpenBB-finance/OpenBB
Docs:    https://docs.openbb.co/
Install: pip install openbb

from openbb import obb

# Set your API keys once
obb.user.credentials.fmp_api_key = "..."
obb.user.credentials.polygon_api_key = "..."
obb.user.credentials.fred_api_key = "..."

# Then use one unified interface regardless of provider
obb.equity.price.historical("AAPL", provider="yfinance")
obb.equity.fundamental.income(symbol="AAPL", period="quarter", limit=8, provider="fmp")
obb.equity.fundamental.balance(symbol="AAPL", provider="fmp")
obb.equity.fundamental.cash(symbol="AAPL", provider="fmp")
obb.equity.fundamental.ratios(symbol="AAPL", provider="fmp")
obb.equity.fundamental.multiples(symbol="AAPL", provider="fmp")   # DCF, EV/EBITDA
obb.equity.estimates.consensus(symbol="AAPL", provider="fmp")     # Analyst consensus
obb.equity.ownership.insider_trading(symbol="AAPL", provider="fmp")
obb.equity.ownership.institutional(symbol="AAPL", provider="fmp") # 13F
obb.economy.fred_series(symbol="DGS10", provider="fred")
obb.equity.shorts.fails_to_deliver(symbol="AAPL")                 # FTD data
```

---

### TIER 3 — PAID (Worth Knowing)

| Provider | Best For | Price |
|---|---|---|
| **Polygon.io** | Real-time, options, global | Free (5/min delayed) → $29/mo |
| **EODHD** | 70+ global markets, 30yr history, earnings calls | ~$20/mo |
| **Tiingo** | Clean EOD + fundamentals, academic | ~$10/mo |
| **Intrinio** | Institutional normalized, ML-ready | $100+/mo |
| **Quandl/Nasdaq Data Link** | Alternative data (satellite, credit card, foot traffic) | Varies |
| **Refinitiv / LSEG** | Bloomberg alternative, enterprise | $$$$ |

---

## 3. GITHUB REPOS & LIBRARIES

### Must-Know Repos

| Repo | Stars | What It Does |
|---|---|---|
| [JerBouma/FinanceToolkit](https://github.com/JerBouma/FinanceToolkit) | ⭐⭐⭐⭐⭐ | 100+ ratios, Altman Z, Piotroski F, Beneish M, DCF — all-in-one |
| [ranaroussi/yfinance](https://github.com/ranaroussi/yfinance) | ⭐⭐⭐⭐⭐ | Yahoo Finance wrapper — most widely used |
| [dgunning/edgartools](https://github.com/dgunning/edgartools) | ⭐⭐⭐⭐ | Best SEC EDGAR parser. 10-K/10-Q/8-K/Form 4 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | ⭐⭐⭐⭐⭐ | Unified platform for all financial data |
| [SimFin/simfin](https://github.com/SimFin/simfin) | ⭐⭐⭐ | Pre-cleaned bulk fundamental data |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | ⭐⭐⭐⭐ | LLM-powered multi-agent hedge fund simulation |
| [virattt/financial-datasets](https://github.com/virattt/financial-datasets) | ⭐⭐⭐ | SEC filing Q&A datasets for LLM fine-tuning |
| [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | ⭐⭐⭐⭐ | Open-source financial LLM (sentiment, prediction) |
| [AI4Finance-Foundation/FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | ⭐⭐⭐ | Financial AI agent framework |
| [jadchaar/sec-edgar-downloader](https://github.com/jadchaar/sec-edgar-downloader) | ⭐⭐⭐ | Bulk download SEC filings |
| [pmorissette/ffn](https://github.com/pmorissette/ffn) | ⭐⭐⭐ | Portfolio and performance analytics |
| [lit26/finvizfinance](https://github.com/lit26/finvizfinance) | ⭐⭐⭐ | Finviz screener + fundamentals |

### Key Library Quick Reference

```bash
# All-in-one financial analysis (USE THIS FIRST)
pip install financetoolkit
from financetoolkit import Toolkit
companies = Toolkit(["AAPL", "MSFT"], api_key="YOUR_FMP_KEY")
companies.models.get_altman_z_score()
companies.models.get_piotroski_score()
companies.models.get_beneish_m_score()
companies.models.get_intrinsic_valuation(growth_rate=0.08)
companies.ratios.get_price_earnings_ratio()
companies.ratios.get_return_on_equity()
# 100+ more methods — see docs

# Best SEC EDGAR parser
pip install edgartools
from edgar import Company, set_identity
set_identity("Your Name your@email.com")
company = Company("AAPL")
filing  = company.get_filings(form="10-K").latest()
income  = filing.financials.income_statement
balance = filing.financials.balance_sheet
cashflow= filing.financials.cash_flow_statement
form4s  = company.get_filings(form="4")    # Insider transactions
```

---

## 4. SCORING MODELS

### Model 1: Altman Z-Score (Bankruptcy Risk)
```python
def altman_z_score(working_capital, retained_earnings, ebit, market_cap,
                   revenue, total_assets, total_liabilities):
    """
    Original Altman Z (1968) — best for manufacturing/industrial companies.

    Z > 2.99  → Safe Zone
    1.81–2.99 → Grey Zone
    Z < 1.81  → Distress Zone (HIGH bankruptcy risk)

    Use Modified Z' for private companies (replace X4 with book equity / liabilities).
    NOT reliable for: banks, insurance, SaaS, pre-revenue companies.
    """
    X1 = working_capital / total_assets           # Liquidity
    X2 = retained_earnings / total_assets         # Profitability / age
    X3 = ebit / total_assets                      # Operating efficiency
    X4 = market_cap / total_liabilities           # Solvency
    X5 = revenue / total_assets                   # Asset turnover

    z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

    if z > 2.99:   zone = "SAFE"
    elif z > 1.81: zone = "GREY"
    else:          zone = "DISTRESS"

    return {"z_score": round(z, 3), "zone": zone, "components": {"X1":X1,"X2":X2,"X3":X3,"X4":X4,"X5":X5}}
```

---

### Model 2: Piotroski F-Score (Fundamental Strength, 0–9)
```python
def piotroski_f_score(curr, prev):
    """
    Piotroski (2000) — systematic value investing signal.
    Best for: value stocks, asset-intensive businesses.

    Score 7–9: Strong (potential long)
    Score 3–6: Neutral
    Score 0–2: Weak (potential short)

    curr/prev = dicts with financial data for current and prior year.
    """
    score = 0
    breakdown = {}

    # ── PROFITABILITY (4 points) ─────────────────────────────────────
    # F1: Positive ROA
    roa_curr = curr["net_income"] / curr["avg_total_assets"]
    f1 = 1 if roa_curr > 0 else 0
    score += f1; breakdown["F1_positive_roa"] = f1

    # F2: Positive Operating Cash Flow
    f2 = 1 if curr["operating_cf"] > 0 else 0
    score += f2; breakdown["F2_positive_ocf"] = f2

    # F3: Improving ROA
    roa_prev = prev["net_income"] / prev["avg_total_assets"]
    f3 = 1 if roa_curr > roa_prev else 0
    score += f3; breakdown["F3_improving_roa"] = f3

    # F4: Cash Flow Quality (OCF > Net Income — earnings backed by cash)
    f4 = 1 if curr["operating_cf"] > curr["net_income"] else 0
    score += f4; breakdown["F4_ocf_gt_ni"] = f4

    # ── LEVERAGE / LIQUIDITY (3 points) ─────────────────────────────
    # F5: Decreasing Long-Term Leverage
    lev_curr = curr["long_term_debt"] / curr["avg_total_assets"]
    lev_prev = prev["long_term_debt"] / prev["avg_total_assets"]
    f5 = 1 if lev_curr < lev_prev else 0
    score += f5; breakdown["F5_decreasing_leverage"] = f5

    # F6: Improving Current Ratio (liquidity)
    cr_curr = curr["current_assets"] / curr["current_liabilities"]
    cr_prev = prev["current_assets"] / prev["current_liabilities"]
    f6 = 1 if cr_curr > cr_prev else 0
    score += f6; breakdown["F6_improving_liquidity"] = f6

    # F7: No Share Dilution
    f7 = 1 if curr["shares_outstanding"] <= prev["shares_outstanding"] else 0
    score += f7; breakdown["F7_no_dilution"] = f7

    # ── OPERATING EFFICIENCY (2 points) ─────────────────────────────
    # F8: Improving Gross Margin
    gm_curr = (curr["revenue"] - curr["cogs"]) / curr["revenue"]
    gm_prev = (prev["revenue"] - prev["cogs"]) / prev["revenue"]
    f8 = 1 if gm_curr > gm_prev else 0
    score += f8; breakdown["F8_improving_gross_margin"] = f8

    # F9: Improving Asset Turnover
    at_curr = curr["revenue"] / curr["avg_total_assets"]
    at_prev = prev["revenue"] / prev["avg_total_assets"]
    f9 = 1 if at_curr > at_prev else 0
    score += f9; breakdown["F9_improving_asset_turnover"] = f9

    if score >= 7:   verdict = "STRONG"
    elif score >= 3: verdict = "NEUTRAL"
    else:            verdict = "WEAK"

    return {"f_score": score, "verdict": verdict, "breakdown": breakdown}
```

---

### Model 3: Beneish M-Score (Earnings Manipulation Detector)
```python
def beneish_m_score(t1, t0):
    """
    Beneish (1999) — the academic standard for earnings manipulation detection.
    Originally backtested on Enron, WorldCom, etc.

    M > -1.78:  POSSIBLE MANIPULATOR — flag for review
    -2.22 to -1.78: Grey zone
    M < -2.22:  UNLIKELY manipulator

    Less reliable for: banks, insurance, early-stage companies.
    NOTE: A high M-Score does NOT prove fraud — it flags elevated risk.

    t1 = current year data dict
    t0 = prior year data dict
    Required keys: revenue, cogs, receivables, current_assets, ppe_net,
                   total_assets, depreciation, sga, current_liabilities,
                   long_term_debt, net_income, operating_cf
    """
    # DSRI: Days Sales in Receivables Index
    # Rising = receivables growing faster than revenue → possible channel stuffing
    DSRI = (t1["receivables"] / t1["revenue"]) / (t0["receivables"] / t0["revenue"])

    # GMI: Gross Margin Index
    # Rising = gross margin deteriorating → pressure to manipulate
    GMI = ((t0["revenue"] - t0["cogs"]) / t0["revenue"]) / \
          ((t1["revenue"] - t1["cogs"]) / t1["revenue"])

    # AQI: Asset Quality Index
    # Rising = non-current/non-PPE assets growing → off-balance-sheet risk
    AQI = (1 - (t1["current_assets"] + t1["ppe_net"]) / t1["total_assets"]) / \
          (1 - (t0["current_assets"] + t0["ppe_net"]) / t0["total_assets"])

    # SGI: Sales Growth Index
    # High growth = more incentive/pressure to manage earnings
    SGI = t1["revenue"] / t0["revenue"]

    # DEPI: Depreciation Index
    # Rising = company slowing depreciation rate → inflating earnings
    DEPI = (t0["depreciation"] / (t0["depreciation"] + t0["ppe_net"])) / \
           (t1["depreciation"] / (t1["depreciation"] + t1["ppe_net"]))

    # SGAI: SG&A Expense Index
    # Rising = overhead growing faster than revenue
    SGAI = (t1["sga"] / t1["revenue"]) / (t0["sga"] / t0["revenue"])

    # LVGI: Leverage Index
    # Rising = more debt → more pressure on management
    LVGI = ((t1["current_liabilities"] + t1["long_term_debt"]) / t1["total_assets"]) / \
           ((t0["current_liabilities"] + t0["long_term_debt"]) / t0["total_assets"])

    # TATA: Total Accruals to Total Assets
    # High accruals = earnings not backed by real cash → RED FLAG
    TATA = (t1["net_income"] - t1["operating_cf"]) / t1["total_assets"]

    m = (-4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI +
         0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI)

    if m > -1.78:   verdict = "POSSIBLE_MANIPULATOR"
    elif m > -2.22: verdict = "GREY_ZONE"
    else:           verdict = "UNLIKELY_MANIPULATOR"

    return {
        "m_score": round(m, 4),
        "verdict": verdict,
        "components": {"DSRI":DSRI,"GMI":GMI,"AQI":AQI,"SGI":SGI,
                       "DEPI":DEPI,"SGAI":SGAI,"LVGI":LVGI,"TATA":TATA}
    }
```

---

### Model 4: Sloan Accrual Ratio (Earnings Quality)
```python
def sloan_accrual_ratio(net_income, operating_cf, investing_cf, total_assets):
    """
    Sloan (1996): Stocks with high accruals significantly underperform.
    Core idea: if earnings > cash flows, the difference = accruals = lower quality.

    < -10%:  Very high cash quality (GREEN — earnings exceeded by cash)
    -10–0%:  Good quality
    0–10%:   Moderate accruals (watch)
    > 10%:   Low quality / possible manipulation (RED FLAG)
    """
    accruals = net_income - (operating_cf + investing_cf)
    ratio = (accruals / total_assets) * 100

    if ratio < -10:   quality = "VERY_HIGH"
    elif ratio < 0:   quality = "HIGH"
    elif ratio < 10:  quality = "MODERATE"
    else:             quality = "LOW — RED FLAG"

    return {"accrual_ratio_pct": round(ratio, 2), "quality": quality}
```

---

### Model 5: Graham Number (Conservative Intrinsic Value)
```python
def graham_number(eps_diluted, book_value_per_share):
    """
    Benjamin Graham's conservative fair value formula.
    Based on: P/E ≤ 15 AND P/B ≤ 1.5 together → max price = sqrt(22.5 * EPS * BVPS)

    If current price < Graham Number → potentially undervalued.
    ONLY meaningful for: profitable, stable, asset-heavy businesses.
    NOT valid for: negative EPS, negative book value, high-growth tech, SaaS.
    """
    if eps_diluted <= 0 or book_value_per_share <= 0:
        return {"graham_number": None, "note": "Not applicable — negative EPS or book value"}

    gn = (22.5 * eps_diluted * book_value_per_share) ** 0.5
    return {"graham_number": round(gn, 2)}
```

---

### Model 6: Two-Stage DCF (Discounted Cash Flow)
```python
def dcf_intrinsic_value(fcf_ttm, growth_yr1_5, growth_yr6_10,
                        terminal_growth, wacc, shares_out, net_debt):
    """
    10-year two-stage DCF with Gordon Growth terminal value.
    Returns fair value per share.

    Typical inputs:
      fcf_ttm       = trailing 12-month free cash flow ($ millions)
      growth_yr1_5  = analyst consensus or historical FCF CAGR (e.g. 0.12 = 12%)
      growth_yr6_10 = fade rate, typically half of yr1-5 (e.g. 0.06)
      terminal_growth = long-run GDP growth rate (0.025–0.03)
      wacc          = weighted average cost of capital (0.08–0.12 typical)
      net_debt      = total_debt - cash (negative = net cash position)
    """
    projected_pv = []
    fcf = fcf_ttm

    for yr in range(1, 11):
        g = growth_yr1_5 if yr <= 5 else growth_yr6_10
        fcf *= (1 + g)
        projected_pv.append(fcf / (1 + wacc) ** yr)

    # Terminal value via Gordon Growth Model
    tv = (fcf * (1 + terminal_growth)) / (wacc - terminal_growth)
    pv_tv = tv / (1 + wacc) ** 10

    enterprise_value  = sum(projected_pv) + pv_tv
    equity_value      = enterprise_value - net_debt
    fair_value_share  = equity_value / shares_out

    return {
        "fair_value_per_share": round(fair_value_share, 2),
        "enterprise_value": round(enterprise_value, 0),
        "pv_terminal_value": round(pv_tv, 0),
        "pv_terminal_pct": round(pv_tv / enterprise_value * 100, 1),
        "assumptions": {
            "fcf_ttm": fcf_ttm, "wacc": wacc,
            "growth_yr1_5": growth_yr1_5, "growth_yr6_10": growth_yr6_10,
            "terminal_growth": terminal_growth
        }
    }

def capm_cost_of_equity(risk_free_rate, beta, equity_risk_premium=0.055):
    """CAPM: Re = Rf + β × ERP. ERP default = 5.5% (Damodaran annual estimate)."""
    return risk_free_rate + beta * equity_risk_premium

def wacc_formula(equity_value, debt_value, cost_equity, cost_debt, tax_rate):
    """WACC = (E/V)*Re + (D/V)*Rd*(1-T)"""
    V = equity_value + debt_value
    return (equity_value/V)*cost_equity + (debt_value/V)*cost_debt*(1-tax_rate)
```

---

### Model 7: Reverse DCF (What Growth Is Priced In?)
```python
from scipy.optimize import brentq

def implied_growth_rate(current_price, fcf_per_share, wacc=0.10,
                        terminal_growth=0.03, years=10):
    """
    Asks: "What FCF growth rate must the market be pricing in to justify this price?"
    If implied_growth >> analyst estimates → stock likely overvalued.
    If implied_growth << analyst estimates → stock may be undervalued.
    """
    def dcf_price(g):
        fcf = fcf_per_share
        pv = sum(fcf*(1+g)**yr / (1+wacc)**yr for yr in range(1, years+1))
        fcf_final = fcf * (1+g)**years
        tv = fcf_final*(1+terminal_growth) / (wacc-terminal_growth)
        return pv + tv/(1+wacc)**years - current_price

    try:
        g = brentq(dcf_price, -0.99, 5.0)
        return {"implied_growth_pct": round(g*100, 2)}
    except:
        return {"implied_growth_pct": None, "note": "Could not solve — check inputs"}
```

---

### Model 8: EV/EBITDA Peer Comparables
```python
def ev_ebitda_implied_price(ebitda, sector_median_ev_ebitda, net_debt, shares_out):
    """
    Compares company's EV/EBITDA to sector median to get implied fair price.
    Use sector median from FMP: GET /v4/sector_price_earning_ratio
    """
    if ebitda <= 0:
        return {"implied_price": None, "note": "Negative EBITDA — not applicable"}
    implied_ev     = ebitda * sector_median_ev_ebitda
    implied_equity = implied_ev - net_debt
    implied_price  = implied_equity / shares_out
    return {"implied_price": round(implied_price, 2), "multiple_used": sector_median_ev_ebitda}
```

---

### Model 9: PEG Ratio (Growth-Adjusted Valuation)
```
PEG = (P/E Ratio) / (EPS Annual Growth Rate %)

< 1.0  → Potentially undervalued relative to growth (Peter Lynch threshold)
= 1.0  → Fairly valued
1–2    → Moderately overvalued
> 2.0  → Potentially overvalued
< 0    → Meaningless (negative earnings or negative growth)

Rule: PEG is only useful for companies with POSITIVE earnings AND POSITIVE growth.
```

---

### Model 10: Owner Earnings (Buffett)
```python
def owner_earnings(net_income, depreciation, amortization, capex,
                   working_capital_change, maintenance_capex_fraction=0.60):
    """
    Warren Buffett's "owner earnings" — the true economic earnings of the business.
    Key insight: not all CapEx maintains the business (some is growth investment).
    maintenance_capex_fraction = ~60% for most industrial/retail cos.
                                 ~20% for asset-light tech/software cos.
                                 ~80% for heavy industries/utilities.
    """
    maintenance_capex = capex * maintenance_capex_fraction
    oe = net_income + depreciation + amortization - maintenance_capex - working_capital_change
    return {"owner_earnings": round(oe, 0), "maintenance_capex_used": round(maintenance_capex, 0)}
```

---

### Composite Fair Value (Average All Models)
```python
def composite_fair_value(dcf_price, graham_number, ev_ebitda_implied,
                         analyst_target, fmp_dcf, current_price):
    """
    Average all non-None valuation estimates.
    Returns fair value and margin of safety vs current price.
    """
    estimates = [v for v in [dcf_price, graham_number, ev_ebitda_implied,
                              analyst_target, fmp_dcf] if v and v > 0]
    if not estimates:
        return None

    avg = sum(estimates) / len(estimates)
    mos = (avg - current_price) / current_price * 100  # + = undervalued, - = overvalued

    return {
        "avg_fair_value": round(avg, 2),
        "current_price": current_price,
        "margin_of_safety_pct": round(mos, 1),
        "verdict": "UNDERVALUED" if mos > 20 else "OVERVALUED" if mos < -20 else "FAIRLY_VALUED",
        "estimates_used": len(estimates)
    }
```

---

## 5. FORENSIC RED FLAG DETECTOR

```python
def run_red_flag_scan(curr, prev, prev2=None):
    """
    Automated BS detector.
    curr  = latest annual/TTM data dict
    prev  = 1 year prior
    prev2 = 2 years prior (optional, used for trend checks)

    Required keys in each dict:
      net_income, operating_cf (OCF), fcf, revenue, cogs, receivables,
      total_assets, current_assets, current_liabilities, long_term_debt,
      equity, total_debt, sga, goodwill, shares_outstanding,
      market_cap, ebit, interest_expense (optional),
      insider_net_selling_90d (optional), ppe_net (for Beneish)

    Optional keys: going_concern, restatement, auditor_change, cfo_departure,
                   sec_investigation, goodwill_impairment
    """
    flags = []

    def add(severity, code, detail):
        flags.append({"severity": severity, "code": code, "detail": detail})

    # ═══════════════════════════════════════════
    # CATEGORY 1: EARNINGS vs CASH DIVERGENCE
    # ═══════════════════════════════════════════

    ni_growth  = (curr["net_income"] - prev["net_income"]) / abs(prev["net_income"] or 1)
    ocf_growth = (curr["operating_cf"] - prev["operating_cf"]) / abs(prev["operating_cf"] or 1)

    # Net income rising but OCF falling — earnings not backed by cash
    if ni_growth > 0.10 and ocf_growth < -0.05:
        add("HIGH", "EARNINGS_CASH_DIVERGENCE",
            f"Net income grew {ni_growth:.0%} but operating cash flow fell {ocf_growth:.0%}. "
            f"Earnings are increasing but not producing real cash — classic manipulation signal.")

    # FCF < 50% of reported net income
    ni_yield  = curr["net_income"] / curr.get("market_cap", curr["revenue"])
    fcf_yield = curr["fcf"] / curr.get("market_cap", curr["revenue"])
    if ni_yield > 0 and 0 < fcf_yield < ni_yield * 0.5:
        add("MEDIUM", "FCF_NI_GAP",
            f"Free cash flow is less than half of reported net income. "
            f"Large gap suggests aggressive accrual accounting.")

    # SBC exceeds FCF (SBC-adjusted FCF is actually negative)
    sbc = curr.get("stock_based_compensation", 0)
    if sbc and curr["fcf"] > 0 and sbc > curr["fcf"] * 0.5:
        add("MEDIUM", "SBC_INFLATES_EARNINGS",
            f"Stock-based compensation (${sbc/1e6:.0f}M) exceeds 50% of FCF (${curr['fcf']/1e6:.0f}M). "
            f"Real free cash flow available to shareholders is much lower than reported.")

    # ═══════════════════════════════════════════
    # CATEGORY 2: REVENUE QUALITY
    # ═══════════════════════════════════════════

    rev_growth = (curr["revenue"] - prev["revenue"]) / abs(prev["revenue"] or 1)
    ar_growth  = (curr["receivables"] - prev["receivables"]) / abs(prev["receivables"] or 1)

    # Receivables growing 50% faster than revenue — channel stuffing / fake revenue
    if ar_growth > rev_growth * 1.5 and ar_growth > 0.15:
        add("HIGH", "RECEIVABLES_INFLATION",
            f"Accounts receivable grew {ar_growth:.0%} vs revenue {rev_growth:.0%}. "
            f"Customers aren't paying — possible channel stuffing or fictitious sales.")

    # DSO spike (Days Sales Outstanding)
    dso_curr = (curr["receivables"] / curr["revenue"]) * 365
    dso_prev = (prev["receivables"] / prev["revenue"]) * 365
    if dso_curr > dso_prev * 1.25 and dso_curr > 60:
        add("HIGH", "DSO_SPIKE",
            f"Days Sales Outstanding jumped from {dso_prev:.0f} to {dso_curr:.0f} days. "
            f"Customers are taking much longer to pay — may indicate fictitious or forced sales.")

    # Revenue declining 2+ consecutive years
    if prev2:
        rev_growth_prev = (prev["revenue"] - prev2["revenue"]) / abs(prev2["revenue"] or 1)
        if rev_growth < -0.05 and rev_growth_prev < -0.05:
            add("HIGH", "SUSTAINED_REVENUE_DECLINE",
                f"Revenue declined {rev_growth:.0%} this year and {rev_growth_prev:.0%} last year. "
                f"Sustained decline — evaluate whether this is structural.")

    # ═══════════════════════════════════════════
    # CATEGORY 3: BALANCE SHEET STRESS
    # ═══════════════════════════════════════════

    # Current ratio < 1 — can't cover short-term obligations
    curr_ratio = curr["current_assets"] / curr["current_liabilities"]
    if curr_ratio < 1.0:
        add("HIGH", "LIQUIDITY_CRISIS",
            f"Current ratio = {curr_ratio:.2f}. Company may not be able to meet "
            f"short-term obligations. Watch for credit line draws or asset sales.")

    # Goodwill > 30% of total assets
    goodwill_pct = curr.get("goodwill", 0) / curr["total_assets"]
    if goodwill_pct > 0.30:
        add("MEDIUM", "GOODWILL_HEAVY",
            f"Goodwill = {goodwill_pct:.0%} of total assets. "
            f"Heavy acquisition reliance. Risk: future impairment write-downs could wipe out equity.")

    # Goodwill impairment this year
    if curr.get("goodwill_impairment", 0) > 0:
        add("HIGH", "GOODWILL_IMPAIRMENT",
            f"Goodwill impairment of ${curr['goodwill_impairment']/1e6:.0f}M charged this year. "
            f"Past acquisition overpaid for or underperforming.")

    # Debt/Equity > 3.0 (not banks)
    de = curr["total_debt"] / (curr["equity"] or 1)
    if de > 3.0:
        add("HIGH", "HIGH_LEVERAGE",
            f"Debt/Equity = {de:.1f}x. Highly leveraged — vulnerable to rising rates, "
            f"refinancing risk, and downturns.")

    # Interest coverage < 1.5x
    ebit = curr.get("ebit", curr.get("operating_income"))
    int_exp = curr.get("interest_expense")
    if ebit and int_exp and int_exp > 0:
        ic = ebit / int_exp
        if ic < 1.5:
            add("HIGH", "INTEREST_COVERAGE_DANGER",
                f"Interest coverage = {ic:.2f}x. EBIT barely covering interest payments. "
                f"One bad quarter could create a default risk.")

    # Net Debt/EBITDA > 5x
    ebitda = curr.get("ebitda")
    net_debt_val = curr["total_debt"] - curr.get("cash", 0)
    if ebitda and ebitda > 0 and net_debt_val / ebitda > 5:
        add("HIGH", "NET_DEBT_EBITDA_HIGH",
            f"Net Debt/EBITDA = {net_debt_val/ebitda:.1f}x. "
            f"Very high leverage relative to earnings power.")

    # ═══════════════════════════════════════════
    # CATEGORY 4: MANAGEMENT / GOVERNANCE
    # ═══════════════════════════════════════════

    # Insider net selling > $5M in 90 days
    insider_sell = curr.get("insider_net_selling_90d", 0)
    if insider_sell > 5_000_000:
        add("MEDIUM", "INSIDER_SELLING",
            f"Insiders net sold ${insider_sell/1e6:.1f}M in last 90 days. "
            f"Context: routine post-vest selling vs concentrated CEO/CFO selling matters.")

    # Share dilution > 3% YoY
    dilution = (curr["shares_outstanding"] - prev["shares_outstanding"]) / prev["shares_outstanding"]
    if dilution > 0.03:
        add("MEDIUM", "SHARE_DILUTION",
            f"Shares outstanding increased {dilution:.1%} YoY. "
            f"Earnings per share are being diluted. Check if acquisitions justify this.")

    # SG&A growing faster than revenue for 2 consecutive years
    if prev2:
        sga_g1 = (curr["sga"] - prev["sga"]) / abs(prev["sga"] or 1)
        sga_g2 = (prev["sga"] - prev2["sga"]) / abs(prev2["sga"] or 1)
        rev_g2 = (prev["revenue"] - prev2["revenue"]) / abs(prev2["revenue"] or 1)
        if sga_g1 > rev_growth * 1.3 and sga_g2 > rev_g2 * 1.3:
            add("MEDIUM", "SGA_BLOAT_TREND",
                f"SG&A growing faster than revenue for 2 consecutive years. "
                f"Operational leverage not materializing — management efficiency declining.")

    # ═══════════════════════════════════════════
    # CATEGORY 5: STRUCTURAL / REGULATORY
    # ═══════════════════════════════════════════

    if curr.get("going_concern"):
        add("CRITICAL", "GOING_CONCERN",
            "Auditor has expressed going concern doubt in the most recent filing. "
            "Company survival is genuinely at risk. Extreme caution.")

    if curr.get("restatement"):
        add("CRITICAL", "PRIOR_PERIOD_RESTATEMENT",
            f"Prior period financial restatement filed. "
            f"Reason: {curr.get('restatement_reason', 'unknown')}. "
            f"Fundamental question: what else in the financials is wrong?")

    if curr.get("auditor_change"):
        add("MEDIUM", "AUDITOR_CHANGE",
            f"Auditor changed to {curr.get('new_auditor', 'unknown')}. "
            f"Investigate: Was the change due to fees, disagreements, or scope issues?")

    if curr.get("cfo_departure"):
        add("MEDIUM", "CFO_DEPARTURE",
            "CFO departed in last 90 days. The CFO is the person most likely to know "
            "if financials are being manipulated. Watch for restatements.")

    if curr.get("sec_investigation"):
        add("CRITICAL", "SEC_INVESTIGATION",
            "Active SEC investigation disclosed. Regulatory risk — potential enforcement action, "
            "fines, or forced restatements.")

    # ═══════════════════════════════════════════
    # SORT AND RETURN
    # ═══════════════════════════════════════════
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    flags.sort(key=lambda x: order.index(x["severity"]))

    return {
        "total_flags": len(flags),
        "critical": sum(1 for f in flags if f["severity"] == "CRITICAL"),
        "high":     sum(1 for f in flags if f["severity"] == "HIGH"),
        "medium":   sum(1 for f in flags if f["severity"] == "MEDIUM"),
        "flags":    flags
    }
```

---

## 6. DATA RECENCY & FRESHNESS SYSTEM

```python
from datetime import datetime

def calculate_freshness(filing_date_str: str, form_type: str = "10-Q") -> dict:
    """
    Every analysis must include a freshness profile so the AI knows how stale the data is.
    SEC requires 10-Q filings within 40-45 days of quarter end.
    10-K filings within 60-90 days of fiscal year end.
    """
    filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
    today = datetime.now()
    days_old = (today - filing_date).days
    quarters_behind = days_old // 90

    if days_old <= 30:
        score, label, color = 100, "Very Fresh", "GREEN"
    elif days_old <= 60:
        score, label, color = 90, "Fresh", "GREEN"
    elif days_old <= 90:
        score, label, color = 75, "Current Quarter", "YELLOW"
    elif days_old <= 135:
        score, label, color = 55, "One Quarter Behind", "YELLOW"
    elif days_old <= 180:
        score, label, color = 35, "Two Quarters Behind", "ORANGE"
    elif days_old <= 365:
        score, label, color = 20, "Very Stale — Annual Filing Only", "RED"
    else:
        score, label, color = 5, "EXTREMELY STALE — Over 1 Year", "RED"

    uncertainty = ""
    if days_old > 90:
        uncertainty = (
            f"WARNING: Financial data is {days_old} days old ({quarters_behind} quarters behind). "
            f"The following may have materially changed since this filing: "
            f"(1) Revenue trajectory — one quarter can flip the narrative. "
            f"(2) Management guidance — may have been raised/lowered. "
            f"(3) Debt levels — refinancing, new issuance, or paydown. "
            f"(4) Macro context — interest rates, inflation, and sector conditions evolve. "
            f"Recommend: Check any 8-K filings and most recent earnings call transcript "
            f"before acting on this analysis."
        )

    return {
        "filing_date": filing_date_str,
        "form_type": form_type,
        "days_old": days_old,
        "quarters_behind": quarters_behind,
        "freshness_score": score,
        "freshness_label": label,
        "freshness_color": color,
        "uncertainty_warning": uncertainty,
        "is_stale": days_old > 135,
        "is_critically_stale": days_old > 270,
        "price_gap_risk": days_old > 45,   # Stock price has likely moved since filing
    }
```

---

## 7. COMPLETE DATA PIPELINE

```python
"""
COMPLETE DATA GATHERING FUNCTION
Pulls everything needed for the master AI analysis.
Run this once per ticker, feed the result into the AI prompt.
"""

import yfinance as yf
import requests
import pandas as pd
from datetime import datetime
from edgar import Company, set_identity

set_identity("Your Name your@email.com")  # Required for SEC EDGAR

FMP_KEY  = "YOUR_FMP_KEY"
FMP_BASE = "https://financialmodelingprep.com/api"
FRED_KEY = "YOUR_FRED_KEY"


def fmp_get(endpoint: str) -> dict | list:
    sep = "&" if "?" in endpoint else "?"
    try:
        r = requests.get(f"{FMP_BASE}{endpoint}{sep}apikey={FMP_KEY}", timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def gather_all_data(ticker: str) -> dict:
    data = {"ticker": ticker, "timestamp": datetime.now().isoformat(), "errors": []}

    # ── 1. PRICE & MARKET DATA (yfinance) ───────────────────────────
    try:
        t = yf.Ticker(ticker)
        info = t.info
        data.update({
            "company_name":   info.get("longName"),
            "sector":         info.get("sector"),
            "industry":       info.get("industry"),
            "exchange":       info.get("exchange"),
            "employees":      info.get("fullTimeEmployees"),
            "description":    info.get("longBusinessSummary", "")[:400],
            "price":          info.get("currentPrice") or info.get("regularMarketPrice"),
            "market_cap":     info.get("marketCap"),
            "beta":           info.get("beta"),
            "high_52w":       info.get("fiftyTwoWeekHigh"),
            "low_52w":        info.get("fiftyTwoWeekLow"),
            "avg_volume":     info.get("averageVolume"),
            "pct_52w":        info.get("52WeekChange"),
            "pe_ttm":         info.get("trailingPE"),
            "pe_fwd":         info.get("forwardPE"),
            "pb":             info.get("priceToBook"),
            "ps":             info.get("priceToSalesTrailing12Months"),
            "de_ratio":       info.get("debtToEquity"),
            "roe":            info.get("returnOnEquity"),
            "roa":            info.get("returnOnAssets"),
            "gross_margin":   info.get("grossMargins"),
            "op_margin":      info.get("operatingMargins"),
            "net_margin":     info.get("profitMargins"),
            "rev_growth_yoy": info.get("revenueGrowth"),
            "eps_growth":     info.get("earningsGrowth"),
            "fcf_yf":         info.get("freeCashflow"),
            "ocf_yf":         info.get("operatingCashflow"),
            "total_cash":     info.get("totalCash"),
            "total_debt":     info.get("totalDebt"),
            "current_ratio":  info.get("currentRatio"),
            "quick_ratio":    info.get("quickRatio"),
            "dividend_yield": info.get("dividendYield"),
            "payout_ratio":   info.get("payoutRatio"),
            "target_price":   info.get("targetMeanPrice"),
            "analyst_count":  info.get("numberOfAnalystOpinions"),
            "analyst_rec":    info.get("recommendationMean"),  # 1=Strong Buy, 5=Sell
            "shares_out":     info.get("sharesOutstanding"),
            "float_shares":   info.get("floatShares"),
            "short_ratio":    info.get("shortRatio"),
            "short_pct_float":info.get("shortPercentOfFloat"),
        })

        # Financial statements (quarterly for recency, annual for trends)
        data["income_q"]  = t.quarterly_financials.to_dict()   if not t.quarterly_financials.empty else {}
        data["balance_q"] = t.quarterly_balance_sheet.to_dict() if not t.quarterly_balance_sheet.empty else {}
        data["cashflow_q"]= t.quarterly_cashflow.to_dict()     if not t.quarterly_cashflow.empty else {}
        data["income_a"]  = t.financials.to_dict()             if not t.financials.empty else {}
        data["balance_a"] = t.balance_sheet.to_dict()          if not t.balance_sheet.empty else {}

        # Insider + ownership
        data["insider_txns"]   = t.insider_transactions.to_dict("records") if not t.insider_transactions.empty else []
        data["inst_holders"]   = t.institutional_holders.to_dict("records") if not t.institutional_holders.empty else []
        data["major_holders"]  = t.major_holders.to_dict("records") if not t.major_holders.empty else []

        # Analyst recommendations
        data["analyst_recs"] = t.recommendations.to_dict("records") if not t.recommendations.empty else []

    except Exception as e:
        data["errors"].append(f"yfinance: {e}")

    # ── 2. SEC EDGAR DATA ────────────────────────────────────────────
    try:
        company = Company(ticker)
        filings_10q = company.get_filings(form="10-Q")
        filings_10k = company.get_filings(form="10-K")
        filings_8k  = company.get_filings(form="8-K")

        # Latest filing metadata
        if filings_10q:
            latest = filings_10q[0]
            data["last_filing_type"] = "10-Q"
            data["last_filing_date"] = str(latest.filing_date)
            data["freshness"]        = calculate_freshness(str(latest.filing_date), "10-Q")
        elif filings_10k:
            latest = filings_10k[0]
            data["last_filing_type"] = "10-K"
            data["last_filing_date"] = str(latest.filing_date)
            data["freshness"]        = calculate_freshness(str(latest.filing_date), "10-K")

        # 8-K events (material events, earnings pre-announcements, leadership changes)
        data["recent_8k"] = [
            {"date": str(f.filing_date), "description": str(f.description)[:200]}
            for f in list(filings_8k)[:10]
        ]

    except Exception as e:
        data["errors"].append(f"edgartools: {e}")
        data["freshness"] = {"freshness_score": 0, "freshness_label": "Unknown", "is_stale": True}

    # ── 3. FMP PRE-COMPUTED SCORES & RATIOS ─────────────────────────
    try:
        ratios  = fmp_get(f"/v3/ratios/{ticker}?limit=4")
        metrics = fmp_get(f"/v3/key-metrics/{ticker}?limit=4")
        scores  = fmp_get(f"/v4/score?symbol={ticker}")
        dcf     = fmp_get(f"/v3/discounted-cash-flow/{ticker}")
        income  = fmp_get(f"/v3/income-statement/{ticker}?period=quarter&limit=8")
        balance = fmp_get(f"/v3/balance-sheet-statement/{ticker}?period=quarter&limit=4")
        cashflow= fmp_get(f"/v3/cash-flow-statement/{ticker}?limit=4")
        insider = fmp_get(f"/v4/insider-trading?symbol={ticker}&limit=20")
        analyst = fmp_get(f"/v3/analyst-estimates/{ticker}?limit=4")
        outlook = fmp_get(f"/v4/company-outlook?symbol={ticker}")

        if isinstance(ratios, list) and ratios:
            r = ratios[0]
            data.update({
                "ev_ebitda":        r.get("enterpriseValueMultiple"),
                "pfcf":             r.get("priceToFreeCashFlowsRatio"),
                "ev_revenue":       r.get("priceToSalesRatio"),
                "interest_cov":     r.get("interestCoverage"),
                "roic":             r.get("returnOnCapitalEmployed"),
                "asset_turnover":   r.get("assetTurnover"),
                "inventory_days":   r.get("daysOfInventoryOnHand"),
                "dso":              r.get("daysOfSalesOutstanding"),
                "dpo":              r.get("daysPayablesOutstanding"),
            })

        if isinstance(metrics, list) and metrics:
            m = metrics[0]
            data.update({
                "ev":           m.get("enterpriseValue"),
                "roic_fmp":     m.get("roic"),
                "fcf_yield":    m.get("freeCashFlowYield"),
                "peg":          m.get("pegRatio"),
                "rev_per_share":m.get("revenuePerShare"),
                "fcf_per_share":m.get("freeCashFlowPerShare"),
                "bvps":         m.get("bookValuePerShare"),
            })

        if isinstance(scores, list) and scores:
            data["altman_z"]    = scores[0].get("altmanZScore")
            data["piotroski_f"] = scores[0].get("piotroskiScore")

        if isinstance(dcf, list) and dcf:
            data["dcf_fmp"] = dcf[0].get("dcf")

        if isinstance(income, list):
            data["fmp_income_q"] = income[:8]   # 8 quarters

        if isinstance(balance, list):
            data["fmp_balance_q"] = balance[:4]

        if isinstance(cashflow, list):
            data["fmp_cashflow"] = cashflow[:4]

        if isinstance(insider, list):
            data["fmp_insider"] = insider[:20]

        if isinstance(analyst, list) and analyst:
            a = analyst[0]
            data["fwd_rev_estimate"] = a.get("estimatedRevenueAvg")
            data["fwd_eps_estimate"] = a.get("estimatedEpsAvg")

    except Exception as e:
        data["errors"].append(f"FMP: {e}")

    # ── 4. MACRO CONTEXT (FRED) ──────────────────────────────────────
    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_KEY)
        data["risk_free_rate"] = float(fred.get_series("DGS10").iloc[-1]) / 100
        data["inflation_rate"] = float(fred.get_series("CPIAUCSL").pct_change(12).iloc[-1])
        data["fed_funds_rate"] = float(fred.get_series("FEDFUNDS").iloc[-1]) / 100
        data["credit_spread"]  = float(fred.get_series("BAMLH0A0HYM2").iloc[-1]) / 100
    except Exception as e:
        data["errors"].append(f"FRED: {e}")
        data["risk_free_rate"] = 0.045   # Fallback estimates
        data["inflation_rate"] = 0.030
        data["fed_funds_rate"] = 0.053
        data["credit_spread"]  = 0.035

    return data


def format_for_ai_prompt(data: dict) -> str:
    """
    Converts gathered data dict into the structured AI prompt.
    See Section 8 for the full prompt template.
    This function fills in the {PLACEHOLDERS} automatically.
    """
    price  = data.get("price", "N/A")
    mktcap = data.get("market_cap", 0)
    fresh  = data.get("freshness", {})

    def fmt_m(v):
        """Format millions/billions."""
        if not v: return "N/A"
        if abs(v) >= 1e9: return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
        return f"${v:.0f}"

    def pct(v):
        if not v: return "N/A"
        return f"{v*100:.1f}%"

    lines = [
        f"TICKER: {data['ticker']}",
        f"COMPANY: {data.get('company_name', 'N/A')}",
        f"SECTOR: {data.get('sector', 'N/A')} | INDUSTRY: {data.get('industry', 'N/A')}",
        f"EMPLOYEES: {data.get('employees', 'N/A'):,}" if data.get('employees') else "EMPLOYEES: N/A",
        "",
        f"CURRENT PRICE: ${price}",
        f"MARKET CAP: {fmt_m(mktcap)}",
        f"ENTERPRISE VALUE: {fmt_m(data.get('ev'))}",
        f"52W RANGE: ${data.get('low_52w', 'N/A')} – ${data.get('high_52w', 'N/A')}",
        f"52W CHANGE: {pct(data.get('pct_52w'))}",
        f"BETA: {data.get('beta', 'N/A')}",
        f"SHORT INTEREST: {pct(data.get('short_pct_float'))} of float",
        "",
        f"DATA FRESHNESS:",
        f"  Last Filing: {data.get('last_filing_type', 'N/A')} filed {data.get('last_filing_date', 'N/A')}",
        f"  Freshness Score: {fresh.get('freshness_score', 0)}/100 — {fresh.get('freshness_label', 'N/A')}",
        f"  {fresh.get('uncertainty_warning', '')}",
        "",
        f"VALUATION RATIOS:",
        f"  P/E (TTM): {data.get('pe_ttm', 'N/A')}x | Forward P/E: {data.get('pe_fwd', 'N/A')}x",
        f"  P/B: {data.get('pb', 'N/A')}x | P/S: {data.get('ps', 'N/A')}x | P/FCF: {data.get('pfcf', 'N/A')}x",
        f"  EV/EBITDA: {data.get('ev_ebitda', 'N/A')}x | PEG: {data.get('peg', 'N/A')}",
        f"  FCF Yield: {pct(data.get('fcf_yield'))} | Dividend Yield: {pct(data.get('dividend_yield'))}",
        "",
        f"PROFITABILITY:",
        f"  Gross Margin: {pct(data.get('gross_margin'))} | Op Margin: {pct(data.get('op_margin'))} | Net Margin: {pct(data.get('net_margin'))}",
        f"  ROE: {pct(data.get('roe'))} | ROA: {pct(data.get('roa'))} | ROIC: {pct(data.get('roic_fmp') or data.get('roic'))}",
        "",
        f"LIQUIDITY & SOLVENCY:",
        f"  Current Ratio: {data.get('current_ratio', 'N/A')} | Quick Ratio: {data.get('quick_ratio', 'N/A')}",
        f"  Debt/Equity: {data.get('de_ratio', 'N/A')} | Interest Coverage: {data.get('interest_cov', 'N/A')}x",
        f"  Total Debt: {fmt_m(data.get('total_debt'))} | Cash: {fmt_m(data.get('total_cash'))}",
        "",
        f"FORENSIC SCORES:",
        f"  Altman Z-Score: {data.get('altman_z', 'N/A')}",
        f"  Piotroski F-Score: {data.get('piotroski_f', 'N/A')}/9",
        f"  FMP DCF Estimate: ${data.get('dcf_fmp', 'N/A')}",
        "",
        f"MACRO CONTEXT:",
        f"  10-Year Treasury (Risk-Free Rate): {data.get('risk_free_rate', 0)*100:.2f}%",
        f"  Fed Funds Rate: {data.get('fed_funds_rate', 0)*100:.2f}%",
        f"  Inflation (CPI YoY): {data.get('inflation_rate', 0)*100:.2f}%",
        f"  HY Credit Spread: {data.get('credit_spread', 0)*100:.2f}%",
        "",
        f"ANALYST CONSENSUS:",
        f"  Mean Target: ${data.get('target_price', 'N/A')} | Analysts: {data.get('analyst_count', 'N/A')}",
        f"  Recommendation Mean: {data.get('analyst_rec', 'N/A')} (1=Strong Buy, 5=Sell)",
        "",
        f"RECENT 8-K EVENTS (Last 10):",
    ]

    for event in (data.get("recent_8k") or [])[:5]:
        lines.append(f"  {event['date']}: {event['description']}")

    if not data.get("recent_8k"):
        lines.append("  None found or data unavailable.")

    return "\n".join(lines)
```

---

## 8. THE MASTER AI PROMPT

**Complete, ready-to-paste prompt for Qwen 2.5 (or any LLM).**
**Pipe the output of `format_for_ai_prompt(data)` into the USER section below.**

```
══════════════════════════════════════════════════════════════════════
SYSTEM PROMPT
══════════════════════════════════════════════════════════════════════

You are an elite financial analyst combining the methodologies of:
- Benjamin Graham (value investing, margin of safety, balance sheet analysis)
- Howard Marks (risk assessment, market cycle awareness)
- Seth Klarman (deep value, downside protection)
- Forensic accounting (Beneish, Sloan — earnings quality and manipulation detection)
- Modern quantitative analysis (Altman, Piotroski, DCF, reverse DCF)

Your PURPOSE is to determine:
  1. Is this company's current stock price justified by its financial fundamentals?
  2. Is the company financially healthy, or are there hidden risks?
  3. Are the financial statements trustworthy, or are there manipulation signals?
  4. What is the most likely investment signal: BUY / HOLD / AVOID / STRONG AVOID?

MANDATORY RULES you MUST follow:
- Ground ALL analysis strictly in the numbers provided. NEVER invent data.
- When data is stale (Freshness Score < 75), explicitly qualify your confidence.
- Separate what you KNOW from what you are INFERRING.
- Call out manipulation signals even if they are uncomfortable.
- Note when a scoring model is not applicable to the company's sector.
- Produce ACTIONABLE output — vague analysis helps no one.
- This is financial analysis, NOT financial advice. Be clear about this distinction.

══════════════════════════════════════════════════════════════════════
USER PROMPT
══════════════════════════════════════════════════════════════════════

Perform a COMPLETE investment analysis on the company data below.
Structure your response EXACTLY as the 12 sections specified.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[PASTE THE OUTPUT OF format_for_ai_prompt(data) HERE]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NOW PRODUCE YOUR ANALYSIS IN EXACTLY THESE 12 SECTIONS:

────────────────────────────────────────────────────────────────
SECTION 1 — EXECUTIVE VERDICT
────────────────────────────────────────────────────────────────
In 3–5 sentences:
- What is this company and what does it actually do?
- Is the current stock price justified by the fundamentals?
- What is the single most important thing an investor needs to know right now?

────────────────────────────────────────────────────────────────
SECTION 2 — INVESTMENT SIGNAL
────────────────────────────────────────────────────────────────
Signal: [STRONG BUY / BUY / HOLD / AVOID / STRONG AVOID]
Confidence: [HIGH / MEDIUM / LOW] — state why (e.g., "LOW because data is 2 quarters stale")
Time Horizon: [SHORT (0–6mo) / MEDIUM (6–18mo) / LONG (2–5yr)]
Margin of Safety: [ADEQUATE / INSUFFICIENT / NEGATIVE]

Price Assessment: State whether the stock appears UNDERVALUED / FAIRLY VALUED / OVERVALUED
  and by approximately what % based on all available valuation methods.

IF any CRITICAL red flags exist → signal CANNOT be higher than AVOID regardless of valuation.

────────────────────────────────────────────────────────────────
SECTION 3 — VALUATION ANALYSIS
────────────────────────────────────────────────────────────────
Interpret EVERY valuation metric provided:
- P/E, Forward P/E, P/B, P/S, P/FCF, EV/EBITDA, PEG, FCF Yield
- Compare each to: historical norms (sector), what they imply about expectations
- FMP DCF estimate: is this reasonable? What assumptions would it require?
- Analyst consensus target: what do analysts see that others might not?

State which valuation methods ARE and ARE NOT applicable to this company/sector.
For example: "Graham Number not meaningful here — negative book value" or
             "Altman Z-Score unreliable — this is a bank."

Produce a FAIR VALUE RANGE: Low estimate / Base estimate / High estimate
How does the current price compare to this range?

────────────────────────────────────────────────────────────────
SECTION 4 — FINANCIAL HEALTH ASSESSMENT
────────────────────────────────────────────────────────────────
Score financial health 1–10 (10 = fortress balance sheet, 1 = pre-bankruptcy).

Evaluate:
LIQUIDITY: Current ratio, quick ratio — can they pay bills?
SOLVENCY:  Debt/Equity, Interest Coverage, Net Debt/EBITDA — are they over-leveraged?
CASH GENERATION: OCF trend, FCF trend, FCF margin — is the business producing real cash?
PROFITABILITY: ROE, ROA, ROIC trends — is the business earning above its cost of capital?
EFFICIENCY: Asset turnover, DSO, inventory days — is management running it well?

Overall Health Score: X/10 — [FORTRESS / HEALTHY / ADEQUATE / STRETCHED / DISTRESSED]

────────────────────────────────────────────────────────────────
SECTION 5 — EARNINGS QUALITY & FORENSIC ANALYSIS
────────────────────────────────────────────────────────────────
Interpret the forensic scores:

ALTMAN Z-SCORE: [Score] → [Zone] — what does this mean for this specific company?
  (Note if sector makes this score unreliable)

PIOTROSKI F-SCORE: [Score]/9 → [Verdict] — which specific signals contributed?
  (Which of the 9 criteria are passing? Which are failing?)

BENEISH M-SCORE: [Score] → [Verdict] — deep interpretation required:
  - Which components are elevated?
  - DSRI: are receivables growing suspiciously fast?
  - GMI: is gross margin trending down (pressure to manipulate)?
  - TATA: are accruals high (earnings not backed by cash)?
  - SGAI: is overhead growing faster than revenue?

SLOAN ACCRUAL RATIO: [%] → [Quality Level]

OVERALL EARNINGS QUALITY VERDICT: [HIGH / MEDIUM / LOW / VERY LOW]
Explain: "We believe the reported earnings [DO / DO NOT] accurately reflect the
          economic reality of this business because..."

────────────────────────────────────────────────────────────────
SECTION 6 — RED FLAG ANALYSIS
────────────────────────────────────────────────────────────────
List ALL red flags identified, grouped by severity:

CRITICAL (if any):
- [Flag name]: [What exactly this means for investors]

HIGH SEVERITY (if any):
- [Flag name]: [Specific detail and investment implication]

MEDIUM SEVERITY (if any):
- [Flag name]: [Context and what to watch]

NO FLAGS: State clearly if none were found.

Overall Credibility Assessment: Do the financial statements appear credible?
Rate: [HIGH CREDIBILITY / ADEQUATE CREDIBILITY / QUESTIONABLE / LOW CREDIBILITY]

────────────────────────────────────────────────────────────────
SECTION 7 — GROWTH & BUSINESS TRAJECTORY
────────────────────────────────────────────────────────────────
- Revenue trend: accelerating / steady / decelerating / declining?
- Margin trend: expanding / compressing / stable?
- Is the business gaining or losing competitive position based on the numbers?
- What does the most recent earnings data (8-K events, if listed) suggest about current conditions?
- Are analyst forward estimates believable given the trajectory?

────────────────────────────────────────────────────────────────
SECTION 8 — MACRO & MARKET CONTEXT
────────────────────────────────────────────────────────────────
Given the provided macro data (risk-free rate, inflation, credit spreads):
- How does the current interest rate environment affect this company's valuation?
  (Higher rates = lower DCF values, especially for growth stocks)
- How does the current rate environment affect their debt burden?
- Is the sector tailwind or headwind in the current macro environment?
- What credit spread tells us about overall market risk appetite

────────────────────────────────────────────────────────────────
SECTION 9 — INSIDER & INSTITUTIONAL SIGNALS
────────────────────────────────────────────────────────────────
Interpret insider trading data:
- Is net insider activity buying or selling?
- Context: routine RSU vesting vs. discretionary open-market purchases
- Clustered buying (multiple insiders same timeframe) = strong signal
- CEO/CFO selling large blocks = watch closely

Institutional ownership:
- High institutional ownership = validation but also crowded-trade risk
- Recent institutional buying or selling (if data available)?

────────────────────────────────────────────────────────────────
SECTION 10 — BULL CASE vs BEAR CASE
────────────────────────────────────────────────────────────────
BULL CASE (What would need to be true for this to be a great investment):
1. [Specific quantitative condition]
2. [Specific business outcome]
3. [Valuation/multiple re-rating scenario]
If bull case: target price = $[X] within [timeframe]

BEAR CASE (What could go seriously wrong):
1. [Specific risk factor with quantitative impact]
2. [Execution risk / competitive risk]
3. [Balance sheet / liquidity risk]
If bear case materializes: downside target = $[X] (–[Y]% from current)

────────────────────────────────────────────────────────────────
SECTION 11 — DATA QUALITY & CONFIDENCE ASSESSMENT
────────────────────────────────────────────────────────────────
Freshness: [Score]/100 — [Label]
Confidence in this analysis: [HIGH / MEDIUM / LOW]
Key data gaps or limitations:
- [List any metrics that were N/A and how that affects the analysis]
- [Any sector-specific caveats]
- [What additional information would materially change the conclusion]

Recommended additional research:
- [ ] Check latest 8-K for any post-filing material events
- [ ] Read most recent earnings call transcript for management tone/guidance
- [ ] Compare to 2–3 direct competitors on the same metrics
- [ ] Check short seller reports (if high short interest)
- [ ] Review most recent proxy statement for executive compensation structure

────────────────────────────────────────────────────────────────
SECTION 12 — FINAL SCORECARD
────────────────────────────────────────────────────────────────
| Dimension                  | Score (1–10) | Comment                          |
|----------------------------|--------------|----------------------------------|
| Valuation (price vs value) |      X       | [one line]                       |
| Financial Health           |      X       | [one line]                       |
| Earnings Quality           |      X       | [one line]                       |
| Growth Trajectory          |      X       | [one line]                       |
| Management Quality         |      X       | [one line based on signals]      |
| Risk Profile               |      X       | [one line]                       |
| Data Freshness             |      X       | [one line]                       |
| OVERALL INVESTMENT SCORE   |      X       | [summary]                        |

FINAL VERDICT: [STRONG BUY / BUY / HOLD / AVOID / STRONG AVOID]
Confidence: [HIGH / MEDIUM / LOW]
One-sentence summary: "[Company] at $[price] is a [signal] because [core reason]."
```

---

## 9. SECTOR-SPECIFIC ADJUSTMENTS

The AI should apply these when scoring model applicability is noted in Section 3/5:

| Sector | Altman Z | Graham Number | Piotroski F | Best Primary Metrics |
|---|---|---|---|---|
| **Technology / SaaS** | Unreliable | Not useful | Moderate | P/FCF, EV/Revenue, Rule of 40, NRR |
| **Banks / Insurance** | NOT APPLICABLE | Not useful | NOT APPLICABLE | P/TBV, NIM, Efficiency Ratio, Tier 1 Capital |
| **Manufacturing** | Most reliable | Useful | Most reliable | All standard models apply |
| **Healthcare / Biotech** | Unreliable | Not useful | Limited | P/Pipeline, Cash Runway, Rev growth |
| **Energy / Utilities** | Moderate | Limited | Moderate | EV/EBITDA, Yield, Payout Ratio, Reserve Life |
| **Real Estate (REIT)** | NOT APPLICABLE | Not useful | NOT APPLICABLE | FFO/AFFO, Cap Rate, Debt/Asset, Yield |
| **Retail** | Moderate | Limited | Useful | Inventory turns, Same-store sales, Op leverage |
| **Pre-revenue** | Not applicable | Not applicable | Not applicable | Cash runway, burn rate, TAM |

**Rule of 40 (SaaS):** Revenue Growth % + FCF Margin % ≥ 40 = healthy
**NRR (SaaS):** Net Revenue Retention > 120% = excellent, < 100% = churn problem
**NIM (Banks):** Net Interest Margin > 3% = healthy
**FFO (REITs):** Use FFO/share instead of EPS — real estate depreciation distorts GAAP

---

## 10. RECOMMENDED APP ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                    STOCK ANALYSIS APP                           │
├─────────────────────────────────────────────────────────────────┤
│  FRONTEND (React / Next.js / Streamlit)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ Ticker Input  │  │ Filters Panel │  │ Analysis Dashboard    │ │
│  │ Search        │  │ - Date range  │  │ - Scorecard           │ │
│  │ Compare mode  │  │ - Sector      │  │ - Valuation chart     │ │
│  │               │  │ - Score min   │  │ - Red flags           │ │
│  │               │  │ - Market cap  │  │ - Freshness badge     │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│  BACKEND (Python / FastAPI)                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ gather_all_data(ticker)  → data dict                     │   │
│  │   ├── yfinance (price, ratios, statements)               │   │
│  │   ├── edgartools (SEC filings, dates, 8-K events)        │   │
│  │   ├── FMP (Altman Z, Piotroski F, DCF)                   │   │
│  │   └── FRED (risk-free rate, inflation, macro)            │   │
│  │                                                           │   │
│  │ run_scoring_models(data) → scores dict                   │   │
│  │   ├── beneish_m_score()                                  │   │
│  │   ├── sloan_accrual_ratio()                              │   │
│  │   ├── dcf_intrinsic_value()                              │   │
│  │   ├── graham_number()                                    │   │
│  │   └── composite_fair_value()                            │   │
│  │                                                           │   │
│  │ run_red_flag_scan(data) → flags list                     │   │
│  │                                                           │   │
│  │ format_for_ai_prompt(data) → formatted string           │   │
│  └──────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│  AI LAYER (Qwen 2.5 via Ollama)                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ ollama.chat(model="qwen2.5:72b",                         │   │
│  │   messages=[                                             │   │
│  │     {"role":"system", "content": SYSTEM_PROMPT},        │   │
│  │     {"role":"user",   "content": formatted_data}        │   │
│  │   ])                                                     │   │
│  └──────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│  CACHING & STORAGE                                              │
│  ├── Redis / SQLite — cache data for 4 hours per ticker        │
│  ├── ~/{ticker}_analysis_{date}.json — save past analyses      │
│  └── simfin bulk data — local cache for offline fundamental    │
└─────────────────────────────────────────────────────────────────┘
```

### Qwen 2.5 via Ollama (Local)

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull Qwen 2.5 (pick based on your VRAM)
ollama pull qwen2.5:7b    # 4GB VRAM — fast, less detailed
ollama pull qwen2.5:14b   # 8GB VRAM — good balance
ollama pull qwen2.5:32b   # 16GB VRAM — excellent
ollama pull qwen2.5:72b   # 40GB VRAM — best financial reasoning
```

```python
import ollama

def analyze_stock(ticker: str) -> str:
    # 1. Gather data
    data = gather_all_data(ticker)

    # 2. Run scoring models (add Beneish, DCF, etc. to data dict here)
    # ... (call the functions from Section 4 and add results to data)

    # 3. Run red flag scan
    # flags = run_red_flag_scan(curr, prev)
    # data["red_flags"] = flags

    # 4. Format for AI
    prompt_data = format_for_ai_prompt(data)

    # 5. Call Qwen 2.5
    response = ollama.chat(
        model="qwen2.5:32b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},   # From Section 8
            {"role": "user",   "content": USER_PROMPT_TEMPLATE.replace("{DATA}", prompt_data)}
        ],
        options={"temperature": 0.1, "num_ctx": 8192}       # Low temp = more factual
    )

    return response["message"]["content"]
```

---

## FILTERS FOR THE UI

```
RECENCY FILTER
  □ Only show analyses with Freshness Score ≥ 75 (current quarter)
  □ Show all but badge stale data with warning
  □ Show only — filed in last 30 days

VALUATION FILTER
  □ Undervalued only (Margin of Safety > 20%)
  □ Fairly valued (MoS -20% to +20%)
  □ Any valuation

SCORE FILTERS
  □ Altman Z > 2.99 (safe zone only)
  □ Piotroski F-Score ≥ 7 (strong fundamentals)
  □ Beneish M-Score < -1.78 (likely not manipulating)
  □ Sloan Accrual Ratio < 5% (high earnings quality)

RED FLAG FILTER
  □ No critical flags
  □ No high severity flags
  □ Show all including flagged

SIGNAL FILTER
  □ Strong Buy only
  □ Buy or Strong Buy
  □ All signals

SECTOR FILTER
  □ [Multi-select dropdown]

MARKET CAP FILTER
  □ Large Cap (>$10B)
  □ Mid Cap ($2B–$10B)
  □ Small Cap ($300M–$2B)
  □ Any
```

---

*Built for: AI-powered public company financial statement analysis*
*Primary data: SEC EDGAR (free) + yfinance + FMP + FRED*
*AI engine: Qwen 2.5 (local via Ollama)*
*Last updated: 2026-03-04*
