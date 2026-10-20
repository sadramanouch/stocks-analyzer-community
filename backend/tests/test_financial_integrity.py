import unittest

from financial_integrity import (annual_facts, extract_annual_financials, dividend_yield_pct,
                                 debt_equity_ratio, free_cash_flow, statement_value, xbrl_total_debt, net_debt_amount)


def fact(value, end="2025-12-31", start="2025-01-01", filed="2026-02-01", **extra):
    result = dict(val=value, end=end, filed=filed, form="10-K", fp="FY", accn="test", **extra)
    if start:
        result["start"] = start
    return result


class FinancialIntegrityTests(unittest.TestCase):
    def test_missing_debt_is_unknown_and_never_substituted_with_long_term_debt(self):
        self.assertIsNone(xbrl_total_debt({'long_term_debt': 50}))
        self.assertIsNone(net_debt_amount(None, 30))
        self.assertIsNone(net_debt_amount(50, None))
        self.assertEqual(xbrl_total_debt({'debt_noncurrent': 50, 'debt_current_portion': 10, 'short_term_borrowings': 5}), 65)
        self.assertEqual(net_debt_amount(0, 30), -30)

    def test_missing_dataframe_value_cannot_shift_current_or_prior_years(self):
        import pandas as pd
        frame = pd.DataFrame([[None, 50, 40]], index=['Revenue'],
                             columns=pd.to_datetime(['2025-12-31', '2024-12-31', '2023-12-31']))
        self.assertIsNone(statement_value(frame, ['Revenue'], '2025-12-31'))
        self.assertEqual(statement_value(frame, ['Revenue'], '2025-12-31', 1), 50)
        self.assertIsNone(statement_value(frame, ['Revenue'], '2026-12-31'))

    def test_rejects_quarters_carried_in_annual_filings(self):
        values = [fact(100), fact(30, start="2025-10-01", filed="2026-03-01")]
        self.assertEqual(annual_facts(values, True)[0]["val"], 100)

    def test_keeps_53_week_year_and_instant_balance(self):
        self.assertEqual(len(annual_facts([fact(100, start="2024-12-26")], True)), 1)
        self.assertEqual(annual_facts([fact(200, start=None)], False)[0]["val"], 200)
        self.assertEqual(annual_facts([fact(200, start=None)], True), [])

    def test_rejects_non_finite_and_future_values(self):
        self.assertEqual(annual_facts([fact(float('nan')), fact(float('inf')), fact(1, filed="2099-01-01")]), [])

    def test_as_of_does_not_see_later_amendments(self):
        values = [fact(100), fact(90, filed="2026-04-01")]
        self.assertEqual(annual_facts(values, True, "2026-03-01")[0]["val"], 100)
        self.assertEqual(annual_facts(values, True, "2026-05-01")[0]["val"], 90)

    def test_recent_alias_wins_and_old_years_are_not_mixed(self):
        payload = {"facts": {"us-gaap": {
            "OldRevenue": {"units": {"USD": [fact(10, end="2018-12-31", start="2018-01-01", filed="2019-02-01")]}},
            "NewRevenue": {"units": {"USD": [fact(200)]}},
            "NetIncomeLoss": {"units": {"USD": [fact(40)]}},
            "OldGrossProfit": {"units": {"USD": [fact(5, end="2018-12-31", start="2018-01-01", filed="2019-02-01")]}},
        }}}
        data = extract_annual_financials(payload, {"revenue": ("OldRevenue", "NewRevenue"),
            "net_income": ("NetIncomeLoss",), "gross_profit": ("OldGrossProfit",)}, ())
        self.assertEqual(data["revenue"], 200)
        self.assertEqual(data["_provenance"]["revenue"]["concept"], "NewRevenue")
        self.assertNotIn("gross_profit", data)

    def test_missing_prior_year_is_not_replaced_with_two_years_ago(self):
        values = [fact(100), fact(70, end="2023-12-31", start="2023-01-01", filed="2024-02-01")]
        payload = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": values}}}}}
        data = extract_annual_financials(payload, {"revenue": ("Revenue",)}, ())
        self.assertNotIn("revenue_prior", data)
        self.assertEqual(data["revenue_prior2"], 70)

    def test_dividend_yield_uses_cash_per_share_not_ambiguous_percent(self):
        self.assertEqual(dividend_yield_pct({"dividendRate": 1.08, "dividendYield": .32}, 336.64), .3208)
        self.assertEqual(dividend_yield_pct({"dividendRate": 0}, 100), 0)
        self.assertIsNone(dividend_yield_pct({"dividendYield": .32}, 336.64))
        self.assertIsNone(dividend_yield_pct({"dividendRate": 1}, 0))

    def test_debt_equity_is_a_ratio_and_negative_equity_is_not_low_risk(self):
        self.assertEqual(debt_equity_ratio(80, 100), .8)
        self.assertIsNone(debt_equity_ratio(80, -100))
        self.assertIsNone(debt_equity_ratio(None, 100))

    def test_missing_capex_does_not_invent_free_cash_flow(self):
        self.assertIsNone(free_cash_flow(100, None))
        self.assertIsNone(free_cash_flow(None, 20))
        self.assertEqual(free_cash_flow(100, -20), 80)
        self.assertEqual(free_cash_flow(100, 20), 80)
        self.assertEqual(free_cash_flow(20, 20), 0)
