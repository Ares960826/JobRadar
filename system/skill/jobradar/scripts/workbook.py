"""Batch workbook (岗位初筛工作表.xlsx) written and read with the standard library only.

The workbook is regenerated from records.json on every export. What the user owns survives:
the two manual columns (用户决定, 我的备注) by JOB ID, and the existing row order (new IDs are
appended). Anything else this module cannot carry over (other sheets, cells right of column R
or above the header) makes the export refuse instead of silently dropping it.
"""
from datetime import datetime, timezone
import hashlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

VERSION = 'stdlib-xlsx-1'
SHEET = '岗位初筛'
FILE = '岗位初筛工作表.xlsx'
HEADERS = ['岗位编号', '实例', '机构', '岗位', '初筛结论', '用户决定', '我的备注', '地点', '开放状态', '记录类型',
           '待核实事项', '淘汰或例外依据', '薪资口径', '资格摘要', '截止信息', '官方链接', '核查时间', '来源证据']
WIDTHS = [16, 13, 27, 40, 24, 15, 32, 24, 24, 15, 55, 40, 34, 45, 34, 55, 28, 55]
MANUAL = (5, 6)  # 用户决定, 我的备注
DECISIONS = ['未决定', '关注', '待补信息', '暂不考虑', '选定深入调查']
TITLE = 'JobRadar · 官网第一轮岗位池'
NOTE = '筛选表头查看大陆 / 国际；黄色两列可手工编辑，刷新按岗位编号保留。保留不等于资格全部通过。'
HEADER_ROW = 4
REGION = {'mainland': '中国大陆', 'international': '国际', 'unassigned': '待分区'}
AVAILABILITY = {'listed': '公开列表在列', 'rolling': '官网注明长期招聘', 'closed': '已明确结束', 'unknown': '是否开放待核实'}
KIND = {'role': '具体岗位', 'program': '招聘项目', 'organization': '机构线索'}

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')


def column(index):
    name = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def position(ref):
    letters, row = re.fullmatch(r'([A-Z]+)(\d+)', ref).groups()
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return int(row), index - 1


def safe(value):
    """Plain text only: a leading = + @ would turn into a formula once the user edits the cell."""
    text = CONTROL.sub('', '' if value is None else str(value))
    return "'" + text if text[:1] in ('=', '+', '@') else text


def cells(record, manual):
    result = record['initial_filter']
    outcome = '保留·仍有待核实' if result['outcome'] == '进入第二轮' else result['outcome']
    basis = '；'.join([h['reason'] + '：' + h['evidence'] for h in result['rule_hits']] + result['exception_review'])
    values = [record['job_id'], REGION.get(record['region'], record['region']), record['company'], record['title'], outcome,
              *manual.get(record['job_id'], ['未决定', '']), record['location'],
              AVAILABILITY.get(record['availability'], record['availability']), KIND.get(record['kind'], record['kind']),
              '；'.join(result['unknowns']), basis or '未发现足够淘汰证据',
              record.get('salary_note') or '未披露/未提取', record.get('eligibility_note') or '待核实',
              record.get('deadline_note') or '未披露', record.get('official_url') or '待核实',
              record.get('checked_at'), record.get('evidence')]
    return [v if i in MANUAL else safe(v) for i, v in enumerate(values)]


# ---------------------------------------------------------------- reading

def text_of(item):
    """Plain or rich text of an <si>/<is>; phonetic hints (rPh) are not cell text."""
    if item is None:
        return ''
    parts = item.findall('m:t', NS) + item.findall('m:r/m:t', NS)
    return ''.join(t.text or '' for t in parts)


def read_sheets(data):
    """{sheet name: {(row, col): text}} for every worksheet in an xlsx byte string."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared = []
        if 'xl/sharedStrings.xml' in names:
            for item in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS):
                shared.append(text_of(item))
        rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets = {}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets', NS):
            target = rels[sheet.get('{%s}id' % NS['r'])].lstrip('/')
            target = target if target.startswith('xl/') else 'xl/' + target
            grid = {}
            for c in ET.fromstring(z.read(target)).iter('{%s}c' % NS['m']):
                kind, v, f = c.get('t'), c.find('m:v', NS), c.find('m:f', NS)
                if f is not None:
                    # Keep what the user typed, not its cached result; a shared-formula child has no text.
                    if not f.text:
                        raise ValueError(f'Shared formula at {c.get("r")} cannot be carried over; '
                                         'replace it with its value before refresh')
                    text = '=' + f.text
                elif kind == 'inlineStr':
                    text = text_of(c.find('m:is', NS))
                elif v is None or v.text is None:
                    continue
                elif kind == 's':
                    text = shared[int(v.text)]
                else:
                    text = v.text
                if text != '':
                    grid[position(c.get('r'))] = text
            sheets[sheet.get('name')] = grid
        return sheets


def existing(data):
    """Row order and manual columns of a workbook this module (or the user) maintained."""
    sheets = read_sheets(data)
    if SHEET not in sheets:
        raise ValueError(f'Workbook has no {SHEET} sheet; inspect before refresh')
    extra = [name for name, grid in sheets.items() if name != SHEET and grid]
    if extra:
        raise ValueError('Workbook has other sheets with content (' + ', '.join(extra) + '); move them out before refresh')
    grid = sheets[SHEET]
    if [grid.get((HEADER_ROW, i), '') for i in range(len(HEADERS))] != HEADERS:
        raise ValueError('Workbook schema changed; inspect before refresh')
    stray = sorted(f'{column(c)}{r}' for (r, c) in grid
                   if c >= len(HEADERS) or r < HEADER_ROW and (r, c) not in ((1, 0), (2, 0)))
    if stray:
        raise ValueError('Workbook has cells outside the managed table (' + ', '.join(stray[:5]) + '); move them before refresh')
    order, manual = [], {}
    for row in sorted({r for r, _ in grid if r > HEADER_ROW}):
        job = grid.get((row, 0))
        if not job:
            if any(grid.get((row, c)) for c in range(len(HEADERS))):
                raise ValueError(f'Workbook row {row} has content but no JOB ID; inspect before refresh')
            continue
        if job in manual:
            raise ValueError('Duplicate workbook JOB ID: ' + job)
        order.append(job)
        manual[job] = [grid.get((row, c), '') for c in MANUAL]
    return order, manual


# ---------------------------------------------------------------- writing

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="11"/><color rgb="FF243746"/><name val="Aptos"/></font>
<font><b/><sz val="18"/><color rgb="FF123B48"/><name val="Aptos"/></font>
<font><sz val="10"/><color rgb="FF526976"/><name val="Aptos"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>
</fonts>
<fills count="4">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF123B48"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFF1C9"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="49" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def cell(ref, text, style, formula=False):
    if text == '':
        return f'<c r="{ref}" s="{style}"/>'
    if formula:
        return f'<c r="{ref}" s="{style}"><f>{escape(text[1:])}</f></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def build(rows):
    """xlsx bytes for title, note, header and one row per record (each a list of 18 strings)."""
    last = HEADER_ROW + max(len(rows), 1)
    span = f'A{HEADER_ROW}:{column(len(HEADERS) - 1)}{last}'
    lines = [f'<row r="1" ht="30" customHeight="1">{cell("A1", TITLE, 1)}</row>',
             f'<row r="2">{cell("A2", NOTE, 2)}</row>',
             f'<row r="{HEADER_ROW}" ht="27" customHeight="1">'
             + ''.join(cell(f'{column(i)}{HEADER_ROW}', h, 3) for i, h in enumerate(HEADERS)) + '</row>']
    for n, values in enumerate(rows, HEADER_ROW + 1):
        lines.append(f'<row r="{n}" ht="76" customHeight="1">' + ''.join(
            cell(f'{column(i)}{n}', CONTROL.sub('', str(v)), 5 if i in MANUAL else 4,
                 # Generated columns are escaped by safe(); a leading = here is the user's own formula.
                 formula=i in MANUAL and str(v).startswith('=')) for i, v in enumerate(values)) + '</row>')
    cols = ''.join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(WIDTHS))
    choices = ','.join(DECISIONS)
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             f'<worksheet xmlns="{NS["m"]}" xmlns:r="{NS["r"]}">'
             f'<dimension ref="A1:{column(len(HEADERS) - 1)}{last}"/>'
             '<sheetViews><sheetView showGridLines="0" workbookViewId="0">'
             f'<pane xSplit="4" ySplit="{HEADER_ROW}" topLeftCell="E{HEADER_ROW + 1}" activePane="bottomRight" state="frozen"/>'
             f'<selection pane="bottomRight" activeCell="E{HEADER_ROW + 1}" sqref="E{HEADER_ROW + 1}"/>'
             '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
             f'<cols>{cols}</cols><sheetData>{"".join(lines)}</sheetData>'
             f'<autoFilter ref="{span}"/>'
             f'<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" '
             f'sqref="F{HEADER_ROW + 1}:F{last}"><formula1>"{escape(choices)}"</formula1></dataValidation></dataValidations>'
             '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>')
    absolute = f"'{SHEET}'!$A${HEADER_ROW}:${column(len(HEADERS) - 1)}${last}"
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<workbook xmlns="{NS["m"]}" xmlns:r="{NS["r"]}"><bookViews><workbookView/></bookViews>'
                f'<sheets><sheet name="{SHEET}" sheetId="1" r:id="rId1"/></sheets>'
                f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">{escape(absolute)}</definedName>'
                '</definedNames><calcPr fullCalcOnLoad="1"/></workbook>')
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(TITLE)}</dc:title><dc:creator>JobRadar</dc:creator>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in [('[Content_Types].xml', CONTENT_TYPES), ('_rels/.rels', ROOT_RELS),
                           ('docProps/core.xml', core), ('xl/workbook.xml', workbook),
                           ('xl/_rels/workbook.xml.rels', WORKBOOK_RELS), ('xl/styles.xml', STYLES),
                           ('xl/worksheets/sheet1.xml', sheet)]:
            z.writestr(name, text)
    return buffer.getvalue()


def export(out, records):
    """Refresh out/岗位初筛工作表.xlsx from screened records; returns a small receipt."""
    out = Path(out)
    if not records or any('initial_filter' not in r for r in records):
        raise ValueError('Screen a nonempty batch first')
    target, support = out / FILE, out / '_support'
    before = target.read_bytes() if target.exists() else None
    order, manual = existing(before) if before else ([], {})
    by_id = {}
    for r in records:
        if r['job_id'] in by_id:
            raise ValueError('Duplicate JOB ID in records: ' + r['job_id'])
        by_id[r['job_id']] = r
    missing = [job for job in order if job not in by_id]
    if missing:
        raise ValueError(f'Would remove existing record {missing[0]}; stopped')
    sequence = order + [r['job_id'] for r in records if r['job_id'] not in manual]
    data = build([cells(by_id[job], manual) for job in sequence])
    pending = support / 'workbook.pending.xlsx'
    support.mkdir(parents=True, exist_ok=True)
    pending.write_bytes(data)
    # Detect edits made while we were building before replacing the user's working table.
    current = target.read_bytes() if target.exists() else None
    if current != before:
        raise ValueError('Workbook changed during export; pending file retained')
    if before:
        backups = support / 'backups'
        backups.mkdir(exist_ok=True)
        (backups / f'workbook-{hashlib.sha256(before).hexdigest()[:16]}.xlsx').write_bytes(before)
    pending.replace(target)
    return {'workbook': str(target), 'records': len(sequence), 'manual_fields_preserved': len(manual),
            'engine': VERSION}
