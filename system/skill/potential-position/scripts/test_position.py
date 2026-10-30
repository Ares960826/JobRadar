import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import unittest.mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('position', HERE / 'position.py')
pos = importlib.util.module_from_spec(spec); spec.loader.exec_module(pos)

VIEW = ['公司｜岗位', '综合评级', '学历要求', '岗位直达链接', '招聘入口', '机会编号', '用户决定', '本人备注']
GOOD = {'record_id': 'rec1', '机会编号': 'JOB-000001', '公司｜岗位': '海川示范银行软件开发中心（上海）｜算法岗',
        '候选状态': ['有效候选'], '综合评级': ['A'], '评级说明': '职责与研究方向高度匹配', '学历要求': '硕士及以上',
        '岗位直达链接': '[算法岗](https://job.example.cn/post/1)', '招聘入口': '[入口](https://job.example.cn/)',
        '核查状态': ['待核线索'], '用户决定': ['待你选定'], '本人备注': '先看职责', '城市': '上海'}


def snapshot(*records, views=None):
    return {'tables': {'candidates': {
        'fields': [{'name': n} for n in {k for r in records for k in r} | set(VIEW)],
        'views': views or [{'id': 'vew1', 'name': '总览', 'visible_fields': list(VIEW), 'sort': [], 'filter': None}],
        'records': [copy.deepcopy(r) for r in records]}}}


def rules(snap):
    return {f['rule'] for f in pos.validate(snap)['findings'] if f['level'] == 'error'}


class ValidateTests(unittest.TestCase):
    def test_clean_record_passes(self):
        self.assertTrue(pos.validate(snapshot(GOOD))['passed'])

    def test_eliminated_record_cannot_keep_grade(self):
        bad = dict(GOOD, 候选状态=['硬条件淘汰'], 淘汰原因='')
        self.assertIn('eliminated_ungraded', rules(snapshot(bad)))

    def test_active_record_needs_grade_or_explicit_unrated(self):
        self.assertIn('grade_scale', rules(snapshot(dict(GOOD, 综合评级=[]))))
        self.assertNotIn('grade_scale', rules(snapshot(dict(GOOD, 综合评级=[], 评级依据状态='未评级：新增记录'))))

    def test_urgency_in_grade_reason_is_flagged(self):
        result = pos.validate(snapshot(dict(GOOD, 评级说明='截止临近，建议优先')))
        self.assertIn('grade_not_urgency', {f['rule'] for f in result['findings']})

    def test_direct_link_rules(self):
        for cell in ['[岗位](http://公司列表已核)', '[岗位](https://job.example.cn/)', 'https://job.example.cn/post/1', '']:
            self.assertIn('direct_link', rules(snapshot(dict(GOOD, 岗位直达链接=cell))), cell)
        self.assertNotIn('direct_link', rules(snapshot(dict(GOOD, 岗位直达链接='公司列表已核，原岗位名称未匹配'))))

    def test_company_needs_group_context(self):
        self.assertIn('company_context', rules(snapshot(dict(GOOD, **{'公司｜岗位': '软件开发中心｜算法岗'}))))
        self.assertIn('company_context', rules(snapshot(dict(GOOD, **{'公司｜岗位': '上海分行｜科技岗'}))))

    def test_education_required(self):
        self.assertIn('education_field', rules(snapshot(dict(GOOD, 学历要求=''))))

    def test_decision_ready_needs_closed_gaps_and_quota(self):
        ready = dict(GOOD, 核查状态=['可供本人决策'], 核查缺口='薪资口径待核')
        self.assertIn('decision_ready', rules(snapshot(ready)))
        ready = dict(GOOD, 核查状态=['可供本人决策'], 限额组='ICBC-2027', 限额处理='本人当届余额未知')
        self.assertIn('decision_ready', rules(snapshot(ready)))
        ready = dict(GOOD, 核查状态=['可供本人决策'], 限额处理='未核到：集团公告未写投递数量')
        self.assertIn('decision_ready', rules(snapshot(ready)))

    def test_retired_link_columns_and_duplicate_ids(self):
        views = [{'id': 'vew1', 'name': '总览', 'visible_fields': VIEW + ['岗位来源'], 'sort': [], 'filter': None}]
        self.assertIn('link_columns', rules(snapshot(GOOD, views=views)))
        self.assertIn('permanent_id', rules(snapshot(GOOD, dict(GOOD, record_id='rec2'))))


class SkillTextTests(unittest.TestCase):
    def test_invariants_identical_across_skills(self):
        import re
        files = [HERE.parents[1] / 'jobradar/SKILL.md', HERE.parents[1] / 'potential-position/SKILL.md', HERE.parents[3] / 'AGENTS.md']
        blocks = [re.findall(r'^\d+\. .*$', re.search(r'## Invariants.*?(?=\n## (?!Invariants))', f.read_text(), re.S).group(0), re.M)
                  for f in files]
        self.assertEqual(len(blocks[0]), 10)
        self.assertEqual(blocks[0], blocks[1]); self.assertEqual(blocks[0], blocks[2])


class GateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / pos.FIXTURE_MARKER).write_text('test workspace')
        remote = snapshot(GOOD)
        remote['tables']['application_tracking'] = {'fields': [{'name': f} for f in ['机会编号', '投递状态', '投递时间', '状态依据']],
                                                    'views': [], 'records': []}
        self.fixture = self.root / 'remote.json'
        self.fixture.write_text(json.dumps(remote, ensure_ascii=False))
        pos.write(self.root / 'system/config/feishu-workbench.json', {'fixture_remote': 'remote.json'})
        self.out = pos.run_dir(self.root, 'maint')

    def start(self, *args, **kw):
        pos.preflight(self.root, 'maint', *args, **kw)
        pos.snapshot(self.root, self.out, 'start')

    def apply(self, value):
        path = self.root / 'delta.json'; path.write_text(json.dumps(value, ensure_ascii=False))
        return pos.apply(self.root, self.out, path)

    def remote(self):
        return json.loads(self.fixture.read_text())

    def test_preflight_requires_scope_and_evidence(self):
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'maintain', 'test')
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'maintain', 'test', ['用户决定'])
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'maintain', 'test', ['机会编号'])
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'maintain', 'test', ['综合评级'])
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'track', 'test')
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'track', 'test', evidence='receipt:missing.png')
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'maintain', 'test', columns=['学历要求'])

    def test_review_needs_hard_condition_fixture(self):
        with self.assertRaises(ValueError): pos.preflight(self.root, 'maint', 'review', 'test')
        (self.root / 'rules.md').write_text('fixture rules')
        pos.write(self.root / 'system/config/preferences.json', {'fixture': True, 'hard_constraint_source': 'rules.md',
                  'hard_constraint_sha256': hashlib.sha256(b'fixture rules').hexdigest()})
        self.assertEqual(pos.preflight(self.root, 'maint', 'review', 'test')['mode'], 'review')

    def test_workbench_placeholders_refused_before_lark(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(ValueError, 'No system/config'): pos.workbench(root)
            template = HERE.parents[3] / 'templates/instance/system/config/feishu-workbench.json'
            pos.write(root / 'system/config/feishu-workbench.json', json.loads(template.read_text()))
            with patch.object(pos, 'lark_json') as lark, self.assertRaisesRegex(ValueError, 'Fill in'):
                pos.snapshot(root, root / 'outputs/run', 'start')
            lark.assert_not_called()
            pos.write(root / 'system/config/feishu-workbench.json', {'base_token': 'base', 'tables': {'candidates': 'tbl1'}})
            self.assertEqual(pos.workbench(root)['tables'], {'candidates': 'tbl1'})

    def test_fixture_needs_marker(self):
        (self.root / pos.FIXTURE_MARKER).unlink()
        with self.assertRaises(ValueError): pos.fixture_remote(self.root)
        with self.assertRaises(ValueError): pos.fixture_remote(self.root, str(self.fixture))

    def test_lock_and_user_approved_release(self):
        pos.preflight(self.root, 'maint', 'maintain', 'test', ['学历要求'])
        with self.assertRaises(ValueError): pos.preflight(self.root, 'other', 'maintain', 'test', ['城市'])
        with self.assertRaises(ValueError): pos.release(self.root, 'maint')
        self.assertTrue(pos.release(self.root, 'maint', user_approved=True)['released'])

    def test_preflight_lists_due_watchlist_entries(self):
        pos.radar.write(pos.radar.watch_path(self.root), {'entries': [
            {'id': 'due', 'name': 'Due fellowship', 'recheck_on': '2000-01-01', 'next_window': '估计：…', 'job_ids': []},
            {'id': 'later', 'name': 'Later', 'recheck_on': '2999-01-01', 'next_window': '估计：…'}]})
        intent = pos.preflight(self.root, 'maint', 'maintain', 'test', ['学历要求'])
        self.assertEqual([d['id'] for d in intent['watch_due']], ['due'])
        self.assertNotIn('watch_due', pos.read(self.out / '_support/intent.json'))

    def test_maintain_applies_named_field_and_column_then_closes(self):
        self.start('maintain', 'test', ['学历要求'], ['学历要求'], allow_views=True)
        order = ['公司｜岗位', '学历要求', '综合评级'] + VIEW[3:]
        receipt = self.apply({'update': [{'job_id': 'JOB-000001', 'fields': {'学历要求': '博士'}}],
                              'views': [{'view': 'vew1', 'visible_fields': order}]})
        self.assertEqual(receipt['status'], 'applied', receipt)
        result = pos.closeout(self.root, self.out)
        self.assertTrue(result['passed'], result)
        self.assertFalse(pos.lock_path(self.root).exists())

    def test_maintain_refuses_unnamed_manual_status_and_id_fields(self):
        self.start('maintain', 'test', ['学历要求'])
        for fields in [{'城市': '北京'}, {'本人备注': 'x'}, {'机会编号': 'JOB-000300'}]:
            self.assertEqual(self.apply({'update': [{'job_id': 'JOB-000001', 'fields': fields}]})['status'], 'refused', fields)
        self.assertEqual(self.remote()['tables']['candidates']['records'][0]['城市'], '上海')

    def test_review_cannot_renumber_or_create_unallocated_ids(self):
        (self.root / 'rules.md').write_text('r')
        pos.write(self.root / 'system/config/preferences.json', {'fixture': True, 'hard_constraint_source': 'rules.md',
                  'hard_constraint_sha256': hashlib.sha256(b'r').hexdigest()})
        self.start('review', 'test')
        self.assertEqual(self.apply({'update': [{'job_id': 'JOB-000001', 'fields': {'机会编号': 'JOB-000300'}}]})['status'], 'refused')
        new = dict(GOOD, 机会编号='JOB-000777'); new.pop('record_id'); new.pop('用户决定'); new.pop('本人备注')
        self.assertEqual(self.apply({'create': [{'fields': new}]})['status'], 'refused')
        pos.write(self.root / 'system/state/skill-ids.json', {'last_id': 100, 'keys': {}})
        job = pos.allocate(self.root, 'https://job.example.cn/post/9', '9')['job_id']
        self.assertEqual(job, 'JOB-000101')
        self.assertEqual(self.apply({'create': [{'fields': dict(new, 机会编号=job)}]})['status'], 'applied')

    def review_with_postdoc_mentors(self):
        (self.root / 'rules.md').write_text('r')
        pos.write(self.root / 'system/config/preferences.json', {'fixture': True, 'hard_constraint_source': 'rules.md',
                  'hard_constraint_sha256': hashlib.sha256(b'r').hexdigest()})
        remote = self.remote()
        remote['tables']['postdoc_mentors'] = {'fields': [dict(f) for f in remote['tables']['candidates']['fields']] +
                                        [{'name': '紧迫性（自动）', 'type': 'formula'}],
                                        'views': [], 'records': []}
        remote['tables']['candidates']['records'].append(dict(GOOD, record_id='rec2', 机会编号='JOB-000002'))
        self.fixture.write_text(json.dumps(remote, ensure_ascii=False))
        self.start('review', 'test')

    def test_move_copies_to_postdoc_mentors_then_removes_source(self):
        self.review_with_postdoc_mentors()
        receipt = self.apply({'move': [{'job_id': 'JOB-000002', 'from': 'candidates', 'to': 'postdoc_mentors'}]})
        self.assertEqual(receipt['status'], 'applied', receipt)
        remote = self.remote()['tables']
        self.assertEqual([pos.text(r['机会编号']) for r in remote['candidates']['records']], ['JOB-000001'])
        moved = remote['postdoc_mentors']['records']
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]['本人备注'], GOOD['本人备注'])  # manual values travel with the record
        self.assertTrue(pos.closeout(self.root, self.out)['passed'])

    def test_move_refusals_and_cross_table_ids(self):
        self.review_with_postdoc_mentors()
        for op in [{'job_id': 'JOB-000009', 'to': 'postdoc_mentors'}, {'job_id': 'JOB-000002', 'to': 'quotas'},
                   {'job_id': 'JOB-000002', 'to': 'postdoc_mentors', 'fields': {'城市': 'x'}}]:
            self.assertEqual(self.apply({'move': [op]})['status'], 'refused', op)
        remote = self.remote()
        remote['tables']['postdoc_mentors']['records'].append(dict(GOOD, record_id='rec9', 机会编号='JOB-000002'))
        self.assertIn('permanent_id', rules(remote))  # one ID across both candidate tables

    def test_deletion_outside_move_is_unexplained(self):
        before = snapshot(GOOD, dict(GOOD, record_id='rec2', 机会编号='JOB-000002'))
        after = snapshot(GOOD)
        self.assertFalse(pos.guard(before, after, {'mode': 'review'})['passed'])
        self.assertTrue(pos.guard(before, after, {'mode': 'review'}, moved={('candidates', 'rec2')})['passed'])

    def test_view_order_and_hidden_columns_are_protected(self):
        self.start('maintain', 'test', ['学历要求'])
        self.assertEqual(self.apply({'views': [{'view': 'vew1', 'visible_fields': VIEW}]})['status'], 'refused')
        pos.release(self.root, 'maint', user_approved=True)
        self.start('maintain', 'test', ['学历要求'], ['学历要求'], allow_views=True)
        swapped = [VIEW[1], VIEW[0]] + VIEW[2:]
        self.assertEqual(self.apply({'views': [{'view': 'vew1', 'visible_fields': swapped}]})['status'], 'refused')
        self.assertEqual(self.apply({'views': [{'view': 'vew1', 'visible_fields': VIEW[:-1]}]})['status'], 'refused')

    def test_start_snapshot_from_an_earlier_preflight_is_refused(self):
        self.start('maintain', 'test', ['学历要求'])
        pos.release(self.root, 'maint', user_approved=True)
        intent = pos.preflight(self.root, 'maint', 'track', 'test', evidence='user_report:本人报告已提交')
        self.assertIn('earlier preflight', intent['note'])
        with self.assertRaisesRegex(ValueError, 'predates'):
            self.apply({'update': [{'job_id': 'JOB-000001', 'fields': {'学历要求': '硕士'}}]})
        self.assertIn('predates', ' '.join(pos.closeout(self.root, self.out)['problems']))

    def test_second_agent_queues_for_the_lock(self):
        self.start('maintain', 'test', ['学历要求'])
        other = pos.radar.paths(self.root, 'other')
        with self.assertRaisesRegex(pos.radar.Busy, 'maint'):
            pos.preflight(self.root, 'other', 'maintain', 'test', ['学历要求'])
        self.assertFalse(other.exists())
        self.assertTrue(pos.lock_path(self.root).exists())
        with patch.object(pos.radar.time, 'sleep', side_effect=lambda _: pos.release(self.root, 'maint', user_approved=True)):
            intent = pos.preflight(self.root, 'other', 'maintain', 'test', ['学历要求'], wait=60)
        self.assertEqual(pos.read(pos.lock_path(self.root))['run'], 'other')
        self.assertEqual(intent['run'], 'other')
        with patch.object(pos.radar.time, 'sleep') as slept, self.assertRaisesRegex(ValueError, 'already holds'):
            pos.preflight(self.root, 'other', 'maintain', 'test', ['学历要求'], wait=60)
        slept.assert_not_called()

    def test_release_of_a_stage1_lease_writes_into_its_own_folder(self):
        lease = pos.radar.paths(self.root, 'fix', 'validation')
        pos.write(pos.lock_path(self.root), {'stage': 1, 'run': 'fix', 'out': str(lease.resolve())})
        pos.release(self.root, 'fix', user_approved=True)
        self.assertTrue((lease / '_support/release.json').is_file())
        self.assertFalse(pos.radar.paths(self.root, 'fix').exists())

    def test_allocate_never_reuses_an_id_seen_in_a_batch(self):
        pos.write(self.root / 'outputs/b1/_support/records.json', [{'job_id': 'JOB-000400', 'company': 'X', 'title': 'Y'}])
        pos.write(self.root / 'system/state/skill-ids.json', {'last_id': 10, 'keys': {}})
        self.assertEqual(pos.allocate(self.root, 'https://example.cn/job/1', 'k1')['job_id'], 'JOB-000401')

    def test_fake_link_refused_before_write(self):
        self.start('maintain', 'test', ['岗位直达链接'])
        receipt = self.apply({'update': [{'job_id': 'JOB-000001', 'fields': {'岗位直达链接': '[岗位](http://公司列表已核)'}}]})
        self.assertEqual(receipt['status'], 'refused')

    def test_guard_detects_manual_change_deletion_and_regrade(self):
        before = snapshot(GOOD, dict(GOOD, record_id='rec2', 机会编号='JOB-000002'))
        after = copy.deepcopy(before)
        after['tables']['candidates']['records'][0]['用户决定'] = ['已选定']
        after['tables']['candidates']['records'][0]['综合评级'] = ['S']
        after['tables']['candidates']['records'].pop()
        problems = ' '.join(pos.guard(before, after, {'mode': 'maintain', 'fields': ['学历要求']})['problems'])
        for word in ['protected field 用户决定', 'records deleted', 'grading field 综合评级']:
            self.assertIn(word, problems)

    def test_track_creates_tracking_row_but_not_grades(self):
        self.start('track', 'test', evidence='user_report:本人报告已提交')
        self.assertEqual(self.apply({'update': [{'job_id': 'JOB-000001', 'fields': {'综合评级': ['S']}}]})['status'], 'refused')
        receipt = self.apply({'create': [{'table': 'application_tracking', 'fields': {
            '机会编号': 'JOB-000001', '投递状态': ['已投递'], '投递时间': '2026-09-21 00:00', '状态依据': '本人报告'}}]})
        self.assertEqual(receipt['status'], 'applied', receipt)
        self.assertTrue(pos.closeout(self.root, self.out)['passed'])

    def test_closeout_catches_writes_that_bypass_apply(self):
        self.start('maintain', 'test', ['学历要求'])
        remote = self.remote(); remote['tables']['candidates']['records'][0]['学历要求'] = '本科'
        self.fixture.write_text(json.dumps(remote, ensure_ascii=False))
        result = pos.closeout(self.root, self.out)
        self.assertFalse(result['passed'])
        self.assertIn('unexplained', ' '.join(result['problems']))
        self.assertTrue(pos.lock_path(self.root).exists())

    def test_closeout_requires_start_snapshot_and_blocks_pending(self):
        pos.preflight(self.root, 'maint', 'maintain', 'test', ['学历要求'])
        self.assertFalse(pos.closeout(self.root, self.out)['passed'])
        pos.snapshot(self.root, self.out, 'start')
        pos.write(self.out / '_support/apply-1.json', {'step': 1, 'status': 'pending'})
        self.assertIn('pending', ' '.join(pos.closeout(self.root, self.out)['problems']))

    def test_lead_ids_are_valid_permanent_ids(self):
        self.assertTrue(pos.validate(snapshot(dict(GOOD, 机会编号='LEAD-20260920-001')))['passed'])
        self.assertIn('permanent_id', rules(snapshot(dict(GOOD, 机会编号='12'))))

    def test_accepted_unknown_allows_decision_ready(self):
        ready = dict(GOOD, 核查状态=['可供本人决策'], 核查缺口='本人已接受：薪资口径未公开', 限额组='ICBC', 限额处理='本人已接受：余额未知')
        self.assertTrue(pos.validate(snapshot(ready))['passed'])


class MaintenanceFindingTests(unittest.TestCase):
    def test_quota_group_without_rule_row_is_warned(self):
        snap = snapshot(dict(GOOD, 限额组='CCB-2027校园招聘'))
        snap['tables']['quotas'] = {'fields': [{'name': '限额组'}], 'views': [], 'records': [{'record_id': 'q1', '限额组': 'ICBC-2027校园招聘'}]}
        result = pos.validate(snap)
        self.assertTrue(result['passed'])
        self.assertIn('quota_table', {f['rule'] for f in result['findings']})
        snap['tables']['quotas']['records'].append({'record_id': 'q2', '限额组': 'CCB-2027校园招聘'})
        self.assertNotIn('quota_table', {f['rule'] for f in pos.validate(snap)['findings']})

    def test_workbench_duplicates_are_warned_not_blocking(self):
        dup = dict(GOOD, record_id='rec2', 机会编号='JOB-000002', **{'公司｜岗位': '海川示范银行软件开发中心（上海）｜算法岗（硕士及以上）'})
        result = pos.validate(snapshot(GOOD, dup))
        self.assertTrue(result['passed'])
        self.assertIn('possible_duplicate', {f['rule'] for f in result['findings']})

    def test_contract_label_elimination_needs_confirmation(self):
        out = dict(GOOD, 候选状态=['硬条件淘汰'], 综合评级=[], 淘汰原因='岗位性质：合同工')
        self.assertIn('contract_label', rules(snapshot(out)))
        sure = dict(out, 淘汰原因='岗位性质：合同工；公告写明劳务派遣（https://x.cn/a）')
        self.assertNotIn('contract_label', rules(snapshot(sure)))
        kept = pos.validate(snapshot(dict(GOOD, 核查缺口='岗位性质：合同工')))
        self.assertTrue(kept['passed'])
        self.assertIn('contract_label', {f['rule'] for f in kept['findings']})
        ok = pos.validate(snapshot(dict(GOOD, 核查缺口='待本人判断：岗位性质标注为“合同工”')))
        self.assertNotIn('contract_label', {f['rule'] for f in ok['findings']})

    def test_urgency_splits_first_three_days(self):
        p = pos.presentation
        self.assertEqual([p.urgency(d, '2026-10-07')[0] for d in
                          ('2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11',
                           '2026-10-14', '2026-10-15', '', 'bad')], [7, 0, 1, 2, 3, 4, 4, 5, 6, 6])
        self.assertEqual(p.urgency('2026-10-07', '2026-10-07')[1], '特别紧急：今天截止（0天）')

    def test_postdoc_urgency_keeps_twenty_day_preparation(self):
        p = pos.presentation
        today = '2026-10-11'
        self.assertEqual([p.urgency(d, today, 20)[0] for d in
                          ('2026-10-10', '2026-10-11', '2026-10-30', '2026-10-31', '2026-11-03', '2026-11-04',
                           '2026-11-07', '2026-11-08', '')], [7, 8, 8, 0, 3, 4, 4, 5, 6])
        self.assertEqual(p.urgency('2026-10-20', today, 20)[1], '准备期不足20天，按已过期处理')
        self.assertEqual(p.urgency('2026-10-20', today)[0], 5)  # ordinary jobs keep the 0-day rule
        self.assertEqual(p.urgency('2026-10-20', today, 30), (8, '准备期不足30天，按已过期处理'))
        self.assertEqual(p.urgency('2026-10-12', today, 1)[1], '特别紧急：距1天准备期线0天')

    def test_postdoc_prep_days_come_from_the_instance(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(pos.radar.postdoc_prep_days(root), 0)
            for value, ok in ((20, True), (-1, False), ('20', False), (True, False)):
                pos.write(root / 'system/config/preferences.json', {'postdoc_prep_days': value})
                if ok: self.assertEqual(pos.radar.postdoc_prep_days(root), value)
                else:
                    with self.assertRaises(ValueError): pos.radar.postdoc_prep_days(root)

    def test_draft_notes_cannot_be_applied(self):
        problems = pos.check_delta({'create': [], '_todo': {}}, {'mode': 'review'}, snapshot(GOOD))
        self.assertTrue(any('unknown delta keys' in p for p in problems))

    def test_draft_prefills_facts_but_not_grades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pos.write(root / 'outputs/run1/_support/records.json', [
                {'job_id': 'JOB-000300', 'company': '远航移动 / 青穹', 'title': '算法岗', 'location': '北京', 'kind': 'role',
                 'official_url': 'https://job.example.cn/post/9', 'source_tier': 1, 'deadline_note': '2026-10-24',
                 'quota_note': '集团统招限投1个岗位'},
                {'job_id': 'JOB-000301', 'company': '远航移动 / 青穹', 'title': '数据岗', 'kind': 'role', 'source_tier': 1}])
            draft = pos.draft(root, 'run1', ['JOB-000300'])
            fields = draft['create'][0]['fields']
            self.assertEqual(fields['公司｜岗位'], '远航移动（青穹）｜算法岗')
            self.assertEqual(fields['岗位直达链接'], '[算法岗](https://job.example.cn/post/9)')
            self.assertEqual(fields['限额处理'], '集团统招限投1个岗位')
            sibling = pos.draft(root, 'run1', ['JOB-000301'])['create'][0]['fields']
            self.assertEqual(sibling['限额处理'], '集团统招限投1个岗位')
            self.assertFalse({'综合评级', '评级说明', '学历要求'} & set(fields))
            self.assertIn('JOB-000300', draft['_todo'])
            with self.assertRaises(ValueError): pos.draft(root, 'run1', ['JOB-000999'])
            pos.write(root / 'outputs/run1/_support/records.json', [
                {'job_id': 'JOB-000300', 'company': 'X', 'title': 'Y', 'initial_filter': {'outcome': '直接淘汰'}}])
            with self.assertRaisesRegex(ValueError, 'Eliminated'): pos.draft(root, 'run1', ['JOB-000300'])
            pos.write(root / 'outputs/run1/_support/records.json', [{'机会编号': 'JOB-000300', '公司｜岗位': 'X｜Y'}])
            with self.assertRaises(ValueError): pos.draft(root, 'run1', ['JOB-000300'])

    def test_live_preflight_refuses_expired_feishu_login(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(pos.radar, 'feishu_auth', return_value={'ready': False, 'detail': 'expired'}):
            with self.assertRaisesRegex(ValueError, 'lark-cli auth login'):
                pos.preflight(Path(tmp), 'run1', 'maintain', 'test', ['城市'])
            self.assertFalse(pos.lock_path(Path(tmp)).exists())

    def test_fixture_preflight_never_checks_feishu(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(pos.radar, 'feishu_auth', side_effect=AssertionError('called')):
            (Path(tmp) / pos.FIXTURE_MARKER).write_text('test')
            self.assertEqual(pos.preflight(Path(tmp), 'run1', 'maintain', 'test', ['城市'])['feishu_auth'], None)

if __name__ == '__main__':
    unittest.main()
