"""Bounded, in-memory XMind and VSDX import. No rendering, OCR or network access."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import unicodedata
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile


class DiagramImportError(ValueError):
    """An unsupported, malformed or unsafe document; suitable for an HTTP 422."""


@dataclass(frozen=True)
class DiagramLimits:
    max_archive_bytes: int = 16 * 1024 * 1024
    max_expanded_bytes: int = 64 * 1024 * 1024
    max_member_bytes: int = 8 * 1024 * 1024
    max_content_bytes: int = 8 * 1024 * 1024
    max_entries: int = 2048
    max_nodes: int = 20000
    max_depth: int = 64
    max_compression_ratio: int = 200


@dataclass
class ParsedDocument:
    content: str
    format: str
    outline: dict
    graph: dict
    assets: list[dict]
    warnings: list[str]


def _name(name: str) -> str:
    if (not name or len(name) > 512 or "\\" in name or ":" in name or "%" in name
            or name.startswith("/") or any(ord(c) < 32 for c in name)):
        raise DiagramImportError("Unsafe archive member name")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in name.split("/"):
        if (not part or part in {".", ".."} or part.endswith((" ", "."))
                or part.split(".", 1)[0].upper() in reserved or any(c in '<>"|?*' for c in part)):
            raise DiagramImportError("Unsafe/Windows-ambiguous archive member name")
    return name


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element, name):
    return [child for child in element if _local(child.tag) == name]


def _child(element, name):
    return next(iter(_children(element, name)), None)


def _text(value, label="text"):
    if not isinstance(value, str) or "\x00" in value:
        raise DiagramImportError("Invalid " + label)
    return value


def _element_text(element):
    return "" if element is None else "".join(element.itertext())


def _identifier(value):
    if value is None:
        raise DiagramImportError("Missing diagram node identifier; source anchors cannot be invented")
    if (not isinstance(value, str) or not value or len(value) > 256
            or any(ord(c) < 32 or c in "\x85\u2028\u2029" for c in value)):
        raise DiagramImportError("Invalid diagram node identifier")
    return value


def _resolve(source, target):
    """Resolve an OPC part URI, allowing normal ../media but never leaving the package."""
    if not isinstance(target, str):
        raise DiagramImportError("Invalid relationship target")
    uri = urlsplit(target)
    if uri.scheme or uri.netloc or uri.query or uri.fragment:
        raise DiagramImportError("Internal relationship is not a package part")
    path = unquote(uri.path)
    if "\\" in path or "\x00" in path:
        raise DiagramImportError("Unsafe relationship target")
    parts = [] if path.startswith("/") else source.split("/")[:-1]
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise DiagramImportError("Relationship escapes archive root")
            parts.pop()
        else:
            parts.append(part)
    return _name("/".join(parts))


class _Archive:
    def __init__(self, raw, limits, *, xml_node_limit=None):
        self.limits, self.data, self.xml, self.json = limits, {}, {}, {}
        if xml_node_limit is not None and (type(xml_node_limit) is not int or xml_node_limit <= 0):
            raise DiagramImportError("XML node limit must be a positive integer")
        self.separate_xml_budget = xml_node_limit is not None
        self.xml_node_limit = limits.max_nodes if xml_node_limit is None else xml_node_limit
        self.nodes, self.xml_nodes, self.json_nodes = 0, 0, 0
        if not isinstance(raw, bytes) or len(raw) > limits.max_archive_bytes:
            raise DiagramImportError("Diagram archive exceeds the byte limit")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > limits.max_entries:
                raise DiagramImportError("Empty archive or archive entry limit exceeded")
            aliases, paths, total = {}, {}, 0
            for entry in infos:
                if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise DiagramImportError("Unsupported ZIP compression method")
                name = _name(entry.filename[:-1] if entry.is_dir() else entry.filename)
                canonical = unicodedata.normalize("NFC", name).casefold()
                if canonical in aliases:
                    raise DiagramImportError("Duplicate/case/Unicode archive alias")
                aliases[canonical] = (name, entry.is_dir())
                components = name.split("/")
                for i in range(1, len(components) + 1):
                    prefix = "/".join(components[:i])
                    key = unicodedata.normalize("NFC", prefix).casefold()
                    if key in paths and paths[key] != prefix:
                        raise DiagramImportError("Windows directory case/Unicode alias")
                    paths[key] = prefix
                mode = entry.external_attr >> 16
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                    raise DiagramImportError("Symlink, nonregular or encrypted ZIP member")
                if entry.is_dir():
                    continue
                total += entry.file_size
                if entry.file_size > limits.max_member_bytes or total > limits.max_expanded_bytes:
                    raise DiagramImportError("Archive expanded/member byte limit exceeded")
                if entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * limits.max_compression_ratio:
                    raise DiagramImportError("ZIP compression ratio limit exceeded")
            for name, is_dir in aliases.values():
                for parent in PurePosixPath(name).parents:
                    if str(parent) == ".":
                        continue
                    existing = aliases.get(unicodedata.normalize("NFC", str(parent)).casefold())
                    if existing and not existing[1]:
                        raise DiagramImportError("Archive file/directory conflict")
            # No extract/extractall or paths on disk. Verify every member's CRC and length.
            for entry in infos:
                if entry.is_dir():
                    continue
                with archive.open(entry) as source:
                    payload = source.read(limits.max_member_bytes + 1)
                if len(payload) != entry.file_size or len(payload) > limits.max_member_bytes:
                    raise DiagramImportError("Archive member length mismatch")
                self.data[entry.filename] = payload
        for name, payload in self.data.items():
            if name.lower().endswith((".xml", ".rels")):
                self.xml[name] = self._xml(payload)
            elif name.lower().endswith(".json"):
                self.json[name] = self._json(payload)
            if name.rsplit("/", 1)[-1].casefold() == "vbaproject.bin":
                raise DiagramImportError("Macro package is unsupported; export a macro-free .vsdx")

    def _xml(self, payload):
        if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = payload.decode("utf-16")
        elif payload.startswith(b"<\x00"):
            text = payload.decode("utf-16-le")
        elif payload.startswith(b"\x00<"):
            text = payload.decode("utf-16-be")
        else:
            text = payload.decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, flags=re.IGNORECASE):
            raise DiagramImportError("DTD/entity/external-entity declarations are forbidden")
        depth, root = 0, None
        for event, element in ET.iterparse(io.StringIO(text), events=("start", "end")):
            if event == "start":
                depth += 1
                self.nodes += 1
                self.xml_nodes += 1
                if root is None:
                    root = element
                counted_nodes = self.xml_nodes if self.separate_xml_budget else self.nodes
                if depth > self.limits.max_depth or counted_nodes > self.xml_node_limit:
                    raise DiagramImportError("XML depth/node limit exceeded")
            else:
                depth -= 1
        if root is None:
            raise DiagramImportError("Empty XML document")
        return root

    def _json(self, payload):
        text = payload.decode("utf-8-sig")
        depth, quoted, escaped, units = 0, False, False, 1
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                units += 1
            elif char in "]}":
                depth -= 1
            elif char in ",:":
                units += 1
            counted_nodes = self.json_nodes if self.separate_xml_budget else self.nodes
            if depth > self.limits.max_depth or units + counted_nodes > self.limits.max_nodes:
                raise DiagramImportError("JSON depth/node limit exceeded")
        self.nodes += units
        self.json_nodes += units
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise DiagramImportError("Duplicate JSON key")
                result[key] = value
            return result
        def invalid_constant(value):
            raise DiagramImportError("Non-finite JSON value")
        return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)

    def require_xml(self, name, root_name):
        if name not in self.xml or _local(self.xml[name].tag) != root_name:
            raise DiagramImportError("Missing or malformed required XML part: " + name)
        return self.xml[name]


class _Result:
    def __init__(self, archive):
        self.archive = archive
        self.lines, self.bytes, self.nodes, self.edges, self.warnings = [], 0, [], [], []
        self.node_ids, self.warned = set(), set()
        self.assets = {}
        for name, raw in archive.data.items():
            extension = PurePosixPath(name).suffix.lower()
            if extension in {".png", ".jpg", ".jpeg"}:
                media = "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else (
                    "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None)
                if not media or (extension == ".png") != (media == "image/png"):
                    raise DiagramImportError("Image signature/extension mismatch: " + name)
                self.assets[name] = {"member_path": name, "media_type": media,
                    "sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw),
                    "raw_bytes": raw, "source_anchors": []}
            elif extension in {".svg", ".emf", ".wmf", ".gif", ".webp", ".bin", ".pdf"}:
                self.warn("Unsupported embedded asset retained only in original archive: " + name)

    def warn(self, message):
        if message not in self.warned:
            self.warned.add(message)
            self.warnings.append(message)

    def emit(self, text):
        # Match str.splitlines(), which the document store uses for citations.
        text = re.sub(r"\r\n|[\r\v\f\x1c-\x1e\x85\u2028\u2029]", "\n", _text(text))
        self.bytes += len(text.encode("utf-8")) + 1
        if self.bytes > self.archive.limits.max_content_bytes:
            raise DiagramImportError("Parsed text exceeds content byte limit; no text was truncated")
        self.lines.extend(text.split("\n"))

    def node(self, scope, identifier, part, title, parent=None, kind="topic", extra_text=""):
        if len(self.nodes) >= self.archive.limits.max_nodes:
            raise DiagramImportError("Semantic graph node limit exceeded")
        node_id = scope + "/" + kind + ":" + quote(identifier, safe="")
        if node_id in self.node_ids:
            raise DiagramImportError("Duplicate node ID within sheet/page")
        self.node_ids.add(node_id)
        source = {"part": part, "node_id": identifier, "scope": scope}
        node = {"id": node_id, "text": title, "kind": kind, "parent_id": parent,
                "source": source, "line_start": len(self.lines) + 1, "assets": []}
        self.emit("[source " + json.dumps(source, ensure_ascii=False, sort_keys=True) + "]")
        self.emit(title)
        if extra_text:
            self.emit(extra_text)
        node["line_end"] = len(self.lines)
        self.nodes.append(node)
        if parent:
            self.edges.append({"kind": "hierarchy", "source": parent, "target": node_id, "origin": source})
        return node

    def asset_ref(self, reference, node, part):
        reference = _text(reference, "asset reference")
        if reference.startswith("xap:"):
            member = _resolve("", reference[4:])
        elif urlsplit(reference).scheme or urlsplit(reference).netloc:
            self.warn("External image was not fetched: " + part + " node " + node["source"]["node_id"])
            return
        else:
            member = _resolve(part, reference)
        if member not in self.assets:
            self.warn("Missing/unsupported image reference: " + member)
            return
        node["assets"].append(member)
        self.assets[member]["source_anchors"].append(node["source"])

    def finish(self, format_name, outline):
        for asset in self.assets.values():
            if not asset["source_anchors"]:
                self.warn("Embedded image has no supported node binding: " + asset["member_path"])
        return ParsedDocument("\n".join(self.lines) + "\n", format_name, outline,
            {"nodes": self.nodes, "edges": self.edges}, list(self.assets.values()), self.warnings)


def _json_notes(topic):
    notes = topic.get("notes", {})
    if not isinstance(notes, dict):
        raise DiagramImportError("Invalid topic notes")
    values = []
    if "plain" in notes:
        plain = notes["plain"]
        if not isinstance(plain, dict):
            raise DiagramImportError("Invalid plain notes")
        values.append(_text(plain.get("content", ""), "plain notes content"))
    if "html" in notes:
        # Preserve rich-note text as inert JSON, including all paragraph/span
        # strings; no DOM execution and no guessing undocumented span fields.
        values.append("Rich notes (inert source JSON): " + json.dumps(notes["html"], ensure_ascii=False))
    labels = topic.get("labels", [])
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
        raise DiagramImportError("Invalid topic labels")
    if labels:
        values.append("Labels: " + "; ".join(labels))
    return "\n".join(values)


def _xmind(archive, result):
    json_format = "content.json" in archive.json
    part = "content.json" if json_format else "content.xml"
    if json_format:
        sheets = archive.json[part]
        if isinstance(sheets, dict):
            sheets = sheets.get("sheets")
        if not isinstance(sheets, list) or not sheets:
            raise DiagramImportError("XMind JSON requires nonempty sheets")
        if "content.xml" in archive.data:
            result.warn("Both XMind content parts exist; current content.json is used, content.xml is not merged")
    else:
        root = archive.require_xml(part, "xmap-content")
        sheets = _children(root, "sheet")
        if not sheets:
            raise DiagramImportError("XMind XML contains no sheets")
    result.warn("XMind layout/style/markers are not rendered; images are preserved without OCR")
    outline = {"sheets": []}
    for sheet_index, sheet in enumerate(sheets):
        if json_format and not isinstance(sheet, dict):
            raise DiagramImportError("Invalid XMind sheet")
        sheet_id = _identifier(sheet.get("id"))
        name = _text(sheet.get("title", "") if json_format else _element_text(_child(sheet, "title")))
        scope, topics = "sheet:" + str(sheet_index), {}
        record = {"id": sheet_id, "name": name, "scope": scope, "source": {"part": part, "node_id": sheet_id}, "topics": []}
        outline["sheets"].append(record)
        result.emit("Sheet " + str(sheet_index + 1) + ": " + name)

        def walk(topic, parent, role, depth):
            if depth > archive.limits.max_depth or json_format and not isinstance(topic, dict):
                raise DiagramImportError("Invalid/deep XMind topic")
            identifier = _identifier(topic.get("id"))
            if identifier in topics:
                raise DiagramImportError("Duplicate topic ID within sheet")
            title = _text(topic.get("title", "") if json_format else _element_text(_child(topic, "title")))
            notes = _json_notes(topic) if json_format else _element_text(_child(topic, "notes"))
            node = result.node(scope, identifier, part, title, parent, extra_text=notes)
            topics[identifier] = node["id"]
            node["role"] = role
            tree = {"id": node["id"], "children": []}
            href = topic.get("href") if json_format else next((value for key, value in topic.attrib.items() if _local(key) == "href"), None)
            if href:
                node["href"] = _text(href, "topic link")
                result.warn("Topic hyperlink preserved as data, never followed: " + scope + "/" + identifier)
            if json_format:
                image = topic.get("image")
                if image is not None:
                    if not isinstance(image, dict) or "src" not in image:
                        raise DiagramImportError("Invalid topic image")
                    result.asset_ref(image["src"], node, part)
                groups = topic.get("children", {})
                if not isinstance(groups, dict):
                    raise DiagramImportError("Invalid topic children")
                for group, children in groups.items():
                    if not isinstance(children, list):
                        raise DiagramImportError("Invalid topic group")
                    for child in children:
                        tree["children"].append(walk(child, node["id"], group, depth + 1))
                for ignored in ("boundaries", "summaries", "extensions"):
                    if topic.get(ignored):
                        result.warn("Unsupported XMind " + ignored + " retained only in original archive: " + identifier)
            else:
                for image in [*_children(topic, "image"), *_children(topic, "img")]:
                    src = next((value for key, value in image.attrib.items() if _local(key) == "src"), None)
                    if src:
                        result.asset_ref(src, node, part)
                    else:
                        result.warn("Image element without source: " + identifier)
                for group in _children(_child(topic, "children") or [], "topics"):
                    for child in _children(group, "topic"):
                        tree["children"].append(walk(child, node["id"], group.get("type", "attached"), depth + 1))
                for ignored in ("boundaries", "summaries", "extensions"):
                    if _child(topic, ignored) is not None:
                        result.warn("Unsupported XMind " + ignored + " retained only in original archive: " + identifier)
            return tree

        roots = [sheet.get("rootTopic")] if json_format else _children(sheet, "topic")
        if not roots or roots[0] is None:
            raise DiagramImportError("XMind sheet has no root topic")
        for topic in roots:
            record["topics"].append(walk(topic, None, "root", 1))
        relationships = sheet.get("relationships", []) if json_format else _children(_child(sheet, "relationships") or [], "relationship")
        if not isinstance(relationships, list):
            raise DiagramImportError("Invalid XMind relationships")
        for index, relation in enumerate(relationships):
            if json_format and not isinstance(relation, dict):
                raise DiagramImportError("Invalid XMind relationship")
            end1 = relation.get("end1Id") if json_format else relation.get("end1")
            end2 = (relation.get("end2Id") or relation.get("end2id")) if json_format else relation.get("end2")
            label = _text(relation.get("title", "") if json_format else _element_text(_child(relation, "title")))
            relation_id = _identifier(relation.get("id"))
            source = {"part": part, "node_id": relation_id, "scope": scope}
            edge = {"kind": "relationship", "source": topics.get(end1), "target": topics.get(end2),
                    "end1_id": end1, "end2_id": end2, "text": label, "origin": source}
            result.edges.append(edge)
            result.emit("Relationship " + json.dumps(source, ensure_ascii=False) + ": " + label)
            if not edge["source"] or not edge["target"]:
                result.warn("Unresolved XMind relationship: " + scope + "/" + relation_id)
    return result.finish("xmind-json" if json_format else "xmind-xml", outline)


def _rels(archive, source, result):
    path = (str(PurePosixPath(source).parent) + "/_rels/" + PurePosixPath(source).name + ".rels") if source else "_rels/.rels"
    path = path.removeprefix("./")
    if path not in archive.xml:
        return {}
    root = archive.require_xml(path, "Relationships")
    relations = {}
    for item in _children(root, "Relationship"):
        identifier = _identifier(item.get("Id"))
        if not identifier or identifier in relations:
            raise DiagramImportError("Missing/duplicate OPC relationship ID")
        kind, target = item.get("Type", ""), item.get("Target", "")
        if item.get("TargetMode") == "External":
            result.warn("External OPC relationship was not followed: " + path + "#" + identifier)
            relations[identifier] = {"type": kind, "target": None, "external": True}
        else:
            relations[identifier] = {"type": kind, "target": _resolve(source, target), "external": False}
    return relations


def _type_target(relations, kind):
    found = [item["target"] for item in relations.values() if item["type"].endswith("/" + kind) and item["target"]]
    if len(found) != 1:
        raise DiagramImportError("Required unique OPC " + kind + " relationship is missing")
    return found[0]


def _vsdx(archive, result):
    types = archive.require_xml("[Content_Types].xml", "Types")
    if any("macroenabled" in node.get("ContentType", "").lower() for node in types):
        raise DiagramImportError("Macro-enabled Visio package is unsupported")
    document = _type_target(_rels(archive, "", result), "document")
    archive.require_xml(document, "VisioDocument")
    pages = _type_target(_rels(archive, document, result), "pages")
    index = archive.require_xml(pages, "Pages")
    page_relations = _rels(archive, pages, result)
    page_entries = _children(index, "Page")
    if not page_entries:
        raise DiagramImportError("VSDX contains no pages")
    result.warn("Visio geometry/formulas/styles/master inheritance are not rendered; connector endpoints are data, not inferred arrow direction; no OCR")
    outline = {"pages": []}
    visited = set()
    for page_index, page in enumerate(page_entries):
        page_id = _identifier(page.get("ID"))
        rel = _child(page, "Rel")
        rid = None if rel is None else next((value for key, value in rel.attrib.items() if _local(key) == "id"), None)
        relation = page_relations.get(rid)
        if not relation or not relation["type"].endswith("/page") or not relation["target"]:
            raise DiagramImportError("VSDX page has no internal page-content relationship")
        part = relation["target"]
        if part in visited:
            raise DiagramImportError("Duplicate page-content part")
        visited.add(part)
        content = archive.require_xml(part, "PageContents")
        scope, shapes = "page:" + str(page_index), {}
        record = {"id": page_id, "name": page.get("Name", page.get("NameU", "")), "scope": scope,
                  "source": {"part": part, "node_id": page_id}, "shapes": []}
        outline["pages"].append(record)
        result.emit("Page " + str(page_index + 1) + ": " + record["name"])
        page_assets = _rels(archive, part, result)

        def walk(shape, parent, depth):
            if depth > archive.limits.max_depth:
                raise DiagramImportError("Visio shape depth limit exceeded")
            identifier = _identifier(shape.get("ID"))
            if identifier in shapes:
                raise DiagramImportError("Duplicate Visio shape ID within page")
            text = _element_text(_child(shape, "Text"))
            node = result.node(scope, identifier, part, text, parent, "shape")
            shapes[identifier] = node
            node["name"] = shape.get("Name", shape.get("NameU", ""))
            node["cells"] = {cell.get("N", ""): {"value": cell.get("V"), "formula": cell.get("F")} for cell in _children(shape, "Cell")}
            if shape.get("Master") or shape.get("MasterShape"):
                result.warn("Unresolved inherited master properties: " + part + " Shape " + identifier)
            foreign = _child(shape, "ForeignData")
            if foreign is not None:
                binding = _child(foreign, "Rel")
                image_id = None if binding is None else next((value for key, value in binding.attrib.items() if _local(key) == "id"), None)
                image = page_assets.get(image_id)
                if image and image["target"]:
                    result.asset_ref("/" + image["target"], node, part)
                else:
                    result.warn("Missing/external foreign-data binding: " + part + " Shape " + identifier)
            tree = {"id": node["id"], "children": []}
            for child in _children(_child(shape, "Shapes") or [], "Shape"):
                tree["children"].append(walk(child, node["id"], depth + 1))
            return tree

        for shape in _children(_child(content, "Shapes") or [], "Shape"):
            record["shapes"].append(walk(shape, None, 1))
        endpoints = {}
        for index, connection in enumerate(_children(_child(content, "Connects") or [], "Connect")):
            frm, to = connection.get("FromSheet"), connection.get("ToSheet")
            source, target = shapes.get(frm), shapes.get(to)
            result.edges.append({"kind": "attachment", "source": source["id"] if source else None,
                "target": target["id"] if target else None, "from_sheet": frm, "to_sheet": to,
                "from_cell": connection.get("FromCell"), "to_cell": connection.get("ToCell"),
                "from_part": connection.get("FromPart"), "to_part": connection.get("ToPart"),
                "raw_attributes": dict(connection.attrib),
                "origin": {"part": part, "node_id": "Connect:" + str(index), "scope": scope}})
            if not source or not target:
                result.warn("Unresolved connector endpoint: " + part + " Connect " + str(index))
            elif connection.get("FromCell") in {"BeginX", "EndX"}:
                source["kind"] = "connector"
                endpoints.setdefault(frm, {}).setdefault(connection.get("FromCell"), []).append(target["id"])
        for identifier, ends in endpoints.items():
            if len(ends.get("BeginX", [])) == len(ends.get("EndX", [])) == 1:
                result.edges.append({"kind": "connector", "source": ends["BeginX"][0], "target": ends["EndX"][0],
                    "connector_id": shapes[identifier]["id"], "origin": shapes[identifier]["source"]})
            else:
                result.warn("Incomplete/ambiguous BeginX/EndX connector: " + part + " Shape " + identifier)
    unvisited = [name for name, root in archive.xml.items() if _local(root.tag) == "PageContents" and name not in visited]
    for part in unvisited:
        result.warn("Unreferenced page-content part was not merged: " + part)
    return result.finish("visio-vsdx", outline)


def parse_diagram(filename: str, raw: bytes, *, limits: DiagramLimits | None = None) -> ParsedDocument:
    """Parse supported diagram bytes, raising DiagramImportError instead of partial success."""
    if not isinstance(filename, str) or not filename or len(filename) > 240 or any(char in filename for char in ("/", "\\", ":", "\x00")):
        raise DiagramImportError("Filename must be a plain filename")
    extension = PurePosixPath(filename).suffix.lower()
    if extension in {".vsd", ".vsdm", ".vdx", ".vssx", ".vstx"}:
        raise DiagramImportError("Unsupported legacy/macro/template Visio format; export a macro-free .vsdx drawing")
    if extension not in {".xmind", ".vsdx"}:
        raise DiagramImportError("Supported diagram formats are .xmind and .vsdx")
    limits = limits or DiagramLimits()
    if any(not isinstance(value, int) or value <= 0 for value in vars(limits).values()):
        raise DiagramImportError("Diagram limits must be positive integers")
    try:
        archive = _Archive(raw, limits)
        result = _Result(archive)
        return _xmind(archive, result) if extension == ".xmind" else _vsdx(archive, result)
    except DiagramImportError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, UnicodeError, json.JSONDecodeError, RecursionError,
            RuntimeError, NotImplementedError, KeyError, TypeError, ValueError, OSError) as exception:
        raise DiagramImportError("Malformed/unsupported diagram archive") from exception
