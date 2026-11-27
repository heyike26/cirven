"""Real HTTP frontend client + configured offline CPU OCR, no GUI or LLM."""
import argparse,hashlib,io,json,os,socket,subprocess,sys,threading,time
from pathlib import Path
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--backend',required=True);parser.add_argument('--evidence',required=True);args=parser.parse_args()
root=Path(args.backend).resolve();evidence=Path(args.evidence).resolve();evidence.mkdir(parents=True,exist_ok=False);appdir=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from apps.api.main import create_app
from apps.api.config import Settings
from PIL import Image,ImageDraw,ImageFont
from rapidocr.utils.download_file import DownloadFile
import uvicorn
def forbidden(*args,**kwargs):raise AssertionError('OCR_FRONTEND_PROBE_NETWORK_DOWNLOAD_FORBIDDEN')
DownloadFile.run=forbidden
import requests
requests.Session.request=forbidden
files=evidence/'actual-files';files.mkdir();image=Image.new('RGB',(1500,350),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype(str(root/'toolchain/fonts/NotoSansCJK-Regular.ttc'),46,index=2);truth=['问题待核实 SOURCE 2026','版本待确认 REV 7']
for index,text in enumerate(truth):draw.text((48,35+index*150),text,font=font,fill='#16253b')
out=io.BytesIO();image.save(out,format='PNG');raw=out.getvalue();(files/'原创画面.png').write_bytes(raw);(evidence/'original-screen.png').write_bytes(raw)
own={str(p.relative_to(appdir)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ['src','scripts','tests'] for p in (appdir/folder).rglob('*') if p.is_file()};node=appdir.parents[1]/'staging-native-ui/tooling/node-v22.22.2-linux-x64/bin/node';model_calls=[];results=[]
class NoModel:
 async def propose(self,request):model_calls.append(request['operation_id']);raise AssertionError('OCR observation/recording must not invoke model')
 async def close(self):pass
source_start={str(Path(m.__file__).resolve()):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in list(sys.modules.values()) if getattr(m,'__file__',None) and str(Path(m.__file__).resolve()).startswith(str(root)) and str(m.__file__).endswith('.py')}
for phase in ['initial','restart']:
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];application=create_app(Settings(evidence/'db','127.0.0.1',port,False));application.state.discovery.transport=NoModel();application.state.planning_proposals.transport=NoModel();server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start();deadline=time.monotonic()+20
 while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.05)
 if not server.started:raise RuntimeError('Owned OCR API did not start')
 try:
  result=subprocess.run([str(node),str(appdir/'tests/real-ocr-client.mjs'),f'http://127.0.0.1:{port}',str(files),str(evidence/'state.json'),phase],cwd=appdir,capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=90);(evidence/(phase+'.stdout')).write_text(result.stdout);(evidence/(phase+'.stderr')).write_text(result.stderr);results.append({'phase':phase,'exit_code':result.returncode});print(result.stdout,flush=True);print(result.stderr,file=sys.stderr,flush=True)
 finally:server.should_exit=True;thread.join(timeout=20);sock.close()
 if thread.is_alive():raise RuntimeError('Owned OCR API did not terminate')
 if result.returncode:break
 if phase=='initial':
  changed=Image.new('RGB',(1500,350),'white');change=io.BytesIO();changed.save(change,format='PNG');(files/'原创画面.png').write_bytes(change.getvalue())
changed_root=[path for path,sha in source_start.items() if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha];changed_own=[name for name,sha in own.items() if hashlib.sha256((appdir/name).read_bytes()).hexdigest()!=sha];passed=len(results)==2 and all(value['exit_code']==0 for value in results) and not changed_root and not changed_own and not model_calls
receipt={'qualification':'PASS_ACTUAL_HTTP_AND_LOCAL_CPU_OCR' if passed else 'FAIL_OR_SOURCE_DRIFT','commands':results,'own_source_files':own,'loaded_backend_source_files':source_start,'changed_source':{'own':changed_own,'backend':changed_root},'original_png_sha256':hashlib.sha256(raw).hexdigest(),'ground_truth':truth,'ocr_network_download_calls':0,'model_calls':model_calls,'native_windows_capture':'NOT_RUN; original generated PNG through actual local-file authority','gui':'NOT_RUN','paid_model_or_fact_accuracy':'NOT_QUALIFIED_BY_THIS_PROBE','python_version':sys.version}
(evidence/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps({key:receipt[key] for key in ['qualification','commands','changed_source','model_calls']}),flush=True);sys.exit(0 if passed else 1)
