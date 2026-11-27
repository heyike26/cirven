"""Actual HTTP lazy explorer snapshots and JPEG CPU OCR; no GUI or model."""
import argparse,hashlib,json,os,socket,subprocess,sys,threading,time
from pathlib import Path
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--backend',required=True);parser.add_argument('--evidence',required=True);args=parser.parse_args();root=Path(args.backend).resolve();evidence=Path(args.evidence).resolve();evidence.mkdir(parents=True,exist_ok=False);sys.path.insert(0,str(root));appdir=Path(__file__).resolve().parents[1]
from apps.api.main import create_app
from apps.api.config import Settings
import uvicorn
from PIL import Image,ImageDraw,ImageFont
from rapidocr.utils.download_file import DownloadFile
import requests,io
def forbidden(*args,**kwargs):raise AssertionError('OFFLINE_OCR_DOWNLOAD_FORBIDDEN')
DownloadFile.run=forbidden;requests.Session.request=forbidden
folder=evidence/'actual-files';folder.mkdir();(folder/'modified.txt').write_bytes(b'AAAA\r\n');(folder/'gone.txt').write_text('deleted later');(folder/' leading.txt').write_text('identity spaces');(folder/'trailing.txt ').write_text('identity spaces');(folder/'nested').mkdir();(folder/'nested/child.txt').write_text('known deleted child');(folder/'empty').mkdir();(folder/'retyped').mkdir();(folder/'retyped/child.txt').write_text('actual child before replacement')
for i in range(105):(folder/f'page-{i:03}.txt').write_text('same-size original')
image=Image.new('RGB',(1200,220),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype(str(root/'toolchain/fonts/NotoSansCJK-Regular.ttc'),44,index=2);draw.text((35,55),'真实 JPEG SOURCE 2026',font=font,fill='#192b3a');out=io.BytesIO();image.save(out,format='JPEG',quality=92);jpeg=out.getvalue();(folder/'真实画面.jpeg').write_bytes(jpeg);(folder/'mismatch.png').write_bytes(jpeg);(evidence/'original-screen.jpeg').write_bytes(jpeg)
(folder/'不越权.txt').write_text('unchanged exact bytes');untouched=hashlib.sha256((folder/'不越权.txt').read_bytes()).hexdigest();source={str(p.relative_to(appdir)):hashlib.sha256(p.read_bytes()).hexdigest() for name in ['src','scripts','tests'] for p in (appdir/name).rglob('*') if p.is_file()};backend={str(Path(m.__file__).resolve()):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in list(sys.modules.values()) if getattr(m,'__file__',None) and str(Path(m.__file__).resolve()).startswith(str(root)) and str(m.__file__).endswith('.py')};model_calls=[]
class NoModel:
 async def propose(self,request):model_calls.append(request['operation_id']);raise AssertionError('source metadata/explicit import must not invoke model')
 async def close(self):pass
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];application=create_app(Settings(evidence/'db','127.0.0.1',port,False));application.state.discovery.transport=NoModel();application.state.planning_proposals.transport=NoModel();server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start();deadline=time.monotonic()+20
while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.05)
if not server.started:raise RuntimeError('Owned API did not start')
try:
 node=appdir.parents[1]/'staging-native-ui/tooling/node-v22.22.2-linux-x64/bin/node';result=subprocess.run([str(node),str(appdir/'tests/real-explorer-client.mjs'),f'http://127.0.0.1:{port}',str(folder)],cwd=appdir,capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=150);(evidence/'stdout').write_text(result.stdout);(evidence/'stderr').write_text(result.stderr);print(result.stdout,flush=True);print(result.stderr,file=sys.stderr,flush=True)
finally:server.should_exit=True;thread.join(timeout=20);sock.close()
changed=[p for p,d in source.items() if hashlib.sha256((appdir/p).read_bytes()).hexdigest()!=d];changed_backend=[p for p,d in backend.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=d];stable=hashlib.sha256((folder/'不越权.txt').read_bytes()).hexdigest()==untouched;passed=result.returncode==0 and not changed and not changed_backend and not model_calls and stable and not thread.is_alive();receipt={'qualification':'PASS_ACTUAL_HTTP_EXPLORER_AND_JPEG_OCR' if passed else 'FAIL_OR_SOURCE_DRIFT','exit_code':result.returncode,'checks':11,'model_calls':model_calls,'source_files':source,'backend_source_files':backend,'source_changed':changed,'backend_changed':changed_backend,'unselected_file_unchanged':stable,'gui':'NOT_RUN; Root owns actual tree render/search/keyboard/color/JPEG decoding checks'};(evidence/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({key:receipt[key] for key in ['qualification','checks','model_calls','source_changed','backend_changed']}),flush=True);sys.exit(0 if passed else 1)
