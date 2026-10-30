"""PotentialPosition gates: declared mode, read-only snapshots, validated deltas, closeout.

The host Agent grades candidates and verifies pages. This core never calls an LLM,
never submits applications and never writes personal documents. Every Feishu record
or view write goes through `apply`, which checks the delta against the declared mode,
simulates it, writes, confirms the values stuck, and guards manual fields, IDs and column
order. `closeout` compares the final remote state with the start snapshot, so writes that
bypassed `apply` are reported as unexplained changes.
"""
import argparse
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
VERSION = '1.1.0'

MODES = {
    'review': 'hard gate, grade and verify named candidates; update/create candidate records',
    'maintain': 'edit only the fields/columns the user named; no search, re-grade or new records',
    'deliver': 'delta-write verified candidates by permanent ID; column changes need --allow-views --columns',
    'track': 'create/update application_tracking rows only from a user report or receipt',
}
MANUAL = {'用户决定', '本人备注', '我的备注', '父记录', '本人决定', '内推码'}
IMMUTABLE = {'机会编号'}
GRADE = {'综合评级', '评级说明', '评级依据状态', '候选状态', '硬条件', '淘汰原因'}
STATUS = {'投递状态', '投递时间', '下次安排', '安排事项', '状态依据'}
TRACK_CREATE = STATUS | {'机会编号', '公司｜岗位', '候选岗位', '博士后岗位', '招聘机构', '招聘入口', '岗位直达链接', '限额组'}
LINKS = {'岗位直达链接', '招聘入口'}
URGENCY_WORDS = ('紧迫', '临期', '即将截止', '剩余天数', '截止临近', 'deadline')
ACCEPTED = '本人已接受'
BARE_UNIT = re.compile(r'^(软件开发中心|数据中心|业务研发中心|研发中心|金融科技中心|总行|总部|[一-龥]{2,3}分行)(（[^）]*）)?$')
ID_FIELD = '机会编号'
# Candidate-like tables: same fields and rules. `postdoc_mentors` is the merged postdoc table: one
# row per mentor, unit post without a named PI, fellowship or entrance, all graded; its deadline
# formula keeps the user's preparation window (`postdoc_prep_days`). The retired `postdocs` table
# is no longer governed.
CANDIDATE_TABLES = ('candidates', 'postdoc_mentors')
ID_PATTERN = r'JOB-\d{6}|LEAD-\d{8}-\d{3}'  # LEAD = direction lead, never a confirmed job
AUTO_TYPES = {'formula', 'lookup', 'auto_number', 'created_time', 'modified_time', 'created_by', 'modified_by'}
FIXTURE_MARKER = '.jobradar-fixture'
GUARDED_PATHS = ('system/config/', 'system/skill/', FIXTURE_MARKER, 'AGENTS.md', '.agents/', '.claude/')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


radar = load('jobradar_radar', HERE.parents[1] / 'jobradar/scripts/radar.py')
presentation = load('pp_presentation', HERE / 'presentation.py')
read, write, now = radar.read, radar.write, radar.now


def text(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return '、'.join(text(v.get('text') or v.get('id') or v.get('name')) if isinstance(v, dict) else text(v) for v in value)
    return str(value).strip()


def run_dir(root, run):
    out = radar.paths(root, run)
    (out / '_support').mkdir(parents=True, exist_ok=True)
    return out


def is_fixture(root):
    """Offline test workspaces carry a marker; closeout fails if it appears in a real project."""
    return (root / FIXTURE_MARKER).is_file()


def hard_conditions(root):
    """The real project must match the verified hard-condition hash; test workspaces use their own."""
    if not is_fixture(root):
        return radar.rules(root)
    pref = read(root / 'system/config/preferences.json', {})
    source = root / pref.get('hard_constraint_source', '-')
    if not pref.get('fixture') or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != pref.get('hard_constraint_sha256'):
        raise ValueError('Hard-condition document changed or missing; review the changed document in Profile/ first')
    return pref


def lock_path(root):
    return root / 'system/state/active-run.json'


def git_changes(root):
    result = subprocess.run(['git', '-C', str(root), '-c', 'core.quotepath=off', 'status', '--porcelain', '-z'],
                            capture_output=True, text=True)
    if result.returncode:
        return ['<git unavailable>']
    return sorted({entry[3:] for entry in result.stdout.split('\0') if len(entry) > 3})


def guarded_state(root):
    """Content hashes of changed config/skill files, so closeout can spot edits made during the run."""
    state = {}
    for path in git_changes(root):
        if path.startswith(GUARDED_PATHS):
            full = root / path
            state[path] = hashlib.sha256(full.read_bytes()).hexdigest() if full.is_file() else 'dir-or-deleted'
    return state


# ---------------------------------------------------------------- intent / lock

def preflight(root, run, mode, host, fields=(), columns=(), allow_views=False, evidence='', check_rules=True, wait=0):
    if mode not in MODES:
        raise ValueError('Unknown mode; choose one of ' + ', '.join(MODES))
    fields, columns = set(fields), set(columns)
    if mode == 'maintain' and not (fields or columns):
        raise ValueError('maintain mode requires --fields and/or --columns naming exactly what the user asked to change')
    if mode == 'track':
        match = re.fullmatch(r'(user_report|receipt):(.+)', evidence or '')
        if not match:
            raise ValueError('track mode requires --evidence user_report:<quote> or receipt:<path>')
        if match.group(1) == 'receipt' and not (root / match.group(2)).exists() and not Path(match.group(2)).exists():
            raise ValueError('Receipt file not found: ' + match.group(2))
    if fields & (MANUAL | IMMUTABLE):
        raise ValueError('Manual fields and 机会编号 are never agent-writable: ' + ','.join(sorted(fields & (MANUAL | IMMUTABLE))))
    if mode == 'maintain' and fields & GRADE:
        raise ValueError('maintain mode cannot change grading fields; use review mode')
    if columns and not allow_views:
        raise ValueError('--columns needs --allow-views')
    if check_rules and mode in ('review', 'deliver'):
        hard_conditions(root)
    auth = None
    if not is_fixture(root):
        auth = radar.feishu_auth()
        if not auth['ready']:
            raise ValueError('Feishu user login is not ready (' + auth['detail'] + '); ask the user to run '
                             '`! lark-cli auth login`, then preflight again')
    out = radar.paths(root, run)  # created only once the lock is ours, so a refusal leaves no empty run
    lock = lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)

    def acquire():
        # Built only once the lock is free: the start time and file baseline must not predate the holder's edits.
        intent = {'run': out.name, 'mode': mode, 'host': host, 'fields': sorted(fields), 'columns': sorted(columns),
                  'allow_views': bool(allow_views), 'evidence': evidence, 'started_at': now(),
                  'allowed': MODES[mode], 'version': VERSION, 'guarded_baseline': guarded_state(root),
                  'feishu_auth': auth}
        try:
            held = read(lock) or {}
        except ValueError:  # another process is between creating and filling the lock
            held = {}
        if held.get('run') == out.name and held.get('stage') != 1:
            raise ValueError('This run already holds the lock (an earlier preflight); continue it, or '
                             'close it out, rather than waiting on yourself')
        with radar.operation(root, out):
            try:
                with open(lock, 'x') as handle:
                    handle.write(json.dumps(intent, ensure_ascii=False, indent=2) + '\n')
            except FileExistsError:
                raise radar.Busy('Another run is active: ' + radar.holder(root)) from None
            write(out / '_support/intent.json', intent)
        return intent
    intent = radar.wait(acquire, wait)
    start = read(out / '_support/snapshots/start.json')
    if start:
        intent = dict(intent, note='start.json is from an earlier preflight (' + str(start.get('taken_at')) +
                      '); take `snapshot --label start` now: apply and closeout refuse an older one')
    days = radar.postdoc_prep_days(root)
    if days:
        intent = dict(intent, postdoc_prep_days=days)
    due = radar.watch_due(root)
    if due:  # local reminders for recurring programmes; there is no scheduler
        intent = dict(intent, watch_due=due,
                      watch_note='Re-check date reached: tell the user and look for the new cycle\'s announcement.')
    return intent


def start_problem(out, intent):
    """The start snapshot must be taken after this preflight, or closeout diffs earlier sessions' writes."""
    start = read(out / '_support/snapshots/start.json')
    if not start:
        return 'no start snapshot (run `snapshot --label start` right after preflight)'
    try:
        stale = datetime.fromisoformat(start['taken_at']) < datetime.fromisoformat(intent['started_at'])
    except (KeyError, TypeError, ValueError):
        stale = True
    return ('start snapshot predates this preflight; take `snapshot --label start` before any apply '
            '(a new day or mode is best a new run)') if stale else ''


def active_intent(root, out):
    intent = read(out / '_support/intent.json')
    lock = read(lock_path(root))
    if not intent or not lock or lock.get('run') != out.name or lock.get('started_at') != intent.get('started_at'):
        raise ValueError('No active preflight for this run; run preflight first')
    return intent


def release(root, run, user_approved=False):
    if not user_approved:
        raise ValueError('Releasing a lock abandons a run; only on the user\'s explicit instruction (--user-approved)')
    lock = read(lock_path(root))
    if not lock:
        return {'released': False, 'reason': 'no active lock'}
    if lock.get('run') != run:
        raise ValueError('Active lock belongs to run ' + str(lock.get('run')))
    lock_path(root).unlink()
    out = Path(lock['out']) if lock.get('out') else radar.paths(root, run)  # a stage-1 lease names its own folder
    write(out / '_support/release.json', {'at': now(), 'released_intent': lock, 'reason': 'user approved'})
    return {'released': True, 'run': run}


# ---------------------------------------------------------------- snapshot

def lark(args, cwd=None):
    result = subprocess.run(['lark-cli', 'base', *args, '--as', 'user'], capture_output=True,
                            text=True, timeout=120, cwd=cwd)
    if result.returncode:
        raise RuntimeError('lark-cli failed: ' + ' '.join(args[:1]) + ': ' + result.stderr[:400])
    return result.stdout


def lark_json(args):
    data = json.loads(lark([*args, '--format', 'json']))
    if not data.get('ok', True):
        raise RuntimeError('lark-cli returned error: ' + json.dumps(data, ensure_ascii=False)[:400])
    return data.get('data', data)


def workbench(root):
    """The instance's Feishu workbench config; refuse the template placeholders before any lark call."""
    config = read(root / 'system/config/feishu-workbench.json')
    if not config:
        raise ValueError('No system/config/feishu-workbench.json in this instance')
    values = [config.get('base_token'), *(config.get('tables') or {}).values()]
    if not config.get('tables') or any(not isinstance(v, str) or not v or v.startswith('<') for v in values):
        raise ValueError('Fill in base_token and table IDs in system/config/feishu-workbench.json first')
    return config


def live_snapshot(config):
    base = config['base_token']
    tables = {}
    for name, table in config['tables'].items():
        common = ['--base-token', base, '--table-id', table]
        fields = lark_json(['+field-list', *common])
        listing = lark_json(['+view-list', *common])
        if listing.get('has_more') or len(listing.get('views', [])) < listing.get('total', 0):
            raise ValueError(f'Table {name}: view list truncated')
        views = []
        for view in listing.get('views', []):
            vc = [*common, '--view-id', view['id']]
            sortable = view.get('type') in ('grid', 'kanban', 'gallery', 'gantt')
            views.append({'id': view['id'], 'name': view['name'], 'type': view.get('type'),
                          'visible_fields': lark_json(['+view-get-visible-fields', *vc]).get('visible_fields', []),
                          'sort': lark_json(['+view-get-sort', *vc]).get('sort', []) if sortable else None,
                          'filter': lark_json(['+view-get-filter', *vc]).get('filter') if sortable else None})
        with tempfile.TemporaryDirectory() as tmp:
            lark(['+record-list', *common, '--limit', '2000', '--output', 'records.ndjson', '--overwrite'], cwd=tmp)
            manifest = json.loads((Path(tmp) / 'records.manifest.json').read_text())
            if manifest.get('has_more') is not False:
                raise ValueError(f'Table {name} was truncated; snapshot must be complete')
            records = [json.loads(line) for line in (Path(tmp) / 'records.ndjson').read_text().splitlines() if line.strip()]
        tables[name] = {'table_id': table, 'fields': fields.get('fields', fields), 'views': views,
                        'records': records, 'rev': manifest.get('rev')}
    return {'taken_at': now(), 'source': 'live', 'base_token': base, 'tables': tables}


def fixture_remote(root, explicit=None):
    configured = explicit or read(root / 'system/config/feishu-workbench.json', {}).get('fixture_remote')
    if configured and not is_fixture(root):
        raise ValueError('Fixture remotes are only honoured in a marked test workspace')
    return str(root / configured) if configured and not Path(configured).is_absolute() else configured


def snapshot(root, out, label, fixture=None):
    if not re.fullmatch(r'[\w-]{1,60}', label):
        raise ValueError('Invalid snapshot label')
    fixture = fixture_remote(root, fixture)
    if fixture:
        data = json.loads(Path(fixture).read_text())
        data.update(taken_at=now(), source='fixture:' + str(fixture))
    else:
        data = live_snapshot(workbench(root))
    path = out / '_support/snapshots' / (label + '.json')
    write(path, data)
    return path


# ---------------------------------------------------------------- validate

def field_names(table):
    return [f.get('name') if isinstance(f, dict) else f for f in table.get('fields', [])]


def auto_fields(table):
    return {f['name'] for f in table.get('fields', []) if isinstance(f, dict) and f.get('type') in AUTO_TYPES} | \
        {n for n in field_names(table) if n.endswith('（自动）')}


def link_problem(cell, direct=True):
    """Return a problem string for a bad link cell, else ''. Entrances may be homepages."""
    value = text(cell)
    if not value:
        return 'empty'
    links = re.findall(r'\[([^\]]*)\]\(([^)]*)\)', value)
    if not links:
        if re.match(r'https?://', value, re.I):
            return 'bare URL without verification label'
        return ''  # Concrete problem text is an allowed state.
    for _, url in links:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return 'link target is not HTTP(S): ' + url
        if re.search(r'[^\x00-\x7f]', parts.hostname or ''):
            return 'problem text was turned into a fake URL: ' + url
        if direct and not parts.fragment and parts.path in ('', '/') and not parts.query:
            return 'homepage or placeholder presented as a direct link: ' + url
    return ''


def validate(data):
    findings = []

    def flag(rule, job, detail, level='error'):
        findings.append({'rule': rule, 'job_id': job, 'detail': detail, 'level': level})

    if not data['tables'].get('candidates'):
        raise ValueError('Snapshot has no candidates table')
    seen = set()
    quota_groups = {text(q.get('限额组')) for q in data['tables'].get('quotas', {}).get('records', [])} - {''}
    active = []
    for table_name in CANDIDATE_TABLES:
        cand = data['tables'].get(table_name)
        if not cand:
            continue
        names = field_names(cand)
        if '学历要求' not in names:
            flag('education_field', '', table_name + ' table has no 学历要求 field')
        active += [r for r in cand['records'] if '淘汰' not in text(r.get('候选状态'))]
        for r in cand['records']:
            job = text(r.get(ID_FIELD))
            if not re.fullmatch(ID_PATTERN, job):
                flag('permanent_id', r.get('record_id', ''), 'missing or malformed 机会编号: ' + job)
            elif job in seen:
                flag('permanent_id', job, 'duplicate 机会编号')
            seen.add(job)
            status, grade = text(r.get('候选状态')), text(r.get('综合评级'))
            reason, basis = text(r.get('评级说明')), text(r.get('评级依据状态'))
            if '淘汰' in status:
                if radar.contract_unconfirmed(text(r.get('淘汰原因'))):
                    flag('contract_label', job, 'eliminated on a 合同工 label without wording that confirms no formal '
                         'status; keep it and write 待本人判断 in 核查缺口')
                if grade:
                    flag('eliminated_ungraded', job, 'hard-eliminated record still carries grade ' + grade)
                if not text(r.get('淘汰原因')):
                    flag('eliminated_ungraded', job, 'hard-eliminated record has no 淘汰原因')
                continue
            if grade and grade not in {'S', 'A', 'B', 'C', 'D'}:
                flag('grade_scale', job, 'grade outside S–D: ' + grade)
            if not grade and not re.search(r'未评级|待评级', reason + basis):
                flag('grade_scale', job, 'active candidate has no grade and is not marked 未评级')
            if grade and not reason:
                flag('grade_reason', job, 'grade without 评级说明')
            if any(w in reason for w in URGENCY_WORDS):
                flag('grade_not_urgency', job, 'grade reason cites time pressure: ' + reason[:60], 'warning')
            if '学历要求' in names and not text(r.get('学历要求')):
                flag('education_field', job, '学历要求 empty; write 待核实 if unknown')
            problem = link_problem(r.get('岗位直达链接'))
            if problem:
                flag('direct_link', job, problem)
            company = (text(r.get('公司｜岗位')) or text(r.get('导师｜课题组'))).split('｜')[0] or text(r.get('公司'))
            for unit in {company, text(r.get('招聘机构'))} - {''}:
                if BARE_UNIT.match(unit):
                    flag('company_context', job, 'name lacks group/company context: ' + unit)
            gaps = text(r.get('核查缺口'))
            if any(radar.CONTRACT_LABEL.search(text(r.get(f))) for f in ('核查缺口', '资格与偏好', '硬条件', '待核实')) \
                    and '待本人判断' not in gaps and ACCEPTED not in gaps:
                flag('contract_label', job, '合同工 label noted but 核查缺口 lacks 待本人判断 for the user', 'warning')
            group = text(r.get('限额组'))
            if 'quotas' in data['tables'] and group and group not in quota_groups:
                flag('quota_table', job, f'限额组 {group} has no rule row in the quotas table', 'warning')
            if '可供本人决策' in text(r.get('核查状态')):
                gaps = text(r.get('核查缺口'))
                if gaps and gaps not in ('无', '—', '-') and ACCEPTED not in gaps:
                    flag('decision_ready', job, 'marked 可供本人决策 with open 核查缺口 (prefix accepted items with 本人已接受：)')
                quota = text(r.get('限额处理'))
                if (text(r.get('限额组')) and re.search(r'未知|待核', quota) or '未核' in quota) and ACCEPTED not in quota:
                    flag('decision_ready', job, 'marked 可供本人决策 while quota group is unresolved')
                if '](' not in text(r.get('岗位直达链接')):
                    flag('decision_ready', job, 'marked 可供本人决策 without a verified direct link')
    for dup in radar.possible_duplicates(None, active, [('workbench', r) for r in active]):
        flag('possible_duplicate', dup['job_id'], 'same employer and role as ' +
             ', '.join(m['job_id'] for m in dup['matches']) + '; link instead of keeping two records', 'warning')
    for table_name, table in data['tables'].items():
        for view in table.get('views', []):
            visible = view.get('visible_fields', [])
            hidden = [f for f in visible if f not in presentation.visible_columns(visible)]
            if table_name in CANDIDATE_TABLES + ('group_comparison',) and hidden:
                flag('link_columns', view['name'], 'retired link/source columns visible: ' + ','.join(hidden))
            if table_name == 'group_comparison' and '学历要求' not in visible:
                flag('education_field', view['name'], 'group comparison view does not show 学历要求')
    errors = [f for f in findings if f['level'] == 'error']
    return {'at': now(), 'passed': not errors, 'errors': len(errors),
            'warnings': len(findings) - len(errors), 'findings': findings}


def regressions(old, new):
    """New errors keyed by (rule, job): editing one bad value into another is not a regression."""
    known = {(f['rule'], f['job_id']) for f in old['findings'] if f['level'] == 'error'}
    return [f for f in new['findings'] if f['level'] == 'error' and (f['rule'], f['job_id']) not in known]


# ---------------------------------------------------------------- guard

def index(table):
    return {r['record_id']: r for r in table.get('records', []) if r.get('record_id')}


def order_kept(before, after, movable):
    kept = [f for f in before if f in after and f not in movable]
    return [f for f in after if f in kept] == kept


def guard(before, after, intent, explained=None, moved=()):
    """Rules that must hold between two snapshots. `explained` = {(table, record_id, field)} written by apply;
    `moved` = {(table, record_id)} source rows a `move` op may delete (also ('*delete*') entries in explained)."""
    problems = []
    mode, fields, columns = intent['mode'], set(intent.get('fields', [])), set(intent.get('columns', []))
    for name, old in before['tables'].items():
        new = after['tables'].get(name)
        if new is None:
            problems.append(f'table {name} disappeared')
            continue
        auto = auto_fields(old) | auto_fields(new)
        lost = set(field_names(old)) - set(field_names(new))
        if lost:
            problems.append(f'{name}: fields deleted: ' + ','.join(sorted(lost)))
        a, b = index(old), index(new)
        allowed = {rid for t, rid in moved if t == name} | \
            {e[1] for e in (explained or ()) if e[0] == name and e[2] == '*delete*'}
        deleted = set(a) - set(b) - allowed
        if deleted:
            problems.append(f'{name}: records deleted: ' + ','.join(sorted(deleted)[:10]))
        created = set(b) - set(a)
        if created and (mode == 'maintain' or mode == 'track' and name != 'application_tracking'):
            problems.append(f'{name}: new records are not allowed in {mode} mode')
        for rid in set(a) & set(b):
            job = text(b[rid].get(ID_FIELD)) or rid
            for f in (set(a[rid]) | set(b[rid])) - auto - {'record_id'}:
                if text(a[rid].get(f)) == text(b[rid].get(f)):
                    continue
                if f in MANUAL or f in IMMUTABLE:
                    problems.append(f'{name} {job}: protected field {f} changed')
                elif mode in ('maintain', 'track') and f in GRADE:
                    problems.append(f'{name} {job}: grading field {f} changed in {mode} mode')
                elif mode != 'track' and f in STATUS and name == 'application_tracking':
                    problems.append(f'{name} {job}: application status {f} changed outside track mode')
                elif mode == 'maintain' and f not in fields:
                    problems.append(f'{name} {job}: field {f} changed but was not named by the user')
                elif mode == 'track' and name != 'application_tracking':
                    problems.append(f'{name} {job}: track mode changed a non-tracking table')
                elif explained is not None and (name, rid, f) not in explained:
                    problems.append(f'{name} {job}: {f} changed outside apply (unexplained write)')
        if explained is not None:
            for rid in created:
                if (name, rid, '*create*') not in explained:
                    problems.append(f'{name}: record {rid} created outside apply (unexplained write)')
        views = {v['id']: v for v in new.get('views', [])}
        for view in old.get('views', []):
            nv = views.get(view['id'])
            if not nv:
                problems.append(f'{name}: view {view["name"]} deleted')
                continue
            if nv.get('visible_fields') != view.get('visible_fields'):
                if not intent.get('allow_views'):
                    problems.append(f'{name}: view {view["name"]} columns changed without --allow-views')
                elif not order_kept(view['visible_fields'], nv['visible_fields'], columns):
                    problems.append(f'{name}: view {view["name"]} user column order changed beyond --columns')
                elif set(view['visible_fields']) - set(nv['visible_fields']) - columns:
                    problems.append(f'{name}: view {view["name"]} hid fields not named in --columns')
                elif explained is not None and (name, view['id'], '*view*') not in explained:
                    problems.append(f'{name}: view {view["name"]} columns changed outside apply')
            if nv.get('sort') != view.get('sort') or nv.get('filter') != view.get('filter'):
                problems.append(f'{name}: view {view["name"]} sort/filter changed (needs the user\'s explicit schema request)')
    return {'at': now(), 'passed': not problems, 'problems': problems}


# ---------------------------------------------------------------- apply

def check_delta(delta, intent, snap):
    mode, fields, columns = intent['mode'], set(intent.get('fields', [])), set(intent.get('columns', []))
    problems = []
    unknown = set(delta) - {'update', 'create', 'views', 'move'}
    if unknown:
        problems.append('unknown delta keys (finish and remove draft notes): ' + ','.join(sorted(unknown)))
    updates, creates, views = delta.get('update', []), delta.get('create', []), delta.get('views', [])
    if mode == 'maintain' and creates:
        problems.append('create is not allowed in maintain mode')
    if views and not intent.get('allow_views'):
        problems.append('view changes need preflight --allow-views')
    existing_ids = {text(r.get(ID_FIELD)) for t in CANDIDATE_TABLES for r in snap['tables'].get(t, {}).get('records', [])}
    for op in delta.get('move', []):
        src, dst, job = op.get('from', 'candidates'), op.get('to', ''), op.get('job_id', '')
        if mode not in ('review', 'deliver'):
            problems.append('move is only allowed in review or deliver mode')
        if src not in CANDIDATE_TABLES or dst not in CANDIDATE_TABLES or src == dst or dst not in snap['tables']:
            problems.append(f'move {job}: from/to must be two different candidate tables present in the workbench')
            continue
        rows = [r for r in snap['tables'][src]['records'] if text(r.get(ID_FIELD)) == job]
        if len(rows) != 1:
            problems.append(f'move {job}: not exactly one record in {src}')
            continue
        if any(text(r.get(ID_FIELD)) == job for r in snap['tables'][dst]['records']):
            problems.append(f'move {job}: already present in {dst}')
        links = [f['name'] for f in snap['tables'][src].get('fields', []) if isinstance(f, dict)
                 and f.get('type') == 'link' and text(rows[0].get(f['name']))]
        if links:
            problems.append(f'move {job}: link fields cannot cross tables, clear or re-link first: ' + ','.join(links))
        if set(op) - {'job_id', 'from', 'to'}:
            problems.append(f'move {job}: a move copies the record as is; put field changes in a separate update')
    registry = set(read(snap.get('_root', Path('.')) / 'system/state/skill-ids.json', {'keys': {}})['keys'].values()) \
        if snap.get('_root') else None
    for op in views:
        table = snap['tables'].get(op.get('table', 'candidates'), {})
        current = next((v for v in table.get('views', []) if v['id'] == op.get('view')), None)
        if current is None:
            problems.append('unknown view ' + str(op.get('view')))
            continue
        old, new = current['visible_fields'], op.get('visible_fields', [])
        if not order_kept(old, new, columns):
            problems.append(f'view {current["name"]}: user column order changed beyond --columns')
        if set(old) - set(new) - columns:
            problems.append(f'view {current["name"]}: hides fields not named in --columns')
    for kind, ops in (('update', updates), ('create', creates)):
        for op in ops:
            table = op.get('table', 'candidates')
            if table not in snap['tables']:
                problems.append('unknown table ' + str(table))
                continue
            names = set(op.get('fields', {}))
            auto = names & auto_fields(snap['tables'][table])
            if auto:
                problems.append(f'{table}: formula/auto fields cannot be written: ' + ','.join(sorted(auto)))
            protected = names & MANUAL - ({'父记录'} if kind == 'create' and mode in ('review', 'deliver') else set())
            if protected:
                problems.append('manual fields are not agent-writable: ' + ','.join(sorted(protected)))
            if kind == 'update' and names & IMMUTABLE:
                problems.append('机会编号 of an existing record never changes')
            if mode == 'maintain' and names - fields:
                problems.append('maintain delta touches unnamed fields: ' + ','.join(sorted(names - fields)))
            if mode == 'track':
                if table != 'application_tracking':
                    problems.append('track mode only writes application_tracking')
                allowed = TRACK_CREATE if kind == 'create' else STATUS
                if names - allowed:
                    problems.append('track delta writes non-status fields: ' + ','.join(sorted(names - allowed)))
            elif table == 'application_tracking' and names & STATUS:
                problems.append('application status can only change in track mode')
            if kind == 'create' and table in CANDIDATE_TABLES:
                job = text(op['fields'].get(ID_FIELD))
                if not re.fullmatch(ID_PATTERN, job) or job in existing_ids:
                    problems.append(f'new candidate needs a fresh JOB-000000 (or LEAD-YYYYMMDD-NNN) id, got {job or "none"}')
                elif job.startswith('JOB-') and registry is not None and job not in registry:
                    problems.append(f'{job} was not allocated; run `position.py allocate` first')
            for f in names & LINKS:
                problem = link_problem(op['fields'][f], direct=f == '岗位直达链接')
                if problem and problem != 'empty':
                    problems.append(f'{f}: {problem}')
    return problems


def resolve(op, snap):
    table = snap['tables'][op.get('table', 'candidates')]
    if op.get('record_id'):
        if op['record_id'] not in index(table):
            raise ValueError('Unknown record_id ' + op['record_id'])
        return op['record_id']
    matches = [r['record_id'] for r in table['records'] if text(r.get(ID_FIELD)) == op.get('job_id')]
    if len(matches) != 1:
        raise ValueError(f'Cannot resolve {op.get("job_id")} to exactly one record')
    return matches[0]


def move_fields(op, snap):
    """Writable copy of the source row for its target table: same names, no auto/link fields."""
    src, dst = snap['tables'][op.get('from', 'candidates')], snap['tables'][op['to']]
    row = index(src)[resolve(dict(op, table=op.get('from', 'candidates')), snap)]
    skip = auto_fields(src) | auto_fields(dst) | {f['name'] for t in (src, dst) for f in t.get('fields', [])
                                                  if isinstance(f, dict) and f.get('type') == 'link'}
    target = set(field_names(dst))
    return {k: v for k, v in row.items() if k != 'record_id' and k not in skip and k in target and text(v)}


def simulate(delta, snap):
    """Apply a delta to a copy of a snapshot (also the fixture writer)."""
    data = copy.deepcopy(snap)
    for op in delta.get('update', []):
        rid = resolve(op, snap)
        for r in data['tables'][op.get('table', 'candidates')]['records']:
            if r['record_id'] == rid:
                r.update(op['fields'])
    for i, op in enumerate(delta.get('create', [])):
        records = data['tables'][op.get('table', 'candidates')]['records']
        records.append(dict(op['fields'], record_id=f'recNew{len(records)}x{i}'))
    for op in delta.get('views', []):
        for v in data['tables'][op.get('table', 'candidates')]['views']:
            if v['id'] == op['view']:
                v['visible_fields'] = op['visible_fields']
    for i, op in enumerate(delta.get('move', [])):
        rid = resolve(dict(op, table=op.get('from', 'candidates')), snap)
        records = data['tables'][op['to']]['records']
        records.append(dict(move_fields(op, snap), record_id=f'recMove{len(records)}x{i}'))
        source = data['tables'][op.get('from', 'candidates')]
        source['records'] = [r for r in source['records'] if r['record_id'] != rid]
    return data


def moved_sources(delta, snap):
    return {(op.get('from', 'candidates'), resolve(dict(op, table=op.get('from', 'candidates')), snap))
            for op in delta.get('move', [])}


def write_live(root, delta, snap):
    config = workbench(root)
    base, ignored = config['base_token'], []
    grouped = {}
    for op in delta.get('update', []):
        grouped.setdefault(op.get('table', 'candidates'), {})[resolve(op, snap)] = op['fields']
    for name, records in grouped.items():
        items = list(records.items())
        for start in range(0, len(items), 200):
            out = lark(['+record-batch-update', '--base-token', base, '--table-id', config['tables'][name],
                        '--json', json.dumps({'update_records': dict(items[start:start + 200])}, ensure_ascii=False)])
            ignored += json.loads(out or '{}').get('data', {}).get('ignored_fields', []) if out.strip().startswith('{') else []
    for op in delta.get('create', []):
        lark(['+record-batch-create', '--base-token', base, '--table-id', config['tables'][op.get('table', 'candidates')],
              '--json', json.dumps({'create_records': [op['fields']]}, ensure_ascii=False)])
    moves = delta.get('move', [])
    if moves:
        by_target = {}
        for op in moves:
            by_target.setdefault(op['to'], []).append(move_fields(op, snap))
        for name, rows in by_target.items():
            for start in range(0, len(rows), 200):
                lark(['+record-batch-create', '--base-token', base, '--table-id', config['tables'][name],
                      '--json', json.dumps({'create_records': rows[start:start + 200]}, ensure_ascii=False)])
        check = live_snapshot(config)  # delete a source row only after its copy is confirmed in the target
        for op in moves:
            want = move_fields(op, snap)
            copies = [r for r in check['tables'][op['to']]['records'] if text(r.get(ID_FIELD)) == op['job_id']]
            types = {f['name']: f.get('type') for f in check['tables'][op['to']].get('fields', []) if isinstance(f, dict)}
            if len(copies) != 1 or any(types.get(k) not in ('datetime',) and text(copies[0].get(k)) != text(v)
                                       for k, v in want.items()):
                raise RuntimeError(f'move {op["job_id"]}: copy in {op["to"]} missing or different; source rows kept')
        for name in {op.get('from', 'candidates') for op in moves}:
            rids = [resolve(dict(op, table=name), snap) for op in moves if op.get('from', 'candidates') == name]
            for start in range(0, len(rids), 100):
                lark(['+record-delete', '--base-token', base, '--table-id', config['tables'][name],
                      '--json', json.dumps({'record_id_list': rids[start:start + 100]}), '--yes'])
    for op in delta.get('views', []):
        lark(['+view-set-visible-fields', '--base-token', base, '--table-id', config['tables'][op.get('table', 'candidates')],
              '--view-id', op['view'], '--json', json.dumps({'visible_fields': op['visible_fields']}, ensure_ascii=False)])
    return ignored


def not_applied(delta, snap, after):
    """Values that did not stick (ignored fields, stale ids, type coercion)."""
    missing = []
    for op in delta.get('update', []):
        table = op.get('table', 'candidates')
        types = {f['name']: f.get('type') for f in after['tables'][table].get('fields', []) if isinstance(f, dict)}
        row = index(after['tables'][table]).get(resolve(op, snap), {})
        for f, value in op['fields'].items():
            if types.get(f) in ('datetime', 'link'):
                continue  # Server normalises these; presence is enough.
            if text(row.get(f)) != text(value):
                missing.append(f'{table} {op.get("job_id") or op.get("record_id")}: {f}')
    for op in delta.get('move', []):
        job, src = op['job_id'], op.get('from', 'candidates')
        if sum(text(r.get(ID_FIELD)) == job for r in after['tables'][op['to']]['records']) != 1:
            missing.append(f'move {job}: not exactly one copy in {op["to"]}')
        if any(text(r.get(ID_FIELD)) == job for r in after['tables'][src]['records']):
            missing.append(f'move {job}: source row still in {src}')
    for op in delta.get('create', []):
        table = op.get('table', 'candidates')
        key = op['fields'].get(ID_FIELD)
        if key and not any(text(r.get(ID_FIELD)) == text(key) for r in after['tables'][table]['records']):
            missing.append(f'{table}: created record {key} not found')
    return missing


def apply(root, out, delta_path, fixture=None):
    intent = active_intent(root, out)
    if start_problem(out, intent):
        raise ValueError(start_problem(out, intent))
    fixture = fixture_remote(root, fixture)
    delta = json.loads(Path(delta_path).read_text())
    step = len(list((out / '_support').glob('apply-*.json'))) + 1
    receipt_path = out / f'_support/apply-{step}.json'
    before_path = snapshot(root, out, f'apply-{step}-before', fixture)
    before = read(before_path)
    receipt = {'at': now(), 'step': step, 'mode': intent['mode'], 'delta': delta, 'before': str(before_path),
               'remote_written': False}
    problems = check_delta(delta, intent, dict(before, _root=root))
    if not problems:
        try:
            simulated = simulate(delta, before)
        except ValueError as exc:
            problems.append(str(exc))
    if not problems:
        problems += guard(before, simulated, intent, moved=moved_sources(delta, before))['problems']
        problems += ['validation regression: ' + f['detail'] for f in regressions(validate(before), validate(simulated))]
    if problems:
        receipt.update(status='refused', problems=problems)
        write(receipt_path, receipt)
        return receipt
    receipt['status'] = 'pending'  # Visible to closeout if the write crashes midway.
    write(receipt_path, receipt)
    if fixture:
        Path(fixture).write_text(json.dumps({k: v for k, v in simulate(delta, before).items()
                                             if k not in ('taken_at', 'source')}, ensure_ascii=False, indent=2) + '\n')
        ignored = []
    else:
        ignored = write_live(root, delta, before)
    after_path = snapshot(root, out, f'apply-{step}-after', fixture)
    after = read(after_path)
    result = guard(before, after, intent, moved=moved_sources(delta, before))
    missing = not_applied(delta, before, after) + [f'ignored by server: {f}' for f in ignored]
    explained = [[op.get('table', 'candidates'), resolve(op, before), f] for op in delta.get('update', []) for f in op['fields']]
    explained += [[op.get('table', 'candidates'), rid, '*create*'] for op in delta.get('create', [])
                  for rid in set(index(after['tables'][op.get('table', 'candidates')])) - set(index(before['tables'][op.get('table', 'candidates')]))]
    explained += [[op.get('table', 'candidates'), op['view'], '*view*'] for op in delta.get('views', [])]
    for name, rid in moved_sources(delta, before):
        explained.append([name, rid, '*delete*'])
    for name in {op['to'] for op in delta.get('move', [])}:
        jobs = {op['job_id'] for op in delta['move'] if op['to'] == name}
        explained += [[name, rid, '*create*'] for rid, r in index(after['tables'][name]).items()
                      if rid not in index(before['tables'][name]) and text(r.get(ID_FIELD)) in jobs]
    receipt.update(status='applied' if result['passed'] and not missing else 'applied_with_problems',
                   remote_written=not fixture, after=str(after_path), guard=result, not_applied=missing,
                   explained=explained)
    write(receipt_path, receipt)
    return receipt


def allocate(root, url, key, company='', title='', run=''):
    """Reserve a permanent ID in the shared registry (same identity rule as jobradar ingest).

    With --company/--title it also lists likely duplicates from every batch and, when --run has
    a start snapshot, from the live workbench, so a role reached through a second URL is linked
    to its existing ID rather than created twice."""
    duplicates = []
    if company or title:
        extra = []
        start = radar.paths(root, run) / '_support/snapshots/start.json' if run else None
        if start and start.is_file():
            extra = [('workbench', r) for t in CANDIDATE_TABLES for r in read(start)['tables'].get(t, {}).get('records', [])]
        duplicates = radar.possible_duplicates(root, [{'company': company, 'title': title}], extra)
        duplicates = duplicates[0]['matches'] if duplicates else []
    with radar.locked(root) as state:
        registry = read(state / 'skill-ids.json', {'last_id': 0, 'keys': {}})
        registry['last_id'] = max(registry['last_id'], radar.seen_max(root))
        k = radar.canonical(url) + '|' + key
        if k not in registry['keys']:
            registry['last_id'] += 1
            registry['keys'][k] = f"JOB-{registry['last_id']:06d}"
            write(state / 'skill-ids.json', registry)
        result = {'job_id': registry['keys'][k], 'key': k}
    if duplicates:
        result.update(possible_duplicates=duplicates,
                      note='Looks like an existing record; update that ID instead of creating this one.')
    return result


KIND = {'role': '具体岗位', 'program': '招聘项目', 'organization': '公司/方向线索（待定位岗位）'}
EVIDENCE = {1: '企业官网/招聘站', 2: '第三方转载', 3: '第三方转载'}


def draft(root, source_run, ids, batch='', run=''):
    """Prefill candidate create ops from stage-1 records. Facts only: grade, 学历要求, gaps and quota stay for the agent."""
    data = read(radar.paths(root, source_run) / '_support/records.json', [])
    rows = {r['job_id']: r for r in (data if isinstance(data, list) else []) if isinstance(r, dict) and r.get('job_id') and r.get('company')}
    missing = [j for j in ids if j not in rows]
    if missing:
        raise ValueError('Not stage-1 records in that run\'s records.json: ' + ', '.join(missing))
    eliminated = [j for j in ids if (rows[j].get('initial_filter') or {}).get('outcome') == '直接淘汰']
    if eliminated:
        raise ValueError('Eliminated in stage 1; do not draft as active candidates: ' + ', '.join(eliminated))
    creates, todo = [], {}
    company_quota = {}
    for r in rows.values():  # one checked record covers its employer (radar.quota_unchecked)
        if (r.get('quota_note') or '').strip():
            company_quota.setdefault(r.get('company'), r['quota_note'])
    for job in ids:
        r = rows[job]
        url = r.get('official_url') or r.get('source_url')
        company, title = r['company'].replace(' / ', '（', 1) + ('）' if ' / ' in r['company'] else ''), r['title']
        fields = {ID_FIELD: job, '公司': company, '岗位': title, '公司｜岗位': f'{company}｜{title}',
                  '城市': '' if r.get('location') == '未知' else r.get('location', ''),
                  '记录类型': KIND.get(r.get('kind'), ''), '候选状态': ['有效候选'], '核查状态': ['待核线索'],
                  '证据等级': [EVIDENCE.get(r.get('source_tier', 1), '第三方转载')],
                  '批次': batch or source_run.replace('_', ' ', 1), '截止说明': r.get('deadline_note', ''),
                  '资格与偏好': r.get('eligibility_note', ''), '薪资与福利证据': r.get('salary_note', ''),
                  '限额处理': r.get('quota_note') or company_quota.get(r.get('company'), '')}
        if r.get('kind') == 'role' and r.get('official_url'):
            fields['岗位直达链接'] = f'[{title}]({url})'
        else:
            fields['定位说明'] = f'第一轮来源：{url}'
        creates.append({'fields': {k: v for k, v in fields.items() if v not in ('', None)}})
        todo[job] = ['综合评级 + 评级说明 (or 评级依据状态=未评级：…)', '学历要求 (待核实 if unknown)', '岗位直达链接 or problem text',
                     '招聘入口', '已核截止日 + 日期可信度 (date-only → 仅日期)', '核查缺口', '限额组 (+ quotas row) + 限额处理',
                     '硬条件']
    start = radar.paths(root, run) / '_support/snapshots/start.json' if run else None
    extra = [('workbench', r) for t in CANDIDATE_TABLES for r in read(start)['tables'].get(t, {}).get('records', [])] \
        if start and start.is_file() else []
    result = {'create': creates, '_todo': todo,
              '_note': 'Draft only: fill the _todo fields, drop every _ key, then `apply`.'}
    duplicates = radar.possible_duplicates(root, [r for j, r in rows.items() if j in ids], extra)
    if duplicates:
        result['_possible_duplicates'] = duplicates
    return result


# ---------------------------------------------------------------- closeout

def closeout(root, out):
    intent = active_intent(root, out)
    applies = [read(p) for p in sorted((out / '_support').glob('apply-*.json'))]
    problems = []
    start_path = out / '_support/snapshots/start.json'
    if start_problem(out, intent):
        problems.append(start_problem(out, intent))
    for a in applies:
        if a.get('status') in ('pending', 'applied_with_problems'):
            problems.append(f'apply step {a.get("step")} is {a.get("status")}: {a.get("guard", {}).get("problems")} {a.get("not_applied")}')
    final = None
    if start_path.exists():
        final = read(snapshot(root, out, 'final'))
        start = read(start_path)
        explained = {tuple(e) for a in applies if a.get('status') == 'applied' for e in a.get('explained', [])}
        problems += guard(start, final, intent, explained)['problems']
        problems += ['validation regression: ' + f'{f["job_id"]} {f["detail"]}' for f in regressions(validate(start), validate(final))]
    if is_fixture(root) and not read(root / 'system/config/feishu-workbench.json', {}).get('fixture_remote'):
        problems.append(FIXTURE_MARKER + ' present in a project with a real workbench')
    baseline = intent.get('guarded_baseline', {})
    edited = sorted(p for p, h in guarded_state(root).items() if baseline.get(p) != h)
    if edited:
        problems.append('config/skill files changed during the run (needs the user\'s explicit request): ' + ', '.join(edited))
    changed = git_changes(root)
    allowed = ('outputs/', 'system/validation/', 'system/state/')
    receipt = {'at': now(), 'run': out.name, 'stage': 2, 'mode': intent['mode'], 'host': intent['host'],
               'applies': len(applies), 'refused': sum(a.get('status') == 'refused' for a in applies),
               'remote_writes': sum(bool(a.get('remote_written')) for a in applies),
               'final_validate': {k: v for k, v in validate(final).items() if k != 'findings'} if final else None,
               'problems': problems,
               'other_project_changes': [p for p in changed if not p.startswith(allowed)],
               'passed': not problems,
               'note': 'Passing closeout proves the gates held, not that grading or page checks are correct.'}
    write(out / '_support/closeout-stage2.json', receipt)
    if receipt['passed']:
        lock_path(root).unlink(missing_ok=True)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--run', default='')
    sub = p.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('preflight')
    pre.add_argument('--mode', required=True, choices=sorted(MODES))
    pre.add_argument('--host', required=True, help='codex | opencode | claude-code | deepseek | other')
    pre.add_argument('--fields', default='', help='comma-separated record fields the user named')
    pre.add_argument('--columns', default='', help='comma-separated view columns the user named (move/hide)')
    pre.add_argument('--allow-views', action='store_true')
    pre.add_argument('--evidence', default='')
    pre.add_argument('--wait', type=int, default=90, help='seconds to queue for a lock held by another run')
    snap = sub.add_parser('snapshot'); snap.add_argument('--label', required=True); snap.add_argument('--fixture')
    val = sub.add_parser('validate'); val.add_argument('--snapshot', type=Path, required=True)
    app = sub.add_parser('apply'); app.add_argument('--delta', type=Path, required=True); app.add_argument('--fixture')
    alo = sub.add_parser('allocate'); alo.add_argument('--url', required=True); alo.add_argument('--key', required=True)
    alo.add_argument('--company', default=''); alo.add_argument('--title', default='')
    drf = sub.add_parser('draft', help='prefill create ops from a stage-1 run')
    drf.add_argument('--from-run', required=True); drf.add_argument('--ids', required=True)
    drf.add_argument('--out', type=Path, required=True); drf.add_argument('--batch', default='')
    rel = sub.add_parser('release'); rel.add_argument('--user-approved', action='store_true')
    sub.add_parser('closeout'); sub.add_parser('modes')
    a = p.parse_args()
    root = a.root.resolve()
    split = lambda s: [x.strip() for x in s.split(',') if x.strip()]
    if a.command == 'modes':
        result = MODES
    elif a.command == 'release':
        result = release(root, a.run, a.user_approved)
    elif a.command == 'allocate':
        result = allocate(root, a.url, a.key, a.company, a.title, a.run)
    elif a.command == 'draft':
        out_path = a.out if a.out.is_absolute() else run_dir(root, a.run) / '_support' / a.out
        result = draft(root, a.from_run, split(a.ids), a.batch, a.run)
        write(out_path, result)
        result = {'draft': str(out_path), 'records': len(result['create']),
                  'possible_duplicates': len(result.get('_possible_duplicates', []))}
    elif a.command == 'preflight':
        result = preflight(root, a.run, a.mode, a.host, split(a.fields), split(a.columns), a.allow_views, a.evidence,
                           wait=a.wait)
    else:
        out = run_dir(root, a.run)
        if a.command == 'snapshot':
            result = {'snapshot': str(snapshot(root, out, a.label, a.fixture))}
        elif a.command == 'validate':
            result = validate(read(a.snapshot))
            write(out / '_support/validate.json', result)
        elif a.command == 'apply':
            result = apply(root, out, a.delta, a.fixture)
        else:
            result = closeout(root, out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and (result.get('passed') is False or result.get('status') in ('refused', 'applied_with_problems')):
        sys.exit(1)


if __name__ == '__main__':
    main()
