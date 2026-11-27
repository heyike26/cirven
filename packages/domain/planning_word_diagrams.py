"""Readable detail windows for frozen diagram pixels, without semantic changes."""
from io import BytesIO
import hashlib
import math

from docx.shared import Emu,Pt

from packages.domain.store import InvalidInput


def _number(value):
    return type(value) in {int,float} and math.isfinite(value)


def detail_windows(geometry, graph, width_px, height_px, page_width_pt, page_height_pt):
    """Plan crops containing every complete node and edge-label at >=9 points."""
    if geometry is None:
        return {'status':'NOT_VERIFIED_MISSING_GEOMETRY','windows':[]}
    if (not isinstance(geometry,dict) or type(geometry.get('version'))is not int or geometry['version']!=1 or
            geometry.get('width_px')!=width_px or geometry.get('height_px')!=height_px or
            geometry.get('text_complete')is not True or
            not _number(geometry.get('minimum_font_px')) or not 0<geometry['minimum_font_px']<=512):
        raise InvalidInput('Frozen diagram Word geometry is invalid or differs from its PNG')
    expected={('NODE',n['id'])for n in graph['nodes']}|{('EDGE_LABEL',e['id'])for e in graph['edges']}
    boxes=geometry.get('content_boxes')
    if not isinstance(boxes,list)or len(boxes)!=len(expected):
        raise InvalidInput('Frozen diagram Word geometry must cover every node and edge label')
    indexed={}
    for box in boxes:
        if not isinstance(box,dict):raise InvalidInput('Frozen diagram Word box is invalid')
        if not isinstance(box.get('kind'),str)or not isinstance(box.get('id'),str):
            raise InvalidInput('Frozen diagram Word box identity is invalid')
        key=(box.get('kind'),box.get('id'))
        if key not in expected or key in indexed:raise InvalidInput('Frozen diagram Word box identity differs from its graph')
        if any(not _number(box.get(k))for k in ('x','y','width','height')):
            raise InvalidInput('Frozen diagram Word box coordinates are invalid')
        x,y,w,h=(box[k]for k in ('x','y','width','height'))
        if x<0 or y<0 or w<=0 or h<=0 or x+w>width_px or y+h>height_px:
            raise InvalidInput('Frozen diagram Word box is outside its PNG')
        indexed[key]=(x,y,w,h)
    if not _number(page_width_pt)or not _number(page_height_pt)or min(page_width_pt,page_height_pt)<=0:
        raise InvalidInput('Word page has no available readable diagram area')
    scale=9/geometry['minimum_font_px']
    width=min(width_px,math.floor(page_width_pt/scale))
    height=min(height_px,math.floor(page_height_pt/scale))
    if width*height>2_000_000:height=2_000_000//width
    if min(width,height)<=0:raise InvalidInput('Word page is too small for readable diagram details')

    def contained(rect,box):
        x,y,w,h=box;l,t,r,b=rect
        return l<=x and t<=y and r>=x+w and b>=y+h

    def rect(x,y):
        x=max(0,min(width_px-width,int(x)));y=max(0,min(height_px-height,int(y)))
        return (x,y,x+width,y+height)

    candidates={rect(0,0),rect(width_px-width,0),rect(0,height_px-height),rect(width_px-width,height_px-height)}
    for box in indexed.values():
        x,y,w,h=box
        # Padding is part of the producer's complete content box. Reject too-large
        # content instead of silently shrinking its text or showing only fragments.
        if math.ceil(x+w)-math.floor(x)>width or math.ceil(y+h)-math.floor(y)>height:
            raise InvalidInput('A complete diagram item cannot fit a Word detail page at 9pt')
        candidates.add(rect(math.floor(x+(w-width)/2),math.floor(y+(h-height)/2)))
    # Coverage is immutable for this layout. Precompute it once rather than
    # rechecking every box for every greedy page selection on larger graphs.
    coverage={candidate:frozenset(key for key,box in indexed.items()if contained(candidate,box))
              for candidate in candidates}
    remaining=set(expected);windows=[]
    while remaining:
        best=max(sorted(candidates),key=lambda r:len(coverage[r]&remaining))
        covered=sorted(coverage[best]&remaining)
        if not covered:raise InvalidInput('Word detail page coverage could not be proved')
        windows.append({'rect':list(best),'complete_items':[{'kind':k[0],'id':k[1]}for k in covered]})
        remaining.difference_update(covered);candidates.remove(best)
        if len(windows)>64:raise InvalidInput('Readable Word diagram detail page limit exceeded')
    return {'status':'GEOMETRY_CHECKED_NATIVE_WORD_NOT_RUN','minimum_font_pt':9,
            'points_per_pixel':scale,'windows':windows,'complete_item_count':len(expected)}


def append_diagram(document, diagram, raw, image_info, *, audit=None, viewport_renderer=None, diagram_number=1):
    """Preserve the overview and add readable pixel crops on new report pages.

    Existing template section properties, original PNG bytes and the graph model
    are never rewritten. Coverage is geometric evidence, not a Word visual pass.
    """
    width_px,height_px=image_info['width'],image_info['height']
    if width_px*height_px>32_000_000:raise InvalidInput('Frozen diagram exceeds the bounded Word image area')
    section=document.sections[-1]
    available_width=section.page_width-section.left_margin-section.right_margin
    available_height=section.page_height-section.top_margin-section.bottom_margin
    geometry=diagram.get('render_geometry')
    if geometry is not None and (not isinstance(geometry,dict)or
            type(geometry.get('width_px'))is not int or type(geometry.get('height_px'))is not int or
            geometry['width_px']!=width_px or geometry['height_px']!=height_px or
            not _number(geometry.get('minimum_font_px'))or geometry['minimum_font_px']<=0):
        raise InvalidInput('Frozen diagram geometry dimensions differ from its PNG')
    logical=geometry
    if geometry is not None and 'logical_width_px'in geometry:
        required={'logical_width_px','logical_height_px','logical_minimum_font_px','logical_content_boxes','png_scale'}
        if not required.issubset(geometry)or any(type(geometry[k])is not int or not 0<geometry[k]<=200000 for k in ('logical_width_px','logical_height_px')):
            raise InvalidInput('Frozen diagram logical dimensions are invalid')
        if not _number(geometry.get('png_scale'))or not 0<geometry['png_scale']<=1:
            raise InvalidInput('Frozen diagram overview scale is invalid')
        logical={**geometry,'width_px':geometry['logical_width_px'],'height_px':geometry['logical_height_px'],
                 'minimum_font_px':geometry['logical_minimum_font_px'],'content_boxes':geometry['logical_content_boxes']}
    logical_width=logical['width_px']if logical is not None else width_px
    logical_height=logical['height_px']if logical is not None else height_px
    plan=detail_windows(logical,diagram['graph'],logical_width,logical_height,
                        available_width/12700,(available_height/12700)-72)
    overview_width=min(available_width,int((available_height*.65)*width_px/height_px))
    label=document.add_paragraph(f'图 {diagram_number} · 概览');label.paragraph_format.keep_with_next=True
    document.add_picture(BytesIO(raw),width=Emu(int(overview_width)))
    if geometry is None:
        document.add_paragraph('此历史图缺少字号与完整边界记录，最终文稿中的图字可读性尚未核验。')
    else:
        overview_font=geometry['minimum_font_px']*(overview_width/12700)/width_px
        plan['overview_minimum_font_pt']=overview_font
        if overview_font>=9:
            plan['windows']=[];plan['overview_covers_all_items']=True
        else:
            document.add_paragraph('概览用于定位；以下详图区以不少于9pt的几何比例保留完整节点和关系文字。')
            if viewport_renderer is None:
                from packages.domain.planning_diagrams import render_diagram_viewport
                viewport_renderer=render_diagram_viewport
            from packages.domain.document_import import image_metadata
            for index,window in enumerate(plan['windows'],1):
                left,top,right,bottom=window['rect']
                viewport={'x':left,'y':top,'width':right-left,'height':bottom-top}
                detail=viewport_renderer(diagram,viewport)
                info=image_metadata(detail)
                if info['width']!=viewport['width']or info['height']!=viewport['height']:
                    raise InvalidInput('Word diagram detail renderer changed the logical viewport dimensions')
                document.add_page_break()
                caption=document.add_paragraph(f'图 {diagram_number} · 详图 {index}/{len(plan["windows"])}')
                caption.paragraph_format.keep_with_next=True
                caption.paragraph_format.space_after=Pt(6)
                physical_width=math.ceil(viewport['width']*plan['points_per_pixel']*12700)
                document.add_picture(BytesIO(detail),width=Emu(physical_width))
                window.update(png_sha256=hashlib.sha256(detail).hexdigest(),byte_length=len(detail),
                    physical_width_emu=physical_width,
                    minimum_font_pt=logical['minimum_font_px']*physical_width/12700/viewport['width'])
    if audit is not None:audit.append({'graph_id':diagram['graph']['id'],'figure_number':diagram_number,'png_sha256':diagram['png_sha256'],**plan})
    return plan
