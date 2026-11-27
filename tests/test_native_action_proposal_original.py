"""Actual original CLI/private tool and real ASGI; only model and OS observation are fixtures."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time

from packages.domain.tasks import canonical, digest
from test_native_file_continuity_api import scene, MESSAGES
from test_native_messages_docx import configure
from test_native_review import FrozenPixels


def check(value, message='original action proposal bridge failed'):
    if not value:
        raise AssertionError(message)


class ProposalWire:
    def __init__(self, span):
        self.calls, self.results, self.errors = [], [], []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                try:
                    body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));owner.calls.append(body)
                    check({row['function']['name'] for row in body['tools']}=={'eeas_native_message_call'})
                    if len(owner.calls)==1:
                        args={'method_id':'propose_native_append','arguments_json':canonical(span)}
                        delta={'role':'assistant','tool_calls':[{'index':0,'id':'exact_action_proposal',
                            'type':'function','function':{'name':'eeas_native_message_call','arguments':canonical(args)}}]};finish='tool_calls'
                    else:
                        for item in body['messages']:
                            if item['role']=='tool':
                                content=item['content'];content='\n'.join(x.get('text','') for x in content) if isinstance(content,list) else content
                                try:owner.results.append(json.loads(content))
                                except ValueError:pass
                        delta={'role':'assistant','content':canonical({'text':'追加提案已准备，尚未输入或保存。','questions':[]})};finish='stop'
                    chunks=[{'id':'proposal-fixture','object':'chat.completion.chunk','created':0,'model':'docx-wire',
                        'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                        {'id':'proposal-fixture','object':'chat.completion.chunk','created':0,'model':'docx-wire',
                        'choices':[{'index':0,'delta':{},'finish_reason':finish}]}]
                    raw=(''.join('data: '+canonical(x)+'\n\n' for x in chunks)+'data: [DONE]\n\n').encode()
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except BaseException as error:
                    owner.errors.append(str(error));self.send_error(500)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=False);self.thread.start()

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)


def test_original_chat_tool_creates_one_bound_proposal_parent_get_returns_it_without_preparing_or_input(scene):
    client,pid,folder,_base,_source,settings=scene
    native=client.app.state.native_messages;native.context=FrozenPixels()
    raw='请在当前页面的经典记事本末尾追加：原始中文🙂'
    span={'start':raw.index('原始中文'),'end':len(raw),'quote':'原始中文🙂'}
    wire=ProposalWire(span);before=(folder/'材料.md').read_bytes();result=None
    try:
        profile=configure(client,settings,wire)
        body={'operation_id':'actual-original-append-proposal','raw_text':raw,'project_id':pid,
            'conversation_id':'original-action-discussion','context_ref':native.context.context_ref,'provider_profile_ref':profile}
        first=client.post(MESSAGES,json=body);check(first.status_code==200,first.text)
        parent=MESSAGES+'/'+body['operation_id'];deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            result=client.get(parent).json()
            if result['status']!='PROCESSING':break
            time.sleep(.1)
        check(result['status']=='RETURNED',canonical(result))
        check(result['text']=='追加提案已准备，尚未输入或保存。')
        check(len(result.get('native_action_proposals',[]))==1,canonical(result))
        proposal=result['native_action_proposals'][0]
        check(proposal['status']=='PROPOSED' and proposal['request']['payload_source']['span']==span)
        check(proposal['request']['raw_text']==raw and proposal['source']['context_ref']==body['context_ref'])
        check(proposal['parent_operation_id']==body['operation_id'] and proposal['parent_request_sha256']==result['request_sha256'])
        check(len(wire.calls)==2 and not wire.errors,canonical(wire.errors))
        check(any(x.get('result',{}).get('status')=='RECORDED_DRAFT' for x in wire.results),canonical(wire.results))
        check(not result['automatic_input_authorized'] and not result['changes_applied'])
        record=native.ws._read(native.path(body['operation_id']),authority=True);check(record['tool_attempts']==1)
        check(not native.ws._path('native-append').exists() and (folder/'材料.md').read_bytes()==before)
        try:
            for mutation in ('checksum', 'owner', 'generation'):
                changed=deepcopy(record);cohort=changed['ordinary_cohort']
                if mutation=='checksum':cohort['native_user_message_id']='changed-message'
                elif mutation=='owner':cohort['owner_ref']['id']='changed-owner'
                else:cohort['generation']+=1
                if mutation!='checksum':
                    cohort['cohort_sha256']=digest({key:value for key,value in cohort.items() if key!='cohort_sha256'})
                native.ws._write(native.path(body['operation_id']),changed)
                rejected=client.get(parent).json();readonly=rejected['native_action_proposals'][0]
                check(readonly['status']=='READONLY' and readonly['request'] is None and
                    readonly['reason_code']=='PROPOSAL_ADMISSION_CHANGED',canonical(rejected))
                check(rejected['text']==result['text'] and len(wire.calls)==2)
        finally:
            native.ws._write(native.path(body['operation_id']),record)
        replay=client.post(MESSAGES,json=body).json();check(replay['request_sha256']==result['request_sha256'] and len(wire.calls)==2)
        out=os.environ.get('EEAS_NATIVE_ACTION_PRODUCER')
        if out:
            p=Path(out);p.mkdir(parents=True,exist_ok=True);(p/'ordinary-original-producer.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    finally:
        wire.close()
