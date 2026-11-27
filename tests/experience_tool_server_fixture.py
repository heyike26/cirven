"""AUTHORED_NOT_RUN: original stdlib JSON-RPC fixture for the official MCP client.

No model, shell, user files, network or capability writes. No automatic startup.
"""
import json
import sys


def reply(request):
    method=request.get('method');params=request.get('params',{})
    if method=='initialize':return {'protocolVersion':params['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'cirven-original-experience-fixture','version':'1'}}
    if method=='ping':return {}
    if method=='tools/list':return {'tools':[{'name':'echo','description':'Original deterministic UNIT response','inputSchema':{'type':'object','properties':{'text':{'type':'string'},'bad':{'type':'boolean'}},'additionalProperties':False}}]}
    if method=='tools/call':
        args=params.get('arguments',{});bad=args.get('bad') is True
        return {'content':[{'type':'text','text':'invalid' if bad else args['text']}],'isError':bad}
    raise ValueError('Unsupported fixture request')


if __name__=='__main__':
    for line in sys.stdin:
        request=json.loads(line)
        if 'id' not in request:continue
        try:value={'jsonrpc':'2.0','id':request['id'],'result':reply(request)}
        except Exception:value={'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'Unsupported original fixture method'}}
        sys.stdout.write(json.dumps(value,ensure_ascii=False)+'\n');sys.stdout.flush()
