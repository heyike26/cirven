"""Explicit traceable format derivatives, never claimed as upstream supplied files."""
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

from campaign_probes import Evidence, load_binding, sha

CONVERTER = "eeas.derived-source-formats.v2"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def original_bodies(captured, evidence=None):
    declared = captured.get("source", {}).get("provenance")
    wrapped = captured["text"].startswith("EEAS PUBLIC AUDIT MATERIAL\n")
    if not declared and not wrapped:
        return None
    if evidence is None:
        raise ValueError("Composite derivative requires frozen original-body evidence; wrapper is not original source")
    from campaign_workflow_probes import source_bodies
    return source_bodies(captured, evidence)


def mapped_lines(captured, bodies):
    """Whole original physical lines, excluding only an explicit converter EOF LF."""
    result, offset, byte_offset = {}, 0, 0
    for number, line in enumerate(captured["text"].splitlines(keepends=True), 1):
        text = line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        body = next((b for b in bodies if b["body"]["line_start"] <= number <= b["body"]["line_end"]
                     and b["body"]["char_start"] <= offset and offset + len(text) <= b["body"]["char_end_exclusive"]), None)
        if body:
            physical = line[:body["body"]["char_end_exclusive"] - offset]
            original_number = body["original"]["line_start"] + number - body["body"]["line_start"]
            original_text = captured["text"][body["body"]["char_start"]:body["body"]["char_end_exclusive"]]
            assert physical == original_text.splitlines(keepends=True)[original_number - body["original"]["line_start"]]
            original_start = body["original"]["char_start"] + offset - body["body"]["char_start"]
            original_byte = body["original"]["byte_start"] + byte_offset - body["body"]["byte_start"]
            result[number] = {"line_start": number, "line_end": number, "char_start": offset,
                "char_end": offset + len(physical), "raw_text": physical, "text": physical.rstrip("\r\n").expandtabs(4),
                "selection_role": "ORIGINAL_BODY", "original_source": {"path": body["source_path"], "sha256": body["sha256"],
                    "source_url": body["source_url"], "archive_member": body["archive_member"], "line": original_number,
                    "char_start": original_start, "char_end": original_start + len(physical),
                    "byte_start": original_byte, "byte_end": original_byte + len(physical.encode()),
                    "generated_terminal_separator_excluded": physical != line}}
        offset += len(line)
        byte_offset += len(line.encode())
    return result


def selections(captured, evidence=None):
    bodies = original_bodies(captured, evidence)
    mapped = mapped_lines(captured, bodies) if bodies else None
    lines = captured["text"].splitlines(keepends=True)
    requested = os.environ.get("EEAS_CAMPAIGN_SOURCE_LINES")
    nonblank = [n for n, part in mapped.items() if part["raw_text"].strip()] if mapped is not None else [index + 1 for index, line in enumerate(lines) if line.strip()]
    numbers = [int(v) for v in requested.split(",")] if requested else sorted(set(nonblank[:2] + nonblank[-1:]))
    if not numbers or len(numbers) > 12 or len(set(numbers)) != len(numbers) or any(n < 1 or n > len(lines) for n in numbers):
        raise ValueError("explicit source lines must be unique 1-based existing lines, at most twelve")
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    if mapped is not None and any(n not in mapped for n in numbers):
        raise ValueError("Explicit derivative lines must be original body lines, never generated wrappers")
    selected = [mapped[n] for n in sorted(numbers)] if mapped is not None else [{"line_start": n, "line_end": n, "char_start": offsets[n - 1],
                 "char_end": offsets[n - 1] + len(lines[n - 1]), "raw_text": lines[n - 1],
                 "text": lines[n - 1].rstrip("\r\n").expandtabs(4)} for n in sorted(numbers)]
    if sum(len(part["text"]) for part in selected) > 1200:
        raise ValueError("selected source exceeds bounded derivative text; choose explicit smaller lines, no truncation")
    return selected


def cmap_glyph(raw, point):
    """Read actual OpenType cmap format4/12 coverage; no tofu-mask success inference."""
    u16 = lambda pos: struct.unpack_from(">H", raw, pos)[0]
    u32 = lambda pos: struct.unpack_from(">I", raw, pos)[0]
    table = next(u32(12 + 16 * n + 8) for n in range(u16(4)) if raw[12 + 16 * n:16 + 16 * n] == b"cmap")
    for n in range(u16(table + 2)):
        platform, encoding = u16(table + 4 + 8 * n), u16(table + 6 + 8 * n)
        if platform not in {0, 3} or platform == 3 and encoding not in {1, 10}:
            continue
        start = table + u32(table + 8 + 8 * n)
        form = u16(start)
        if form == 12:
            for group in range(u32(start + 12)):
                pos = start + 16 + 12 * group
                first, last, glyph = u32(pos), u32(pos + 4), u32(pos + 8)
                if first <= point <= last:
                    return glyph + point - first
        elif form == 4 and point <= 65535:
            count = u16(start + 6) // 2
            ends, begins = start + 14, start + 16 + 2 * count
            deltas, ranges = begins + 2 * count, begins + 4 * count
            for index in range(count):
                if u16(begins + 2 * index) <= point <= u16(ends + 2 * index):
                    delta, offset = u16(deltas + 2 * index), u16(ranges + 2 * index)
                    glyph = u16(ranges + 2 * index + offset + 2 * (point - u16(begins + 2 * index))) if offset else point
                    return (glyph + delta) % 65536 if glyph else 0
    return 0


def render(selected):
    font = ImageFont.load_default(size=20)
    font_raw = font.path.getvalue()
    text = "\n".join(part["text"] for part in selected)
    unsupported = sorted({ord(char) for char in text if char != "\n" and cmap_glyph(font_raw, ord(char)) == 0})
    if unsupported:
        raise ValueError("Pillow bundled font lacks selected Unicode glyphs; refusing silent replacement: " + ",".join(f"U+{p:04X}" for p in unsupported))
    # Wrap without dropping any source character; keep the render/source mapping explicit.
    rows = []
    for part in selected:
        row = ""
        for char in part["text"]:
            if row and font.getlength(row + char) > 920:
                rows.append({"source_line": part["line_start"], "text": row})
                row = ""
            row += char
        rows.append({"source_line": part["line_start"], "text": row})
    image = Image.new("RGB", (960, 48 + len(rows) * 30), "white")
    draw = ImageDraw.Draw(image)
    for n, row in enumerate(rows):
        draw.text((20, 20 + n * 30), row["text"], font=font, fill="black")
    raw = io.BytesIO()
    image.save(raw, format="PNG")
    license_path = next(p for p in importlib.metadata.distribution("Pillow").files if str(p).endswith("licenses/LICENSE"))
    legal = importlib.metadata.distribution("Pillow").locate_file(license_path).read_bytes()
    return raw.getvalue(), {"font": "Pillow bundled Aileron Regular", "font_sha256": sha(font_raw),
                          "pillow_version": PILLOW_VERSION, "pillow_license_sha256": sha(legal),
                          "font_cmap_coverage_checked": True, "rows": rows,
                          "width": image.width, "height": image.height,
                          "transform": "strip physical newline for layout; expand tabs to four columns; wrap without dropping characters"}


def word(selected, image):
    ET.register_namespace("w", W)
    ET.register_namespace("r", R)
    ET.register_namespace("a", A)
    document = ET.Element("{" + W + "}document")
    body = ET.SubElement(document, "{" + W + "}body")
    def paragraph(parent, text):
        p = ET.SubElement(parent, "{" + W + "}p")
        r = ET.SubElement(p, "{" + W + "}r")
        t = ET.SubElement(r, "{" + W + "}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        t.text = text
        return p
    for part in selected:
        p = paragraph(body, part["text"])
        if part["text"].startswith("# "):
            props = ET.Element("{" + W + "}pPr")
            ET.SubElement(props, "{" + W + "}pStyle", {"{" + W + "}val": "Heading1"})
            p.insert(0, props)  # Source '#' is preserved; this is a derived heading style.
    table = ET.SubElement(body, "{" + W + "}tbl")
    for part in selected:
        row = ET.SubElement(table, "{" + W + "}tr")
        for text in (f"source line {part['line_start']} chars {part['char_start']}:{part['char_end']}", part["text"]):
            paragraph(ET.SubElement(row, "{" + W + "}tc"), text)
    p = ET.SubElement(body, "{" + W + "}p")
    r = ET.SubElement(p, "{" + W + "}r")
    drawing = ET.SubElement(r, "{" + W + "}drawing")
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"
    width, height = Image.open(io.BytesIO(image)).size
    cx, cy = 5486400, 5486400 * height // width
    inline = ET.SubElement(drawing, "{" + wp + "}inline")
    ET.SubElement(inline, "{" + wp + "}extent", {"cx": str(cx), "cy": str(cy)})
    ET.SubElement(inline, "{" + wp + "}docPr", {"id": "1", "name": "Derived source selection"})
    graphic = ET.SubElement(inline, "{" + A + "}graphic")
    data = ET.SubElement(graphic, "{" + A + "}graphicData", {"uri": pic})
    picture = ET.SubElement(data, "{" + pic + "}pic")
    properties = ET.SubElement(picture, "{" + pic + "}nvPicPr")
    ET.SubElement(properties, "{" + pic + "}cNvPr", {"id": "1", "name": "source.png"})
    ET.SubElement(properties, "{" + pic + "}cNvPicPr")
    fill = ET.SubElement(picture, "{" + pic + "}blipFill")
    ET.SubElement(fill, "{" + A + "}blip", {"{" + R + "}embed": "sourceImage"})
    ET.SubElement(ET.SubElement(fill, "{" + A + "}stretch"), "{" + A + "}fillRect")
    shape = ET.SubElement(picture, "{" + pic + "}spPr")
    transform = ET.SubElement(shape, "{" + A + "}xfrm")
    ET.SubElement(transform, "{" + A + "}off", {"x": "0", "y": "0"})
    ET.SubElement(transform, "{" + A + "}ext", {"cx": str(cx), "cy": str(cy)})
    ET.SubElement(ET.SubElement(shape, "{" + A + "}prstGeom", {"prst": "rect"}), "{" + A + "}avLst")
    relationships = lambda values: ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"/>' for rid, kind, target in values) + '</Relationships>').encode()
    parts = {"[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
             "_rels/.rels": relationships([("main", "officeDocument", "word/document.xml")]),
             "word/document.xml": ET.tostring(document, encoding="utf-8", xml_declaration=True),
             "word/_rels/document.xml.rels": relationships([("sourceImage", "image", "media/source.png")]),
             "word/media/source.png": image}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in parts.items():
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    return stream.getvalue()


def import_derived(client, captured, evidence, raw, name, *, prefix=""):
    created = client.post("/api/projects", json={"name": "Derived " + captured["source"]["project_id"]})
    assert created.status_code == 201
    pid = created.json()["id"]
    response = client.post(f"/api/projects/{pid}/documents", files={"file": (name, raw)},
                           data={"source": "derived:source-format-probe:parent=" + sha(captured["raw"])})
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["sha256"] == sha(raw)
    base = f"/api/projects/{pid}/documents/{doc['id']}"
    original = client.get(base + "/download", params={"revision": 1})
    assert original.status_code == 200 and original.content == raw
    parsed = client.get(base).json()
    assets = client.get(base + "/assets", params={"revision": 1}).json()["assets"]
    evidence.save(prefix + "derived-original.bin", original.content)
    evidence.save(prefix + "actual-format-document.json", parsed)
    evidence.save(prefix + "actual-format-structure.json", client.get(base + "/structure", params={"revision": 1}).json())
    for n, asset in enumerate(assets):
        retrieved = client.get(base + f"/assets/{asset['asset_id']}", params={"revision": 1})
        assert retrieved.status_code == 200 and sha(retrieved.content) == asset["sha256"]
        evidence.save(prefix + f"actual-asset-{n}.bin", retrieved.content)
    evidence.save(prefix + "actual-format-assets.json", assets)
    return parsed, assets


def derived_trace(captured, selected, rendered, raw, suffix):
    return {"schema": "eeas.derived-format.v1", "converter": CONVERTER,
            "converter_code_sha256": sha(Path(__file__).read_bytes()), "command": sys.argv,
            "parent_raw_sha256": sha(captured["raw"]), "parent_source": captured["source"],
            "selected_source": selected, "selected_complete_source": sum(len(p["raw_text"]) for p in selected) == len(captured["text"]),
            "derived_format": suffix, "derived_sha256": sha(raw), "render": rendered,
            "material_kind": "traceable-derived-format-not-upstream-supplied", "ocr_executed": False,
            "limits": "bounded explicit selected lines, bundled font supported glyphs only; no upstream format/authenticity certification"}


def test_source_docx(client):
    captured = load_binding("source-docx")
    evidence = Evidence(captured)
    selected = selections(captured, evidence)
    image, rendering = render(selected)
    raw = word(selected, image)
    evidence.save("derived-format-trace.json", derived_trace(captured, selected, rendering, raw, "docx"))
    parsed, assets = import_derived(client, captured, evidence, raw, "derived-source.docx")
    assert parsed["format"] == "docx"
    nodes = parsed["graph"]["nodes"]
    for n, part in enumerate(selected, 1):
        node = next(node for node in nodes if node["source"]["part"] == "word/document.xml" and node["source"]["node_id"] == f"/document[1]/body[1]/p[{n}]")
        assert node["text"] == part["text"]
        assert part["text"] in "\n".join(parsed["content"].splitlines()[node["line_start"] - 1:node["line_end"]])
    assert any(node["kind"] == "table" for node in nodes)
    assert len(assets) == 1 and assets[0]["sha256"] == sha(image) and assets[0]["source_anchors"]
    evidence.observe()


def test_source_raster(client):
    captured = load_binding("source-raster")
    evidence = Evidence(captured)
    selected = selections(captured, evidence)
    raw, rendering = render(selected)
    evidence.save("derived-format-trace.json", derived_trace(captured, selected, rendering, raw, "png"))
    parsed, assets = import_derived(client, captured, evidence, raw, "derived-source.png")
    assert parsed["format"] == "image" and len(assets) == 1
    assert assets[0]["width"] == rendering["width"] and assets[0]["height"] == rendering["height"]
    assert assets[0]["sha256"] == sha(raw) and not assets[0]["source_anchors"]
    assert "IMAGE_TEXT_NOT_EXTRACTED" in parsed["warnings"] and "MODEL_VISION_NOT_VALIDATED" in parsed["warnings"]
    evidence.observe()


def atx_outline(captured, required=True):
    """ATX document outline only; fenced code headings are not document structure."""
    nodes, stack, fence, offset = [], [], None, 0
    # Preserve every character/physical-line position while hiding non-heading comments.
    masked = re.sub(r"<!--.*?(?:-->|$)", lambda found: "".join(
        char if char in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029" else " " for char in found.group()),
        captured["text"], flags=re.S)
    for number, raw_line in enumerate(masked.splitlines(keepends=True), 1):
        line = raw_line.rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
        elif fence is None:
            found = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)", line)
            if found:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", found.group(2)).strip()
                if not title:
                    raise ValueError("empty source heading requires explicit review, no invented title")
                start = line.index(title, found.start(2))
                if captured["text"][offset + start:offset + start + len(title)] != title:
                    raise ValueError("heading has noncontiguous comment text; explicit source mapping required")
                level = len(found.group(1))
                while stack and stack[-1]["level"] >= level:
                    stack.pop()
                node = {"id": "line-" + str(number), "title": title, "level": level,
                        "parent_id": stack[-1]["id"] if stack else None,
                        "line_start": number, "line_end": number,
                        "char_start": offset + start, "char_end": offset + start + len(title)}
                nodes.append(node)
                stack.append(node)
        offset += len(raw_line)
    if (required and not nodes) or len(nodes) > 64:
        raise ValueError("requires actual ATX source headings, at most64; no invented or truncated document outline")
    return nodes


def adorned_outline(text, syntax):
    """Bounded single-line Setext/RST headings; no literal/directive block inference."""
    masked = re.sub(r"<!--.*?(?:-->|$)", lambda found: "".join(
        char if char in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029" else " " for char in found.group()), text, flags=re.S)
    raw_lines = masked.splitlines(keepends=True)
    offsets, cursor = [], 0
    for line in raw_lines:
        offsets.append(cursor)
        cursor += len(line)
    lines = [line.rstrip("\r\n") for line in raw_lines]
    nodes, styles, index, fence, section_end = [], {}, 0, None, None
    def adornment(line):
        found = re.fullmatch(r"([=\-~^\"'`:+*#_.<>])\1*[ \t]*", line)
        return found.group(1) if found else None
    while index < len(lines):
        line = lines[index]
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker and syntax == "markdown":
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
            index += 1
            continue
        if fence:
            index += 1
            continue
        overline = syntax == "rst" and index + 2 < len(lines) and adornment(line) and adornment(lines[index + 2]) == adornment(line)
        title_index = index + 1 if overline else index
        title_line = lines[title_index]
        title = title_line.strip()
        underline_index = index + 2 if overline else index + 1
        char = adornment(lines[underline_index]) if underline_index < len(lines) else None
        candidate = bool(char and title and not adornment(title_line) and len(title) <= 1200)
        if syntax == "rst":
            candidate = candidate and (title_line == title or overline and len(title_line) - len(title_line.lstrip()) <= 3)
            candidate = candidate and len(lines[underline_index].rstrip()) >= len(title_line.rstrip())
            candidate = candidate and (not overline or len(line.rstrip()) >= len(title_line.rstrip()))
            if candidate and "\t" in title_line:
                raise ValueError("Tabbed RST title requires explicit column review")
            candidate = candidate and (index == 0 or not lines[index - 1].strip() or section_end == index - 1)
        else:
            candidate = candidate and char in {"=", "-"} and title_line == title and not title.startswith(("#", "- ", "* ", "+ ", ">"))
            if candidate and index > 0 and lines[index - 1].strip() and not adornment(lines[index - 1]):
                raise ValueError("Multiline Setext title needs explicit review; no truncated heading")
            candidate = candidate and (index == 0 or not lines[index - 1].strip())
        if candidate:
            key = (bool(overline), char)
            if syntax == "rst":
                if key not in styles:
                    styles[key] = len(styles) + 1
                level = styles[key]
            else:
                level = 1 if char == "=" else 2
            start = offsets[title_index] + title_line.index(title)
            if text[start:start + len(title)] != title:
                raise ValueError("Noncontiguous heading text needs explicit review")
            nodes.append({"id": "line-" + str(title_index + 1), "title": title, "level": level,
                "parent_id": None, "line_start": title_index + 1, "line_end": title_index + 1,
                "char_start": start, "char_end": start + len(title), "heading_syntax": "rst" if syntax == "rst" else "setext"})
            section_end = underline_index
            index = underline_index + 1
        else:
            index += 1
    return nodes


def source_outline(captured, evidence=None):
    bodies = original_bodies(captured, evidence)
    if bodies is None:
        return atx_outline(captured)  # Preserve the original plain-source contract.
    requested = os.environ.get("EEAS_CAMPAIGN_OUTLINE_FILES")
    wanted = requested.split(",") if requested else [body["source_path"] for body in bodies]
    if not wanted or len(set(wanted)) != len(wanted) or any(path not in {body["source_path"] for body in bodies} for path in wanted):
        raise ValueError("Outline files must be unique exact original source paths from the frozen provenance")
    nodes = []
    for body in bodies:
        if body["source_path"] not in wanted:
            continue
        text = captured["text"][body["body"]["char_start"]:body["body"]["char_end_exclusive"]]
        syntax = "rst" if Path(body["source_path"]).suffix.lower() == ".rst" else "markdown"
        local = adorned_outline(text, syntax)
        if syntax == "markdown":
            local += [{**node, "heading_syntax": "atx"} for node in atx_outline({"text": text}, required=False)]
        stack = []
        for node in sorted(local, key=lambda node: node["line_start"]):
            line = body["body"]["line_start"] + node["line_start"] - 1
            start, end = node["char_start"], node["char_end"]
            original_line = body["original"]["line_start"] + node["line_start"] - 1
            mapped = {**node, "id": "line-" + str(line), "line_start": line, "line_end": line,
                "char_start": body["body"]["char_start"] + start, "char_end": body["body"]["char_start"] + end,
                "original_source": {"path": body["source_path"], "sha256": body["sha256"], "source_url": body["source_url"],
                    "archive_member": body["archive_member"], "line": original_line,
                    "char_start": body["original"]["char_start"] + start, "char_end": body["original"]["char_start"] + end,
                    "byte_start": body["original"]["byte_start"] + len(text[:start].encode()),
                    "byte_end": body["original"]["byte_start"] + len(text[:end].encode())}}
            while stack and stack[-1]["level"] >= mapped["level"]:
                stack.pop()
            mapped["parent_id"] = stack[-1]["id"] if stack else None
            nodes.append(mapped)
            stack.append(mapped)
    if not nodes or len(nodes) > 64:
        raise ValueError("Requires actual original document headings, at most64; select exact original outline files explicitly, no truncation")
    return nodes


def outline_archive(nodes, suffix, *, include_hierarchy_links=False):
    if type(include_hierarchy_links) is not bool:
        raise ValueError("Explicit hierarchy-link choice must be boolean")
    pairs = [node for node in nodes if node["parent_id"]]
    if include_hierarchy_links and (len(nodes) > 64 or suffix == "vsdx" and len(nodes) + len(pairs) > 64):
        raise ValueError("Fidelity outline permits at most64 actual diagram nodes including connector shapes; select exact original files, no truncation")
    children = {node["id"]: [] for node in nodes}
    roots = []
    for node in nodes:
        (children[node["parent_id"]] if node["parent_id"] else roots).append(node)
    if suffix == "xmind":
        def topic(node):
            value = {"id": node["id"], "title": node["title"]}
            if children[node["id"]]:
                value["children"] = {"attached": [topic(child) for child in children[node["id"]]]}
            return value
        sheets = []
        for root in roots:
            sheet = {"id": "sheet-" + root["id"], "title": root["title"], "rootTopic": topic(root)}
            if include_hierarchy_links:
                def descendants(node):
                    yield node
                    for child in children[node["id"]]:
                        yield from descendants(child)
                sheet["relationships"] = [{"id": "derived-link-" + node["id"], "end1Id": node["parent_id"],
                    "end2Id": node["id"], "title": "Derived original document heading hierarchy"}
                    for node in descendants(root) if node["parent_id"]]
            sheets.append(sheet)
        parts = {"content.json": json.dumps(sheets, ensure_ascii=False).encode(),
                 "metadata.json": b'{"creator":{"name":"EEAS derived document outline"}}'}
    elif suffix == "vsdx":
        namespace = "http://schemas.microsoft.com/office/visio/2012/main"
        relation_type = "http://schemas.microsoft.com/visio/2010/relationships/"
        def xml(tag):
            return "{" + namespace + "}" + tag
        content = ET.Element(xml("PageContents"))
        shapes = ET.SubElement(content, xml("Shapes"))
        def shape(node, parent):
            value = ET.SubElement(parent, xml("Shape"), {"ID": str(node["line_start"]), "NameU": node["id"],
                                                        "Type": "Group" if children[node["id"]] else "Shape"})
            ET.SubElement(value, xml("Text")).text = node["title"]
            if children[node["id"]]:
                subshapes = ET.SubElement(value, xml("Shapes"))
                for child in children[node["id"]]:
                    shape(child, subshapes)
        for node in roots:
            shape(node, shapes)
        if include_hierarchy_links:
            by_id = {node["id"]: node for node in nodes}
            connects = ET.SubElement(content, xml("Connects"))
            next_id = max(node["line_start"] for node in nodes) + 1
            for index, child in enumerate(pairs):
                identifier = str(next_id + index)
                connector = ET.SubElement(shapes, xml("Shape"), {"ID": identifier, "NameU": "derived-link-" + child["id"], "Type": "Shape"})
                ET.SubElement(connector, xml("Cell"), {"N": "OneD", "V": "1"})
                ET.SubElement(connector, xml("Text")).text = "Derived original document heading hierarchy"
                for cell, endpoint, part in (("BeginX", by_id[child["parent_id"]], "9"), ("EndX", child, "12")):
                    ET.SubElement(connects, xml("Connect"), {"FromSheet": identifier, "FromCell": cell,
                        "FromPart": part, "ToSheet": str(endpoint["line_start"]), "ToCell": "PinX", "ToPart": "3"})
        relationships = lambda values: ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
            f'<Relationship Id="{rid}" Type="{relation_type}{kind}" Target="{target}"/>' for rid, kind, target in values) + '</Relationships>').encode()
        parts = {"[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>',
                 "_rels/.rels": relationships([("doc", "document", "visio/document.xml")]),
                 "visio/document.xml": f'<VisioDocument xmlns="{namespace}"/>'.encode(),
                 "visio/_rels/document.xml.rels": relationships([("pages", "pages", "pages/pages.xml")]),
                 "visio/pages/pages.xml": f'<Pages xmlns="{namespace}" xmlns:r="{R}"><Page ID="0" Name="Derived document outline"><Rel r:id="page"/></Page></Pages>'.encode(),
                 "visio/pages/_rels/pages.xml.rels": relationships([("page", "page", "page1.xml")]),
                 "visio/pages/page1.xml": ET.tostring(content, encoding="utf-8", xml_declaration=True)}
    else:
        raise ValueError("unsupported outline derivative")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, raw in parts.items():
            archive.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), raw)
    return stream.getvalue()


def check_outline(client, case_id, suffix):
    captured = load_binding(case_id)
    evidence = Evidence(captured)
    outline = source_outline(captured, evidence)
    raw = outline_archive(outline, suffix)
    trace = derived_trace(captured, [], {"rendered": False, "geometry": "not claimed"}, raw, suffix)
    mapped = any("original_source" in node for node in outline)
    trace.update(converter="eeas.derived-document-outline.v2" if mapped else "eeas.derived-document-outline.v1", source_outline=outline,
                 selected_complete_source=False, graph_semantics="ATX heading hierarchy only, not engineering topology",
                 heading_policy="level stack, multiple actual roots; no heading inferred from fenced code; no invented root/node/connector")
    if mapped:
        trace.update(graph_semantics="Original document heading hierarchy (ATX/Setext/RST), not engineering topology",
            outline_source_files=sorted({node["original_source"]["path"] for node in outline}),
            outline_file_selection=os.environ.get("EEAS_CAMPAIGN_OUTLINE_FILES"),
            outline_coverage="EXPLICIT_ORIGINAL_FILE_SELECTION" if os.environ.get("EEAS_CAMPAIGN_OUTLINE_FILES") else "ALL_FROZEN_ORIGINAL_BODIES",
            heading_policy="Whole title lines and exact char/byte ranges only; RST adornment styles ranked by first use per original file; bounded single-line Setext; roots reset per file; no generated wrapper or engineering topology")
    evidence.save("derived-format-trace.json", trace)
    parsed, assets = import_derived(client, captured, evidence, raw, "derived-outline." + suffix)
    assert not assets  # No invented upstream drawings or images.
    actual = parsed["graph"]["nodes"]
    assert len(actual) == len(outline)
    matched = {}
    for original in outline:
        identifier = str(original["line_start"]) if suffix == "vsdx" else original["id"]
        node = next(node for node in actual if node["source"]["node_id"] == identifier)
        assert node["text"] == captured["text"][original["char_start"]:original["char_end"]] == original["title"]
        assert original["title"] in "\n".join(parsed["content"].splitlines()[node["line_start"] - 1:node["line_end"]])
        assert node["source"]["part"] == ("visio/pages/page1.xml" if suffix == "vsdx" else "content.json")
        matched[original["id"]] = node
    for original in outline:
        assert matched[original["id"]]["parent_id"] == (matched[original["parent_id"]]["id"] if original["parent_id"] else None)
    expected_edges = {(matched[n["parent_id"]]["id"], matched[n["id"]]["id"]) for n in outline if n["parent_id"]}
    assert {(e["source"], e["target"]) for e in parsed["graph"]["edges"]} == expected_edges
    assert all(e["kind"] == "hierarchy" for e in parsed["graph"]["edges"])
    evidence.save("actual-outline-source-map.json", [{"original": node, "parsed": matched[node["id"]]} for node in outline])
    evidence.observe()


def test_source_vsdx(client):
    check_outline(client, "source-vsdx", "vsdx")


def test_source_xmind(client):
    check_outline(client, "source-xmind", "xmind")
