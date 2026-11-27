// Permanent worst-case reservations: no blob polling, automatic GC or replay.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {canonical,digest,fail,mkdirOwned,ordinaryChain,readJSON,writeOnce} from './files.mjs';
export const TOTAL_BYTES=96*1024*1024,EMERGENCY_BYTES=1024*1024;
export const CALL_RESERVE=2*1024*1024+512*1024;
export const RESERVATIONS=Math.floor((TOTAL_BYTES-EMERGENCY_BYTES)/CALL_RESERVE);
export const MAX_ACTIVE=8;
export function createBudget(root,budgetIdentity,{ledgerRoot,scopeSha}){
  const policy={schema:'eeas.command-observation.budget.v1',budget_identity_sha256:budgetIdentity,total_bytes:TOTAL_BYTES,emergency_bytes:EMERGENCY_BYTES,
    call_reserve_bytes:CALL_RESERVE,reservation_slots:RESERVATIONS,max_calls:32,max_active:MAX_ACTIVE};
  const header=path.join(root,'budget.json');
  function check(){if(canonical(readJSON(header,4096))!==canonical(policy))fail('BUDGET_CHANGED');}
  // Startup audits membership/size/ordinary identities, never blob content.
  let bytes=0,files=0,dirs=0;
  const visit=(dir,depth)=>{
    if(depth>5||++dirs>1024)fail('LEDGER_AUDIT_CAPACITY');ordinaryChain(dir);
    const members=fs.opendirSync(dir);
    try{let entry;while((entry=members.readSync())){
      const name=path.join(dir,entry.name),s=fs.lstatSync(name,{bigint:true});
      if(s.isSymbolicLink())fail('LEDGER_LINK');
      if(s.isDirectory()){visit(name,depth+1);continue;}
      if(!s.isFile()||s.nlink!==1n||s.size>262144n||++files>8192)fail('LEDGER_AUDIT_CAPACITY');
      bytes+=Number(s.size);if(bytes>TOTAL_BYTES)fail('LEDGER_BUDGET_EXHAUSTED');
    }}finally{members.closeSync();}
  };visit(root,0);visit(ledgerRoot,0);
  if(!fs.existsSync(header)&&files!==0)fail('BUDGET_UNKNOWN');
  const reservations=mkdirOwned(root,'reservations');
  try{writeOnce(header,Buffer.from(canonical(policy)+'\n'));}catch(e){if(e.code!=='EEXIST')throw e;}
  check();
  const active=mkdirOwned(root,'active');
  function notice(reason){
    const file=path.join(ledgerRoot,'capacity.json');
    try{writeOnce(file,Buffer.from(canonical({schema:'eeas.command-observation.capacity.v1',scope_sha256:scopeSha,status:'CAPACITY_REACHED',reason,at:new Date().toISOString()})+'\n'));}catch(e){if(e.code!=='EEXIST')throw e;}
  }
  return {acquire(callKey){
    check();
    for(let slot=0;slot<MAX_ACTIVE;slot++){
      const filename=path.join(active,String(slot).padStart(4,'0')+'.json'),row={schema:'eeas.command-observation.active.v1',budget_identity_sha256:budgetIdentity,scope_sha256:scopeSha,call_key:callKey,nonce:randomUUID()};
      try{
        writeOnce(filename,Buffer.from(canonical(row)+'\n'));
        return {release(){try{if(canonical(readJSON(filename,4096))===canonical(row)){ordinaryChain(filename,{file:true});fs.unlinkSync(filename);}}catch{}}};
      }catch(e){
        if(e.code!=='EEXIST')throw e;
        const old=readJSON(filename,4096);if(old.schema!==row.schema||old.budget_identity_sha256!==budgetIdentity||!/^[a-f0-9]{64}$/.test(old.scope_sha256)||!/^[a-f0-9]{64}$/.test(old.call_key))fail('BUDGET_UNKNOWN');
      }
    }
    notice('ACTIVE_CAPTURE_CAPACITY');return null;
  },reserve(callKey){
    check();
    const reservationKey=digest(canonical({scope_sha256:scopeSha,call_key:callKey}));
    for(let slot=0;slot<RESERVATIONS;slot++){
      const file=path.join(reservations,String(slot).padStart(4,'0')+'.json');
      const row={schema:'eeas.command-observation.reservation.v1',budget_identity_sha256:budgetIdentity,scope_sha256:scopeSha,slot,call_key:callKey,reservation_key:reservationKey,reserved_bytes:CALL_RESERVE};
      try{writeOnce(file,Buffer.from(canonical(row)+'\n'));return true;}catch(e){
        if(e.code!=='EEXIST')throw e;
        const old=readJSON(file,4096);
        if(old.schema!==row.schema||old.budget_identity_sha256!==budgetIdentity||old.slot!==slot||old.reserved_bytes!==CALL_RESERVE||!/^[a-f0-9]{64}$/.test(old.scope_sha256)||!/^[a-f0-9]{64}$/.test(old.call_key)||
          old.reservation_key!==digest(canonical({scope_sha256:old.scope_sha256,call_key:old.call_key})))fail('BUDGET_UNKNOWN');
        if(old.reservation_key===reservationKey){notice('ORPHAN_RESERVATION_NO_REPLAY');return false;}
      }
    }
    notice('LEDGER_BUDGET_EXHAUSTED');return false;
  },notice};
}
