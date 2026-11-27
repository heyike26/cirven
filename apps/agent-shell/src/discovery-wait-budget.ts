import {isPlanWaitSeconds,type PlanWaitSeconds} from './discovery-api.ts';

export const DISCOVERY_WAIT_KEY='eeas-discovery-wait-seconds-v1';
export function readDiscoveryWait(storage:Pick<Storage,'getItem'>):PlanWaitSeconds{
  try{const raw=storage.getItem(DISCOVERY_WAIT_KEY);return raw==='120'||raw==='300'||raw==='600'?Number(raw) as PlanWaitSeconds:300;}catch{return 300;}
}
export function saveDiscoveryWait(storage:Pick<Storage,'getItem'|'setItem'>,value:unknown):PlanWaitSeconds{
  if(!isPlanWaitSeconds(value))throw Error('请选择 2、5 或 10 分钟的等待预算。');
  try{storage.setItem(DISCOVERY_WAIT_KEY,String(value));if(storage.getItem(DISCOVERY_WAIT_KEY)!==String(value))throw Error('unconfirmed');}
  catch{throw Error('等待上限尚未确认保存，本窗口仍使用原选择');}
  return value;
}
