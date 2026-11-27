import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("doctor", Path(__file__).parents[1] / "scripts/doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def test_no_credentials_are_serialized_or_inferred_valid(self):
        secret = "original-secret-fixture-never-persist"
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": secret}):
            result = doctor.diagnose()
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(result["runtime"]["api_key_present"])
        self.assertFalse(result["runtime"]["authentication_verified"])
        self.assertEqual(result["runtime"]["live_status"], "BLOCKED")
        self.assertFalse(result["developer_agent"]["login_cache_inspected"])

    def test_missing_tool_and_persistent_temporary_probe(self):
        self.assertEqual(doctor.tool_version("eeas-nonexistent-fixture"),
                         {"status": "MISSING", "version": None})
        result = doctor.diagnose()
        self.assertTrue(result["temporary_persistence_probe"])
        self.assertEqual(result["platform_verification"]["windows_native"], "NOT_VALIDATED")


if __name__ == "__main__":
    unittest.main()
