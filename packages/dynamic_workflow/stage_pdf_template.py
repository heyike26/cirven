"""Fill reviewed AcroForm text widgets in an incremental copy of the original PDF."""
from collections import defaultdict
from copy import copy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

from packages.domain.document_import import MAX_DOCUMENT_BYTES
from packages.domain.pdf_import import MAX_PAGES, TIMEOUT_SECONDS, pdf_worker_command

MAX_EDITS_BYTES = 4 * 1024 * 1024
MAX_WIDGETS = 1200
EXCLUDED_FLAGS = 1 | (1 << 13) | (1 << 20) | (1 << 25)  # read-only, password, file, rich-text


def value_issue(target, value):
    if not isinstance(value, str) or any(ord(c) < 32 and c not in '\r\n' for c in value):
        return 'PDF 字段需填写可显示的文本'
    if target['required'] and not value.strip():
        return 'PDF 必填字段不能为空'
    if not target['multiline'] and ('\r' in value or '\n' in value):
        return 'PDF 单行字段不能包含换行'
    if target.get('max_length') and len(value) > target['max_length']:
        return '填写内容超出 PDF 原字段的字符数限制'
    return None


def _worker_call(mode, raw, request=None):
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('PDF 模板文件大小超出限制')
    header = json.dumps(request or {}, ensure_ascii=False, separators=(',', ':')).encode()
    if len(header) > MAX_EDITS_BYTES + 1024 * 1024:
        raise ValueError('PDF 模板填写内容超出限制')
    try:
        proc = subprocess.run(pdf_worker_command('packages.dynamic_workflow.stage_pdf_template', mode),
                              cwd=Path(__file__).resolve().parents[2], input=len(header).to_bytes(4,'big') + header + raw,
                              capture_output=True, check=False, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('PDF 表单处理超时') from exc
    if proc.returncode or not proc.stdout:
        raise ValueError('PDF 表单处理失败或超过资源限制')
    if proc.stdout[:1] == b'!':
        raise ValueError(proc.stdout[1:500].decode('utf-8','replace'))
    return proc.stdout


def inspect_pdf(raw):
    return json.loads(_worker_call('describe', raw))


def fill_pdf(raw, targets, edits):
    return _worker_call('export', raw, {'targets': targets, 'edits': edits})


class _TextOnly(ValueError):
    """Native reproduction is inappropriate, but readable source text can still be used."""


def _form(reader):
    from pypdf.generic import IndirectObject
    if reader.is_encrypted:
        raise ValueError('请提供已经解锁的 PDF 模板')
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError('PDF 页数超出限制')
    catalog = reader.root_object
    acro = catalog.get('/AcroForm')
    if not acro:
        raise _TextOnly('PDF 没有可填写的文本表单')
    # An incremental copy also retains embedded scripts, files, signatures and XFA. Do not redistribute
    # these as a newly generated form. Only inspect dictionaries and references; never execute actions.
    seen = set()
    stack = [catalog]
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            obj = obj.get_object()
        if not isinstance(obj, (dict, list)) or id(obj) in seen:
            continue
        seen.add(id(obj))
        if len(seen) > 20000:
            raise _TextOnly('PDF 表单结构超出原位填写范围')
        if isinstance(obj, dict):
            open_action = obj.get('/OpenAction')
            if open_action is not None:
                open_action = open_action.get_object()
            if (isinstance(open_action,dict) or any(k in obj for k in ('/JS','/JavaScript','/AA','/EmbeddedFiles','/EF','/AF','/XFA',
                                       '/ByteRange','/Perms','/RichMediaContent','/3DD')) or
                    obj.get('/S') in ('/JavaScript','/Launch','/GoToR','/GoToE','/SubmitForm','/ImportData','/Rendition') or
                    obj.get('/FT') == '/Sig' and obj.get('/V')):
                raise _TextOnly('此 PDF 含签名、动态表单、附件或自动动作')
            stack.extend(obj.values())
        else:
            stack.extend(obj)
    return acro.get_object()


def _fields(reader):
    form = _form(reader)
    roots = form['/Fields'] if '/Fields' in form else []
    if not roots:
        raise _TextOnly('PDF 没有可填写的文本表单')
    # Follow field ownership, independently of rendered page widgets. Cycles and duplicate owners
    # cannot be mapped unambiguously to a reviewed position.
    names = {}
    stack = [(ref, '') for ref in roots]
    while stack:
        ref, prefix = stack.pop()
        field = ref.get_object()
        if id(field) in names or len(names) >= MAX_WIDGETS or len(prefix) > 512:
            raise _TextOnly('PDF 表单字段关系无法唯一对应')
        part = str(field.get('/T', ''))
        full = '.'.join(filter(None, [prefix, part]))
        names[id(field)] = full
        if '/Kids' in field:
            stack.extend((child, full) for child in field['/Kids'])
    by_owner = {}
    widgets = defaultdict(list)
    for pageno, page in enumerate(reader.pages, 1):
        for ref in page.annotations or []:
            widget = ref.get_object()
            if widget.get('/Subtype') != '/Widget' or int(widget.get('/F',0)) & (1|2|32):
                continue
            field = widget if '/FT' in widget and '/T' in widget else widget.get('/Parent')
            if field is None:
                continue
            field = field.get_object()
            if id(field) not in names:
                continue
            flags = int(field.get_inherited('/Ff', 0))
            if field.get('/FT') != '/Tx' or flags & EXCLUDED_FLAGS:
                if flags & 2 and not field.get('/V') and not flags & 1:
                    raise _TextOnly('PDF 中有需要使用专用控件填写的必填字段')
                continue
            name = names[id(field)]
            rect = widget['/Rect'] if '/Rect' in widget else []
            if not name or not field.get('/T') or len(rect)!=4 or rect[0]==rect[2] or rect[1]==rect[3]:
                continue
            token = by_owner.get(id(field))
            if token is None:
                token = f'pdf:field:{len(by_owner)+1}'
                by_owner[id(field)] = token
                if len(by_owner)>300:
                    raise _TextOnly('PDF 可填写字段超出单节点处理数量')
            widgets[token].append((pageno,widget,field))
    targets=[]
    for token, instances in widgets.items():
        _, _, field = instances[0]
        flags = int(field.get_inherited('/Ff',0))
        name = names[id(field)]
        label = str(field.get('/TU') or name)
        target = dict(target=token,columns=[label[:160]],field_name=name,
                      sample=[str(field.get('/V',''))[:200]],repeatable=False,required=bool(flags & 2),
                      multiline=bool(flags & (1<<12)),pages=sorted({n for n,_,_ in instances}))
        limit = field.get_inherited('/MaxLen',0)
        if int(limit)>0:
            target['max_length']=int(limit)
        targets.append(target)
    if not targets:
        raise _TextOnly('PDF 没有可原位填写的文本字段')
    return targets,widgets


def _export(reader, raw, targets, edits):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, NameObject
    from pypdf._font import Font

    writer = PdfWriter(reader, incremental=True)
    actual, groups = _fields(writer)
    if actual != targets:
        raise ValueError('PDF 模板填写位置与原件不一致')
    by_token = {t['target']:t for t in actual}
    selected = {}
    for edit in edits:
        key, values = edit['target'],edit['values']
        if key not in by_token or key in selected or not isinstance(values,list) or len(values)!=1:
            raise ValueError('PDF 模板填写位置无效')
        value=values[0]
        issue = value_issue(by_token[key],value)
        if issue:
            raise ValueError(issue)
        selected[key]=value
    if not selected or any(t['required'] and t['target'] not in selected for t in actual):
        raise ValueError('PDF 模板仍有未填写的必填位置')
    for token,value in selected.items():
        for number,widget,field in groups[token]:
            # Scope the writer to this exact widget. Matching a short field name on the whole
            # page can silently update a second, similarly named child in another form group.
            page = copy(writer.pages[number-1])
            page[NameObject('/Annots')] = ArrayObject([widget.indirect_reference])
            writer.update_page_form_field_values(page,{str(field['/T']):value},auto_regenerate=False)
            appearance=widget['/AP']['/N']
            for resource in appearance['/Resources']['/Font'].values():
                font = Font.from_font_resource(resource.get_object())
                if not font.can_encode(value.replace('\r','').replace('\n','')):
                    raise ValueError('原 PDF 字体无法显示填写文字，请使用支持这些字符的表单或导出审核文本')
    out=BytesIO()
    writer.write(out)
    filled=out.getvalue()
    if not filled.startswith(raw) or len(filled)>MAX_DOCUMENT_BYTES+MAX_EDITS_BYTES:
        raise ValueError('PDF 增量输出超出限制或原文件校验失败')
    verified, actual_groups = _fields(PdfReader(BytesIO(filled),strict=True))
    if [t['target'] for t in verified]!=list(by_token) or any(
            str(field.get('/V',''))!=value for key,value in selected.items() for _,_,field in actual_groups[key]):
        raise ValueError('无法核实导出 PDF 的填写结果')
    return filled


def _main():
    from pypdf import PdfReader
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
    except ImportError:
        pass
    stream=sys.stdin.buffer
    try:
        size=int.from_bytes(stream.read(4),'big')
        if size>MAX_EDITS_BYTES+1024*1024:
            raise ValueError('PDF 模板填写内容超出限制')
        request=json.loads(stream.read(size))
        raw=stream.read(MAX_DOCUMENT_BYTES+1)
        if len(raw)>MAX_DOCUMENT_BYTES:
            raise ValueError('PDF 模板文件大小超出限制')
        reader=PdfReader(BytesIO(raw),strict=True)
        if sys.argv[1]=='describe':
            try:
                targets,_=_fields(reader)
                result={'targets':targets}
            except _TextOnly as error:
                result={'targets':[],'warning':str(error)}
            data=json.dumps(result,ensure_ascii=False).encode()
        else:
            data=_export(reader,raw,request['targets'],request['edits'])
    except Exception as exc:
        message=str(exc) if type(exc) in (ValueError,_TextOnly) else 'PDF 表单结构无效或无法安全填写'
        data=b'!'+message.encode('utf-8','replace')
    sys.stdout.buffer.write(data)


if __name__=='__main__':
    _main()
