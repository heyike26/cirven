import importlib.metadata
import unittest


class SDKInstallTests(unittest.TestCase):
    def test_native_entry_points_without_authentication(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, query
        self.assertTrue(callable(query))
        self.assertTrue(callable(ClaudeSDKClient))
        options = ClaudeAgentOptions(allowed_tools=["Read"], max_turns=1)
        self.assertEqual(options.allowed_tools, ["Read"])
        self.assertTrue(importlib.metadata.version("claude-agent-sdk"))
