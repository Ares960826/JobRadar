// Exact task-scoped controls; never expose generic submit/Enter/upload operations.
import {JevError} from './jev-client.mjs';
const denied=/(提交|投递|发送|上传|支付|购买|删除|注册|登录|立即申请|申请职位|apply\s*now|submit|send|upload|pay\b|purchase|delete|sign\s*in)/i;
const kinds=new Set(['click','fill','select','check','uncheck','hover']);
export function validateTask(t,c){
  if(!t||typeof t.url!=='string'||!Array.isArray(t.allowed_origins)||!t.allowed_origins.length||t.allowed_origins.length>12)throw new JevError('INVALID_BROWSER_TASK');
  for(const origin of t.allowed_origins){const u=new URL(origin);if(u.protocol!=='https:'||u.origin!==origin||u.username||u.password)throw new JevError('INVALID_ORIGIN');}
  const u=new URL(t.url);if(!t.allowed_origins.includes(u.origin)||u.username||u.password)throw new JevError('OUTSIDE_ALLOWED_ORIGINS');
  if(!Array.isArray(t.controls)||t.controls.length>40||!Array.isArray(t.steps)||!t.steps.length||t.steps.length>c.browser_max_steps)throw new JevError('INVALID_BROWSER_TASK');
  for(const x of t.controls)if(!kinds.has(x.kind)||typeof x.name!=='string'||!x.name||typeof x.role!=='string'||!x.role||denied.test(x.name))throw new JevError('UNSAFE_CONTROL');
  for(const s of t.steps){
    if(typeof s.instruction!=='string'||!s.instruction.trim())throw new JevError('INVALID_BROWSER_STEP');
    if(s.values&&(!Object.values(s.values).every(v=>typeof v==='string')))throw new JevError('INVALID_INPUT_VALUES');
  }
  if(t.expected_text&&!Array.isArray(t.expected_text))throw new JevError('INVALID_EXPECTATION');
  return t;
}
export function allowPlan(plan,t,c,currentURL){
  if(!t.allowed_origins.includes(new URL(currentURL).origin)||!Number.isFinite(plan.confidence)||plan.confidence<c.browser_min_confidence)return false;
  const a=plan.action;if(a.kind==='scroll')return t.allow_scroll===true;
  if(!kinds.has(a.kind)||!a.target||denied.test(a.target.name??''))return false;
  return t.controls.some(x=>x.kind===a.kind&&x.name===a.target.name&&x.role===a.target.role);
}
