"""Financial input normalization; no market opinions or missing-value estimates."""
import math
from datetime import date

SCHEMA_VERSION = 5
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
FLOW_FIELDS = {"revenue", "net_income", "operating_cf", "capex", "da", "ebit",
               "gross_profit", "cogs", "investing_cf"}


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def annual_facts(entries, duration=None, as_of=None):
    """Choose actual full-year durations, not every fact carried in a 10-K.

    An optional as-of cutoff excludes later filings/restatements. This alone is
    not a complete point-in-time backtest dataset.
    """
    cutoff = as_of or date.today().isoformat()
    best = {}
    for fact in entries:
        if fact.get("form") not in ANNUAL_FORMS or finite(fact.get("val")) is None:
            continue
        end, filed, start = fact.get("end", ""), fact.get("filed", ""), fact.get("start")
        try:
            end_date = date.fromisoformat(end)
            date.fromisoformat(filed)
            if not filed or filed > cutoff or end > cutoff or filed < end:
                continue
            if start:
                days = (end_date - date.fromisoformat(start)).days + 1
                if duration is False or not 335 <= days <= 395:
                    continue
            elif duration is True:
                continue
        except (ValueError, TypeError):
            continue
        previous = best.get(end)
        if previous is None or (filed, fact.get("accn", "")) > (previous.get("filed", ""), previous.get("accn", "")):
            best[end] = fact
    return sorted(best.values(), key=lambda fact: fact["end"], reverse=True)


def extract_annual_financials(payload, concept_map, share_concepts, as_of=None):
    namespaces = payload.get("facts", {})
    gaap = namespaces.get("us-gaap", {})
    candidates = {}
    for field, concepts in {**concept_map, "shares": share_concepts}.items():
        unit = "shares" if field == "shares" else "USD"
        entries = []
        for priority, concept in enumerate(concepts):
            namespace = "dei" if concept.startswith("Entity") else "us-gaap"
            data = namespaces.get(namespace, gaap).get(concept, {})
            for fact in annual_facts(data.get("units", {}).get(unit, []), field in FLOW_FIELDS, as_of):
                entries.append({**fact, "concept": concept, "namespace": namespace, "priority": priority})
        candidates[field] = entries
    # Fix one annual period for the financial statements. An obsolete alias
    # must never cause 2018 revenue to be combined with 2025 income/assets.
    anchors = [fact["end"] for field in ("revenue", "net_income", "operating_cf")
               for fact in candidates.get(field, [])]
    if not anchors:
        anchors = [fact["end"] for fact in candidates.get("total_assets", [])]
    if not anchors:
        return {}
    anchor = max(anchors)
    result = {"_schema_version": SCHEMA_VERSION, "_period_end": anchor, "_provenance": {}}
    anchor_date = date.fromisoformat(anchor)
    for field, entries in candidates.items():
        for offset, suffix in enumerate(("", "_prior", "_prior2")):
            eligible = [fact for fact in entries if
                        (fact["end"] == anchor if offset == 0 else
                         abs((anchor_date - date.fromisoformat(fact["end"])).days - 365 * offset) <= 30)]
            if not eligible:
                continue
            chosen = max(eligible, key=lambda fact: (fact["end"], fact["filed"], -fact["priority"]))
            key = field + suffix
            result[key] = finite(chosen["val"])
            result[key + "_date"] = chosen["end"]
            result["_provenance"][key] = {key: chosen.get(key) for key in
                ("concept", "namespace", "start", "end", "filed", "accn", "form")}
            result["_provenance"][key].update(value=finite(chosen['val']), unit='shares' if field == 'shares' else 'USD')
            cik = str(payload.get('cik', ''))
            accn = chosen.get('accn', '')
            if cik.isdigit() and len(accn.replace('-', '')) == 18:
                result['_provenance'][key]['source_url'] = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{accn}-index.htm"
    return result


def dividend_yield_pct(info, price):
    """Annual cash dividend per share / quote; avoids ambiguous provider units."""
    price = finite(price)
    for key in ("dividendRate", "trailingAnnualDividendRate"):
        rate = finite(info.get(key))
        if rate is not None and rate >= 0 and price is not None and price > 0:
            return round(rate / price * 100, 4)
    return None


def debt_equity_ratio(total_debt, equity):
    debt, equity = finite(total_debt), finite(equity)
    if debt is None or debt < 0 or equity is None or equity <= 0:
        return None
    return round(debt / equity, 4)


def xbrl_total_debt(data):
    """Require all non-overlapping debt components; absent is not zero."""
    parts = [finite(data.get(field)) for field in
             ('debt_noncurrent', 'debt_current_portion', 'short_term_borrowings')]
    return sum(parts) if all(value is not None and value >= 0 for value in parts) else None


def net_debt_amount(total_debt, cash):
    debt, cash = finite(total_debt), finite(cash)
    return debt - cash if debt is not None and cash is not None else None


def free_cash_flow(operating_cf, capex):
    operating_cf, capex = finite(operating_cf), finite(capex)
    return None if operating_cf is None or capex is None else operating_cf - abs(capex)


def statement_value(frame, keys, period_end, offset=0):
    """Read one annual period without skipping a missing year or mixing frames."""
    if frame is None or frame.empty or not period_end:
        return None
    anchor = date.fromisoformat(period_end)
    candidates = [column for column in frame.columns if
                  (str(column.date()) == period_end if offset == 0 else
                   abs((anchor - column.date()).days - 365 * offset) <= 30)]
    if not candidates:
        return None
    column = max(candidates)
    for key in keys:
        if key in frame.index:
            value = finite(frame.loc[key, column])
            if value is not None:
                return value
    return None
