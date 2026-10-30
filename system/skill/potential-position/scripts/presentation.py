"""Pure presentation helpers. No network, ranking model or remote writes."""
from datetime import date
from urllib.parse import urlsplit

def direct_cell(*, url='', verified=False, problem='', label='打开岗位'):
    if verified:
        parts=urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname:
            raise ValueError('Verified direct link requires an HTTP(S) URL')
        return f'[{label.replace(chr(91), "（").replace(chr(93), "）")}]({url})'
    if not problem.strip():
        raise ValueError('An unresolved entry requires a concrete problem')
    return problem.strip()

def visible_columns(current):
    omit={'岗位来源','入口核查状态','岗位列表或补充入口'}
    return [field for field in current if field not in omit]

# Same codes as the workbench formula 紧迫排序（自动）: 0–3 = days left, then 4–7 days, 8+, unknown, past.
# The postdoc table counts days left before the user's preparation window (`postdoc_prep_days` in
# the instance preferences); fewer than that is code 8, handled like an expired date.
URGENCY = {0:'特别紧急：今天截止（0天）', 1:'特别紧急：剩1天', 2:'特别紧急：剩2天', 3:'特别紧急：剩3天',
           4:'一般紧急（4–7天）', 5:'常规（8天及以上）', 6:'截止待核', 7:'参考日已过，核实状态',
           8:'准备期不足{days}天，按已过期处理'}
POSTDOC_URGENCY = {0:'特别紧急：距{days}天准备期线0天', 1:'特别紧急：距准备期线剩1天', 2:'特别紧急：距准备期线剩2天',
                   3:'特别紧急：距准备期线剩3天', 4:'一般紧急（距准备期线4–7天）', 5:'常规（距准备期线8天及以上）'}

def urgency(deadline, today, prep_days=0):
    try:
        delta=(date.fromisoformat(deadline)-date.fromisoformat(today)).days
    except (TypeError, ValueError):
        return 6, URGENCY[6]
    if delta<0:
        return 7, URGENCY[7]
    left=delta-prep_days
    if left<0:
        return 8, URGENCY[8].format(days=prep_days)
    code=left if left<=3 else 4 if left<=7 else 5
    return code, (POSTDOC_URGENCY if prep_days else URGENCY)[code].format(days=prep_days)

def order_key(grade, deadline, today, permanent_id, applied=False, prep_days=0):
    bucket,_=urgency(deadline,today,prep_days)
    normalized=deadline if bucket!=6 else '9999-12-31'
    return applied,{'S':0,'A':1,'B':2,'C':3,'D':4}.get(grade,99),bucket,normalized,permanent_id
