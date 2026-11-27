"""Fill reviewed cells in a copy of a selected template. Original package parts remain intact."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Callable
import hashlib
from io import BytesIO
from pathlib import PurePosixPath
import re
from zipfile import ZipFile

from lxml import etree as ET

from packages.domain.diagram_import import _resolve
from packages.domain.document_import import MAX_DOCUMENT_BYTES
from packages.domain.store import InvalidInput, NotFound
from packages.dynamic_workflow.stage_templates import template_issues

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
X = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
SPACE = '{http://www.w3.org/XML/1998/namespace}space'
SLOT = re.compile(r'\{\{\s*([^{}\r\n]{1,90}?)\s*\}\}')
CELL = re.compile(r'([A-Z]{1,3})([1-9][0-9]{0,6})$')
TEXT_FORMATS = {'.csv':'text/csv', '.tsv':'text/tab-separated-values', '.md':'text/markdown',
                '.markdown':'text/markdown', '.txt':'text/plain', '.json':'application/json'}
NATIVE = {'.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
FORMATS = {'.docx':'.docx','.dotx':'.docx','.xlsx':'.xlsx','.xltx':'.xlsx','.pptx':'.pptx'}
MAX_TARGETS = 300
MAX_FILLED = 4 * 1024**2


class UnsupportedPresentationLayout(ValueError):
    """A readable slide deck has fields that cannot be safely filled in place."""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def store_original(db, project, owner, template, data):
    if len(data)>MAX_DOCUMENT_BYTES or _hash(data)!=template['sha256']:
        raise InvalidInput('模板文件与已解析的版本不一致')
    db.execute('CREATE TABLE IF NOT EXISTS stage_template_files (project TEXT NOT NULL,owner TEXT NOT NULL,'
               'sha TEXT NOT NULL,bytes BLOB NOT NULL,PRIMARY KEY(project,owner,sha))')
    db.execute('INSERT OR IGNORE INTO stage_template_files VALUES (?,?,?,?)',(project,owner,template['sha256'],data))


def original_bytes(db,project,owner,template):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage_template_files'").fetchone():
        raise NotFound('历史模板仅保留提取的正文，可导出文本')
    row=db.execute('SELECT bytes FROM stage_template_files WHERE project=? AND owner=? AND sha=?',
                   (project,owner,template['sha256'])).fetchone()
    if not row:raise NotFound('未保存这一版模板的文件，可导出文本')
    if _hash(row[0])!=template['sha256']:raise InvalidInput('已保存的原模板校验失败')
    return row[0]


@dataclass
class Target:
    name: str
    columns: list
    sample: list
    repeatable: bool
    required: bool
    apply: Callable[[list[list[str]]],None]
    clone_bytes: int=0
    part_name: str=''

    def description(self):
        return dict(target=self.name, columns=self.columns, sample=[v[:200] for v in self.sample],
                    repeatable=self.repeatable,required=self.required)


def _xml(archive,name):
    return ET.fromstring(archive.read(name), ET.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))


def _simple_paragraph(p):
    return all(c.tag==W+'pPr' or (c.tag==W+'r' and all(x.tag in (W+'rPr',W+'t') for x in c)) for c in p)


def _paragraph_text(p):
    return ''.join(t.text or '' for t in p.iter(W+'t'))


def _put_text(node,text):
    if any(ord(c)<32 and c not in '\r\n\t' for c in text):raise ValueError('模板填写值含控制字符')
    pieces=re.split(r'([\n\t])',text.replace('\r\n','\n').replace('\r','\n'))
    node.text=pieces[0];node.set(SPACE,'preserve');last=node
    for token in pieces[1:]:
        if token in ('\n','\t'):
            element=ET.Element(W+('br' if token=='\n' else 'tab'))
        else:
            element=ET.Element(W+'t');element.set(SPACE,'preserve');element.text=token
        last.addnext(element);last=element


def replace_markers(strings, matches, values):
    if len(matches) != len(values):
        raise ValueError('模板段落占位符数量不一致')
    # Replace from the right so each original rich-text offset remains valid.
    offsets = [0]
    for text in strings:
        offsets.append(offsets[-1] + len(text))
    for match, value in reversed(list(zip(matches, values))):
        first = bisect_right(offsets, match.start()) - 1
        last = bisect_left(offsets, match.end()) - 1
        before = strings[first][:match.start() - offsets[first]]
        after = strings[last][match.end() - offsets[last]:]
        if first == last:
            strings[first] = before + value + after
        else:
            strings[first] = before + value
            strings[last] = after
            for index in range(first + 1, last):
                strings[index] = ''


def _write_paragraph(p,values):
    nodes=list(p.iter(W+'t'))
    if not nodes:
        run=p.find(W+'r')
        if run is None:run=ET.SubElement(p,W+'r')
        nodes=[ET.SubElement(run,W+'t')]
    strings=[n.text or '' for n in nodes]
    original=''.join(strings)
    matches=list(SLOT.finditer(original))
    if matches:
        replace_markers(strings, matches, values)
    else:
        # Only a blank or a single-style sample is eligible for whole-paragraph replacement.
        strings=[values[0],*(['']*(len(nodes)-1))]
    for n,s in zip(nodes,strings):
        if s!=(n.text or ''):_put_text(n,s)


def _wslots(archive):
    name='word/document.xml';document=_xml(archive,name);body=document.find(W+'body')
    if body is None:raise ValueError('模板没有 Word 正文')
    targets=[];heading='';tables=0
    for position,block in enumerate(body):
        if block.tag==W+'p':
            content=_paragraph_text(block);matches=list(SLOT.finditer(content));style=block.find(W+'pPr/'+W+'pStyle')
            role=(style.get(W+'val','') if style is not None else '').lower()
            if not matches and (role.startswith(('heading','title','subtitle','toc')) or content.rstrip().endswith(('：',':'))):
                heading=content;continue
            if not _simple_paragraph(block) or (not matches and len(block.findall(W+'r'))>1):continue
            if not matches and not heading and not content.strip():continue
            labels=[m.group(1) for m in matches] or [heading[:120] or '正文']
            targets.append(Target(f'paragraph:{position}',labels,[content],False,bool(matches),
                                  lambda rows,p=block:_write_paragraph(p,rows[0])))
            continue
        if block.tag!=W+'tbl':continue
        tables+=1;rows=block.findall(W+'tr')
        if not rows:continue
        labels=[_paragraph_text(c).strip()[:120] for c in rows[0].findall(W+'tc')]
        if (len(rows)==1 and len(labels)>1 and all(labels) and
            not any(SLOT.search(label) for label in labels) and
            all(len(c.findall(W+'p'))==1 and _simple_paragraph(c.find(W+'p')) for c in rows[0].findall(W+'tc')) and
            next(rows[0].iter(W+'gridSpan'),None) is None and next(rows[0].iter(W+'vMerge'),None) is None):
            blank=deepcopy(rows[0])
            for flag in list(blank.iter(W+'tblHeader')):flag.getparent().remove(flag)
            for text in blank.iter(W+'t'):text.text=''
            rows.append(blank)
        for index,row in enumerate(rows):
            cells=row.findall(W+'tc');texts=[_paragraph_text(c) for c in cells]
            if index==0 and not any(SLOT.search(s) for s in texts):continue
            if row.find(W+'trPr/'+W+'tblHeader') is not None:continue
            if not cells or len(cells)>128:continue
            def safe_cell(c):
                pars=c.findall(W+'p')
                return (len(pars)==1 and _simple_paragraph(pars[0]) and
                        all(child.tag in (W+'p',W+'tcPr') for child in c) and
                        c.find(W+'tcPr/'+W+'vMerge') is None and c.find(W+'tcPr/'+W+'gridSpan') is None and
                        len(list(SLOT.finditer(_paragraph_text(c))))<=1)
            if not all(safe_cell(c) for c in cells):continue
            # A model can only change data cells. Header rows, surrounding sections and other parts are untouched.
            editable=[i for i,s in enumerate(texts) if SLOT.search(s)]
            explicit=bool(editable)
            if not editable:editable=list(range(len(cells)))
            titles=[(SLOT.search(texts[i]).group(1) if SLOT.search(texts[i]) else
                     labels[i] if i<len(labels) and labels[i] else f'列 {i+1}') for i in editable]
            can_clone=index==len(rows)-1 and all(k.tag in (W+'tc',W+'trPr') for k in row)
            def fill(records,r=row,positions=editable,table=block):
                if r.getparent() is None:table.append(r)
                pattern=deepcopy(r);previous=r
                for order,values in enumerate(records):
                    target=deepcopy(pattern) if order else r
                    if order:
                        for para in target.iter(W+'p'):
                            for attr in list(para.attrib):
                                if ET.QName(attr).localname in ('paraId','textId'):del para.attrib[attr]
                        previous.addnext(target)
                    row_cells=target.findall(W+'tc')
                    for i,value in zip(positions,values):_write_paragraph(row_cells[i].find(W+'p'),[value])
                    previous=target
            targets.append(Target(f'table:{tables}:row:{index+1}',titles,[texts[i] for i in editable],can_clone,explicit,fill,len(ET.tostring(row)) if can_clone else 0))
    return targets,{name:document}


def _col_index(s):
    v=0
    for c in s:v=v*26+ord(c)-64
    return v


def _sheet_cell(c,shared):
    if c.get('t')=='inlineStr':return ''.join(t.text or '' for t in c.iter(X+'t'))
    value=c.findtext(X+'v',default='')
    return shared[int(value)] if c.get('t')=='s' and value else value


def _within(c_range,row,col):
    m=re.fullmatch(r'([A-Z]+)([0-9]+)(?::([A-Z]+)([0-9]+))?',c_range)
    if not m:return True
    return int(m[2])<=row<=int(m[4] or m[2]) and _col_index(m[1])<=col<=_col_index(m[3] or m[1])


def _xslots(archive):
    book=_xml(archive,'xl/workbook.xml');rels=_xml(archive,'xl/_rels/workbook.xml.rels')
    paths={r.get('Id'):_resolve('xl/workbook.xml',r.get('Target','')) for r in rels if r.get('Type','').endswith('/worksheet') and r.get('TargetMode')!='External'}
    strings=[]
    if 'xl/sharedStrings.xml' in archive.namelist():
        strings=[''.join(t.text or '' for t in s.findall(X+'t')+s.findall(X+'r/'+X+'t')) for s in _xml(archive,'xl/sharedStrings.xml')]
    targets=[];parts={}
    for num,sheet in enumerate(book.iter(X+'sheet'),1):
        path=paths.get(sheet.get(R+'id'));root=_xml(archive,path);parts[path]=root
        data=root.find(X+'sheetData')
        if data is None or sheet.get('state','visible')!='visible' or root.find(X+'sheetProtection') is not None:continue
        rows=data.findall(X+'row')
        if not rows:continue
        headers={}
        merged=[m.get('ref','') for m in root.iter(X+'mergeCell')]
        # Inserting outside table, named-range, validation, formula and drawing references can produce a misleading export.
        can_append=(not merged and not any(root.find(X+k) is not None for k in
                    ('autoFilter','tableParts','drawing','dataValidations','conditionalFormatting')) and book.find(X+'definedNames') is None)
        if len(rows)==1 and can_append:
            source=rows[0];entries=source.findall(X+'c')
            if (1<len(entries)<=128 and all(CELL.fullmatch(c.get('r','')) and c.find(X+'f') is None and
                    _sheet_cell(c,strings).strip() and not SLOT.search(_sheet_cell(c,strings)) for c in entries)):
                number=int(source.get('r','0'))+1
                if 1<number<=1048576:
                    blank=ET.Element(X+'row',r=str(number))
                    for c in entries:ET.SubElement(blank,X+'c',r=CELL.fullmatch(c.get('r')).group(1)+str(number))
                    data.append(blank);rows.append(blank)
        for idx,row in enumerate(rows):
            number=int(row.get('r','0'))
            if not 1<=number<=1048576:continue
            cells=[]
            for c in row.findall(X+'c'):
                m=CELL.fullmatch(c.get('r',''))
                if not m or int(m[2])!=number or _col_index(m[1])>16384:continue
                col=m[1];value=_sheet_cell(c,strings)
                if idx==0:headers[col]=value[:120]
                if c.find(X+'f') is None and not any(_within(area,number,_col_index(col)) for area in merged):
                    cells.append((col,value,c))
            marked=[v for v in cells if SLOT.search(v[1])]
            if idx==0 and not marked:continue
            automatic=idx>0 and all(not v[1].strip() for v in cells)
            if not marked and not automatic:continue
            items=marked or cells
            if not items or len(items)>128 or any(len(list(SLOT.finditer(v[1])))>1 for v in items):continue
            repeating=can_append and idx==len(rows)-1 and row.find('.//'+X+'f') is None and (len(cells)==len(row.findall(X+'c')))
            columns=[SLOT.search(v).group(1) if SLOT.search(v) else headers.get(col) or col for col,v,_ in items]
            def fill(records,pattern=row,items=items,n=number,root=root):
                clone=deepcopy(pattern);previous=pattern
                for offset,values in enumerate(records):
                    if n+offset>1048576:raise ValueError('填充结果超出 Excel 行数')
                    dest=deepcopy(clone) if offset else pattern
                    if offset:
                        previous.addnext(dest);dest.set('r',str(n+offset))
                        for cell in dest.findall(X+'c'):
                            cell.set('r',CELL.fullmatch(cell.get('r')).group(1)+str(n+offset))
                    for (col,sample,_),v in zip(items,values):
                        cell=next(c for c in dest.findall(X+'c') if c.get('r')==col+str(n+offset))
                        text=SLOT.sub(lambda _m:v,sample,count=1) if SLOT.search(sample) else v
                        if len(text)>32767:raise ValueError('填写内容超过 Excel 单元格长度')
                        for tag in ('v','is','f'):
                            elem=cell.find(X+tag)
                            if elem is not None:cell.remove(elem)
                        cell.set('t','inlineStr');inline=ET.SubElement(cell,X+'is');node=ET.SubElement(inline,X+'t')
                        node.set(SPACE,'preserve');node.text=text
                    previous=dest
                if records:
                    dimension=root.find(X+'dimension')
                    if dimension is not None:
                        ref=dimension.get('ref','');first=ref.split(':')[0];last=CELL.fullmatch(ref.split(':')[-1])
                        if last:dimension.set('ref',f'{first}:{last[1]}{max(n+len(records)-1,int(last[2]))}')
            targets.append(Target(f'sheet:{num}:row:{number}',[f'{sheet.get("name","")} · {col}' for col in columns],
                                  [v for _,v,_ in items],repeating,bool(marked),fill,len(ET.tostring(row)) if repeating else 0))
    return targets,parts


def _layout(raw,extension):
    with ZipFile(BytesIO(raw)) as archive:
        if any(p.lower().endswith(('vbaproject.bin','vbadata.xml')) for p in archive.namelist()):
            raise ValueError('此工作流不导出含宏模板')
        if extension == '.pptx':
            from packages.dynamic_workflow.stage_pptx_template import presentation_slots
            return presentation_slots(raw, archive)
        return _wslots(archive) if extension=='.docx' else _xslots(archive)


def describe_template(template,raw):
    suffix=PurePosixPath(template['name']).suffix.lower()
    if suffix == '.pdf':
        from packages.dynamic_workflow.stage_pdf_template import inspect_pdf
        if _hash(raw) != template['sha256']:
            raise ValueError('所选 PDF 模板的原始文件校验失败')
        result = inspect_pdf(raw)
        if result['targets']:
            return dict(template, edit_targets=result['targets'], export_extension='.pdf')
        return dict(template, export_extension='.txt', warnings=[*template.get('warnings', []),
                    result['warning'] + '；审核后提供纯文本。'])
    if suffix in FORMATS:
        if suffix == '.pptx' and _hash(raw) != template['sha256']:
            raise ValueError('所选 PowerPoint 模板的原始文件校验失败')
        try:
            slots,_=_layout(raw,FORMATS[suffix])
        except UnsupportedPresentationLayout as exc:
            return dict(template, export_extension='.txt',
                        warnings=[*template.get('warnings', []), str(exc) + '；审核后提供纯文本。'])
        if len(slots)>MAX_TARGETS or sum(len(t.columns) for t in slots)>2500 or sum(len(v[:200]) for t in slots for v in t.sample)>100000:
            slots=[]
        if slots:
            return dict(template,edit_targets=[s.description() for s in slots],export_extension=FORMATS[suffix])
    if suffix in TEXT_FORMATS:return dict(template,export_extension=suffix)
    warnings=list(template.get('warnings',[]))
    if suffix == '.pptx':
        warnings.append('未找到可完整填写的幻灯片版式，或字段超过导出上限。可在文本框或表格单元格中使用 {{字段名}}；此次审核后提供纯文本。')
    else:
        warnings.append('此模板已提取正文；审核后提供纯文本。')
    return dict(template,export_extension='.txt',warnings=warnings)


def export_stage_template(template,artifact,raw,plain=False):
    if raw is not None and _hash(raw)!=template['sha256']:raise ValueError('所选模板原始文件校验失败')
    text=artifact.get('template_output','')
    errors=template_issues(template,text,artifact.get('template_edits'))
    if errors:raise ValueError(errors[0])
    filename=PurePosixPath(template['name'].replace('\\','/')).name
    ext=template.get('export_extension','.txt') if not plain else '.txt'
    outname=PurePosixPath(filename).stem+'-已审核'+ext
    if ext in TEXT_FORMATS:
        encoding='utf-8'
        if raw and ext!='.txt':
            encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
        return outname,TEXT_FORMATS[ext],text.encode(encoding)
    if raw is None:raise ValueError('缺少所选模板的原始文件')
    if ext == '.pdf':
        from packages.dynamic_workflow.stage_pdf_template import fill_pdf
        return outname, 'application/pdf', fill_pdf(raw, template['edit_targets'], artifact.get('template_edits', []))
    targets,parts=_layout(raw,ext)
    if [s.description() for s in targets]!=template['edit_targets']:raise ValueError('模板填写位置已改变')
    edits=defaultdict(list)
    for edit in artifact.get('template_edits',[]):edits[edit['target']].append(edit['values'])
    if sum(max(0,len(edits.get(slot.name,()))-1)*slot.clone_bytes for slot in targets)>MAX_DOCUMENT_BYTES:
        raise ValueError('展开模板行后超出文稿大小限制')
    if sum(len(s.encode()) for rows in edits.values() for row in rows for s in row)>MAX_FILLED:
        raise ValueError('模板填充超出长度限制')
    if ext == '.pptx':
        selected = {slot.part_name for slot in targets if slot.name in edits}
        parts = {name: xml for name, xml in parts.items() if name in selected}
    if ext=='.xlsx':
        with ZipFile(BytesIO(raw)) as base:
            book=_xml(base,'xl/workbook.xml');rels=_xml(base,'xl/_rels/workbook.xml.rels')
            paths={r.get('Id'):_resolve('xl/workbook.xml',r.get('Target','')) for r in rels if r.get('Type','').endswith('/worksheet') and r.get('TargetMode')!='External'}
            selected={paths[sheet.get(R+'id')] for i,sheet in enumerate(book.iter(X+'sheet'),1)
                      if any(id.startswith(f'sheet:{i}:') for id in edits)}
            parts={name:xml for name,xml in parts.items() if name in selected}
    for slot in targets:
        if slot.name in edits:slot.apply(edits[slot.name])
    src=BytesIO(raw);out=BytesIO()
    with ZipFile(src) as base,ZipFile(out,'w') as dest:
        if PurePosixPath(filename).suffix.lower() in ('.dotx','.xltx'):
            types=_xml(base,'[Content_Types].xml')
            for override in types:
                value=override.get('ContentType','')
                if value.endswith('.template.main+xml'):
                    override.set('ContentType',value.replace('.template.main+xml',
                          '.document.main+xml' if ext=='.docx' else '.sheet.main+xml'))
            parts['[Content_Types].xml']=types
        for member in base.infolist():
            value=(ET.tostring(parts[member.filename],encoding='UTF-8',xml_declaration=True)
                   if member.filename in parts else base.read(member))
            dest.writestr(member,value)
    return outname,NATIVE[ext],out.getvalue()
