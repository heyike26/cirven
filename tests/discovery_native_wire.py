"""External OpenAI-compatible protocol fixture; the original OpenCode loop is real."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
from packages.domain.tasks import canonical
from test_opencode_planning import KEY,MODEL
from test_discovery_workbench import candidate


class DiscoveryNativeWire:
    def __init__(self):
        self.requests=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append({'body':body,'auth_matches':self.headers.get('Authorization')=='Bearer '+KEY})
                schema=body['tools'][0]['function']['parameters']
                if 'claims' in schema.get('properties',{}):output=candidate()
                else:output={'title':'真实原生协议任务计划','summary':'由明确接受的要求编排，外部模型仅协议夹具。',
                    'nodes':[{'id':'check','title':'核对资料','description':'核对原件、范围和验收。','outputs':['来源核对清单'],
                        'inputs':['所选资料'],'acceptance':['逐项对应原文和验收。']},
                        {'id':'report','title':'编写任务报告','description':'用核对结果生成文稿。','depends_on':['check'],
                         'inputs':['来源核对清单'],'outputs':['任务报告'],'acceptance':['待确认项目单列。']}],
                    'questions':[]}
                events=[{'id':'chatcmpl-original-discovery','object':'chat.completion.chunk','created':0,'model':MODEL,
                    'choices':[{'index':0,'delta':{'role':'assistant','tool_calls':[{'index':0,'id':'call_discovery',
                    'type':'function','function':{'name':'StructuredOutput','arguments':canonical(output)}}]},'finish_reason':None}]},
                    {'id':'chatcmpl-original-discovery','object':'chat.completion.chunk','created':0,'model':MODEL,
                    'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls'}],
                    'usage':{'prompt_tokens':40,'completion_tokens':80,'total_tokens':120}}]
                raw=(''.join('data: '+canonical(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def start(self):self.thread.start();return self
    @property
    def base_url(self):return f'http://127.0.0.1:{self.server.server_port}/v1'
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
