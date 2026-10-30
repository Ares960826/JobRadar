import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {JevError} from './jev-client.mjs';
import {validateTask,allowPlan} from './jev-policy.mjs';
const runtime=new URL('../../../runtime/jev/node_modules/',import.meta.url);
export async function dependencies(){
  const {JevBrowser}=await import(new URL('@tontoko/jev-browser/dist/index.js',runtime));
  const {chromium}=await import(new URL('playwright/index.mjs',runtime));
  return {JevBrowser,chromium};
}
export async function browse(task,c,client,checkpoint){
  validateTask(task,c);
  if(c.browser_cdp_endpoint!=='http://127.0.0.1:19222')throw new JevError('UNEXPECTED_BROWSER_ENDPOINT');
  try{
    const {stdout}=await promisify(execFile)('python3',[fileURLToPath(new URL('../../../tools/browser.py',import.meta.url)),'status'],{timeout:5000});
    if(!JSON.parse(stdout).ready)throw new Error();
  }catch{throw new JevError('DEDICATED_BROWSER_NOT_READY');}
  const {JevBrowser,chromium}=await dependencies();
  const connection=await chromium.connectOverCDP(c.browser_cdp_endpoint,{timeout:5000});
  let page,core;
  const receipt={status:'running',steps:[],model_receipts:client.receipts,final_submission_enabled:false};
  const deadline=AbortSignal.timeout(c.browser_max_ms);
  let unexpectedPopup=false;
  try{
    const context=connection.contexts()[0];if(!context)throw new JevError('NO_BROWSER_CONTEXT');
    page=await context.newPage();
    page.on('popup',async popup=>{unexpectedPopup=true;await popup.close().catch(()=>{});});
    page.on('dialog',dialog=>dialog.dismiss().catch(()=>{}));
    await page.route('**/*',async route=>{
      const request=route.request();
      if(!['GET','HEAD','OPTIONS'].includes(request.method()))return route.abort();
      if(request.isNavigationRequest()&&!task.allowed_origins.includes(new URL(request.url()).origin))return route.abort();
      return route.continue();
    });
    const engine={decide:async(req,options)=>{
      if(unexpectedPopup)throw new JevError('POPUP_REQUIRES_HANDOFF');
      const signal=options?.signal?AbortSignal.any([deadline,options.signal]):deadline;
      try{return await client.decide(req,{signal});}
      finally{await checkpoint(receipt);}
    }};
    core=new JevBrowser({page,engine,timeoutMs:Math.min(c.browser_max_ms,30000),
      allowAction:plan=>allowPlan(plan,task,c,page.url()),
      allowCommand:command=>['click','type','select_option','check','hover','snapshot'].includes(command.command)||(command.command==='mouse'&&command.action==='wheel'&&task.allow_scroll===true)});
    await page.goto(task.url,{waitUntil:'domcontentloaded',timeout:20000});
    for(const [index,step] of task.steps.entries()){
      deadline.throwIfAborted();
      if(unexpectedPopup)throw new JevError('POPUP_REQUIRES_HANDOFF');
      const observed=await core.observe(step.instruction,{values:step.values??{},signal:deadline});
      if(!observed){receipt.status='needs_handoff';receipt.reason='NO_ALLOWED_MATCH';break;}
      const entry={step:index+1,kind:observed.action.kind,target:observed.action.target?.name??null,confidence:observed.confidence,executed:false};
      receipt.steps.push(entry);await checkpoint(receipt);
      try{await core.act({id:observed.id},{signal:deadline});entry.executed=true;}
      catch(e){entry.outcome='not_verified_no_retry';throw e;}
      entry.url=page.url();await checkpoint(receipt);
    }
    const final=await core.snapshot({signal:deadline});
    receipt.final_snapshot=final;
    const text=(await page.locator('body').innerText({timeout:5000}));
    const expected=task.expected_text??[];
    receipt.readback={url:page.url(),expected_text:expected.map(s=>({text:s,present:text.includes(s)}))};
    if(receipt.status==='running')receipt.status=expected.length&&receipt.readback.expected_text.every(x=>x.present)?'readback_matched':'needs_independent_verification';
    return receipt;
  }catch(e){
    receipt.status='stopped';receipt.reason=e instanceof JevError?e.code:typeof e.code==='string'&&/^[A-Z_]+$/.test(e.code)?e.code:'BROWSER_STOPPED';
    return receipt;
  }finally{
    await checkpoint(receipt);
    await core?.close().catch(()=>{});
    await page?.close().catch(()=>{});
    await connection.close().catch(()=>{}); // Disconnect only; dedicated Chrome stays open.
  }
}
