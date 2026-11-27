"""Lossless UTF-8 context files bounded by the workspace text-edit contract."""
import hashlib
import json


def context_files(filename,text):
    def item(name,content):
        raw=content.encode('utf-8')
        return {'filename':name,'content':content,'sha256':hashlib.sha256(raw).hexdigest()}
    raw=text.encode('utf-8')
    if len(raw)<=800000:return [item(filename,text)]
    fragments=[item(filename+f'.part-{index:04d}',text[start:start+180000])
        for index,start in enumerate(range(0,len(text),180000),1)]
    index={'schema_version':'eeas.split-context.v1','original_filename':filename,
        'whole_sha256':hashlib.sha256(raw).hexdigest(),'whole_byte_length':len(raw),
        'encoding':'UTF-8','reconstruction':'CONCATENATE_PART_BYTES_IN_LIST_ORDER_THEN_VERIFY_WHOLE_SHA256',
        'parts':[{'filename':v['filename'],'sha256':v['sha256'],'byte_length':len(v['content'].encode('utf-8'))} for v in fragments]}
    if filename.endswith('.md'):
        listing=['# 大型需求背景索引','','完整正文按以下顺序分卷保存，没有截断。',
            '拼接各卷UTF-8字节后验证完整SHA256：'+index['whole_sha256'],'']
        listing+=['- '+p['filename']+' / '+p['sha256'] for p in index['parts']]
        main='\n'.join(listing)+'\n'
    else:main=json.dumps(index,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    return [item(filename,main),*fragments]
