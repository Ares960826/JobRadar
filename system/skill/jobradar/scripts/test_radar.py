import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import radar
import workbook as wb

PROJECT=Path(__file__).resolve().parents[4]
TEMPLATE=PROJECT/'templates/instance'
rules=radar.ruleset(json.loads((TEMPLATE/'system/config/preferences.json').read_text())['hard_rules'],'fixture')

class RadarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out=radar.paths(self.root,'pilot')
        radar.write(radar.support(self.out)/'plan.json',{'max_records':3,'instances':['mainland','international']})
        self.row=dict(company='Example',title='Researcher',location='上海',region='mainland',kind='role',
          official_url='https://example.org/job/1',record_key='1',source_name='official',
          evidence='Public official listing',checked_at='2026-09-18T12:00:00+00:00',availability='listed',facts={},
          quota_note='未核到：官网公告未写投递数量限制')
    def test_ids_seeded_above_existing_batches_and_shared_between_runs(self):
        radar.write(radar.paths(self.root,'earlier')/'_support/records.json',[{'job_id':'JOB-000155'}])
        radar.ingest(self.root,self.out,[copy.deepcopy(self.row)])
        self.assertEqual(radar.read(radar.support(self.out)/'records.json')[0]['job_id'],'JOB-000156')
        other=radar.paths(self.root,'second');radar.write(radar.support(other)/'plan.json',{'max_records':3})
        radar.ingest(self.root,other,[copy.deepcopy(self.row)])
        self.assertEqual(radar.read(radar.support(other)/'records.json')[0]['job_id'],'JOB-000156')
    def test_tracking_url_dedup_and_older_update_rejected(self):
        r=copy.deepcopy(self.row);radar.ingest(self.root,self.out,[r])
        r['official_url']+='?utm_source=test#fragment';r['title']='OLD'
        r['checked_at']='2026-09-18T13:00:00+02:00'
        radar.ingest(self.root,self.out,[r])
        actual=radar.read(radar.support(self.out)/'records.json');self.assertEqual(len(actual),1)
        self.assertEqual(actual[0]['title'],'Researcher')
    def test_budget_and_validation_are_atomic(self):
        radar.ingest(self.root,self.out,[copy.deepcopy(self.row)])
        before=(radar.support(self.out)/'records.json').read_bytes()
        batch=[dict(copy.deepcopy(self.row),record_key=str(i)) for i in range(8)]
        with self.assertRaises(ValueError):radar.ingest(self.root,self.out,batch)
        self.assertEqual(before,(radar.support(self.out)/'records.json').read_bytes())
        with self.assertRaises(ValueError):radar.ingest(self.root,self.out,[{'company':'broken'}])
        self.assertEqual(before,(radar.support(self.out)/'records.json').read_bytes())
    def test_unknown_retained_closed_rejected(self):
        r=copy.deepcopy(self.row);c=dict(copy.deepcopy(r),record_key='closed',availability='closed')
        radar.ingest(self.root,self.out,[r,c])
        with patch.object(radar,'rules',return_value=rules):radar.screen(self.root,self.out)
        rows=radar.read(radar.support(self.out)/'records.json')
        self.assertEqual(rows[0]['initial_filter']['outcome'],'进入第二轮')
        self.assertTrue(rows[0]['initial_filter']['unknowns'])
        self.assertEqual(rows[1]['initial_filter']['outcome'],'直接淘汰')
    def test_evidenceless_rejection_stops(self):
        r=copy.deepcopy(self.row);r['facts']={'ineligible':True}
        radar.ingest(self.root,self.out,[r])
        with patch.object(radar,'rules',return_value=rules),self.assertRaises(ValueError):radar.screen(self.root,self.out)
        self.assertNotIn('initial_filter',radar.read(radar.support(self.out)/'records.json')[0])
    def test_path_and_config_boundaries(self):
        with self.assertRaises(ValueError):radar.paths(self.root,'../2026-09-17_第一轮筛选池')
        evil=radar.paths(self.root,'evil');evil.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):radar.paths(self.root,'evil')
        with self.assertRaises(ValueError):radar.collect_ats(self.root,self.out,{'board':'x','name':'../escape','limit':1})
    def test_changed_constraints_stop(self):
        with self.assertRaises(ValueError):radar.rules(self.root)
        self.assertFalse(radar.rule_status(self.root)['ready'])
    def test_example_instance_rules_follow_the_reviewed_document(self):
        shutil.copytree(TEMPLATE,self.root,dirs_exist_ok=True)
        status=radar.rule_status(self.root)
        self.assertTrue(status['ready'],status)
        self.assertEqual(status['fingerprint'],radar.rules(self.root).fingerprint)
        doc=self.root/'Profile/hard-conditions.md';doc.write_text(doc.read_text()+'\n- 新增条件\n')
        with self.assertRaisesRegex(ValueError,'Hard-condition document changed'):radar.rules(self.root)
        pref=radar.read(self.root/'system/config/preferences.json')
        pref['hard_constraint_sha256']=radar.digest(doc);pref['hard_rules'].pop('version')
        radar.write(self.root/'system/config/preferences.json',pref)
        with self.assertRaisesRegex(ValueError,'version'):radar.rules(self.root)
    def test_collection_without_official_url_keeps_provenance(self):
        r=copy.deepcopy(self.row);r.pop('official_url');r.update(source_tier=2,source_url='https://example.org/collection?record=1')
        radar.ingest(self.root,self.out,[r]);actual=radar.read(radar.support(self.out)/'records.json')[0]
        self.assertEqual(actual['channel'],'paid_aggregation');self.assertEqual(actual['observations'][0]['source_tier'],2)
        self.assertNotIn('official_url',actual)
    def test_conflicting_closure_cannot_eliminate(self):
        r=copy.deepcopy(self.row);r.update(availability='closed',conflicts=['official page and collection disagree on deadline'])
        radar.ingest(self.root,self.out,[r])
        with patch.object(radar,'rules',return_value=rules):radar.screen(self.root,self.out)
        self.assertNotEqual(radar.read(radar.support(self.out)/'records.json')[0]['initial_filter']['outcome'],'直接淘汰')
    def test_export_writes_workbook_that_closeout_accepts(self):
        self.begin();radar.ingest(self.root,self.out,[copy.deepcopy(self.row),dict(copy.deepcopy(self.row),record_key='2',company='=cmd()')])
        with patch.object(radar,'rules',return_value=rules):
            radar.screen(self.root,self.out)
            receipt=radar.export_workbook(self.root,self.out)
            self.assertTrue(radar.closeout(self.root,self.out)['passed'])
        self.assertEqual(receipt['workbook_runtime'],wb.VERSION)
        grid=wb.read_sheets((self.out/wb.FILE).read_bytes())[wb.SHEET]
        self.assertEqual([grid[(4,i)] for i in range(18)],wb.HEADERS)
        self.assertEqual((grid[(5,0)],grid[(5,5)],grid[(6,2)]),('JOB-000001','未决定',"'=cmd()"))
    def begin(self):
        plan={'scope':'synthetic test','max_records':3,'instances':['mainland','international']}
        radar.write(radar.support(self.out)/'plan.json',plan)
        radar.begin(self.root,self.out,plan)
    def export_fixture(self):
        (self.out/'岗位初筛工作表.xlsx').write_bytes(b'synthetic export fixture')
        radar.write(radar.support(self.out)/'export-receipt.json',dict(
            records_sha256=radar.digest(radar.support(self.out)/'records.json'),
            workbook_sha256=radar.digest(self.out/'岗位初筛工作表.xlsx')))
    def test_closeout_requires_current_screen_workbook_and_rules(self):
        self.begin()
        radar.ingest(self.root,self.out,[copy.deepcopy(self.row)])
        with patch.object(radar,'rules',return_value=rules):
            radar.screen(self.root,self.out)
            self.assertTrue(radar.closeout(self.root,self.out)['passed'])  # the local workbook is optional
            self.begin(); self.export_fixture()
            self.assertTrue(radar.closeout(self.root,self.out)['passed'])
            for spoil in (lambda f:f.write_bytes(b'edited by hand'),lambda f:f.unlink()):
                self.begin(); self.export_fixture(); spoil(self.out/'岗位初筛工作表.xlsx')
                result=radar.closeout(self.root,self.out)
                self.assertFalse(result['passed']);self.assertIn('local workbook',' '.join(result['problems']))
            self.export_fixture()
            self.begin()
            radar.ingest(self.root,self.out,[dict(self.row,title='updated')])
            result=radar.closeout(self.root,self.out)
            self.assertFalse(result['passed']);self.assertIn('stale',' '.join(result['problems']))
        self.begin(); self.export_fixture()
        self.assertFalse(radar.closeout(self.root,self.out)['passed'])
    def test_closeout_rejects_grades(self):
        self.begin();radar.ingest(self.root,self.out,[dict(self.row,grade='A')])
        with patch.object(radar,'rules',return_value=rules):
            radar.screen(self.root,self.out);self.export_fixture()
            self.assertIn('second-stage',' '.join(radar.closeout(self.root,self.out)['problems']))
    def test_run_lease_blocks_other_batches_and_stage2(self):
        self.begin()
        with self.assertRaises(ValueError):
            radar.ingest(self.root,radar.paths(self.root,'other'),[copy.deepcopy(self.row)])
        self.assertFalse(radar.closeout(self.root,self.out)['passed'])
        radar.write(self.root/'system/state/active-run.json',{'run':'stage2','stage':2})
        with self.assertRaises(ValueError):radar.screen(self.root,self.out)
        with self.assertRaises(ValueError):self.begin()
    def test_operation_mutex_covers_screen_and_export(self):
        mutex=self.root/'system/state/stage1-operation.lock'
        mutex.mkdir(parents=True)
        with self.assertRaises(ValueError):radar.screen(self.root,self.out)
        with self.assertRaises(ValueError):radar.export_workbook(self.root,self.out)
        self.assertTrue(mutex.exists())
    def test_diagnostic_empty_is_not_search_and_requires_evidence(self):
        out=radar.paths(self.root,'diagnostic','validation')
        plan={'mode':'diagnostic','scope':'synthetic','max_records':0,'required_checks':['one']}
        radar.begin(self.root,out,plan)
        with patch.object(radar,'rules',return_value=rules):
            self.assertFalse(radar.closeout(self.root,out)['passed'])
            radar.begin(self.root,out,plan)
            log=out/'check.log';log.write_text('test output')
            radar.write(radar.support(out)/'checks.json',{'one':{'passed':True,'evidence':'check.log','sha256':radar.digest(log)}})
            self.assertTrue(radar.closeout(self.root,out)['passed'])
            radar.begin(self.root,out,plan);log.write_text('changed')
            self.assertFalse(radar.closeout(self.root,out)['passed'])
        with self.assertRaises(ValueError):radar.begin(self.root,radar.paths(self.root,'bad'),plan)
    def test_closeout_detects_changes_to_preexisting_dirty_files(self):
        with patch.object(radar,'guarded_state',return_value={'AGENTS.md':'before'}):self.begin()
        with patch.object(radar,'guarded_state',return_value={'AGENTS.md':'after'}):
            result=radar.closeout(self.root,self.out)
        self.assertIn('AGENTS.md',result['project_changes_outside_run'])
        self.assertFalse(result['passed'])
    def test_export_rejects_changed_implementation(self):
        radar.ingest(self.root,self.out,[copy.deepcopy(self.row)])
        with patch.object(radar,'rules',return_value=rules):
            radar.screen(self.root,self.out)
            summary=radar.read(radar.support(self.out)/'summary.json')
            summary['rules']=dict(summary['rules'],implementation_sha256='old implementation')
            radar.write(radar.support(self.out)/'summary.json',summary)
            with self.assertRaises(ValueError):radar.export_workbook(self.root,self.out)
    def test_diagnostic_cli_refuses_collection_before_any_network(self):
        from types import SimpleNamespace
        out=radar.paths(self.root,'diagnostic','validation')
        radar.begin(self.root,out,{'mode':'diagnostic','scope':'test','max_records':0})
        with self.assertRaisesRegex(ValueError,'cannot collect'):
            radar.dispatch(SimpleNamespace(command='collect-feishu'),self.root,out)
    def test_amend_records_user_scope_change_and_keeps_budget(self):
        out=radar.paths(self.root,'amended');plan={'scope':'groups','instances':['mainland'],'max_records':2}
        radar.begin(self.root,out,plan);radar.ingest(self.root,out,[copy.deepcopy(self.row),dict(copy.deepcopy(self.row),record_key='2')])
        bigger=dict(plan,max_records=5,priority=['banks','operators'])
        with self.assertRaises(ValueError):radar.amend(self.root,out,bigger,' ')
        with self.assertRaises(ValueError):radar.amend(self.root,out,dict(plan,max_records=1),'user: shrink')
        with self.assertRaises(ValueError):radar.amend(self.root,out,dict(bigger,priority='banks'),'user: x')
        with self.assertRaises(ValueError):radar.amend(self.root,out,dict(bigger,mode='maintenance'),'user: x')
        mout=radar.paths(self.root,'maint','validation');mplan={'mode':'maintenance','scope':'m','max_records':0,'allowed_changes':['a']}
        with patch.object(radar,'require_lease'),self.assertRaisesRegex(ValueError,'search plans'):
            radar.write(radar.support(mout)/'plan.json',mplan);radar.amend(self.root,mout,dict(mplan,allowed_changes=['a','b']),'x')
        self.assertEqual(radar.amend(self.root,out,bigger,'user: 央企最优先')['max_records'],5)
        self.assertEqual(radar.read(radar.support(out)/'plan.json'),bigger)
        self.assertEqual(radar.read(radar.support(out)/'amendments.json')[0]['previous'],plan)
        radar.ingest(self.root,out,[dict(copy.deepcopy(self.row),record_key=str(i)) for i in range(3,6)])
        with self.assertRaisesRegex(ValueError,'amend'):radar.begin(self.root,out,plan)
        from types import SimpleNamespace
        with self.assertRaisesRegex(ValueError,'other than'):
            radar.dispatch(SimpleNamespace(command='init',plan=radar.support(out)/'plan.json'),self.root,out)
        self.assertEqual(radar.begin(self.root,out,bigger)['run'],'amended')
    def test_possible_duplicates_across_batches(self):
        old=radar.paths(self.root,'2026-09-20_old')
        radar.write(radar.support(old)/'records.json',[{'机会编号':'JOB-000181','公司｜岗位':'远达信人工智能科技（北京）｜2027校招视觉算法工程师'},
            {'job_id':'JOB-000223','company':'远航移动 / 青穹','title':'算法开发岗（多模态大模型）-新星苗（北京）'}])
        rows=[{'job_id':'JOB-000246','company':'远达信人工智能科技(北京)有限公司(云帆AI)','title':'视觉算法工程师'},
              {'job_id':'JOB-000224','company':'远航移动 / 青穹','title':'算法开发岗（智能体与强化学习）-新星苗（北京）'},
              {'job_id':'JOB-000300','company':'Example','title':'Researcher'}]
        found={d['job_id']:[m['job_id'] for m in d['matches']] for d in radar.possible_duplicates(self.root,rows)}
        self.assertEqual(found,{'JOB-000246':['JOB-000181']})
        radar.ingest(self.root,self.out,[dict(copy.deepcopy(self.row),company='远达信人工智能科技有限公司',title='视觉算法工程师')])
        self.assertEqual(radar.read(radar.support(self.out)/'ingest-receipt.json')['possible_duplicates'][0]['matches'][0]['job_id'],'JOB-000181')
    def test_feishu_auth_reports_missing_or_expired_login(self):
        from types import SimpleNamespace
        with patch.object(radar.subprocess,'run',side_effect=FileNotFoundError('lark-cli')):
            self.assertFalse(radar.feishu_auth()['ready'])
        expired=json.dumps({'identities':{'user':{'status':'expired','available':False,'message':'token expired'}}})
        with patch.object(radar.subprocess,'run',return_value=SimpleNamespace(stdout=expired)):
            self.assertEqual(radar.feishu_auth(),{'ready':False,'detail':'token expired','refresh_expires_at':None})
        real=json.dumps({'appId':'cli_x','identity':'user','identities':{'bot':{'status':'ready','available':True},
            'user':{'status':'ready','available':True,'message':'User identity: ready','tokenStatus':'valid',
                    'refreshExpiresAt':'2099-10-13T19:01:41+01:00'}}})  # shape of lark-cli auth status, 2026-10-06
        with patch.object(radar.subprocess,'run',return_value=SimpleNamespace(stdout=real)):
            self.assertEqual(radar.feishu_auth()['ready'],True);self.assertNotIn('warning',radar.feishu_auth())
        lapsed=json.dumps({'identities':{'user':{'status':'needs_refresh','available':True,'tokenStatus':'needs_refresh',
            'message':'User identity: needs refresh (will auto-refresh on next user API call)',
            'refreshExpiresAt':'2099-10-13T19:01:41+01:00'}}})  # observed 2026-10-06
        with patch.object(radar.subprocess,'run',return_value=SimpleNamespace(stdout=lapsed)):
            self.assertTrue(radar.feishu_auth()['ready'])
        with patch.object(radar.subprocess,'run',return_value=SimpleNamespace(stdout='[]')):
            self.assertFalse(radar.feishu_auth()['ready'])
        soon=json.dumps({'identities':{'user':{'status':'ready','available':True,'message':'ok','refreshExpiresAt':radar.now()}}})
        with patch.object(radar.subprocess,'run',return_value=SimpleNamespace(stdout=soon)):
            self.assertIn('warning',radar.feishu_auth())
    def test_contract_label_alone_is_not_identity_evidence(self):
        bare=dict(copy.deepcopy(self.row),facts={'forbidden_identity':True,'forbidden_identity_evidence':'岗位性质：合同工'})
        with self.assertRaisesRegex(ValueError,'合同工'):radar.ingest(self.root,self.out,[bare])
        sure=dict(copy.deepcopy(self.row),facts={'forbidden_identity':True,'forbidden_identity_evidence':'岗位性质：合同工；公告：劳务派遣用工'})
        self.assertEqual(radar.ingest(self.root,self.out,[sure])['new'],1)
    def test_closed_recurring_programme_must_reach_the_watchlist(self):
        r=dict(copy.deepcopy(self.row),availability='closed',kind='program',
               recurring_note='公告写明每年分两批招募（官网公告链接）')
        receipt=radar.ingest(self.root,self.out,[r])
        self.assertEqual(receipt['watch_missing'],['JOB-000001'])
        self.begin()
        with patch.object(radar,'rules',return_value=rules):
            radar.screen(self.root,self.out);self.export_fixture()
            self.assertIn('watchlist',' '.join(radar.closeout(self.root,self.out)['problems']))
        entry=dict(id='example-fellowship',name='Example fellowship',official_url='https://example.org/job/1',
                   cycle='每年两批（2026年公告）',next_window='估计：2027年1月公告，依据2026年公告日期',
                   recheck_on='2027-01-05',job_ids=['JOB-000001'])
        radar.watch_add(self.root,entry,today='2026-10-10')
        self.assertEqual(radar.watch_missing(self.root,radar.read(radar.support(self.out)/'records.json')),[])
        self.assertEqual(radar.watch_due(self.root,'2027-01-04'),[])
        self.assertEqual(radar.watch_due(self.root,'2027-01-05')[0]['id'],'example-fellowship')
        radar.watch_add(self.root,dict(entry,job_ids=['JOB-000002'],recheck_on='2027-03-01'),today='2026-12-01')
        saved=radar.read(radar.watch_path(self.root))['entries'][0]
        self.assertEqual((saved['job_ids'],saved['added'],saved['updated']),(['JOB-000001','JOB-000002'],'2026-10-10','2026-12-01'))
        radar.watch_add(self.root,dict(entry,status='closed'))
        self.assertEqual(radar.watch_due(self.root,'2030-01-01'),[])
        for bad in [dict(entry,next_window='2027年1月'),dict(entry,recheck_on='明年'),dict(entry,job_ids=['318']),
                    dict(entry,official_url='postdoctor'),dict(entry,extra=1),dict(entry,status='done')]:
            with self.assertRaises(ValueError):radar.watch_add(self.root,bad)
        with self.assertRaises(ValueError):radar.ingest(self.root,self.out,[dict(r,recurring_note=1)])

    def test_watch_update_keeps_unnamed_facts_and_a_closed_status(self):
        entry=dict(id='p',name='P',official_url='https://example.org/p',cycle='每年',next_window='未知：未公布',
                   recheck_on='2027-01-05',eligibility_window=['35周岁以下'],open_questions=['在站可否申请'],status='closed')
        radar.watch_add(self.root,entry,today='2026-10-10')
        radar.watch_add(self.root,{'id':'p','recheck_on':'2027-02-01'},today='2026-11-01')
        saved=radar.read(radar.watch_path(self.root))['entries'][0]
        self.assertEqual((saved['status'],saved['eligibility_window'],saved['open_questions'],saved['recheck_on']),
                         ('closed',['35周岁以下'],['在站可否申请'],'2027-02-01'))
        with self.assertRaises(ValueError):radar.watch_add(self.root,{'id':'new-only-id'})

    def test_damaged_watchlist_is_reported_not_fatal(self):
        radar.watch_path(self.root).parent.mkdir(parents=True,exist_ok=True)
        for bad in ['{not json','{"entries":[1]}','[]']:
            radar.watch_path(self.root).write_text(bad)
            self.assertIn('problem',radar.watch_due(self.root,'2026-10-10')[0])
            self.assertEqual(radar.watch_missing(self.root,[]),[])
            with self.assertRaises(ValueError):
                radar.watch_add(self.root,dict(id='x',name='X',official_url='https://e.org',cycle='c',
                                               next_window='未知：',recheck_on='2027-01-01'))

    def test_mainland_employer_needs_quota_check(self):
        bare={k:v for k,v in copy.deepcopy(self.row).items() if k!='quota_note'}
        self.assertEqual(radar.ingest(self.root,self.out,[bare])['quota_unchecked'],['Example'])
        self.begin()
        with patch.object(radar,'rules',return_value=rules):
            radar.screen(self.root,self.out);self.export_fixture()
            result=radar.closeout(self.root,self.out)
        self.assertFalse(result['passed']);self.assertIn('quota_note',' '.join(result['problems']))
        intl=dict(bare,record_key='2',region='international',company='Abroad')
        self.assertNotIn('Abroad',radar.ingest(self.root,self.out,[intl]).get('quota_unchecked',[]))
        capped=dict(copy.deepcopy(self.row),record_key='3',quota_note='集团统招限投1个岗位（公告链接）')
        self.assertNotIn('quota_unchecked',radar.ingest(self.root,self.out,[capped]))
        with self.assertRaises(ValueError):radar.ingest(self.root,self.out,[dict(copy.deepcopy(self.row),quota_note=1)])
        later=dict(bare,record_key='3',checked_at='2026-09-18T13:00:00+00:00')
        radar.ingest(self.root,self.out,[later])
        kept=[r for r in radar.read(radar.support(self.out)/'records.json') if r['record_key']=='3'][0]
        self.assertEqual(kept['quota_note'],'集团统招限投1个岗位（公告链接）')
        self.assertEqual(radar.quota_unchecked([dict(bare,initial_filter={'outcome':'直接淘汰'})]),[])
    def test_different_branch_or_city_is_not_a_duplicate(self):
        known=[('workbench',{'机会编号':'JOB-000281','公司｜岗位':'星海示范银行（星信金融科技有限责任公司）｜科技类专项人才','城市':'上海'}),
               ('workbench',{'机会编号':'JOB-000299','公司｜岗位':'云岭示范银行（上海分行）｜信息科技岗','城市':'上海'}),
               ('workbench',{'机会编号':'JOB-000181','公司｜岗位':'远达信人工智能科技（北京）｜视觉算法工程师','城市':'北京'})]
        def hits(**row):
            found=radar.possible_duplicates(None,[dict(job_id='JOB-000900',**row)],known)
            return [m['job_id'] for d in found for m in d['matches']]
        self.assertEqual(hits(company='星海示范银行（上海市分行-浦东城区三行）',title='科技类专项人才',location='上海'),[])
        self.assertEqual(hits(company='云岭示范银行 / 深圳分行',title='信息科技岗',location='深圳'),[])
        self.assertEqual(hits(company='云岭示范银行',title='信息科技岗',location='深圳'),[])
        self.assertEqual(hits(company='云岭示范银行 / 上海分行',title='信息科技岗',location='上海市浦东新区'),['JOB-000299'])
        self.assertEqual(hits(company='远达信人工智能科技(北京)有限公司(云帆AI)',title='视觉算法工程师',location='北京/成都/西安'),['JOB-000181'])
        self.assertEqual(hits(company='星海示范银行 / 星信金融科技有限责任公司（星信金科）',title='科技类专项人才',location='未知'),['JOB-000281'])
        # Unparsed or differently written places never separate a real duplicate.
        for place in ('待具体岗位核实','全国32个省(区、市)及境外','Shanghai','上海市浦东新区（另含北京）'):
            self.assertEqual(hits(company='云岭示范银行（上海分行）',title='信息科技岗',location=place),['JOB-000299'],place)
        self.assertEqual(hits(company='云岭示范银行 / 上海市分行',title='信息科技岗',location='上海'),['JOB-000299'])
        self.assertEqual(hits(company='星海示范银行（星信金科）',title='科技类专项人才',location='上海'),['JOB-000281'])
        self.assertFalse(radar._same_unit('北京分行','南京分行'));self.assertFalse(radar._same_unit('北京分行','北京支行'))
        self.assertTrue(radar._same_unit('上海市分行','上海分行'));self.assertTrue(radar._same_unit('星信金科','星信金融科技'))
        known.append(('workbench',{'机会编号':'JOB-000282','公司｜岗位':'远航移动（苏州研发中心）｜算法岗','城市':'苏州'}))
        self.assertEqual(hits(company='远航移动 / 苏州研发中心',title='算法岗',location='江苏省-苏州市'),['JOB-000282'])
    def test_init_queues_for_another_runs_lease(self):
        radar.write(self.root/'system/state/active-run.json',{'stage':2,'run':'other','mode':'track','host':'codex',
            'started_at':'2026-10-08T10:00:00+00:00'})
        plan={'scope':'synthetic test','max_records':3,'instances':['mainland']}
        queued=radar.paths(self.root,'queued')
        with self.assertRaisesRegex(radar.Busy,'other \\(stage 2 track, host codex'):radar.begin(self.root,queued,plan)
        release=lambda _:(self.root/'system/state/active-run.json').unlink()
        with patch.object(radar.time,'sleep',side_effect=release):
            self.assertEqual(radar.wait(lambda:radar.begin(self.root,queued,plan),60)['run'],'queued')
        with patch.object(radar.time,'sleep'),patch.object(radar.time,'monotonic',side_effect=iter(range(0,10**6,1000))):
            other=radar.paths(self.root,'second');radar.write(radar.support(other)/'plan.json',plan)
            with self.assertRaisesRegex(radar.Busy,'Still busy'):radar.wait(lambda:radar.begin(self.root,other,plan),30)
    def test_cli_init_queues_outside_the_operation_lock(self):
        lock=self.root/'system/state/active-run.json'
        radar.write(lock,{'stage':2,'run':'other','mode':'track','started_at':'2026-10-08T10:00:00+00:00'})
        plan=self.root/'plan.input.json';radar.write(plan,{'scope':'synthetic','max_records':3,'instances':['mainland']})
        argv=['radar.py','--root',str(self.root),'--run','queued','init','--plan',str(plan),'--wait','60']
        def release(_):
            self.assertFalse((self.root/'system/state/stage1-operation.lock').exists())
            lock.unlink()
        with patch.object(sys,'argv',argv),patch.object(radar.time,'sleep',side_effect=release),patch('builtins.print'):
            radar.main()
        self.assertEqual(radar.read(lock)['run'],'queued')
    def test_dead_operation_owner_is_reported_not_waited_on(self):
        mutex=self.root/'system/state/stage1-operation.lock';mutex.mkdir(parents=True)
        (mutex/'pid').write_text('999999')
        with patch.object(radar.os,'kill',side_effect=ProcessLookupError):
            with self.assertRaisesRegex(ValueError,'no longer runs') as caught:
                with radar.operation(self.root,self.out):pass
        self.assertNotIsInstance(caught.exception,radar.Busy)
        with patch.object(radar.os,'kill'),self.assertRaises(radar.Busy):
            with radar.operation(self.root,self.out):pass
    def test_ids_skip_numbers_already_used_in_batches(self):
        radar.write(radar.paths(self.root,'older')/'_support/records.json',[{'job_id':'JOB-000500'}])
        radar.write(self.root/'system/state/skill-ids.json',{'last_id':3,'keys':{}})
        radar.ingest(self.root,self.out,[copy.deepcopy(self.row)])
        self.assertEqual(radar.read(radar.support(self.out)/'records.json')[0]['job_id'],'JOB-000501')
    def test_historical_batch_marker_blocks_mutation(self):
        radar.write(radar.support(self.out)/'batch.json',{'origin':'user_social_excerpt'})
        with self.assertRaises(ValueError):radar.paths(self.root,'pilot')


def xlsx(cells,extra_sheet=None):
    """A workbook saved the way Excel does (shared strings), to test reading what users edited."""
    import io,zipfile
    strings=sorted(set(cells.values())|set((extra_sheet or {}).values()))
    def sheet(grid):
        rows={}
        for ref,v in grid.items():rows.setdefault(int(''.join(ch for ch in ref if ch.isdigit())),[]).append(
            f'<c r="{ref}" t="s"><v>{strings.index(v)}</v></c>')
        return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                +''.join(f'<row r="{r}">{"".join(c)}</row>' for r,c in sorted(rows.items()))+'</sheetData></worksheet>')
    sheets=[('岗位初筛',cells)]+([('笔记',extra_sheet)] if extra_sheet is not None else [])
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            +''.join(f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>' for i,(n,_) in enumerate(sheets))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            +''.join(f'<Relationship Id="rId{i+1}" Target="/xl/worksheets/s{i+1}.xml"/>' for i in range(len(sheets)))+'</Relationships>')
        z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            +''.join(f'<si><r><t>{v[:1]}</t></r><r><t>{v[1:]}</t></r><rPh><t>x</t></rPh></si>' for v in strings)+'</sst>')
        for i,(_,grid) in enumerate(sheets):z.writestr(f'xl/worksheets/s{i+1}.xml',sheet(grid))
    return buf.getvalue()

class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.out=Path(self.tmp.name)/'batch'
        self.rows=[self.record(i) for i in (1,2)]
    def record(self,i,**kw):
        return dict(dict(job_id=f'JOB-00000{i}',region='mainland',company=f'机构{i}',title=f'岗位{i}',location='北京',
            availability='listed',kind='role',official_url=f'https://example.org/{i}',checked_at='2026-10-29',evidence='synthetic',
            initial_filter={'outcome':'进入第二轮','unknowns':['待核'],'rule_hits':[],'exception_review':[]}),**kw)
    def user_saved(self,order,manual,**extra):
        cells={f'{wb.column(i)}4':h for i,h in enumerate(wb.HEADERS)}
        cells.update(A1=wb.TITLE)
        for n,job in enumerate(order,5):
            cells.update({f'A{n}':job,f'C{n}':'旧机构',f'F{n}':manual[job][0],f'G{n}':manual[job][1]})
        cells.update(extra.pop('cells',{}))
        (self.out/wb.FILE).write_bytes(xlsx(cells,**extra))
    def test_refresh_keeps_user_order_and_manual_columns(self):
        self.out.mkdir();self.user_saved(['JOB-000002','JOB-000001'],{'JOB-000001':['关注','先看职责'],'JOB-000002':['暂不考虑','']})
        result=wb.export(self.out,[self.record(1,title='新岗位名'),self.record(2),self.record(3)])
        self.assertEqual((result['records'],result['manual_fields_preserved']),(3,2))
        grid=wb.read_sheets((self.out/wb.FILE).read_bytes())[wb.SHEET]
        self.assertEqual([grid[(r,0)] for r in (5,6,7)],['JOB-000002','JOB-000001','JOB-000003'])
        self.assertEqual((grid[(6,3)],grid[(6,2)],grid[(6,5)],grid[(6,6)]),('新岗位名','机构1','关注','先看职责'))
        self.assertEqual((grid[(5,5)],grid.get((5,6)),grid[(7,5)]),('暂不考虑',None,'未决定'))
        self.assertEqual(len(list((self.out/'_support/backups').glob('workbook-*.xlsx'))),1)
        self.assertEqual(wb.existing((self.out/wb.FILE).read_bytes())[0],['JOB-000002','JOB-000001','JOB-000003'])
    def test_manual_formula_survives_and_shared_formula_refuses(self):
        self.out.mkdir();self.user_saved(['JOB-000001'],{'JOB-000001':['关注','']})
        import io,zipfile
        def with_formula(f):
            src=zipfile.ZipFile(io.BytesIO((self.out/wb.FILE).read_bytes()));buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:
                for n in src.namelist():
                    data=src.read(n)
                    if n.endswith('s1.xml'):data=data.replace(b'</row><row r="5">',b'</row><row r="5"><c r="G5">'+f+b'</c>',1)
                    z.writestr(n,data)
            return buf.getvalue()
        base=(self.out/wb.FILE).read_bytes()
        (self.out/wb.FILE).write_bytes(with_formula(b'<f>SUM(1,2)</f><v>3</v>'))
        grid=wb.read_sheets((self.out/wb.FILE).read_bytes())[wb.SHEET]
        self.assertEqual(grid[(5,6)],'=SUM(1,2)')
        wb.export(self.out,[self.record(1)])
        self.assertIn(b'<c r="G5" s="5"><f>SUM(1,2)</f></c>',zipfile.ZipFile(self.out/wb.FILE).read('xl/worksheets/sheet1.xml'))
        self.assertEqual(wb.existing((self.out/wb.FILE).read_bytes())[1]['JOB-000001'],['关注','=SUM(1,2)'])
        (self.out/wb.FILE).write_bytes(base);(self.out/wb.FILE).write_bytes(with_formula(b'<f t="shared" si="0"/><v>3</v>'))
        with self.assertRaisesRegex(ValueError,'Shared formula'):wb.export(self.out,[self.record(1)])
    def test_refresh_refuses_to_drop_user_content(self):
        manual={'JOB-000001':['关注','x'],'JOB-000002':['未决定','']}
        cases=[(dict(),[self.record(1)],'Would remove'),
               (dict(cells={'A6':'JOB-000001'}),self.rows,'Duplicate'),
               (dict(cells={'B4':'地区'}),self.rows,'schema'),
               (dict(cells={'S5':'自己加的列'}),self.rows,'outside'),
               (dict(cells={'C7':'没有编号的行'}),self.rows,'no JOB ID'),
               (dict(extra_sheet={'A1':'笔记'}),self.rows,'other sheets')]
        self.out.mkdir()
        for extra,records,message in cases:
            self.user_saved(['JOB-000001','JOB-000002'],manual,**extra)
            before=(self.out/wb.FILE).read_bytes()
            with self.assertRaisesRegex(ValueError,message):wb.export(self.out,records)
            self.assertEqual((self.out/wb.FILE).read_bytes(),before)
    def test_new_workbook_is_well_formed(self):
        import zipfile
        from xml.etree import ElementTree as ET
        wb.export(self.out,[self.record(1,evidence='控制字符\x07<&>')])
        with zipfile.ZipFile(self.out/wb.FILE) as z:
            for name in z.namelist():ET.fromstring(z.read(name))
            sheet=z.read('xl/worksheets/sheet1.xml').decode()
        for part in ('state="frozen"','topLeftCell="E5"','<autoFilter ref="A4:R5"/>','sqref="F5:F5"','<c r="F5" s="5"','<c r="G5" s="5"'):
            self.assertIn(part,sheet)
        self.assertEqual(wb.read_sheets((self.out/wb.FILE).read_bytes())[wb.SHEET][(5,17)],'控制字符<&>')

if __name__=='__main__':unittest.main()
