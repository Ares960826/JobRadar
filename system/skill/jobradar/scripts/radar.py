"""JobRadar v0.5: bounded collection, evidence-first screening, editable workbook.

The host Agent discovers official pages and extracts non-API facts. This core
never calls an LLM, edits a batch it did not create, or submits applications.
"""
import argparse
from contextvars import ContextVar
from functools import partial, wraps
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen

VERSION = '0.5.0'


def now():
    return datetime.now(timezone.utc).isoformat()


class Busy(ValueError):
    """Another run holds the shared lock; callers may queue with `wait` instead of asking the user."""


def holder(root):
    """Who holds the shared lock, for queue messages: run, stage, mode, host and how long."""
    lock = read(root / 'system/state/active-run.json')
    if not lock:
        return 'nobody'
    since = lock.get('started_at', '')
    try:
        age = f', held {int((datetime.now(timezone.utc) - datetime.fromisoformat(since)).total_seconds() // 60)} min'
    except (TypeError, ValueError):
        age = ''
    stage = 'stage 1' if lock.get('stage') == 1 else 'stage 2'
    host = f", host {lock['host']}" if lock.get('host') else ''
    return f"{lock.get('run', '?')} ({stage} {lock.get('mode', '?')}{host}{age})"


def wait(attempt, seconds=0, poll=15, report=60):
    """Retry `attempt` while the lock is busy, up to `seconds`; never removes anyone's lock."""
    deadline, last = time.monotonic() + max(0, seconds), 0.0
    while True:
        try:
            return attempt()
        except Busy as exc:
            left = deadline - time.monotonic()
            if left <= 0:
                raise Busy(f'{exc}\nStill busy after waiting {int(seconds)} s. Wait longer (e.g. --wait 3000 in a '
                           'background command); ask the user only if the holder looks abandoned. Never remove the lock.') from None
            if time.monotonic() - last >= report:
                print(f'waiting for the lock: {exc}'.splitlines()[0], file=sys.stderr, flush=True)
                last = time.monotonic()
            time.sleep(min(poll, left))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.pending')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, path)


def canonical(url):
    u = urlsplit(url)
    if u.scheme not in {'https', 'http'} or not u.hostname or u.username or u.password:
        raise ValueError('Expected public HTTP(S) evidence URL')
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith('utm_')]
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path, urlencode(sorted(query)), ''))


def key(row):
    # Separate roles on a common announcement page require stable record_key.
    return canonical(row.get('official_url') or row['source_url']) + '|' + row['record_key']


def paths(root, run, area="outputs"):
    if not re.fullmatch(r'[\w][\w-]{0,95}', run):
        raise ValueError('Use a meaningful dated run name, no path separators')
    if area not in {'outputs', 'validation'}:
        raise ValueError('Invalid run area')
    base = root / ('outputs' if area == 'outputs' else 'system/validation')
    out = base / run
    if base.is_symlink() or out.is_symlink() or not out.resolve().is_relative_to(base.resolve()):
        raise ValueError('Output must be in the separate skill-radar tree')
    origin = read(out/'_support/batch.json',{}).get('origin')
    if origin and origin != 'jobradar_skill':
        raise ValueError(f'Batch {out.name} was not created by JobRadar ({origin}); it is read-only')
    return out


def support(out):
    return out / '_support'


@contextmanager
def locked(root):
    state = root / 'system/state'
    state.mkdir(parents=True, exist_ok=True)
    lock = state / 'skill-radar.lock'
    lock.mkdir()  # Fail rather than race another writer.
    try:
        yield state
    finally:
        lock.rmdir()


_ACTIVE_OPERATION = ContextVar('jobradar_operation', default=None)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def rule_status(root):
    try:
        ruleset = rules(root)
        return {'ready': True, 'version': ruleset.version, 'fingerprint': ruleset.fingerprint}
    except (ValueError, OSError, KeyError) as exc:
        return {'ready': False, 'error': str(exc)}


@contextmanager
def operation(root, out):
    """Serialize entire read/modify/write and workbook operations, not only ID allocation."""
    identity = (str(root.resolve()), str(out.resolve()))
    if _ACTIVE_OPERATION.get() == identity:
        yield
        return
    state = root / 'system/state'
    state.mkdir(parents=True, exist_ok=True)
    mutex = state / 'stage1-operation.lock'
    try:
        mutex.mkdir()
    except FileExistsError:
        try:
            owner = int((mutex / 'pid').read_text())
            os.kill(owner, 0)
        except ProcessLookupError:  # the owner died mid-operation; waiting cannot help
            raise ValueError(f'Stage-1 operation lock left by process {owner}, which no longer runs; '
                             'tell the user, do not remove it yourself') from None
        except (OSError, ValueError):
            pass  # pid not written yet, or not ours to signal: treat as running
        raise Busy('Stage-1 operation already running; do not remove its lock') from None
    (mutex / 'pid').write_text(str(os.getpid()))
    token = _ACTIVE_OPERATION.set(identity)
    try:
        active = read(state / 'active-run.json')
        if active and (active.get('stage') != 1 or active.get('out') != str(out.resolve())):
            raise Busy('Another stage/run owns the workspace: ' + holder(root))
        yield
    finally:
        _ACTIVE_OPERATION.reset(token)
        (mutex / 'pid').unlink(missing_ok=True)
        mutex.rmdir()


def serialized(function):
    @wraps(function)
    def guarded(root, out, *args, **kwargs):
        with operation(root, out):
            return function(root, out, *args, **kwargs)
    return guarded


def check_plan(root, out, plan):
    mode = plan.get('mode', 'search')
    diagnostic = mode in {'diagnostic', 'maintenance'}
    if mode not in {'search', 'diagnostic', 'maintenance'}:
        raise ValueError('Unsupported plan mode')
    limit = plan.get('max_records')
    if type(limit) is not int or (limit != 0 if diagnostic else not 1 <= limit <= 10000):
        raise ValueError('Diagnostics require max_records=0; search requires 1..10000')
    if not plan.get('scope') or (not diagnostic and not plan.get('instances')):
        raise ValueError('Plan requires scope and search instances')
    priority = plan.get('priority', [])
    if not isinstance(priority, list) or any(not isinstance(x, str) or not x.strip() for x in priority):
        raise ValueError('priority must be an ordered array of non-empty strings')
    if diagnostic and not out.resolve().is_relative_to((root/'system/validation').resolve()):
        raise ValueError('Diagnostics belong in validation, not outputs')
    return mode


def begin(root, out, plan):
    """Persistent run lease, shared with potential-position, released only by closeout."""
    mode = check_plan(root, out, plan)
    if read(support(out)/'plan.json') not in (None, plan):
        raise ValueError('Run already exists with a different plan; a user scope change goes through `amend`')
    state = root/'system/state'
    state.mkdir(parents=True, exist_ok=True)
    lease = state/'active-run.json'
    previous = read(lease)
    if previous:
        if previous.get('stage') != 1 or previous.get('out') != str(out.resolve()):
            raise Busy('Another run is active: ' + holder(root))
        return previous
    intent = {'stage': 1, 'run': out.name, 'out': str(out.resolve()), 'mode': mode,
              'started_at': now(), 'baseline': guarded_state(root)}
    try:
        with lease.open('x') as handle:
            json.dump(intent, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise Busy('Another run is active: ' + holder(root)) from None
    write(support(out)/'plan.json', plan)
    write(support(out)/'intent.json', intent)
    return intent


def amend(root, out, plan, reason):
    """A mid-run scope change from the user: same lease, dated amendment, budget re-checked."""
    require_lease(root, out)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('amend requires --reason quoting the user\'s instruction')
    current = read(support(out)/'plan.json') or {}
    if current.get('mode', 'search') != 'search' or plan.get('mode', 'search') != 'search':
        raise ValueError('amend is for search plans; diagnostic/maintenance scope and checks are fixed at init')
    check_plan(root, out, plan)
    records = read(support(out)/'records.json', [])
    if len(records) > plan['max_records']:
        raise ValueError(f'Run already holds {len(records)} records; max_records cannot go below that')
    if plan.get('instances') and any(r['region'] not in plan['instances'] for r in records):
        raise ValueError('Amended instances would exclude records already ingested')
    amendments = read(support(out)/'amendments.json', [])
    amendments.append({'at': now(), 'reason': reason.strip(), 'previous': current, 'plan': plan})
    write(support(out)/'amendments.json', amendments)
    write(support(out)/'plan.json', plan)
    return {'status': 'amended', 'amendments': len(amendments), 'records': len(records),
            'max_records': plan['max_records'], 'priority': plan.get('priority', [])}


def guarded_state(root):
    # Hash actual tracked content, including pre-existing edits; ignore other agents' new files.
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, text=True)
    if result.returncode:
        return {}
    return {p: digest(root/p) for p in result.stdout.split('\0') if p and
            not p.startswith(('system/state/', 'system/validation/'))}


def require_lease(root, out):
    intent = read(support(out)/'intent.json')
    active = read(root/'system/state/active-run.json')
    if not intent or not active or active.get('stage') != 1 or active.get('out') != str(out.resolve()) or active.get('started_at') != intent.get('started_at'):
        raise ValueError('Run init to acquire a stage-1 lease first')
    return intent


def seen_max(root):
    """Highest JOB number in any batch's records.json or the newest workbench snapshots.

    The registry is the counter, but it is an uncommitted local file; if it is reverted or lost,
    this keeps a new ID from reusing one that already exists in a batch or in Feishu."""
    out = root / 'outputs'
    files = list(out.glob('*/_support/records.json'))
    files += sorted(out.glob('*/_support/snapshots/*.json'), key=lambda p: p.stat().st_mtime)[-3:]
    best = 0
    for path in files:
        try:
            best = max([best, *map(int, re.findall(r'JOB-(\d{6})', path.read_text(errors='ignore')))])
        except OSError:
            continue
    return best


CONTRACT_LABEL = re.compile(r'合同工|合同制|合同用工')
# Wording that confirms an ordinary contract without formal status; a bare 合同工 label never does.
NO_FORMAL_STATUS = re.compile(r'劳务派遣|派遣制|派遣用工|外包|不纳入编制|不占编制|无编制|编外|非正式(职工|员工|编制)|项目制|劳务用工|劳务合同')


def contract_unconfirmed(text):
    """True when text relies on a contract label without wording that confirms no formal status."""
    return bool(CONTRACT_LABEL.search(text or '')) and not NO_FORMAL_STATUS.search(text or '')


def _flat(value):
    return '、'.join(map(str, value)) if isinstance(value, list) else str(value or '')


def _norm(value):
    """Loose company/title form: drop brackets, legal suffixes, cohort words and separators."""
    value = re.sub(r'[（(][^）)]*[）)]', '', _flat(value))
    value = re.sub(r'有限责任公司|股份有限公司|有限公司|^中国|20\d\d届?|校园招聘|校招|[\s/｜|·、，,_—–－-]', '', value)
    return value.lower()


def _close(a, b):
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return a == b or (short in long and len(short) >= 0.6 * len(long))


def _qualifiers(value):
    return frozenset(_norm(q) for q in re.findall(r'[（(]([^）)]*)[）)]', _flat(value))) - {''}


UNIT = re.compile(r'分行|支行|中心|公司|研究院|研究所|研发部|事业部|本部|分院')


CITIES = ('北京', '上海', '天津', '重庆', '深圳', '广州', '杭州', '南京', '苏州', '成都', '武汉', '西安', '合肥',
          '长沙', '郑州', '济南', '青岛', '厦门', '福州', '大连', '沈阳', '哈尔滨', '长春', '昆明', '贵阳', '南宁',
          '南昌', '太原', '石家庄', '兰州', '乌鲁木齐', '呼和浩特', '银川', '西宁', '拉萨', '海口', '珠海', '东莞',
          '佛山', '宁波', '无锡', '雄安', '香港', '澳门')
CITY_EN = {'beijing': '北京', 'shanghai': '上海', 'shenzhen': '深圳', 'guangzhou': '广州', 'hangzhou': '杭州',
           'hong kong': '香港', 'chengdu': '成都', 'wuhan': '武汉', 'nanjing': '南京', 'suzhou': '苏州'}


def _unit(company):
    """Sub-unit named after ' / ' or in a bracket that names an organization (分行, 中心, 研究院 …)."""
    raw = _flat(company)
    parts = [raw.split('/', 1)[1]] if '/' in raw else []
    parts += [q for q in re.findall(r'[（(]([^（）()]*)[）)]', raw.split('/', 1)[0]) if UNIT.search(q)]
    return _norm('、'.join(parts))


def _cities(r):
    """Known city names in 城市/location (brackets included, so 另含北京 widens the set). Anything
    unrecognized (待核实, 全国, a province) yields nothing, and nothing never separates two records."""
    raw = _flat(r.get('城市') or r.get('location'))
    low = raw.lower()
    return frozenset([c for c in CITIES if c in raw] + [c for en, c in CITY_EN.items() if en in low])


def _same_unit(a, b):
    """Close names, or most characters shared (上海市分行/上海分行, 星信金科/星信金融科技), unless they
    name different cities (北京分行/南京分行) or different branch levels (分行/支行)."""
    ca, cb = {c for c in CITIES if c in a}, {c for c in CITIES if c in b}
    if ca and cb and not ca & cb:
        return False
    if any((k in a) != (k in b) for k in ('分行', '支行')) and any(k in a for k in ('分行', '支行')) \
            and any(k in b for k in ('分行', '支行')):
        return False
    a, b = a.replace('市', '').replace('省', ''), b.replace('市', '').replace('省', '')
    return _close(a, b) or len(set(a) & set(b)) >= 0.75 * min(len(set(a)), len(set(b)))


def _identity(r):
    combo = _flat(r.get('公司｜岗位'))
    company = r.get('company') or r.get('公司') or combo.split('｜')[0]
    title = r.get('title') or r.get('岗位') or r.get('岗位名称') or (combo.split('｜', 1)[1] if '｜' in combo else '')
    names = frozenset({_norm(company), _norm(_flat(company).split('/', 1)[0])}) - {''}
    return (_flat(r.get('job_id') or r.get('机会编号')), names, (_norm(title), _qualifiers(title)),
            (_unit(company), _cities(r)))


def _same_company(a, b):
    """Full names or parent names (before ' / '; brackets are already dropped) are close."""
    return any(_close(x, y) for x in a for y in b)


def _same_place(a, b):
    """Different stated sub-units (上海分行 vs 深圳分行) or disjoint cities mean different roles."""
    if a[0] and b[0] and not _same_unit(a[0], b[0]):
        return False
    return not a[1] or not b[1] or bool(a[1] & b[1])


def _same_title(a, b):
    """Bracketed qualifiers (方向, 城市) separate roles only when both sides state different ones."""
    return _close(a[0], b[0]) and (not a[1] or not b[1] or a[1] == b[1])


def known_records(root, extra=()):
    """Records from every batch's records.json (stage-1 or workbench form) plus `extra` (e.g. a snapshot)."""
    seen = []
    for path in sorted((root/'outputs').glob('*/_support/records.json')) if root else ():
        try:
            rows = read(path, [])
        except (OSError, ValueError):
            continue
        rows = rows.get('records', []) if isinstance(rows, dict) else rows
        seen += [(path.parents[1].name, r) for r in rows if isinstance(r, dict)]
    return seen + [(label, r) for label, r in extra]


def possible_duplicates(root, rows, extra=()):
    """`root=None` compares only against `extra`. Same employer and role under another permanent ID (identity is URL+key, so a second portal URL slips through)."""
    known = [(run, *_identity(r), r) for run, r in known_records(root, extra)]
    found = []
    for row in rows:
        job, company, title, place = _identity(row)
        matches = {}
        for run, other, c, t, pl, r in known:
            if other and other != job and _same_company(company, c) and _same_title(title, t) and _same_place(place, pl):
                matches[other] = {'job_id': other, 'run': run, 'company': _flat(r.get('company') or r.get('公司') or r.get('公司｜岗位')),
                                  'title': _flat(r.get('title') or r.get('岗位') or r.get('岗位名称'))}
        if matches:
            found.append({'job_id': job, 'company': _flat(row.get('company') or row.get('公司')),
                          'title': _flat(row.get('title') or row.get('岗位')), 'matches': list(matches.values())})
    return found


def validate(row):
    required = ['company', 'title', 'location', 'region', 'kind',
                'record_key', 'source_name', 'evidence', 'checked_at', 'availability']
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
        raise ValueError('Missing record field: ' + ','.join(k for k in required if not row.get(k)))
    tier=row.setdefault('source_tier',1)
    if type(tier) is not int or tier not in {1,2,3}:raise ValueError('Invalid source tier')
    if tier==1 and not row.get('official_url'):raise ValueError('Tier 1 requires official evidence URL')
    canonical(row.get('official_url') or row.get('source_url',''))
    if row['region'] not in {'mainland', 'international', 'unassigned'}:
        raise ValueError('Unsupported region')
    if row['kind'] not in {'role', 'program', 'organization'}:
        raise ValueError('Unsupported record granularity')
    if row['availability'] not in {'listed', 'rolling', 'closed', 'unknown'}:
        raise ValueError('Unsupported availability')
    if datetime.fromisoformat(row['checked_at']).tzinfo is None:
        raise ValueError('checked_at requires a time zone')
    if not isinstance(row.get('facts', {}), dict):
        raise ValueError('facts must be an object')
    facts = row.get('facts', {})
    if facts.get('forbidden_identity') is True and contract_unconfirmed(str(facts.get('forbidden_identity_evidence', ''))):
        raise ValueError('forbidden_identity rests on a 合同工 label alone; quote wording that confirms no formal status '
                         '(派遣/外包/编外…) or leave it unknown for the user to judge in stage 2')
    if not isinstance(row.get('quota_note',''),str):raise ValueError('quota_note must be a string')
    if not isinstance(row.get('recurring_note',''),str):raise ValueError('recurring_note must be a string')
    if not isinstance(row.get('conflicts',[]),list):raise ValueError('conflicts must be an array')
    if not isinstance(row.get('observations',[]),list):raise ValueError('observations must be an array')
    row.setdefault('observations',[{'source_tier':tier,'url':row.get('official_url') or row['source_url'],
        'record_key':row['record_key'],'checked_at':row['checked_at'],'evidence':row['evidence']}])
    row['channel'] = 'paid_aggregation' if tier==2 else 'radar_search'
    row['collection_origin'] = 'jobradar_skill'
    row.setdefault('facts', {})
    row['facts']['region'] = row['region']
    return row


def quota_unchecked(rows):
    """Mainland employers with no record carrying the application-count check (record-schema.md)."""
    done = {r['company'] for r in rows if (r.get('quota_note') or '').strip()}
    live = [r for r in rows if (r.get('initial_filter') or {}).get('outcome') != '直接淘汰']
    return sorted({r['company'] for r in live if r.get('region') == 'mainland'} - done)


WATCH_FIELDS = {'id': str, 'name': str, 'official_url': str, 'cycle': str, 'next_window': str,
                'recheck_on': str, 'job_ids': list, 'eligibility_window': list, 'open_questions': list,
                'status': str, 'added': str, 'updated': str, 'source_run': str, 'note': str}


def watch_path(root):
    return root / 'system/state/watchlist.json'


def watch_entry(entry, today):
    """A recurring programme kept for its next cycle (record-schema.md, "Recurring programmes")."""
    if not isinstance(entry, dict):
        raise ValueError('Watchlist entry must be an object')
    unknown = set(entry) - set(WATCH_FIELDS)
    if unknown:
        raise ValueError('Unknown watchlist keys: ' + ','.join(sorted(unknown)))
    entry = dict({'job_ids': [], 'eligibility_window': [], 'open_questions': [], 'status': 'watching'}, **entry)
    for k in ('id', 'name', 'official_url', 'cycle', 'next_window', 'recheck_on'):
        if not isinstance(entry.get(k), str) or not entry[k].strip():
            raise ValueError('Watchlist entry needs ' + k)
    for k, kind in WATCH_FIELDS.items():
        if k in entry and not isinstance(entry[k], kind):
            raise ValueError(f'Watchlist {k} must be {kind.__name__}')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', entry['id']):
        raise ValueError('Watchlist id must be a lowercase slug')
    if not entry['official_url'].startswith(('http://', 'https://')):
        raise ValueError('Watchlist official_url must be the official page that shows the cycle')
    if not entry['next_window'].startswith(('官方：', '估计：', '未知：')):
        raise ValueError('next_window starts with 官方： (announced), 估计： (with its basis) or 未知：')
    if entry['status'] not in {'watching', 'closed'}:
        raise ValueError('Watchlist status is watching or closed (closed only on the user\'s word)')
    if any(not re.fullmatch(r'JOB-\d{6}|LEAD-\d{8}-\d{3}', j) for j in entry['job_ids']):
        raise ValueError('job_ids holds permanent IDs only')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['recheck_on']):
        raise ValueError('recheck_on is a date, YYYY-MM-DD')
    datetime.fromisoformat(entry['recheck_on'])
    entry.setdefault('added', today)
    entry['updated'] = today
    return entry


def watch_add(root, entries, today=None):
    today = today or datetime.now().astimezone().date().isoformat()
    raw = entries if isinstance(entries, list) else [entries]
    if any(not isinstance(e, dict) or not isinstance(e.get('id'), str) for e in raw):
        raise ValueError('Watchlist entries are objects with an id')
    try:
        with locked(root):
            data = read(watch_path(root), {'entries': []})
            current = {e['id']: e for e in watch_entries(root, strict=True)}
            saved = []
            for e in raw:
                old = current.get(e['id'])
                # An update restates only what changed: unnamed keys, a closed status and history stay.
                merged = {**old, **e} if old else dict(e)
                if old:
                    merged['job_ids'] = sorted(set(old.get('job_ids', [])) | set(e.get('job_ids', [])))
                    merged['added'] = old.get('added', today)
                current[e['id']] = watch_entry(merged, today)
                saved.append(e['id'])
            data = {'entries': sorted(current.values(), key=lambda e: e['id'])}
            write(watch_path(root), data)
    except FileExistsError:
        raise ValueError('Another JobRadar command is writing state (skill-radar.lock); retry in a moment') from None
    return {'watchlist': str(watch_path(root)), 'saved': saved, 'total': len(data['entries'])}


def watch_entries(root, strict=False):
    """The watchlist's entries; a damaged file is reported, never allowed to stop doctor/init/preflight."""
    try:
        data = read(watch_path(root), {'entries': []})
    except (ValueError, OSError) as exc:
        if strict:
            raise ValueError('watchlist.json is unreadable: ' + str(exc)) from None
        return None
    entries = data.get('entries') if isinstance(data, dict) else None
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        if strict:
            raise ValueError('watchlist.json must be {"entries": [objects]}')
        return None
    return entries


def watch_due(root, today=None):
    """Entries whose re-check date has come; every stage start lists them, there is no scheduler."""
    today = today or datetime.now().astimezone().date().isoformat()
    entries = watch_entries(root)
    if entries is None:
        return [{'id': '?', 'problem': 'watchlist.json is unreadable or malformed; fix it before relying on reminders'}]
    return [{'id': e.get('id'), 'name': e.get('name'), 'recheck_on': e.get('recheck_on'),
             'next_window': e.get('next_window'), 'job_ids': e.get('job_ids', [])}
            for e in entries if e.get('status', 'watching') == 'watching' and str(e.get('recheck_on') or '0') <= today]


def watch_missing(root, rows):
    """Closed records with official recurrence evidence that no watchlist entry holds yet."""
    entries = watch_entries(root) or []
    held = {j for e in entries if isinstance(e.get('job_ids'), list) for j in e['job_ids']}
    return sorted(r['job_id'] for r in rows if r.get('availability') == 'closed'
                  and (r.get('recurring_note') or '').strip() and r.get('job_id') not in held)


@serialized
def ingest(root, out, incoming):
    if not isinstance(incoming, list):
        raise ValueError('Input must contain a records array')
    rows = [validate(dict(r)) for r in incoming]  # Validate entire batch first.
    plan = read(support(out) / 'plan.json')
    if not plan:
        raise ValueError('Run init with an explicit bounded plan first')
    if plan.get('instances') and any(r['region'] not in plan['instances'] for r in rows):
        raise ValueError('Record region outside this run plan')
    with locked(root) as state:
        records = read(support(out) / 'records.json', [])
        existing = {key(r): r for r in records}
        combined = set(existing) | {key(r) for r in rows}
        if len(combined) > plan['max_records']:
            raise ValueError('Record budget exceeded; split or explicitly revise the plan')
        registry = read(state / 'skill-ids.json', {'last_id': 0, 'keys': {}})
        registry['last_id'] = max(registry['last_id'], seen_max(root))
        new, updated, fresh = 0, 0, []
        for row in rows:
            k = key(row)
            if k not in registry['keys']:
                registry['last_id'] += 1
                registry['keys'][k] = f"JOB-{registry['last_id']:06d}"
            old = existing.get(k)
            row['job_id'] = registry['keys'][k]
            row['first_seen'] = old['first_seen'] if old else row['checked_at']
            row['run_id'] = out.name
            if old:
                updated += 1
                if datetime.fromisoformat(old['checked_at']) > datetime.fromisoformat(row['checked_at']):
                    continue
                observations=old.get('observations',[])+row['observations']
                row['observations']=list({json.dumps(x,sort_keys=True,ensure_ascii=False):x for x in observations}.values())
                if old.get('conflicts') and 'conflicts' not in row:
                    row['conflicts']=old['conflicts']
                for note in ('quota_note', 'recurring_note'):
                    if old.get(note) and not (row.get(note) or '').strip():
                        row[note]=old[note]
            else:
                new += 1
                fresh.append(row)
            existing[k] = row
        # Reserving first is recoverable; rerun reuses IDs after an interrupted write.
        write(state / 'skill-ids.json', registry)
        write(support(out) / 'records.json', sorted(existing.values(), key=lambda r: r['job_id']))
        receipt = {'at': now(), 'input': len(rows), 'new': new, 'updated_or_duplicate': updated,
                   'total': len(existing),
                   'possible_duplicates': possible_duplicates(root, fresh)}
        if receipt['possible_duplicates']:
            log = read(support(out)/'possible-duplicates.json', [])
            write(support(out)/'possible-duplicates.json', log + [dict(d, at=receipt['at']) for d in receipt['possible_duplicates']])
            receipt['note'] = ('New IDs that look like an existing role under another ID; check them and link the '
                               'old ID in stage 2 instead of creating a second workbench record.')
        unchecked = quota_unchecked(list(existing.values()))
        if unchecked:
            receipt['quota_unchecked'] = unchecked
            receipt['quota_note'] = ('Check each employer/group application-count limit and put it in quota_note '
                                     '(or 未核到 plus the pages checked); closeout refuses without it.')
        missing = watch_missing(root, list(existing.values()))
        if missing:
            receipt['watch_missing'] = missing
            receipt['watch_note'] = ('Closed now but recurring: add each to the watchlist (`radar.py watch --add`) '
                                     'so the next cycle is not lost; closeout refuses without it.')
        write(support(out) / 'ingest-receipt.json', receipt)
    return receipt


def rules(root):
    """The user's hard rules, refused once the hard-condition document differs from the reviewed version."""
    pref = read(root / 'system/config/preferences.json')
    if not pref:
        raise ValueError('No system/config/preferences.json in this instance; start from the '
                         'templates/instance/ example in the JobRadar code directory')
    source = root / pref['hard_constraint_source']
    if hashlib.sha256(source.read_bytes()).hexdigest() != pref.get('hard_constraint_sha256'):
        raise ValueError('Hard-condition document changed; review the changed document in Profile/ and '
                         'update hard_rules before setting the new hard_constraint_sha256')
    return ruleset(pref.get('hard_rules'), pref['hard_constraint_sha256'])


def postdoc_prep_days(root):
    """Days the user needs to prepare a postdoc application (instance preferences; 0 = no window)."""
    days = read(root / 'system/config/preferences.json', {}).get('postdoc_prep_days', 0)
    if type(days) is not int or not 0 <= days <= 365:
        raise ValueError('postdoc_prep_days in system/config/preferences.json must be a whole number of days, 0–365')
    return days


def ruleset(hard, source_sha256):
    """Bind initial_filter to one rule set; the fingerprint tells screen results from stale ones."""
    import importlib.util
    path = Path(__file__).with_name('initial_filter.py')
    spec = importlib.util.spec_from_file_location('jobradar_rules', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.check(hard)
    fingerprint = {'source_sha256': source_sha256, 'implementation_sha256': digest(path),
                   'rules_sha256': hashlib.sha256(json.dumps(hard, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    return SimpleNamespace(version=hard['version'] + ' / ' + mod.VERSION, fingerprint=fingerprint,
                           evaluate=partial(mod.evaluate, rules=hard))


@serialized
def screen(root, out):
    ruleset = rules(root)
    rows = read(support(out) / 'records.json', [])
    for row in rows:
        facts = dict(row['facts'])
        facts['region'] = row['region']
        unknowns = list(facts.get('unknowns', []))
        for check, note in [('all_locations_excluded', '工作地点是否全部满足地域限制'),
                            ('forbidden_identity', '合同与用工身份'),
                            ('ineligible', '毕业时间及不可豁免资格'),
                            ('benefits_deficient', '福利与五险一金适用情况')]:
            if check not in facts:
                unknowns.append(note)
        if row['availability'] == 'closed':
            facts.update(closed=True, closed_evidence=row['evidence'])
        elif row['availability'] == 'unknown':
            unknowns.append('岗位当前是否开放')
        if row['kind'] != 'role':
            unknowns.append('需定位到具体岗位后才能形成完整资格判断')
        if row.get('source_tier',1)>1:
            unknowns.append('合集/平台元信息需与官网核对；未核实不得当作官方结论')
        if row.get('conflicts'):
            unknowns.append('存在未解决的来源冲突：'+str(row['conflicts']))
            # Conflicting observations cannot automatically eliminate a candidate.
            facts={'region':row['region'],'unknowns':unknowns}
        facts['unknowns'] = unknowns
        row['initial_filter'] = ruleset.evaluate(facts)
        row['screened_at'] = now()
    write(support(out) / 'records.json', rows)
    summary = {'version': VERSION, 'run_id': out.name, 'at': now(), 'records': len(rows),
               'instances': dict(Counter(r['region'] for r in rows)),
               'granularity': dict(Counter(r['kind'] for r in rows)),
               'outcomes': dict(Counter(r['initial_filter']['outcome'] for r in rows)),
               'unknown_records': sum(bool(r['initial_filter']['unknowns']) for r in rows),
               'note': 'Retained means insufficient rejection evidence; no second-stage ranking',
               'rules': ruleset.fingerprint,
               'records_sha256': digest(support(out)/'records.json')}
    write(support(out) / 'summary.json', summary)
    return summary


@serialized
def collect_ats(root, out, config):
    """One public Ashby or Greenhouse board response; no authentication or application API."""
    plan = read(support(out) / 'plan.json')
    if not plan:
        raise ValueError('Initialize a bounded plan first')
    board = config['board']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', config['name']):
        raise ValueError('Invalid source name')
    if type(config['limit']) is not int or not 1 <= config['limit'] <= plan['max_records']:
        raise ValueError('Source limit must fit run budget')
    if config['region'] not in plan['instances']:
        raise ValueError('Source region outside plan')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', board):
        raise ValueError('Invalid public board identifier')
    adapter = config['adapter']
    if adapter == 'ashby':
        url = f'https://api.ashbyhq.com/posting-api/job-board/{board}'
    elif adapter == 'greenhouse':
        url = f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true'
    else:
        raise ValueError('Unsupported public ATS adapter')
    canonical(config['official_evidence_url'])
    receipt = {'at': now(), 'source': config['name'], 'url': url, 'official_evidence_url': config['official_evidence_url'],
               'authentication': 'none', 'personalization': 'public board; no user account supplied',
               'title_terms': config.get('title_terms', []), 'limit': config['limit']}
    try:
        with urlopen(Request(url, headers={'User-Agent': 'JobRadar/0.3 public vacancy research'}), timeout=25) as response:
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise ValueError('Source exceeded 20 MB response budget')
        data = json.loads(raw)
        jobs = data['jobs']
        if not isinstance(jobs, list):
            raise ValueError('Missing jobs array')
        digest = hashlib.sha256(raw).hexdigest()
        snap = support(out) / 'evidence' / (config['name'] + '-' + digest[:16] + '.json')
        snap.parent.mkdir(exist_ok=True)
        if not snap.exists():
            snap.write_bytes(raw)
        selected = [j for j in jobs if j.get('isListed', True) and
                    (not config.get('title_terms') or any(t.casefold() in j['title'].casefold() for t in config['title_terms']))]
        selected.sort(key=lambda j: str(j.get('id', j.get('jobUrl', ''))))
        rows = []
        for j in selected[:config['limit']]:
            location = j.get('location', 'unknown')
            if isinstance(location, dict):
                location = location.get('name', 'unknown')
            if j.get('secondaryLocations'):
                location += '; ' + '; '.join(x['location'] for x in j['secondaryLocations'])
            text = html.unescape(re.sub('<[^>]+>', ' ', j.get('descriptionPlain', j.get('content', ''))))
            official = j.get('jobUrl', j.get('absolute_url'))
            rows.append(dict(company=config['company'], title=j['title'], location=location or 'unknown',
                region=config['region'], kind='role', record_key=str(j.get('id') or official), official_url=official,
                source_name=config['name'], checked_at=receipt['at'], availability='listed',
                evidence='公开雇主招聘系统在本次响应中列出该职位；详情保存在本地原始响应。',
                official_evidence_url=config['official_evidence_url'], evidence_file=str(snap.relative_to(out)),
                evidence_sha256=digest, description=text, salary_note='见职位详情；未做税后或年薪推算',
                eligibility_note='详见官网职位要求；尚未按个人履历精筛',
                deadline_note='本次接口未提供明确截止', facts={'unknowns':['签证/工作许可及可入职时间']},
                discovery_context=receipt))
        receipt.update(status='sample_passed' if rows else 'empty', returned=len(jobs),
                       term_matches=len(selected), selected=len(rows), truncated=len(selected)>len(rows),
                       response_hash=digest, response_scope='one board response; not platform-wide coverage')
        if rows:
            receipt['ingest'] = ingest(root, out, rows)
    except Exception as exc:
        receipt.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    write(support(out) / 'sources' / (config['name'] + '.json'), receipt)
    return receipt


HOST_ENV_MARKERS = (
    ('CODEX_THREAD_ID', 'codex'),
    ('CODEX_SANDBOX', 'codex'),
    ('CLAUDECODE', 'claude-code'),
    ('OPENCODE', 'opencode'),
    ('GEMINI_CLI', 'gemini-cli'),
)


def jobradar_host():
    """Host identity for shared-resource provenance; JOBRADAR_HOST overrides."""
    explicit = re.sub(r'[^A-Za-z0-9._-]', '-', (os.environ.get('JOBRADAR_HOST') or '').strip())[:32]
    if explicit:
        return explicit
    for marker, name in HOST_ENV_MARKERS:
        if os.environ.get(marker):
            return name
    return 'unknown'


def workbook():
    """The stdlib xlsx module next to this script (radar.py is also loaded by path from stage 2)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('jobradar_workbook', Path(__file__).with_name('workbook.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def feishu_auth(timeout=20):
    """lark-cli user identity, so an expired login shows up before stage 2, not in the middle of it."""
    try:
        result = subprocess.run(['lark-cli', 'auth', 'status'], capture_output=True, text=True, timeout=timeout)
        payload = json.loads(result.stdout or '{}')
        user = payload.get('identities', {}).get('user', {}) if isinstance(payload, dict) else {}
        user = user if isinstance(user, dict) else {}
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError) as exc:
        return {'ready': False, 'detail': 'lark-cli auth status failed: ' + str(exc)[:200]}
    # `needs_refresh` means the access token lapsed but lark-cli refreshes it on the next call.
    status = {'ready': user.get('status') in ('ready', 'needs_refresh') and user.get('available') is True,
              'detail': user.get('message') or 'no user identity', 'refresh_expires_at': user.get('refreshExpiresAt')}
    try:
        expires = datetime.fromisoformat(status['refresh_expires_at'].replace('Z', '+00:00'))
        left = (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)) - datetime.now(timezone.utc)
        if status['ready'] and left.total_seconds() < 86400:
            status['warning'] = 'refresh token expires within 24 h; ask the user to run `lark-cli auth login`'
    except (AttributeError, TypeError, ValueError):
        pass
    return status


def git_changes(root):
    result = subprocess.run(['git', '-C', str(root), '-c', 'core.quotepath=off', 'status', '--porcelain', '-z'],
                            capture_output=True, text=True)
    return sorted({e[3:] for e in result.stdout.split('\0') if len(e) > 3}) if result.returncode == 0 else ['<git unavailable>']


@serialized
def export_workbook(root, out):
    current = rules(root).fingerprint
    rows = read(support(out)/'records.json', [])
    summary = read(support(out)/'summary.json', {})
    if not rows or any('initial_filter' not in row for row in rows) or summary.get('records_sha256') != digest(support(out)/'records.json') or summary.get('rules') != current:
        raise ValueError('Run screen on current records/rules before export')
    result = workbook().export(out, rows)
    receipt = {'at': now(), 'host': jobradar_host(), 'workbook_runtime': result['engine'],
               'manual_fields_preserved': result['manual_fields_preserved'],
               'records_sha256': digest(support(out)/'records.json'),
               'workbook_sha256': digest(out/'岗位初筛工作表.xlsx')}
    if not receipt['workbook_sha256']:
        raise ValueError('Export did not produce a workbook')
    write(support(out)/'export-receipt.json', receipt)
    return receipt


@serialized
def closeout(root, out):
    """Validate delivery evidence; diagnostic success is never a search-readiness claim."""
    rows = read(support(out)/'records.json', [])
    plan = read(support(out)/'plan.json', {})
    problems = []
    try:
        intent = require_lease(root, out)
    except ValueError as exc:
        intent = {}
        problems.append(str(exc))
    if not plan:
        problems.append('no bounded plan')
    status = rule_status(root)
    if not status['ready']:
        problems.append('rules not ready: ' + status['error'])
    mode = plan.get('mode', 'search')
    summary = read(support(out)/'summary.json', {})
    if mode in {'diagnostic', 'maintenance'}:
        checks = read(support(out)/'checks.json', {})
        required = plan.get('required_checks', [])
        if not required or any(checks.get(k, {}).get('passed') is not True for k in required):
            problems.append('required diagnostic checks missing or failed')
        for name in required:
            check = checks.get(name, {})
            evidence = (out/check.get('evidence', '')).resolve()
            if not evidence.is_relative_to(out.resolve()) or not evidence.is_file() or digest(evidence) != check.get('sha256'):
                problems.append('diagnostic evidence missing/changed: ' + name)
        if rows:
            problems.append('diagnostic run must not contain candidate records')
    else:
        if not rows:
            problems.append('empty run is not a completed search delivery')
        if any('initial_filter' not in r for r in rows):
            problems.append('unscreened records present')
        if not status['ready'] or summary.get('rules') != status['fingerprint'] or summary.get('records_sha256') != digest(support(out)/'records.json'):
            problems.append('screen summary missing or stale')
        # The local workbook is optional (Feishu is the main workbench); once made it must stay current.
        export = read(support(out)/'export-receipt.json')
        workbook_hash = digest(out/'岗位初筛工作表.xlsx')
        if (export or workbook_hash) and (not export or export.get('workbook_sha256') != workbook_hash
                                          or export.get('records_sha256') != digest(support(out)/'records.json')):
            problems.append('local workbook missing, modified, or stale; run export again')
        unchecked = quota_unchecked(rows)
        if unchecked:
            problems.append('application-count limit not checked (quota_note) for: ' + '、'.join(unchecked))
        missing = watch_missing(root, rows)
        if missing:
            problems.append('recurring programmes not on the watchlist: ' + '、'.join(missing))
        if any(any(k in r for k in ('grade', 'rating', '综合评级')) for r in rows):
            problems.append('stage-1 records carry second-stage grades')
    baseline = intent.get('baseline', {})
    current = guarded_state(root)
    changed = sorted(p for p in set(baseline)|set(current) if baseline.get(p) != current.get(p))
    allowed = plan.get('allowed_changes', []) if mode == 'maintenance' else []
    outside = [p for p in changed if p not in allowed]
    if outside:
        problems.append('tracked files changed outside declared scope')
    receipt = {'at': now(), 'run': out.name, 'stage': 1, 'mode': mode, 'records': len(rows),
               'rules': status, 'summary': summary, 'workbook': (out/'岗位初筛工作表.xlsx').exists(),
               'project_changes_outside_run': outside, 'problems': problems, 'passed': not problems,
               'note': 'Diagnostic pass proves listed checks only; not host/search coverage. Search pass is not source completeness.'}
    write(support(out)/'closeout-stage1.json', receipt)
    # A failed run also closes its lease; fixing and resuming requires explicit init, never stale-lock deletion.
    if intent:
        (root/'system/state/active-run.json').unlink()
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--run', default='')
    p.add_argument('--area', choices=['outputs', 'validation'], default='outputs')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    wat = sub.add_parser('watch', help='list the recurring-programme watchlist, or add/update entries')
    wat.add_argument('--add', type=Path, help='JSON file with one entry or a list of entries')
    init = sub.add_parser('init'); init.add_argument('--plan', type=Path, required=True)
    init.add_argument('--wait', type=int, default=90, help='seconds to queue for a lock held by another run')
    amd = sub.add_parser('amend'); amd.add_argument('--plan', type=Path, required=True)
    amd.add_argument('--reason', required=True, help="the user's instruction, quoted")
    inc = sub.add_parser('ingest'); inc.add_argument('--input', type=Path, required=True)
    col = sub.add_parser('collect-ats'); col.add_argument('--config', type=Path, required=True)
    fei = sub.add_parser('collect-feishu'); fei.add_argument('--config',type=Path,required=True)
    fei.add_argument('--keyword',default='');fei.add_argument('--fresh',action='store_true')
    sub.add_parser('screen'); sub.add_parser('status'); sub.add_parser('export'); sub.add_parser('closeout')
    a = p.parse_args(); root = a.root.resolve()
    if a.command == 'doctor':
        status = rule_status(root)
        try:
            prep = postdoc_prep_days(root)
        except ValueError as exc:
            prep = {'error': str(exc)}
        print(json.dumps({'version': VERSION, 'project': str(root), 'rules': status,
              'python': sys.version.split()[0], 'host_search': 'not tested by doctor',
              'scheduler': 'not configured',
              'host': jobradar_host(), 'workbook_runtime': workbook().VERSION,
              'postdoc_prep_days': prep,
              'feishu_auth': feishu_auth(), 'watch_due': watch_due(root)}, ensure_ascii=False))
        if not status['ready']:
            sys.exit(1)
        return
    if a.command == 'watch':
        if a.add and not a.add.is_file():
            raise ValueError('Entry file not found: ' + str(a.add))
        result = watch_add(root, read(a.add)) if a.add else {'entries': watch_entries(root, strict=True), 'due': watch_due(root)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    out = paths(root, a.run, a.area)

    def attempt():
        with operation(root, out):
            return dispatch(a, root, out)
    # Only init queues; it waits outside the operation mutex so the holder can still close out.
    result = wait(attempt, a.wait) if a.command == 'init' else attempt()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('status') == 'failed' or result.get('passed') is False:
        sys.exit(1)


def dispatch(a, root, out):
    if a.command not in {'init', 'status', 'closeout'}:
        require_lease(root, out)
        if read(support(out)/'plan.json', {}).get('mode') in {'diagnostic', 'maintenance'}:
            raise ValueError('Diagnostic/maintenance plans cannot collect, screen or export candidate records')
    if a.command == 'init':
        if a.plan.resolve() == (support(out)/'plan.json').resolve():
            raise ValueError('Pass a plan file other than _support/plan.json (e.g. _support/plan.input.json); '
                             'init compares it with the stored plan')
        plan = read(a.plan)
        begin(root, out, plan)
        write(support(out)/'batch.json', {'schema_version': 1, 'origin': 'jobradar_skill', 'run_id': out.name})
        if not (out/'批次说明.md').exists():
            (out/'批次说明.md').write_text('# ' + out.name + '\n\n' + plan['scope'] + '\n')
        result = {'run': str(out), 'status': 'initialized'}
        due = watch_due(root)
        if due:
            result['watch_due'] = due
            result['watch_note'] = 'Re-check date reached: tell the user and look for the new cycle\'s announcement.'
    elif a.command == 'amend': result=amend(root,out,read(a.plan),a.reason)
    elif a.command == 'ingest': result=ingest(root,out,read(a.input)['records'])
    elif a.command == 'collect-ats': result=collect_ats(root,out,read(a.config))
    elif a.command == 'collect-feishu':
        if not read(support(out)/'plan.json'):raise ValueError('Initialize a bounded run first')
        from feishu_source import collect
        result=collect(out,read(a.config),a.keyword,a.fresh)
    elif a.command == 'screen': result=screen(root,out)
    elif a.command == 'closeout': result=closeout(root,out)
    elif a.command == 'status': result={'plan':read(support(out)/'plan.json'),'summary':read(support(out)/'summary.json'),
            'workbook':(out/'岗位初筛工作表.xlsx').exists()}
    else: result = export_workbook(root, out)
    return result


if __name__ == '__main__':
    main()
