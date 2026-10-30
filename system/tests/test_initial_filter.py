import sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"skill/jobradar/scripts")]
import json, unittest
from functools import partial
from pathlib import Path
import initial_filter

# The shipped example instance doubles as the rule fixture, so the template stays valid.
TEMPLATE = Path(__file__).resolve().parents[2] / 'templates/instance/system/config/preferences.json'
RULES = initial_filter.check(json.loads(TEMPLATE.read_text())['hard_rules'])
evaluate = partial(initial_filter.evaluate, rules=RULES)

class InitialFilterTest(unittest.TestCase):
    def salary(self,low,high,**kw):
        return evaluate(dict(region='mainland',annual_salary_cny=dict(min=low,max=high,basis='gross_total',scope_confirmed=True,evidence='synthetic official JD'),**kw))
    def test_unknown_is_retained(self):
        x=evaluate({'region':'mainland'}); self.assertEqual(x['outcome'],'进入第二轮'); self.assertTrue(x['unknowns'])
    def test_crossing_absolute_threshold_not_rejected(self):
        self.assertNotEqual(self.salary(100000,250000)['outcome'],'直接淘汰')
        self.assertEqual(self.salary(100000,119999)['outcome'],'直接淘汰')
    def test_group_or_internship_pay_cannot_reject(self):
        self.assertEqual(evaluate(dict(region='mainland',annual_salary_cny=dict(min=10000,max=20000,basis='daily_intern',scope_confirmed=False)))['outcome'],'进入第二轮')
    def test_location_must_apply_to_all_options(self):
        self.assertEqual(evaluate(dict(region='mainland',all_locations_excluded=False))['outcome'],'进入第二轮')
        self.assertEqual(evaluate(dict(all_locations_excluded=True,all_locations_excluded_evidence='synthetic solely northeast location'))['outcome'],'直接淘汰')
    def test_no_rejection_without_evidence(self):
        with self.assertRaises(ValueError): evaluate(dict(forbidden_identity=True))
    def test_location_exception_and_nonwaivable_absolute_floor(self):
        self.assertEqual(self.salary(130000,175000,beijing_only=True)['outcome'],'例外保留')
        self.assertEqual(self.salary(100000,110000,beijing_only=True)['outcome'],'直接淘汰')
    def test_benefits_million_exception(self):
        self.assertEqual(self.salary(800000,1200000,benefits_deficient=True)['outcome'],'例外保留')
        self.assertEqual(self.salary(300000,400000,benefits_deficient=True,benefits_deficient_evidence='synthetic no contributions')['outcome'],'直接淘汰')
    def test_international_and_travel_no_automatic_failure(self):
        self.assertEqual(evaluate(dict(region='international',frequent_travel=True))['outcome'],'进入第二轮')
    def test_explicit_nonexception_fails_but_unknown_stays(self):
        for region,flag,code in [('international','net_savings_below_floor','NET_SAVINGS'),
                                 ('mainland','sustained_intensity','INTENSITY')]:
            facts=dict(region=region,exceptional_opportunity=False,exceptional_opportunity_evidence='reviewed ordinary role')
            facts.update({flag:True,flag+'_evidence':'synthetic official requirement'})
            self.assertEqual(evaluate(facts)['rule_hits'][0]['code'],code)
            facts.pop('exceptional_opportunity_evidence')
            self.assertEqual(evaluate(facts)['outcome'],'例外保留')
        self.assertEqual(self.salary(130000,170000,beijing_only=True,beijing_salary_exception=False,
            beijing_salary_exception_evidence='reviewed no qualifying compensation')['outcome'],'直接淘汰')
    def test_no_exception_can_override_absolute_floor(self):
        self.assertEqual(self.salary(90000,110000,beijing_only=True,beijing_salary_exception=True,
            beijing_salary_exception_evidence='exceptional platform')['outcome'],'直接淘汰')
    def test_omitted_sections_are_not_conditions(self):
        bare=initial_filter.evaluate(dict(region='mainland',benefits_deficient=True,sustained_intensity=True),{'version':'none'})
        self.assertEqual((bare['outcome'],bare['unknowns']),('进入第二轮',[]))
        self.assertEqual(initial_filter.evaluate(dict(closed=True,closed_evidence='official notice'),{'version':'none'})['outcome'],'直接淘汰')
    def test_malformed_rules_refused(self):
        for bad in [{}, {'version':'x','salary':{}}, {'version':'x','mainland_salary':{'absolute_floor_cny':'15万'}},
                    {'version':'x','review_notes':[{'fact':'x'}]}, {'version':'x','mainland_salary':dict(RULES['mainland_salary'],absolute_floor_cny=-1)}]:
            with self.assertRaises(ValueError): initial_filter.check(bad)

if __name__=='__main__': unittest.main()
