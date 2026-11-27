"""Original mandatory test baseline executed in its own Python process."""
import json
from pathlib import Path
import sys
import unittest

expected = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
workspace = Path(sys.argv[2]).resolve()
report_path = Path(sys.argv[3])
suite = unittest.TestSuite()
ids = []
for index, filename in enumerate(expected["files"]):
    test_id = f"original_artifact_{index+1}"
    ids.append(test_id)

    def check(name=filename):
        path = workspace / name
        if path.is_symlink() or not path.resolve().is_relative_to(workspace):
            raise AssertionError("Artifact escaped the approved workspace")
        if not path.is_file():
            raise AssertionError("Expected candidate artifact is missing")
        if path.read_bytes() != expected["content"].encode("utf-8"):
            raise AssertionError("Candidate content differs from the frozen original fixture")
    suite.addTest(unittest.FunctionTestCase(check, description=test_id))
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
report_path.write_text(json.dumps({"test_ids": ids, "tests_run": result.testsRun,
    "skipped": len(result.skipped), "successful": result.wasSuccessful()}, indent=2), encoding="utf-8")
raise SystemExit(0 if result.wasSuccessful() else 1)
