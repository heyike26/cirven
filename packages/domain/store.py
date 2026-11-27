from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid
import hashlib
import json
import re
from packages.domain.document_chunks import chunks, metadata, line_starts, matching_excerpt
from packages.domain.document_import import parse_document, DocumentImportError, SUPPORTED_SUFFIXES, image_metadata


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class InvalidInput(Exception):
    pass


def validate_document_name(filename: str):
    if not filename or len(filename) > 200 or any(c in filename for c in ("/", "\\", ":", "\x00")):
        raise InvalidInput("Filename must be a plain filename without directories")
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise InvalidInput("Supported: UTF-8 Markdown/TXT, Word .docx, PowerPoint .pptx, PDF, PNG/JPEG, Visio .vsdx and XMind .xmind; export legacy .doc/.ppt/.vsd to macro-free .docx/.pptx/.vsdx")


class Store:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self):
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
            applied = {r[0] for r in connection.execute("SELECT version FROM schema_migrations")}
            migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
            names = [path.name for path in migrations]
            # Prefix equality alone can accept a source tree with a numeric hole.
            # Reject missing/duplicate migration numbers before touching old data.
            if any(name[:4] != f"{index:03d}_" for index, name in enumerate(names, start=1)):
                raise Conflict("Migration sources must form a complete contiguous sequence; restore missing originals")
            if applied - set(names) or applied != set(names[:len(applied)]):
                raise Conflict("Database migration history is incompatible with this code; restore a compatible stopped snapshot")
            for path in migrations:
                if path.name in applied:
                    continue
                statement = ""
                for line in path.read_text().splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError("Incomplete migration statement")
                connection.execute("INSERT INTO schema_migrations VALUES (?)", (path.name,))

    def create_project(self, name: str, description: str, *, operation_id: str | None = None) -> dict:
        if operation_id is not None and (not isinstance(operation_id, str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id)):
            raise InvalidInput('Project operation identity is invalid')
        checksum = hashlib.sha256(json.dumps({'name': name, 'description': description},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        row = {"id": str(uuid.uuid4()), "name": name, "description": description,
               "created_at": datetime.now(timezone.utc).isoformat()}
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if operation_id is not None:
                previous = connection.execute('SELECT * FROM project_operations WHERE operation_id=?', (operation_id,)).fetchone()
                if previous is not None:
                    if previous['request_sha256'] != checksum:
                        raise Conflict('Project operation is already bound to another input')
                    return self._project_operation_result(previous)
            connection.execute("INSERT INTO projects VALUES (:id,:name,:description,:created_at)", row)
            if operation_id is not None:
                raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                connection.execute('INSERT INTO project_operations VALUES (?,?,?,?,?)',
                    (operation_id, checksum, row['id'], raw, hashlib.sha256(raw.encode()).hexdigest()))
        return row

    @staticmethod
    def _project_operation_result(record):
        raw = record['result_json']
        if hashlib.sha256(raw.encode()).hexdigest() != record['result_sha256']:
            raise Conflict('Project creation receipt integrity changed')
        try:
            result = json.loads(raw)
            expected = hashlib.sha256(json.dumps({'name': result['name'], 'description': result['description']},
                ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if result['id'] != record['project_id'] or expected != record['request_sha256']:
                raise ValueError()
            return result
        except (ValueError, KeyError, TypeError):
            raise Conflict('Project creation receipt is invalid') from None

    def project_operation(self, operation_id: str) -> dict:
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise InvalidInput('Project operation identity is invalid')
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM project_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            raise NotFound('Project creation operation has not been observed; it was not repeated')
        return {'operation_id': operation_id, 'request_sha256': row['request_sha256'],
                'project': self._project_operation_result(row)}

    def list_projects(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(r) for r in connection.execute("SELECT * FROM projects ORDER BY created_at,id")]

    def project(self, project_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise NotFound("Project does not exist")
        return dict(row)

    def save_document(self, project_id: str, filename: str, raw: bytes, source: str,
                      document_id: str | None = None, expected_revision: int = 0) -> dict:
        self.project(project_id)
        validate_document_name(filename)
        try:
            parsed = parse_document(filename, raw)
            assets = []
            if len(parsed.assets) > 200:
                raise DocumentImportError("Document exceeds the 200 image asset limit")
            for asset in parsed.assets:
                info = image_metadata(asset["raw_bytes"])
                asset_id = hashlib.sha256((asset["member_path"] + "\x00" + info["sha256"]).encode()).hexdigest()
                assets.append({**asset, **info, "asset_id": asset_id})
        except DocumentImportError as exception:
            raise InvalidInput(str(exception)) from exception
        text = parsed.content
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if document_id:
                old = connection.execute("SELECT * FROM documents WHERE id=? AND project_id=? AND deleted_at IS NULL",
                                         (document_id, project_id)).fetchone()
                if not old:
                    raise NotFound("Document does not exist in this project")
                if old["current_revision"] != expected_revision:
                    raise Conflict("Document changed; reload its current revision")
                revision = old["current_revision"] + 1
                connection.execute("UPDATE documents SET current_revision=?,filename=? WHERE id=?",
                                   (revision, filename, document_id))
                self._invalidate_source(connection, project_id, document_id)
            else:
                document_id = str(uuid.uuid4())
                revision = 1
                connection.execute("INSERT INTO documents VALUES (?,?,?,?,NULL,?)",
                                   (document_id, project_id, filename, revision, now))
            digest = hashlib.sha256(raw).hexdigest()
            connection.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)",
                               (document_id, revision, digest, raw, text, source, now))
            connection.execute("INSERT INTO document_parses VALUES (?,?,?,?,?,?,?,?)",
                               (document_id, revision, parsed.format, "eeas.document-import.v1",
                                hashlib.sha256(text.encode()).hexdigest(), json.dumps({**parsed.outline,
                                    "asset_sources": {asset["asset_id"]: asset.get("source_anchors", []) for asset in assets}}, ensure_ascii=False),
                                json.dumps(parsed.graph, ensure_ascii=False), json.dumps(parsed.warnings, ensure_ascii=False)))
            for asset in assets:
                connection.execute("INSERT INTO document_assets VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (document_id, revision, asset["asset_id"], asset["member_path"], asset["media_type"], asset["sha256"],
                     asset["byte_length"], asset["width"], asset["height"], asset["raw_bytes"]))
        return {"id": document_id, "project_id": project_id, "filename": filename,
                "revision": revision, "sha256": digest, "source": source,
                "byte_length": len(raw), "created_at": now, **metadata(text),
                "format": parsed.format, "asset_count": len(assets), "warnings": parsed.warnings}

    def documents(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.connect() as connection:
            rows = connection.execute("""SELECT d.id,d.project_id,d.filename,d.current_revision AS revision,
                v.sha256,v.source,length(v.raw_bytes) AS byte_length,v.created_at,v.content
                FROM documents d JOIN document_versions v
                ON v.document_id=d.id AND v.revision=d.current_revision
                WHERE d.project_id=? AND d.deleted_at IS NULL ORDER BY d.created_at,d.id""", (project_id,))
            result = []
            for row in rows:
                item = dict(row)
                item.update(metadata(item.pop("content")))
                details = self._parse_metadata(connection, item["id"], item["revision"])
                item.update({"format": details.get("format", Path(item["filename"]).suffix[1:]),
                             "warnings": details.get("warnings", []),
                             "asset_count": connection.execute("SELECT count(*) FROM document_assets WHERE document_id=? AND revision=?",
                                                              (item["id"], item["revision"])).fetchone()[0]})
                result.append(item)
            return result

    def document(self, project_id: str, document_id: str, revision: int | None = None) -> dict:
        with self.connect() as connection:
            doc = connection.execute("SELECT * FROM documents WHERE id=? AND project_id=? AND deleted_at IS NULL",
                                     (document_id, project_id)).fetchone()
            if doc is None:
                raise NotFound("Document does not exist in this project")
            revision = doc["current_revision"] if revision is None else revision
            row = connection.execute("SELECT * FROM document_versions WHERE document_id=? AND revision=?",
                                     (document_id, revision)).fetchone()
            if row is None:
                raise NotFound("Document revision does not exist")
            return {"id": document_id, "project_id": project_id, "filename": doc["filename"],
                    "current_revision": doc["current_revision"], **dict(row),
                    **self._parse_metadata(connection, document_id, revision)}

    @staticmethod
    def _parse_metadata(connection, document_id, revision):
        row = connection.execute("SELECT * FROM document_parses WHERE document_id=? AND revision=?",
                                 (document_id, revision)).fetchone()
        if row is None:
            return {}
        return {"format": row["format"], "parser_version": row["parser_version"],
                "content_sha256": row["content_sha256"], "outline": json.loads(row["outline"]),
                "graph": json.loads(row["graph"]), "warnings": json.loads(row["warnings"])}

    def document_assets(self, project_id, document_id, revision=None):
        document = self.document(project_id, document_id, revision)
        with self.connect() as connection:
            return self._document_asset_metadata(connection, document_id, document["revision"],
                                                 document.get("outline", {}))

    @staticmethod
    def _document_asset_metadata(connection, document_id, revision, outline):
        rows = [dict(row) for row in connection.execute("""SELECT asset_id,member_path,media_type,sha256,byte_length,width,height
                FROM document_assets WHERE document_id=? AND revision=? ORDER BY member_path,asset_id""",
                (document_id, revision))]
        anchors = outline.get("asset_sources", {})
        return [{**row, "source_anchors": anchors.get(row["asset_id"], [])} for row in rows]

    def document_receipt(self, project_id, document_id, revision):
        document = self.document(project_id, document_id, revision)
        content_sha256 = hashlib.sha256(document["content"].encode()).hexdigest()
        if document.get("content_sha256", content_sha256) != content_sha256:
            raise Conflict("Derived document text integrity check failed")
        return self._document_parse_receipt(document, content_sha256,
                                            self.document_assets(project_id, document_id, revision))

    @staticmethod
    def _document_parse_receipt(parsed, content_sha256, assets):
        manifest = {key: parsed.get(key) for key in ("format", "parser_version", "outline", "graph", "warnings")}
        manifest.update(content_sha256=content_sha256, assets=assets)
        digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"content_sha256": content_sha256, "parse_manifest_sha256": digest}

    def document_asset(self, project_id, document_id, revision, asset_id):
        document = self.document(project_id, document_id, revision)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM document_assets WHERE document_id=? AND revision=? AND asset_id=?",
                                     (document_id, document["revision"], asset_id)).fetchone()
        if row is None:
            raise NotFound("Image asset does not exist in this project and document revision")
        result = dict(row)
        if len(result["raw_bytes"]) != result["byte_length"] or hashlib.sha256(result["raw_bytes"]).hexdigest() != result["sha256"]:
            raise Conflict("Stored image asset integrity check failed")
        result["source_anchors"] = document.get("outline", {}).get("asset_sources", {}).get(asset_id, [])
        return result

    def delete_document(self, project_id: str, document_id: str):
        with self.connect() as connection:
            result = connection.execute("UPDATE documents SET deleted_at=? WHERE id=? AND project_id=? AND deleted_at IS NULL",
                                        (datetime.now(timezone.utc).isoformat(), document_id, project_id))
            if result.rowcount != 1:
                raise NotFound("Document does not exist in this project")
            self._invalidate_source(connection, project_id, document_id)

    def document_chunks(self, project_id, document_id, revision=None, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise InvalidInput("Chunk offset must be nonnegative and limit between 1 and 50")
        document = self.document(project_id, document_id, revision)
        parts = chunks(document["content"])
        selected = [{**part, "chunk_id": f"{document_id}:v{document['revision']}:{part['index']}"}
                    for part in parts[offset:offset + limit]]
        return {"document_id": document_id, "revision": document["revision"], "sha256": document["sha256"],
                "total_characters": len(document["content"]), "total_chunks": len(parts),
                "offset": offset, "next_offset": offset + len(selected) if offset + len(selected) < len(parts) else None,
                "chunks": selected}

    def document_content(self, project_id, document_id, revision=None, char_start=0, char_limit=8000):
        if type(char_start) is not int or char_start < 0 or type(char_limit) is not int or not 1 <= char_limit <= 32000:
            raise InvalidInput("Content offset must be nonnegative and character limit between 1 and 32000")
        document = self.document(project_id, document_id, revision)
        content = document["content"]
        if char_start > len(content):
            raise InvalidInput("Content offset is beyond the end of this revision")
        end = min(char_start + char_limit, len(content))
        starts = line_starts(content)
        from bisect import bisect_right
        return {"document_id": document_id, "revision": document["revision"], "sha256": document["sha256"],
                "content": content[char_start:end], "char_start": char_start, "char_end": end,
                "line_start": bisect_right(starts, char_start) if char_start < end else None,
                "line_end": bisect_right(starts, end - 1) if char_start < end else None,
                "total_characters": len(content), "next_char_start": end if end < len(content) else None}

    def document_context(self, project_id, document_id, revision, chunk_indexes=None,
                         char_budget=120000, byte_budget=220000):
        document = self.document(project_id, document_id, revision)
        parts = chunks(document["content"])
        indexes = list(range(len(parts))) if chunk_indexes is None else chunk_indexes
        if (not isinstance(indexes, list) or any(type(i) is not int or i < 0 or i >= len(parts) for i in indexes)
                or len(set(indexes)) != len(indexes)):
            raise InvalidInput("Context chunks must be unique indexes from this exact document revision")
        if (type(char_budget) is not int or not 1 <= char_budget <= 200000 or
                type(byte_budget) is not int or not 1 <= byte_budget <= 240000):
            raise InvalidInput("Context budgets must be within 200000 characters / 240000 UTF-8 bytes")
        selected = [parts[i] for i in sorted(indexes)]
        characters = sum(len(p["content"]) for p in selected)
        byte_length = sum(len(p["content"].encode()) for p in selected)
        if characters > char_budget or byte_length > byte_budget:
            raise InvalidInput("Selected document chunks exceed the context budget; choose fewer chunks. No text was truncated")
        result = {"document_id": document_id, "revision": document["revision"], "sha256": document["sha256"],
                  "parse_receipt": self.document_receipt(project_id, document_id, revision),
                  "total_characters": len(document["content"]), "total_chunks": len(parts),
                  "included_chunks": [p["index"] for p in selected], "omitted_chunk_count": len(parts) - len(selected),
                  "coverage": "COMPLETE" if len(selected) == len(parts) else "PARTIAL_EXPLICIT_SELECTION",
                  "included_characters": characters, "included_utf8_bytes": byte_length,
                  "chunks": selected, "truncated": False}
        result["context_sha256"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                                            separators=(",", ":")).encode()).hexdigest()
        return result

    def search(self, project_id: str, keyword: str, document_id: str | None = None,
               revision: int | None = None) -> list[dict]:
        return self.search_page(project_id, keyword, document_id, revision)["hits"]

    def search_page(self, project_id, keyword, document_id=None, revision=None, offset=0, limit=50):
        self.project(project_id)
        if not keyword.strip() or len(keyword) > 200:
            raise InvalidInput("Search keyword must contain 1 to 200 characters")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise InvalidInput("Search offset must be nonnegative and limit between 1 and 50")
        with self.connect() as connection:
            query = """SELECT d.id,d.filename,v.revision,v.sha256,v.content,v.source
                       FROM documents d JOIN document_versions v ON v.document_id=d.id
                       WHERE d.project_id=? AND d.deleted_at IS NULL"""
            params = [project_id]
            if document_id:
                query += " AND d.id=?"
                params.append(document_id)
            if revision is not None:
                query += " AND v.revision=?"
                params.append(revision)
            else:
                query += " AND v.revision=d.current_revision"
            rows = connection.execute(query + " ORDER BY d.id,v.revision", params)
            hits, matches = [], 0
            for row in rows:
                position = 0
                for number, physical_line in enumerate(row["content"].splitlines(keepends=True), 1):
                    line = physical_line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
                    excerpt = matching_excerpt(line, keyword)
                    if excerpt is not None:
                        matches += 1
                        if matches <= offset:
                            position += len(physical_line)
                            continue
                        if len(hits) == limit:
                            return {"hits": hits, "offset": offset, "next_offset": offset + limit}
                        for key in ("excerpt_char_start", "excerpt_char_end", "match_char_start", "match_char_end"):
                            excerpt[key] += position
                        hits.append({"document_id": row["id"], "filename": row["filename"],
                                     "revision": row["revision"], "sha256": row["sha256"],
                                     "line_start": number, "line_end": number,
                                     **excerpt, "source": row["source"]})
                    position += len(physical_line)
        return {"hits": hits, "offset": offset, "next_offset": None}

    def _invalidate_source(self, connection, project_id, document_id):
        rows = connection.execute("""SELECT p.id,v.payload FROM planning_tasks p
            JOIN requirement_versions v ON v.requirement_id=p.requirement_id AND v.revision=p.requirement_revision
            WHERE p.project_id=?""", (project_id,)).fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if any(ref["document_id"] == document_id for item in payload["requirements"] for ref in item["source_refs"]):
                connection.execute("UPDATE planning_tasks SET review_status='NEEDS_REVIEW' WHERE id=?", (row["id"],))

    def save_requirements(self, project_id, payload, requirement_id=None, expected_revision=0):
        self.project(project_id)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in payload["requirements"]:
                for ref in item["source_refs"]:
                    document = connection.execute("""SELECT v.sha256,v.content FROM document_versions v
                        JOIN documents d ON d.id=v.document_id
                        WHERE d.project_id=? AND d.id=? AND d.deleted_at IS NULL AND v.revision=?""",
                        (project_id, ref["document_id"], ref["revision"])).fetchone()
                    if not document or document["sha256"] != ref["sha256"]:
                        raise InvalidInput("Source reference is missing, belongs to another project, or has a mismatched hash")
                    if not 1 <= ref["line_start"] <= ref["line_end"] <= len(document["content"].splitlines()):
                        raise InvalidInput("Source line range is invalid")
            if requirement_id:
                old = connection.execute("SELECT * FROM requirement_sets WHERE id=? AND project_id=?",
                                         (requirement_id, project_id)).fetchone()
                if not old:
                    raise NotFound("Requirements do not exist in this project")
                if old["current_revision"] != expected_revision:
                    raise Conflict("Requirements changed; reload the current revision")
                revision = expected_revision + 1
                connection.execute("UPDATE requirement_sets SET current_revision=? WHERE id=?", (revision, requirement_id))
                connection.execute("UPDATE planning_tasks SET review_status='NEEDS_REVIEW' WHERE requirement_id=?",
                                   (requirement_id,))
            else:
                requirement_id, revision = str(uuid.uuid4()), 1
                connection.execute("INSERT INTO requirement_sets VALUES (?,?,?)", (requirement_id, project_id, revision))
            connection.execute("INSERT INTO requirement_versions VALUES (?,?,?,?,?)",
                (requirement_id, revision, canonical, digest, datetime.now(timezone.utc).isoformat()))
        return self.requirements(project_id, requirement_id, revision)

    def requirements(self, project_id, requirement_id, revision=None):
        with self.connect() as connection:
            head = connection.execute("SELECT * FROM requirement_sets WHERE id=? AND project_id=?",
                                      (requirement_id, project_id)).fetchone()
            if not head:
                raise NotFound("Requirements do not exist in this project")
            revision = head["current_revision"] if revision is None else revision
            row = connection.execute("SELECT * FROM requirement_versions WHERE requirement_id=? AND revision=?",
                                     (requirement_id, revision)).fetchone()
            if not row:
                raise NotFound("Requirement revision does not exist")
            payload = json.loads(row["payload"])
            sources, has_refs = [], False
            for item in payload["requirements"]:
                for ref in item["source_refs"]:
                    has_refs = True
                    document = connection.execute("SELECT * FROM documents WHERE id=? AND project_id=?",
                                                  (ref["document_id"], project_id)).fetchone()
                    status = "DELETED" if not document or document["deleted_at"] else (
                        "CURRENT" if document["current_revision"] == ref["revision"] else "SUPERSEDED")
                    sources.append({**ref, "status": status})
            source_status = "STALE" if any(r["status"] != "CURRENT" for r in sources) else (
                "CURRENT" if has_refs and all(item["source_refs"] for item in payload["requirements"]) else "UNSOURCED")
            return {"id": requirement_id, "project_id": project_id, "revision": revision,
                    "current_revision": head["current_revision"], "payload": payload,
                    "sha256": row["sha256"], "created_at": row["created_at"],
                    "source_status": source_status, "sources": sources}

    def list_requirements(self, project_id):
        self.project(project_id)
        with self.connect() as connection:
            ids = [r[0] for r in connection.execute("SELECT id FROM requirement_sets WHERE project_id=? ORDER BY id", (project_id,))]
        return [self.requirements(project_id, key) for key in ids]

    def plan(self, project_id, requirement_id, revision):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            head = self.requirements(project_id, requirement_id, revision)
            if head["current_revision"] != revision or head["source_status"] == "STALE":
                raise Conflict("Requirements or sources changed; review the latest revision")
            if not head["payload"]["confirmed"]:
                raise InvalidInput("Confirm requirements before planning")
            for item in head["payload"]["requirements"]:
                acceptance = [a for a in head["payload"]["acceptance"] if item["id"] in a["requirement_ids"]]
                if not acceptance:
                    raise InvalidInput("Each planned requirement needs an acceptance mapping")
                text = json.dumps(acceptance, ensure_ascii=False, sort_keys=True)
                connection.execute("INSERT OR IGNORE INTO planning_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), project_id, requirement_id, revision, item["id"], item["text"],
                     head["sha256"], text, hashlib.sha256(text.encode()).hexdigest(), "DRAFT"))
        return self.planning_tasks(project_id)

    def planning_tasks(self, project_id):
        self.project(project_id)
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM planning_tasks WHERE project_id=? ORDER BY id", (project_id,))]
