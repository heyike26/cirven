"""Owned local API process and real JS client, isolated DB; external model fixture only."""
import argparse,copy,hashlib,io,json,os,socket,subprocess,sys,threading,time,zipfile
from pathlib import Path
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--backend',required=True);parser.add_argument('--evidence',required=True);args=parser.parse_args()
root=Path(args.backend).resolve();evidence=Path(args.evidence).resolve();evidence.mkdir(parents=True,exist_ok=False)
appdir=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from apps.api.main import create_app
from apps.api.config import Settings
from docx import Document
import uvicorn
source_start={str(Path(m.__file__).resolve()):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in list(sys.modules.values()) if getattr(m,'__file__',None) and str(Path(m.__file__).resolve()).startswith(str(root)) and str(m.__file__).endswith('.py')}
files=evidence/'actual-files';templates=evidence/'readonly-templates';files.mkdir();templates.mkdir();(files/'资料.txt').write_bytes('保留原始资料与版本。\r\n尚未明确范围，不伪称完成。 😀\r\n'.encode());(templates/'写法.md').write_text('# 工作说明\n\n每个结论附来源，未知内容写为待核实。\n',encoding='utf8')
d=Document();d.sections[0].header.paragraphs[0].text='用户模板页眉';d.add_heading('用户报告模板',1);d.add_paragraph('{{ report_sections }}');raw=io.BytesIO();d.save(raw);out=io.BytesIO()
with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as src,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dst:
 for member in src.namelist():
  data=src.read(member)
  if member=='[Content_Types].xml':data=data.replace(b'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml',b'application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml')
  dst.writestr(member,data)
(templates/'报告模板.dotx').write_bytes(out.getvalue())
model_calls=[]
class ExternalProtocolFixture:
 async def propose(self,request):
  service=application.state.discovery
  views=service.list(request['project_id']);candidate=copy.deepcopy(views[0]['payload']['candidate']);candidate['summary']+=' [外部模型协议夹具回执，不证明语义]'
  model_calls.append({'operation_id':request['operation_id'],'source_receipt_sha256':request['source_receipt']['sha256'],'feedback_count':request['prompt'].count('NORMALIZED_VIEWPORT_NOT_INFERRED_MODEL_LOCATIONS'),'qualification':'CONTROLLED_EXTERNAL_MODEL_PROTOCOL_FIXTURE'})
  return {'status':'RETURNED','source_receipt_sha256':request['source_receipt']['sha256'],'candidate_json':candidate,'fixture':True}
 async def close(self):pass
node=appdir.parents[1]/'staging-native-ui/tooling/node-v22.22.2-linux-x64/bin/node';results=[]
for phase in ['initial','restart']:
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];application=create_app(Settings(evidence/'db','127.0.0.1',port,False));fixture=ExternalProtocolFixture();application.state.discovery.transport=fixture;application.state.planning_proposals.transport=fixture
 server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
 deadline=time.monotonic()+20
 while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.05)
 if not server.started:raise RuntimeError('Owned API process did not start')
 try:
  result=subprocess.run([str(node),str(appdir/'tests/real-api-client.mjs'),f'http://127.0.0.1:{port}',str(files),str(templates),str(evidence/'state.json'),phase],cwd=appdir,capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=90)
  (evidence/(phase+'.stdout')).write_text(result.stdout);(evidence/(phase+'.stderr')).write_text(result.stderr);results.append({'phase':phase,'exit_code':result.returncode});print(result.stdout);print(result.stderr,file=sys.stderr)
 finally:server.should_exit=True;thread.join(timeout=20);sock.close()
 if thread.is_alive():raise RuntimeError('Owned API process did not terminate')
 if result.returncode:break
changed=[p for p,h in source_start.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
receipt={'qualification':'PASS_ACTUAL_HTTP_CLIENT_NON_GUI' if all(r['exit_code']==0 for r in results) and len(results)==2 and not changed else 'FAIL_OR_SOURCE_DRIFT','commands':results,'external_model_calls':model_calls,'source_files':source_start,'source_changed':changed,'python_version':sys.version,'windows_native':'NOT_QUALIFIED','original_live_model':'NOT_QUALIFIED','gui':'NOT_RUN_BY_CHILD'}
(evidence/'receipt.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'qualification':receipt['qualification'],'model_calls':len(model_calls),'phases':results}))
sys.exit(0 if receipt['qualification']=='PASS_ACTUAL_HTTP_CLIENT_NON_GUI' else 1)
