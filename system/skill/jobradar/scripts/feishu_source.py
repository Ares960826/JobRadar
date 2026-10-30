"""Read-only, bounded Feishu collection via the user's existing lark-cli login."""
import hashlib,json,subprocess,time
from pathlib import Path

def collect(out,config,keyword='',fresh=False):
    required=['name','base_token','table_id','view_id','fields','search_fields','limit']
    if not config:raise ValueError('No Feishu collection configured; fill system/config/feishu-aggregate.json in the instance or skip this source')
    if any(k not in config for k in required):raise ValueError('Incomplete Feishu source configuration')
    import re
    if any('<' in str(config[k]) for k in required):raise ValueError('Feishu source configuration still holds placeholders; fill it in or skip this source')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',config['name']):raise ValueError('Invalid source name')
    if type(config['limit']) is not int or not 1<=config['limit']<=2000:raise ValueError('Invalid page budget')
    # No remote write verbs, uploads, identity fallback or view-filter override.
    query={k:config[k] for k in required};query['keyword']=keyword
    digest=hashlib.sha256(json.dumps(query,sort_keys=True).encode()).hexdigest()[:20]
    folder=out/'_support/evidence'/config['name'];folder.mkdir(parents=True,exist_ok=True)
    data=folder/(digest+'.ndjson');receipt_path=folder/(digest+'.receipt.json')
    if not fresh and data.exists() and receipt_path.exists() and time.time()-data.stat().st_mtime<config.get('cache_minutes',15)*60:
        receipt=json.loads(receipt_path.read_text());receipt['cache_hit']=True;return receipt
    command=['lark-cli','base','+record-search' if keyword else '+record-list',
      '--base-token',config['base_token'],'--table-id',config['table_id'],'--view-id',config['view_id'],
      '--limit',str(config['limit']),'--format','ndjson','--output',str(data), '--overwrite','--as','user']
    if keyword:
        command += ['--keyword',keyword]
        for field in config['search_fields']:command += ['--search-field',field]
    for field in config['fields']:command += ['--field-id',field]
    result=subprocess.run(command,capture_output=True,text=True,timeout=60)
    if result.returncode:raise RuntimeError('Feishu read failed; existing user login/ACL must be checked. '+result.stderr[:400])
    manifest_path=data.with_suffix('.manifest.json')
    if not manifest_path.exists():raise ValueError('Missing query manifest; do not infer completeness')
    manifest=json.loads(manifest_path.read_text())
    count=sum(1 for line in data.read_text().splitlines() if line.strip())
    receipt={'status':'read_passed','source_tier':2,'source':config['name'],'records':count,
      'has_more':manifest.get('has_more'),'next_offset':manifest.get('next_offset'),
      'query_complete':manifest.get('has_more') is False,'query':query,
      'query_context':manifest.get('query_context'),'rev':manifest.get('rev'),
      'scope':'specified view plus keyword; one bounded page, not entire collection',
      'data':str(data),'manifest':str(manifest_path),'cache_hit':False,'remote_writes':0}
    receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt
