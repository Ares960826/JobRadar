#!/usr/bin/env node
// Explicit test entrypoint. No daemon, no background work, no production pool writes.
import {readFile,writeFile,mkdir,stat,realpath} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {spawnSync} from 'node:child_process';
import {loadConfig,credential,createClient,validateRequest,JevError,hash,configURL} from './jev-client.mjs';
import {validateTask} from './jev-policy.mjs';
const system=fileURLToPath(new URL('../../../',import.meta.url));
async function save(path,value){await writeFile(path,JSON.stringify(value,null,2)+'\n',{mode:0o600});}
async function newRun(name){
  if(!/^\d{4}-\d{2}-\d{2}_[\p{L}\p{N}_-]{1,80}$/u.test(name??''))throw new JevError('MEANINGFUL_NEW_RUN_NAME_REQUIRED');
  const root=join(system,'validation');await mkdir(root,{recursive:true});
  if(await realpath(root)!==resolve(root))throw new JevError('UNEXPECTED_RESULT_PATH');
  const path=join(root,name);try{await mkdir(path,{mode:0o700});}catch{throw new JevError('RUN_EXISTS_OR_UNWRITABLE');}return path;
}
async function ultrafastBrowse(task,out,c){
  const runtime=fileURLToPath(new URL('../../../runtime/jev-ultrafast',import.meta.url));
  const python=join(runtime,'.venv','bin','python');
  const script=fileURLToPath(new URL('./jev_ultrafast_browser.py',import.meta.url));
  const receiptPath=join(out,'receipt.json');
  const res=spawnSync(python,[script,'--task',join(out,'input.json'),'--out',receiptPath,'--max-steps',String(c.browser_max_steps)],
    {encoding:'utf8',env:{...process.env,BU_CDP_URL:c.browser_cdp_endpoint??'http://127.0.0.1:19222',BH_TELEMETRY:'0'},timeout:c.browser_max_ms+30000});
  if(res.error)return{status:'stopped',reason:'ADAPTER_LAUNCH_FAILED',engine:'jev-ultrafast'};
  try{return JSON.parse(await readFile(receiptPath,'utf8'));}catch{return{status:'stopped',reason:'ADAPTER_NO_RECEIPT',engine:'jev-ultrafast',stderr:(res.stderr??'').slice(-400)};}
}
async function main(){
  const {positionals,values}=parseArgs({allowPositionals:true,options:{input:{type:'string'},run:{type:'string'},execute:{type:'boolean',default:false},engine:{type:'string',default:'ultrafast'}}});
  const command=positionals[0],c=await loadConfig();
  if(command==='doctor'){
    let key='not_checked',nodeReady=false;
    try{await credential(c);key='filled_not_authenticated';}catch(e){key=e instanceof JevError?e.code:'unavailable';}
    try{await (await import('./jev-browser.mjs')).dependencies();nodeReady=true;}catch{}
    const {existsSync}=await import('node:fs');
    const ultrafastPython=join(fileURLToPath(new URL('../../../runtime/jev-ultrafast',import.meta.url)),'.venv','bin','python');
    const ultrafastReady=existsSync(ultrafastPython);
    console.log(JSON.stringify({provider:c.provider,model:c.model,credential_file:c.credential_file,key_status:key,browser_runtime_installed:nodeReady,default_engine:'ultrafast',engines:{ultrafast:ultrafastReady?'ready':'missing',node:nodeReady?'ready':'missing'},network_requests:0,live_test:'not_checked_by_doctor',config:fileURLToPath(configURL)},null,2));return;
  }
  if(!['decide','browse'].includes(command)||!values.input)throw new JevError('USAGE_doctor_OR_decide_browse_input_run_execute');
  const input=JSON.parse(await readFile(resolve(values.input),'utf8'));
  const request=command==='decide'?validateRequest(input,c):validateTask(input,c);
  if(!values.execute){console.log(JSON.stringify({status:'prepared_not_sent',command,provider:c.provider,input_sha256:hash(request),network_requests:0,requires:'explicit --execute after user gives test task'}));return;}
  const key=await credential(c);if(JSON.stringify(input).includes(key))throw new JevError('CREDENTIAL_IN_PAYLOAD');
  const out=await newRun(values.run),client=createClient(c,key);
  // Never save secret values even if an upstream error accidentally returns them.
  const checkpoint=async receipt=>{if(JSON.stringify(receipt).includes(key))throw new JevError('CREDENTIAL_IN_RECEIPT');await save(join(out,'receipt.json'),receipt);};
  await save(join(out,'input.json'),input);
  let receipt;
  try{
    if(command==='decide')receipt={status:'received_for_review',result:await client.decide(input),model_receipts:client.receipts,automatic_elimination:false};
    else if(values.engine==='node')receipt=await (await import('./jev-browser.mjs')).browse(input,c,client,checkpoint);
    else if(values.engine==='ultrafast')receipt=await ultrafastBrowse(input,out,c);
    else throw new JevError('UNKNOWN_ENGINE');
  }catch(e){receipt={status:'stopped',reason:e instanceof JevError?e.code:'REQUEST_FAILED',model_receipts:client.receipts};}
  await checkpoint(receipt);
  console.log(JSON.stringify({status:receipt.status,reason:receipt.reason??null,requests:client.requests,receipt:join(out,'receipt.json')}));
  if(receipt.status==='stopped')process.exitCode=1;
}
main().catch(e=>{console.error(JSON.stringify({status:'stopped',error:e instanceof JevError?e.code:'LOCAL_SETUP_ERROR'}));process.exitCode=1;});
