#!/usr/bin/env python3
"""Generate an original, intentionally failing toy repository, not business data."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

INITIAL = '''"""Synthetic EEAS fixture, intentionally incomplete."""

def parse_samples(text: str) -> list[int]:
    return [int(text)]
'''
REFERENCE = '''"""Reference used only for fixture self-validation."""

def parse_samples(text: str) -> list[int]:
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not text.strip():
        return []
    fields = text.split(",")
    if any(not field.strip() for field in fields):
        raise ValueError("empty field")
    return [int(field.strip()) for field in fields]
'''
TESTS = '''"""Synthetic acceptance tests; candidate root is supplied by the Runner."""
import importlib.util
import os
import unittest
from pathlib import Path

root = Path(os.environ["EEAS_CANDIDATE_ROOT"]).resolve()
spec = importlib.util.spec_from_file_location("candidate_samples", root / "src" / "samples.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Candidate module could not be loaded")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ParseSamplesTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(module.parse_samples("7"), [7])
    def test_many(self):
        self.assertEqual(module.parse_samples("1,2,3"), [1, 2, 3])
    def test_whitespace(self):
        self.assertEqual(module.parse_samples(" 1, 2 ,\\n3 "), [1, 2, 3])
    def test_signs(self):
        self.assertEqual(module.parse_samples("-3,+4,0"), [-3, 4, 0])
    def test_empty(self):
        self.assertEqual(module.parse_samples(""), [])
        self.assertEqual(module.parse_samples(" \\n "), [])
    def test_invalid(self):
        with self.assertRaises(ValueError):
            module.parse_samples("1,abc")
    def test_empty_field(self):
        for item in ("1,,2", ",1", "1,", " , "):
            with self.subTest(item=item), self.assertRaises(ValueError):
                module.parse_samples(item)
    def test_wrong_type(self):
        for item in (None, 12, [], True):
            with self.subTest(item=item), self.assertRaises(TypeError):
                module.parse_samples(item)

if __name__ == "__main__":
    unittest.main(verbosity=2)
'''
BRIEF = '''# SYNTHETIC / 非企业任务

在 repo/src/samples.py 实现 parse_samples(text: str) -> list[int]。
逗号分隔整数，忽略字段前后空白；正负数可用；空字符串或纯空白返回 []；
非空输入中的空字段和非法整数抛 ValueError；非 str 输入抛 TypeError。
只修改 repo；不得修改 verification 或原始夹具清单。

这是用于验证 EEAS 调用、修改、测试和证据链路的原创小任务，
不是数据板业务需求，不证明真实通信、嵌入式或 HIL 能力。
'''

def write_fixture(out: Path) -> dict:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"Target must be new or empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "repo/src").mkdir(parents=True)
    (out / "verification").mkdir()
    files = {"repo/src/samples.py": INITIAL, "public_brief.md": BRIEF,
             "verification/test_samples.py": TESTS}
    for relative, text in files.items():
        (out / relative).write_text(text, encoding="utf-8")
    manifest = {"schema_version": "eeas.synthetic-fixture.v1", "source_kind": "synthetic",
                "business_validated": False, "hardware_validated": False,
                "initial_tests_expected": "FAIL", "unit_test_count": 8,
                "candidate_root": "repo", "protected_verification_root": "verification",
                "hashes": {p: hashlib.sha256((out / p).read_bytes()).hexdigest() for p in files}}
    (out / "FIXTURE.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return manifest

def run_fixture_tests(out: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(EEAS_CANDIDATE_ROOT=str((out / "repo").resolve()),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(out / "verification/test_samples.py")],
                          cwd=str(out), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=20, check=False)

def self_test() -> dict:
    with tempfile.TemporaryDirectory(prefix="eeas_fixture_") as temp:
        out = Path(temp) / "case"
        write_fixture(out)
        initial = run_fixture_tests(out)
        if initial.returncode == 0 or "Ran 8 tests" not in initial.stderr:
            raise RuntimeError("Initial fixture did not execute expected failing tests")
        before = hashlib.sha256((out / "verification/test_samples.py").read_bytes()).hexdigest()
        (out / "repo/src/samples.py").write_text(REFERENCE, encoding="utf-8")
        repaired = run_fixture_tests(out)
        if repaired.returncode != 0 or "Ran 8 tests" not in repaired.stderr:
            raise RuntimeError("Reference did not pass:\n" + repaired.stderr)
        after = hashlib.sha256((out / "verification/test_samples.py").read_bytes()).hexdigest()
        if before != after:
            raise RuntimeError("Verification tests were altered")
        overwrite_rejected = False
        try:
            write_fixture(out)
        except ValueError:
            overwrite_rejected = True
        if not overwrite_rejected:
            raise RuntimeError("Nonempty target was not protected")
        return {"result": "PASS_FIXTURE_ONLY", "unit_tests": 8,
                "initial_exit_code": initial.returncode, "reference_exit_code": repaired.returncode,
                "verification_unchanged": True, "overwrite_rejected": True,
                "agent_called": False, "eeas_application_tested": False}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", type=Path, help="Create in a missing or empty directory")
    group.add_argument("--self-test", action="store_true", help="Validate fixture in temp directory")
    args = parser.parse_args()
    try:
        if args.self_test:
            print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        else:
            out = args.out.resolve()
            info = write_fixture(out)
            print(json.dumps({"created_at": str(out), "source_kind": "synthetic",
                              "expected_initial_result": "FAIL", "tests": info["unit_test_count"],
                              "agent_called": False}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
