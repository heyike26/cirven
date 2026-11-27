"""Portable index of the files and approval records in a delivery."""
from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
import stat
from unicodedata import normalize
from zipfile import BadZipFile, ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo
from zlib import error as DeflateError

from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.dynamic_workflow.stage_assertions import FILE_CHECK_METHODS, passing_check
from packages.dynamic_workflow.stage_workspace import MAX_STAGE, read_evidence_file, suite_input_files
from packages.extensions.schema import safe_member

MAX_ARCHIVE_ENTRIES = 8192
MAX_ARCHIVE_DEPTH = 8


def _path_key(path):
    return normalize('NFC',path.casefold())


class DeliveryArchiveReview:
    """Check artifact bytes against upstream file identities before approval."""

    def __init__(self, data_dir, upstream):
        self.versions = defaultdict(set)
        suites = []
        for record in upstream:
            if record.get("kind") == "file":
                self._add_version(record["path"],record["sha256"])
            elif record.get("kind") == "check" and passing_check(record):
                if record.get("method") in FILE_CHECK_METHODS and record.get("path") and record.get("sha256"):
                    self._add_version(record["path"],record["sha256"])
                elif record.get("method") == "container_suite":
                    suites.append(record)
        for path, checksum in suite_input_files(data_dir,suites):
            self._add_version(path,checksum)
        self.remaining = MAX_STAGE
        self.entries = MAX_ARCHIVE_ENTRIES

    def _add_version(self, path, checksum):
        self.versions[_path_key(path)].add(checksum)

    def check_version(self, path, checksum, location):
        versions = self.versions.get(_path_key(path))
        if versions and versions != {checksum}:
            raise ValueError(f"{location} 与已审或已测的文件版本不一致。请恢复原版本，或在开发阶段更新并复测。")

    def members(self, content, location, depth=1):
        if depth > MAX_ARCHIVE_DEPTH:
            raise ValueError(f"{location} 的 ZIP 嵌套层数超出核对范围。")
        files, folders, explicit, result = set(), set(), set(), []
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                self.entries -= len(entries)
                if self.entries < 0:
                    raise ValueError(f"{location} 的 ZIP 成员数量超出核对范围。")
                for info in entries:
                    directory = info.is_dir()
                    path = info.filename.removesuffix('/') if directory else info.filename
                    key = PurePosixPath(_path_key(path))
                    parents = set(key.parents)
                    if (info.filename != info.orig_filename or not safe_member(path) or key in explicit
                            or key in files or parents.intersection(files) or not directory and key in folders):
                        raise ValueError(f"{location} 内存在重复、重叠或无效的成员路径。")
                    explicit.add(key)
                    folders.update(parents)
                    mode = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
                    if mode not in (0,stat.S_IFDIR if directory else stat.S_IFREG):
                        raise ValueError(f"{location} 内存在链接或特殊文件。")
                    if directory:
                        folders.add(key)
                        continue
                    if info.compress_type not in (ZIP_STORED,ZIP_DEFLATED,ZIP_BZIP2):
                        raise ValueError(f"{location} 使用了无法受限核对的 ZIP 压缩格式。")
                    files.add(key)
                    self.remaining -= info.file_size
                    if self.remaining < 0:
                        raise ValueError(f"{location} 的 ZIP 解压体积超出核对范围。")
                    raw = archive.read(info)
                    checksum = sha256(raw).hexdigest()
                    member_location = location + '!/' + path
                    self.check_version(path,checksum,member_location)
                    item = {"path":path,"sha256":checksum,"bytes":len(raw)}
                    if path.casefold().endswith(".zip"):
                        item["members"] = self.members(raw,member_location,depth+1)
                    result.append(item)
        except (BadZipFile,RuntimeError,NotImplementedError,EOFError,DeflateError,OSError) as exc:
            raise ValueError(f"{location} 的 ZIP 内容无法核对。") from exc
        return result


def review_delivery_members(data_dir, artifact, records, upstream):
    sources = approved_delivery_files({"verified_evidence":[*upstream,*records]})
    result = {}
    review = DeliveryArchiveReview(data_dir,upstream)
    for row in artifact["items"]:
        path, checksum = row["path"], row["sha256"]
        review.check_version(path,checksum,path)
        try:
            _, content = read_evidence_file(data_dir,sources[(path,checksum)])
        except (KeyError,OSError,RuntimeError,ValueError) as exc:
            raise ValueError(f"{path} 的交付文件无法核对，请重新生成。") from exc
        if path.casefold().endswith(".zip"):
            result[row["id"]] = review.members(content,path)
    return result


def approved_delivery_files(approval):
    return {(item["path"], item["sha256"]): item
            for item in approval["verified_evidence"] if item["kind"] == "file"}


def receipt_bytes(receipt):
    return (json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')


def _archive_file(archive, path, content, files, folders):
    if not safe_member(path):
        raise Conflict('交付包内存在无法安全解压的文件路径，请逐项下载。')
    key = PurePosixPath(normalize('NFC',path).casefold())
    parents = set(key.parents)
    if key in files or key in folders or parents.intersection(files):
        raise Conflict('交付包内的文件或目录路径重叠，请逐项下载。')
    files.add(key)
    folders.update(parents)
    archive.writestr(ZipInfo('files/'+path), content)


def _files(data_dir, approval, archive):
    sources = approved_delivery_files(approval)
    files, folders = set(), set()
    for row in approval["artifact"]["items"]:
        identity = row["path"], row["sha256"]
        try:
            record = sources[identity]
            _, content = read_evidence_file(data_dir, record)
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            raise Conflict(f'{row["id"]} 的获批文件无法校验，请核对原交付记录。') from exc
        if archive is not None:
            _archive_file(archive, row["path"], content, files, folders)
        metadata = {"item_id": row["id"], "path": row["path"], "sha256": row["sha256"],
                    "bytes": len(content)}
        if row["id"] in approval.get("archive_members",{}):
            metadata["members"] = approval["archive_members"][row["id"]]
        yield metadata


def _testing(approval, latest_checks):
    by_case = {}
    for record in latest_checks(approval["verified_evidence"]):
        by_case.setdefault(record["case_id"], []).append(record)
    for row in approval["artifact"]["items"]:
        records = by_case.get(row["case_id"], [])
        references = set(row["evidence_refs"])
        if (not records or row["status"] != "passed" or
                any(r["id"] not in references or not passing_check(r) for r in records)):
            raise Conflict("已审核的测试依据无法核对，无法生成交付凭证。")
        yield {"case_id": row["case_id"], "result_id": row["id"], "status": "passed",
               "checks": [dict(receipt_id=record["id"], action_id=record.get("action_id"),
                               source=("manual_observation" if record["kind"] == "manual_check" else
                                       "connector_response" if record.get("method") == "mcp_response" else
                                       "automated_check"),
                               **{key:record[key] for key in ("subject","hardware_readback") if record.get(key)})
                          for record in records]}


def _subject_delivery_paths(files, checks):
    subjects = {check["subject"]["sha256"] for check in checks if "subject" in check}
    matches = {}
    def index(entries, prefix=""):
        for file in entries:
            location = prefix+file["path"]
            if file["sha256"] in subjects:
                matches.setdefault(file["sha256"],[location])
            index(file.get("members",[]),location+"!/")
    if subjects:
        index(files)
        for check in checks:
            if "subject" in check:
                check["delivery_members"] = matches.get(check["subject"]["sha256"],[])


def delivery_receipt(data_dir, run, stage, snapshots, latest_checks, *, archive=None):
    definition = run["definition"]
    versions, test_results = [], []
    for source, approval, kind, inputs, binding in snapshots:
        sha = source["approval_sha256"]
        versions.append({"node_id": source["node_id"], "kind": kind,
                         "approval_sha256": sha, "stage_session_id": source["stage_session_id"],
                         "artifact_sha256": digest(approval["artifact"]), "approved_at": approval["at"],
                         "input_approvals": [item["approval_sha256"] for item in inputs],
                         "input_binding": binding})
        if kind == "testing":
            test_results.extend(dict(result, approval_sha256=sha) for result in _testing(approval, latest_checks))
    if not test_results:
        raise Conflict("此交付版本缺少可核对的测试依据，无法生成交付凭证。")
    # Snapshots are ordered from upstream to downstream, ending at the requested delivery.
    files = list(_files(data_dir, snapshots[-1][1], archive))
    _subject_delivery_paths(files,[check for result in test_results for check in result["checks"]])
    receipt = {"schema": "cirven.workflow.delivery-receipt.v1", "run_id": run["id"],
            "definition": {"id": definition["id"], "revision": definition["revision"]},
            "delivery_approval_sha256": stage["approval_sha256"], "files": files,
            "approvals": versions, "test_results": test_results}
    if archive is not None:
        archive.writestr(ZipInfo('receipt.json'), receipt_bytes(receipt))
    return receipt
