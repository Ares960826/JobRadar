"""Evidence-first elimination. Facts must be scoped to this role, never a group average.

This module does not infer personal eligibility, annualize internship stipends,
estimate overseas taxes, or score a career profile. Unknown stays unknown.

Thresholds and personal flags are not part of this module: they come from the user's
`hard_rules` (instance `system/config/preferences.json`, schema below). An omitted section
means that condition is not part of the user's hard conditions.

    version                   str, the user's label for this rule set
    mainland_salary           absolute_floor_cny, review_below_cny, location_floors: [
                                {fact, label, floor_cny, review_below_cny, exception, code}]
    mainland_benefits         exception_min_annual_cny
    international_net_savings monthly_floor_cny
    review_notes              [{fact, note}]            flag -> kept for human review
    conditional_rejections    [{fact, code, reason, exception}]
    deferred_checks           [{fact, note}]            flag -> unknown for stage 2
"""
VERSION = 'implementation-0.4'

SECTIONS = {
    'mainland_salary': {'absolute_floor_cny': int, 'review_below_cny': int, 'location_floors': list},
    'mainland_benefits': {'exception_min_annual_cny': int},
    'international_net_savings': {'monthly_floor_cny': int},
    'review_notes': list, 'conditional_rejections': list, 'deferred_checks': list,
}
ITEMS = {
    'location_floors': {'fact': str, 'label': str, 'floor_cny': int, 'review_below_cny': int, 'exception': str, 'code': str},
    'review_notes': {'fact': str, 'note': str},
    'conditional_rejections': {'fact': str, 'code': str, 'reason': str, 'exception': str},
    'deferred_checks': {'fact': str, 'note': str},
}


def check(rules):
    """Refuse a malformed rule set instead of silently skipping a condition."""
    def fields(where, value, spec):
        if not isinstance(value, dict):
            raise ValueError(f'hard_rules.{where} must be an object')
        for key, kind in spec.items():
            if type(value.get(key)) is not kind or kind is int and value[key] < 0:
                raise ValueError(f'hard_rules.{where}.{key} must be a {kind.__name__}')
    if not isinstance(rules, dict) or not isinstance(rules.get('version'), str) or not rules['version'].strip():
        raise ValueError('hard_rules needs a non-empty version label')
    unknown = set(rules) - set(SECTIONS) - {'version', 'note'}
    if unknown:
        raise ValueError('hard_rules has unknown sections: ' + ', '.join(sorted(unknown)))
    for name, spec in SECTIONS.items():
        if name not in rules:
            continue
        if spec is list:
            if not isinstance(rules[name], list):
                raise ValueError(f'hard_rules.{name} must be a list')
        else:
            fields(name, rules[name], spec)
    lists = dict(rules, **{'location_floors': rules.get('mainland_salary', {}).get('location_floors', [])})
    for name, spec in ITEMS.items():
        for i, item in enumerate(lists.get(name, [])):
            fields(f'{name}[{i}]', item, spec)
    return rules


def wan(amount):
    return f'{amount / 10000:g}万元'


def evaluate(facts, rules):
    hits, unknowns, exceptions = [], list(facts.get('unknowns', [])), []
    def hit(code, text, evidence):
        hits.append(dict(code=code, reason=text, evidence=evidence))
    def conditional(code, text, evidence, exception_key):
        # Missing opportunity assessment is uncertainty, never evidence against a role.
        exception = facts.get(exception_key)
        if exception is False and facts.get(exception_key + '_evidence'):
            hit(code, text, str(evidence) + "; 例外核查：" + facts[exception_key + '_evidence'] if evidence else evidence)
        else:
            exceptions.append(text + '；例外依据已给出，待复核' if exception is True and facts.get(exception_key + '_evidence')
                              else text + '；例外是否成立未知，保留待核实')
    # Only explicit, scoped facts enter these branches.
    for key, code, text in [('closed','CLOSED','已结束的招聘窗口'),
                            ('all_locations_excluded','LOCATION','所有已确认工作地点均明确排除'),
                            ('forbidden_identity','IDENTITY','明确不接受的用工身份'),
                            ('ineligible','ELIGIBILITY','明确不满足不可豁免资格')]:
        if facts.get(key) is True:
            hit(code,text,facts.get(key+'_evidence',''))
    pay, benefits = rules.get('mainland_salary'), rules.get('mainland_benefits')
    savings = rules.get('international_net_savings')
    if facts.get('region') == 'mainland':
        salary = facts.get('annual_salary_cny')
        confirmed = bool(salary and salary.get('scope_confirmed') and salary.get('basis') == 'gross_total')
        if pay and confirmed:
            low, high = salary.get('min'), salary.get('max')
            place = next((f for f in pay.get('location_floors', []) if facts.get(f['fact'])), None)
            if high is not None and high < pay['absolute_floor_cny']:
                hit('SALARY_ABSOLUTE','大陆税前年薪最高仍低于' + wan(pay['absolute_floor_cny']),salary['evidence'])
            elif place and high is not None and high < place['floor_cny']:
                conditional(place['code'], place['label'] + '税前年薪最高低于' + wan(place['floor_cny']), salary['evidence'], place['exception'])
            elif low is not None and low < (place or pay)['review_below_cny']:
                exceptions.append('薪资区间涉及需补偿价值或谈薪的区间，不能按下限淘汰整个范围')
        elif pay:
            unknowns.append('适用本人具体岗位的税前年薪组成与范围')
        if benefits and facts.get('benefits_deficient') is True:
            floor = benefits['exception_min_annual_cny']
            if confirmed and salary.get('min') is not None and salary['min'] >= floor:
                exceptions.append('五险一金不足命中年薪至少' + wan(floor) + '例外，须人类复核')
            elif confirmed and salary.get('max') is not None and salary['max'] < floor:
                hit('BENEFITS','五险一金明确不足且未达高薪例外',facts.get('benefits_deficient_evidence',''))
            else:
                exceptions.append('五险一金明确不足，但' + wan(floor) + '高薪例外能否成立未知')
    elif facts.get('region') == 'international':
        if savings:
            floor = '人民币' + wan(savings['monthly_floor_cny'])
            unknowns.append('海外税后收入减必要开支的月净储蓄是否达到' + floor)
            if facts.get('net_savings_below_floor') is True:
                conditional('NET_SAVINGS', '海外月净储蓄低于' + floor, facts.get('net_savings_below_floor_evidence', ''), 'exceptional_opportunity')
    elif pay or savings:
        unknowns.append('地区未确定或混合：分别核查大陆税前年薪与国际净储蓄，不能套用单一地区门槛')
    for note in rules.get('review_notes', []):
        if facts.get(note['fact']):
            exceptions.append(note['note'])
    for rule in rules.get('conditional_rejections', []):
        if facts.get(rule['fact']):
            conditional(rule['code'], rule['reason'], facts.get(rule['fact'] + '_evidence', ''), rule['exception'])
    # Deferred checks (e.g. travel) are later-stage review signals, never a hard rejection.
    for note in rules.get('deferred_checks', []):
        if facts.get(note['fact']):
            unknowns.append(note['note'])
    for item in hits:
        if not item['evidence']:
            raise ValueError('淘汰必须有明确来源证据')
    return dict(outcome='直接淘汰' if hits else ('例外保留' if exceptions else '进入第二轮'),
                rule_version=rules.get('version', '') + ' / ' + VERSION, rule_hits=hits, exception_review=exceptions,
                unknowns=list(dict.fromkeys(unknowns)),
                meaning='进入第二轮仅表示未发现足够淘汰证据，不等于硬条件全部通过')
