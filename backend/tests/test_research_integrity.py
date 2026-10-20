import unittest
from research_integrity import equity_value, implied_growth, instrument_eligibility, valuation_scenario

class ResearchIntegrityTests(unittest.TestCase):
    def test_perpetuity_and_discount_sensitivity(self):
        self.assertAlmostEqual(equity_value(100,0,.1,0),1000,places=8)
        self.assertAlmostEqual(equity_value(100,0,.2,0),500,places=8)
        self.assertGreater(equity_value(100,.1,.1),equity_value(100,.1,.12))
        for args in [(100,0,.025,.025),(None,0,.1,0),(100,-1,.1,0),(float('inf'),0,.1,0)]:
            self.assertIsNone(equity_value(*args))
    def test_reverse_solver_recovers_growth_and_rejects_unbracketed_target(self):
        target=equity_value(100,.07,.11,fade=False)
        self.assertAlmostEqual(implied_growth(100,target,.11),.07,places=8)
        self.assertIsNone(implied_growth(100,1e20,.1))
    def test_instrument_and_sector_exclusions(self):
        for name in ['Example 7.25% Senior Notes due 2028','Example Preferred','Example Warrants','Example Units']:
            self.assertEqual(instrument_eligibility(name,'EQUITY')['status'],'excluded')
        self.assertEqual(instrument_eligibility('Company','ETF')['status'],'excluded')
        self.assertEqual(instrument_eligibility('Bank','EQUITY',sector='Financial Services')['status'],'excluded')
        self.assertEqual(instrument_eligibility('Company')['status'],'unverified')
        self.assertEqual(instrument_eligibility('Company',security_type='CS')['status'],'eligible')
    def test_shares_and_growth_required_and_sensitivity_reproducible(self):
        info=dict(longName='Example Common Stock',quoteType='EQUITY',sharesOutstanding=100,currentPrice=20,marketCap=2000)
        result=valuation_scenario(info,100,[(110,100)])
        self.assertEqual(result['status'],'scenario'); self.assertEqual(len(result['sensitivity']),9)
        self.assertEqual(result['price'],round(equity_value(100,.1,.1)/100,2))
        for altered in [dict(info,sharesOutstanding=None),dict(info,marketCap=100_000)]:
            self.assertEqual(valuation_scenario(altered,100,[(110,100)])['status'],'unavailable')
        self.assertEqual(valuation_scenario(info,100,[])['status'],'unavailable')
