// Optional Jev transport. Does not read credentials until explicitly requested.
import {readFile,stat} from 'node:fs/promises';
import {homedir} from 'node:os';
import {resolve} from 'node:path';
import {parseEnv} from 'node:util';
import {createHash} from 'node:crypto';
export const configURL=new URL('../../../config/jev.json',import.meta.url);
export const routes={
  typesafe:{url:'https://api.typesafe.ai/v1/systemone',key:'TYPESAFE_API_KEY'},
  openrouter:{url:'https://openrouter.ai/api/alpha/decisions',key:'OPENROUTER_API_KEY'}
};
export class JevError extends Error {constructor(code){super(code);this.code=code;}}
const fail=code=>{throw new JevError(code);};
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const probability=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&x<=1;
export function hash(value){return createHash('sha256').update(JSON.stringify(value)).digest('hex');}
export async function loadConfig(){
  const c=JSON.parse(await readFile(configURL,'utf8'));
  if(!routes[c.provider]||typeof c.model!=='string'||!/^([~]?typesafe\/)?jev-[a-z0-9.-]+$/.test(c.model))fail('INVALID_CONFIG');
  for(const [key,max] of [['timeout_ms',60000],['max_requests',100],['max_request_bytes',200000],['max_questions',64],['browser_max_steps',30],['browser_max_ms',300000]]){
    if(!Number.isInteger(c[key])||c[key]<1||c[key]>max)fail('INVALID_CONFIG');
  }
  if(!probability(c.browser_min_confidence)||c.browser_min_confidence<0.5)fail('INVALID_CONFIG');
  if(typeof c.credential_file!=='string'||!c.credential_file)fail('INVALID_CONFIG');
  c.credential_file=resolve(c.credential_file.startsWith('~/')?homedir()+c.credential_file.slice(1):c.credential_file);
  return c;
}
export async function credential(c){
  let info,text;
  try{info=await stat(c.credential_file);if(!info.isFile()||(info.mode&0o077))fail('KEY_FILE_PERMISSIONS');text=await readFile(c.credential_file,'utf8');}
  catch(e){if(e instanceof JevError)throw e;fail('KEY_FILE_UNAVAILABLE');}
  let env;try{env=parseEnv(text);}catch{fail('KEY_FILE_FORMAT');}
  const key=env[routes[c.provider].key]?.trim();
  if(!key||/PASTE_|YOUR_.*KEY|在此填写/i.test(key))fail('KEY_NOT_FILLED');
  if(/[\r\n\s]/.test(key)||key.length<12)fail('KEY_FILE_FORMAT');
  return key;
}
export function validateRequest(request,c){
  if(!object(request)||!Object.hasOwn(request,'state')||!object(request.questions))fail('INVALID_REQUEST');
  if(!['string','object'].includes(typeof request.state)||request.state===null)fail('INVALID_STATE');
  const entries=Object.entries(request.questions);
  if(!entries.length||entries.length>c.max_questions)fail('QUESTION_BUDGET');
  for(const [id,q] of entries){
    if(!/^[A-Za-z0-9_-]{1,80}$/.test(id)||!object(q)||!['choice','score','noul'].includes(q.type)||!q.instructions)fail('INVALID_QUESTION');
    if(q.type==='choice'&&(!object(q.criteria)||!Object.keys(q.criteria).length||Object.keys(q.criteria).length>255))fail('INVALID_CRITERIA');
    if(q.type==='score'&&(!Array.isArray(q.criteria)||q.criteria.length<2||q.criteria.length>255))fail('INVALID_CRITERIA');
  }
  const body={model:c.model,state:request.state,questions:request.questions};
  if(Buffer.byteLength(JSON.stringify(body))>c.max_request_bytes)fail('REQUEST_SIZE_BUDGET');
  return body;
}
export function validateResponse(raw,request){
  if(!object(raw)||typeof raw.model!=='string'||!/^([~]?typesafe\/)?jev-[a-z0-9.-]+$/.test(raw.model)||!object(raw.answers))fail('INVALID_RESPONSE');
  const answers={};
  for(const [id,q] of Object.entries(request.questions)){
    const a=raw.answers[id];if(!object(a)||a.type!==q.type)fail('INVALID_ANSWER');
    if(q.type==='noul'){
      if(!probability(a.noul))fail('INVALID_PROBABILITY');answers[id]={type:'noul',noul:a.noul};continue;
    }
    const keys=q.type==='choice'?Object.keys(q.criteria):q.criteria.map((_,i)=>String(i));
    if(!probability(a.confidence)||!object(a.probabilities)||Object.keys(a.probabilities).sort().join('|')!==[...keys].sort().join('|'))fail('INVALID_DISTRIBUTION');
    const ps=Object.values(a.probabilities);
    if(!ps.every(probability)||Math.abs(ps.reduce((s,x)=>s+x,0)-1)>0.025)fail('INVALID_DISTRIBUTION');
    if(q.type==='choice'){
      if(!keys.includes(a.choice)||a.probabilities[a.choice]<Math.max(...ps)-1e-6)fail('UNKNOWN_CHOICE');
      answers[id]={type:'choice',choice:a.choice,confidence:a.confidence,probabilities:a.probabilities};
    }else{
      if(typeof a.score!=='number'||!Number.isFinite(a.score)||a.score<0||a.score>keys.length-1)fail('INVALID_SCORE');
      answers[id]={type:'score',score:a.score,confidence:a.confidence,probabilities:a.probabilities};
    }
  }
  const usage=raw.usage&&['input_tokens','output_tokens'].every(k=>Number.isInteger(raw.usage[k])&&raw.usage[k]>=0)?{input_tokens:raw.usage.input_tokens,output_tokens:raw.usage.output_tokens}:null;
  return {model:raw.model,answers,usage};
}
export function createClient(c,key,{fetchFn=fetch}={}){
  let requests=0;const receipts=[];
  return {receipts,get requests(){return requests;},async decide(request,{signal}={}){
    if(requests>=c.max_requests)fail('REQUEST_COUNT_BUDGET');
    const body=validateRequest(request,c),payload=JSON.stringify(body);
    if(payload.includes(key))fail('CREDENTIAL_IN_PAYLOAD');
    requests++;const started=performance.now();
    const receipt={request:requests,provider:c.provider,requested_model:c.model,input_sha256:hash(body),at:new Date().toISOString(),status:'pending'};
    receipts.push(receipt);
    try{
      const deadline=AbortSignal.timeout(c.timeout_ms),combined=signal?AbortSignal.any([signal,deadline]):deadline;
      const response=await fetchFn(routes[c.provider].url,{method:'POST',redirect:'error',signal:combined,headers:{Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:payload});
      if(!response.ok)fail(`HTTP_${response.status}`);
      const text=await response.text();if(Buffer.byteLength(text)>2000000)fail('RESPONSE_SIZE_BUDGET');
      let raw;try{raw=JSON.parse(text);}catch{fail('INVALID_JSON');}
      const result=validateResponse(raw,body);
      receipt.status='received';receipt.model=result.model;receipt.usage=result.usage;
      return {...result,elapsedMs:Math.round(performance.now()-started)};
    }catch(e){receipt.status='failed';receipt.error=e instanceof JevError?e.code:'TRANSPORT_FAILED';throw new JevError(receipt.error);}
    finally{receipt.elapsed_ms=Math.round(performance.now()-started);}
  }};
}
