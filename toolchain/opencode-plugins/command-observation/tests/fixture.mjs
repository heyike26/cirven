// Test boundary only: production observer never imports child_process.
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createObserver} from '../index.mjs';
import {canonical,digest} from '../files.mjs';

export async function exercise(configPath,{callID='call_one',exit=0,changeBefore=null,changeDuring=null,
  after=true,nullExit=false,large=false,close=false,cwd=null,omitWorkdir=false,changeDuringFile=null}={}){
  const config=JSON.parse(fs.readFileSync(configPath,'utf8')),scope=config.scope,native=config.native_session_id;
  const timings={},start=performance.now();
  const hooks=createObserver({directory:scope.workspace_root,configPath});timings.create_ms=performance.now()-start;
  const q=text=>"'"+text.replaceAll("'","'\\''")+"'";
  let code=changeDuring===null?'':`require('fs').writeFileSync(${JSON.stringify(path.join(scope.workspace_root,'a.py'))},${JSON.stringify(changeDuring)});`;
  if(changeDuringFile!==null){if(changeDuring!==null)throw Error('Choose one fixture change source');code=`require('fs').copyFileSync(${JSON.stringify(changeDuringFile)},${JSON.stringify(path.join(scope.workspace_root,'a.py'))});`;}
  code+=large?"process.stdout.write('界'.repeat(40000));":`process.stdout.write('actual-command-${exit}\\n');process.stderr.write('actual-stderr\\n');`;
  code+=`process.exit(${exit});`;
  const args={command:q(process.execPath)+' -e '+q(code)+' 2>&1',...omitWorkdir?{}:{workdir:scope.workspace_root}};
  const input={tool:'bash',sessionID:native,callID};
  let mark=performance.now();await hooks['tool.execute.before'](input,{args});timings.before_ms=performance.now()-mark;
  if(changeBefore!==null)fs.writeFileSync(path.join(scope.workspace_root,'a.py'),changeBefore);
  mark=performance.now();await hooks['shell.env']({cwd:cwd??scope.workspace_root,sessionID:native,callID},{env:{}});timings.pre_ms=performance.now()-mark;
  mark=performance.now();
  const processResult=spawnSync('/bin/sh',['-c',args.command],{cwd:scope.workspace_root,encoding:'utf8',maxBuffer:1024*1024});
  if(processResult.error)throw processResult.error;
  timings.command_ms=performance.now()-mark;
  const output=processResult.stdout;
  const result={title:args.command,output,metadata:{output:output.slice(-30000),exit:nullExit?null:processResult.status,truncated:false}};
  mark=performance.now();if(after)await hooks['tool.execute.after']({...input,args},result);timings.after_ms=performance.now()-mark;
  if(close)await hooks.dispose();
  const key=digest(canonical({native_session_id:native,call_id:callID})),dir=path.join(scope.ledger_root,'calls',key);
  const read=name=>fs.existsSync(path.join(dir,name))?JSON.parse(fs.readFileSync(path.join(dir,name),'utf8')):null;
  return {input,args,result,process_exit:processResult.status,before:read('before.json'),pre:read('pre.json'),record:read('result.json'),dir,timings};
}
