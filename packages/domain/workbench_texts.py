"""Versioned Word paragraph maps for quotation annotations, never executable docs."""
from io import BytesIO
import json
from zipfile import ZipFile
from lxml import etree as ET
from packages.domain.local_workspaces import digest,sha
from packages.domain.store import Conflict,InvalidInput

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class WorkbenchTexts:
    def __init__(self,reports,discovery):self.reports,self.discovery=reports,discovery

    def report(self,pid,source_id,artifact_id,source_kind='DISCOVERY'):
        raw,item=self.reports.download(pid,source_id,artifact_id,'docx',source_kind=source_kind)
        snapshot_bytes,_=self.reports.download(pid,source_id,artifact_id,'source_snapshot',source_kind=source_kind)
        snapshot=json.loads(snapshot_bytes)
        if source_kind=='DISCOVERY':view=snapshot['discovery']
        elif source_kind=='PROPOSAL':
            ref=snapshot['proposal']['payload'].get('discovery_ref')
            if not ref:raise InvalidInput('This Word report has no confirmed discovery context')
            view=self.discovery.get(pid,ref['id'],ref['revision'])
            if view['sha256']!=ref['sha256']:raise Conflict('Word report discovery context changed')
        else:raise InvalidInput('Unknown Word report source kind')
        with ZipFile(BytesIO(raw)) as archive:
            info=archive.getinfo('word/document.xml')
            if info.file_size>16*1024*1024:raise InvalidInput('Word text map exceeds the supported size')
            xml=archive.read(info)
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise InvalidInput('Word text map rejects XML document declarations')
            document=ET.fromstring(xml,parser=ET.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))
        paragraphs=[]
        for index,p in enumerate(document.iter(W+'p')):
            parts=[]
            for child in p.iter():
                if child.tag==W+'t':parts.append(child.text or '')
                elif child.tag==W+'tab':parts.append('\t')
                elif child.tag in {W+'br',W+'cr'}:parts.append('\n')
            text=''.join(parts)
            paragraphs.append({'index':index,'text':text,'sha256':sha(text.encode())})
        result={'source_id':source_id,'source_kind':source_kind,'artifact_id':artifact_id,'docx_sha256':item['sha256'],
            'artifact_ref':{'kind':'DISCOVERY','id':view['id'],'revision':view['revision'],'sha256':view['sha256']},
            'paragraphs':paragraphs,'offset_unit':'UNICODE_CODEPOINT','coverage':'WORD_MAIN_BODY_PARAGRAPHS_TABLES_NO_HEADERS_FOOTERS_FIELDS'}
        return {**result,'sha256':digest(result)}

    def quotation(self,pid,locator,view):
        result=self.report(pid,locator.source_id,locator.artifact_id,locator.source_kind)
        expected={'kind':'DISCOVERY','id':view['id'],'revision':view['revision'],'sha256':view['sha256']}
        if result['artifact_ref']!=expected or result['docx_sha256']!=locator.docx_sha256:
            raise Conflict('Word selection belongs to another artifact revision')
        if not 0<=locator.paragraph_index<len(result['paragraphs']):raise InvalidInput('Word paragraph is outside the artifact')
        text=result['paragraphs'][locator.paragraph_index]['text']
        if not 0<=locator.char_start<locator.char_end<=len(text):raise InvalidInput('Word selection is outside the exact paragraph')
        return text[locator.char_start:locator.char_end]
