"""Real DOCX rendering; existing proposal/answer decisions remain authoritative."""
from io import BytesIO
import base64
from copy import deepcopy
import hashlib
from importlib.metadata import version, PackageNotFoundError
import math
from pathlib import Path
import re
import zipfile

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Mm, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from docxtpl import DocxTemplate
from jinja2 import StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from packages.domain.diagram_import import _Archive, DiagramLimits, DiagramImportError
from packages.domain.local_workspaces import canonical
from packages.domain.store import InvalidInput
from packages.domain.chinese_word_typography import (
    GeneratedChineseTypography, builtin_chinese_fonts, mark_template_slots,
)

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
SLOTS = {'title', 'summary', 'status', 'proposal_ref', 'goals', 'constraints', 'risks',
         'assumptions', 'report_body', 'report_sections', 'pending_questions', 'source_receipt'}
STATUS = {'DRAFT': '待确认草稿（DRAFT）', 'CONFIRMED': '已确认计划（CONFIRMED）'}
PLAN_STATE = {'DRAFT':'待确认','NEEDS_INPUT':'等待补充','ACCEPTED_PLAN':'已接受计划','RESOLVED':'已明确决议'}
REVIEW = {'CURRENT':'来源版本一致','NEEDS_REVIEW':'来源已变化，需复核'}
ORIGIN = {'USER_STATEMENT':'用户陈述','MODEL_SUGGESTION':'模型建议','SOURCE_OUTLINE':'资料结构'}
KINDS = {'TASK':'任务','MILESTONE':'里程碑','DELIVERABLE':'交付物','DECISION':'决策'}
QUESTION_STATE = {'AWAITING_ANSWER':'待回答','ANSWERED_PENDING_ACCEPTANCE':'已记录回答，待接受','RESOLVED':'已有明确决议'}


def provenance():
    try:libraries={name:version(name) for name in ('python-docx','docxtpl','Jinja2','MarkupSafe','lxml','markdown-it-py','mdurl')}
    except PackageNotFoundError as exc:
        raise InvalidInput('The pinned offline Word capability is incomplete') from exc
    return {'layout': 'ORIGINAL_EEAS_A4_PLANNING_REPORT_V2',
            'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'block_renderer_sha256':hashlib.sha256(Path(__file__).with_name('planning_word_blocks.py').read_bytes()).hexdigest(),
            'diagram_renderer_sha256':hashlib.sha256(Path(__file__).with_name('planning_word_diagrams.py').read_bytes()).hexdigest(),
            'reading_projection_sha256':hashlib.sha256(Path(__file__).with_name('planning_word_projection.py').read_bytes()).hexdigest(),
            'typography_renderer_sha256':hashlib.sha256(Path(__file__).with_name('chinese_word_typography.py').read_bytes()).hexdigest(),
            'libraries': libraries,
            'sources': ['https://github.com/python-openxml/python-docx',
                       'https://github.com/elapouya/python-docx-template',
                       'https://github.com/github/spec-kit',
                       'https://github.com/executablebooks/markdown-it-py']}


def bounded_docx(raw,*,allow_reference_hyperlinks=False):
    """Inert OOXML only; never extract to disk, execute fields or fetch resources."""
    try:
        archive = _Archive(raw, DiagramLimits(max_archive_bytes=32*1024*1024,
            max_member_bytes=32*1024*1024, max_nodes=2000000))
        archive.require_xml('[Content_Types].xml', 'Types')
        archive.require_xml('word/document.xml', 'document')
        if archive.xml['word/document.xml'].tag!=qn('w:document'):
            raise InvalidInput('Only transitional WordprocessingML templates are supported')
        rels=archive.require_xml('_rels/.rels','Relationships')
        office=[e for e in rels if e.get('Type','').endswith('/officeDocument')]
        if len(office)!=1 or office[0].get('Target') not in {'word/document.xml','/word/document.xml'}:
            raise InvalidInput('Word package must identify its actual main document exactly once')
        for name in archive.data:
            if name.startswith(('word/embeddings/', 'word/activeX/')):
                raise InvalidInput('Embedded executable/OLE template content is unsupported')
        for root in archive.xml.values():
            for element in root.iter():
                content_type=element.get('ContentType','').lower()
                if 'macroenabled' in content_type or 'vbaproject' in content_type or element.get('Type','').lower().endswith('/vbaproject'):
                    raise InvalidInput('Macro-enabled templates are unsupported')
                if element.get('TargetMode') == 'External':
                    if not (allow_reference_hyperlinks and element.get('Type','').endswith('/hyperlink')
                            and element.get('Target','').startswith(('https://','http://','mailto:'))):
                        raise InvalidInput('External template relationships are unsupported')
                if element.tag.rsplit('}',1)[-1] in {'altChunk', 'object', 'OLEObject'}:
                    raise InvalidInput('Executable or alternate-format template content is unsupported')
            if not allow_reference_hyperlinks:
                for paragraph in (p for p in root.iter() if p.tag==qn('w:p')):
                    instructions=''.join(e.text or '' for e in paragraph.iter() if e.tag==qn('w:instrText'))
                    instructions+=' '.join(e.get(qn('w:instr'),'') for e in paragraph.iter() if e.tag==qn('w:fldSimple'))
                    if any(command in instructions.upper() for command in ('DDE','INCLUDETEXT','INCLUDEPICTURE','DATABASE','MACROBUTTON','LINK ')):
                        raise InvalidInput('Active/external Word field instructions are unsupported in rendering templates')
        return archive
    except (DiagramImportError, zipfile.BadZipFile, UnicodeError, ValueError) as exc:
        raise InvalidInput('DOCX is malformed or exceeds its inert OOXML bounds') from exc


def normalize_template(raw,*,allow_reference_hyperlinks=False):
    """Preserve the selected original; turn inert DOTX into document bytes in memory."""
    archive=bounded_docx(raw,allow_reference_hyperlinks=allow_reference_hyperlinks)
    types=archive.xml['[Content_Types].xml']
    main=[e for e in types if e.get('PartName')=='/word/document.xml']
    normal='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
    template='application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml'
    if len(main)!=1 or main[0].get('ContentType') not in {normal,template}:
        raise InvalidInput('Word main part must have an actual DOCX or macro-free DOTX content type')
    source_type='DOTX' if main[0].get('ContentType')==template else 'DOCX'
    normalized=raw
    if source_type=='DOTX':
        import xml.etree.ElementTree as ET
        main[0].set('ContentType',normal);out=BytesIO()
        with zipfile.ZipFile(out,'w') as target:
            for name,payload in archive.data.items():
                info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                target.writestr(info,ET.tostring(types,encoding='utf-8',xml_declaration=True) if name=='[Content_Types].xml' else payload)
        normalized=out.getvalue();bounded_docx(normalized,allow_reference_hyperlinks=allow_reference_hyperlinks)
    return normalized,{'source_type':source_type,'original_sha256':hashlib.sha256(raw).hexdigest(),
                       'normalized_sha256':hashlib.sha256(normalized).hexdigest()}


def template_info(raw):
    normalized,normalization=normalize_template(raw)
    archive = bounded_docx(normalized); found = set();structured_count=0
    for root in archive.xml.values():
        for element in root.iter():
            values=[*element.attrib.values(),element.tail or '']
            if element.tag!=qn('w:t'):values.append(element.text or '')
            if any(any(token in value for token in ('{{','}}','{%','{#')) for value in values):
                raise InvalidInput('Template code/tags outside paragraph text are unsupported')
        paragraphs = [p for p in root.iter() if p.tag.rsplit('}',1)[-1] == 'p']
        for paragraph in paragraphs:
            text = ''.join((e.text or '') for e in paragraph.iter() if e.tag.rsplit('}',1)[-1] == 't')
            if any(tag in text for tag in ('{%', '{#')):
                raise InvalidInput('Word templates support named text slots only, without code or control tags')
            for match in re.finditer(r'\{\{(.*?)\}\}', text, re.S):
                slot = match.group(1).strip()
                if slot not in SLOTS:
                    raise InvalidInput('Unknown Word template slot or expression: '+slot[:100])
                if slot=='report_sections':
                    if text.strip() not in {'{{ report_sections }}','{{report_sections}}'}:
                        raise InvalidInput('Structured report_sections slot must occupy its own paragraph')
                    structured_count+=1
                found.add(slot)
            if '{{' in re.sub(r'\{\{.*?\}\}', '', text, flags=re.S) or '}}' in re.sub(r'\{\{.*?\}\}', '', text, flags=re.S):
                raise InvalidInput('Word template contains an incomplete text slot')
    if structured_count>1:raise InvalidInput('Structured report_sections must appear exactly once')
    if 'report_sections' in found and 'report_body' in found:raise InvalidInput('Choose one complete report slot')
    return {'slots': sorted(found), 'mode': 'STRUCTURED_SECTIONS' if structured_count else ('NAMED_SLOTS' if found else 'APPEND_REPORT'),
            'complete_report_in_slot': bool({'report_body','report_sections'}&found),'normalization':normalization}


def _field(paragraph, text):
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), text)
    paragraph._p.append(field)


def _paragraph(document, text, style=None, *, small=False, keep_next=False):
    # Separate paragraphs keep long editable text and mixed line endings intact.
    lines=str(text).splitlines() or ['']
    compact=len(lines)<=8 and sum(map(len,lines))<=450
    for index,line in enumerate(lines):
        paragraph=document.add_paragraph(line, style=style)
        paragraph.paragraph_format.keep_with_next=keep_next and (compact or (index==len(lines)-1 and len(line)<=600))
        paragraph.paragraph_format.widow_control=True
        paragraph.paragraph_format.keep_together=len(line)<=600
        if small:
            for run in paragraph.runs:run.font.size=Pt(8.5)


def _summary(document, text, coverage=None, *, seed=None, source_key='candidate.summary'):
    from packages.domain.planning_word_blocks import write_markdown
    result=write_markdown(document,text,seed=seed,source_key=source_key)
    if coverage is not None:coverage.append(result)


def _block_slot(document, marker, write):
    matches=[p for p in document.element.body.iter(qn('w:p'))
             if ''.join(e.text or '' for e in p.iter(qn('w:t')))==marker]
    if len(matches)!=1:raise InvalidInput('Word block slot was not preserved exactly once')
    location=matches[0];parent=location.getparent();position=parent.index(location)
    before=len(document.element.body)-1;write(location)
    blocks=list(document.element.body)[before:-1]
    parent.remove(location)
    for offset,block in enumerate(blocks):parent.insert(position+offset,block)
    # A Word cell must end in a paragraph even when its final generated block is a table.
    if parent.tag==qn('w:tc') and (not len(parent) or parent[-1].tag!=qn('w:p')):
        parent.append(OxmlElement('w:p'))


def _template_body_slots(raw):
    """Only body slots own report content; a repeating header is not a body title."""
    archive=bounded_docx(raw);root=archive.xml['word/document.xml'];owned=set()
    parents={child:parent for parent in root.iter() for child in parent}
    for p in root.iter(qn('w:p')):
        text=''.join(e.text or '' for e in p.iter(qn('w:t')))
        slots=[match.group(1).strip() for match in re.finditer(r'\{\{(.*?)\}\}',text,re.S)]
        owned.update(slots)
        for slot in slots:
            if slot not in {'summary','report_body','report_sections'}:continue
            if len(slots)!=1 or text.strip() not in {'{{ '+slot+' }}','{{'+slot+'}}'}:
                raise InvalidInput('Word '+slot+' block slot must occupy its own paragraph')
            if parents[p].tag not in {qn('w:body'),qn('w:tc')}:
                raise InvalidInput('Word '+slot+' block slot must be in the document body or a table cell')
            if any(e.tag in {qn('w:sectPr'),qn('w:fldSimple'),qn('w:instrText'),qn('w:drawing'),
                             qn('w:commentRangeStart'),qn('w:commentRangeEnd'),qn('w:commentReference'),
                             qn('w:bookmarkStart'),qn('w:bookmarkEnd'),qn('w:ins'),qn('w:del')}
                   for e in p.iter()):
                raise InvalidInput('Word '+slot+' block slot contains a field, section, annotation or revision')
    return owned


def _bullet_number(document):
    numbering=document.part.numbering_part.element
    abstract_id=max([int(e.get(qn('w:abstractNumId'))) for e in numbering.findall(qn('w:abstractNum'))] or [-1])+1
    num_id=max([int(e.get(qn('w:numId'))) for e in numbering.findall(qn('w:num'))] or [0])+1
    abstract=OxmlElement('w:abstractNum');abstract.set(qn('w:abstractNumId'),str(abstract_id))
    multi=OxmlElement('w:multiLevelType');multi.set(qn('w:val'),'singleLevel');abstract.append(multi)
    level=OxmlElement('w:lvl');level.set(qn('w:ilvl'),'0')
    for name,value in [('start','1'),('numFmt','bullet'),('lvlText','\u2022'),('lvlJc','left')]:
        element=OxmlElement('w:'+name);element.set(qn('w:val'),value);level.append(element)
    properties=OxmlElement('w:pPr');indent=OxmlElement('w:ind')
    indent.set(qn('w:left'),'300');indent.set(qn('w:hanging'),'180');properties.append(indent);level.append(properties)
    rpr=OxmlElement('w:rPr');fonts=OxmlElement('w:rFonts')
    fonts.set(qn('w:ascii'),'DejaVu Sans');fonts.set(qn('w:hAnsi'),'DejaVu Sans');rpr.append(fonts);level.append(rpr)
    abstract.append(level)
    first_num=numbering.find(qn('w:num'))
    if first_num is None:numbering.append(abstract)
    else:numbering.insert(numbering.index(first_num),abstract)
    number=OxmlElement('w:num');number.set(qn('w:numId'),str(num_id))
    ref=OxmlElement('w:abstractNumId');ref.set(qn('w:val'),str(abstract_id));number.append(ref);numbering.append(number)
    return num_id


def _items(document, title, items, num_id):
    if not items:
        return
    document.add_heading(title, level=3).paragraph_format.keep_with_next=True
    lines=[line for item in items for line in (str(item).splitlines() or [''])]
    compact=len(lines)<=8 and sum(map(len,lines))<=450
    for index,line in enumerate(lines):
        paragraph=document.add_paragraph(line)
        paragraph.paragraph_format.space_after=Pt(3)
        paragraph.paragraph_format.widow_control=True
        paragraph.paragraph_format.keep_together=len(line)<=600
        paragraph.paragraph_format.keep_with_next=compact and index<len(lines)-1
        numbering=paragraph._p.get_or_add_pPr().get_or_add_numPr()
        numbering.get_or_add_ilvl().val=0;numbering.get_or_add_numId().val=num_id


def _question_heading(document,index,text):
    """Bound the navigation label; keep the complete question as editable prose."""
    label=' '.join(str(text).split())
    abbreviated=len(label)>24 or label!=text
    title=label[:24]+'…' if len(label)>24 else label
    document.add_heading(f'3.{index}  '+title,level=2)
    if abbreviated:
        _paragraph(document,text,keep_next=True)


def _task_reading_context(index,title):
    """A short business label plus the full document number survives page turns."""
    label=' '.join(str(title).split())
    short=label[:24]+'…' if len(label)>24 else label
    return f'2.{index} '+(short or '未命名任务')


def _anchors(document, values):
    for anchor in values:
        _paragraph(document, f'摘录来源：{anchor["document_id"]} · 修订{anchor["revision"]} · 片段{anchor["chunk_index"]} · 字符{anchor["char_start"]}–{anchor["char_end"]}',small=True)



def _confirmation_label(snapshot):
    discovery=snapshot.get('source_kind')=='DISCOVERY'
    primary=snapshot['discovery'] if discovery else snapshot['proposal']
    return ({'DRAFT':'待确认需求发现草稿（DRAFT）','CONFIRMED':'已确认需求发现（CONFIRMED）'} if discovery else STATUS)[primary['payload']['status']]


def _current_state_lines(snapshot):
    discovery=snapshot.get('source_kind')=='DISCOVERY'
    primary=snapshot['discovery'] if discovery else snapshot['proposal']
    label='需求发现' if discovery else '计划'
    review=primary['readiness']['review_status'] if discovery else primary['review_status']
    status=_confirmation_label(snapshot)
    lines=['本版状态：'+status,
           f'{label}版本 {primary["revision"]}  ·  '+REVIEW[review]]
    candidate=primary['payload']['candidate'];pending=snapshot['pending']
    if not discovery:
        nodes=candidate['nodes']
        lines.append(f'任务 {len(nodes)} 个；前置依赖 {sum(len(n["depends_on"]) for n in nodes)} 条。')
    lines.append(f'登记问题 {len(pending["questions"])} 项；当前未决 {pending["pending_count"]} 项。')
    lines.append('确认范围：本版确认记录与正文约束并列保留；正文另标待审的方案，不因确认标签或导出而自动获接受。')
    lines.append('计划或需求确认不代表工具权限、执行授权或独立验收已经通过。')
    return lines


def _front_state_lines(snapshot):
    """Keep the current caveat visible without pushing the user's title down."""
    lines=_current_state_lines(snapshot)
    return [lines[0]+' · '+lines[1],lines[-1]]


def _state_block(document, lines):
    """Keep a compact state group together, ending its chain at the caveat."""
    paragraphs=[]
    for index,line in enumerate(lines):
        _paragraph(document,line,keep_next=index<len(lines)-1)
        paragraphs.append(document.paragraphs[-1])
    return paragraphs


def _source_documents(snapshot):
    result=[];seen=set()
    for name in ('proposal','discovery'):
        primary=snapshot.get(name)
        if not primary:continue
        for row in primary.get('payload',{}).get('source_receipt',{}).get('documents',[]):
            key=(row['document_id'],row['revision'])
            if key not in seen:result.append(row);seen.add(key)
    return result


def _source_label(source):
    kind=source.get('source','')
    if kind.startswith('discovery-confirmed-baseline:'):return '已确认要求基线'
    if kind.startswith('discovery-user-statements:'):return '用户陈述记录'
    return source['filename']


def _readable_anchors(document,values,snapshot):
    sources={(row['document_id'],row['revision']):'资料 '+str(index)+' · '+_source_label(row)
             for index,row in enumerate(_source_documents(snapshot),1)}
    labels=[]
    for anchor in values:
        name=sources.get((anchor['document_id'],anchor['revision']))
        label=(name+' · 修订'+str(anchor['revision'])) if name else '原文摘录'
        if label not in labels:labels.append(label)
    if labels:_paragraph(document,'资料来源：'+'；'.join(labels)+'。精确定位见可追溯附录。',small=True)


def _original_summary_slot(document,text,coverage,seed):
    document.add_heading('生成时说明（原文；用户模板指定位置）',level=2)
    _paragraph(document,'本段保留产生时用语；当前确认与来源状态以报告前部的本版状态为准。')
    _summary(document,text,coverage,seed=seed)


def _historical_records(snapshot,requested_slots=frozenset()):
    """Account for exact historical fields without relabeling them as rendered.

    The report service writes this same immutable source snapshot atomically with
    the Word file. A user-selected template slot still owns its requested value.
    """
    primary_name='discovery' if snapshot.get('source_kind')=='DISCOVERY' else 'proposal'
    records=[]
    for name in ('proposal','discovery'):
        primary=snapshot.get(name)
        if not primary:continue
        candidate=primary['payload']['candidate']
        for field in ('title','summary'):
            # The primary business subject is always visible, either through a
            # selected title slot or through the generated subject paragraph.
            if field=='title' and name==primary_name:continue
            if field not in candidate or (name==primary_name and field in requested_slots):continue
            text=candidate[field]
            records.append({'source_key':name+'.candidate.'+field,
                'source_snapshot_pointer':'/'+name+'/payload/candidate/'+field,
                'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'byte_length':len(text.encode('utf-8')),'preserved_in':'source_snapshot'})
    return records


def _anchor_groups(snapshot):
    proposal=snapshot.get('proposal')
    if proposal:
        for node in proposal['payload']['candidate']['nodes']:
            yield '任务 '+node['id'],node.get('source_anchors',[])
    for question in snapshot.get('pending',{}).get('questions',[]):
        yield '问题 '+question['id'],question.get('source_anchors',[])
    discovery=snapshot.get('discovery')
    if discovery:
        candidate=discovery['payload']['candidate']
        for claim in candidate['claims']:
            yield '事项 '+claim['id'],claim.get('source_anchors',[])
        for competitor in candidate['competitors']:
            for index,observation in enumerate(competitor['observations'],1):
                yield '替代方案 '+competitor['name']+' · 观测'+str(index),observation.get('source_anchors',[])
    for index,diagram in enumerate(snapshot.get('diagrams',[]),1):
        graph=diagram['graph'];labels={node['id']:node['label'] for node in graph['nodes']}
        for node in graph['nodes']:
            yield f'图 {index} · '+node['label'],node.get('source_anchors',[])
        for edge in graph['edges']:
            yield f'图 {index} · '+labels[edge['source']]+' → '+labels[edge['target']],edge.get('source_anchors',[])


def _write_trace_appendix(document,snapshot,*,diagram_audit=None):
    first=len(document.paragraphs)
    document.add_heading('可追溯附录',level=1)
    document.add_heading('本版状态与确认范围',level=2)
    _state_block(document,_current_state_lines(snapshot))
    rows=[]
    for name,label in (('proposal','计划'),('discovery','需求发现')):
        primary=snapshot.get(name)
        if primary and 'revision'in primary:rows.append([label+'版本','第'+str(primary['revision'])+'版'])
    sources=_source_documents(snapshot)
    source_names={(row['document_id'],row['revision']):'资料 '+str(index)+' · '+_source_label(row)
                  for index,row in enumerate(sources,1)}
    for index,source in enumerate(sources,1):
        rows.append(['资料 '+str(index),_source_label(source)+' · 原件修订 '+str(source['revision'])])
    if snapshot.get('template_ref'):
        ref=snapshot['template_ref'];rows.append(['用户模板',ref['path']+' · 第'+str(ref['revision'])+'版'])
    for index,diagram in enumerate(snapshot.get('diagrams',[]),1):
        ref=diagram.get('session_ref')
        source='需求发现第'+str(ref['revision'])+'版' if ref and 'revision'in ref else '来源时点未登记'
        rows.append(['图 '+str(index)+'冻结来源',source])
    if rows:_table(document,['记录','阅读引用'],rows,[.28,.72])
    projected={row['figure_number'] for row in (diagram_audit or [])
               if row.get('projection')=='CIRVEN_WORD_READING_PROJECTION_V1'}
    for index,diagram in enumerate(snapshot.get('diagrams',[]),1):
        if index not in projected:continue
        graph=diagram['graph']
        document.add_heading(f'图 {index} 说明 · '+graph['title'],level=2)
        for node in graph['nodes']:
            document.add_heading(node['label'],level=3)
            _paragraph(document,node['description'])
            if node['claim_ids']:_paragraph(document,'需求依据：'+'、'.join(node['claim_ids']),small=True)
    groups={}
    for label,values in _anchor_groups(snapshot):
        for anchor in values:
            key=canonical(anchor)
            entry=groups.setdefault(key,{'anchor':anchor,'owners':[]})
            if label not in entry['owners']:entry['owners'].append(label)
    for index,entry in enumerate(groups.values(),1):
        document.add_heading('摘录 '+str(index)+' · 原文定位',level=2)
        _paragraph(document,'引用位置：'+'；'.join(entry['owners']),small=True,keep_next=True)
        anchor=entry['anchor']
        name=source_names.get((anchor['document_id'],anchor['revision']),'未列入本版资料表的来源')
        _paragraph(document,name+' · 修订 '+str(anchor['revision'])+' · 片段 '+str(anchor['chunk_index'])+
                   ' · 字符 '+str(anchor['char_start'])+'–'+str(anchor['char_end']),small=True)
    _paragraph(document,'生成时说明、其他来源的历史题名及精确版本校验记录完整保存在随报告交付的“生成来源快照”中；可按需打开核对。用户模板指定的原文仍按所选位置保留。',small=True)
    # Repeated trace notes need less separation than the reading body. Set only
    # their spacing, leaving template styles, line height and keep chains intact.
    for paragraph in document.paragraphs[first+1:]:
        if not paragraph.text:continue
        level=next((level for level in (2,3)
                    if paragraph.style==document.styles['Heading '+str(level)]),None)
        paragraph.paragraph_format.space_before=Pt({2:8,3:6}.get(level,0))
        paragraph.paragraph_format.space_after=Pt(2 if level==3 else 3)


def _frozen_diagram_note(diagram):
    ref=diagram.get('session_ref')
    source='需求发现第'+str(ref['revision'])+'版' if ref and 'revision'in ref else '所附原图快照（来源时点未登记）'
    return '冻结来源：'+source+'。图中文字保留生成时状态；本版当前确认与来源状态见报告前部。图示不代表执行授权或独立验收结果。'



def _diagram_detail_locations(diagram,plan):
    """Index complete frozen objects, including duplicates across viewports."""
    windows=plan.get('windows',[])
    geometry=diagram.get('render_geometry')
    if not windows or not geometry:return None
    boxes=geometry.get('logical_content_boxes',geometry['content_boxes'])
    result={}
    for box in boxes:
        pages=[]
        for index,window in enumerate(windows,1):
            left,top,right,bottom=window['rect']
            if (left<=box['x'] and top<=box['y'] and
                    right>=box['x']+box['width'] and bottom>=box['y']+box['height']):
                pages.append(index)
        result[(box['kind'],box['id'])]='、'.join(map(str,pages)) or '未核完整位置'
    return result


def _table(document,headings,rows,proportions):
    table=document.add_table(rows=1,cols=len(headings));table.autofit=False;table.alignment=WD_TABLE_ALIGNMENT.CENTER
    if 'Table Grid' in document.styles:table.style='Table Grid'
    section=document.sections[-1];available=section.page_width-section.left_margin-section.right_margin
    widths=[int(available*fraction) for fraction in proportions[:-1]];widths.append(available-sum(widths))
    table._tbl.tblPr.find(qn('w:tblW')).set(qn('w:type'),'dxa')
    table._tbl.tblPr.find(qn('w:tblW')).set(qn('w:w'),str(round(available/635)))
    for column,width in zip(table.columns,widths):column.width=Emu(width)
    for cells,values in [(table.rows[0].cells,headings),*[(table.add_row().cells,row) for row in rows]]:
        for i,(cell,value) in enumerate(zip(cells,values)):
            cell.width=Emu(widths[i]);cell.text=str(value)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after=Pt(3)
                paragraph.paragraph_format.keep_with_next=False
                paragraph.paragraph_format.keep_together=False
                paragraph.paragraph_format.widow_control=True
                for run in paragraph.runs:run.font.size=Pt(9.5)
    for cell in table.rows[0].cells:
        shade=OxmlElement('w:shd');shade.set(qn('w:val'),'clear');shade.set(qn('w:fill'),'EEF2F6');cell._tc.get_or_add_tcPr().append(shade)
        for paragraph in cell.paragraphs:paragraph.paragraph_format.keep_with_next=True
        for run in cell.paragraphs[0].runs:run.bold=True
    repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
    # Keep each record together rather than chaining all table paragraphs.
    # Word can still paginate a row whose own content exceeds a whole page.
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
    # Word merges directly adjacent tables and may reuse the first header.
    # A real, compact paragraph keeps generated tables structurally separate.
    separator=document.add_paragraph()
    separator.paragraph_format.line_spacing=Pt(1)
    separator.paragraph_format.space_before=Pt(0)
    separator.paragraph_format.space_after=Pt(4)
    separator.paragraph_format.keep_with_next=False


def _layout(document):
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(22), Mm(20)
    section.left_margin = section.right_margin = Mm(23)
    normal = document.styles['Normal']; normal.font.name = 'Times New Roman'
    normal.font.size = Pt(10.5); normal.font.color.rgb = RGBColor.from_string('263445')
    normal.paragraph_format.space_after = Pt(6); normal.paragraph_format.line_spacing = 1.2
    for name, size in [('Title',26),('Heading 1',17),('Heading 2',13),('Heading 3',11)]:
        style = document.styles[name]; style.font.name = 'Times New Roman'; style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string('203A53')
        style.paragraph_format.keep_with_next = True
    builtin_chinese_fonts(document)
    header = section.header.paragraphs[0]; header.text = 'Cirven  /  项目规划报告'
    header.style = document.styles['Caption']
    footer = section.footer.paragraphs[0]; footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('项目规划报告     '); _field(footer, 'PAGE'); footer.add_run(' / '); _field(footer, 'NUMPAGES')


def _write_report(document, snapshot, *, built_in=False, owned=frozenset(), coverage=None, diagram_audit=None):
    if snapshot.get('source_kind')=='DISCOVERY':
        return _write_discovery_report(document,snapshot,built_in=built_in,owned=owned,coverage=coverage,diagram_audit=diagram_audit)
    start_index=len(document._element.body)-1
    proposal = snapshot['proposal']; payload = proposal['payload']; candidate = payload['candidate']
    pending = snapshot['pending']
    if built_in:
        _layout(document)
    num_id=_bullet_number(document)
    if 'title' not in owned:
        document.add_heading('项目任务书', 0)
        _paragraph(document,'主题（题名原文）：'+candidate['title'],keep_next=True)
    _state_block(document,_front_state_lines(snapshot))
    _paragraph(document, '本报告保留当前任务方案、问题、已有回答与明确决议。草稿中的建议和假设尚待用户确认；已确认计划也不代表工具权限或执行已获批准。')
    document.add_heading('1  目标与工作边界', level=1)
    
    for label, key in [('目标','goals'),('约束','constraints'),('风险','risks'),('待核假设','assumptions')]:
        if key not in owned:_items(document, label, candidate.get(key, []),num_id)
    document.add_heading('2  任务网络与分解', level=1)
    _paragraph(document, f'任务节点 {len(candidate["nodes"])} 个；问题 {len(candidate["questions"])} 项。以下完整保留节点与依赖，执行状态均以原系统记录为准。')
    states = {n['id']: n.get('planning_status','DRAFT') for n in proposal['flow']['nodes']}
    for index, node in enumerate(candidate['nodes'],1):
        context=_task_reading_context(index,node['title'])
        document.add_heading(f'2.{index}  {node["title"]}', level=2).paragraph_format.keep_with_next=True
        _paragraph(document, KINDS[node['kind']]+' · '+PLAN_STATE[states.get(node['id'],'DRAFT')],keep_next=True)
        _paragraph(document, node['description'])
        if node.get('parent_id'): _paragraph(document, '所属节点 · '+context+'：'+node['parent_id'])
        _paragraph(document, '前置依赖 · '+context+'：'+(', '.join(node['depends_on']) or '无已登记依赖'))
        for label,key in [('输入','inputs'),('交付物','outputs'),('验收条件','acceptance'),('所需能力','capabilities'),('节点风险','risks')]:
            _items(document, label+' · '+context, node.get(key, []),num_id)
        _paragraph(document, '内容依据：'+ORIGIN[node['origin']]+' · 节点编号 '+node['id'],small=True)
        _readable_anchors(document,node['source_anchors'],snapshot)
    document.add_heading('3  待确认事项与已有决议', level=1)
    _paragraph(document, f'当前未决事项 {pending["pending_count"]} 项。已记录回答只有在明确决议接受后才视为解决；页面仍可继续回答和补充资料。')
    if not pending['questions']:
        _paragraph(document, '本版本没有登记问题；这不代表资料完整性已经独立验证。')
    for index, question in enumerate(pending['questions'],1):
        _question_heading(document,index,question['text'])
        _paragraph(document, f'状态 · 3.{index}：'+QUESTION_STATE[question['status']])
        _paragraph(document, '提出原因：'+question['reason'])
        _paragraph(document, '影响节点：'+(', '.join(question['affects_node_ids']) or '整个计划'))
        _paragraph(document, '是否影响开始：'+('是' if question['blocking'] else '否'))
        _items(document,f'请求补充资料 · 3.{index}',question['requested_materials'],num_id)
        _items(document,f'已有回答 · 3.{index}',[a['text']+'（已记录，接受状态见明确决议）' for a in question['answers']],num_id)
        resolution=question['resolution']
        _paragraph(document,'明确决议：'+(resolution['reason'] if resolution else '尚未接受答案或假设。'))
        if resolution:
            _paragraph(document,'接受方式：'+{'ANSWER_ACCEPTED':'接受回答','ASSUMPTION_ACCEPTED':'接受假设'}.get(resolution['mode'],resolution['mode']))
            if resolution.get('answer_text'):_paragraph(document,'已接受回答：'+resolution['answer_text'])
            if resolution.get('assumption'):_paragraph(document,'已接受假设：'+resolution['assumption'])
        _paragraph(document,'问题编号 '+question['id'],small=True)
        _readable_anchors(document,question['source_anchors'],snapshot)
    document.add_heading('4  资料来源与可追溯记录', level=1)
    coverage_by_id={row['document_id']:row for row in payload['source_receipt']['coverage']}
    rows=[]
    for source in payload['source_receipt']['documents']:
        source_coverage=coverage_by_id[source['document_id']]
        selected=source.get('local_file_source_ref')
        rows.append([_source_label(source),'第'+str(source['revision'])+'版',selected['path'] if selected else '项目资料原件',
            '完整已解析文本' if source_coverage['text_coverage']=='FULL_PARSED_TEXT' else '用户所选片段'])
    _table(document,['资料文件','原件修订','相对路径','文本覆盖'],rows,[.27,.14,.32,.27])
    _paragraph(document,'来源记录保留原件与解析内容的精确版本。图片、对象和原始版式的覆盖尚未独立评估。')
    for source in payload['source_receipt']['documents']:
        source_coverage=coverage_by_id[source['document_id']]
        if source_coverage['omitted_chunk_indexes']:_paragraph(document,_source_label(source)+'未纳入片段：'+', '.join(str(i) for i in source_coverage['omitted_chunk_indexes']))
        for warning in source_coverage['warnings']:_paragraph(document,_source_label(source)+'解析记录：'+str(warning))
    _apply_profile(document,snapshot,start_index)
    _write_supplement(document,snapshot,coverage=coverage,diagram_audit=diagram_audit)
    _write_trace_appendix(document,snapshot,diagram_audit=diagram_audit)


def _write_discovery_report(document,snapshot,*,built_in=False,owned=frozenset(),coverage=None,diagram_audit=None):
    session=snapshot['discovery'];payload=session['payload'];pending=snapshot['pending']
    start=len(document._element.body)-1
    if built_in:_layout(document)
    if 'title' not in owned:
        document.add_heading('需求发现报告',0)
        _paragraph(document,'主题（题名原文）：'+payload['candidate']['title'],keep_next=True)
    _state_block(document,_front_state_lines(snapshot))
    document.add_heading('1  目标与工作边界',level=1)
    _paragraph(document,'本报告记录当前发现阶段、证据、问题与决议；未确认内容仍可继续回答和修改。')
    document.add_heading('2  任务网络与分解',level=1)
    _paragraph(document,'当前导出的是需求发现阶段快照。任务网络以另行形成的真实计划为准；本报告不虚构计划节点或执行结果。')
    document.add_heading('3  待确认事项与已有决议',level=1)
    _paragraph(document,f"尚待确认 {pending['pending_count']} 项；已记录回答在明确接受前保持未决。")
    for index,q in enumerate(pending['questions'],1):
        _question_heading(document,index,q['text']);_paragraph(document,f'状态 · 3.{index}：'+QUESTION_STATE[q['status']])
        _paragraph(document,'原因：'+q['reason']);_paragraph(document,'影响要求：'+(', '.join(q['affects_claim_ids']) or '整个发现过程'))
        _paragraph(document,f'请求资料 · 3.{index}：'+'；'.join(q['requested_materials']))
        for answer in q['answers']:_paragraph(document,f'已有回答（未接受） · 3.{index}：'+answer['text'])
        _readable_anchors(document,q['source_anchors'],snapshot)
    if not pending['questions']:_paragraph(document,'本版本没有未决问题；不代表需求完整性或工程可行性已经验证。')
    for resolution in payload['resolutions']:
        _paragraph(document,'明确决议：'+resolution['question_id']+' · '+resolution['reason'])
        if resolution.get('assumption'):_paragraph(document,'已接受假设：'+resolution['assumption'])
        for answer in session['answers']:
            if answer['id']==resolution.get('answer_id'):_paragraph(document,'已接受回答：'+answer['text'])
    document.add_heading('4  资料来源与可追溯记录',level=1)
    receipt=payload['source_receipt']
    _table(document,['资料','修订'],[[_source_label(s),s['revision']] for s in receipt['documents']],[.75,.25])
    _paragraph(document,'来源的精确版本与摘录定位见可追溯附录。',small=True)
    _apply_profile(document,snapshot,start);_write_supplement(document,snapshot,coverage=coverage,diagram_audit=diagram_audit)
    _write_trace_appendix(document,snapshot,diagram_audit=diagram_audit)


def _apply_profile(document,snapshot,start_index):
    profile=snapshot.get('experience')
    if not profile:return
    mapping={r['section_key']:r for r in profile['approved_rules'] if r.get('section_key')}
    labels={'OVERVIEW':'1  目标与工作边界','TASKS':'2  任务网络与分解',
            'QUESTIONS':'3  待确认事项与已有决议','SOURCES':'4  资料来源与可追溯记录'}
    body=document._element.body;blocks=list(body)[start_index:-1]
    groups={};prefix=[];current=None
    for block in blocks:
        text=''.join(e.text or '' for e in block.iter() if e.tag==qn('w:t'))
        style=block.find(qn('w:pPr'));style=style.find(qn('w:pStyle'))if style is not None else None
        is_section=style is not None and style.get(qn('w:val'))==document.styles['Heading 1'].style_id
        key=next((key for key,label in labels.items() if text==label and is_section),None)
        if key:current=key;groups[current]=[]
        if current:groups[current].append(block)
        else:prefix.append(block)
    for key,rule in mapping.items():
        if key not in groups:continue
        heading=Paragraph(groups[key][0],document);heading.text=rule['heading']
        if rule.get('style_id'):
            style=next((s for s in document.styles if s.style_id==rule['style_id']),None)
            if style is not None:heading.style=style
    if mapping:
        order=[r['section_key'] for r in profile['approved_rules'] if r.get('section_key')]
        order+=[key for key in labels if key not in order]
        for block in blocks:body.remove(block)
        for block in [*prefix,*[block for key in order for block in groups.get(key,[])]]:
            body.insert(len(body)-1,block)


def _write_supplement(document,snapshot,*,coverage=None,diagram_audit=None):
    accepted=snapshot.get('requirement_coverage')
    if accepted is not None:
        document.add_heading('已确认事项承接',level=1)
        links={r['claim_id']:r for r in accepted['claim_links']}
        excluded={r['claim_id']:r for r in accepted['user_exclusions']}
        rows=[]
        for row in accepted['accepted_claims']:
            claim=row['claim'];link=links.get(claim['id'])
            target='；'.join(t['node_id'] if t['kind']=='TASK' else '全任务共享约束 '+str(t['constraint_index']) for t in link['targets']) if link else ''
            if claim['id'] in excluded:target='本次用户排除：'+excluded[claim['id']]['reason']
            rows.append([claim['id'],claim['text'],target or '尚未承接'])
        _table(document,['事项编号','已确认内容','承接位置或本次排除理由'],rows,[.15,.5,.35])
    discovery=snapshot.get('discovery')
    if discovery:
        candidate=discovery['payload']['candidate']
        document.add_heading('需求发现过程与竞品依据',level=1)
        _paragraph(document,'发现阶段：'+{'DISCOVER':'发现问题','DEFINE':'明确问题','RESEARCH':'调查验证','SPECIFY':'完善需求','PLAN':'形成计划'}[candidate['phase']])
        _paragraph(document,'本节保留发现阶段事项与观测原文；当前确认状态见报告前部，产生时说明见配套的“生成来源快照”。')
        categories={'PROBLEM':'问题','NEED':'需要','REQUIREMENT':'需求','CONSTRAINT':'约束','RISK':'风险','ASSUMPTION':'假设','DECISION':'决策'}
        for claim in candidate['claims']:
            context=categories[claim['category']]+' · '+claim['id']
            document.add_heading(context,level=2)
            _paragraph(document,claim['text'])
            _paragraph(document,'依据类型 · '+context+'：'+{'SOURCE_FACT':'资料事实','USER_STATEMENT':'用户陈述','MODEL_SUGGESTION':'模型建议'}[claim['origin']])
            if claim['acceptance']:_items(document,'验收条件 · '+context,claim['acceptance'],_bullet_number(document))
            _readable_anchors(document,claim['source_anchors'],snapshot)
        status={'OFFICIAL_CLAIM':'官方声明','USER_TESTED':'用户实际测试','INFERENCE':'推断','UNKNOWN':'尚不明确'}
        for competitor in candidate['competitors']:
            document.add_heading('替代方案：'+competitor['name'],level=2)
            _paragraph(document,competitor['summary'],keep_next=True)
            _table(document,['比较维度','观测','证据状态'],[[o['dimension'],o['text'],status[o['status']]] for o in competitor['observations']],[.2,.6,.2])
            for observation in competitor['observations']:_readable_anchors(document,observation['source_anchors'],snapshot)
        for action in candidate['next_actions']:
            _paragraph(document,'生成时建议（原文）：'+action['action']+'；原因：'+action['reason']+'；验证方法：'+action['method']+'；预期产物：'+action['expected_artifact'])
    for diagram_number,diagram in enumerate(snapshot.get('diagrams',[]),1):
        graph=diagram['graph']
        raw=base64.b64decode(diagram['png_base64'])
        if hashlib.sha256(raw).hexdigest()!=diagram['png_sha256']:raise InvalidInput('Frozen diagram PNG hash mismatch')
        if hashlib.sha256(canonical(graph).encode()).hexdigest()!=diagram.get('model_sha256'):
            raise InvalidInput('Frozen diagram graph hash mismatch')
        from packages.domain.document_import import image_metadata
        info=image_metadata(raw)
        from packages.domain.planning_word_projection import build_reading_projection
        section=document.sections[-1]
        width=(section.page_width-section.left_margin-section.right_margin)/12700
        heading_text=f'图 {diagram_number}  '+graph['title']
        heading_size=document.styles['Heading 1'].font.size
        heading_pt=heading_size.pt if heading_size is not None else 17
        # Reserve the title, source note and caption on the same page. The
        # two-line base includes paragraph spacing; longer titles get more area.
        heading_lines=max(1,math.ceil(len(heading_text)*heading_pt/width))
        reserve=120+max(0,heading_lines-2)*heading_pt*1.3
        height=(section.page_height-section.top_margin-section.bottom_margin)/12700-reserve
        if height<=0:raise InvalidInput('Word page has no available diagram area after its heading')
        projection=build_reading_projection(diagram,width,height)
        if projection.get('reason')=='SOURCE_GRAPH_IDENTITY_MISMATCH':
            raise InvalidInput('Frozen diagram graph hash mismatch')
        heading=document.add_heading(heading_text,level=1)
        if projection['status']=='GEOMETRY_CHECKED_NATIVE_WORD_NOT_RUN':
            heading.paragraph_format.page_break_before=True
            _paragraph(document,_frozen_diagram_note(diagram),small=True,keep_next=True)
            document.add_picture(BytesIO(projection['png_bytes']),
                width=Emu(round(projection['physical_width_pt']*12700)),
                height=Emu(round(projection['physical_height_pt']*12700)))
            image_paragraph=document.paragraphs[-1]
            image_paragraph.paragraph_format.line_spacing=1.0
            image_paragraph.paragraph_format.space_before=Pt(0)
            image_paragraph.paragraph_format.space_after=Pt(4)
            image_paragraph.paragraph_format.keep_together=True
            image_paragraph.paragraph_format.keep_with_next=True
            _paragraph(document,projection['audit']['caption_required'],small=True)
            if diagram_audit is not None:
                diagram_audit.append({**projection['audit'],'figure_number':diagram_number,
                    'status':projection['status'],'windows':[],
                    'complete_item_count':len(graph['nodes'])+len(graph['edges'])})
            continue
        _paragraph(document,{'ARCHITECTURE':'架构图','FLOW':'流程图','MINDMAP':'思维图'}[graph['kind']]+'；'+_frozen_diagram_note(diagram),keep_next=True)
        from packages.domain.planning_word_diagrams import append_diagram
        plan=append_diagram(document,diagram,raw,info,audit=diagram_audit,diagram_number=diagram_number)
        if diagram_audit is not None:
            diagram_audit[-1]['reading_projection_fallback']={k:v for k,v in projection.items() if k!='png_bytes'}
        locations=_diagram_detail_locations(diagram,plan)
        nodes=[[n['id'],n['label']+'\n'+n['description'],', '.join(n['claim_ids'])] for n in graph['nodes']]
        edges=[[e['id'],e['source']+' → '+e['target'],e['relation']] for e in graph['edges']]
        if locations is not None:
            _paragraph(document,'详图为局部视窗；线段触及边界不表示关系结束。下表按完整对象的实际位置列出详图号；关系文字与两端节点可分处不同详图。')
            for row,node in zip(nodes,graph['nodes']):row.append(locations[('NODE',node['id'])])
            for row,edge in zip(edges,graph['edges']):
                row.append('文字：'+locations[('EDGE_LABEL',edge['id'])]+'；起点：'+locations[('NODE',edge['source'])]+'；终点：'+locations[('NODE',edge['target'])])
            _table(document,['节点编号','名称与说明','需求依据','完整详图号'],nodes,[.13,.50,.17,.20])
            _table(document,['关系编号','起点 → 终点','关系','完整对象详图号'],edges,[.13,.27,.40,.20])
        else:
            _table(document,['节点编号','名称与说明','需求依据'],nodes,[.15,.65,.2])
            _table(document,['关系编号','起点 → 终点','关系'],edges,[.15,.35,.5])
    if snapshot.get('experience'):
        document.add_heading('已审阅的写作经验',level=1)
        _paragraph(document,'本节经验只用于结构和表达，不作为当前项目事实。')
        for instruction in snapshot['experience']['instructions']:_paragraph(document,instruction)


def _mapped_template_sections(document,snapshot,*,coverage=None,diagram_audit=None):
    """Replace only the explicitly reviewed chapter ranges of the same original."""
    profile=snapshot.get('experience');template_ref=snapshot.get('template_ref')
    if not profile or not template_ref:return False
    rules=[r for r in profile['approved_rules'] if r.get('section_key')]
    if {r['section_key'] for r in rules}!={'OVERVIEW','TASKS','QUESTIONS','SOURCES'}:return False
    paragraphs=list(document.element.iter(qn('w:p')));targets=[]
    body=document.element.body;original=list(body)[:-1]
    for rule in rules:
        anchors=[a for a in rule['source_anchors'] if a['part']=='word/document.xml' and
            a['source_index']<len(profile['source_refs']) and profile['source_refs'][a['source_index']]==template_ref]
        if len(anchors)!=1:return False
        match=re.fullmatch(r'/paragraph\[(\d+)\]',anchors[0]['path'])
        if match is None:return False
        index=int(match[1])-1
        if index>=len(paragraphs):raise InvalidInput('Approved template chapter anchor no longer exists')
        target=paragraphs[index]
        text=''.join(e.text or '' for e in target.iter(qn('w:t')))
        if hashlib.sha256(text.encode()).hexdigest()!=anchors[0]['quote_sha256']:
            raise InvalidInput('Approved template chapter text hash mismatch')
        if target.getparent() is not body:raise InvalidInput('Mapped template chapters must be top-level paragraphs; use report_sections inside a table')
        targets.append((original.index(target),target,rule))
    if len({position for position,_,_ in targets})!=4:raise InvalidInput('Each mapped template chapter must have its own source paragraph')
    before=len(body)-1
    # Form the real structured content once; source text and facts remain from this snapshot.
    _write_report(document,{**snapshot,'experience':None},coverage=coverage,diagram_audit=diagram_audit)
    generated=list(body)[before:-1];groups={};prefix=[];current=None
    names={'1  目标与工作边界':'OVERVIEW','2  任务网络与分解':'TASKS','3  待确认事项与已有决议':'QUESTIONS','4  资料来源与可追溯记录':'SOURCES'}
    for block in generated:
        text=''.join(e.text or '' for e in block.iter() if e.tag==qn('w:t'))
        style=block.find(qn('w:pPr'));style=style.find(qn('w:pStyle'))if style is not None else None
        if text in names and style is not None and style.get(qn('w:val'))==document.styles['Heading 1'].style_id:
            current=names[text];groups[current]=[]
        if current:groups[current].append(block)
        else:prefix.append(block)
    for block in generated:body.remove(block)
    mapped_styles={rule.get('style_id') for _,_,rule in targets if rule.get('style_id')}
    ordered=sorted(targets,key=lambda row:row[0])
    for position,target,rule in reversed(ordered):
        end=len(original)
        for i in range(position+1,len(original)):
            block=original[i]
            if block.tag==qn('w:p'):
                style=block.find(qn('w:pPr'))
                style=style.find(qn('w:pStyle')) if style is not None else None
                if style is not None and style.get(qn('w:val')) in mapped_styles:end=i;break
        insertion=body.index(target);properties=target.find(qn('w:pPr'))
        for old in original[position:end]:
            if old.getparent() is body:body.remove(old)
        section=groups[rule['section_key']]
        heading=Paragraph(section[0],document);heading.text=rule['heading']
        if properties is not None:
            existing=section[0].find(qn('w:pPr'))
            if existing is not None:section[0].remove(existing)
            section[0].insert(0,deepcopy(properties))
        for offset,block in enumerate(section):body.insert(insertion+offset,block)
    first=min(body.index(groups[r['section_key']][0]) for _,_,r in targets)
    for offset,block in enumerate(prefix):body.insert(first+offset,block)
    document.add_heading('已审阅的写作经验',level=1)
    _paragraph(document,'经验只用于结构与表达；原报告中的具体项目事实未转入当前正文。')
    for instruction in profile['instructions']:_paragraph(document,instruction)
    return True


def report_text(snapshot):
    doc = Document(); _write_report(doc,snapshot)
    lines=[]
    for block in doc.iter_inner_content():
        if isinstance(block,Paragraph):lines.append(block.text)
        elif isinstance(block,Table):lines.extend(' | '.join(cell.text for cell in row.cells) for row in block.rows)
    return '\n'.join(lines)


def render_docx(snapshot, template_raw=None):
    template_mode = 'BUILT_IN'
    normalization=None;coverage=[];owned=set();requested_slots=set();diagram_audit=[]
    primary=snapshot['discovery'] if snapshot.get('source_kind')=='DISCOVERY' else snapshot['proposal']
    if template_raw is None:
        document = Document(); typography=GeneratedChineseTypography(document)
        _write_report(document,snapshot,built_in=True,coverage=coverage,diagram_audit=diagram_audit)
    else:
        info = template_info(template_raw); template_mode = info['mode'];requested_slots=set(info['slots'])
        template_raw,normalization=normalize_template(template_raw)
        owned=_template_body_slots(template_raw)
        if ({'summary','report_body','report_sections'}&set(info['slots']))-owned:
            raise InvalidInput('Word structured content slots are supported only in the main document body')
        candidate = primary['payload']['candidate']
        if info['slots']:
            context = {key:'\n'.join(candidate.get(key,[])) for key in ('goals','constraints','risks','assumptions')}
            marker_hash=hashlib.sha256(canonical(snapshot).encode()).hexdigest()
            context.update(title=candidate['title'],summary='EEAS_WORD_SUMMARY_'+marker_hash,
                status=_confirmation_label(snapshot),
                proposal_ref=canonical(snapshot['pending'].get('proposal_ref') or snapshot['pending'].get('session_ref')),report_body='EEAS_WORD_BODY_'+marker_hash,
                pending_questions=snapshot['pending_markdown'],source_receipt=canonical(primary['payload']['source_receipt']),
                report_sections='EEAS_STRUCTURED_REPORT_'+hashlib.sha256(canonical(snapshot).encode()).hexdigest())
            environment = ImmutableSandboxedEnvironment(undefined=StrictUndefined,autoescape=True)
            environment.globals.clear()
            template = DocxTemplate(BytesIO(template_raw)); mark_template_slots(template.get_docx())
            template.render(context,jinja_env=environment,autoescape=True)
            out=BytesIO(); template.save(out); document=Document(BytesIO(out.getvalue()))
            typography=GeneratedChineseTypography(document,template=True,slot_markers=True)
            if 'summary' in owned:
                _block_slot(document,context['summary'],lambda seed:_original_summary_slot(document,candidate['summary'],coverage,seed))
            for slot in ('report_sections','report_body'):
                if slot in owned:
                    def write_report_slot(seed):
                        if seed.getparent().tag!=qn('w:body') and snapshot.get('diagrams'):
                            raise InvalidInput('A Word report block containing diagrams must be a document-body paragraph')
                        _write_report(document,snapshot,owned=owned,coverage=coverage,diagram_audit=diagram_audit)
                    _block_slot(document,context[slot],write_report_slot)
        else:
            document=Document(BytesIO(template_raw))
            typography=GeneratedChineseTypography(document,template=True)
            if _mapped_template_sections(document,snapshot,coverage=coverage,diagram_audit=diagram_audit):
                template_mode='PROFILE_SECTION_MAPPING';info['complete_report_in_slot']=True
        if not info['complete_report_in_slot']:
            document.add_page_break(); _write_report(document,snapshot,owned=owned,coverage=coverage,diagram_audit=diagram_audit)
        # Always make draft/confirmation status visible even when a user template
        # has only report_body or arbitrary display slots.
        for index,lead in enumerate(_state_block(document,_front_state_lines(snapshot))):
            document._element.body.insert(index,lead._p)
    document.core_properties.title=primary['payload']['candidate']['title']
    document.core_properties.subject=_confirmation_label(snapshot)
    document.core_properties.author='EEAS'
    document.core_properties.comments=snapshot.get('source_kind','PROPOSAL')+' '+canonical(snapshot['pending'].get('proposal_ref') or snapshot['pending'].get('session_ref'))
    zoom=document.settings.element.find(qn('w:zoom'))
    if zoom is not None and zoom.get(qn('w:percent')) is None:zoom.set(qn('w:percent'),'100')
    typography_audit=typography.apply()
    out=BytesIO(); document.save(out); raw=out.getvalue(); bounded_docx(raw)
    return raw,{'template_mode':template_mode,'normalization':normalization,
                'renderer':'python-docx/docxtpl/markdown-it-py','native_word_validation':'NOT_RUN',
                'body_content_slots':sorted(owned),'content_coverage':coverage,'diagram_readability':diagram_audit,
                'historical_records':_historical_records(snapshot,requested_slots),
                'chinese_typography':typography_audit}
